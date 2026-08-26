#!/usr/bin/env python3
"""
Live smoke test for your Telegram bot setup. Run this yourself once
you've created a bot and gotten your chat ID (see the steps below) -
this sandbox has no network access to run it here.

    export TELEGRAM_BOT_TOKEN=your-bot-token
    export TELEGRAM_CHAT_ID=your-chat-id
    python scripts/verify_telegram.py

--- One-time setup, if you haven't done this yet ---

1. Open Telegram, message @BotFather, send /newbot, follow the prompts.
   BotFather gives you a token like "123456789:AAH...". That's TELEGRAM_BOT_TOKEN.

2. Send any message to your new bot (search for its username and say hi).

3. Get your chat ID: visit this URL in a browser, with YOUR_TOKEN swapped in:
     https://api.telegram.org/botYOUR_TOKEN/getUpdates
   Find "chat":{"id": ...} in the response - that number is TELEGRAM_CHAT_ID.

4. Set both as GitHub repo secrets (Settings > Secrets and variables >
   Actions) with exactly those two names, so the scheduled workflow can
   use them. Never commit them to any file.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from notifications.notifier import TelegramNotifier


def main():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("ERROR: set both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first.")
        print("See the setup steps in this script's docstring if you haven't created a bot yet.")
        sys.exit(1)

    notifier = TelegramNotifier(bot_token=bot_token, chat_id=chat_id)

    print("Sending a test message...")
    ok = notifier.send_text(
        "✅ Demand Zone Scanner test message.\n\n"
        "If you're reading this in Telegram, your bot token and chat ID are both correct."
    )

    if ok:
        print("SUCCESS - check Telegram, you should have a message.")
    else:
        print("FAILED - check the log output above. Common causes:")
        print("  - bot token copied incorrectly")
        print("  - chat_id wrong, or you haven't messaged the bot first")
        print("  - chat_id needs to be a string of just digits (or -digits for a group chat)")
        sys.exit(1)


if __name__ == "__main__":
    main()
