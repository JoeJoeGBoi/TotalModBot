# moderator_bot.py
import json
import logging
import os
from typing import List

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, filters

# ---------- CONFIG ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "<PUT_YOUR_TOKEN_HERE>")
# Replace with your Telegram user ID(s) who may perform global bans:
ADMINS = {123456789}  # set of ints
DATA_FILE = "mod_data.json"
# ----------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# persistent storage helpers
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"managed_chats": [], "global_bans": []}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

data = load_data()

async def is_admin_user(user_id: int) -> bool:
    return user_id in ADMINS

# register current chat as managed (only works in groups/channels)
async def register(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if not await is_admin_user(user.id):
        await ctx.bot.send_message(chat.id, "Only configured bot admins can register chats.")
        return

    cid = chat.id
    if cid in data["managed_chats"]:
        await ctx.bot.send_message(chat.id, "This chat is already managed.")
        return

    data["managed_chats"].append(cid)
    save_data(data)
    await ctx.bot.send_message(chat.id, f"Registered this chat (id={cid}) as managed.")

# unregister current chat
async def unregister(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if not await is_admin_user(user.id):
        await ctx.bot.send_message(chat.id, "Only configured bot admins can unregister chats.")
        return

    cid = chat.id
    if cid not in data["managed_chats"]:
        await ctx.bot.send_message(chat.id, "This chat is not managed.")
        return

    data["managed_chats"].remove(cid)
    save_data(data)
    await ctx.bot.send_message(chat.id, f"Unregistered this chat (id={cid}).")

# list managed chats
async def list_managed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not await is_admin_user(user.id):
        await ctx.bot.send_message(update.effective_chat.id, "Only configured bot admins can view this list.")
        return
    if not data["managed_chats"]:
        await ctx.bot.send_message(update.effective_chat.id, "No managed chats.")
        return
    lines = [f"{i+1}. id={cid}" for i, cid in enumerate(data["managed_chats"])]
    await ctx.bot.send_message(update.effective_chat.id, "Managed chats:\n" + "\n".join(lines))

# helper to resolve a username or user_id from args
def parse_target_arg(arg: str):
    # accept @username or numeric id
    if arg.startswith("@"):
        return arg  # username string
    try:
        return int(arg)
    except ValueError:
        return None

# global ban command: /globalban <user_id|@username> <reason (optional)>
async def globalban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    issuer = update.effective_user
    if not await is_admin_user(issuer.id):
        await ctx.bot.send_message(update.effective_chat.id, "You are not authorized to run that command.")
        return

    if not ctx.args:
        await ctx.bot.send_message(update.effective_chat.id, "Usage: /globalban <user_id or @username> [reason]")
        return

    target_arg = ctx.args[0]
    reason = " ".join(ctx.args[1:]) if len(ctx.args) > 1 else "No reason provided"
    target = parse_target_arg(target_arg)
    if target is None:
        await ctx.bot.send_message(update.effective_chat.id, "Could not parse user id. Provide numeric id or @username.")
        return

    # Add to persistent global blacklist (store raw arg)
    if target_arg in data["global_bans"]:
        await ctx.bot.send_message(update.effective_chat.id, f"{target_arg} is already globally banned.")
        return

    data["global_bans"].append(target_arg)
    save_data(data)

    # Attempt to ban from each managed chat where bot has permission
    results = []
    for cid in list(data["managed_chats"]):
        try:
            # First try to resolve username to id if target is username
            if isinstance(target, str) and target.startswith("@"):
                member = await ctx.bot.get_chat_member(cid, target)  # may raise
                uid = member.user.id
            else:
                uid = target
            # Ensure bot is admin in the chat by checking its status
            bot_member = await ctx.bot.get_chat_member(cid, (await ctx.bot.get_me()).id)
            if not (bot_member.status in ("administrator", "creator") and bot_member.can_restrict_members):
                results.append(f"Chat {cid}: bot lacks ban permission — skipped.")
                continue

            await ctx.bot.ban_chat_member(cid, uid)
            results.append(f"Chat {cid}: banned user {target_arg}.")
        except Exception as e:
            logger.exception("Ban failed for chat %s", cid)
            results.append(f"Chat {cid}: failed to ban ({e}).")

    await ctx.bot.send_message(update.effective_chat.id,
                               f"Global ban applied for {target_arg}.\nReason: {reason}\n\nResults:\n" + "\n".join(results))

# global unban
async def globalunban(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    issuer = update.effective_user
    if not await is_admin_user(issuer.id):
        await ctx.bot.send_message(update.effective_chat.id, "You are not authorized to run that command.")
        return

    if not ctx.args:
        await ctx.bot.send_message(update.effective_chat.id, "Usage: /globalunban <user_id or @username>")
        return

    target_arg = ctx.args[0]
    if target_arg not in data["global_bans"]:
        await ctx.bot.send_message(update.effective_chat.id, f"{target_arg} is not in the global ban list.")
        return

    data["global_bans"].remove(target_arg)
    save_data(data)

    results = []
    for cid in list(data["managed_chats"]):
        try:
            if target_arg.startswith("@"):
                member = await ctx.bot.get_chat_member(cid, target_arg)
                uid = member.user.id
            else:
                uid = int(target_arg)
            await ctx.bot.unban_chat_member(cid, uid)
            results.append(f"{cid}: unbanned.")
        except Exception as e:
            results.append(f"{cid}: failed to unban ({e}).")

    await ctx.bot.send_message(update.effective_chat.id, f"Removed {target_arg} from global bans.\nResults:\n" + "\n".join(results))

# simple start
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await ctx.bot.send_message(update.effective_chat.id,
                               "Moderation bot online. Admin commands: /register /unregister /globalban /globalunban /list_managed.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("register", register))
    app.add_handler(CommandHandler("unregister", unregister))
    app.add_handler(CommandHandler("list_managed", list_managed))
    app.add_handler(CommandHandler("globalban", globalban))
    app.add_handler(CommandHandler("globalunban", globalunban))

    print("Bot starting...")
    app.run_polling()

if __name__ == "__main__":
    main()
