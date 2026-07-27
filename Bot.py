#!/usr/bin/env python3
"""
Advanced Telegram "Earning" Bot with Ads (simulation).
- Python 3.9+
- Dependencies: python-telegram-bot==20.4
- Usage:
    export BOT_TOKEN="123:ABC..."
    export OWNER_ID="123456789"
    python advanced_bot.py
- Notes:
    - This bot simulates earnings and tracks withdraw requests. It does NOT perform real payments.
    - Ads are configurable by admin. Ads are shown on /earn (or via /showad) and clicks are tracked.
"""

import os
import logging
import random
import sqlite3
import asyncio
from datetime import datetime, date, timedelta
from typing import Optional, Tuple, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    CallbackQueryHandler, MessageHandler, filters
)

# --- Config ---
TOKEN = os.getenv("8529379473:AAGz60EDs8xVzdcUEpumCMpUsFxmmJgdjQo")
OWNER_ID = int(os.getenv("OWNER_ID") or 0)
DB_PATH = os.getenv("EARN_BOT_DB", "earn_bot_advanced.sqlite")

# Earning rules
START_BONUS = 20
REFERRAL_BONUS = 75
DAILY_EARN_MAX = 6
DAILY_EARN_MIN_AMOUNT = 5
DAILY_EARN_MAX_AMOUNT = 25

# Withdraw rules
MIN_WITHDRAW = 100

# Ads behavior
DEFAULT_AD_REWARD_ON_CLICK = 5     # optional reward when user clicks an ad (if ad configured)
MAX_ADS_PER_EARN = 1               # show at most this many ads per /earn

