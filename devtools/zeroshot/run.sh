#!/bin/sh
# Run a local Zeroshot software-change review cycle on DesignGate in an isolated worktree.
# Usage: [ZS_ATTEMPT=2] devtools/zeroshot/run.sh NAME TASK_FILE [--validate-only]  (ZS_ATTEMPT: rerun after a failed run on the same worktree HEAD)
# TASK_FILE is plain text; the shared test-environment preamble is appended.
# ZS_RUNTIME=deepseek: DeepSeek worker through the local Yandex adapter (devtools/zeroshot/adapter.sh, port
# ${ADAPTER_PORT:-8798}) and gpt-6.1-sol xhigh reviewers on the default Codex lane (runtime-deepseek.json).
# ZS_RUNTIME=deepseek-fast: same worker, gpt-6.1-sol high reviewers; for frontend/UI cards, not backend/data model.
# After a run: devtools/zeroshot/scope-check.sh /tmp/zs-designgate/NAME TASK_FILE before committing anything.
# Parallel runs: one NAME per run (own worktree/branch); a second start of an active NAME is refused (lock dir
# /tmp/zs-designgate/NAME.lock). Each run gets its own TMPDIR (NAME.tmp). The native run can outlive this CLI
# (Ctrl-C/detach), so both stay until the orchestrator's cleanup after a terminal status (AGENTS.md step 3).
set -eu
name=$1; task=$2; shift 2
repo=$(git rev-parse --show-toplevel)
here=$repo/devtools/zeroshot
work=/tmp/zs-designgate/$name
mkdir -p /tmp/zs-designgate
# Disk guard: reviewer scratch copies filled the disk once (STATE Open). Threshold ZS_MIN_FREE_GB (default 30).
free=$(df -Pk /tmp | awk 'NR==2 {print int($4/1048576)}')
[ "$free" -ge "${ZS_MIN_FREE_GB:-30}" ] || { echo "only ${free} GB free on /tmp; clean /tmp/zs-agent-* of finished runs" >&2; exit 2; }
stale=$(find -H /tmp -maxdepth 1 -name 'zs-agent-*' -mmin +1440 2>/dev/null | wc -l | tr -d ' ')
[ "$stale" = 0 ] || echo "note: $stale scratch dirs /tmp/zs-agent-* older than 24 h (check they belong to finished runs)" >&2
mkdir "$work.lock" 2>/dev/null || { echo "run $name is already active ($work.lock)" >&2; exit 2; }
mkdir -p "$work.tmp"; export TMPDIR=$work.tmp
[ -d "$work" ] || { git -C "$repo" worktree add -q -b "zs-$name" "$work" HEAD; git -C "$work" rev-parse HEAD > "$work.base"; }
mkdir -p "$work/.claude"
cp "$here/agent-settings.json" "$work/.claude/settings.local.json"
python3 -c 'import json,sys; print(json.dumps({"task": open(sys.argv[1]).read().strip() + "\n\n" + open(sys.argv[2]).read()}))' \
  "$task" "$here/preamble.md" > "$work.input.json"
runtime=$here/runtime.json
if [ "${ZS_RUNTIME:-}" = deepseek ] || [ "${ZS_RUNTIME:-}" = deepseek-fast ]; then
  runtime=$here/runtime-$ZS_RUNTIME.json
  port=${ADAPTER_PORT:-8798}
  python3 -c 'import sys,urllib.request; sys.exit(urllib.request.urlopen(sys.argv[1], timeout=2).read() != b"{\"adapter\":\"designgate-yandex\"}")' \
    "http://127.0.0.1:$port/designgate-adapter/health" 2>/dev/null \
    || { echo "Yandex adapter is not answering on 127.0.0.1:$port; start devtools/zeroshot/adapter.sh" >&2; exit 2; }
  export GATEWAY_BASE_URL=http://127.0.0.1:$port/v1 GATEWAY_API_KEY=dummy
fi
cd "$work"
exec zeroshot run --title "designgate $name" --template software-change --input "$work.input.json" \
  --runtime-config "$runtime" --submission-key "designgate-$name-$(git rev-parse --short HEAD)${ZS_ATTEMPT:+-$ZS_ATTEMPT}" "$@"
