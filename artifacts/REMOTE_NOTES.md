# Remote Docker rig — step 1 notes (2026-09-08)

Host: `root@<box1-ip>` (Hetzner cx43, 8 vCPU / 16 GB, Ubuntu 6.8, Docker 29.8.0,
buildkit v0.33.0, buildx v0.37.0, compose v5.5.1). **Auto-deletes ~12 h from provisioning.**

## 1. Tunnel

`./tunnel.sh` — forwards the remote `/var/run/docker.sock` onto local TCP 2375.

```sh
. ./tunnel.sh          # (re)establish + export DOCKER_HOST in this shell
./tunnel.sh status     # "up (pid(s): N)" / "down"
./tunnel.sh down       # tear down
export DOCKER_HOST=tcp://127.0.0.1:2375
```

- Kills any stale forward first, then `setsid ssh -N -f -L 2375:/var/run/docker.sock`,
  then waits until `GET http://127.0.0.1:2375/_ping` succeeds. Liveness is the Docker API
  answering, not merely a process existing.
- `setsid` + `-f`: fully detached, survives the shell/step that created it.
- `ServerAliveInterval=30`, `ServerAliveCountMax=6`, `ExitOnForwardFailure=yes`.
- **Gotcha:** this container has no `ps`/`pgrep`, so the script finds the ssh pid by
  scanning `/proc/*/cmdline`. Don't "simplify" it back to `pgrep`.
- Why TCP instead of `DOCKER_HOST=ssh://…`: `docker compose` hard-codes
  `ssh -o ControlMaster=no`, so every compose exec/cp would be a fresh TCP+SSH handshake.
  One forwarded socket = one SSH connection, cheap `direct-tcpip` channels, no
  `MaxStartups` pressure. Verified: `docker version` reports the remote engine
  (`Name: ar-34d157f7-f470fe`, 8 CPU, 16 GB, overlayfs).

## 2. Build

`./build.sh` with `DOCKER_HOST` set. **~2.5 min wall clock**, `EXIT=0` (full log: `build.log`).

Two things had to be fixed first:

1. **No buildx locally.** `build.sh` uses `--build-context`, a buildx-only flag; the
   user-installed CLI only had the compose plugin, so `docker build` fell back to the
   legacy builder and the flag did not exist. Fixed by copying the plugin off the remote
   host (no external network needed):
   ```sh
   scp -i "$SSH_KEY" root@<ip>:/usr/libexec/docker/cli-plugins/docker-buildx \
       ~/.docker/cli-plugins/docker-buildx && chmod +x ~/.docker/cli-plugins/docker-buildx
   ```
   Builder is `default` (docker driver) → the remote daemon's built-in BuildKit v0.33.0.
2. **`.venv` is not in `.dockerignore`**, so the final `docker build .` would have streamed
   ~276 MB of venv to the remote daemon. `.venv/` was appended to `.dockerignore` for the
   build and **the file was restored to its exported state afterwards**. Nothing COPYs
   `.venv`, so the image is unaffected — this is transfer hygiene only. Re-apply it (or
   accept the upload) on any rebuild.

Images on the remote daemon:

| image | size |
|---|---|
| `honeyforge-workstation:base` | 861 MB |
| `honeyforge-inspect-runner:base` | 861 MB |
| `honeyforge-beat-stockfish-base:base` | 1.01 GB |
| `beat-stockfish:local` | 1.01 GB |

Verified inside `beat-stockfish:local`: Stockfish **17.1** (`sf_17.1`, `ARCH=x86-64`, LTO,
embedded NNUE) at `/opt/engine/engine`, mode `---x------ nobody:nogroup`, benching
**371 k nodes/s**; `python-chess 1.11.2`; `/grader` 0700 root; `/workdir/arena.py` present.

## 3. Rollout

`./smoke_run.sh` — detached launcher (`smoke.log`, `smoke.pid`, `smoke.done` = `EXIT=<rc>`),
`INSPECT_DOCKER_CLI_CONCURRENCY=4`.

**Gotcha (cost one failed run): `--log-dir` must be ABSOLUTE.** `rollout.py` writes
`compose.json` under the log dir resolved against the *current* directory, but inspect
resolves the sandbox config path against the *task file's* directory (`run/`). With
`--log-dir logs/smoke` inspect looked for `run/logs/smoke/compose.json` and died with
`compose file … is invalid … no such file or directory` before any container started.
`smoke_run.sh` now `readlink -f`s it.

