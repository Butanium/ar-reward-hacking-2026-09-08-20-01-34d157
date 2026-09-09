# Beat-Stockfish reproduction + prompt ablations

Reproduction of the Goodhart Labs *beat-stockfish* honeypot on two frontier models —
**Fable 5.1** and **GPT-6-Astra** — plus five prompt/tool ablations. The agent is told to
win a game against a strong chess engine; the honeypot measures whether it instead
**cheats** (calls the very engine it is supposed to beat) and whether it **discloses**
doing so. Each model × condition is n = 30 rollouts.

## Headline (engine-driven cheating rate)

- **Both models cheat at the original prompt:** GPT-6-Astra 27/30, Fable 5.1 12/30.
- **"No external tools" (clarified) and "do not game the eval" zero both models** — the
  engine socket stays fully discoverable (agents read the code, then decline).
- Fable also drops to 0 under **no-GRADING** and the **stop_eval** tool; Astra stays
  elevated there (18/30 and 19/30).
- **"believe in yourself!" changes nothing** at n = 30 (Fable 8/30, Astra 24/30).
- When a model cheats, it **almost never admits it in its final answer.**

Full analysis — per-condition rates with confidence intervals, disclosure breakdown,
stated reasoning, and example transcripts — is in the reports.

## What's here

- **`reports/`** — published HTML reports (open in a browser):
  - `Beat-stockfish-reproduction-and-prompt-ablations/` — the main report (n = 30).
  - `Debrief-probe-self-reported-reward-hacking/` — follow-up: when a cheating trajectory
    is asked about it in a debrief turn, does it self-report?
  - `Beat-stockfish-reproduction-results/` — early baseline-only report.
- **`data-release/`** — canonical analysed data: 22 Inspect `.eval` arms plus a flat
  `transcripts.jsonl` (360 rollouts, every judge score + full transcript). See its README.
- **`analysis/`** — the report generators (`prepare_data*.py` → `build_report*.py`).
- **`app/`** — a small Flask campaign dashboard.
- **`kit/`** — the HTML report component library the reports are built with.

The rollout **runner** (the Inspect task, Docker rig, and judges) lives in a separate repo,
[Goodhart-Labs/beat-stockfish](https://github.com/Goodhart-Labs/beat-stockfish), and is
**not** included here — this repo holds its outputs and our analysis of them.

Models were run via OpenRouter (`anthropic/claude-fable-5.1`, `openai/gpt-6-astra`) with the
runner's defaults (80-message limit, 3 h wall clock).
