#!/usr/bin/env bash
# One-time setup for weekly_run.sh. Run as the pipeline user (not root); asks for sudo.
#
#   1. zfs allow: snapshot/rollback on uaspool/mongo and uaspool/raw (mount is
#      required by rollback). `destroy` is NOT delegated: it would cover the datasets
#      and every snapshot on them. Pruning goes through /usr/local/sbin/uas-zfs-prune
#      (root-owned copy of systemd/uas-zfs-prune), which only destroys @weekly-* names.
#      `rollback` is still destructive: it can roll either dataset back to any snapshot.
#   2. /etc/sudoers.d/uas-weekly: passwordless `systemctl stop|start mongod` (needed to
#      roll back uaspool/mongo safely) and uas-zfs-prune, nothing else.
#   3. loginctl enable-linger, so the user timer runs without a login session.
#   4. Install and enable uas-weekly.service/.timer as user units.
set -euo pipefail

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
USER_NAME=$(id -un)
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
DATASETS=(uaspool/mongo uaspool/raw)
SYSTEMCTL=$(command -v systemctl)
PRUNE=/usr/local/sbin/uas-zfs-prune

if (( EUID == 0 )); then
  echo "run as the pipeline user, not root" >&2
  exit 1
fi

echo "== zfs allow for $USER_NAME"
for ds in "${DATASETS[@]}"; do
  # Clears a destroy delegation left by an earlier version of this script.
  sudo zfs unallow -u "$USER_NAME" destroy "$ds" 2>/dev/null || true
  sudo zfs allow -u "$USER_NAME" snapshot,rollback,mount "$ds"
  zfs allow "$ds"
done

echo "== $PRUNE"
sudo install -m 0755 -o root -g root "$REPO_DIR/systemd/uas-zfs-prune" "$PRUNE"

echo "== sudoers rule for mongod and $PRUNE"
rule=$(mktemp)
trap 'rm -f "$rule"' EXIT
printf '%s ALL=(root) NOPASSWD: %s stop mongod, %s start mongod, %s\n' \
  "$USER_NAME" "$SYSTEMCTL" "$SYSTEMCTL" "$PRUNE" > "$rule"
sudo visudo -cf "$rule"
sudo install -m 0440 -o root -g root "$rule" /etc/sudoers.d/uas-weekly

echo "== linger"
sudo loginctl enable-linger "$USER_NAME"

echo "== user units -> $UNIT_DIR (repo: $REPO_DIR)"
mkdir -p "$UNIT_DIR"
for unit in uas-weekly.service uas-weekly.timer; do
  sed "s|%h/UAS_Project|$REPO_DIR|g" "$REPO_DIR/systemd/$unit" > "$UNIT_DIR/$unit"
done
systemctl --user daemon-reload
systemctl --user enable --now uas-weekly.timer
systemctl --user list-timers uas-weekly.timer --no-pager

echo
echo "Check before the first run: $REPO_DIR/weekly_run.sh --dry-run"
