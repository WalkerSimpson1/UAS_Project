#!/usr/bin/env bash
# Weekly UAS Tracker run (docs/mongodb_schema.md sections 6.1, 8, 9).
#
#   1. ZFS snapshot uaspool/mongo + uaspool/raw   @weekly-<date>-pre
#   2. SQLite notebooks (executed copies go to the log dir; notebooks/ is untouched)
#   3. scripts/weekly_ingest.py                    (MongoDB pipeline)
#   4. Validate MongoDB document counts
#   on failure in 1-4: stop mongod, roll back both datasets to -pre, start mongod
#   on success:        -post snapshot, mongodump, prune (12 weekly / 12 monthly),
#                      off-laptop copy (stub until D13 is decided)
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
INGEST=$REPO_DIR/scripts/weekly_ingest.py

# Run order matches the manual weekly update. The one-time USASpending_Past_* loaders are not run.
NOTEBOOKS=(LegiScan1 LegiScan2 USASpending1 USASpending2 USASpenging_Past_Visualizations)
NOTEBOOK_TIMEOUT=1800

# Must be non-empty after ingest. Every collection is also checked for shrinking.
REQUIRED_COLLECTIONS=(legislation_history legislation contracts contract_transactions contracts_weekly_transactions_all runs)

KEEP_WEEKLY_DAYS=84    # 12 weeks
KEEP_MONTHLY_DAYS=365  # 12 months

PATH=/usr/sbin:/sbin:$PATH

DRY_RUN=0
case ${1:-} in
  --dry-run) DRY_RUN=1 ;;
  -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
  "") ;;
  *) echo "unknown option: $1" >&2; exit 1 ;;
esac

TODAY=$(date +%F)
TAG=""
PRE_TAKEN=0
ROLLBACK_ARMED=0
WARNINGS=()

log()  { printf '%s %s\n' "$(date '+%F %T')" "$*"; }
warn() { log "WARNING: $*"; WARNINGS+=("$*"); }

# Runs a state-changing command, or prints it under --dry-run.
run() {
  if (( DRY_RUN )); then log "DRY: $*"; else "$@"; fi
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
  (( rc == 0 )) && log "ROLLBACK: done"
  # The SQLite DB, CSV exports and LegiScan zip live in the repo, not on ZFS.
  log "NOTE: repo files are not rolled back; check: git -C $REPO_DIR status"
  return "$rc"
}

fail() {
  trap - ERR INT TERM
  log "FAILED: $1"
  if (( ROLLBACK_ARMED )); then
    rollback || log "ROLLBACK INCOMPLETE: fix by hand before the next run"
  elif (( PRE_TAKEN )); then
    log "past validation; not rolling back"
  fi
  log "log file: $LOG_FILE"
  exit 1
}

trap 'fail "unexpected error at line $LINENO"' ERR
trap 'fail "interrupted"' INT TERM

step() {
  local name=$1; shift
  log "== $name"
  "$@" || fail "$name"
}

# ---------------------------------------------------------------- steps

