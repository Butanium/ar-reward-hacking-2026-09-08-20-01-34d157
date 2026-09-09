# logs-disclosure-wrapup

Copies of the 20 analysed arms (10 phase-1 from `../logs-rescored/`, 10 `p2-*` from
`../logs/`) with **one extra score appended**:

    disclosure_judge_wrapup   <- corrected verdict; final answer = the FULL wrap-up
                                 (all assistant messages after the last non-submit tool
                                 call, labelled "[final answer, part i of N]")
    disclosure_judge          <- ORIGINAL verdict, kept as-is; saw only the LAST message

Everything else in these logs (`shipped_grader`, `engine_use_judge`, samples, events) is
byte-identical to the source arm — the engine judge was not re-run. The source dirs
`../logs/` and `../logs-rescored/` were not modified.

Analysis code should read these copies via `engine_use_lib.resolve_log_dir()` and pick the
verdict with `engine_use_lib.disclosure_verdict()` (prefers `disclosure_judge_wrapup`).

Produced by `../rejudge_disclosure.sh`; per-sample old -> new diff in
`../disclosure_rejudge.csv`; see the "Disclosure verdicts" section of `../REMOTE_NOTES.md`.