# Bot settings
BROADCAST_BATCH_SIZE = 50

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- DB helpers (sync wrapped in asyncio.to_thread) ---
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    balance INTEGER DEFAULT 0,
    created_at TEXT,
    referrer INTEGER,
    last_earn_date TEXT,
    daily_earn_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS withdraws (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    requested_at TEXT,
    status TEXT DEFAULT 'pending',
    note TEXT
);
CREATE TABLE IF NOT EXISTS ads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    image_url TEXT,
    target_url TEXT,
    active INTEGER DEFAULT 1,
    reward_on_click INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS ad_clicks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ad_id INTEGER,
    user_id INTEGER,
    clicked_at TEXT
);
"""

def init_db_sync():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    cur = conn.cursor()
    cur.executescript(SCHEMA)
    conn.commit()
    conn.close()

async def init_db():
    await asyncio.to_thread(init_db_sync)

def get_conn():
    return sqlite3.connect(DB_PATH, timeout=30)

# user functions
async def ensure_user(user_id: int, username: str, referrer: Optional[int] = None) -> bool:
    def _ensure():
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        r = cur.fetchone()
        if not r:
            now = datetime.utcnow().isoformat()
            cur.execute(
                "INSERT INTO users (user_id, username, balance, created_at, referrer) VALUES (?, ?, ?, ?, ?)",
                (user_id, username, START_BONUS, now, referrer)
            )
            conn.commit()
            conn.close()
            return True
        conn.close()
        return False
    return await asyncio.to_thread(_ensure)

async def add_balance(user_id: int, amount: int):
    def _add():
        conn = get_conn(); cur = conn.cursor()
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        conn.commit(); conn.close()
    await asyncio.to_thread(_add)

async def get_balance(user_id: int) -> int:
    def _get():
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        r = cur.fetchone(); conn.close()
        return r[0] if r else 0
    return await asyncio.to_thread(_get)

async def get_user_info(user_id: int):
    def _get():
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT user_id, username, balance, created_at, referrer, last_earn_date, daily_earn_count FROM users WHERE user_id = ?", (user_id,))
        r = cur.fetchone(); conn.close()
        return r
    return await asyncio.to_thread(_get)

async def reset_daily_if_needed(user_id: int):
    def _reset():
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT last_earn_date FROM users WHERE user_id = ?", (user_id,))
        r = cur.fetchone()
        today = date.today().isoformat()
        if r:
            last_date = r[0]
            if last_date != today:
                cur.execute("UPDATE users SET last_earn_date = ?, daily_earn_count = 0 WHERE user_id = ?", (today, user_id))
                conn.commit()
        conn.close()
    await asyncio.to_thread(_reset)

async def increment_daily_count(user_id: int):
    def _inc():
        conn = get_conn(); cur = conn.cursor()
        today = date.today().isoformat()
        cur.execute("UPDATE users SET daily_earn_count = daily_earn_count + 1, last_earn_date = ? WHERE user_id = ?",
                    (today, user_id))
        conn.commit(); conn.close()
    await asyncio.to_thread(_inc)

# withdraw functions
async def record_withdraw_request(user_id: int, amount: int, note: str = ""):
    def _rec():
        conn = get_conn(); cur = conn.cursor()
        now = datetime.utcnow().isoformat()
        cur.execute("INSERT INTO withdraws (user_id, amount, requested_at, status, note) VALUES (?, ?, ?, 'pending', ?)",
                    (user_id, amount, now, note))
        conn.commit(); conn.close()
    await asyncio.to_thread(_rec)

async def list_pending_withdraws() -> List[Tuple]:
    def _list():
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT id, user_id, amount, requested_at, note FROM withdraws WHERE status = 'pending' ORDER BY requested_at DESC")
        rows = cur.fetchall(); conn.close(); return rows
    return await asyncio.to_thread(_list)

async def update_withdraw_status(wid: int, new_status: str):
    def _upd():
        conn = get_conn(); cur = conn.cursor()
        cur.execute("UPDATE withdraws SET status = ? WHERE id = ?", (new_status, wid))
        conn.commit(); conn.close()
    await asyncio.to_thread(_upd)

# ads functions
async def add_ad(title: str, image_url: str, target_url: str, reward_on_click: int = 0):
    def _add():
        conn = get_conn(); cur = conn.cursor()
        now = datetime.utcnow().isoformat()
        cur.execute("INSERT INTO ads (title, image_url, target_url, active, reward_on_click, created_at) VALUES (?, ?, ?, 1, ?, ?)",
                    (title, image_url, target_url, reward_on_click, now))
        conn.commit(); conn.close()
    await asyncio.to_thread(_add)

async def list_ads(active_only: bool = False) -> List[Tuple]:
    def _list():
        conn = get_conn(); cur = conn.cursor()
        if active_only:
            cur.execute("SELECT id, title, image_url, target_url, reward_on_click, created_at FROM ads WHERE active = 1 ORDER BY created_at DESC")
        else:
            cur.execute("SELECT id, title, image_url, target_url, active, reward_on_click, created_at FROM ads ORDER BY created_at DESC")
        rows = cur.fetchall(); conn.close(); return rows
    return await asyncio.to_thread(_list)

async def toggle_ad(ad_id: int, make_active: Optional[bool] = None):
    def _toggle():
        conn = get_conn(); cur = conn.cursor()
        if make_active is None:
            # flip
            cur.execute("UPDATE ads SET active = 1 - active WHERE id = ?", (ad_id,))
        else:
            cur.execute("UPDATE ads SET active = ? WHERE id = ?", (1 if make_active else 0, ad_id))
        conn.commit(); conn.close()
    await asyncio.to_thread(_toggle)

async def get_random_active_ads(limit: int = 1) -> List[Tuple]:
    def _get():
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT id, title, image_url, target_url, reward_on_click FROM ads WHERE active = 1 ORDER BY RANDOM() LIMIT ?", (limit,))
        rows = cur.fetchall(); conn.close(); return rows
    return await asyncio.to_thread(_get)

async def record_ad_click(ad_id: int, user_id: int):
    def _rec():
        conn = get_conn(); cur = conn.cursor()
        now = datetime.utcnow().isoformat()
        cur.execute("INSERT INTO ad_clicks (ad_id, user_id, clicked_at) VALUES (?, ?, ?)", (ad_id, user_id, now))
        conn.commit(); conn.close()
    await asyncio.to_thread(_rec)

# leaderboard
async def top_leaderboard(limit: int = 10) -> List[Tuple]:
    def _top():
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT user_id, username, balance FROM users ORDER BY balance DESC LIMIT ?", (limit,))
        rows = cur.fetchall(); conn.close(); return rows
    return await asyncio.to_thread(_top)

# admin broadcast helper
async def all_user_ids() -> List[int]:
    def _all():
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT user_id FROM users")
        rows = [r[0] for r in cur.fetchall()]; conn.close(); return rows
    return await asyncio.to_thread(_all)

# --- Utility ---
def is_owner(user_id: int) -> bool:
    return OWNER_ID and user_id == OWNER_ID

# --- Bot handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    args = context.args or []
    ref_code = None
    if args:
        try:
            ref_code = int(args[0])
        except Exception:
            ref_code = None

    created = await ensure_user(user.id, user.username or user.full_name, referrer=ref_code)
    msg = []
    if created:
        msg.append(f"Welcome {user.first_name}! You received a start bonus of {START_BONUS} units.")
        if ref_code and ref_code != user.id:
            ref_info = await get_user_info(ref_code)
            if ref_info:
                await add_balance(ref_code, REFERRAL_BONUS)
                await add_balance(user.id, REFERRAL_BONUS)
                msg.append(f"Referral used — both you and the referrer received {REFERRAL_BONUS} units.")
    else:
        msg.append(f"Welcome back, {user.first_name}!")

    msg.append("\nCommands:\n/balance\n/earn\n/withdraw <amount> [note]\n/leaderboard\n/help")
    msg.append("Share your referral link: https://t.me/{bot}?start={id}".format(bot=(context.bot.username or "thisbot"), id=user.id))
    await update.message.reply_text("\n".join(msg))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start [referrer_id]\n/balance\n/earn\n/withdraw <amount> [note]\n/leaderboard\n/showad\n\nAdmin: /ads_add <title>|<image_url>|<target_url>|<reward>\n/ads_list\n/ads_toggle <id>\n/stats\n/withdraws\n/withdraw_approve <id>\n/broadcast <message>"
    )

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    bal = await get_balance(user.id)
    await update.message.reply_text(f"Your balance: {bal} units.")

async def earn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    await ensure_user(user.id, user.username or user.full_name)
    await reset_daily_if_needed(user.id)
    info = await get_user_info(user.id)
    if not info:
        await update.message.reply_text("User not found.")
        return
    daily_count = info[6] or 0
    if daily_count >= DAILY_EARN_MAX:
        await update.message.reply_text("You reached daily earn limit. Come back tomorrow.")
        return
    amount = random.randint(DAILY_EARN_MIN_AMOUNT, DAILY_EARN_MAX_AMOUNT)
    await add_balance(user.id, amount)
    await increment_daily_count(user.id)
    # after earning, show up to MAX_ADS_PER_EARN random ads
    await update.message.reply_text(f"Task complete! You earned {amount} units. Use /balance to see total.")
    ads = await get_random_active_ads(limit=MAX_ADS_PER_EARN)
    for ad in ads:
        await send_ad_to_user(context, user.id, ad)

async def send_ad_to_user(context: ContextTypes.DEFAULT_TYPE, chat_id: int, ad_row: Tuple):
    # ad_row: id, title, image_url, target_url, reward_on_click
    ad_id, title, image_url, target_url, reward = ad_row
    buttons = [
        [InlineKeyboardButton(text="Open Sponsor", callback_data=f"adopen:{ad_id}")],
        [InlineKeyboardButton(text="No thanks", callback_data="adskip")]
    ]
    text = f"Sponsored: {title}"
    try:
        if image_url:
            await context.bot.send_photo(chat_id=chat_id, photo=image_url, caption=text, reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        logger.warning("Failed to send ad to %s: %s", chat_id, e)
        # fallback to text-only
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"{text}\n{target_url}", reply_markup=InlineKeyboardMarkup(buttons))
        except Exception:
            pass

async def ad_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()  # acknowledge
    data = query.data or ""
    user = query.from_user
    if data.startswith("adopen:"):
        try:
            ad_id = int(data.split(":", 1)[1])
        except Exception:
            await query.edit_message_caption("Invalid ad.")
            return
        # record click
        await record_ad_click(ad_id, user.id)
        # reward user if configured
        rows = await asyncio.to_thread(lambda: __fetch_ad_by_id(ad_id))
        if rows:
            _, title, image_url, target_url, active, reward_on_click, created_at = rows
            if reward_on_click and reward_on_click > 0:
                await add_balance(user.id, reward_on_click)
                await query.message.reply_text(f"Thanks for checking the sponsor — you got {reward_on_click} units!")
            # send the real URL as message (can't open automatically from callback)
            await query.message.reply_text(f"Open sponsor link: {target_url}")
        else:
            await query.message.reply_text("Ad not found.")
    elif data == "adskip":
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

def __fetch_ad_by_id(ad_id: int):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, title, image_url, target_url, active, reward_on_click, created_at FROM ads WHERE id = ?", (ad_id,))
    row = cur.fetchone(); conn.close(); return row

async def show_ad_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    ads = await get_random_active_ads(limit=1)
    if not ads:
        await update.message.reply_text("No active ads available.")
        return
    await send_ad_to_user(context, user.id, ads[0])

async def withdraw_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /withdraw <amount> [note]")
        return
    try:
        amount = int(args[0])
    except Exception:
        await update.message.reply_text("Please enter a valid integer amount.")
        return
    note = " ".join(args[1:]) if len(args) > 1 else ""
    bal = await get_balance(user.id)
    if amount <= 0:
        await update.message.reply_text("Enter amount greater than zero.")
        return
    if amount > bal:
        await update.message.reply_text("Insufficient balance.")
        return
    if amount < MIN_WITHDRAW:
        await update.message.reply_text(f"Minimum withdraw is {MIN_WITHDRAW}.")
        return
    await add_balance(user.id, -amount)
    await record_withdraw_request(user.id, amount, note)
    await update.message.reply_text("Your withdraw request is recorded. Admin will process it manually.")

# Admin handlers
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id):
        return
    # simple stats
    def _stats():
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT COUNT(*), SUM(balance) FROM users")
        total_users, total_balance = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM withdraws WHERE status = 'pending'")
        pending = cur.fetchone()[0]
        conn.close()
        return total_users or 0, total_balance or 0, pending or 0
    total_users, total_balance, pending = await asyncio.to_thread(_stats)
    await update.message.reply_text(f"Users: {total_users}\nTotal balance: {total_balance}\nPending withdraws: {pending}")

async def admin_withdraws(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id):
        return
    rows = await list_pending_withdraws()
    if not rows:
        await update.message.reply_text("No pending withdraws.")
        return
    text = ["Pending withdraws (id user amount at note):"]
    for r in rows[:50]:
        wid, uid, amt, when, note = r
        text.append(f"{wid} | {uid} | {amt} | {when} | {note}")
    await update.message.reply_text("\n".join(text))

async def admin_withdraw_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id):
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /withdraw_approve <id>")
        return
    try:
        wid = int(args[0])
    except Exception:
        await update.message.reply_text("Invalid id.")
        return
    await update_withdraw_status(wid, "approved")
    await update.message.reply_text(f"Withdraw {wid} marked approved.")

async def admin_addfunds(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id):
        return
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: /addfunds <user_id> <amount>")
        return
    try:
        uid = int(args[0]); amt = int(args[1])
    except Exception:
        await update.message.reply_text("Invalid args.")
        return
    await ensure_user(uid, f"user{uid}")
    await add_balance(uid, amt)
    await update.message.reply_text(f"Added {amt} to {uid}.")

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id):
        return
    text = " ".join(context.args or [])
    if not text:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    uids = await all_user_ids()
    sent = 0
    for i, uid in enumerate(uids):
        try:
            await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except Exception as e:
            logger.warning("Broadcast failed to %s: %s", uid, e)
        # simple rate limiting to avoid flood
        if i % BROADCAST_BATCH_SIZE == 0:
            await asyncio.sleep(1)
    await update.message.reply_text(f"Broadcast attempted to {len(uids)} users, sent={sent}.")

# Ads admin
async def admin_ads_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id):
        return
    payload = " ".join(context.args or [])
    # expected format: title|image_url|target_url|reward
    parts = payload.split("|")
    if len(parts) < 3:
        await update.message.reply_text("Usage: /ads_add title|image_url|target_url|reward(optional)")
        return
    title = parts[0].strip()
    image_url = parts[1].strip()
    target_url = parts[2].strip()
    reward = int(parts[3]) if len(parts) >= 4 and parts[3].strip().isdigit() else 0
    await add_ad(title, image_url, target_url, reward)
    await update.message.reply_text("Ad added.")

async def admin_ads_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id):
        return
    rows = await list_ads(active_only=False)
    if not rows:
        await update.message.reply_text("No ads.")
        return
    text = ["Ads (id | title | active | reward | created_at):"]
    for r in rows:
        # r: id, title, image_url, target_url, active, reward_on_click, created_at
        text.append(f"{r[0]} | {r[1][:40]} | {r[4]} | {r[5]} | {r[6]}")
    await update.message.reply_text("\n".join(text))

async def admin_ads_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id):
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /ads_toggle <id>")
        return
    try:
        aid = int(args[0])
    except Exception:
        await update.message.reply_text("Invalid id.")
        return
    await toggle_ad(aid)
    await update.message.reply_text("Toggled ad active state.")

async def leaderboard_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await top_leaderboard(limit=10)
    if not rows:
        await update.message.reply_text("No users yet.")
        return
    text = ["Top users:"]
    for i, r in enumerate(rows, start=1):
        uid, uname, bal = r
        text.append(f"{i}. {uname or uid} — {bal} units")
    await update.message.reply_text("\n".join(text))

# small helper to show recent ad stats (admin)
async def admin_ad_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_owner(user.id):
        return
    def _stats():
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM ads WHERE active = 1")
        active_ads = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM ad_clicks WHERE clicked_at > ?", ((datetime.utcnow()-timedelta(days=7)).isoformat(),))
        clicks_week = cur.fetchone()[0]
        conn.close()
        return active_ads, clicks_week
    active_ads, clicks_week = await asyncio.to_thread(_stats)
    await update.message.reply_text(f"Active ads: {active_ads}\nClicks (7d): {clicks_week}")

# --- Application setup ---
def main():
    if not TOKEN:
        logger.error("BOT_TOKEN env var required")
        return
    # init DB
    asyncio.run(init_db())

    app = ApplicationBuilder().token(TOKEN).build()

    # user commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("earn", earn))
    app.add_handler(CommandHandler("withdraw", withdraw_cmd))
    app.add_handler(CommandHandler("leaderboard", leaderboard_cmd))
    app.add_handler(CommandHandler("showad", show_ad_cmd))

    # ad callbacks
    app.add_handler(CallbackQueryHandler(ad_callback, pattern=r"^(adopen:|adskip)"))

    # admin commands
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("withdraws", admin_withdraws))
    app.add_handler(CommandHandler("withdraw_approve", admin_withdraw_approve))
    app.add_handler(CommandHandler("addfunds", admin_addfunds))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))

    app.add_handler(CommandHandler("ads_add", admin_ads_add))
    app.add_handler(CommandHandler("ads_list", admin_ads_list))
    app.add_handler(CommandHandler("ads_toggle", admin_ads_toggle))
    app.add_handler(CommandHandler("adstats", admin_ad_stats))

    logger.info("Bot starting polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
