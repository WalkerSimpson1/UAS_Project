#!/usr/bin/env bash
# Dev layout: tmux session "uas" with Claude Code on the left, Neovim top-right,
# and mongosh (uas_tracker DB) bottom-right. Re-running attaches to the existing session.
set -euo pipefail

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SESSION=uas

# Prints KEY's value from .env (python-dotenv style KEY=value, optional quotes).
env_value() {
  local line
  line=$(grep -E "^[[:space:]]*(export[[:space:]]+)?$1=" "$REPO_DIR/.env" 2>/dev/null | tail -n 1) || return 0
  line=${line#*=}
  line=${line%\"}; line=${line#\"}
  line=${line%\'}; line=${line#\'}
  printf '%s' "$line"
}

if ! tmux has-session -t "=$SESSION" 2>/dev/null; then
  mongo_uri=${MONGO_URI:-$(env_value MONGO_URI)}
  mongo_db=${MONGO_DB:-$(env_value MONGO_DB)}

  # Panes start a shell and get their command via send-keys, so they stay open
  # if the program exits (e.g. mongod is down).
  left=$(tmux new-session -d -s "$SESSION" -c "$REPO_DIR" -P -F '#{pane_id}')
  tmux set-environment -t "$SESSION" MONGO_URI "${mongo_uri:-mongodb://127.0.0.1:27017/}"
  tmux set-environment -t "$SESSION" MONGO_DB "${mongo_db:-uas_tracker}"
  top_right=$(tmux split-window -h -t "$left" -c "$REPO_DIR" -P -F '#{pane_id}')
  bottom_right=$(tmux split-window -v -t "$top_right" -c "$REPO_DIR" -P -F '#{pane_id}')

  tmux send-keys -t "$left" 'claude' Enter
  tmux send-keys -t "$top_right" 'nvim .' Enter
  tmux send-keys -t "$bottom_right" \
    'mongosh "$MONGO_URI" --shell --eval "db = db.getSiblingDB(process.env.MONGO_DB)"' Enter
  tmux select-pane -t "$left"
fi

if [[ -n ${TMUX:-} ]]; then
  tmux switch-client -t "=$SESSION"
else
  tmux attach-session -t "=$SESSION"
fi