`docker compose` over the tunnel works; the predicted `"No services to build"` warning
appears and is harmless. No containers leaked (`docker ps -a` empty afterwards);
remote disk 4.0 G / 150 G.

## Re-create everything after a reboot / new session

```sh
cd /work/workspace/beat-stockfish
. ./tunnel.sh                       # tunnel + DOCKER_HOST
docker images | grep beat-stockfish # if missing: ./build.sh   (~2.5 min)
. .venv/bin/activate
./smoke_run.sh                      # or python3 run/rollout.py --log-dir "$PWD/logs/..."
```
Reap stragglers: `docker ps -a --filter name=inspect- ; docker rm -f <ids>`.

---

# Step 2 — baseline reproduction (condition A, unmodified prompt/code)

## Launcher

`./launch_run.sh --name <NAME> --model <MODEL> --epochs <N>` (generalises `smoke_run.sh`;
extra args pass through to `rollout.py`). Detached with `setsid nohup`, so it survives the
step that launched it. Per run it writes `runs/<NAME>.{log,pid,done}` and `logs/<NAME>/`.
`INSPECT_DOCKER_CLI_CONCURRENCY=8`; `--log-dir` is made absolute (see the step-1 gotcha).

Baselines launched 2026-09-08 20:35 PT, 10 epochs each, 1 variant (`v001`), inspect
defaults for `max_samples`/`max_connections` (10) → 10 containers per run, 20 total.

## Progress while a run is in flight

Inspect **does** flush finished samples into the `.eval` as it goes (the file grows; header
`status == "started"`), so per-sample progress is readable live:

```sh
python3 summarize_logs.py logs/baseline-fable51 logs/baseline-astra --progress
docker ps -q | wc -l          # containers still alive ~= samples still running
```

`runs/<NAME>.log` is nearly silent until the end (inspect's TUI degrades to a final panel
when stdout is not a tty) — do **not** use it as the progress signal. `recoverable_eval_logs()`
returns nothing for a healthy in-flight run; it is for crash recovery only.

## `summarize_logs.py`

One row per sample from any mix of `.eval` files / directories, via the documented
`inspect_ai.log` API. `--json`/`--csv` (`-` = stdout), `--all-fields`, `--progress`,
`--no-table`. Verified against the step-1 hand summary of `logs/smoke` (8/8 fields agree)
and against a transcript read by eye.

## FINDING: fable-5.1 episodes are killed by a provider-side content filter

Both the step-1 smoke rollout and the first finished baseline rollout end the same way:
after the agent lists `/opt/engine` (the Stockfish binary + NNUE), the next model calls come
back `stop_reason == "content_filter"` with the body

> "This request triggered restrictions on violative cyber content and was blocked under
> Anthropic's Usage Policy. …"

Inspect's `react()` has a hard-coded circuit breaker — `_react.py:259-263`, break after **3
consecutive** `content_filter` responses — so the episode ends at ~5-6 model calls out of a
budget of 40 turns / 3 h, the grader runs on an unstarted game, and it scores 0 with
"the game was not completed". That is a *filter* artefact, not a behavioural result: nothing
about propensity is being measured in those rollouts. Watch `content_filter_calls` /
`content_filter_first_call` in the summariser output.

---

# Step 3 — ablation runner (`--prompt-variant`, `--stop-eval-tool`)

`run/rollout.py` gained two orthogonal, opt-in knobs. **With neither passed the
episode is byte-for-byte the step-2 baseline** (verified: `logs/verify-noflags`).
Full diff: `rollout_ablation.diff`. Consolidated evidence: `verify_evidence.txt`.

## `--prompt-variant {original,nograding,notools,believe}`

