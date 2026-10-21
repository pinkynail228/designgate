Test environment for every agent in this run (worker, reviewers):
- Do not try to build the frontend (`pnpm test`, `build:*`, shadow-cljs, `render-wasm/build`) or install toolchains. The build and tests run in the fork's CI on the candidate SHA after this run.
- Lint is local and mandatory for every changed `.clj/.cljs/.cljc` file, run from `frontend/`: `cljfmt check <files>` (fix with `cljfmt fix <files>`) and `clj-kondo --config-dir ../.clj-kondo --lint <files>` (0 errors, no new warnings). A diff that fails either is not done.
- In-run checks you can and must run when relevant: `msgfmt -c --statistics -o /dev/null frontend/translations/<lang>.po`, plain `node`/`python3` scripts that read `.po`/source files, `git diff`, `grep`. Use the exact checks the task names.
- Before any check that writes files, create a private scratch dir with `mktemp -d /tmp/zs-agent-XXXXXX` and use it as TMPDIR. For revert/mutation checks copy the repo into it with `python3 -c 'import shutil,sys; shutil.copytree(".", sys.argv[1], ignore=shutil.ignore_patterns(".git","node_modules",".shadow-cljs","target",".pnpm-store"))' /tmp/zs-agent-.../repo`; never revert files in the shared worktree. `rm -rf` your scratch dir before you finish.
- Do not commit, push, or use the network.
- Shell commands run in a sandbox: write only inside the worktree and `/tmp`, no network, the home dir is unreadable (`HOME` is `/tmp/zs-home`). If a check you need is blocked by the sandbox, do not work around it: report it as the result.
- Code navigation: the Serena tools `get_symbols_overview`, `find_symbol`, `find_referencing_symbols` (Clojure, read-only) find definitions and call sites across `frontend/` and `common/` (their line numbers are 0-based: add 1 for `file:line`); use them before wide `rg` searches when tracing who uses a function or key. `rg` stays fine for text, keywords and translations. Edit files with your normal tools.

Repository invariants (on top of the FigmaPot agent rules in AGENTS.override.md):
- Touch only the paths the task allows. A file outside them, including a new untracked file, is a scope failure.
- User-visible branding stays Penpot; FigmaPot is the fork name only and never appears in the UI. Translations keep every placeholder of the `en.po` msgid (`%s`, `%d`, `{{...}}`) and Russian plural forms.
- Keep the diff small and in upstream style: it must merge with future Penpot releases.

Roles in this repo (on top of the graph's built-in instructions):
- Worker: implement exactly the task; smallest coherent change. Final summary maps each acceptance criterion to evidence (command result or file:line) and lists what was left to CI or the owner.
- Worker, card gaps (critic/15): if two parts of the card contradict each other, or the outcome needs a path the card does not allow, stop before editing and report it as the result. A small gap that an existing code pattern settles: follow the pattern and record the choice. The final summary ends with "Interpretations:" listing each such choice (unclear phrase -> decision -> file:line), or "none found".
- Worker, every visit (first and repair): the first action is a shell command (`git status --porcelain`); a visit that ends without a file change or a check command is a failure, not a result.
- Code reviewer: correctness against the card, invariants above, scope (allowed paths), and that each test the card asks for fails when its guarded behavior is removed.
- Reviewers, blocking bar (reviewer-precision audit 2026-10-21: rounds 2-4 found 0 defects that mattered and 11 new issues in old code): reject only for a violated acceptance criterion or listed invariant, triggered by an input the real system can produce (name where it comes from: the card, a recorded payload, or the producing code `file:line`), shown by a test or command. Every rejection names the criterion or invariant, `file:line` and that check. A failure mode the card does not name, or a server response you invented, is a note, not a rejection.
- Reviewers, rounds: round 1 is the full review; report every blocking finding at once. In later rounds only verify that each earlier finding is fixed and that the repair broke nothing in the lines it changed; an issue in code the repair did not touch is a note. Do not repeat a rejection the worker already answered unless you add new evidence.
- Reviewers, tests: ask for one test per acceptance criterion the change implements, failing without the guarded code; nothing more. Do not reject for missing extra edge cases, extra coverage, or inputs the card does not name.
- Reviewers, blocked checks: if the sandbox blocks a check (sockets, network, build), mark it pending with the reason. Do not replace it with your own probe against invented responses and do not reject because of it.
- Reviewers, notes: non-blocking notes go last under "Notes (non-blocking)", at most 5, and never change the verdict.
- Acceptance reviewer: verify each criterion with concrete evidence. List separately what is left to CI, preview or the owner; never mark those as passed and do not reject only because they are pending.
- Repair: fix only the blocking findings without weakening the requested behavior; ignore notes.
- Everyone: leave AGENTS.md, AGENTS.override.md and devtools/zeroshot alone unless the task says otherwise. If Zeroshot itself gets in the way, say so in your summary.
