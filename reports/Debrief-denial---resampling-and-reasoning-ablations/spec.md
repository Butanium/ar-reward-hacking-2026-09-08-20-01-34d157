# Debrief denial — resampling and reasoning ablations

Why does one Fable 5.1 cheating trajectory answer "reward-hacked: false" every time in the debrief probe, while most others say "true"?

- **Setup**: the debrief probe (model, replayed transcript, the debrief message verbatim), the trajectories resampled, 10 draws per condition, sampling config.
- **Denial is a stance, not concealment**: every "false" answer still describes the engine use in full; verbatim examples of a "false" and a "true" answer.
- **Per-trajectory denial rates** across the 9 resampled Fable 5.1 trajectories, with CIs, annotated with game outcome and the reasoning-judge verdict.
- **Interventions on the replayed transcript**: single-passage deletion/insertion (null), all reasoning stripped (denial disappears), per-turn and per-phase reasoning removal on the 10/10 trajectory (no turn or phase is necessary; the discovery phase alone is not sufficient). Denial rate vs amount of reasoning kept.
- **Explorer** over all resampled debrief answers (full text), filterable by trajectory, condition and self-report.
- Appendix: exact edit texts, the reasoning-block map of the 10/10 trajectory, parse-failure counts.