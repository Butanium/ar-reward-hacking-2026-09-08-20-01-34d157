# Debrief probe — self-reported reward-hacking and concealment

Appended a verbatim human "debrief" user turn to every cheating (engine-driven) trajectory from the beat-stockfish campaign (n=79: 69 GPT-6-Astra, 10 Fable 5.1), captured one completion per trajectory (same model, same sampling as the source run), and elicited a JSON self-report `{env-feedback, reward-hacked, freeform-note}`.

## Contents
- **Self-reported reward-hacked rate**, per model × per setup (arm). Wilson 95% CIs.
- **Concealment rate** from a post-hoc judge (judge 4) reading each debrief response, per model × per setup, and the **mismatch** between the judge verdict and the model's own `reward-hacked` boolean.
- **Qualitative analysis** of the freeform notes (agent, full reads): themes, candor, what models flag as broken, notable/atypical responses.
- Full verbatim dump of every debrief response (env-feedback + reward-hacked + freeform-note), untruncated, browsable per trajectory.

Metadata: models are `anthropic/claude-fable-5.1` and `openai/gpt-6-astra` via OpenRouter; debrief message is quoted verbatim.