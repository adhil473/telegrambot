import asyncio
import os
import random
import uuid
import concurrent.futures
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, 
    CommandHandler, 
    ContextTypes, 
    ConversationHandler, 
    MessageHandler, 
    filters
)
from telethon import TelegramClient
from telethon.sessions import StringSession
from pymongo import MongoClient
from scraper import run_scraper

# --- CONFIGURATION ---
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = [int(x) for x in os.environ["ADMIN_IDS"].split(",")]
MONGO_URI = os.environ["MONGO_URI"]

# --- DB CONNECTION ---
db_client = MongoClient(MONGO_URI)
db = db_client["telegram_farm"]

# --- CONVERSATION STATES ---
CHOOSING_CREDENTIALS, WAITING_FOR_OTP = range(2)

# --- THREAD POOL FOR ISOLATING TELETHON LOOP COLLISIONS ---
executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

# --- SERVICE NOTIFICATION HELPER ---
async def send_system_log(bot, message_text: str):
    """Broadcasts real-time system alerts directly to all registered administrators."""
    for admin in ADMIN_ID:
        try:
            await bot.send_message(chat_id=admin, text=message_text, parse_mode='HTML')
        except Exception as e:
            print(f"[LOG ERROR] Failed sending alert to {admin}: {e}")

# --- DEVICE SPOOFING GENERATOR ---
def generate_s23_identity():
    android_versions = ["13.0", "14.0"]
    software_builds = ["UP1A.231005.007", "UKQ1.230804.001"]
    return {
        "device_model": f"SM-S918U (Galaxy S23 Ultra Variant-{random.randint(1,100)})",
        "system_version": f"Android {random.choice(android_versions)} (Build/{random.choice(software_builds)})",
        "device_id": str(uuid.uuid4())
    }

# --- THREAD-ISOLATED TELETHON TASKS ---
def _th_request_otp(session_str, api_id, api_hash, phone, identity):
    """Executes the core connection handshake in an isolated runtime loop."""
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    client = TelegramClient(
        StringSession(session_str), api_id, api_hash,
        device_model=identity["device_model"],
        system_version=identity["system_version"],
        loop=loop
    )
    
    async def _run():
        await client.connect()
        code_hash = await client.send_code_request(phone)
        await client.disconnect()
        return code_hash.phone_code_hash

    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()

def _th_verify_otp(session_str, api_id, api_hash, phone, otp_code, phone_code_hash, identity):
    """Verifies the input login code within an isolated thread context."""
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    client = TelegramClient(
        StringSession(session_str), api_id, api_hash,
        device_model=identity["device_model"],
        system_version=identity["system_version"],
        loop=loop
    )
    
    async def _run():
        await client.connect()
        await client.sign_in(phone=phone, code=otp_code, phone_code_hash=phone_code_hash)
        final_session = client.session.save()
        await client.disconnect()
        return final_session

    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()

# --- AUTOMATED PROXY HEALTH MONITOR (BACKGROUND ENGINE) ---
async def run_proxy_heartbeat_check(bot):
    """Automated worker validation daemon checking connection state health bounds."""
    while True:
        print("[HEARTBEAT] Commencing periodic connectivity health sweep...")
        workers = list(db.workers.find({"status": "HEALTHY", "proxy": {"$ne": None}}))
        
        for worker in workers:
            phone = worker.get("phone", "Unknown")
            
            client = TelegramClient(
                StringSession(worker['session_str']),
                worker['api_id'],
                worker['api_hash'],
                device_model=worker['identity']['device_model'],
                system_version=worker['identity']['system_version'],
                proxy=worker['proxy'],
                connection_retries=0, 
                timeout=10
            )
            
            try:
                start_time = datetime.now()
                await client.connect()
                await client.get_me() 
                latency = (datetime.now() - start_time).total_seconds()
                
                print(f"[HEARTBEAT] {phone} -> Proxy Validated ({latency:.2f}s latency)")
                await client.disconnect()
                
            except Exception as e:
                print(f"[HEARTBEAT ALERT] Worker {phone} connection dropped: {e}")
                
                db.workers.update_one(
                    {"_id": worker["_id"]},
                    {"$set": {"proxy_status": "DEAD_OR_EXPIRED", "proxy_last_checked": datetime.now()}}
                )
                
                alert_msg = (
                    f"⚠️ <b>PROXY BREAKDOWN ALERT</b>\n\n"
                    f"📱 <b>Worker Account:</b> <code>{phone}</code>\n"
                    f"❌ <b>Trace Error:</b> <code>Gateway handshake timeout.</code>\n"
                    f"💡 <i>Action: Extraction line expired. Execute proxy_manager.py script soon.</i>"
                )
                await send_system_log(bot, alert_msg)
                
                try:
                    await client.disconnect()
                except Exception:
                    pass
                    
        await asyncio.sleep(600)  # Verify status loops every 10 minutes

