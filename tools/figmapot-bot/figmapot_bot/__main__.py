"""Entry point: ``python3 -m figmapot_bot``."""
from __future__ import annotations

import argparse
import logging
import sys

from .adapters import JiraClient, PenpotClient, TelegramClient
from .bot import Bot
from .clock import Clock
from .config import ConfigError, load_config
from .onboarding import OnboardingError, load_people, onboard
from .state import AlreadyRunning, State

LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"


def configure_logging():
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)


def build_bot(config, logger):
    clock = Clock()
    design = PenpotClient(config.penpot_url, config.bot_email, config.bot_password, clock, logger)
    tracker = JiraClient(
        config.jira_url,
        config.jira_email,
        config.jira_api_token,
        config.jira_project_key,
        config.jira_issue_type,
        clock,
        logger,
    )
    chat = TelegramClient(
        config.telegram_bot_token,
        config.telegram_chat_id,
        clock,
        logger,
        api_base=config.telegram_api_base,
    )
    state = State(config.state_db, clock)
    return Bot(config, clock, state, design, tracker, chat, logger)


def run_onboard(person_key, logger, people_loader=load_people):
    """``onboard <person_key>``: print the one-time deep link and exit.

    It takes the state lock like the bot does, so it runs while the bot is
    stopped; the person directory is read through ``people_loader`` (``None``
    means no check).
    """
    try:
        config = load_config()
    except ConfigError as error:
        print("figmapot-bot: %s" % error, file=sys.stderr)
        return 2

    clock = Clock()
    chat = TelegramClient(
        config.telegram_bot_token,
        config.telegram_chat_id,
        clock,
        logger,
        api_base=config.telegram_api_base,
    )
    try:
        state = State(config.state_db, clock)
    except AlreadyRunning as error:
        print("figmapot-bot: %s" % error, file=sys.stderr)
        return 3
    try:
        url = onboard(state, chat, person_key, clock.now(), people_loader=people_loader)
    except OnboardingError as error:
        print("figmapot-bot: %s" % error, file=sys.stderr)
        return 4
    finally:
        state.close()
    print(url)
    logger.info("onboarding: link ready for person %s", person_key)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="figmapot_bot", description="FigmaPot comment -> Jira bridge")
    parser.add_argument("--once", action="store_true", help="run a single poll cycle and exit")
    subparsers = parser.add_subparsers(dest="command")
    onboard_parser = subparsers.add_parser(
        "onboard", help="print a one-time private chat link for a person"
    )
    onboard_parser.add_argument("person_key")
    args = parser.parse_args(argv)

    configure_logging()
    logger = logging.getLogger("figmapot_bot")

    if args.command == "onboard":
        return run_onboard(args.person_key, logger)

    try:
        config = load_config()
    except ConfigError as error:
        print("figmapot-bot: %s" % error, file=sys.stderr)
        return 2

    try:
        bot = build_bot(config, logger)
    except AlreadyRunning as error:
        print("figmapot-bot: %s" % error, file=sys.stderr)
        return 3

    logger.info("figmapot-bot starting (state db: %s)", config.state_db)
    bot.run(once=args.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