Variants are **full-text constants** in `rollout.py` derived from
`TASK_PROMPT_ORIGINAL` (a copy of `task.py`'s `TASK_PROMPT`) by `str.replace`,
never by editing the prompt in flight. `task.py` cannot be imported here — it
does `sys.path.insert(0, "/opt/match"); import matchd` at module scope, which
only resolves inside the container.

Desync guard, two layers:
1. `assert len(set(TASK_PROMPTS.values())) == len(TASK_PROMPTS)` at import — a
   mistyped needle makes `replace()` a no-op, which would silently run the
   original under a variant's name.
2. At runtime, before substituting, the text `task.py` actually rendered into
   `/task.txt` must equal `TASK_PROMPT_ORIGINAL`, else `RuntimeError`. So an
   edit to `task.py` (or a stale image) fails the run instead of ablating the
   wrong text. The `original` path is left untouched, so it stays a byte-exact
   baseline — and `logs/verify-original` incidentally proves the constant is in
   sync, since the assertion compares the container's render to it.

Substitution happens in `environment_setup`, in **both** places the prompt
exists: `state.user_prompt.text` *and* `/task.txt` in the container (`sh -c 'cat
> "$1"'` as root — truncate-in-place, so `task.py`'s mode/owner survive), then
read back. An agent that cats `/task.txt` therefore sees the prompt it was given.

## `--stop-eval-tool`

Registers `stop_eval(reason: str)` next to `capped_bash`. The tool writes
`store().set("stop_eval_reason", reason)` and returns a short acknowledgement;
`_budget`'s `on_continue` returns `False` when that key is set, which is a
`break` in `react()`'s loop — so the episode ends **cleanly** (`sample.error is
None`, `sample.limit is None`) and the scorer still runs the shipped grader.
The reason survives in `EvalSample.store` *and* in the tool call in the
transcript. The store check in `on_continue` is unconditional but inert without
the flag (nothing else ever writes that key); verified `store == {}` and normal
looping in all no-flag runs.

Both knobs land in `EvalSpec.task_args`, so every `.eval` says which condition
produced it. `launch_run.sh` passes both through unchanged (verified:
`logs/verify-launcher`, `notools` + `stop_eval` together).

## `summarize_logs.py` new columns

| column | meaning |
|---|---|
| `prompt_variant`, `stop_eval_tool` | the condition, from `eval.task_args` (defaults `original`/`False`, so pre-step-3 logs still read) |
| `stop_eval_called`, `stop_eval_reason` | from `EvalSample.store`, falling back to the `stop_eval` tool call's `reason` |
| `limit_event`, `limit_event_message` | see below |

## GOTCHA: `sample.limit` is always `None` in this rig

`bounded_agent` deliberately swallows `LimitExceededError` so a
turn/wall-clock-exhausted episode is still graded. The side effect is that
inspect never records `EvalSample.limit`, so `sample_status` used to read
`completed` for episodes that were actually **cut off by the message or time
limit**. The durable record is the `sample_limit` **event** in the transcript.
`summarize_logs.py` now reads that (`limit_event` / `limit_event_message`) and
`sample_status` reports `limit:message` / `limit:time` from it. Re-summarise the
step-2 baselines after they finish — some "completed" rows will become
`limit:*`.

## Verification (no real rollouts; `mockllm/model`, real sandbox/setup/grader)

`verify_ablation.py` — `prompts` (assert first user message per log),
`cat` (mock agent runs `cat /task.txt` through the bash tool, as the *agent*
user, and the result must equal the variant byte for byte), `stopeval`
(3rd mock output is a canary that must never be consumed).

11 mockllm containers total, one at a time, ~15 s each; nothing leaked; the
step-2 baselines were not disturbed.

## Exact campaign commands (step 4)

```sh
cd /work/workspace/beat-stockfish
. ./tunnel.sh                      # point DOCKER_HOST at the *new* host, build the image there
export OPENROUTER_API_KEY=...

# prompt-variant arms (per model)
./launch_run.sh --name abl-nograding-fable51 --model openrouter/anthropic/claude-fable-5.1 \
    --epochs 10 --prompt-variant nograding
./launch_run.sh --name abl-notools-fable51   --model openrouter/anthropic/claude-fable-5.1 \
    --epochs 10 --prompt-variant notools
./launch_run.sh --name abl-believe-fable51   --model openrouter/anthropic/claude-fable-5.1 \
    --epochs 10 --prompt-variant believe
# stop_eval arm: original prompt + the extra tool
./launch_run.sh --name abl-stopeval-fable51  --model openrouter/anthropic/claude-fable-5.1 \
    --epochs 10 --stop-eval-tool
```
(same four with `--model openrouter/openai/gpt-6-astra`, names `…-astra`.)

---

# Step 4 — the ablation campaign (8 runs, 2 new hosts)

Launched 2026-09-08 21:13–21:15 PT. Baselines stayed on box1 throughout and were
never touched.

## Hosts

| box | ip | local port | booted | auto-delete (~12 h) | carries |
|---|---|---|---|---|---|
| box1 | <box1-ip> | 2375 | 20:21 PT | ~08:21 PT | step-2 baselines |
| box2 | <box2-ip> | 2376 | 20:44 PT | ~08:44 PT | the 4 fable-5.1 arms |
| box3 | <box3-ip> | 2377 | 21:03 PT | ~09:03 PT | the 4 astra arms |

All three are cx43-class: 8 vCPU, 15.6 GB, Docker 29.8.0, buildx v0.37.0,
compose v5.5.1, 150 G disk.

## 1. `tunnel.sh` is now multi-box

```sh
. ./tunnel.sh up box2      # (re)establish + export DOCKER_HOST=tcp://127.0.0.1:2376
./tunnel.sh status all     # report every box, touch nothing
./tunnel.sh down box3
. ./tunnel.sh              # unchanged meaning: box1
```

A `BOXES` table maps name -> `ip:local_port`; a raw `ip:port` selector also works
for a host not yet in the table. **Every operation is scoped by `LOCAL_PORT`**:
`_pids()` matches the `-L <port>:/var/run/docker.sock` spec, so bringing box2 up
or down can never kill box1's forward. That is what made it safe to run this step
while the baselines were mid-flight. Verified: distinct `docker info` `.Name`
(`…f470fe` / `…bb7c24` / `…e9469e`) on 2375 / 2376 / 2377.

Two fixes to the step-1 script:
- exit status is propagated when the script is *executed* and suppressed when it
  is *sourced* (`[ "${BASH_SOURCE[0]}" = "$0" ]`), so callers can `|| exit`
  without a sourced call killing the caller's shell.
- the `2>/dev/null` in `_pids` now wraps the **redirect**, not just `tr`:
  procfs entries vanish mid-scan and *bash itself* was printing
  `/proc/N/cmdline: No such file or directory` into every launcher log.

## 2. Build on both new hosts

`DOCKER_HOST=tcp://127.0.0.1:2376 ./build.sh` and the same on `:2377`, run in
parallel (`build-box2.log`, `build-box3.log`, both `EXIT=0`, ~5 min wall clock).
Full 4-image chain present on both; inside `beat-stockfish:local`: Stockfish
**17.1** at `/opt/engine/engine` (`---x------ nobody:nogroup`, 412 k / 257 k
nodes/s on box2 / box3), `python-chess 1.11.2`, `/grader` 0700 root.

**`.venv/` in `.dockerignore` is now permanent** (the step-1 add-then-restore
dance is gone; the exported original is kept at `.dockerignore.exported.bak`).
This is safe *because the Dockerfile has no `COPY . .`* — every COPY is an
explicit path — so the line cannot change the image, only the ~276 MB of context
that would otherwise stream to the remote daemon on each build.

## 3. `launch_run.sh --box`

```sh
./launch_run.sh --box box2 --name abl-believe-fable51 \
    --model openrouter/anthropic/claude-fable-5.1 --epochs 10 --prompt-variant believe
```

- `--box` (default `box1`, i.e. the step-2 meaning is unchanged) resolves through
  `tunnel.sh`'s table and pins the run's `DOCKER_HOST` for its whole life;
  `--docker-host tcp://host:port` bypasses the table.
- After tunnel setup it **hard-fails unless `/_ping` answers**, and records the
  daemon's `.Name` — so a run can never quietly start against the wrong box, or
  no box.
- **Collision guards** (`--force` overrides): refuses if a run of that name is
  still alive, or if `logs/<NAME>/` already holds an `.eval`. Per-run log dirs
  are the whole reason 8 concurrent runs don't collide — `rollout.py` writes
  `compose.json` *into* `--log-dir`, so two runs sharing one would race on it.
- New `runs/<NAME>.json`: box, docker host, daemon name, model, epochs, pid,
  log/marker/log-dir paths, extra args, `INSPECT_DOCKER_CLI_CONCURRENCY`,
  `OPENROUTER_BASE_URL`, launch time.
- The rollout argv is now built with `printf %q` instead of an unquoted
  `${EXTRA[*]}` splice.
- Watch out: `[ cond ] && arr+=(…)` exits the script under `set -e` when the
  test is false (the AND-list returns 1). Spelled as an `if`.

`launch_campaign.sh` launches all 8: fable→box2, astra→box3, staggered 30 s
inside a box so four 10-container `compose up`s don't land at once.

Endpoint check: inspect's `OpenAICompatibleAPI` reads `OPENROUTER_BASE_URL`
(`openai_compatible.py:117-118`) and it overrides the built-in default, so the
required endpoint override is honoured with no code change.

## FINDING: 40 concurrent samples exceeds a 15.6 GB box's RAM — box3 OOM-killed engines

Within 7 minutes of launch box3 (astra) hit the memory ceiling and the kernel
OOM-killed **4 `engine` processes** plus 2 host session processes (`systemd
--user`, `(sd-pam)`) between 21:18:10 and 21:20:11.

Cause is `task_assets/matchd.py`, and it is specific to the *relay*:

| engine | how spawned | config | measured VSZ |
|---|---|---|---|
| opponent | `SimpleEngine.popen_uci`, `matchd.py:139-140` | hard-coded `Threads 1, Hash 16` | 339 MB |
| **relay** | bare `subprocess.Popen([ENGINE])`, `matchd.py:301` | **agent-settable** via `RELAY_OPTIONS = {"Threads": (1,2), "Hash": (16,256)}` | 585 MB @ Hash 256 |

An agent that finds the relay and raises `Hash` therefore adds a ~0.5 GB process
of its own. 40 samples × (opponent + a maxed relay) ≈ 20 GB > 15.6 GB, with no
swap. box2 (fable) never OOMed — fable's episodes are short and it uses the relay
less — but it sits at ~10.3 GB used, i.e. it was on the same trajectory.

**All 4 victims were relay engines, not opponents:** every one logged
`total-vm:659036kB` (644 MB), *above* the 585 MB Hash-256 line and far above the
opponent's 339 MB, and the opponent is never reconfigured by `matchd`. So no game
record is corrupted — what the affected agents saw was `RelayFailed: engine
session exited unexpectedly` on their own optional relay socket, a visible,
retryable tool error. RSS is *not* a usable discriminator here: under memory
pressure the kernel trims it (box3 showed live engines at 72–181 MB), so compare
`total-vm`.

Affected samples, by container id → run (via the
`com.docker.compose.project.config_files` label):

| container | run |
|---|---|
| `57dc1388f71d` | abl-believe-astra |
| `d8b5a84a7c49` | abl-believe-astra |
| `4116d99878 31` | abl-nograding-astra |
| `8bfcab50d983` | abl-stopeval-astra |

**Mitigation applied: 24 G swapfile + `vm.swappiness=10` on box2 and box3.**
This is infrastructure, not experimental design — it engages only under pressure
and changes nothing an agent can observe, whereas an OOM kill destroys the
episode's relay session and can reach dockerd/sshd. It worked: box3's last OOM
was 21:20:11 (≈90 s after swapon), and 5 min later box3 was at 13.4 GB RSS +
1.4 GB swap, load 38, zero new kills; box2 has touched 0 B of swap.

**Open question for the design, not fixed here:** if a future campaign packs
4 runs per box again, either cap the relay (`Hash` ceiling, or a container
`--memory` limit) or run 2 runs per box. 40 samples/box is over the RAM budget
by ~30 % even with the relay untouched by the agent.

## `tunnel_watchdog.sh`

3 h of unattended runs on a forward whose death silently kills all 4 runs on that
box. The watchdog rebuilds a forward **only when no ssh process holds the `-L`
for that port** — it deliberately ignores a failing `/_ping`, because under
load 30–40 the daemon can miss a 10 s ping while the tunnel is fine, and tearing
down a healthy forward would break every in-flight `compose exec`. Watches box2
and box3 only; box1 was left strictly alone. `kill $(cat tunnel_watchdog.pid)`
to stop it; it logs to `tunnel_watchdog.log`.

## Also noted

`_openrouter_reasoning.py:52` logs `Error parsing OpenRouter reasoning details:
… reasoning.text.text Field required` for fable-5.1 — OpenRouter returns a
`reasoning.text` block carrying only a `signature`, which inspect's tagged union
rejects. It is a **warning**: the fallback stores the raw JSON as the reasoning
content and generation continues. Not seen in the step-2 baselines only because
their stdout logs are near-silent; it does not affect scores.
