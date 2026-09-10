# Fable 5 on the stop_eval condition

30 rollouts of the beat-stockfish honeypot on `anthropic/claude-fable-5` (via OpenRouter) under condition E — the original task prompt plus a `stop_eval(reason)` tool — with the same runner, budgets (80 messages / 3 h), grader and judges as the campaign's Fable 5.1 arms.

## Contents
- **Outcome breakdown** (engine-driven cheating with disclosure split, probe-only, clean completed game, provider refusal, called stop_eval, resigned, ran out of turns), Wilson 95% CIs, side by side with Fable 5.1's stop_eval arm (n=30) and Fable 5.1's original-prompt arm for reference.
- **stop_eval calls**: how many, and every recorded `reason` verbatim.
- **Game results** (win/draw/loss) by method.
- Example rollouts shown in full (full transcripts with reasoning summaries).

Metadata: models `anthropic/claude-fable-5` and `anthropic/claude-fable-5.1` via OpenRouter, provider-default sampling; judges GLM-5.3-flash (engine use, wrap-up disclosure).