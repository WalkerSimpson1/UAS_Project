#!/usr/bin/env bash
# Weekly UAS Tracker run (docs/mongodb_schema.md sections 6.1, 8, 9).
#
#   1. ZFS snapshot uaspool/mongo + uaspool/raw   @weekly-<date>-pre
#   2. scripts/weekly_ingest.py --legiscan-download (exit 2 = partial, a warning)
#   3. Validate MongoDB document counts
#   on failure in 1-3: stop mongod, roll back both datasets to -pre, start mongod
#   on success:        -post snapshot, CSV exports to the repo root, mongodump,
#                      prune (12 weekly / 12 monthly), restore the dump to Atlas (D20;
#                      a failure there is a warning)
#
# Since cut-over (section 9) MongoDB is the system of record; the SQLite
# notebooks are no longer run.
#
# Exit status: 0 ok, 1 failed (rolled back if past step 1), 2 completed with warnings.
# Usage: weekly_run.sh [--dry-run]
#   --dry-run  print the commands that change state instead of running them
#
# Setup (ZFS delegation, sudoers rule for mongod, systemd units): systemd/install.sh
set -Eeuo pipefail

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
VENV=${UAS_VENV:-$REPO_DIR/.venv}
PY=$VENV/bin/python
STATE_DIR=${XDG_STATE_HOME:-$HOME/.local/state}/uas-weekly

MONGO_DATASET=uaspool/mongo
RAW_DATASET=uaspool/raw
BACKUP_DIR=/uaspool/backups
MONGOD_UNIT=mongod
# Root-owned, sudo-allowed; destroys only @weekly-* snapshots (systemd/uas-zfs-prune).
ZFS_PRUNE=/usr/local/sbin/uas-zfs-prune
INGEST=$REPO_DIR/scripts/weekly_ingest.py
EXPORT=$REPO_DIR/scripts/export_csvs.py

# Must be non-empty after ingest. Every collection is also checked for shrinking,
# including rulemakings and rulemaking_comments, which are deliberately not
# required: a week with no matching documents or dockets leaves them empty.
REQUIRED_COLLECTIONS=(legislation_history legislation contracts contract_transactions contracts_weekly_transactions_all runs)

KEEP_WEEKLY_DAYS=84    # 12 weeks
KEEP_MONTHLY_DAYS=365  # 12 months

PATH=/usr/sbin:/sbin:$PATH

DRY_RUN=0
case ${1:-} in
  --dry-run) DRY_RUN=1 ;;
  -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
  "") ;;
  *) echo "unknown option: $1" >&2; exit 1 ;;
esac

TODAY=$(date +%F)
TAG=""
PRE_TAKEN=0
ROLLBACK_ARMED=0

log()  { printf '%s %s\n' "$(date '+%F %T')" "$*"; }
# Steps run in subshells, so warnings are collected in a file ($WARN_FILE, set in main).
warn() { log "WARNING: $*"; printf '%s\n' "$*" >> "$WARN_FILE"; }

# Runs a state-changing command, or prints it under --dry-run.
# fd 9 is the run lock (see main); children must not inherit it.
run() {
  if (( DRY_RUN )); then log "DRY: $*"; else "$@" 9>&-; fi
}

