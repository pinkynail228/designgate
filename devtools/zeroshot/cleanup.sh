#!/bin/sh
# Release finished runs: remove $ZS_ROOT/NAME.lock and NAME.tmp when the newest Zeroshot run titled
# "figmapot NAME" (or "designgate NAME") is finished. A lock without any listed run is kept (unknown status keeps
# data, critic/18) but stops counting as active after 10 min; it is reported on stderr for the orchestrator.
# Worktrees stay (they hold the diff for triage); remove them with `git worktree remove` after merge.
# Prints the number of still active runs. run.sh calls this before admission.
set -eu
base=${ZS_ROOT:-/Users/Shared/figmapot/runs}
mkdir -p "$base"
zeroshot list | python3 -c '
import json, os, shutil, sys, time
base = sys.argv[1]
newest = {}
for r in json.load(sys.stdin)["runs"]:
    parts = r["title"].split()
    if len(parts) == 2 and parts[0] in ("figmapot", "designgate"):
        if parts[1] not in newest or r["runId"] > newest[parts[1]]["runId"]:
            newest[parts[1]] = r
active = 0
for entry in sorted(os.listdir(base)):
    if not entry.endswith(".lock"):
        continue
    name = entry[:-5]
    lock = os.path.join(base, entry)
    run = newest.get(name)
    locked_at = os.path.getmtime(lock)
    # runId is a UUIDv7: its first 48 bits are the submit time in ms. A lock newer than the newest
    # listed run belongs to a run that is starting (ZS_ATTEMPT rerun) and is not listed yet.
    if run and int(run["runId"].replace("-", "")[:12], 16) / 1000 >= locked_at - 5:
        done = run["status"]["phase"] == "finished"
    else:
        if time.time() - locked_at > 600:
            print("stale lock without a listed run, kept: " + lock, file=sys.stderr)
        else:
            active += 1
        continue
    if done:
        os.rmdir(lock)
        shutil.rmtree(os.path.join(base, name + ".tmp"), ignore_errors=True)
    else:
        active += 1
print(active)
' "$base"
