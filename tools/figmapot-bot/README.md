# figmapot-bot

A small, stdlib-only bridge: the owner writes `@FigmaPot Bot task <title>` in a
comment on an allowlisted Penpot file, and about a minute later a Jira issue
exists, the bot replies in the thread, and a Telegram message arrives. A
command may name an assignee and a due date, and the assignee gets a private
message once they have linked their chat with `/start <code>`.

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
   (default `~/.local/state/figmapot-bot/state.db`), `POLL_SECONDS` (default 30),
   `PEOPLE_FILE` (the person directory below; unset = no assignees) and
   `BOT_TIMEZONE` (default `Europe/Moscow`, used to read `today`).

## Assignees and due dates

A command may add the optionals `@<person>` and `due <YYYY-MM-DD>` (in either
order, only at the end):

```
@FigmaPot Bot task Fix header @ivan due 2026-10-25
```

`<person>` is a key in the person directory, or `penpot:<profile-id>`. The
issue is created assigned to that Jira account with that due date, the thread
reply and the group notice name the assignee and the due date, and a private
Telegram message goes to the person's bound chat. A command that names an
unknown person, or whose date is malformed or in the past, gets a one-line
reply and creates no issue.

`PEOPLE_FILE` is a JSON file:

```json
{
  "people": [
    {"key": "ivan", "name": "Ivan Petrov", "jira_account_id": "<jira-account-id>",
     "penpot_profile_id": "<optional-penpot-uuid>", "owner": true}
  ]
}
```

`key` (case-insensitive) and `jira_account_id` are required and unique;
`penpot_profile_id` is optional and unique; at most one entry may set
`owner`. An unreadable or malformed file stops the start with a clear message.

Before the bot starts polling it asks Jira which people are assignable in the
project; anyone Jira rejects is logged by key and treated as unknown until the
next start.

## Onboarding a person

A person links their private chat with a one-time link printed by:

```sh
python3 -m figmapot_bot onboard ivan
```

It issues a single-use code (valid for a week) and prints a `t.me` deep link to
send to that person; opening it sends `/start <code>` to the bot. The bot polls
Telegram each cycle and binds (or rebinds) the chat; the link is checked against
`PEOPLE_FILE`, and the bot confirms the link in the private chat. Only a chat
that has written first can be messaged.

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

Tests drive Penpot, Jira and Telegram through in-process fakes, and use a fake
clock and a temp `STATE_DB`; they open no socket and make no external calls.
