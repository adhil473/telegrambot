import asyncio
import os
from telethon import TelegramClient
from telethon.sessions import StringSession
from pymongo import MongoClient

# --- Database Setup ---
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
targets_col = db["targets"]

async def run_scraper(group_index=None):
    """
    If group_index is None: Returns the list of groups for the bot to display.
    If group_index is an integer: Scrapes that specific group.
    """
    print(f"[SCRAPER] run_scraper called with group_index={group_index}")

    # 1. Pull a Healthy Worker
    worker = workers_col.find_one({"status": "HEALTHY", "proxy": {"$ne": None}})
    
    if not worker:
        return "[FAIL] No healthy, shielded workers found."

    print(f"[SCRAPER] Using worker: {worker['phone']}")

    # 2. Client Setup
    client = TelegramClient(
        StringSession(worker['session_str']),
        worker['api_id'],
        worker['api_hash'],
        device_model=worker['identity']['device_model'],
        proxy=worker['proxy'],
        connection_retries=2,
        timeout=15
    )

    try:
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return "[FAIL] Worker session expired. Re-register with identity_manager.py"

        # Sync session
        await client.get_dialogs(limit=1)
        
        # 3. Fetch Dialogs
        groups = []
        async for dialog in client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                if hasattr(dialog.entity, 'broadcast') and dialog.entity.broadcast:
                    continue
                groups.append(dialog)

        # If no index provided, return the group list to the Master Bot
        if group_index is None:
            await client.disconnect()
            print(f"[SCRAPER] Returning {len(groups)} groups")
            return groups

        # If index provided, attempt to scrape
        try:
            target_group = groups[int(group_index)]
        except (ValueError, IndexError):
            await client.disconnect()
            return "[FAIL] Invalid group selection. Use /scrape to see the list."

        # 5. The Scrape
        limit = 1000
        active_users = {}
        print(f"[SCRAPER] Scraping '{target_group.name}' (limit={limit})...")

        async for message in client.iter_messages(target_group, limit=limit):
            if message.sender_id:
                user = await message.get_sender()
                if user and hasattr(user, 'username') and user.username:
                    active_users[user.id] = {
                        "user_id": user.id,
                        "username": user.username,
                        "name": f"{getattr(user, 'first_name', '')}".strip(),
                        "source": target_group.name,
                        "status": "PENDING"
                    }

        await client.disconnect()

        # 6. Database Save
        if active_users:
            new_targets = list(active_users.values())
            for target in new_targets:
                targets_col.update_one(
                    {"user_id": target["user_id"]},
                    {"$set": target},
                    upsert=True
                )
            print(f"[SCRAPER] Harvested {len(active_users)} users")
            return f"[OK] Harvested {len(active_users)} users from {target_group.name}"
        else:
            return f"[!] No active users with usernames found in {target_group.name}."

    except Exception as e:
        print(f"[SCRAPER] Error: {e}")
        try:
            await client.disconnect()
        except:
            pass
        return f"[FAIL] Unexpected Error: {str(e)}"

# Keep this for local testing if needed
if __name__ == "__main__":
    import sys
    idx = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(run_scraper(idx))
