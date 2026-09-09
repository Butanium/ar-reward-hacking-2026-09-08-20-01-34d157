#!/usr/bin/env bash
# Step-4 ablation campaign: 4 conditions x 2 models = 8 detached runs, 10 epochs
# each.  fable -> box2, astra -> box3 (astra's engine-relay games are CPU-heavy,
# fable's episodes are often short; keeping the load profiles on separate hosts).
# Launches are staggered inside a box so 4x10 `compose up`s do not land at once.
set -uo pipefail
cd "$(dirname "$0")"
export INSPECT_DOCKER_CLI_CONCURRENCY=8

launch_box() {   # $1=box  $2=suffix  $3=model
  local box="$1" sfx="$2" model="$3" first=1
  local -a arms=(
    "nograding|--prompt-variant nograding"
    "notools|--prompt-variant notools"
    "believe|--prompt-variant believe"
    "stopeval|--stop-eval-tool"
  )
  for arm in "${arms[@]}"; do
    local key="${arm%%|*}" flags="${arm#*|}"
    [ "$first" -eq 1 ] || sleep 30
    first=0
    # shellcheck disable=SC2086
    ./launch_run.sh --box "$box" --name "abl-${key}-${sfx}" --model "$model" --epochs 10 $flags
  done
}

launch_box box2 fable51 openrouter/anthropic/claude-fable-5.1 > runs/campaign-box2.launch.log 2>&1 &
P2=$!
launch_box box3 astra   openrouter/openai/gpt-6-astra        > runs/campaign-box3.launch.log 2>&1 &
P3=$!
wait $P2; wait $P3
cat runs/campaign-box2.launch.log runs/campaign-box3.launch.log
