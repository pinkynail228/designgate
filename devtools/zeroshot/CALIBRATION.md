# DeepSeek worker calibration

One row per finished `ZS_RUNTIME=deepseek` run. Objective columns first; the orchestrator's judgement last. Unknown = `?`.

| Date | Task | Grade | Worker effort | Visits (worker) | Review rounds | Findings (blocker/major/minor) | Wall time | Cost | Result | Orchestrator diff verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-16 | m0b-fork-ci (CI workflow) | medium | high | 2 | 2 (1 reject -> repair -> accepted) | ? | ~17 min | 6.31M in (5.35M cached) / 68K out | CI green first try: 740 tests, lint 0, GHCR digest sha256:83215dd3 | good; wrong dhi.io auth assumption (pull is anonymous), stale NOTE comment to drop |
| 2026-10-17 | ds1-per-asset-update (sol1 one-reviewer) | T2 | high | 2 | 2 (1 reject -> accepted) | ? | 26 min | 23.0M in / 119K out | lint clean locally; train wave1 | good; sync-status guard kept for full-library sync |
| 2026-10-17 | c3c3-pixel-grid-keys (sol1 one-reviewer) | T2 | high | 1 | 1 (accepted) | 0 | 8 min | 6.5M in / 31K out | lint clean locally; train wave1 | good; orchestrator added :show-command for Cmd+Shift+' |
| 2026-10-17 | c2d-canvas-edge (sol1 one-reviewer) | T2 | high | 3 | 3 (exhausted) | ? | 38 min | 32.2M in / 159K out | exhausted; orchestrator fixed wasm frame-visible? | reviewer was right: wasm renderer still drew the ruler frame |
| 2026-10-17 | c2d-m rerun (sol1m: reviewer medium) | T2 | high | 3 | 3 (exhausted) | ? | 26 min (worker 20, review 6) | 26.0M in / 106K out | exhausted | known answer hit: rejected for the wasm ruler frame |
| 2026-10-17 | ds1-m rerun (sol1m) | T2 | high | 1 | 1 (accepted) | 0 | 12 min (worker 10, review 1.7) | 12.7M in / 55K out | accepted | MISS: shadow sol-high review found applied* reset on tab switch (P2), also present in the merged sol1 ds1; fixed in dg/ds1-applied-fix |
| 2026-10-17 | c3c3-m rerun (sol1m) | T2 | high | 1 | 1 (accepted) | 0 | 8 min (worker 6.4, review 1.6) | 6.9M in / 35K out | accepted | same as sol1 |
| 2026-10-17 | ds9b-version-layer-diff (sol1m) | T2 | high | 1 | 1 (accepted) | 0 | 7 min (worker 5.1, review 1.6) | 6.1M in / 32K out | scope OK, lint CI green; shadow sol-high: accepted | good |

## Per-node wall time (node-times.py, 2026-10-17)

17 runs 2026-10-16..17: worker (deepseek-v4.1-flash) 313 min in total, reviewers ~121 min (two sol nodes ran in parallel, counted once per round). The worker, not the reviewer, sets the run's wall time: c2d 28 of 36 min, ds1 21 of 25 min. Big cards (c1, ds2, ds9a, c3c2) take 30-42 min of worker time.

## Reviewer medium vs high (wave 2, 2026-10-17)

sol1m matched sol1 on the c2d known answer and on c3c3, at ~1.6-1.9 min per review vs 4.7-8.1 min. It missed one P2 on ds1 (state reset on tab switch) that a shadow high review caught; sol1 had missed it too. Small sample: keep medium only with a high shadow review on data/state cards.

## Calibrated reviewer rules (bot wave 1, 2026-10-21)

After the preamble blocking bar, round freeze and test budget (bdd25c8340), all four bot cards (t0, d1, d2, d3; sol1,
DeepSeek worker) were accepted in review round 1 (1.3-5.9M input tokens each). Before: the bota run
took 4 rounds and ~99 min with 21% reviewer precision. Cards carried recorded live payloads instead of invented
ones. Orchestrator review still found one gap the reviewer passed (d1: `@Ivan`/`До` not case-insensitive while the
people lookup is). Reviewers still ran 14-20 mutations per card against a one-guard budget: cost, not rounds.
