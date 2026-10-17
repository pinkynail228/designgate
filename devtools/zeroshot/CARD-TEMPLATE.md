# Card template (critic/13, critic/15)

Write each fact once. Copy, fill, delete the hints.

```
<ID> <title>

Outcome: <one user-visible result, in the user's words>

Acceptance (AC-1, AC-2, ...): <each item names the action and the exact result to see>

Adjacent states: pick only the transitions this card touches and state the result for each,
or mark it out of scope (out of scope never lifts data-safety or permission guarantees).
  focus (canvas / text input), selection (none / one / many), renderer (SVG / WASM),
  undo/redo, panel or tab switch, reload / save + reopen, read-only / viewer,
  library / consumer file / version, related asset missing or deleted.

Invariants: <what must not change>

Entry points: <files or functions to start from; a pointer, not a prescription>

Impact check: affected modes -> implementation points -> allowed paths. Every point must be
inside the allowed paths, otherwise widen them or split the card.

Allowed paths: <list>

Verification: <one command or spec per AC: unit test, Playwright spec (AC-ID -> spec), or
"owner on preview: checklist #N">
```

Before launch: read Outcome against Allowed paths and each AC against the others; words like
"session", "current", "same", "refused" get an explicit boundary. For new contracts,
persistence, RPC, both renderers or disputed scope, run one sol medium pass on the card text.
After the run: read the worker's "Interpretations:"; a recurring one becomes a rule here.
