import asyncio
import os
import random
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import InviteToChannelRequest
from pymongo import MongoClient
from scraper import run_scraper

# --- CONFIGURATION ---
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = [int(x) for x in os.environ["ADMIN_IDS"].split(",")]
MONGO_URI = os.environ["MONGO_URI"]

# --- DB CONNECTION ---
db_client = MongoClient(MONGO_URI)
db = db_client["telegram_farm"]

# --- Auto-Adder Logic ---
async def run_auto_adder(target_group):
    """Rotates through workers to add pending targets to the given group."""
    print(f"[ADDER] run_auto_adder called for {target_group}")

    # Atomically claim targets to prevent double-adds
    targets = []
    for _ in range(50):
        target = db["targets"].find_one_and_update(
            {"status": "PENDING"},
            {"$set": {"status": "IN_PROGRESS"}},
        )
        if not target:
            break
        targets.append(target)

    workers = list(db["workers"].find({"status": "HEALTHY", "proxy": {"$ne": None}}))

    if not targets:
        return "[FAIL] No pending targets. Run /scrape first."
    if not workers:
        for t in targets:
            db["targets"].update_one({"_id": t["_id"]}, {"$set": {"status": "PENDING"}})
        return "[FAIL] No healthy workers available."

    print(f"[ADDER] {len(targets)} targets, {len(workers)} workers")
    added = 0
    limit_per_worker = 5

    for worker in workers:
        if not targets:
            break

        client = TelegramClient(
            StringSession(worker['session_str']),
            worker['api_id'],
            worker['api_hash'],
            device_model=worker['identity']['device_model'],
            system_version=worker['identity']['system_version'],
            proxy=worker['proxy'],
            connection_retries=1,
            timeout=15
        )

        try:
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                print(f"[ADDER] Worker {worker['phone']} session expired")
                continue

            for _ in range(limit_per_worker):
                if not targets:
                    break
                target = targets.pop(0)
                try:
                    print(f"[ADDER] Adding {target['username']}...")
                    await client(InviteToChannelRequest(target_group, [target['username']]))
                    db["targets"].update_one({"_id": target["_id"]}, {"$set": {"status": "COMPLETED"}})
                    added += 1
                    await asyncio.sleep(random.randint(60, 120))
                except Exception as e:
                    error_msg = str(e)
                    print(f"[ADDER] Error adding {target['username']}: {error_msg}")
                    if "PeerFloodError" in error_msg or "UserDeactivated" in error_msg:
                        db["workers"].update_one({"_id": worker["_id"]}, {"$set": {"status": "COOLDOWN"}})
                        break
                    if "UserPrivacyRestrictedError" in error_msg:
                        db["targets"].update_one({"_id": target["_id"]}, {"$set": {"status": "PRIVACY_RESTRICTED"}})

            await client.disconnect()

        except (ConnectionError, asyncio.TimeoutError) as e:
            print(f"[ADDER] Proxy expired for {worker['phone']}: {e}")
            db["workers"].update_one({"_id": worker["_id"]}, {"$set": {"proxy_status": "EXPIRED"}})
            try:
                await client.disconnect()
            except:
                pass
            continue

    # Release any unclaimed targets back to PENDING
    for t in targets:
        db["targets"].update_one({"_id": t["_id"]}, {"$set": {"status": "PENDING"}})

    print(f"[ADDER] Done. Added {added} members.")
    return f"[OK] Added {added} members to {target_group}"

# --- COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"[CMD] /start from user {update.effective_user.id}")
    if update.effective_user.id not in ADMIN_ID:
        return
    await update.message.reply_text(
        "<b>Farm Controller Online</b>\n\n"
        "Available Commands:\n"
        "/status - Check Workers &amp; Targets\n"
        "/scrape - List groups / Scrape a group\n"
        "/add - Start adding members",
        parse_mode='HTML'
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"[CMD] /status from user {update.effective_user.id}")
    if update.effective_user.id not in ADMIN_ID: return
    
    try:
        healthy = db.workers.count_documents({"status": "HEALTHY"})
        pending = db.targets.count_documents({"status": "PENDING"})
        completed = db.targets.count_documents({"status": "COMPLETED"})
        
        msg = (f"<b>Current Stats</b>\n"
               f"Workers: {healthy}/10\n"
               f"Pending Targets: {pending}\n"
               f"Total Added: {completed}")
        await update.message.reply_text(msg, parse_mode='HTML')
    except Exception as e:
        print(f"[ERROR] /status failed: {e}")
        await update.message.reply_text(f"[ERROR] {e}")

async def scrape_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"[CMD] /scrape from user {update.effective_user.id}, args={context.args}")
    if update.effective_user.id not in ADMIN_ID: return

    try:
        if not context.args:
            await update.message.reply_text("Fetching your group list...")
            groups = await run_scraper(group_index=None)
            
            if isinstance(groups, str):
                await update.message.reply_text(groups)
                return

            if not groups:
                await update.message.reply_text("[!] No groups found. Join a group with a worker first.")
                return

            msg = "<b>Select a group index to scrape:</b>\n\n"
            for i, group in enumerate(groups):
                msg += f"<code>{i}</code> - {group.name}\n"
            msg += "\nReply with: <code>/scrape [index]</code>"
            await update.message.reply_text(msg, parse_mode='HTML')
        else:
            idx = context.args[0]
            await update.message.reply_text(f"Starting harvest on group index: {idx}...")
            result = await run_scraper(group_index=idx)
            await update.message.reply_text(str(result))
    except Exception as e:
        print(f"[ERROR] /scrape failed: {e}")
        await update.message.reply_text(f"[ERROR] Scrape failed: {e}")

async def add_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"[CMD] /add from user {update.effective_user.id}, args={context.args}")
    if update.effective_user.id not in ADMIN_ID: return

    try:
        if not context.args:
            await update.message.reply_text("Usage: <code>/add @YourGroupUsername</code>", parse_mode='HTML')
            return

        target_group = context.args[0]
        await update.message.reply_text(f"Launching worker fleet for {target_group}...")
        
        result = await run_auto_adder(target_group)
        await update.message.reply_text(str(result))
    except Exception as e:
        print(f"[ERROR] /add failed: {e}")
        await update.message.reply_text(f"[ERROR] Add failed: {e}")

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("scrape", scrape_handler))
    app.add_handler(CommandHandler("add", add_handler))
    
    print("[BOT] Master Controller is LIVE and waiting for commands...")
    app.run_polling()
