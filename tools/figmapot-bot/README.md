# figmapot-bot

A small, stdlib-only bridge: the owner writes `@FigmaPot Bot task <title>` in a
comment on an allowlisted Penpot file, and about a minute later a Jira issue
exists, the bot replies in the thread, and a Telegram message arrives.

Python 3.9+, no third-party packages, one foreground process with SQLite.

## Setup

1. Create the config file (default path shown; override with `$FIGMAPOT_BOT_ENV`):

   ```sh
   mkdir -p ~/.config/figmapot-bot
   cp env.example ~/.config/figmapot-bot/env
   chmod 600 ~/.config/figmapot-bot/env
   ```

2. Fill in every key in that file. Required: `PENPOT_URL`, `PENPOT_TEAM_ID`,
   `PENPOT_BOT_EMAIL`, `PENPOT_BOT_PASSWORD`, `PENPOT_OWNER_ID`,
   `PENPOT_FILE_IDS`, `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`,
   `JIRA_PROJECT_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
   Optional: `JIRA_ISSUE_TYPE` (default `Task`), `STATE_DB`
   (default `~/.local/state/figmapot-bot/state.db`), `POLL_SECONDS` (default 30).

## Run and stop

```sh
cd tools/figmapot-bot
python3 -m figmapot_bot            # foreground; Ctrl-C stops it
python3 -m figmapot_bot --once     # a single poll cycle, then exit
```

Only one instance may run: a second one exits with a clear message. A missing
config key makes the bot exit naming that key.

## Finding the ids

- **Owner id** (`PENPOT_OWNER_ID`) and the bot's own id: open Penpot, then
  `get-profile` in the browser devtools' network tab, or copy the uuid in the
  owner's avatar menu URL.
- **Team id** (`PENPOT_TEAM_ID`): open the team's dashboard; the uuid is in the
  URL, `/dashboard/team/<team-id>`.
- **File ids** (`PENPOT_FILE_IDS`, comma-separated allowlist): open a file; the
  uuid is in the workspace URL, `#/workspace?team-id=...&file-id=<file-id>`.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

Tests start loopback fake HTTP servers for Penpot, Jira and Telegram, and use a
fake clock and a temp `STATE_DB`; they make no external network calls.
