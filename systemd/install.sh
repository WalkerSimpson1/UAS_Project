#!/usr/bin/env bash
# One-time setup for weekly_run.sh. Run as the pipeline user (not root); asks for sudo.
#
#   1. zfs allow: snapshot/rollback/destroy on uaspool/mongo and uaspool/raw
#      (mount is required by rollback and destroy). Note `destroy` is delegated
#      per dataset, so it also covers the dataset itself, not only its snapshots;
#      weekly_run.sh only destroys @weekly-* snapshots.
#   2. /etc/sudoers.d/uas-weekly: passwordless `systemctl stop|start mongod` only,
#      needed to roll back uaspool/mongo safely.
#   3. loginctl enable-linger, so the user timer runs without a login session.
#   4. Install and enable uas-weekly.service/.timer as user units.
set -euo pipefail

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
USER_NAME=$(id -un)
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
DATASETS=(uaspool/mongo uaspool/raw)
SYSTEMCTL=$(command -v systemctl)

if (( EUID == 0 )); then
  echo "run as the pipeline user, not root" >&2
  exit 1
fi

echo "== zfs allow for $USER_NAME"
for ds in "${DATASETS[@]}"; do
  sudo zfs allow -u "$USER_NAME" snapshot,rollback,destroy,mount "$ds"
  zfs allow "$ds"
done

echo "== sudoers rule for mongod"
rule=$(mktemp)
trap 'rm -f "$rule"' EXIT
printf '%s ALL=(root) NOPASSWD: %s stop mongod, %s start mongod\n' "$USER_NAME" "$SYSTEMCTL" "$SYSTEMCTL" > "$rule"
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
