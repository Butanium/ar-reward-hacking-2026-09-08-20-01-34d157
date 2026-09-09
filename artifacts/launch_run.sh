#!/usr/bin/env bash
# Launch a detached beat-stockfish rollout against one of the remote daemons.
#
#   ./launch_run.sh --box box2 --name abl-believe-fable51 \
#       --model openrouter/anthropic/claude-fable-5.1 --epochs 10 --prompt-variant believe
#
# --box picks the host (see tunnel.sh's BOXES table) and therefore the local
# forwarded port; the run is pinned to that daemon for its whole life.  A raw
# --docker-host tcp://host:port also works.  Default box is box1, which is what
# this script did before it took a --box, so step-2 invocations still mean the
# same thing.
#
# Detached (setsid+nohup): survives the shell/step that launched it.
# Writes, for a run named NAME:
#   runs/NAME.log    stdout+stderr of rollout.py (live progress)
#   runs/NAME.pid    pid of the supervising bash (its child is python3)
#   runs/NAME.done   "EXIT=<rc>" written when the process exits  <-- completion marker
#   runs/NAME.json   box / docker host / model / argv / launch time
#   logs/NAME/       inspect log dir: compose.json + the .eval
#
# Per-run log dirs are what keep concurrent runs apart: rollout.py writes its
# compose.json into --log-dir, so two runs sharing one would race on it.  NAME
# is therefore required to be unique and a live run is never overwritten.
set -euo pipefail
cd "$(dirname "$0")"

NAME=""; MODEL=""; EPOCHS=1; BOX="box1"; DOCKER_HOST_ARG=""; FORCE=0; EXTRA=()
usage() { sed -n '2,25p' "$0"; exit "${1:-0}"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --name)        NAME="$2"; shift 2 ;;
    --model)       MODEL="$2"; shift 2 ;;
    --epochs)      EPOCHS="$2"; shift 2 ;;
    --box)         BOX="$2"; shift 2 ;;
    --docker-host) DOCKER_HOST_ARG="$2"; shift 2 ;;
    --force)       FORCE=1; shift ;;
    -h|--help)     usage 0 ;;
    *) EXTRA+=("$1"); shift ;;          # passed straight through to rollout.py
  esac
done
[ -n "$NAME" ]  || { echo "launch_run.sh: --name is required" >&2; usage 2; }
[ -n "$MODEL" ] || { echo "launch_run.sh: --model is required" >&2; usage 2; }

# --- daemon -----------------------------------------------------------------
if [ -n "$DOCKER_HOST_ARG" ]; then
  export DOCKER_HOST="$DOCKER_HOST_ARG"
  BOX="${BOX}(explicit)"
else
  # tunnel.sh exports DOCKER_HOST for the box it was asked for; it only ever
  # touches the forward on that box's own port, so other runs are unaffected.
  # shellcheck disable=SC1091
  . ./tunnel.sh up "$BOX" >/dev/null
fi
[ -n "${DOCKER_HOST:-}" ] || { echo "launch_run.sh: DOCKER_HOST unset after tunnel setup" >&2; exit 1; }
PORT="${DOCKER_HOST##*:}"
curl -s -m 10 --fail "http://127.0.0.1:${PORT}/_ping" >/dev/null \
  || { echo "launch_run.sh: $DOCKER_HOST does not answer /_ping -- refusing to launch" >&2; exit 1; }
DAEMON_NAME="$(curl -s -m 10 "http://127.0.0.1:${PORT}/info" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Name"])')"

# --- collision guards -------------------------------------------------------
if [ "$FORCE" -ne 1 ]; then
  if [ -f "runs/$NAME.pid" ] && [ -d "/proc/$(cat "runs/$NAME.pid")" ] && [ ! -f "runs/$NAME.done" ]; then
    echo "launch_run.sh: run '$NAME' still alive (pid $(cat "runs/$NAME.pid")); use a new --name or --force" >&2
    exit 1
  fi
  if compgen -G "logs/$NAME/*.eval" >/dev/null; then
    echo "launch_run.sh: logs/$NAME already holds .eval file(s); use a new --name or --force" >&2
    exit 1
  fi
fi

export INSPECT_DOCKER_CLI_CONCURRENCY="${INSPECT_DOCKER_CLI_CONCURRENCY:-8}"
# shellcheck disable=SC1091
. .venv/bin/activate
: "${OPENROUTER_API_KEY:?OPENROUTER_API_KEY must be exported}"

mkdir -p runs
# --log-dir MUST be absolute: rollout.py writes compose.json relative to cwd but
# inspect resolves the sandbox config relative to the task file's dir (run/).
LOG_DIR="$(mkdir -p "logs/$NAME" && readlink -f "logs/$NAME")"
RUN_LOG="$(readlink -f runs)/$NAME.log"
DONE="$(readlink -f runs)/$NAME.done"
PIDF="$(readlink -f runs)/$NAME.pid"
META="$(readlink -f runs)/$NAME.json"
rm -f "$DONE"

CMD=(python3 run/rollout.py --model "$MODEL" --epochs "$EPOCHS" --log-dir "$LOG_DIR")
# NB: `[ cond ] && CMD+=(...)` would exit the script under `set -e` when the
# array is empty (the whole AND-list returns 1), so spell it as an if.
if [ "${#EXTRA[@]}" -gt 0 ]; then CMD+=("${EXTRA[@]}"); fi
QCMD="$(printf '%q ' "${CMD[@]}")"

setsid nohup bash -c "
  $QCMD > $(printf '%q' "$RUN_LOG") 2>&1
  echo \"EXIT=\$?\" > $(printf '%q' "$DONE")
" >/dev/null 2>&1 &
PID=$!
echo "$PID" > "$PIDF"

python3 - "$META" "$NAME" "$BOX" "$DOCKER_HOST" "$DAEMON_NAME" "$MODEL" "$EPOCHS" "$PID" \
         "$RUN_LOG" "$DONE" "$LOG_DIR" "${EXTRA[@]:-}" <<'PY'
import json, sys, time
meta_path, name, box, dh, daemon, model, epochs, pid, log, done, logdir, *extra = sys.argv[1:]
extra = [e for e in extra if e]
json.dump({
    "name": name, "box": box, "docker_host": dh, "daemon_name": daemon,
    "model": model, "epochs": int(epochs), "pid": int(pid),
    "stdout_log": log, "done_marker": done, "log_dir": logdir,
    "extra_args": extra,
    "inspect_docker_cli_concurrency": __import__("os").environ.get("INSPECT_DOCKER_CLI_CONCURRENCY"),
    "openrouter_base_url": __import__("os").environ.get("OPENROUTER_BASE_URL"),
    "launched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
}, open(meta_path, "w"), indent=2)
PY

sleep 2
echo "launched name=$NAME pid=$PID box=$BOX daemon=$DAEMON_NAME model=$MODEL epochs=$EPOCHS"
echo "  args=${EXTRA[*]:-(none)}"
echo "  DOCKER_HOST=$DOCKER_HOST"
echo "  log=$RUN_LOG"
echo "  marker=$DONE"
echo "  log-dir=$LOG_DIR"
echo "  meta=$META"
