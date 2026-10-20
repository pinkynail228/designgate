#!/bin/sh
# Run a local Zeroshot software-change review cycle on DesignGate in an isolated worktree.
# Usage: [ZS_ATTEMPT=2] devtools/zeroshot/run.sh NAME TASK_FILE [--validate-only]  (ZS_ATTEMPT: rerun after a failed run on the same worktree HEAD)
# TASK_FILE is plain text; the shared test-environment preamble is appended.
# ZS_RUNTIME=deepseek: DeepSeek worker through the shared Yandex adapter (launchd instance
# local.yandex-adapter.designgate on ${ADAPTER_PORT:-8798}; code and rules in ~/.local/share/yandex-adapter)
# and gpt-6.1-sol high reviewers on the default Codex lane (runtime-deepseek.json).
# ZS_RUNTIME=deepseek-fast: same worker, gpt-6.1-sol high reviewers; for frontend/UI cards, not backend/data model.
# Run state lives under ZS_ROOT (default /Users/Shared/figmapot/runs, survives reboots): worktree NAME, NAME.lock,
# NAME.tmp, NAME.base. The worktree starts from ZS_BASE (default origin/figmapot), not from the lane's HEAD.
# After a run: devtools/zeroshot/scope-check.sh $ZS_ROOT/NAME TASK_FILE before committing anything.
# Parallel runs (at most ZS_MAX_RUNS, see cleanup.sh): one NAME per run (own worktree/branch); a second start of an active NAME is refused (lock dir
# $ZS_ROOT/NAME.lock). Each run gets its own TMPDIR (NAME.tmp). The native run can outlive this CLI
# (Ctrl-C/detach), so both stay until the orchestrator's cleanup after a terminal status (AGENTS.md step 3).
set -eu
name=$1; task=$2; shift 2
repo=$(git rev-parse --show-toplevel)
here=$repo/devtools/zeroshot
root=${ZS_ROOT:-/Users/Shared/figmapot/runs}; export ZS_ROOT=$root
work=$root/$name
mkdir -p "$root"
# Admission (critic/12): release finished runs, then cap parallel runs at ZS_MAX_RUNS (default 4).
# Start runs one after another; two simultaneous starts can both pass the count.
active=$(sh "$here/cleanup.sh")
[ "$active" -lt "${ZS_MAX_RUNS:-4}" ] || { echo "$active runs active (ZS_MAX_RUNS=${ZS_MAX_RUNS:-4}); wait for one to finish" >&2; exit 2; }
# Disk guard: reviewer scratch copies filled the disk once (STATE Open). Needs ZS_MIN_FREE_GB (default 30)
# plus a ZS_GB_PER_RUN (default 10) reserve for every active run.
free=$(df -Pk /tmp | awk 'NR==2 {print int($4/1048576)}')
need=$(( ${ZS_MIN_FREE_GB:-30} + active * ${ZS_GB_PER_RUN:-10} ))
[ "$free" -ge "$need" ] || { echo "only ${free} GB free on /tmp, need ${need}; clean /tmp/zs-agent-* of finished runs" >&2; exit 2; }
stale=$(find -H /tmp -maxdepth 1 -name 'zs-agent-*' -mmin +1440 2>/dev/null | wc -l | tr -d ' ')
[ "$stale" = 0 ] || echo "note: $stale scratch dirs /tmp/zs-agent-* older than 24 h (check they belong to finished runs)" >&2
mkdir "$work.lock" 2>/dev/null || { echo "run $name is already active ($work.lock)" >&2; exit 2; }
mkdir -p "$work.tmp"; export TMPDIR=$work.tmp
base=${ZS_BASE:-origin/figmapot}
[ -d "$work" ] || { git -C "$repo" worktree add -q -b "zs-$name" "$work" "$base"; git -C "$work" rev-parse HEAD > "$work.base"; }
echo "worktree $work at $(cat "$work.base") ($base)" >&2
mkdir -p "$work/.claude"
cp "$here/agent-settings.json" "$work/.claude/settings.local.json"
python3 -c 'import json,sys; print(json.dumps({"task": open(sys.argv[1]).read().strip() + "\n\n" + open(sys.argv[2]).read()}))' \
  "$task" "$here/preamble.md" > "$work.input.json"
runtime=$here/runtime.json
if [ "${ZS_RUNTIME:-}" = deepseek ] || [ "${ZS_RUNTIME:-}" = deepseek-fast ] || [ "${ZS_RUNTIME:-}" = sol1 ] || [ "${ZS_RUNTIME:-}" = sol1m ]; then
  runtime=$here/runtime-$ZS_RUNTIME.json
  port=${ADAPTER_PORT:-8798}
  python3 -c 'import sys,urllib.request; sys.exit(urllib.request.urlopen(sys.argv[1], timeout=2).read() != b"{\"adapter\":\"designgate-yandex\"}")' \
    "http://127.0.0.1:$port/designgate-adapter/health" 2>/dev/null \
    || { echo "Yandex adapter is not answering on 127.0.0.1:$port; run ~/.local/share/yandex-adapter/install.sh designgate $port" >&2; exit 2; }
  export GATEWAY_BASE_URL=http://127.0.0.1:$port/v1 GATEWAY_API_KEY=dummy
fi
# Isolated Codex home and agent sandbox (critic/16), regenerated each run so new private dirs stay denied.
export CODEX_HOME="${ZS_CODEX_HOME:-$HOME/.codex-zs}"
sh "$here/codex-home.sh" "$CODEX_HOME"
# Serena: Clojure only, read-only (gitignored local override); reuse the lane's symbol cache if present.
# Serena migrates the tracked .serena/project.yml on load; keep that rewrite out of the run's diff.
git -C "$work" update-index --skip-worktree .serena/project.yml
printf '%s\n' '# Zeroshot run: Clojure only, no edits through Serena.' 'languages:' '- clojure' 'read_only: true' > "$work/.serena/project.local.yml"
[ -d "$work/.serena/cache" ] || [ ! -d "$repo/.serena/cache" ] || cp -R "$repo/.serena/cache" "$work/.serena/cache"
# Worker/reviewer rules replace the repo AGENTS.md (critic/17): read-only copy, hidden from git status,
# its hash recorded so scope-check can tell it was not edited.
rm -f "$work/AGENTS.override.md"; cp "$here/AGENTS.worker.md" "$work/AGENTS.override.md"; chmod 444 "$work/AGENTS.override.md"
grep -qx '/AGENTS.override.md' "$(git -C "$work" rev-parse --git-path info/exclude)" 2>/dev/null \
  || echo '/AGENTS.override.md' >> "$(git -C "$work" rev-parse --git-path info/exclude)"
shasum -a 256 "$here/AGENTS.worker.md" | cut -d' ' -f1 > "$work.agents.sha"
cd "$work"
# ZS_RUNTIME=sol1: one reviewer (acceptance + code) and at most 3 change iterations (critic/12);
# sol1m: same graph with the reviewer at medium effort (calibration against sol1).
graph=(--template software-change)
case "${ZS_RUNTIME:-}" in sol1|sol1m) graph=(--graph "$here/graph-one-reviewer.json") ;; esac
exec zeroshot run --title "figmapot $name" "${graph[@]}" --input "$work.input.json" \
  --runtime-config "$runtime" --submission-key "designgate-$name-$(git rev-parse --short HEAD)${ZS_ATTEMPT:+-$ZS_ATTEMPT}" "$@"