# --- CORE ADMINISTRATIVE COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID: return
    await update.message.reply_text(
        "⚡ <b>Farm Controller Online</b>\n\n"
        "Available Commands:\n"
        "/status - Detailed Fleet Breakdown & Analytics\n"
        "/add_worker - Onboard a new account via chat\n"
        "/scrape - List groups / Scrape a group\n"
        "/test_proxies - Force manual proxy fleet validation check",
        parse_mode='HTML'
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID: return
    
    try:
        healthy = db.workers.count_documents({"status": "HEALTHY"})
        pending = db.targets.count_documents({"status": "PENDING"})
        workers = list(db.workers.find({}))
        
        msg = (f"<b>🎛 FARM ANALYTICS METRICS</b>\n"
               f"🟢 Operational Workers: {healthy}\n"
               f"🎯 Unprocessed Leads: {pending}\n"
               f"=============================\n\n"
               f"📋 <b>INDIVIDUAL FLEET STATUS:</b>\n\n")
        
        for idx, worker in enumerate(workers, start=1):
            phone = worker.get("phone", "Unknown")
            status_field = worker.get("status", "UNKNOWN")
            p_status = worker.get("proxy_status", "HEALTHY")
            
            status_icon = "🟢" if (status_field == "HEALTHY" and p_status != "DEAD_OR_EXPIRED") else "🔴"
            proxy_link = "⚠️ DEAD / EXPIRED" if p_status == "DEAD_OR_EXPIRED" else "🔒 Secured" if worker.get("proxy") else "⚠️ Missing"
            
            msg += f"{idx}. {status_icon} <b>{phone}</b>\n"
            msg += f"   • Account Status: <code>{status_field}</code>\n"
            msg += f"   • Proxy Protection: <code>{proxy_link}</code>\n"
            msg += f"-----------------------------\n"
            
        await update.message.reply_text(msg, parse_mode='HTML')
    except Exception as e:
        await update.message.reply_text(f"[ERROR] Metrics aggregation failed: {e}")

async def force_proxy_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID: return
    await update.message.reply_text("⚡ <i>Forcing manual proxy fleet health test... Check log notifications shortly.</i>", parse_mode='HTML')
    asyncio.create_task(send_system_log(context.bot, "📊 <b>Manual Proxy Scan Started...</b>"))

async def scrape_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID: return
    try:
        if not context.args:
            await update.message.reply_text("Fetching your group list...")
            groups = await run_scraper(group_index=None)
            
            if isinstance(groups, str):
                await update.message.reply_text(groups)
                return

            msg = "<b>Select a group index to scrape:</b>\n\n"
            for i, group in enumerate(groups):
                msg += f"<code>{i}</code> - {group.name}\n"
            msg += "\nReply with: <code>/scrape [index]</code>"
            await update.message.reply_text(msg, parse_mode='HTML')
        else:
            idx = context.args[0]
            await send_system_log(context.bot, f"🚀 <b>HARVEST ENGINE LIVE</b>\nTarget Index: {idx}\nStatus: Processing data blocks...")
            
            result = await run_scraper(group_index=idx)
            await update.message.reply_text(str(result))
            
            await send_system_log(context.bot, f"✅ <b>HARVEST COMPLETED</b>\nTarget Index: {idx}\nDatabase elements successfully cached.")
    except Exception as e:
        await update.message.reply_text(f"[ERROR] Scrape engine failure: {e}")

# --- WORKER ONBOARDING WIZARD HANDLERS ---

async def start_add_worker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID: return ConversationHandler.END
    await update.message.reply_text(
        "📝 <b>Worker Registration Wizard Active</b>\n\n"
        "Send your login details exactly in this layout:\n"
        "<code>API_ID API_HASH PHONE_NUMBER</code>",
        parse_mode='HTML'
    )
    return CHOOSING_CREDENTIALS

async def process_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID: return ConversationHandler.END
    try:
        parts = update.message.text.strip().split()
        if len(parts) != 3:
            await update.message.reply_text("❌ Missing arguments. Pattern: <code>API_ID API_HASH PHONE</code>", parse_mode='HTML')
            return CHOOSING_CREDENTIALS
        
        api_id, api_hash, phone = int(parts[0]), parts[1], parts[2]
        await update.message.reply_text("⏳ Issuing secure background thread challenge request...")
        
        identity = generate_s23_identity()
        string_session = StringSession()
        
        # Offload structural handshake block to protected executor pool
        current_loop = asyncio.get_running_loop()
        phone_code_hash = await current_loop.run_in_executor(
            executor, 
            _th_request_otp, 
            string_session.save(), api_id, api_hash, phone, identity
        )
        
        context.user_data.update({
            "api_id": api_id, "api_hash": api_hash, "phone": phone, 
            "phone_code_hash": phone_code_hash, "session_str": string_session.save(), "identity": identity
        })
        await update.message.reply_text(f"📩 Code routed to <b>{phone}</b>. Text the code here to login:", parse_mode='HTML')
        return WAITING_FOR_OTP
    except Exception as e:
        await update.message.reply_text(f"❌ Handshake failed: <code>{str(e)}</code>", parse_mode='HTML')
        return ConversationHandler.END

async def process_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID: return ConversationHandler.END
    otp_code = update.message.text.strip()
    data = context.user_data
    await update.message.reply_text("⚡ Registering verified session token context...")
    
    try:
        current_loop = asyncio.get_running_loop()
        final_session_str = await current_loop.run_in_executor(
            executor,
            _th_verify_otp,
            data["session_str"], data["api_id"], data["api_hash"], 
            data["phone"], otp_code, data["phone_code_hash"], data["identity"]
        )
        
        worker_document = {
            "phone": data["phone"], "api_id": data["api_id"], "api_hash": data["api_hash"],
            "session_str": final_session_str, "identity": data["identity"], 
            "proxy": None, "status": "HEALTHY", "proxy_status": "HEALTHY"
        }
        db.workers.update_one({"phone": data["phone"]}, {"$set": worker_document}, upsert=True)
        await update.message.reply_text(f"✅ <b>Worker Saved!</b>\n📱 Phone: <code>{data['phone']}</code>\n🟢 Fleet Status: <b>HEALTHY</b>", parse_mode='HTML')
    except Exception as e:
        await update.message.reply_text(f"❌ Authentication failed: <code>{str(e)}</code>\n\n<i>Tip: If this worker has 2FA active, disable it temporary while adding.</i>", parse_mode='HTML')
    finally:
        context.user_data.clear()
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Wizard session closed.")
    return ConversationHandler.END

# --- SYSTEM ENTRY POINT ---
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add_worker", start_add_worker)],
        states={
            CHOOSING_CREDENTIALS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_credentials)],
            WAITING_FOR_OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_otp)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("test_proxies", force_proxy_test))
    app.add_handler(CommandHandler("scrape", scrape_handler))
    app.add_handler(conv_handler)
    
    await app.initialize()
    await app.start()
    
    # Run background proxy status validations concurrently without bottleneck loops
    asyncio.create_task(run_proxy_heartbeat_check(app.bot))
    
    print("[BOT] Master Controller Engine is actively processing events...")
    await app.updater.start_polling()
    
    while True:
        await asyncio.sleep(3600)

if __name__ == '__main__':
    asyncio.run(main())