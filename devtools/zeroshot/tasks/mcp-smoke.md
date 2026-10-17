Sandbox and Serena smoke test (no code change expected).

1. List the MCP tools available to you in this session (server.tool names).
2. Call Serena `find_referencing_symbols` (or `find_symbol` first if needed) for the function `updates-tab*` in `frontend/src/app/main/ui/workspace/libraries.cljs` and report the file:line of each reference.
3. Run each command and report whether it succeeded: `git status --porcelain`; `ls ~/.codex`; `ls ~/Documents/PenPot/critic`; `mktemp -d /tmp/zs-agent-XXXXXX` (then remove it); `python3 -c "import urllib.request; urllib.request.urlopen('https://example.com', timeout=5)"`.

Do not edit any file. Your final summary must contain the lines `MCP_TOOLS: <list or none>`, `SERENA_REFS: <file:line list or failed: reason>`, `SANDBOX: git=<ok|failed> codex_home=<readable|denied> owner_docs=<readable|denied> tmp=<ok|failed> network=<ok|denied>`.
Allowed paths: none.
