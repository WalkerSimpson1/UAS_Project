#!/usr/bin/env bash
# Dev layout: tmux session "uas" with Claude Code on the left, Neovim top-right,
# and mongosh (uas_tracker DB) bottom-right. Re-running attaches to the existing session.
set -euo pipefail

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SESSION=uas

# mongosh reads MONGO_URI/MONGO_DB from the environment or .env itself, so the URI
# (and any credentials in it) stays off the command line (ps) and out of tmux's
# session environment. Runs with the pane's cwd, the repo root.
MONGOSH_INIT=$(cat <<'EOF'
const env = require("fs").existsSync(".env") ? require("fs").readFileSync(".env", "utf8") : "";
const fromEnv = k => {
  const m = [...env.matchAll(new RegExp("^\\s*(?:export\\s+)?" + k + "=(.*)$", "gm"))].pop();
  return m ? m[1].trim().replace(/^[\x22\x27]|[\x22\x27]$/g, "") : "";
};
db = connect(process.env.MONGO_URI || fromEnv("MONGO_URI") || "mongodb://127.0.0.1:27017/")
  .getSiblingDB(process.env.MONGO_DB || fromEnv("MONGO_DB") || "uas_tracker");
EOF
)
MONGOSH_INIT=${MONGOSH_INIT//$'\n'/ }

if ! tmux has-session -t "=$SESSION" 2>/dev/null; then
  # Panes start a shell and get their command via send-keys, so they stay open
  # if the program exits (e.g. mongod is down).
  left=$(tmux new-session -d -s "$SESSION" -c "$REPO_DIR" -P -F '#{pane_id}')
  top_right=$(tmux split-window -h -t "$left" -c "$REPO_DIR" -P -F '#{pane_id}')
  bottom_right=$(tmux split-window -v -t "$top_right" -c "$REPO_DIR" -P -F '#{pane_id}')

  tmux send-keys -t "$left" 'claude' Enter
  tmux send-keys -t "$top_right" 'nvim .' Enter
  tmux send-keys -t "$bottom_right" "mongosh --nodb --shell --quiet --eval '$MONGOSH_INIT'" Enter
  tmux select-pane -t "$left"
fi

if [[ -n ${TMUX:-} ]]; then
  tmux switch-client -t "=$SESSION"
else
  tmux attach-session -t "=$SESSION"
fi
