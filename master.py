import asyncio
import os
import random
import uuid
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
from pymongo import MongoClient
from telethon import TelegramClient
from telethon.sessions import StringSession

# --- CONFIGURATION ---
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = [int(x) for x in os.environ["ADMIN_IDS"].split(",")]
MONGO_URI = os.environ["MONGO_URI"]

# --- DB CONNECTION ---
db_client = MongoClient(MONGO_URI)
db = db_client["telegram_farm"]

# --- CONVERSATION STATES ---
CHOOSING_CREDENTIALS, WAITING_FOR_OTP = range(2)

# --- DEVICE SPOOFING GENERATOR ---
def generate_s23_identity():
    """Generates a unique Samsung Galaxy S23 Ultra signature to avoid library fingerprinting"""
    android_versions = ["13.0", "14.0"]
    software_builds = ["UP1A.231005.007", "UKQ1.230804.001"]
    return {
        "device_model": f"SM-S918U (Galaxy S23 Ultra Variant-{random.randint(1,100)})",
        "system_version": f"Android {random.choice(android_versions)} (Build/{random.choice(software_builds)})",
        "device_id": str(uuid.uuid4())
    }

# --- EXISTING COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID:
        return
    await update.message.reply_text(
        "⚡ **Farm Controller Online**\n\n"
        "Available Commands:\n"
        "/status - Check Workers & Targets\n"
        "/add_worker - Register a new account via chat\n"
        "/scrape - Start Harvester\n"
        "/add - Start Auto-Adder"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID: return
    
    healthy = db.workers.count_documents({"status": "HEALTHY"})
    pending = db.targets.count_documents({"status": "PENDING"})
    completed = db.targets.count_documents({"status": "COMPLETED"})
    
    msg = (f"📊 **Current Stats**\n"
           f"✅ Workers: {healthy}/10\n"
           f"🎯 Pending Targets: {pending}\n"
           f"🎉 Total Added: {completed}")
    await update.message.reply_text(msg)

# --- NEW: WORKER REGISTRATION WIZARD ---

async def start_add_worker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID: 
        return ConversationHandler.END
    
    await update.message.reply_text(
        "📝 **Worker Registration Wizard Started**\n\n"
        "Please send your credentials exactly in this format:\n"
        "`API_ID API_HASH PHONE_NUMBER`\n\n"
        "Example:\n"
        "`123456 abcdef1234567890 +917994308742`"
    )
    return CHOOSING_CREDENTIALS

async def process_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID: 
        return ConversationHandler.END
    
    try:
        parts = update.message.text.strip().split()
        if len(parts) != 3:
            await update.message.reply_text("❌ Invalid format. Send: `API_ID API_HASH PHONE`")
            return CHOOSING_CREDENTIALS
        
        api_id = int(parts[0])
        api_hash = parts[1]
        phone = parts[2]
        
        await update.message.reply_text("⏳ Handshaking with Telegram core servers...")
        
        identity = generate_s23_identity()
        string_session = StringSession()
        
        # Instantiate temporary client loop
        client = TelegramClient(
            string_session, api_id, api_hash,
            device_model=identity["device_model"],
            system_version=identity["system_version"]
        )
        
        await client.connect()
        phone_code_hash = await client.send_code_request(phone)
        await client.disconnect()
        
        # Stash details contextually into conversation step memory
        context.user_data["api_id"] = api_id
        context.user_data["api_hash"] = api_hash
        context.user_data["phone"] = phone
        context.user_data["phone_code_hash"] = phone_code_hash.phone_code_hash
        context.user_data["session_str"] = string_session.save()
        context.user_data["identity"] = identity
        
        await update.message.reply_text(f"📩 Login OTP code sent to **{phone}**. Reply here with that code:")
        return WAITING_FOR_OTP
        
    except Exception as e:
        await update.message.reply_text(f"❌ Handshake failed: `{str(e)}`")
        return ConversationHandler.END

async def process_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID: 
        return ConversationHandler.END
    
    otp_code = update.message.text.strip()
    data = context.user_data
    
    await update.message.reply_text("⚡ Authenticating login session tokens...")
    
    client = TelegramClient(
        StringSession(data["session_str"]), 
        data["api_id"], 
        data["api_hash"],
        device_model=data["identity"]["device_model"],
        system_version=data["identity"]["system_version"]
    )
    
    try:
        await client.connect()
        await client.sign_in(
            phone=data["phone"],
            code=otp_code,
            phone_code_hash=data["phone_code_hash"]
        )
        
        final_session_str = client.session.save()
        
        # Formulate core worker schema payload mapping
        worker_document = {
            "phone": data["phone"],
            "api_id": data["api_id"],
            "api_hash": data["api_hash"],
            "session_str": final_session_str,
            "identity": data["identity"],
            "proxy": None, # Will be filled automatically via proxy_manager.py later
            "status": "HEALTHY"
        }
        
        db.workers.update_one(
            {"phone": data["phone"]},
            {"$set": worker_document},
            upsert=True
        )
        
        await update.message.reply_text(
            f"✅ **Onboarding Complete!**\n\n"
            f"📱 Account: `{data['phone']}`\n"
            f"⚙️ Profile: `{data['identity']['device_model']}`\n"
            f"🟢 Status: **HEALTHY**"
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Activation Error: `{str(e)}`")
    finally:
        await client.disconnect()
        context.user_data.clear()
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Action cancelled.")
    return ConversationHandler.END

if __name__ == '__main__':
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
    app.add_handler(conv_handler)
    
    print("Master Controller is waiting for commands...")
    app.run_polling()