#!/bin/sh
# Fail closed if a worker's worktree leaves its task's scope: commits on top of the recorded base, or
# tracked/untracked changes outside the task's "Allowed paths:" line. Run before every orchestrator commit.
# Usage: devtools/zeroshot/scope-check.sh WORKTREE TASK_FILE
set -eu
work=$1; task=$2
base=$(cat "$work.base" 2>/dev/null || true)
[ -n "$base" ] || { echo "SCOPE_FAIL: no recorded base ($work.base)"; exit 1; }
[ "$(git -C "$work" rev-parse HEAD)" = "$base" ] || { echo "SCOPE_FAIL: worktree HEAD moved off base $base (worker committed?)"; exit 1; }
# AGENTS.override.md is git-excluded, so check its content against the hash run.sh recorded.
if [ -f "$work.agents.sha" ]; then
  [ "$(shasum -a 256 "$work/AGENTS.override.md" 2>/dev/null | cut -d' ' -f1)" = "$(cat "$work.agents.sha")" ] \
    || { echo "SCOPE_FAIL: AGENTS.override.md changed or missing"; exit 1; }
fi
python3 -I - "$work" "$task" <<'PY'
import re, subprocess, sys
work, task = sys.argv[1:]
m = re.search(r'^Allowed paths:\s*(.+)$', open(task).read(), re.M)
if not m:
    print("SCOPE_FAIL: task has no 'Allowed paths:' line"); sys.exit(1)
allowed = [p.strip().rstrip('.') for p in m.group(1).split('Nothing else')[0].split(',')]
allowed = [p for p in allowed if p]
odd = [p for p in allowed if ' ' in p]
if odd:
    print("SCOPE_FAIL: unparsable allowed path(s):", odd); sys.exit(1)
out = subprocess.run(['git', '-C', work, 'status', '--porcelain', '-uall', '--no-renames'],
                     capture_output=True, text=True, check=True).stdout
changed = [line[3:] for line in out.splitlines()]
def ok(p):
    return p.startswith('.claude/') or any(p == a or (a.endswith('/') and p.startswith(a)) for a in allowed)
bad = [p for p in changed if not ok(p)]
if bad:
    print("SCOPE_FAIL: outside allowed paths:"); print("\n".join("  " + p for p in bad)); sys.exit(1)
print(f"SCOPE_OK: {len(changed)} changed path(s) within {len(allowed)} allowed")
PY