preflight() {
  local ok=1
  [[ -x $PY ]] || { log "no python at $PY (set UAS_VENV)"; ok=0; }
  # The jupyter launcher exists without nbconvert; check the subcommand actually runs.
  "$VENV/bin/jupyter" nbconvert --version >/dev/null 2>&1 \
    || { log "jupyter nbconvert not available in $VENV (pip install nbconvert)"; ok=0; }
  [[ -f $REPO_DIR/.env ]] || { log "no $REPO_DIR/.env"; ok=0; }
  [[ -f $INGEST ]] || { log "missing $INGEST"; ok=0; }
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
  if (( ok )); then
    mongo_counts >/dev/null || { log "cannot reach MongoDB at MONGO_URI"; ok=0; }
  fi
  (( ok )) || { (( DRY_RUN )) && { warn "preflight problems above (ignored for --dry-run)"; return 0; }; return 1; }
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

run_notebook() {
  log "notebook: $1"
  run "$VENV/bin/jupyter" nbconvert --to notebook --execute \
    --ExecutePreprocessor.timeout="$NOTEBOOK_TIMEOUT" \
    --output-dir "$RUN_DIR/notebooks" --output "$1" \
    "$REPO_DIR/notebooks/$1.ipynb"
}

# LegiScan2 loads the newest zip with a plain INSERT, so only run it when that
# zip hasn't been loaded yet.
legiscan2_ready() {
  local newest loaded
  newest=$(find "$REPO_DIR/legiscan_getDatasetRaw_data" -maxdepth 1 -name 'getDatasetRaw*.zip' -printf '%f\n' | sort | tail -n 1)
  # Runs happen after the dataset date, so a load on or after it means this zip is in already.
  loaded=$("$PY" - "$REPO_DIR/UAS_Tracker.db" <<'EOF'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
print(con.execute("SELECT MAX(date_added) FROM legislation WHERE source_platform = 'LegiScan'").fetchone()[0] or "")
EOF
  )
  local zip_date=${newest#getDatasetRaw}; zip_date=${zip_date%.zip}
  if [[ -n $loaded && ! $loaded < $zip_date ]]; then
    warn "LegiScan2 skipped: $newest already loaded (latest date_added $loaded); LegiScan dataset may not have refreshed"
    return 1
  fi
}

run_notebooks() {
  mkdir -p "$RUN_DIR/notebooks"
  local nb
  for nb in "${NOTEBOOKS[@]}"; do
    if [[ $nb == LegiScan2 ]] && ! legiscan2_ready; then
      continue
    fi
    run_notebook "$nb" || return 1
  done
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

run_ingest() {
  mongo_counts > "$RUN_DIR/counts_before.txt" || return 1
  (cd "$REPO_DIR" && UAS_RUN_TAG=$TAG UAS_ZFS_SNAPSHOT_PRE=$TAG-pre run "$PY" "$INGEST") || return 1
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

mongodump_archive() {
  local out=$BACKUP_DIR/uas_tracker_${TAG#weekly-}.archive.gz cfg rc=0
  # URI goes through a 0600 config file so credentials don't show in ps.
  cfg=$(mktemp "$RUN_DIR/mongodump.XXXXXX.yaml")
  chmod 600 "$cfg"
  printf "uri: '%s'\n" "${MONGO_URI//\'/\'\'}" > "$cfg"
  run mongodump --config="$cfg" --db="$MONGO_DB" --archive="$out.partial" --gzip \
    && run mv "$out.partial" "$out" || rc=1
  rm -f "$cfg"
  (( rc == 0 )) && log "dump: $out"
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

  for ds in "$MONGO_DATASET" "$RAW_DATASET"; do
    while read -r name; do
      [[ $name =~ @weekly-${date_re}-(pre|post)$ ]] && items+=("${BASH_REMATCH[1]} snap $name")
    done < <(zfs list -H -t snapshot -o name -d 1 "$ds")
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
      snap) [[ $name == *@weekly-* ]] && { run zfs destroy "$name" || return 1; } ;;
      dump) run rm -f -- "$name" || return 1 ;;
    esac
  done
  return 0
}

# D13: off-laptop copy (tentatively azcopy to Azure Blob Storage, section 6.2) is not set up yet.
offsite_copy() {
  log "off-laptop copy: not configured (D13 open); skipped"
}

# ---------------------------------------------------------------- main

mkdir -p "$STATE_DIR/logs"
LOG_FILE=$STATE_DIR/logs/run-$(date +%F_%H%M%S).log
exec > >(tee -a "$LOG_FILE") 2>&1

exec 9>"$STATE_DIR/lock"
flock -n 9 || { log "another weekly run is in progress"; exit 1; }

(( DRY_RUN )) && log "dry run: no snapshots, notebooks, ingest, dumps or deletes"

export MONGO_URI=${MONGO_URI:-$(env_value MONGO_URI)}
export MONGO_DB=${MONGO_DB:-$(env_value MONGO_DB)}
: "${MONGO_URI:=mongodb://127.0.0.1:27017/}" "${MONGO_DB:=uas_tracker}"

step "preflight" preflight
step "wait for network" wait_for_network
pick_tag
RUN_DIR=$STATE_DIR/runs/$TAG
mkdir -p "$RUN_DIR"

step "snapshot $TAG-pre" snapshot pre
PRE_TAKEN=1
ROLLBACK_ARMED=$(( ! DRY_RUN ))

step "SQLite notebooks" run_notebooks
step "MongoDB ingest" run_ingest
step "validate document counts" validate_counts
ROLLBACK_ARMED=0

# Past this point the data is good; failures are reported but not rolled back.
post_ok=1
log "== snapshot $TAG-post"; snapshot post     || { log "ERROR: post snapshot failed"; post_ok=0; }
log "== mongodump";          mongodump_archive || { log "ERROR: mongodump failed"; post_ok=0; }
log "== prune";              prune             || { log "ERROR: prune failed"; post_ok=0; }
log "== off-laptop copy";    offsite_copy      || { log "ERROR: off-laptop copy failed"; post_ok=0; }
trap - ERR INT TERM

(( post_ok )) || { log "FAILED: backup steps (data kept, see above)"; log "log file: $LOG_FILE"; exit 1; }
if (( ${#WARNINGS[@]} )); then
  log "DONE with warnings:"
  printf '  - %s\n' "${WARNINGS[@]}"
  log "log file: $LOG_FILE"
  exit 2
fi
log "DONE: $TAG"
