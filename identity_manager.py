import asyncio
import uuid
import os
import random
from dotenv import load_dotenv
load_dotenv()

from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from pymongo import MongoClient

MONGO_URI = os.environ["MONGO_URI"] 
db_client = MongoClient(MONGO_URI)
try:
    db_client.admin.command('ping')
    print("[OK] MongoDB connected successfully")
except Exception as e:
    print(f"[FAIL] MongoDB connection failed: {e}")
    exit(1)

db = db_client["telegram_farm"]
workers_col = db["workers"]

async def register_worker():
    print("--- Step 1: Secure Identity Registration ---")
    
    phone = await asyncio.to_thread(input, "Enter Phone Number (+countrycode): ")
    api_id = int(await asyncio.to_thread(input, "Enter API ID: "))
    api_hash = await asyncio.to_thread(input, "Enter API Hash: ")

    # Randomized Device Fingerprinting to avoid the "Script" detection
    # We use different Samsung S23 variants to diversify the 'fleet'
    model_variants = ["SM-S918B", "SM-S918U", "SM-S918N", "SM-S918W"]
    device_model = f"{random.choice(model_variants)} (S23 Ultra)"
    system_version = f"Android {random.randint(12, 14)}.0"
    
    print(f"Generating Unique Identity: {device_model} on {system_version}...")

    # Initialize with StringSession: No .session files needed on your disk!
    client = TelegramClient(
        StringSession(), 
        api_id, 
        api_hash,
        device_model=device_model,
        system_version=system_version,
        app_version="10.5.0"
    )

    try:
        async def get_code():
            return await asyncio.to_thread(input, "Enter the OTP code: ")

        async def get_password():
            return await asyncio.to_thread(input, "Enter 2FA password (if enabled): ")

        await client.start(
            phone=phone,
            code_callback=get_code,
            password=get_password
        )
        
        # This string is the 'key' to your account—no more SMS codes needed
        session_str = client.session.save()

        # Save to MongoDB with Status tracking
        worker_data = {
            "phone": phone,
            "api_id": api_id,
            "api_hash": api_hash,
            "session_str": session_str,
            "identity": {
                "device_model": device_model,
                "system_version": system_version,
                "device_id": str(uuid.uuid4())
            },
            "status": "HEALTHY", # Tracks if account is banned or on cooldown
            "total_adds": 0,
            "proxy": None        # To be filled in Step 2
        }

        workers_col.update_one({"phone": phone}, {"$set": worker_data}, upsert=True)
        print(f"[OK] SUCCESS: {phone} saved to database as {device_model}")

    except Exception as e:
        import traceback
        print(f"[FAIL] FAILED: {e}")
        traceback.print_exc()
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(register_worker())