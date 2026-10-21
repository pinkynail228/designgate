"""Configuration loading for figmapot-bot.

The bot reads a simple ``KEY=VALUE`` env file. The path comes from
``$FIGMAPOT_BOT_ENV`` and defaults to ``~/.config/figmapot-bot/env``.
Secrets are never echoed: errors name the missing key but never its value,
and the config ``repr`` masks every secret field.
"""
from __future__ import annotations

import datetime
import os
import zoneinfo

DEFAULT_ENV_PATH = "~/.config/figmapot-bot/env"
DEFAULT_STATE_DB = "~/.local/state/figmapot-bot/state.db"
DEFAULT_TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_TIMEZONE = "Europe/Moscow"

REQUIRED_KEYS = (
    "PENPOT_URL",
    "PENPOT_TEAM_ID",
    "PENPOT_BOT_EMAIL",
    "PENPOT_BOT_PASSWORD",
    "PENPOT_OWNER_ID",
    "PENPOT_FILE_IDS",
    "JIRA_URL",
    "JIRA_EMAIL",
    "JIRA_API_TOKEN",
    "JIRA_PROJECT_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)

# Optional keys with a default value. ``TELEGRAM_API_BASE`` is only useful for
# self-hosted or test setups; production always uses the public Telegram API.
DEFAULTS = {
    "JIRA_ISSUE_TYPE": "Task",
    "STATE_DB": DEFAULT_STATE_DB,
    "POLL_SECONDS": "30",
    "TELEGRAM_API_BASE": DEFAULT_TELEGRAM_API_BASE,
    # Optional person directory; unset means an empty directory (no assignees).
    "PEOPLE_FILE": "",
    "BOT_TIMEZONE": DEFAULT_TIMEZONE,
}

# Keys whose value must never reach logs, error messages or exceptions.
SECRET_KEYS = ("PENPOT_BOT_PASSWORD", "JIRA_API_TOKEN", "TELEGRAM_BOT_TOKEN")

# Secret keys that are embedded in URLs or transport headers, where whitespace
# is invalid. ``PENPOT_BOT_PASSWORD`` is excluded on purpose: Penpot accepts
# passphrases containing spaces (common/src/app/common/schema.cljc).
TOKEN_KEYS = ("JIRA_API_TOKEN", "TELEGRAM_BOT_TOKEN")


class ConfigError(Exception):
    """Raised when the environment configuration is unusable."""


def parse_env_file(path):
    """Parse a ``KEY=VALUE`` env file. Missing files yield an empty mapping."""
    values = {}
    if not path or not os.path.exists(path):
        return values
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            values[key] = value
    return values


class Config:
    """Resolved, validated configuration."""

    def __init__(self, values, env_path=None):
        self.env_path = env_path
        self.penpot_url = values["PENPOT_URL"].rstrip("/")
        self.team_id = values["PENPOT_TEAM_ID"]
        self.bot_email = values["PENPOT_BOT_EMAIL"]
        self.bot_password = values["PENPOT_BOT_PASSWORD"]
        self.owner_id = values["PENPOT_OWNER_ID"]
        self.file_ids = [item.strip() for item in values["PENPOT_FILE_IDS"].split(",") if item.strip()]
        self.jira_url = values["JIRA_URL"].rstrip("/")
        self.jira_email = values["JIRA_EMAIL"]
        self.jira_api_token = values["JIRA_API_TOKEN"]
        self.jira_project_key = values["JIRA_PROJECT_KEY"]
        self.jira_issue_type = values["JIRA_ISSUE_TYPE"]
        self.telegram_bot_token = values["TELEGRAM_BOT_TOKEN"]
        self.telegram_chat_id = values["TELEGRAM_CHAT_ID"]
        self.telegram_api_base = values["TELEGRAM_API_BASE"] or DEFAULT_TELEGRAM_API_BASE
        self.state_db = os.path.expanduser(values["STATE_DB"])
        self.poll_seconds = float(values["POLL_SECONDS"])
        self.people_file = os.path.expanduser(values["PEOPLE_FILE"]) or None
        self.bot_timezone = values["BOT_TIMEZONE"] or DEFAULT_TIMEZONE

    def __repr__(self):
        parts = []
        for key, value in sorted(self.__dict__.items()):
            if key.upper() in SECRET_KEYS or "password" in key or "token" in key:
                value = "***"
            parts.append("%s=%r" % (key, value))
        return "Config(%s)" % ", ".join(parts)


def load_config(environ=None, env_path=None):
    """Load configuration from the env file and the process environment.

    Real environment variables win over the file (handy for one-off runs),
    but the file is the source of truth for the deployment.
    """
    environ = os.environ if environ is None else environ
    path = env_path or environ.get("FIGMAPOT_BOT_ENV") or os.path.expanduser(DEFAULT_ENV_PATH)
    values = dict(DEFAULTS)
    for key, value in parse_env_file(path).items():
        if value != "":
            values[key] = value
    for key in list(REQUIRED_KEYS) + list(DEFAULTS):
        if environ.get(key):
            values[key] = environ[key]
    missing = [key for key in REQUIRED_KEYS if not values.get(key)]
    if missing:
        raise ConfigError("missing required config key(s): " + ", ".join(missing))
    # Validate without ever echoing the offending value: a bad value could be a
    # secret, and error messages end up in logs.
    for key in ("PENPOT_URL", "JIRA_URL"):
        value = values[key]
        if not value.startswith(("http://", "https://")) or any(ch.isspace() for ch in value):
            raise ConfigError("%s must be an absolute http(s) URL without whitespace" % key)
    for key in TOKEN_KEYS:
        if any(ch.isspace() for ch in values[key]):
            raise ConfigError("%s must not contain whitespace" % key)
    try:
        float(values["POLL_SECONDS"])
    except ValueError:
        raise ConfigError("POLL_SECONDS must be a number")
    try:
        zoneinfo.ZoneInfo(values["BOT_TIMEZONE"])
    except Exception:
        # The name is validated here so a typo stops the start instead of
        # crashing on the first command.
        raise ConfigError("BOT_TIMEZONE must be a known IANA timezone name")
    return Config(values, env_path=path)


def local_date(timezone_name, epoch_seconds):
    """The calendar date (ISO ``YYYY-MM-DD``) at ``epoch_seconds`` in a zone."""
    moment = datetime.datetime.fromtimestamp(epoch_seconds, zoneinfo.ZoneInfo(timezone_name))
    return moment.date().isoformat()


def required_key_names():
    """Names listed in ``env.example``."""
    return list(REQUIRED_KEYS) + ["JIRA_ISSUE_TYPE", "STATE_DB", "POLL_SECONDS"]
