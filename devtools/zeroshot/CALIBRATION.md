# DeepSeek worker calibration

One row per finished `ZS_RUNTIME=deepseek` run. Objective columns first; the orchestrator's judgement last. Unknown = `?`.

| Date | Task | Grade | Worker effort | Visits (worker) | Review rounds | Findings (blocker/major/minor) | Wall time | Cost | Result | Orchestrator diff verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-16 | m0b-fork-ci (CI workflow) | medium | high | 2 | 2 (1 reject -> repair -> accepted) | ? | ~17 min | 6.31M in (5.35M cached) / 68K out | CI green first try: 740 tests, lint 0, GHCR digest sha256:83215dd3 | good; wrong dhi.io auth assumption (pull is anonymous), stale NOTE comment to drop |
