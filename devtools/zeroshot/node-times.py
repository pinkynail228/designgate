#!/usr/bin/env python3
"""Per-node wall time of a finished Zeroshot run (critic/12: record per-node time and usage).

Usage: devtools/zeroshot/node-times.py RUN_ID
Prints one line per execution (node, minutes from its first to its last log record, record count)
and the run's token usage. Time is approximate: model wait after the last log record is not counted.
"""
import json
import selectors
import subprocess
import sys


def stream(cmd, stop_cursor, idle=15):
    """Yield NDJSON records of a following zeroshot command until stop_cursor or idle seconds."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    try:
        while sel.select(idle):
            line = proc.stdout.readline()
            if not line:
                break
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            yield rec
            if int(str(rec.get("cursor", "v2:0")).split(":")[1]) >= stop_cursor:
                break
    finally:
        proc.kill()


def main(run_id):
    status = json.loads(subprocess.check_output(["zeroshot", "status", run_id], text=True))
    if status["status"]["phase"] != "finished":
        sys.exit("run is not finished")
    last = int(status["atCursor"].split(":")[1])
    node_of = {}
    for rec in stream(["zeroshot", "watch", run_id], last):
        for ex in rec.get("status", {}).get("activeExecutions", []):
            node_of[ex["execution"]] = ex["node"]
    spans = {}
    for rec in stream(["zeroshot", "logs", run_id], last - 1):
        ex = rec.get("execution")
        if ex:
            s = spans.setdefault(ex, [rec["timestamp"], rec["timestamp"], 0])
            s[1] = rec["timestamp"]
            s[2] += 1
    total = {}
    print(f"{status['title']}: {status['status']['terminalResult']}")
    for ex, (start, end, n) in sorted(spans.items(), key=lambda kv: kv[1][0]):
        node = node_of.get(ex, "?")
        minutes = (end - start) / 60000
        total[node] = total.get(node, 0) + minutes
        print(f"  {node:12} {minutes:6.1f} min  {n:4} records")
    print("  total: " + ", ".join(f"{k} {v:.1f} min" for k, v in total.items()))
    usage = status["status"].get("metadata", {}).get("tokenUsage", {})
    print(f"  tokens: in {usage.get('inputTokens', 0) / 1e6:.1f}M"
          f" (cached {usage.get('cacheReadInputTokens', 0) / 1e6:.1f}M),"
          f" out {usage.get('outputTokens', 0) / 1e3:.0f}K")


if __name__ == "__main__":
    main(sys.argv[1])
