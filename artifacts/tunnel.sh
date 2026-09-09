#!/usr/bin/env bash
# Persistent local->remote Docker socket forwards for the beat-stockfish rig.
#
# One forward per remote host ("box"), each on its own local TCP port, so
# several daemons can be driven from this container at once:
#
#   . ./tunnel.sh up box2     # (re)establish + export DOCKER_HOST for box2
#   ./tunnel.sh status all    # report every known box, touch nothing
#   ./tunnel.sh down box3     # tear one forward down
#   . ./tunnel.sh             # back-compatible default: box1
#
# Ports are per-box and fixed (see BOXES), so a forward for one box can never
# kill another's: every operation is scoped by LOCAL_PORT.
#
# Why a TCP forward instead of DOCKER_HOST=ssh://... :  `docker compose` hard-codes
# `ssh -o ControlMaster=no`, so every compose exec/cp would be a fresh TCP+SSH
# handshake against the host's MaxStartups budget.  One forwarded unix socket
# gives us a single SSH connection and cheap direct-tcpip channels instead.
set -uo pipefail

# box name -> ip:local_port.  box1 carries the step-2 baselines; do not disturb.
BOXES="\
box1=<box1-ip>:2375
box2=<box2-ip>:2376
box3=<box3-ip>:2377"

SSH_KEY="${SSH_KEY:-/dev/shm/ar-hetzner-work/id_ed25519}"
REMOTE_SOCK="${REMOTE_SOCK:-/var/run/docker.sock}"
SSH_USER="${SSH_USER:-root}"

_box_names() { echo "$BOXES" | cut -d= -f1; }

# Resolve a box selector into REMOTE_HOST/LOCAL_PORT.  Accepts a known box
# name, or a raw "ip:port" pair for a host that is not in the table yet.
_resolve() {
  local sel="$1" entry
  entry="$(echo "$BOXES" | grep "^${sel}=" | head -1 | cut -d= -f2)"
  if [ -n "$entry" ]; then
    BOX="$sel"
  elif case "$sel" in *:*) true ;; *) false ;; esac; then
    entry="$sel"; BOX="$sel"
  else
    echo "tunnel.sh: unknown box '$sel' (known: $(_box_names | tr '\n' ' ')); or pass ip:port" >&2
    return 1
  fi
  REMOTE_HOST="${SSH_USER}@${entry%:*}"
  LOCAL_PORT="${entry##*:}"
}

SSH_OPTS=(
  -i "$SSH_KEY"
  -o IdentitiesOnly=yes
  -o StrictHostKeyChecking=accept-new
  -o ExitOnForwardFailure=yes
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=6
  -o TCPKeepAlive=yes
  -o ConnectTimeout=20
)

# No `ps`/`pgrep` in this container -> scan procfs directly.  Matched on the
# -L spec, i.e. scoped to LOCAL_PORT: never touches another box's forward.
_pids() {
  local p c
  for p in /proc/[0-9]*; do
    # The 2>/dev/null must wrap the *redirect*, not just tr: procfs entries
    # vanish mid-scan and bash itself prints the "No such file" otherwise.
    c=$( { tr '\0' ' ' < "$p/cmdline"; } 2>/dev/null ) || continue
    case "$c" in
      ssh\ *-L\ ${LOCAL_PORT}:${REMOTE_SOCK}\ *) echo "${p#/proc/}" ;;
    esac
  done
}

_alive() {
  # A tunnel is only "up" if the forwarded port answers the Docker API.
  curl -s -m 10 --fail "http://127.0.0.1:${LOCAL_PORT}/_ping" >/dev/null 2>&1
}

_down() {
  local pids; pids="$(_pids || true)"
  if [ -n "$pids" ]; then
    echo "tunnel.sh[$BOX]: killing forward(s) on ${LOCAL_PORT}: $(echo "$pids" | tr '\n' ' ')" >&2
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null
    sleep 1
    pids="$(_pids || true)"
    # shellcheck disable=SC2086
    [ -n "$pids" ] && kill -9 $pids 2>/dev/null
  fi
  return 0
}

_up() {
  _down
  # setsid + -f: fully detached from this shell's session, survives the caller.
  setsid ssh -N -f "${SSH_OPTS[@]}" -L "${LOCAL_PORT}:${REMOTE_SOCK}" "$REMOTE_HOST" \
    || { echo "tunnel.sh[$BOX]: ssh failed to establish the forward" >&2; return 1; }
  for _ in $(seq 1 20); do
    _alive && break
    sleep 0.5
  done
  if ! _alive; then
    echo "tunnel.sh[$BOX]: forward established but 127.0.0.1:${LOCAL_PORT} does not answer /_ping" >&2
    return 1
  fi
  echo "tunnel.sh[$BOX]: up  (pid(s): $(_pids | tr '\n' ' ') -> ${REMOTE_HOST}:${REMOTE_SOCK} on :${LOCAL_PORT})" >&2
  return 0
}

_do_one() {
  local action="$1"
  _resolve "$2" || return 1
  case "$action" in
    status)
      if _alive; then echo "$BOX :$LOCAL_PORT -> $REMOTE_HOST  up (pid(s): $(_pids | tr '\n' ' '))"
      else echo "$BOX :$LOCAL_PORT -> $REMOTE_HOST  down"; fi
      ;;
    down)
      _down; echo "tunnel.sh[$BOX]: down" >&2
      ;;
    up)
      if _alive; then
        echo "tunnel.sh[$BOX]: already up (pid(s): $(_pids | tr '\n' ' ') on :${LOCAL_PORT})" >&2
      else
        _up || { echo "tunnel.sh[$BOX]: FAILED" >&2; return 1; }
      fi
      ;;
    *) echo "usage: $0 [up|down|status] [box|ip:port|all]" >&2; return 2 ;;
  esac
}

ACTION="${1:-up}"
# Back-compat: with no box, honour REMOTE_HOST/LOCAL_PORT if the caller set
# them, else default to box1 (what the pre-step-4 script did).
if [ $# -ge 2 ]; then
  SELECTOR="$2"
elif [ -n "${REMOTE_HOST:-}" ] || [ -n "${LOCAL_PORT:-}" ]; then
  SELECTOR="${REMOTE_HOST##*@}:${LOCAL_PORT:-2375}"
else
  SELECTOR="${TUNNEL_BOX:-box1}"
fi

case "$ACTION" in
  -h|--help) sed -n '2,17p' "$0"; return 0 2>/dev/null || exit 0 ;;
esac

TUNNEL_RC=0
if [ "$SELECTOR" = "all" ]; then
  for b in $(_box_names); do _do_one "$ACTION" "$b" || TUNNEL_RC=1; done
  echo "DOCKER_HOST not exported ('all' is ambiguous); use: . ./tunnel.sh up boxN" >&2
else
  _do_one "$ACTION" "$SELECTOR" || TUNNEL_RC=1
  export DOCKER_HOST="tcp://127.0.0.1:${LOCAL_PORT}"
  echo "DOCKER_HOST=${DOCKER_HOST}"
fi

# Sourced (`. ./tunnel.sh`) -> must not exit the caller's shell; executed ->
# propagate failure so callers can `|| exit`.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  exit "$TUNNEL_RC"
fi
