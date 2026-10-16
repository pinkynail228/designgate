Test environment for every agent in this run (worker, reviewers):
- This Mac has no JDK, clojure, emsdk, clj-kondo or cljfmt. Do not try to build the frontend (`pnpm test`, `build:*`, shadow-cljs, `render-wasm/build`) or install toolchains. The full build, tests and lint run in the fork's CI on the candidate SHA after this run's code review; the acceptance verdict for build/test criteria is `pending CI`, never passed.
- In-run checks you can and must run when relevant: `msgfmt -c --statistics -o /dev/null frontend/translations/<lang>.po`, plain `node`/`python3` scripts that read `.po`/source files, `git diff`, `grep`. Use the exact checks the task names.
- Before any check that writes files, create a private scratch dir with `mktemp -d /tmp/zs-agent-XXXXXX` and use it as TMPDIR. For revert/mutation checks copy the repo into it with `python3 -c 'import shutil,sys; shutil.copytree(".", sys.argv[1], ignore=shutil.ignore_patterns(".git","node_modules",".shadow-cljs","target",".pnpm-store"))' /tmp/zs-agent-.../repo`; never revert files in the shared worktree. `rm -rf` your scratch dir before you finish.
- Do not commit, push, or use the network.

Repository invariants (see AGENTS.md, DesignGate overrides first):
- Touch only the paths the task allows. A file outside them, including a new untracked file, is a scope failure.
- User-visible text says DesignGate, never Penpot (the `.penpot` file extension stays). Translations keep every placeholder of the `en.po` msgid (`%s`, `%d`, `{{...}}`) and Russian plural forms.
- Keep the diff small and in upstream style: it must merge with future Penpot releases.

Roles in this repo (on top of the graph's built-in instructions):
- Worker: implement exactly the task; smallest coherent change. Final summary maps each acceptance criterion to evidence (command result or file:line) and lists what was left to CI or the owner.
- Worker, every visit (first and repair): the first action is a shell command (`git status --porcelain`); a visit that ends without a file change or a check command is a failure, not a result.
- Code reviewer: correctness, invariants above, scope (allowed paths), that each new test would fail when its guarded behavior is removed. Reject only for substantive issues.
- Acceptance reviewer: verify each criterion with concrete evidence. List separately what is left to CI, preview or the owner; never mark those as passed and do not reject only because they are pending.
- Repair: fix only the reviewers' findings without weakening the requested behavior.
- Everyone: leave AGENTS.md, STATE.md and devtools/zeroshot alone unless the task says otherwise. If Zeroshot itself gets in the way, say so in your summary.
