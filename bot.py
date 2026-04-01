#!/usr/bin/env python3
"""
Telegram Balance Bot.
- Works ONLY from configured groups (ignores private messages and unknown groups)
- Each group linked to a merchant with its own API credentials
- Command: @botname баланс (or /balance)
- Hourly notifications with balance to each group
- Config-driven: add new merchants in config.json, restart bot
"""

import json
import logging
import asyncio
import httpx
from pathlib import Path
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_merchant_by_group(config, group_id):
    for m in config["merchants"]:
        if m["group_id"] == group_id:
            return m
    return None


async def fetch_balance(api_url, bearer_token, currency):
    """Fetch balance from API."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                api_url,
                params={"currency": currency},
                headers={"Authorization": f"Bearer {bearer_token}"},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        log.error(f"API error: {e}")
        return {"error": str(e)}


def format_balance(name, data, currency):
    """Format balance response for Telegram."""
    if "error" in data:
        return f"❌ *{name}* ({currency})\nОшибка: `{data['error']}`"

    # Adapt to actual API response structure
    if "balance" in data:
        bal = data["balance"]
    elif "result" in data and isinstance(data["result"], dict):
        bal = data["result"]
    else:
        bal = data

    lines = [f"💰 *{name}* — {currency}"]

    if isinstance(bal, dict):
        for key, val in bal.items():
            if key in ("currency",):
                continue
            lines.append(f"  {key}: `{val}`")
    else:
        lines.append(f"  Balance: `{bal}`")

    return "\n".join(lines)


async def get_all_balances(config, merchant):
    """Fetch balances for all currencies of a merchant."""
    results = []
    for cur in merchant["currencies"]:
        data = await fetch_balance(
            config["api_url"], merchant["bearer_token"], cur
        )
        results.append(format_balance(merchant["name"], data, cur))
    return "\n\n".join(results)


def is_group_message(update: Update) -> bool:
    """Check if message is from a group chat."""
    return update.effective_chat and update.effective_chat.type in (
        "group",
        "supergroup",
    )


async def handle_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /balance command or '@bot баланс' mention."""
    if not is_group_message(update):
        return  # Ignore private messages

    config = load_config()
    group_id = update.effective_chat.id
    merchant = get_merchant_by_group(config, group_id)

    if not merchant:
        log.warning(f"Unknown group: {group_id} ({update.effective_chat.title})")
        return  # Ignore unknown groups silently

    log.info(
        f"Balance request from group '{update.effective_chat.title}' "
        f"({group_id}) for merchant '{merchant['name']}'"
    )

    msg = await update.message.reply_text("⏳ Загрузка баланса...")
    text = await get_all_balances(config, merchant)
    await msg.edit_text(text, parse_mode="Markdown")


async def handle_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle '@bot баланс' text mention in groups."""
    if not is_group_message(update):
        return

    if not update.message or not update.message.text:
        return

    text = update.message.text.lower()
    bot_username = (await context.bot.get_me()).username.lower()

    # Check if bot is mentioned and "баланс" or "balance" is in the message
    if f"@{bot_username}" in text and (
        "баланс" in text or "balance" in text
    ):
        await handle_balance(update, context)


async def send_scheduled_balances(context: ContextTypes.DEFAULT_TYPE):
    """Send hourly balance notifications to all configured groups."""
    config = load_config()
    bot: Bot = context.bot

    for merchant in config["merchants"]:
        try:
            text = await get_all_balances(config, merchant)
            header = f"📊 *Автоотчёт — {merchant['name']}*\n\n"
            await bot.send_message(
                chat_id=merchant["group_id"],
                text=header + text,
                parse_mode="Markdown",
            )
            log.info(f"Scheduled balance sent to '{merchant['name']}' ({merchant['group_id']})")
        except Exception as e:
            log.error(f"Failed to send to {merchant['name']}: {e}")


async def handle_any_private(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Silently ignore private messages."""
    return


def main():
    config = load_config()

    log.info(f"Starting bot with {len(config['merchants'])} merchant(s):")
    for m in config["merchants"]:
        log.info(f"  - {m['name']} (group: {m['group_id']}, currencies: {m['currencies']})")

    app = Application.builder().token(config["bot_token"]).build()

    # /balance command — only works in groups
    app.add_handler(CommandHandler("balance", handle_balance))
    app.add_handler(CommandHandler("bal", handle_balance))

    # @bot баланс — mention handler
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.GROUPS,
            handle_mention,
        )
    )

    # Private messages — tell user to use groups
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.PRIVATE,
            handle_any_private,
        )
    )

    # Scheduled hourly notifications
    interval = config.get("notify_interval_minutes", 60) * 60
    app.job_queue.run_repeating(
        send_scheduled_balances,
        interval=interval,
        first=10,  # first run 10 sec after start
    )

    log.info("Bot started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
