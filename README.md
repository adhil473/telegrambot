# Telegram Farm Bot

A 4-step pipeline to register Telegram worker accounts, shield them with proxies, scrape active members from target groups, and auto-add them to your group — all monitored via a Telegram bot controller.

---

## How It Works

```
Step 1                    Step 2                    Step 3                    Step 4
identity_manager.py  →   proxy_manager.py     →   scraper.py           →   auto_adder.py
Register accounts        Assign proxies            Harvest members           Add to your group

                              master.py
                         (Remote Controller Bot)
                     Monitor stats via Telegram commands
```

### Step 1: Identity Registration (`identity_manager.py`)
- Logs into Telegram with your phone number + OTP
- Generates a randomized device fingerprint (Samsung S23 variants) to avoid bot detection
- Saves the session string + identity to MongoDB so you never need OTP again

### Step 2: Proxy Assignment (`proxy_manager.py`)
- Takes SOCKS5 proxy lines from your provider (BrightData, Smartproxy, OwlProxy, etc.)
- Assigns proxies to all HEALTHY workers using round-robin distribution
- Ensures each worker's traffic routes through a residential IP

### Step 3: Member Scraping (`scraper.py`)
- Picks a healthy, proxy-shielded worker from the database
- Verifies proxy is alive with a heartbeat check
- Lists all joined groups and lets you pick one
- Scans the last 5000 messages for active users
- Deduplicates and saves users (with usernames) to MongoDB as `PENDING` targets

### Step 4: Auto-Adder (`auto_adder.py`)
- Pulls all `PENDING` targets and `HEALTHY` workers from MongoDB
- Rotates through workers (each adds 5 users max per cycle)
- Invites targets to your group using `InviteToChannelRequest`
- Sleeps 60-120s between adds to mimic human behavior
- Handles errors:
  - `PeerFloodError` → marks worker as `COOLDOWN`, moves to next
  - `UserPrivacyRestrictedError` → marks target as `PRIVACY_RESTRICTED`
  - Proxy timeout → marks worker proxy as `EXPIRED`, rotates to next

### Remote Controller (`master.py`)
- A Telegram bot you control from your phone
- Only responds to your ADMIN_ID (everyone else is silently ignored)
- Available commands:
  - `/start` — Shows available commands
  - `/status` — Reports healthy workers, pending targets, and total added count

---

## Prerequisites

- Python 3.10+
- MongoDB Atlas account (or local MongoDB)
- Telegram API credentials (get from https://my.telegram.org)
- SOCKS5 residential proxies from a provider
- A Telegram bot token from @BotFather (for master.py)

## Installation

```bash
pip install telethon pymongo python-telegram-bot
```

## Usage

### Step 1: Register a worker account
```bash
python identity_manager.py
```
You'll be prompted for:
- Phone number (+countrycode)
- API ID
- API Hash
- OTP code (sent to your Telegram)

### Step 2: Assign proxies
```bash
python proxy_manager.py
```
Paste your proxy lines in format `host:port:username:password`, then press `Ctrl+Z` + `Enter` (Windows).

### Step 3: Scrape members
```bash
python scraper.py
```
Select a group from the list when prompted.

### Step 4: Add members to your group
```bash
python auto_adder.py
```
Enter your group username (e.g., `@mygroup`) when prompted.

### Remote Controller
```bash
python master.py
```
Then open your Telegram and send `/status` to your bot.

---

## Database Structure (MongoDB)

**Database:** `telegram_farm`

### `workers` collection
```json
{
  "phone": "+1234567890",
  "api_id": 12345,
  "api_hash": "abc123",
  "session_str": "...",
  "identity": {
    "device_model": "SM-S918B (S23 Ultra)",
    "system_version": "Android 13.0",
    "device_id": "uuid"
  },
  "status": "HEALTHY",
  "total_adds": 0,
  "proxy": {
    "addr": "gate.proxy.com",
    "port": 7000,
    "username": "user",
    "password": "pass",
    "proxy_type": "socks5"
  }
}
```

**Worker statuses:** `HEALTHY` | `COOLDOWN` | `DEAD`

### `targets` collection
```json
{
  "user_id": 1001,
  "username": "alice",
  "name": "Alice",
  "source": "GroupName",
  "status": "PENDING"
}
```

**Target statuses:** `PENDING` | `COMPLETED` | `PRIVACY_RESTRICTED`

---

## Running Tests

```bash
python -m unittest test_identity_manager -v
python -m unittest test_proxy_manager -v
python -m unittest test_scraper -v
python -m unittest test_auto_adder -v
python -m unittest test_master -v
```

---

## Project Structure

```
telegrambot/
├── identity_manager.py      # Step 1: Register worker accounts
├── proxy_manager.py         # Step 2: Assign SOCKS5 proxies
├── scraper.py               # Step 3: Scrape active members
├── auto_adder.py            # Step 4: Auto-add members to your group
├── master.py                # Remote controller bot (Telegram commands)
└── README.md
```
