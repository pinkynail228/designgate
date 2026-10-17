# DeepSeek worker calibration

One row per finished `ZS_RUNTIME=deepseek` run. Objective columns first; the orchestrator's judgement last. Unknown = `?`.

| Date | Task | Grade | Worker effort | Visits (worker) | Review rounds | Findings (blocker/major/minor) | Wall time | Cost | Result | Orchestrator diff verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-16 | m0b-fork-ci (CI workflow) | medium | high | 2 | 2 (1 reject -> repair -> accepted) | ? | ~17 min | 6.31M in (5.35M cached) / 68K out | CI green first try: 740 tests, lint 0, GHCR digest sha256:83215dd3 | good; wrong dhi.io auth assumption (pull is anonymous), stale NOTE comment to drop |
| 2026-10-17 | ds1-per-asset-update (sol1 one-reviewer) | T2 | high | 2 | 2 (1 reject -> accepted) | ? | 26 min | 23.0M in / 119K out | lint clean locally; train wave1 | good; sync-status guard kept for full-library sync |
| 2026-10-17 | c3c3-pixel-grid-keys (sol1 one-reviewer) | T2 | high | 1 | 1 (accepted) | 0 | 8 min | 6.5M in / 31K out | lint clean locally; train wave1 | good; orchestrator added :show-command for Cmd+Shift+' |
| 2026-10-17 | c2d-canvas-edge (sol1 one-reviewer) | T2 | high | 3 | 3 (exhausted) | ? | 38 min | 32.2M in / 159K out | exhausted; orchestrator fixed wasm frame-visible? | reviewer was right: wasm renderer still drew the ruler frame |

## Per-node wall time (node-times.py, 2026-10-17)

17 runs 2026-10-16..17: worker (deepseek-v4.1-flash) 313 min in total, reviewers ~121 min (two sol nodes ran in parallel, counted once per round). The worker, not the reviewer, sets the run's wall time: c2d 28 of 36 min, ds1 21 of 25 min. Big cards (c1, ds2, ds9a, c3c2) take 30-42 min of worker time.
