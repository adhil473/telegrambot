from pymongo import MongoClient
import sys

# --- Database Connection ---
# Using your provided URI for the telegram_farm database
MONGO_URI = "mongodb+srv://adhilu260_db_user:eRrfjLS0765RjmIT@cluster0.axenuzr.mongodb.net/?appName=Cluster0" 
db_client = MongoClient(MONGO_URI)

try:
    db_client.admin.command('ping')
    print("[OK] MongoDB connected successfully")
except Exception as e:
    print(f"[FAIL] MongoDB connection failed: {e}")
    exit(1)

db = db_client["telegram_farm"]
workers_col = db["workers"]

def parse_proxy_line(line):
    """
    Parses your specific residential proxy format: host:port:username:password
    Example format: change5.owlproxy.com:7778:DWuyUP3xGJ20_..._time_5:2997933
    """
    try:
        parts = line.strip().split(':')
        if len(parts) >= 4:
            return {
                "addr": parts[0],
                "port": int(parts[1]),
                "username": parts[2],
                "password": parts[3],
                "proxy_type": "socks5"
            }
    except Exception as e:
        print(f"[!] Skipping invalid line: {line[:30]}... Error: {e}")
        return None
    return None

def assign_proxies_to_fleet():
    print("\n--- Step 2: Proxy Fleet Distribution ---")
    print("Paste your extracted proxy lines from the dashboard below.")
    print("Press Enter, then Ctrl+D (Linux/Mac) or Ctrl+Z (Windows) to process:")
    
    # Read all lines pasted from the terminal
    try:
        input_data = sys.stdin.read()
    except EOFError:
        input_data = ""
        
    lines = [l.strip() for l in input_data.split('\n') if l.strip()]

    if not lines:
        print("[FAIL] No proxy lines were provided. Extraction aborted.")
        return

    # Fetch all workers currently in the database
    # We target 'HEALTHY' workers to ensure we don't waste proxies on 'DEAD' accounts
    workers = list(workers_col.find({"status": "HEALTHY"}))
    
    if not workers:
        print("[!] No workers with status 'HEALTHY' found in the database.")
        return

    print(f"[*] Processing: {len(workers)} workers and {len(lines)} proxy lines.")
    
    assigned_count = 0
    for i, worker in enumerate(workers):
        # Round-robin logic: If workers > proxies, it restarts the list to ensure 
        # every worker gets a shielded connection.
        proxy_line = lines[i % len(lines)]
        proxy_data = parse_proxy_line(proxy_line)
        
        if proxy_data:
            workers_col.update_one(
                {"_id": worker["_id"]},
                {"$set": {"proxy": proxy_data}}
            )
            assigned_count += 1

    print(f"\n[OK] Successfully shielded {assigned_count} workers with SOCKS5 proxies.")
    print("[INFO] All proxies set to 5-Minute Sticky Cycles as per dashboard config.")

if __name__ == "__main__":
    assign_proxies_to_fleet()