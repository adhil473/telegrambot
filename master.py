import asyncio
import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from pymongo import MongoClient

# --- CONFIGURATION ---
BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = [int(x) for x in os.environ["ADMIN_IDS"].split(",")]
MONGO_URI = os.environ["MONGO_URI"]

# --- DB CONNECTION ---
db_client = MongoClient(MONGO_URI)
db = db_client["telegram_farm"]

# --- COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_ID:
        return
    await update.message.reply_text(
        "⚡ **Farm Controller Online**\n\n"
        "Available Commands:\n"
        "/status - Check Workers & Targets\n"
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

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    
    print("Master Controller is waiting for commands...")
    app.run_polling()