# Prints KEY's value from .env (python-dotenv style KEY=value, optional quotes).
env_value() {
  local line
  line=$(grep -E "^[[:space:]]*(export[[:space:]]+)?$1=" "$REPO_DIR/.env" 2>/dev/null | tail -n 1) || return 0
  line=${line#*=}
  line=${line%\"}; line=${line#\"}
  line=${line%\'}; line=${line#\'}
  printf '%s' "$line"
}

snapshot_exists() { zfs list -H -t snapshot -o name "$1" >/dev/null 2>&1; }

# ---------------------------------------------------------------- failure handling

rollback() {
  local snap=$TAG-pre rc=0
  log "ROLLBACK: $MONGO_DATASET@$snap and $RAW_DATASET@$snap"
  if ! run sudo -n systemctl stop "$MONGOD_UNIT"; then
    log "ROLLBACK ABORTED: could not stop $MONGOD_UNIT; datasets left as they are"
    return 1
  fi
  run zfs rollback "$MONGO_DATASET@$snap" || { log "ROLLBACK ERROR: $MONGO_DATASET"; rc=1; }
  run zfs rollback "$RAW_DATASET@$snap"   || { log "ROLLBACK ERROR: $RAW_DATASET"; rc=1; }
  run sudo -n systemctl start "$MONGOD_UNIT" || { log "ROLLBACK ERROR: could not start $MONGOD_UNIT"; rc=1; }
  if (( rc == 0 )); then
    log "ROLLBACK: done"
    # Tells the next preflight this -pre snapshot without a -post was cleaned up.
    mkdir -p "$STATE_DIR/resolved" && touch "$STATE_DIR/resolved/$TAG"
  fi
  # The rollback of uaspool/raw also removes any LegiScan zip downloaded this run;
  # the next run downloads it again.
  return "$rc"
}

fail() {
  trap - ERR INT TERM
  # If tee is gone anyway, a write must not SIGPIPE us before the rollback.
  trap '' PIPE
  log "FAILED: $1"
  if (( ROLLBACK_ARMED )); then
    rollback || log "ROLLBACK INCOMPLETE: fix by hand before the next run"
  elif (( PRE_TAKEN )); then
    log "past validation; not rolling back"
  fi
  log "log file: $LOG_FILE"
  exit 1
}

ON_ERR='fail "unexpected error at line $LINENO"'
trap "$ON_ERR" ERR
trap 'fail "interrupted"' INT TERM

# Runs "$@" in a subshell with errexit in force and sets STEP_RC. A function called
# on the left of || (or under if/!) runs with set -e and the ERR trap disabled for its
# whole body, so steps are never called that way. The parent's ERR trap is off while
# the subshell runs so a failing step is handled once, by the caller.
run_isolated() {
  trap - ERR
  set +e
  (
    # Drop the run lock's fd so nothing the step starts (e.g. a hung Jupyter kernel)
    # can hold the lock past this run. The parent's fd keeps the lock held.
    exec 9>&-
    set -e
    trap 'log "error at line $LINENO: $BASH_COMMAND"' ERR
    "$@"
  )
  STEP_RC=$?
  set -e
  trap "$ON_ERR" ERR
}

step() {
  local name=$1; shift
  log "== $name"
  run_isolated "$@"
  (( STEP_RC == 0 )) || fail "$name"
}

# For the steps after validation: report a failure, don't roll back.
post_step() {
  local name=$1; shift
  log "== $name"
  run_isolated "$@"
  (( STEP_RC == 0 )) || { log "ERROR: $name failed"; post_ok=0; }
}

# ---------------------------------------------------------------- steps

preflight() {
  local ok=1
  [[ -x $PY ]] || { log "no python at $PY (set UAS_VENV)"; ok=0; }
  [[ -f $REPO_DIR/.env ]] || { log "no $REPO_DIR/.env"; ok=0; }
  # Checked for presence only; the value is never printed.
  [[ -n $(env_value REGULATIONS_API_KEY) ]] || { log "REGULATIONS_API_KEY is not set in $REPO_DIR/.env"; ok=0; }
  [[ -f $INGEST ]] || { log "missing $INGEST"; ok=0; }
  [[ -f $EXPORT ]] || { log "missing $EXPORT"; ok=0; }
  for ds in "$MONGO_DATASET" "$RAW_DATASET"; do
    zfs list -H -o name "$ds" >/dev/null 2>&1 || { log "no ZFS dataset $ds"; ok=0; }
  done
  [[ -d $BACKUP_DIR && -w $BACKUP_DIR ]] || { log "$BACKUP_DIR missing or not writable"; ok=0; }
  systemctl is-active --quiet "$MONGOD_UNIT" || { log "$MONGOD_UNIT is not running"; ok=0; }
  # Without these the failure path cannot roll back, so don't start.
  for action in stop start; do
    sudo -n -l systemctl "$action" "$MONGOD_UNIT" >/dev/null 2>&1 \
      || { log "no passwordless sudo for 'systemctl $action $MONGOD_UNIT' (systemd/install.sh)"; ok=0; }
  done
  sudo -n -l "$ZFS_PRUNE" >/dev/null 2>&1 \
    || { log "no passwordless sudo for $ZFS_PRUNE (systemd/install.sh)"; ok=0; }
  if (( ok )); then
    mongo_counts >/dev/null || { log "cannot reach MongoDB at MONGO_URI"; ok=0; }
  fi
  if (( ok )); then
    local stale
    if ! stale=$(unfinished_run); then
      log "cannot check for an unfinished earlier run"; ok=0
    elif [[ -n $stale ]]; then
      log "earlier run $stale never finished: no -post snapshot, no successful weekly_ingest run, not rolled back"
      log "roll it back with:"
      log "  sudo systemctl stop $MONGOD_UNIT && zfs rollback $MONGO_DATASET@$stale-pre && zfs rollback $RAW_DATASET@$stale-pre && sudo systemctl start $MONGOD_UNIT"
      log "then (or if the data is fine as it is): touch $STATE_DIR/resolved/$stale"
      ok=0
    fi
  fi
  (( ok )) || { (( DRY_RUN )) && { warn "preflight problems above (ignored for --dry-run)"; return 0; }; return 1; }
}

# Prints the tag of the newest run that took a -pre snapshot but never finished:
# no -post snapshot, not rolled back, and no successful weekly_ingest run
# ("ok", or "partial", which completed with warnings).
# (SIGKILL, power loss or suspend mid-run skip all cleanup.)
unfinished_run() {
  local snaps pre tag
  snaps=$(zfs list -H -t snapshot -o name -s creation -d 1 "$MONGO_DATASET") || return 1
  pre=$(grep -E '@weekly-.*-pre$' <<<"$snaps" | tail -n 1 || true)
  [[ -n $pre ]] || return 0
  tag=${pre#*@}; tag=${tag%-pre}
  snapshot_exists "$MONGO_DATASET@$tag-post" && return 0
  [[ -e $STATE_DIR/resolved/$tag ]] && return 0
  local ingest_ok
  ingest_ok=$("$PY" - "$tag" <<'EOF'
import os, sys
from pymongo import MongoClient
db = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=5000)[os.environ["MONGO_DB"]]
print(db.runs.count_documents({"pipeline": "weekly_ingest", "run_id": sys.argv[1],
                               "status": {"$in": ["ok", "partial"]}}))
EOF
  ) || return 1
  (( ingest_ok > 0 )) && return 0
  printf '%s' "$tag"
}

wait_for_network() {
  local i
  for i in $(seq 30); do
    getent hosts api.legiscan.com >/dev/null && getent hosts api.usaspending.gov >/dev/null && return 0
    log "network not ready ($i/30)"
    sleep 10
  done
  return 1
}

# weekly-<date>, or weekly-<date>_2, _3 ... when today already has a run.
pick_tag() {
  local n=2
  TAG=weekly-$TODAY
  while snapshot_exists "$MONGO_DATASET@$TAG-pre" || snapshot_exists "$RAW_DATASET@$TAG-pre"; do
    TAG=weekly-${TODAY}_$n
    n=$((n + 1))
  done
  log "run tag: $TAG"
}

snapshot() {
  # One command, so both datasets are snapshotted atomically.
  run zfs snapshot "$MONGO_DATASET@$TAG-$1" "$RAW_DATASET@$TAG-$1"
}

# Prints "<collection> <count>" for every collection in the DB.
mongo_counts() {
  "$PY" - <<'EOF'
import os
from pymongo import MongoClient
client = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=5000)
db = client[os.environ["MONGO_DB"]]
for name in sorted(db.list_collection_names()):
    print(name, db[name].count_documents({}))
EOF
}

# weekly_ingest.py exits 2 when only rulemaking loaders were partial (e.g. the
# Regulations.gov rate limit): keep the data and finish with a warning.
run_ingest() {
  mongo_counts > "$RUN_DIR/counts_before.txt" || return 1
  local rc=0
  (cd "$REPO_DIR" && UAS_RUN_TAG=$TAG UAS_ZFS_SNAPSHOT_PRE=$TAG-pre run "$PY" "$INGEST" --legiscan-download) || rc=$?
  if (( rc == 2 )); then
    warn "MongoDB ingest partial: see the weekly_ingest runs document for $TAG"
  elif (( rc != 0 )); then
    return 1
  fi
  mongo_counts > "$RUN_DIR/counts_after.txt" || return 1
}

validate_counts() {
  if (( DRY_RUN )); then log "DRY: validation skipped (ingest did not run)"; return 0; fi
  local ok=1 name before after
  declare -A b=() a=()
  while read -r name before; do b[$name]=$before; done < "$RUN_DIR/counts_before.txt"
  while read -r name after;  do a[$name]=$after;  done < "$RUN_DIR/counts_after.txt"
  for name in "${!b[@]}"; do
    after=${a[$name]:-0}
    log "count $name: ${b[$name]} -> $after"
    (( after >= b[$name] )) || { log "count dropped: $name"; ok=0; }
  done
  for name in "${!a[@]}"; do
    [[ -v b[$name] ]] || log "count $name: new -> ${a[$name]}"
  done
  for name in "${REQUIRED_COLLECTIONS[@]}"; do
    (( ${a[$name]:-0} > 0 )) || { log "empty after ingest: $name"; ok=0; }
  done
  (( ${a[runs]:-0} > ${b[runs]:-0} )) || { log "ingest wrote no runs document"; ok=0; }
  (( ok ))
}

# The committed CSV exports (CLAUDE.md "Repo conventions"); git shows what changed.
export_csvs() {
  (cd "$REPO_DIR" && run "$PY" "$EXPORT" --out-dir "$REPO_DIR")
}

# Writes a 0600 config file holding URI $2 for mongodump/mongorestore and prints
# its path. The URI goes through the file so credentials don't show in ps.
uri_config() {
  local cfg
  cfg=$(mktemp "$RUN_DIR/$1.XXXXXX.yaml") || return 1
  chmod 600 "$cfg"
  printf "uri: '%s'\n" "${2//\'/\'\'}" > "$cfg" || { rm -f "$cfg"; return 1; }
  printf '%s' "$cfg"
}

# Filters mongodump/mongorestore output for the log: their connection errors print
# the whole URI, password included.
redact_uris() {
  sed -u -E 's#mongodb(\+srv)?://[^[:space:]]*#mongodb\1://[REDACTED]#g'
}

mongodump_archive() {
  local cfg rc=0
  cfg=$(uri_config mongodump "$MONGO_URI")
  run mongodump --config="$cfg" --db="$MONGO_DB" --archive="$DUMP_FILE.partial" --gzip 2>&1 | redact_uris \
    && run mv "$DUMP_FILE.partial" "$DUMP_FILE" || rc=1
  rm -f "$cfg"
  (( rc == 0 )) && log "dump: $DUMP_FILE"
  return "$rc"
}

# Keep everything from the last 12 weeks, plus the first run of each month for 12 months.
# Only touches names this script creates.
prune() {
  local cutoff_weekly cutoff_monthly item d kind name ds
  cutoff_weekly=$(date -d "-$KEEP_WEEKLY_DAYS days" +%F)
  cutoff_monthly=$(date -d "-$KEEP_MONTHLY_DAYS days" +%F)
  local -a items=()
  local -A month_first=()
  local date_re='([0-9]{4}-[0-9]{2}-[0-9]{2})(_[0-9]+)?'

  local snaps
  for ds in "$MONGO_DATASET" "$RAW_DATASET"; do
    snaps=$(zfs list -H -t snapshot -o name -d 1 "$ds") || return 1
    while read -r name; do
      [[ $name =~ @weekly-${date_re}-(pre|post)$ ]] && items+=("${BASH_REMATCH[1]} snap $name")
    done <<<"$snaps"
  done
  for name in "$BACKUP_DIR"/uas_tracker_*.archive.gz; do
    [[ ${name##*/} =~ ^uas_tracker_${date_re}\.archive\.gz$ ]] && items+=("${BASH_REMATCH[1]} dump $name")
  done

  for item in "${items[@]}"; do
    d=${item%% *}
    [[ -z ${month_first[${d:0:7}]:-} || $d < ${month_first[${d:0:7}]} ]] && month_first[${d:0:7}]=$d
  done

  for item in "${items[@]}"; do
    read -r d kind name <<<"$item"
    [[ ! $d < $cutoff_weekly ]] && continue
    [[ $d == "${month_first[${d:0:7}]}" && ! $d < $cutoff_monthly ]] && continue
    log "prune: $name"
    case $kind in
      snap) run sudo -n "$ZFS_PRUNE" "$name" || return 1 ;;
      dump) run rm -f -- "$name" || return 1 ;;
    esac
  done
  return 0
}

# D13, D20: off-laptop copy. Restores this run's dump into Atlas (ATLAS_URI in .env),
# dropping each collection in the dump first, so Atlas mirrors the local DB as of the
# -post snapshot. Every failure here is a warning (exit 2), never a rollback or exit 1:
# the local snapshots and dump are already in place.
offsite_copy() {
  local uri cfg rc=0
  uri=$(env_value ATLAS_URI)
  if [[ -z $uri ]]; then
    warn "off-laptop copy skipped: ATLAS_URI is not set in $REPO_DIR/.env"
    return 0
  fi
  if (( ! DRY_RUN )) && [[ ! -s $DUMP_FILE ]]; then
    warn "off-laptop copy skipped: no dump at $DUMP_FILE"
    return 0
  fi
  cfg=$(uri_config mongorestore "$uri") || { warn "off-laptop copy skipped: cannot write the mongorestore config"; return 0; }
  run mongorestore --config="$cfg" --archive="$DUMP_FILE" --gzip \
    --nsInclude="$MONGO_DB.*" --drop --stopOnError 2>&1 | redact_uris || rc=$?
  rm -f "$cfg"
  if (( rc != 0 )); then
    warn "off-laptop copy to Atlas failed (mongorestore exit $rc); Atlas may be missing collections until the next run"
  elif (( ! DRY_RUN )); then
    log "off-laptop copy: $DUMP_FILE restored to Atlas"
  fi
  return 0
}

# ---------------------------------------------------------------- main

mkdir -p "$STATE_DIR/logs"
LOG_FILE=$STATE_DIR/logs/run-$(date +%F_%H%M%S).log
WARN_FILE=${LOG_FILE%.log}.warnings
# systemd stop/timeout sends SIGTERM to the whole cgroup and Ctrl-C reaches the whole
# process group; tee must outlive them so fail() can still log and roll back.
exec > >(trap '' INT TERM; exec tee -a "$LOG_FILE") 2>&1
TEE_PID=$!
# When the main process exits, systemd kills what's left in the cgroup, tee included.
# Close our end of the pipe and wait, so tee writes the last lines first.
finish_log() {
  exec >&- 2>&-
  wait "$TEE_PID" 2>/dev/null || true
}
trap finish_log EXIT

exec 9>"$STATE_DIR/lock"
flock -n 9 || { log "another weekly run is in progress"; exit 1; }

(( DRY_RUN )) && log "dry run: no snapshots, ingest, exports, dumps or deletes"

export MONGO_URI=${MONGO_URI:-$(env_value MONGO_URI)}
export MONGO_DB=${MONGO_DB:-$(env_value MONGO_DB)}
: "${MONGO_URI:=mongodb://127.0.0.1:27017/}" "${MONGO_DB:=uas_tracker}"

step "preflight" preflight
step "wait for network" wait_for_network
pick_tag
RUN_DIR=$STATE_DIR/runs/$TAG
DUMP_FILE=$BACKUP_DIR/uas_tracker_${TAG#weekly-}.archive.gz
mkdir -p "$RUN_DIR"

step "snapshot $TAG-pre" snapshot pre
PRE_TAKEN=1
ROLLBACK_ARMED=$(( ! DRY_RUN ))

step "MongoDB ingest" run_ingest
step "validate document counts" validate_counts
ROLLBACK_ARMED=0

# Past this point the data is good; failures are reported but not rolled back.
post_ok=1
post_step "snapshot $TAG-post" snapshot post
post_step "CSV exports" export_csvs
post_step "mongodump" mongodump_archive
post_step "prune" prune
post_step "off-laptop copy (Atlas)" offsite_copy
trap - ERR INT TERM

(( post_ok )) || { log "FAILED: backup steps (data kept, see above)"; log "log file: $LOG_FILE"; exit 1; }
if [[ -s $WARN_FILE ]]; then
  log "DONE with warnings:"
  sed 's/^/  - /' "$WARN_FILE"
  log "log file: $LOG_FILE"
  exit 2
fi
log "DONE: $TAG"
