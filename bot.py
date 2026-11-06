import os
import threading
import psycopg
from psycopg.rows import dict_row
import asyncio
import secrets
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, unquote, quote
import json
import re

from telegram import (
    Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton,
    InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes,
    ConversationHandler, CallbackQueryHandler, filters,
)

# ==================================================
# CONFIG
# ==================================================

TOKEN = os.getenv("BOT_TOKEN")
SUPER_ADMIN_USER_ID = os.getenv("SUPER_ADMIN_USER_ID")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

SUPPORT_PHONE = "0960011010"
SUPPORT_USERNAME = "tanacargosupportbot"
BOT_USERNAME = "tanacargobot"
CHANNEL_USERNAME = "tanacargopage"

START_IMAGE_ID = "AgACAgQAAxkBAAFWw_Vq389auQ7yoeq8rNETEyhfn02JagACCw9rG-tuAVNIehm8-b92jwEAAwIAA3kAAz0E"

TRACCAR_URL = (os.getenv("TRACCAR_URL") or "https://demo2.traccar.org").rstrip("/")
TRACCAR_TOKEN = os.getenv("TRACCAR_TOKEN", "")
TRACCAR_DEVICE_ID = os.getenv("TRACCAR_DEVICE_ID", "92643104")
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or ("https://" + os.getenv("RENDER_EXTERNAL_HOSTNAME", "") if os.getenv("RENDER_EXTERNAL_HOSTNAME") else "")).rstrip("/")

CONVERSATION_TIMEOUT_SECONDS = 600
CARGO_EXPIRY_HOURS = 72
MESSAGE_AUTO_DELETE_SECONDS = 1800
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_PATH = None

class _CompatRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int): return list(self.values())[key]
        return super().__getitem__(key)

class _CompatCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None
    def execute(self, sql, params=None):
        sql = sql.replace("?", "%s")
        if "INSERT OR REPLACE INTO PENDING_LISTING_BROADCASTS" in sql.upper():
            sql = sql.replace("INSERT OR REPLACE INTO", "INSERT INTO")
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload, created_at=EXCLUDED.created_at, status=EXCLUDED.status"
        upper = sql.lstrip().upper()
        identity_tables = ("cargo_posts", "truck_posts", "brokers", "pending_gmaps_shares", "rental_tracking", "vehicle_inquiries", "vehicle_listings", "machinery_listings", "admin_broadcast_log")
        if upper.startswith("INSERT INTO") and "RETURNING" not in upper:
            table = upper.split("INSERT INTO", 1)[1].strip().split(None, 1)[0].strip('"')
            if table.lower() in identity_tables:
                sql = sql.rstrip().rstrip(";") + " RETURNING id"
        self._cursor.execute(sql, params or ())
        if upper.startswith("INSERT INTO") and "RETURNING ID" in sql.upper():
            row = self._cursor.fetchone()
            self.lastrowid = (row.get("id") if isinstance(row, dict) else row[0]) if row else None
        return self
    def fetchone(self):
        row = self._cursor.fetchone()
        return _CompatRow(row) if isinstance(row, dict) else row
    def fetchall(self):
        return [_CompatRow(row) if isinstance(row, dict) else row for row in self._cursor.fetchall()]
    def __iter__(self):
        for row in self._cursor:
            yield _CompatRow(row) if isinstance(row, dict) else row
    @property
    def description(self): return self._cursor.description
    @property
    def rowcount(self): return self._cursor.rowcount

class _CompatConnection:
    def __init__(self, conn): self._conn = conn
    def cursor(self): return _CompatCursor(self._conn.cursor())
    def execute(self, sql, params=None): return self.cursor().execute(sql, params)
    def commit(self): return self._conn.commit()
    def rollback(self): return self._conn.rollback()
    def close(self): return self._conn.close()
    @property
    def row_factory(self): return None
    @row_factory.setter
    def row_factory(self, value): pass

def _db_connect(*args, **kwargs):
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is missing. Set Neon PostgreSQL connection string.")
    return _CompatConnection(psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10))

# ==================================================
# TRACCAR API
# ==================================================

def _cached_traccar_location(device_id: str):
    try:
        conn = _db_connect(DB_PATH)
        try:
            row = conn.execute("SELECT * FROM gps_last_known WHERE device_id=?", (str(device_id),)).fetchone()
            if not row: return None
            return {
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "address": row["address"] or "የመጨረሻ የተመዘገበ ቦታ",
                "timestamp": row["timestamp"] or row["cached_at"],
                "is_live": False,
                "status": "offline",
            }
        finally: conn.close()
    except Exception: return None

def _save_traccar_location(device_id: str, location: dict):
    try:
        conn = _db_connect(DB_PATH)
        try:
            conn.execute("""INSERT INTO gps_last_known (device_id,latitude,longitude,address,timestamp,cached_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET
                latitude=excluded.latitude, longitude=excluded.longitude,
                address=excluded.address, timestamp=excluded.timestamp, cached_at=excluded.cached_at""",
                (str(device_id), float(location["latitude"]), float(location["longitude"]),
                 location.get("address", ""), location.get("timestamp", ""),
                 datetime.now().astimezone().isoformat(timespec="seconds")))
            conn.commit()
        finally: conn.close()
    except Exception as e: print(f"GPS cache write error: {e}")

def get_traccar_device_id_for_user(user_id=None):
    if user_id is not None:
        try:
            per_user_key = f"TRACCAR_DEVICE_ID_{int(user_id)}"
            per_user_value = os.getenv(per_user_key, "").strip()
            if per_user_value: return per_user_value
        except (TypeError, ValueError): pass
    return str(TRACCAR_DEVICE_ID).strip()

def get_traccar_location(device_id: str):
    if not TRACCAR_TOKEN: return _cached_traccar_location(device_id)
    try:
        headers = {"Authorization": f"Bearer {TRACCAR_TOKEN}"}
        res = requests.get(f"{TRACCAR_URL}/api/devices", headers=headers, params={"uniqueId": str(device_id)}, timeout=10)
        if res.status_code != 200: return _cached_traccar_location(device_id)
        devices = res.json()
        if not devices: return _cached_traccar_location(device_id)
        pos_id = devices[0].get("positionId")
        if not pos_id: return _cached_traccar_location(device_id)
        res_pos = requests.get(f"{TRACCAR_URL}/api/positions?id={pos_id}", headers=headers, timeout=10)
        if res_pos.status_code != 200: return _cached_traccar_location(device_id)
        positions = res_pos.json()
        if not positions: return _cached_traccar_location(device_id)
        pos = positions[0]
        fix_time = pos.get("fixTime") or pos.get("deviceTime") or ""
        status = str(devices[0].get("status") or "unknown").lower()
        is_live = status == "online"
        try:
            fix_dt = datetime.fromisoformat(str(fix_time).replace("Z", "+00:00"))
            if fix_dt.tzinfo is None: fix_dt = fix_dt.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - fix_dt.astimezone(timezone.utc)).total_seconds() > 180:
                is_live = False
        except (TypeError, ValueError): is_live = False
        location = {
            "latitude": pos.get("latitude"),
            "longitude": pos.get("longitude"),
            "address": pos.get("address") or "አድራሻ አልተገኘም",
            "timestamp": str(fix_time)[:19],
            "is_live": is_live,
            "status": "online" if is_live else "offline",
        }
        if location["latitude"] is not None and location["longitude"] is not None:
            _save_traccar_location(device_id, location)
            return location
        return _cached_traccar_location(device_id)
    except Exception as e:
        print(f"Traccar API Error: {e}")
        return _cached_traccar_location(device_id)

# ==================================================
# ETHIOPIAN CALENDAR
# ==================================================

def is_ethiopian_leap_year(et_year): return (et_year % 4) == 3
def is_gregorian_leap_year(gc_year): return (gc_year % 4 == 0 and gc_year % 100 != 0) or (gc_year % 400 == 0)
def et_new_year_gregorian(et_year):
    gc_year = et_year + 7
    start_day = 12 if is_gregorian_leap_year(gc_year + 1) else 11
    return datetime(gc_year, 9, start_day)
def et_to_gc(et_year, et_month, et_day):
    max_day = 6 if et_month == 13 and is_ethiopian_leap_year(et_year) else 5 if et_month == 13 else 30
    if et_month < 1 or et_month > 13 or et_day < 1 or et_day > max_day: raise ValueError("Invalid date")
    return et_new_year_gregorian(et_year) + timedelta(days=(et_month - 1) * 30 + et_day - 1)
def gc_to_et_date(gc_date):
    candidate_year = gc_date.year - 7
    start = et_new_year_gregorian(candidate_year)
    if gc_date < start:
        candidate_year -= 1
        start = et_new_year_gregorian(candidate_year)
    days = (gc_date - start).days
    month = days // 30 + 1
    day = days % 30 + 1
    if month == 13:
        max_day = 6 if is_ethiopian_leap_year(candidate_year) else 5
        if day > max_day: raise ValueError("Out of year")
    return candidate_year, month, day
def parse_flexible_date(date_text):
    raw = date_text.strip().split("\n")[0].strip()
    raw = " ".join(raw.split())
    for sep in ["-", ",", ".", " "]: raw = raw.replace(sep, "/")
    parts = [p.strip() for p in raw.split("/") if p.strip()]
    if len(parts) != 3: return None, "የቀን ቅርጸቱ ትክክል አይደለም። ምሳሌ፦ 11/02/2019"
    try: day, month, year = (int(p) for p in parts)
    except ValueError: return None, "ቀን/ወር/ዓመት በቁጥር መሆን አለበት።"
    if year < 1900 or year > 2100: return None, "ዓመት ትክክል አይደለም።"
    return {"day": day, "month": month, "year": year}, ""
def convert_calendar(date_parts, calendar):
    day, month, year = date_parts["day"], date_parts["month"], date_parts["year"]
    try:
        if calendar == "ET":
            if month < 1 or month > 13: return "", "", "ወር 1 እና 13 መካከል መሆን አለበት።"
            max_day = 6 if month == 13 and is_ethiopian_leap_year(year) else 5 if month == 13 else 30
            if day < 1 or day > max_day: return "", "", f"ቀኑ 1 እና {max_day} መካከል መሆን አለበት።"
            gc_obj = et_to_gc(year, month, day)
            et_year, et_month, et_day = year, month, day
        else:
            gc_obj = datetime(year, month, day)
            et_year, et_month, et_day = gc_to_et_date(gc_obj)
    except (ValueError, OverflowError): return "", "", "ያስገቡት ቀን ትክክል አይደለም።"
    return f"{et_day:02d}/{et_month:02d}/{et_year}", f"{gc_obj.day:02d}/{gc_obj.month:02d}/{gc_obj.year}", ""
def init_db():
    conn = _db_connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id BIGINT PRIMARY KEY, name TEXT, username TEXT, owner_name TEXT, owner_phone TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS cargo_posts (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, user_id BIGINT, from_location TEXT, to_location TEXT, cargo_type TEXT, vehicle TEXT, size INTEGER, size_name TEXT, weight TEXT, date_et TEXT, date_gc TEXT, phone TEXT, price REAL, price_display TEXT, price_status TEXT, active INTEGER DEFAULT 1, timestamp TEXT, expiry_date TEXT, poster_type TEXT DEFAULT 'customer')")
    cursor.execute("CREATE TABLE IF NOT EXISTS truck_posts (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, user_id BIGINT, truck_type TEXT, plate TEXT, capacity TEXT, route TEXT, address TEXT, driver TEXT, phone TEXT, active INTEGER DEFAULT 1, timestamp TEXT, expiry_date TEXT, poster_type TEXT DEFAULT 'customer')")
    cursor.execute("CREATE TABLE IF NOT EXISTS brokers (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, user_id BIGINT, name TEXT, username TEXT, phone TEXT, address TEXT, active INTEGER DEFAULT 1, approved INTEGER DEFAULT 0, timestamp TEXT, sectors TEXT DEFAULT '[]')")
    cursor.execute("CREATE TABLE IF NOT EXISTS machinery_listings (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL, machinery_type TEXT NOT NULL, listing_mode TEXT NOT NULL, location TEXT NOT NULL, details TEXT DEFAULT '', price TEXT DEFAULT '', phone TEXT NOT NULL, photo_ids TEXT DEFAULT '[]', active INTEGER DEFAULT 1, created_at TEXT NOT NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS tracking_sessions (tracking_code TEXT PRIMARY KEY, request_type TEXT NOT NULL, driver_user_id BIGINT NOT NULL, customer_user_id BIGINT NOT NULL, origin TEXT DEFAULT '', destination TEXT DEFAULT '', cargo_description TEXT DEFAULT '', truck_description TEXT DEFAULT '', status TEXT DEFAULT 'ready', latitude REAL, longitude REAL, location_updated_at TEXT, created_at TEXT NOT NULL, completed_at TEXT)")
    
    for column, definition in (("customer_name", "TEXT DEFAULT ''"), ("customer_phone", "TEXT DEFAULT ''"), ("driver_phone", "TEXT DEFAULT ''")):
        cursor.execute(f"ALTER TABLE tracking_sessions ADD COLUMN IF NOT EXISTS {column} {definition}")
        
    cursor.execute("CREATE TABLE IF NOT EXISTS rental_tracking (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, requester_id BIGINT NOT NULL, requester_name TEXT DEFAULT '', target_name TEXT NOT NULL, target_phone TEXT NOT NULL, target_user_id BIGINT, map_url TEXT DEFAULT '', traccar_url TEXT DEFAULT '', status TEXT DEFAULT 'pending', created_at TEXT NOT NULL, approved_at TEXT, stopped_at TEXT)")
    cursor.execute("ALTER TABLE rental_tracking ADD COLUMN IF NOT EXISTS share_token TEXT")
    cursor.execute("CREATE TABLE IF NOT EXISTS vehicle_listings (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL, category TEXT NOT NULL, vehicle_type TEXT NOT NULL, location TEXT NOT NULL, details TEXT DEFAULT '', price TEXT DEFAULT '', phone TEXT NOT NULL, photo_ids TEXT DEFAULT '[]', photo_status TEXT DEFAULT 'provided', active INTEGER DEFAULT 1, created_at TEXT NOT NULL)")
    for column, definition in (("owner_name", "TEXT DEFAULT ''"), ("plate", "TEXT DEFAULT ''")):
        cursor.execute(f"ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS {column} {definition}")
        
    cursor.execute("CREATE TABLE IF NOT EXISTS vehicle_inquiries (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, listing_id INTEGER NOT NULL, owner_id BIGINT NOT NULL, buyer_id BIGINT NOT NULL, buyer_name TEXT NOT NULL, buyer_username TEXT DEFAULT '', buyer_phone TEXT NOT NULL, category TEXT NOT NULL, vehicle_type TEXT DEFAULT '', created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new')")
    cursor.execute("CREATE TABLE IF NOT EXISTS admin_broadcast_log (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, admin_id BIGINT NOT NULL, target_user_id BIGINT, message_text TEXT NOT NULL, sent_count INTEGER DEFAULT 0, created_at TEXT NOT NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS admin_preferences (user_id BIGINT PRIMARY KEY, show_private INTEGER NOT NULL DEFAULT 1)")
    cursor.execute("CREATE TABLE IF NOT EXISTS admin_media_links (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, platform TEXT NOT NULL, media_type TEXT NOT NULL, organization_name TEXT NOT NULL, media_url TEXT NOT NULL, created_by BIGINT NOT NULL, created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)")
    cursor.execute("CREATE TABLE IF NOT EXISTS pending_listing_broadcasts (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending')")
    cursor.execute("ALTER TABLE pending_listing_broadcasts ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending'")
    cursor.execute("CREATE TABLE IF NOT EXISTS connection_requests (request_index INTEGER PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS gps_admin_requests (request_type TEXT NOT NULL, request_index INTEGER NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL, PRIMARY KEY (request_type, request_index))")
    cursor.execute("CREATE TABLE IF NOT EXISTS pending_gmaps_shares (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, phone TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS gps_last_known (device_id TEXT PRIMARY KEY, latitude REAL NOT NULL, longitude REAL NOT NULL, address TEXT DEFAULT '', timestamp TEXT DEFAULT '', cached_at TEXT NOT NULL)")
    cursor.execute("CREATE TABLE IF NOT EXISTS public_tracking_tokens (token TEXT PRIMARY KEY, device_id TEXT NOT NULL, status TEXT DEFAULT 'active', label TEXT DEFAULT 'TANA CARGO', created_at TEXT NOT NULL, expires_at TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS referrals (id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, referrer_id BIGINT NOT NULL, referred_id BIGINT NOT NULL UNIQUE, created_at TEXT NOT NULL)")
    
    for table, column in (("users", "user_id"), ("cargo_posts", "user_id"), ("truck_posts", "user_id"), ("brokers", "user_id"), ("tracking_sessions", "driver_user_id"), ("tracking_sessions", "customer_user_id"), ("rental_tracking", "requester_id"), ("rental_tracking", "target_user_id"), ("vehicle_listings", "user_id"), ("machinery_listings", "user_id"), ("vehicle_inquiries", "owner_id"), ("vehicle_inquiries", "buyer_id"), ("admin_broadcast_log", "admin_id"), ("admin_broadcast_log", "target_user_id"), ("admin_preferences", "user_id")):
        cursor.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE BIGINT USING "{column}"::BIGINT')
    conn.commit()
    conn.close()

def load_persistent_data():
    global cargo_posts, truck_posts, brokers, connection_requests, users
    global pending_listing_broadcasts, next_listing_broadcast_id
    global gmaps_share_requests, tracking_stops, pending_gmaps_shares
    conn = _db_connect(DB_PATH)
    conn.row_factory = dict_row
    try:
        users = {}
        for row in conn.execute("SELECT * FROM users"):
            r = dict(row)
            users[int(r["user_id"])] = {
                "id": int(r["user_id"]), "name": r.get("name") or "",
                "username": r.get("username") or "",
                "owner": {"name": r.get("owner_name") or r.get("name") or "", "phone": r.get("owner_phone") or "", "user_id": int(r["user_id"])}
            }
        cargo_posts = []
        for row in conn.execute("SELECT * FROM cargo_posts ORDER BY id"):
            r = dict(row)
            cargo_posts.append({
                "id": r["id"], "user_id": r["user_id"], "from": r["from_location"],
                "to": r["to_location"], "type": r["cargo_type"], "vehicle": r["vehicle"],
                "size": r["size"], "size_name": r["size_name"], "weight": r["weight"],
                "date_et": r["date_et"], "date_gc": r["date_gc"], "phone": r["phone"],
                "price": r["price"], "price_display": r["price_display"],
                "price_status": r["price_status"], "active": bool(r["active"]),
                "timestamp": r["timestamp"], "expiry_date": r["expiry_date"],
                "poster_type": r["poster_type"] or "customer"
            })
        truck_posts = []
        for row in conn.execute("SELECT * FROM truck_posts ORDER BY id"):
            r = dict(row)
            truck_posts.append({
                "id": r["id"], "user_id": r["user_id"], "type": r["truck_type"],
                "plate": r["plate"], "capacity": r["capacity"], "route": r["route"],
                "address": r["address"], "driver": r["driver"], "phone": r["phone"],
                "active": bool(r["active"]), "timestamp": r["timestamp"],
                "expiry_date": r["expiry_date"], "poster_type": r["poster_type"] or "customer"
            })
        brokers = []
        for row in conn.execute("SELECT * FROM brokers ORDER BY id"):
            r = dict(row)
            brokers.append({
                "id": r["id"], "user_id": r["user_id"], "name": r["name"],
                "username": r["username"], "phone": r["phone"], "address": r["address"],
                "active": bool(r["active"]), "approved": bool(r["approved"]),
                "timestamp": r["timestamp"], "sectors": json.loads(r.get("sectors") or "[]")
            })
        connection_requests = []
        for row in conn.execute("SELECT payload FROM connection_requests ORDER BY request_index"):
            try: connection_requests.append(json.loads(row["payload"]))
            except (TypeError, json.JSONDecodeError): continue
        pending_listing_broadcasts = {}
        for row in conn.execute("SELECT id,payload FROM pending_listing_broadcasts WHERE status='pending' ORDER BY id"):
            try: pending_listing_broadcasts[int(row["id"])] = json.loads(row["payload"])
            except (TypeError, ValueError, json.JSONDecodeError): continue
        last_listing_id = conn.execute("SELECT COALESCE(MAX(id),0) FROM pending_listing_broadcasts").fetchone()[0]
        next_listing_broadcast_id = int(last_listing_id or 0) + 1
        gmaps_share_requests = []
        tracking_stops = []
        for row in conn.execute("SELECT request_type,request_index,payload FROM gps_admin_requests ORDER BY request_type,request_index"):
            try:
                payload = json.loads(row["payload"])
                if row["request_type"] == "gmaps": gmaps_share_requests.append(payload)
                elif row["request_type"] == "stop": tracking_stops.append(payload)
            except (TypeError, json.JSONDecodeError): continue
        pending_gmaps_shares = []
        for row in conn.execute("SELECT id,payload FROM pending_gmaps_shares ORDER BY id"):
            try:
                payload = json.loads(row["payload"])
                payload["_db_id"] = int(row["id"])
                pending_gmaps_shares.append(payload)
            except (TypeError, json.JSONDecodeError): continue
        print(f"✅ Restored DB Data Successfully")
    finally: conn.close()

def db_save_user(user_id, name, username, owner_name=None, owner_phone=None):
    conn = _db_connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, name, username, owner_name, owner_phone)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            name = excluded.name, username = excluded.username,
            owner_name = COALESCE(excluded.owner_name, users.owner_name),
            owner_phone = COALESCE(excluded.owner_phone, users.owner_phone)
    """, (user_id, name, username, owner_name, owner_phone))
    conn.commit(); conn.close()

def get_total_users_count():
    conn = _db_connect(DB_PATH)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        return 7000 + cursor.fetchone()[0]
    finally: conn.close()
users, cargo_posts, truck_posts, brokers = {}, [], [], []
connection_requests, tracking_stops, gmaps_share_requests = [], [], []
pending_gmaps_shares, pending_deletes = [], []
pending_listing_broadcasts = {}
next_listing_broadcast_id = 1

(CARGO_FROM, CARGO_TO, CARGO_TYPE, CARGO_VEHICLE, CARGO_SIZE, CARGO_WEIGHT, CARGO_CALENDAR, CARGO_DATE, CARGO_PHONE, CARGO_PRICE) = range(10)
(TRUCK_TYPE, TRUCK_PLATE, TRUCK_CAPACITY, TRUCK_ROUTE, TRUCK_ADDRESS, TRUCK_DRIVER, TRUCK_PHONE) = range(10, 17)
OWNER_NAME, OWNER_PHONE = range(17, 19)
(CONNECT_NAME, CONNECT_PHONE, CONNECT_TYPE) = range(19, 22)
(BROKER_NAME, BROKER_PHONE, BROKER_ADDRESS) = range(22, 25)
(TRACK_SHARE_ADDRESS, TRACK_SHARE_NAME, TRACK_SHARE_PHONE, TRACK_STOP_NAME, TRACK_STOP_PHONE) = range(25, 30)
START_PHONE = 34
RENTAL_TARGET_NAME, RENTAL_TARGET_PHONE = 35, 36
SUPER_BROADCAST_TARGET, SUPER_BROADCAST_TEXT, SUPER_BROADCAST_PHOTO = range(37, 40)
VEHICLE_LIST_TYPE, VEHICLE_LIST_LOCATION, VEHICLE_LIST_DETAILS, VEHICLE_LIST_PRICE, VEHICLE_LIST_PHOTOS = range(40, 45)
VEHICLE_ADMIN_EDIT, MICRO_CATEGORY, MICRO_DETAILS, MICRO_PLATE, MICRO_PHOTOS, MICRO_OWNER, MICRO_PHONE = range(45, 52)
PHONE_CONFIRM, VEHICLE_BUYER_NAME, VEHICLE_LIST_PLATE, VEHICLE_LIST_OWNER, BROKER_SECTORS = range(52, 57)
MACH_TYPE, MACH_MODE, MACH_LOCATION, MACH_DETAILS, MACH_PRICE, MACH_PHONE, MACH_PHOTOS, MACH_SEARCH = range(57, 65)

def get_admin_pii_visibility(user_id):
    try:
        conn = _db_connect(DB_PATH)
        try:
            row = conn.execute("SELECT show_private FROM admin_preferences WHERE user_id=?", (int(user_id),)).fetchone()
            return bool(row[0]) if row else True
        finally: conn.close()
    except: return True

def set_admin_pii_visibility(user_id, visible):
    conn = _db_connect(DB_PATH)
    try:
        conn.execute("INSERT INTO admin_preferences (user_id,show_private) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET show_private=excluded.show_private", (int(user_id), 1 if visible else 0))
        conn.commit()
    finally: conn.close()

def main_menu(user_id=None):
    if user_id and is_admin(user_id):
        keyboard = [
            ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
            ["🚛 የጭነት መኪና ማስመዝገብ", "🚛 የጭነት መኪና መፈለግ"],
            ["🚗 የሽያጭና/ኪራይ መኪና መለጠፍ", "🔎 የሽያጭና/ኪራይ መኪናዎችን ፈልግ"],
            ["🚜 የኮንስትራክሽን ማሽነሪ ሽያጭ/ኪራይ", "🔎 ማሽነሪ ፈልግ"],
            ["👤 የኔ መረጃ", "👥 ጓደኛ ጋብዝ"],
            ["🚛 የጭነት መኪና ክትትል", "🚙 የኪራይ መኪና ክትትል"],
            ["📚 የክትትል መዝገብ"],
            ["🤝 የግንኙነት ጥያቄዎች"],
            ["📍 የGPS ጥያቄዎች"],
            ["📋 የጭነት ደላሎች መዝገብ"],
            ["🗂️ የኪራይ/ሽያጭ መዝገብ"],
            ["📡 የሚዲያ መረጃ"],
        ]
        if is_super_admin(user_id):
            keyboard.append(["📢 መልእክት ለደንበኞች ለመላክ"])
        keyboard.append(["📞 Support", "ℹ️ About"])
    else:
        keyboard = [
            ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
            ["🚛 የጭነት መኪና ማስመዝገብ", "🚛 የጭነት መኪና መፈለግ"],
            ["🚗 የሽያጭና/ኪራይ መኪና መለጠፍ", "🔎 የሽያጭና/ኪራይ መኪናዎችን ፈልግ"],
            ["🚜 የኮንስትራክሽን ማሽነሪ ሽያጭ/ኪራይ", "🔎 ማሽነሪ ፈልግ"],
            ["👤 የኔ መረጃ", "👥 ጓደኛ ጋብዝ"],
            ["🚛 የጭነት መኪና ክትትል", "🚙 የኪራይ መኪና ክትትል"],
            ["📨 ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ"],
            ["🤝 የንግድ አጋር / ደላላ ይመዝገቡ"],
            ["💳 የአገልግሎት ክፍያ ለመፈጸም"],
            ["📞 Support", "ℹ️ About"],
        ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def size_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🟢 ሙሉ ጭነት 100%", callback_data="size_100")], [InlineKeyboardButton("🟡 ግማሽ ጭነት 50%", callback_data="size_50")], [InlineKeyboardButton("🟠 እሩብ ጭነት 25%", callback_data="size_25")]])

def vehicle_keyboard(prefix="vehicle"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🚛 ተሳቢ", callback_data=f"{prefix}_ተሳቢ")], [InlineKeyboardButton("🚛 ካሶኒ", callback_data=f"{prefix}_ካሶኒ")], [InlineKeyboardButton("🚛 ኦባማ", callback_data=f"{prefix}_ኦባማ")], [InlineKeyboardButton("🚛 Isuzu", callback_data=f"{prefix}_isuzu")], [InlineKeyboardButton("✍️ ሌላ", callback_data=f"{prefix}_other")]])

def calendar_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🇪🇹 የኢትዮጵያ ካላንደር (EC)", callback_data="cal_et")]])

def phone_request_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("📱 ስልኬን አጋራ", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)

def is_user_registered(user_id):
    if user_id in users and users[user_id].get("owner", {}).get("phone"): return True
    conn = _db_connect(DB_PATH)
    try:
        row = conn.execute("SELECT owner_phone FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row and row[0]: return True
    finally: conn.close()
    return False

def get_registered_phone(user_id):
    user = users.get(user_id, {})
    phone = user.get("owner", {}).get("phone", "")
    if not phone:
        conn = _db_connect(DB_PATH)
        try:
            row = conn.execute("SELECT owner_phone FROM users WHERE user_id = ?", (int(user_id),)).fetchone()
            if row: phone = row[0]
        finally: conn.close()
    return phone

def validate_phone(phone):
    phone = phone.strip().replace(" ", "")
    if phone.startswith("+251"): phone = "0" + phone[4:]
    elif phone.startswith("251"): phone = "0" + phone[3:]
    if not (phone.startswith("09") or phone.startswith("07")): return False, "ስልክ ቁጥሩ በ09 ወይም በ07 መጀመር አለበት።"
    if len(phone) != 10: return False, "ስልክ ቁጥሩ 10 ዲጂት መሆን አለበት።"
    return True, phone

def validate_plate(plate):
    if not plate.strip().isdigit(): return False, "ታርጋ ቁጥር ብቻ መሆን አለበት።"
    return True, ""

def validate_weight(weight):
    if not any(u in weight for u in ["ቶን", "ቢያጆ", "ኩንታል"]): return False, "ክብደቱን በቶን፣ በቢያጆ ወይም በኩንታል ይግለጹ።"
    return True, ""

def is_super_admin(user_id): return bool(SUPER_ADMIN_USER_ID) and str(user_id) == str(SUPER_ADMIN_USER_ID)
def is_admin(user_id): return is_super_admin(user_id) or (bool(ADMIN_USER_ID) and str(user_id) == str(ADMIN_USER_ID))
def get_admin_ids():
    ids = []
    if SUPER_ADMIN_USER_ID: ids.append(int(SUPER_ADMIN_USER_ID))
    if ADMIN_USER_ID and str(ADMIN_USER_ID) != str(SUPER_ADMIN_USER_ID): ids.append(int(ADMIN_USER_ID))
    return ids

def is_broker(user_id):
    for b in brokers:
        if b.get("user_id") == user_id and b.get("approved") and b.get("active"): return True
    return False
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    context.user_data.clear()
    user = update.effective_user
    try: db_save_user(user.id, user.full_name, user.username or "")
    except Exception: pass
    
    if args and args[0].startswith("ref_"):
        try:
            referrer_id = int(args[0].split("_", 1)[1])
            if db_record_referral(referrer_id, user.id):
                try: await context.bot.send_message(chat_id=referrer_id, text=f"🎉 አዲስ ተጠቃሚ በግብዣ ሊንክህ TANA CARGO ተቀላቅሏል።\nጠቅላላ ግብዣዎች፦ {db_referral_count(referrer_id)}")
                except Exception: pass
        except (ValueError, IndexError): pass

    if args and args[0].startswith("rent_"):
        try:
            share_token = args[0].split("_", 1)[1]
            return await handle_rental_deeplink(update, context, share_token)
        except (ValueError, IndexError):
            await update.message.reply_text("የክትትል ሊንኩ ትክክል አይደለም።")
            return ConversationHandler.END
    
    if is_user_registered(user.id):
        users.setdefault(user.id, {"name": user.full_name, "username": user.username or "", "id": user.id})
        try: db_save_user(user.id, user.full_name, user.username or "")
        except Exception: pass
        total_users = get_total_users_count()
        try:
            await update.message.reply_photo(photo=START_IMAGE_ID, caption=f"👋 **እንኳን ደህና መጡ ወደ TANA CARGO!**\n\n👥 **እስካሁን የተቀላቀሉ ተጠቃሚዎች፦** {total_users:,}\n\n👇 ከታች ካለው ምናሌ የፈለጉትን ይምረጡ፦", reply_markup=main_menu(user.id), parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(f"👋 **እንኳን ደህና መጡ ወደ TANA CARGO!**\n\n👥 **እስካሁን የተቀላቀሉ ተጠቃሚዎች፦** {total_users:,}\n\n👇 ከታች ካለው ምናሌ የፈለጉትን ይምረጡ፦", reply_markup=main_menu(user.id), parse_mode="Markdown")
        return ConversationHandler.END
    
    try:
        await update.message.reply_photo(photo=START_IMAGE_ID, caption="🚛 **TANA CARGO | ጣና ጭነት** 📦\n━━━━━━━━━━━━━━━━━━━━\n\n👋 **እንኳን ወደ TANA CARGO ጣና ጭነት በደህና መጡ!**\n\nSTART 👈 ይጫኑ፤ ስልክ ያጋሩ። 📱\n\n👇 ከታች ያለውን '📱 ስልኬን አጋራ' ይጫኑ።", reply_markup=phone_request_keyboard(), parse_mode="Markdown")
    except Exception:
        await update.message.reply_text("START 👈 ይጫኑ፤ ስልክ ያጋሩ።", reply_markup=phone_request_keyboard())
    return START_PHONE

async def start_phone_input(update, context):
    if not update.message.contact:
        await update.message.reply_text("❌ እባክዎ '📱 ስልኬን አጋራ' የሚለውን ቁልፍ ይጫኑ።", reply_markup=phone_request_keyboard())
        return START_PHONE
    phone = update.message.contact.phone_number
    is_valid, result = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {result}\nእባክዎ ትክክለኛ የራስዎን ስልክ ቁጥር ያጋሩ።", reply_markup=phone_request_keyboard())
        return START_PHONE
    
    user = update.effective_user
    users.setdefault(user.id, {"name": user.full_name, "username": user.username or "", "id": user.id})
    users[user.id]["owner"] = {"phone": result, "name": user.full_name}
    try:
        db_save_user(user.id, user.full_name, user.username or "", owner_name=user.full_name, owner_phone=result)
        if not is_user_registered(user.id): raise RuntimeError("User registration failed.")
    except Exception:
        await update.message.reply_text("❌ ምዝገባዎን ማስቀመጥ አልተቻለም። እባክዎ /start በድጋሚ ይጫኑ።")
        return START_PHONE
        
    await update.message.reply_text("እናመሰግናለን ምዝገባዎን አጠናቀዋል ከታች ካሉት ምናሌዎች የፈለጉትን መጠቀም ይችላሉ።", reply_markup=main_menu(user.id))
    return ConversationHandler.END

async def super_broadcast_start(update, context):
    if not is_super_admin(update.effective_user.id): return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("ለሁሉም ደንበኞች", callback_data="broadcast_all")], [InlineKeyboardButton("ለደላሎች ብቻ", callback_data="broadcast_brokers")], [InlineKeyboardButton("ለአንድ ደንበኛ", callback_data="broadcast_one")]])
    await update.message.reply_text("📣 መልዕክቱን ለማን መላክ ይፈልጋሉ?", reply_markup=kb)
    return SUPER_BROADCAST_TARGET

async def broadcast_target_choice(update, context):
    q = update.callback_query; await q.answer()
    context.user_data["broadcast_mode"] = q.data.replace("broadcast_", "")
    if q.data == "broadcast_one":
        await q.message.reply_text("📝 የደንበኛውን **Telegram Chat ID** ያስገቡ፦")
        return SUPER_BROADCAST_TARGET
    await q.message.reply_text("📝 የሚያጋሩትን **መልእክት (ጽሁፍ)** ይጻፉ።")
    return SUPER_BROADCAST_TEXT

async def broadcast_text_received(update, context):
    text = update.message.text.strip()
    if context.user_data.get("broadcast_mode") == "one" and not context.user_data.get("single_target_id"):
        context.user_data["single_target_id"] = text
        await update.message.reply_text("📝 ለዚህ ደንበኛ የሚላከውን **መልእክት (ጽሁፍ)** ይጻፉ።")
        return SUPER_BROADCAST_TEXT
    context.user_data["broadcast_msg_text"] = text
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("የለኝም (ፎቶ ሳይኖር ላክ)", callback_data="broadcast_photo_none")]])
    await update.message.reply_text("📸 ከመልእክቱ ጋር 1 ወይም 2 ፎቶ ማያያዝ ይፈልጋሉ?\nፎቶዎቹን ይላኩ ወይም ከታች ያለውን ይጫኑ።", reply_markup=kb)
    return SUPER_BROADCAST_PHOTO

async def broadcast_photo_received(update, context):
    photos = context.user_data.setdefault("broadcast_photos", [])
    if len(photos) >= 2:
        await update.message.reply_text("ከ2 ፎቶ በላይ መላክ አይቻልም።")
        return SUPER_BROADCAST_PHOTO
    photos.append(update.message.photo[-1].file_id)
    if len(photos) == 1:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("ፎቶ ጨርሻለሁ (አሁን ላክ)", callback_data="broadcast_photo_done")]])
        await update.message.reply_text("✅ 1ኛ ፎቶ ተቀብለናል። 2ኛ ፎቶ ይላኩ ወይም ከታች ያለውን ይጫኑ።", reply_markup=kb)
        return SUPER_BROADCAST_PHOTO
    return await execute_broadcast(update, context)

async def broadcast_photo_callback(update, context):
    q = update.callback_query; await q.answer()
    return await execute_broadcast(update, context)

async def execute_broadcast(update, context):
    text = context.user_data.get("broadcast_msg_text", "")
    photos = context.user_data.get("broadcast_photos", [])
    target = context.user_data.get("broadcast_mode")
    final_text = f"📢 **ከ TANA CARGO & LOGISTICS 🚛 የተላከ**\n\n{text}\n\n━━━━━━━━━━━━━━━━━━━━\n📌 እባክዎን የሚፈልግ ደንበኛ እጆት ላይ ካለ\n📞 በ {SUPPORT_PHONE} ይደውሉ\nወይም በቴሌግራም @{SUPPORT_USERNAME} ላይ የፃፉልን የሚል ይኖረዋል።"
    
    targets = []
    if target == "all":
        conn = _db_connect(DB_PATH); targets = [r[0] for r in conn.execute("SELECT user_id FROM users").fetchall()]; conn.close()
    elif target == "brokers":
        targets = [b["user_id"] for b in brokers if b.get("approved") and b.get("active")]
    else:
        try: targets = [int(context.user_data.get("single_target_id"))]
        except: targets = []
            
    sent, failed = 0, 0
    for uid in targets:
        try:
            if photos:
                if len(photos) == 1: await context.bot.send_photo(uid, photo=photos[0], caption=final_text, parse_mode="Markdown")
                else: await context.bot.send_media_group(uid, media=[InputMediaPhoto(photos[0], caption=final_text, parse_mode="Markdown"), InputMediaPhoto(photos[1])])
            else:
                await context.bot.send_message(uid, final_text, parse_mode="Markdown")
            sent += 1; await asyncio.sleep(0.05)
        except: failed += 1
    reply_target = update.message if hasattr(update.message, 'reply_text') else update.callback_query.message
    await reply_target.reply_text(f"✅ መልእክቱ ተልኳል።\n📨 የደረሰው: {sent}\n⚠️ ያልደረሰው: {failed}", reply_markup=main_menu(update.effective_user.id))
    context.user_data.pop("broadcast_msg_text", None); context.user_data.pop("broadcast_photos", None); context.user_data.pop("broadcast_mode", None); context.user_data.pop("single_target_id", None)
    return ConversationHandler.END

# CARGO & TRUCK POSTING
async def cargo_start(update, context):
    context.user_data.clear(); context.user_data["cargo"] = {}
    await update.message.reply_text("🚚 **ጭነት መለጠፍ**\n\n1️⃣ ጭነቱ የሚነሳበትን ቦታ ይጻፉ።", reply_markup=main_menu(update.effective_user.id))
    return CARGO_FROM
async def cargo_from(update, context):
    context.user_data["cargo"]["from"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የሚደርስበትን ቦታ ይጻፉ።")
    return CARGO_TO
async def cargo_to(update, context):
    context.user_data["cargo"]["to"] = update.message.text.strip()
    await update.message.reply_text("3️⃣ የጭነቱን አይነት ይጻፉ።")
    return CARGO_TYPE
async def cargo_type(update, context):
    context.user_data["cargo"]["type"] = update.message.text.strip()
    await update.message.reply_text("4️⃣ የሚፈልጉትን የመኪና አይነት ይምረጡ።", reply_markup=vehicle_keyboard("cargo_vehicle"))
    return CARGO_VEHICLE
async def cargo_vehicle(update, context):
    query = update.callback_query; await query.answer()
    value = query.data.replace("cargo_vehicle_", "")
    if value == "other":
        await query.edit_message_text("✍️ የሚፈልጉትን የመኪና አይነት ይጻፉ።"); context.user_data["cargo"]["vehicle_waiting"] = True; return CARGO_VEHICLE
    context.user_data["cargo"]["vehicle"] = value
    await query.edit_message_text(f"🚛 የተመረጠው መኪና፦ {value}\n\n5️⃣ የጭነቱን መጠን ይምረጡ።", reply_markup=size_keyboard())
    return CARGO_SIZE
async def cargo_vehicle_text(update, context):
    cargo = context.user_data["cargo"]
    if not cargo.get("vehicle_waiting"): return CARGO_VEHICLE
    cargo["vehicle"] = update.message.text.strip(); cargo["vehicle_waiting"] = False
    await update.message.reply_text(f"🚛 የተፈለገው መኪና፦ {cargo['vehicle']}\n\n5️⃣ የጭነቱን መጠን ይምረጡ።", reply_markup=size_keyboard())
    return CARGO_SIZE
async def cargo_size(update, context):
    query = update.callback_query; await query.answer()
    sizes = {"size_100": (100, "ሙሉ ጭነት"), "size_50": (50, "ግማሽ ጭነት"), "size_25": (25, "እሩብ ጭነት")}
    if query.data not in sizes: return CARGO_SIZE
    size, size_name = sizes[query.data]
    context.user_data["cargo"]["size"] = size; context.user_data["cargo"]["size_name"] = size_name
    await query.edit_message_text(f"📦 የተመረጠው፦ {size_name}\n\n6️⃣ የጭነቱን ክብደት ይግለጹ።")
    return CARGO_WEIGHT
async def cargo_weight(update, context):
    weight = update.message.text.strip()
    if not any(u in weight for u in ["ቶን", "ቢያጆ", "ኩንታል"]):
        await update.message.reply_text("❌ ክብደቱን በቶን፣ በቢያጆ ወይም በኩንታል ይግለጹ።"); return CARGO_WEIGHT
    context.user_data["cargo"]["weight"] = weight
    await update.message.reply_text("7️⃣ የሚጫንበትን ቀን ይጻፉ።", reply_markup=calendar_keyboard())
    return CARGO_CALENDAR
async def cargo_calendar(update, context):
    query = update.callback_query; await query.answer()
    context.user_data["cargo"]["calendar"] = "ET"
    await query.edit_message_text("ቀኑን በዚህ ቅርጸት ይጻፉ፦ ቀን/ወር/ዓመት\nምሳሌ፦ 26/1/2019")
    return CARGO_DATE
async def cargo_date(update, context):
    date_text = update.message.text.strip(); parts, err = parse_flexible_date(date_text)
    if err: await update.message.reply_text(f"❌ {err}"); return CARGO_DATE
    et_date, gc_date, err = convert_calendar(parts, "ET")
    if err: await update.message.reply_text(f"❌ {err}"); return CARGO_DATE
    cargo = context.user_data.setdefault("cargo", {}); cargo["date_et"], cargo["date_gc"] = et_date, gc_date
    cargo["expiry_date"] = (datetime.now() + timedelta(hours=CARGO_EXPIRY_HOURS)).isoformat()
    reg_phone = get_registered_phone(update.effective_user.id)
    await update.message.reply_text(f"📅 ቀን: {et_date}\n\n8️⃣ 📞 የተመዘገበ ስልክ ቁጥርዎ፦ `{reg_phone}`\nከላይ ያለውን ለመጠቀም ✅ ይጫኑ ወይም አዲስ ስልክ ቁጥር ይጻፉ።", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ ትክክል ነው", callback_data="phone_ok"), InlineKeyboardButton("✍️ ሌላ ስልክ", callback_data="phone_new")]]), parse_mode="Markdown")
    return CARGO_PHONE
async def cargo_phone(update, context):
    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "phone_ok":
            phone = get_registered_phone(update.effective_user.id); await update.callback_query.message.delete()
        else:
            await update.callback_query.edit_message_text("✍️ አዲስ ስልክ ቁጥር ይጻፉ፦"); return CARGO_PHONE
    else:
        is_valid, phone = validate_phone(update.message.text.strip())
        if not is_valid: await update.message.reply_text(f"❌ {phone}"); return CARGO_PHONE
    context.user_data["cargo"]["phone"] = phone
    await update.message.reply_text("9️⃣ የመጫኛ ዋጋ ይጻፉ።", reply_markup=main_menu(update.effective_user.id))
    return CARGO_PRICE
async def cargo_price(update, context):
    raw = update.message.text.strip().split("\n")[0].strip()
    if raw in ["በስምምነት", "በድርድር"]: price, price_status, price_display = None, "agreement", "በስምምነት"
    else:
        try: price = float(raw.replace(",", "").replace("ብር", "").strip()); price_status, price_display = "fixed", f"{price:,.2f} ብር"
        except: await update.message.reply_text("❌ ዋጋውን በቁጥር ወይም 'በስምምነት' ይጻፉ።"); return CARGO_PRICE
    cargo = context.user_data["cargo"]
    cargo.update({"price": price, "price_status": price_status, "price_display": price_display, "user_id": update.effective_user.id, "active": True, "timestamp": datetime.now().isoformat(), "poster_type": "broker" if is_broker(update.effective_user.id) else "customer"})
    cargo_posts.append(cargo.copy())
    conn = _db_connect(DB_PATH)
    conn.execute("INSERT INTO cargo_posts (user_id, from_location, to_location, cargo_type, vehicle, size, size_name, weight, date_et, date_gc, phone, price, price_display, price_status, timestamp, poster_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (cargo["user_id"], cargo["from"], cargo["to"], cargo["type"], cargo["vehicle"], cargo["size"], cargo["size_name"], cargo["weight"], cargo["date_et"], cargo["date_gc"], cargo["phone"], cargo["price"], cargo["price_display"], cargo["price_status"], cargo["timestamp"], cargo["poster_type"]))
    conn.commit(); conn.close()
    await update.message.reply_text("✅ **ጭነትዎ በትክክል ተመዝግቧል!**", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END
async def truck_start(update, context):
    context.user_data.clear(); context.user_data["truck"] = {}
    await update.message.reply_text("🚛 **የጭነት መኪና ማስመዝገብ**\n\n1️⃣ የመኪናውን አይነት ይጻፉ።", reply_markup=main_menu(update.effective_user.id))
    return TRUCK_TYPE
async def truck_type(update, context):
    context.user_data["truck"]["type"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የመኪናውን ታርጋ ይጻፉ።")
    return TRUCK_PLATE
async def truck_plate(update, context):
    context.user_data["truck"]["plate"] = update.message.text.strip()
    await update.message.reply_text("3️⃣ የመጫን አቅምና የጭነት አይነት ይግለጹ።")
    return TRUCK_CAPACITY
async def truck_capacity(update, context):
    context.user_data["truck"]["capacity"] = update.message.text.strip()
    await update.message.reply_text("4️⃣ የመንገድ ዝርዝር/አድራሻ ይጻፉ።")
    return TRUCK_ADDRESS
async def truck_address(update, context):
    context.user_data["truck"]["address"] = update.message.text.strip()
    context.user_data["truck"]["route"] = "—"
    await update.message.reply_text("5️⃣ የሹፌሩን ስም ይጻፉ።")
    return TRUCK_DRIVER
async def truck_driver(update, context):
    context.user_data["truck"]["driver"] = update.message.text.strip()
    reg_phone = get_registered_phone(update.effective_user.id)
    await update.message.reply_text(f"6️⃣ 📞 የተመዘገበ ስልክ ቁጥርዎ፦ `{reg_phone}`\nከላይ ያለውን ለመጠቀም ✅ ይጫኑ ወይም አዲስ ስልክ ቁጥር ይጻፉ።", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ ትክክል ነው", callback_data="phone_ok"), InlineKeyboardButton("✍️ ሌላ ስልክ", callback_data="phone_new")]]), parse_mode="Markdown")
    return TRUCK_PHONE
async def truck_phone(update, context):
    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "phone_ok":
            phone = get_registered_phone(update.effective_user.id); await update.callback_query.message.delete()
        else:
            await update.callback_query.edit_message_text("✍️ አዲስ ስልክ ቁጥር ይጻፉ፦"); return TRUCK_PHONE
    else:
        is_valid, phone = validate_phone(update.message.text.strip())
        if not is_valid: await update.message.reply_text(f"❌ {phone}"); return TRUCK_PHONE
    truck = context.user_data["truck"]
    truck.update({"phone": phone, "user_id": update.effective_user.id, "active": True, "timestamp": datetime.now().isoformat(), "poster_type": "broker" if is_broker(update.effective_user.id) else "customer"})
    truck_posts.append(truck.copy())
    conn = _db_connect(DB_PATH)
    conn.execute("INSERT INTO truck_posts (user_id, truck_type, plate, capacity, route, address, driver, phone, timestamp, poster_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (truck["user_id"], truck["type"], truck["plate"], truck["capacity"], truck["route"], truck["address"], truck["driver"], truck["phone"], truck["timestamp"], truck["poster_type"]))
    conn.commit(); conn.close()
    await update.message.reply_text("✅ **መኪናዎ በትክክል ተመዝግቧል!**", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END

# CAR LISTING
async def car_post_start(update, context):
    context.user_data.clear()
    await update.message.reply_text("🚗 **የሽያጭና/ኪራይ መኪና መለጠፍ**\n\nየሚለጥፉትን ዓይነት ይምረጡ፦", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚗 የኪራይ መኪና", callback_data="action_car_rent")], [InlineKeyboardButton("🚙 የሽያጭ መኪና", callback_data="action_car_sale")]]))
    return VEHICLE_LIST_TYPE
async def vehicle_listing_type_action(update, context):
    query = update.callback_query; await query.answer()
    cat = "rental" if query.data == "action_car_rent" else "sale"
    context.user_data["vehicle_listing"] = {"category": cat, "photos": []}
    await query.edit_message_text("1️⃣ የመኪናውን አይነት/ሞዴል ይጻፉ።")
    return VEHICLE_LIST_LOCATION
async def vehicle_listing_location(update, context):
    context.user_data["vehicle_listing"]["vehicle_type"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የመኪናውን ታርጋ ቁጥር ይጻፉ።")
    return VEHICLE_LIST_PLATE
async def vehicle_listing_plate(update, context):
    context.user_data["vehicle_listing"]["plate"] = update.message.text.strip()
    await update.message.reply_text("3️⃣ መኪናው የሚገኝበትን ከተማ/ቦታ ይጻፉ።")
    return VEHICLE_LIST_DETAILS
async def vehicle_listing_details(update, context):
    context.user_data["vehicle_listing"]["details"] = update.message.text.strip()
    await update.message.reply_text("4️⃣ ስለ መኪናው ተጨማሪ መረጃ ይጻፉ።")
    return VEHICLE_LIST_PRICE
async def vehicle_listing_price(update, context):
    context.user_data["vehicle_listing"]["price"] = update.message.text.strip()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📷 ፎቶ የለኝም", callback_data="vehicle_photo_none")]])
    await update.message.reply_text("5️⃣ የመኪናውን 1 ወይም 2 ፎቶ ይላኩ።", reply_markup=kb)
    return VEHICLE_LIST_PHOTOS
async def vehicle_listing_photo(update, context):
    photos = context.user_data["vehicle_listing"].setdefault("photos", [])
    if not update.message.photo: return VEHICLE_LIST_PHOTOS
    photos.append(update.message.photo[-1].file_id)
    if len(photos) == 1:
        await update.message.reply_text("✅ 1ኛ ፎቶ ተቀብለናል። 2ኛ ፎቶ ይላኩ ወይም 'ፎቶ ጨርሻለሁ' ይጫኑ።", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ፎቶ ጨርሻለሁ", callback_data="vehicle_photo_done")]]))
        return VEHICLE_LIST_PHOTOS
    return await vehicle_listing_ask_price(update, context)
async def vehicle_listing_photo_callback(update, context):
    await update.callback_query.answer()
    return await vehicle_listing_ask_price(update, context)
async def vehicle_listing_ask_price(update, context):
    await update.effective_message.reply_text("6️⃣ የተሽከርካሪው ባለቤት ሙሉ ስም ይጻፉ።")
    return VEHICLE_LIST_OWNER
async def vehicle_listing_owner(update, context):
    context.user_data["vehicle_listing"]["owner_name"] = update.message.text.strip()
    reg_phone = get_registered_phone(update.effective_user.id)
    await update.message.reply_text(f"7️⃣ 📞 የተመዘገበ ስልክ ቁጥርዎ፦ `{reg_phone}`\n✅ ይጫኑ ወይም አዲስ ይጻፉ።", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ ትክክል ነው", callback_data="phone_ok"), InlineKeyboardButton("✍️ ሌላ ስልክ", callback_data="phone_new")]]))
    return PHONE_CONFIRM

async def find_cargo(update, context):
    active_cargos = [c for c in cargo_posts if c.get("active", True)]
    if not active_cargos: return await update.message.reply_text("📦 በአሁኑ ጊዜ ተስማሚ ጭነት የለም።", reply_markup=main_menu(update.effective_user.id))
    for i, cargo in enumerate(active_cargos):
        real_index = cargo_posts.index(cargo)
        admin_info = f"👤 ስም: {users.get(cargo['user_id'], {}).get('name', 'N/A')}\n📞 ስልክ: {cargo.get('phone', 'N/A')}\n" if is_admin(update.effective_user.id) and get_admin_pii_visibility(update.effective_user.id) else ""
        msg = f"📦 **ጭነት {i+1:03d}**\n📍 መነሻ፦ {cargo['from']}\n📍 መድረሻ፦ {cargo['to']}\n📦 አይነት፦ {cargo['type']}\n💰 ዋጋ፦ {cargo.get('price_display', 'በስምምነት')}\n{admin_info}"
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"የመረጡት ጭነት {i+1:03d} ከሆነ እዚጋ ያጫኑ 👈", callback_data=f"connect_{real_index}")]]))

async def find_truck(update, context):
    active_trucks = [t for t in truck_posts if t.get("active", True)]
    if not active_trucks: return await update.message.reply_text("🚛 በአሁኑ ጊዜ ተስማሚ የጭነት መኪና የለም።", reply_markup=main_menu(update.effective_user.id))
    for i, truck in enumerate(active_trucks):
        real_index = truck_posts.index(truck)
        admin_info = f"👤 ስም: {users.get(truck['user_id'], {}).get('name', 'N/A')}\n📞 ስልክ: {truck.get('phone', 'N/A')}\n🔢 ታርጋ: {truck.get('plate', 'N/A')}\n" if is_admin(update.effective_user.id) and get_admin_pii_visibility(update.effective_user.id) else ""
        msg = f"🚛 **መኪና {i+1:03d}**\n🔹 አይነት፦ {truck['type']}\n⚖️ አቅም፦ {truck['capacity']}\n📍 አድራሻ፦ {truck['address']}\n{admin_info}"
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"የመረጡት መኪና {i+1:03d} ከሆነ ይጫኑ 👈", callback_data=f"truckconnect_{real_index}")]]))

async def find_car_start(update, context):
    await update.message.reply_text("🔎 **የሽያጭና/ኪራይ መኪናዎችን ፈልግ**\n\nየሚፈልጉትን ዓይነት ይምረጡ፦", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚗 የኪራይ መኪና ፈልግ", callback_data="find_car_rent")], [InlineKeyboardButton("🚙 የሽያጭ መኪና ፈልግ", callback_data="find_car_sale")]]))

async def find_vehicle_listings(update, context, category=None):
    if update.callback_query:
        query = update.callback_query; await query.answer()
        category = query.data.split("_")[-1]; await query.message.delete()
    cat_map = {"rent": "rental", "sale": "sale"}
    db_cat = cat_map.get(category, category)
    conn = _db_connect(DB_PATH); conn.row_factory = dict_row
    try: records = [dict(r) for r in conn.execute("SELECT * FROM vehicle_listings WHERE category=? AND active=1 ORDER BY id DESC LIMIT 30", (db_cat,)).fetchall()]
    finally: conn.close()
    if not records: return await context.bot.send_message(update.effective_chat.id, f"🔎 አሁን የተመዘገበ የለም።", reply_markup=main_menu(update.effective_user.id))
    for rec in records:
        photos = json.loads(rec.get("photo_ids") or "[]")
        admin_info = f"👤 ባለቤት: {rec.get('owner_name', 'N/A')}\n📞 ስልክ: {rec.get('phone', 'N/A')}\n" if is_admin(update.effective_user.id) and get_admin_pii_visibility(update.effective_user.id) else ""
        text_msg = f"🚘 {rec['vehicle_type']}\n📍 {rec['location']}\n💰 {rec['price']}\n{admin_info}"
        buyer_kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"📩 ግንኙነት ጠይቅ", callback_data=f"vehiclebuyer_{rec['id']}")]])
        if photos: await context.bot.send_photo(update.effective_chat.id, photo=photos[0], caption=text_msg, reply_markup=buyer_kb)
        else: await context.bot.send_message(update.effective_chat.id, text_msg, reply_markup=buyer_kb)
async def connection_request(update, context):
    query = update.callback_query; await query.answer()
    index = int(query.data.split("_")[1])
    cargo = cargo_posts[index]
    if cargo["user_id"] == update.effective_user.id: return await query.answer("❌ የራስዎን ጭነት መጠየቅ አይችሉም።", show_alert=True)
    context.user_data["connect_info"] = {"type": "cargo", "item_index": index, "requester_id": update.effective_user.id}
    await query.edit_message_text("📝 **መረጃዎን ያስገቡ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።")
    return CONNECT_NAME

async def truck_connection_request(update, context):
    query = update.callback_query; await query.answer()
    index = int(query.data.split("_")[1])
    truck = truck_posts[index]
    if truck["user_id"] == update.effective_user.id: return await query.answer("❌ የራስዎን መኪና መጠየቅ አይችሉም።", show_alert=True)
    context.user_data["connect_info"] = {"type": "truck", "item_index": index, "requester_id": update.effective_user.id}
    await query.edit_message_text("📝 **መረጃዎን ያስገቡ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።")
    return CONNECT_NAME

async def connect_name(update, context):
    info = context.user_data.get("connect_info", {})
    info["name"] = update.message.text.strip(); context.user_data["connect_info"] = info
    reg_phone = get_registered_phone(update.effective_user.id)
    await update.message.reply_text(f"2️⃣ 📞 የተመዘገበ ስልክ ቁጥርዎ፦ `{reg_phone}`\n✅ ይጫኑ ወይም አዲስ ይጻፉ።", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ ትክክል ነው", callback_data="phone_ok"), InlineKeyboardButton("✍️ ሌላ ስልክ", callback_data="phone_new")]]))
    return CONNECT_PHONE

async def connect_phone(update, context):
    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "phone_ok":
            phone = get_registered_phone(update.effective_user.id); await update.callback_query.message.delete()
        else:
            await update.callback_query.edit_message_text("✍️ አዲስ ስልክ ቁጥር ይጻፉ፦"); return CONNECT_PHONE
    else:
        is_valid, phone = validate_phone(update.message.text.strip())
        if not is_valid: await update.message.reply_text(f"❌ {phone}"); return CONNECT_PHONE
    context.user_data["connect_info"]["phone"] = phone
    await update.message.reply_text("3️⃣ **የሚገኙበትን አድራሻ** ይጻፉ።")
    return CONNECT_TYPE

async def connect_type(update, context):
    info = context.user_data.get("connect_info", {})
    info["requester_address"] = update.message.text.strip()
    requester = update.effective_user
    req_data = {"type": info["type"], "item_index": info["item_index"], "requester_id": requester.id, "requester_name": info["name"], "requester_phone": info["phone"], "requester_address": info["requester_address"], "status": "admin_confirming", "created_at": datetime.now().isoformat()}
    connection_requests.append(req_data)
    await update.message.reply_text("✅ እናመሰግናለን! አድሚን አጽድቆ መረጃውን ይልክልዎታል።", reply_markup=main_menu(requester.id))
    for admin_id in get_admin_ids():
        try: await context.bot.send_message(admin_id, f"🔔 **አዲስ ግንኙነት ጥያቄ!**\n👤 ስም: {info['name']}\n📞 ስልክ: {info['phone']}")
        except: pass
    return ConversationHandler.END

async def broker_start(update, context):
    context.user_data["broker"] = {"sectors": []}
    await update.message.reply_text("🤝 **የንግድ አጋር / ደላላ ምዝገባ**\n\nየሚሰሩባቸውን ዘርፎች ይምረጡ።", reply_markup=broker_sector_keyboard([]))
    return BROKER_SECTORS

def broker_sector_keyboard(selected):
    choices = [("freight", "🚛 የጭነት ደላላ"), ("machinery", "🚜 የኮንስትራክሽን"), ("cars", "🚗 የመኪና ሽያጭ / ኪራይ"), ("other", "🤝 ሌሎች የንግድ አጋሮች"), ("all", "🌐 ሁሉንም")]
    rows = [[InlineKeyboardButton(("✅ " if key in selected else "▫️ ") + label, callback_data=f"broker_sector_{key}")] for key, label in choices]
    rows.append([InlineKeyboardButton("✅ ጨርስ", callback_data="broker_sector_done")])
    return InlineKeyboardMarkup(rows)

async def broker_sector_callback(update, context):
    query = update.callback_query; selected = context.user_data.setdefault("broker", {}).setdefault("sectors", [])
    key = query.data.replace("broker_sector_", "", 1)
    mapping = {"freight": "የጭነት ደላላ", "machinery": "የኮንስትራክሽን", "cars": "የመኪና ሽያጭ / ኪራይ", "other": "ሌሎች የንግድ አጋሮች"}
    if key == "done":
        if not selected: return await query.answer("ቢያንስ አንድ ዘርፍ ይምረጡ።", show_alert=True)
        await query.answer(); await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("1️⃣ ሙሉ ስምዎን ይጻፉ።"); return BROKER_NAME
    await query.answer()
    if key == "all": selected[:] = ["ሁሉንም"] if "ሁሉንም" not in selected else []
    elif key in mapping:
        if "ሁሉንም" in selected: selected.remove("ሁሉንም")
        label = mapping[key]
        if label in selected: selected.remove(label)
        else: selected.append(label)
    await query.edit_message_reply_markup(reply_markup=broker_sector_keyboard(selected))
    return BROKER_SECTORS

async def broker_name(update, context):
    context.user_data["broker"]["name"] = update.message.text.strip()
    reg_phone = get_registered_phone(update.effective_user.id)
    await update.message.reply_text(f"2️⃣ 📞 የተመዘገበ ስልክ ቁጥርዎ፦ `{reg_phone}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ ትክክል ነው", callback_data="phone_ok"), InlineKeyboardButton("✍️ ሌላ ስልክ", callback_data="phone_new")]]))
    return PHONE_CONFIRM

async def broker_phone(update, context):
    if update.callback_query:
        await update.callback_query.answer()
        if update.callback_query.data == "phone_ok":
            phone = get_registered_phone(update.effective_user.id); await update.callback_query.message.delete()
        else:
            await update.callback_query.edit_message_text("✍️ አዲስ ስልክ ቁጥር ይጻፉ፦"); return BROKER_PHONE
    else:
        is_valid, phone = validate_phone(update.message.text.strip())
        if not is_valid: await update.message.reply_text(f"❌ {phone}"); return BROKER_PHONE
    context.user_data["broker"]["phone"] = phone
    await update.message.reply_text("3️⃣ የሚሰሩበትን አድራሻ ይጻፉ።")
    return BROKER_ADDRESS

async def broker_address(update, context):
    context.user_data["broker"]["address"] = update.message.text.strip()
    user = update.effective_user
    broker = {"user_id": user.id, "name": context.user_data["broker"]["name"], "username": user.username or "", "phone": context.user_data["broker"]["phone"], "address": context.user_data["broker"]["address"], "sectors": context.user_data["broker"].get("sectors", []), "timestamp": datetime.now().isoformat()}
    brokers.append(broker)
    conn = _db_connect(DB_PATH)
    conn.execute("INSERT INTO brokers (user_id, name, username, phone, address, timestamp, sectors) VALUES (?, ?, ?, ?, ?, ?, ?)", (broker["user_id"], broker["name"], broker["username"], broker["phone"], broker["address"], broker["timestamp"], json.dumps(broker["sectors"])))
    conn.commit(); conn.close()
    await update.message.reply_text(f"✅ **ስለተመዘገቡ እናመሰግናለን!**\n\nዘርፎች: {', '.join(broker['sectors'])}\n📌 ማረጋገጫ ሲደረግ ይነገርዎታል።", reply_markup=main_menu(user.id))
    return ConversationHandler.END

async def tracking_menu(update, context):
    context.user_data.clear()
    await update.message.reply_text("📍 **TANA CARGO — የጭነት ክትትል**\n\nአዲስ ክትትል ለመጀመር ወይም ለማቆም ይምረጡ።", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🗺️ የጎግል መረጃ ለመላክ 👈", callback_data="tracking_share_gmaps")], [InlineKeyboardButton("🛑 ክትትል አቁም", callback_data="tracking_stop_request")]]))

async def tracking_share_gmaps_start(update, context):
    await update.callback_query.answer()
    context.user_data["gmaps_share"] = {}
    await update.callback_query.message.reply_text("1️⃣ **የራስዎን አድራሻ** ይጻፉ።", reply_markup=ReplyKeyboardRemove())
    return TRACK_SHARE_ADDRESS
async def tracking_share_address(update, context):
    context.user_data["gmaps_share"]["address"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ **መረጃው የሚላክለት ሰው ስም** ይጻፉ።")
    return TRACK_SHARE_NAME
async def tracking_share_name(update, context):
    context.user_data["gmaps_share"]["name"] = update.message.text.strip()
    await update.message.reply_text("3️⃣ **መረጃው የሚላክለት ሰው ስልክ ቁጥር** ይጻፉ።")
    return TRACK_SHARE_PHONE
async def tracking_share_phone(update, context):
    is_valid, phone = validate_phone(update.message.text.strip())
    if not is_valid: await update.message.reply_text(f"❌ {phone}"); return TRACK_SHARE_PHONE
    info = context.user_data.pop("gmaps_share"); info.update({"phone": phone, "sender_id": update.effective_user.id, "sender_name": update.effective_user.full_name})
    await update.message.reply_text("✅ መረጃዎን ልከናል! አድሚን አጽድቆ ይደርስዎታል።", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END

async def tracking_stop_start(update, context):
    await update.callback_query.answer()
    context.user_data["tracking_stop"] = {}
    await update.callback_query.message.reply_text("1️⃣ **የሚከታተለው ሰው ስም** ይጻፉ።", reply_markup=ReplyKeyboardRemove())
    return TRACK_STOP_NAME
async def tracking_stop_name(update, context):
    context.user_data["tracking_stop"]["name"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ **ስልክ ቁጥር** ይጻፉ።")
    return TRACK_STOP_PHONE
async def tracking_stop_phone(update, context):
    is_valid, phone = validate_phone(update.message.text.strip())
    if not is_valid: await update.message.reply_text(f"❌ {phone}"); return TRACK_STOP_PHONE
    info = context.user_data.pop("tracking_stop"); info.update({"phone": phone, "requester_id": update.effective_user.id})
    await update.message.reply_text("✅ ጥያቄው ለአድሚን ተልኳል።", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END

class HealthHandler(BaseHTTPRequestHandler):
    def _send(self, status, content_type, body):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        path = urlparse(self.path).path.strip("/")
        if path in ("", "health", "healthz"): return self._send(200, "text/plain", "TANA CARGO is running")
        parts = path.split("/")
        if len(parts) == 2 and parts[0] in ("track", "api-track"):
            token = unquote(parts[1]); record = get_public_tracking_token(token)
            if not record: return self._send(404, "text/html", "Link closed.")
            if parts[0] == "api-track":
                loc = get_traccar_location(record["device_id"])
                if not loc: return self._send(503, "application/json", json.dumps({"ok": False, "message": "GPS not found"}))
                return self._send(200, "application/json", json.dumps({"ok": True, **loc}))
            page = r'''<!doctype html><html lang="am"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TANA CARGO Live</title><style>body{margin:0;background:#f2f5f7;font-family:sans-serif;color:#17212b}.top{background:#174d75;color:white;padding:18px;display:flex;align-items:center;gap:10px}.top b{font-size:21px}.wrap{padding:16px;max-width:900px;margin:auto}.card{background:white;border-radius:14px;padding:16px;box-shadow:0 4px 12px #0001;margin-bottom:12px}#map{height:58vh;min-height:330px;border-radius:12px;background:#e4e9ed;overflow:hidden}iframe{width:100%;height:100%;border:0}.muted{color:#657786;font-size:14px}a{color:#1769aa}</style></head><body><div class="top"><span style="font-size:24px;">🚛</span> <b>ከ TANA CARGO & LOGISTICS የተላከ</b></div><div class="wrap"><div class="card"><b>🚗 የቀጥታ የመኪና አካባቢ</b><p id="status" class="muted">GPS መረጃ በመፈለግ ላይ...</p><div id="map">GPS ቦታ ሲገኝ ካርታው ይታያል።</div><a id="google" target="_blank" style="display:none">በGoogle Maps ክፈት</a></div><div class="muted">ይህ የTANA CARGO የክትትል ገጽ ነው።</div></div><script>const api='/api-track/TOKEN_VALUE';async function refresh(){try{const r=await fetch(api,{cache:'no-store'});const d=await r.json();if(!d.ok){document.getElementById('status').textContent=d.message;return}const lat=Number(d.latitude),lon=Number(d.longitude);if(!Number.isFinite(lat)||!Number.isFinite(lon))throw Error();document.getElementById('status').textContent='🚗 '+(d.address||'አድራሻ አልተገኘም');document.getElementById('map').innerHTML='<iframe src="https://maps.google.com/maps?q='+lat+','+lon+'&z=15&output=embed"></iframe>';const a=document.getElementById('google');a.href='https://www.google.com/maps?q='+lat+','+lon;a.style.display='inline-block'}catch(e){document.getElementById('status').textContent='GPS መረጃ ማግኘት አልተቻለም።'}}refresh();setInterval(refresh,15000);</script></body></html>'''
            page = page.replace("TOKEN_VALUE", token)
            return self._send(200, "text/html", page)
        return self._send(404, "text/plain", "Not found")
    def log_message(self, format, *args): return

def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

async def menu_router(update, context):
    text = update.message.text; uid = update.effective_user.id
    if not is_user_registered(uid): return await update.message.reply_text("⚠️ **እባክዎ /start ይጫኑ።**")
    context.user_data.clear()
    if text == "🚚 ጭነት መለጠፍ": return await cargo_start(update, context)
    if text == "🔎 ጭነት መፈለግ": await find_cargo(update, context); return ConversationHandler.END
    if text == "🚛 የጭነት መኪና ማስመዝገብ": return await truck_start(update, context)
    if text == "🚛 የጭነት መኪና መፈለግ": await find_truck(update, context); return ConversationHandler.END
    if text == "🚗 የሽያጭና/ኪራይ መኪና መለጠፍ": return await car_post_start(update, context)
    if text == "🔎 የሽያጭና/ኪራይ መኪናዎችን ፈልግ": await find_car_start(update, context); return ConversationHandler.END
    if text == "👤 የኔ መረጃ": await profile(update, context); return ConversationHandler.END
    if text == "🚛 የጭነት መኪና ክትትል": await tracking_menu(update, context); return ConversationHandler.END
    if text == "🚙 የኪራይ መኪና ክትትል": await tracking_menu(update, context); return ConversationHandler.END
    if text == "📢 መልእክት ለደንበኞች ለመላክ" and is_super_admin(uid): return await super_broadcast_start(update, context)
    if text == "🤝 የንግድ አጋር / ደላላ ይመዝገቡ": return await broker_start(update, context)
    if text == "📞 Support": await support_start(update, context); return ConversationHandler.END
    if text == "ℹ️ About": await about(update, context); return ConversationHandler.END
    return ConversationHandler.END

async def profile(update, context):
    user_id = update.effective_user.id
    user = users.get(user_id, {"name": update.effective_user.full_name, "username": update.effective_user.username or "", "id": user_id})
    message = f"👤 **የኔ መረጃ**\n\n👤 ስም፦ {user['name']}\n🔗 Username፦ @{user['username'] or 'የለም'}\n🆔 Telegram ID፦ {user['id']}\n\n👥 **ጠቅላላ ተጠቃሚዎች፦** {get_total_users_count():,}\n"
    if "owner" in user: message += f"\n📦 የጭነት ባለቤት ምዝገባ፦ ✅\n👤 {user['owner'].get('name', 'N/A')}\n📞 {user['owner'].get('phone', 'N/A')}\n"
    await update.message.reply_text(message, reply_markup=main_menu(user_id))

async def support_start(update, context):
    await update.message.reply_text(f"📞 TANA CARGO የጣና ጭነት እገዛ\n\nለእገዛ ለማግኘት @{SUPPORT_USERNAME} ይጠቀሙ።\n\n☎️ {SUPPORT_PHONE}\n\n🕐 24 ሰዓት / 7 ቀን በመስመር ላይ ነን።", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END

async def about(update, context):
    await update.message.reply_text("🚚 **TANA CARGO & LOGISTICS**\n\nየጭነት ባለቤቶችንና የጭነት መኪና ባለቤቶችን ለማገናኘት የተዘጋጀ አገልግሎት ነው።\n\n🙏 እኛን ስለመረጡ እናመሰግናለን።", reply_markup=main_menu(update.effective_user.id))

async def phone_confirm_callback(update, context):
    query = update.callback_query; await query.answer()
    if query.data == "phone_new":
        await query.message.reply_text("📱 አዲሱን የራስዎን ስልክ ቁጥር ያጋሩ ወይም ይጻፉ።", reply_markup=phone_request_keyboard())
        return PHONE_CONFIRM
    phone = get_registered_phone(update.effective_user.id)
    return await continue_after_phone_confirmation(update, context, phone)

async def phone_confirm_input(update, context):
    if update.message.contact:
        phone = update.message.contact.phone_number
        if phone.startswith("+251"): phone = "0" + phone[4:]
        elif phone.startswith("251"): phone = "0" + phone[3:]
    else:
        phone = (update.message.text or "").strip().split("\n")[0].strip()
    valid, error = validate_phone(phone)
    if not valid: await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678"); return PHONE_CONFIRM
    return await continue_after_phone_confirmation(update, context, phone)

async def continue_after_phone_confirmation(update, context, phone):
    target = context.user_data.pop("phone_confirm_target", "")
    if target == "vehicle_listing":
        listing = context.user_data.get("vehicle_listing", {})
        listing["phone"] = phone
        photo_status = listing.get("photo_status", "provided" if listing.get("photos") else "missing")
        return await save_vehicle_listing(update, context, photo_status=photo_status)
    return ConversationHandler.END

async def save_vehicle_listing(update, context, photo_status):
    listing = context.user_data.get("vehicle_listing", {})
    uid = update.effective_user.id
    conn = _db_connect(DB_PATH)
    try:
        cur = conn.execute("INSERT INTO vehicle_listings (user_id,category,vehicle_type,location,details,price,phone,photo_ids,photo_status,active,created_at,owner_name,plate) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (uid, listing.get("category"), listing.get("vehicle_type"), listing.get("location", ""), listing.get("details", ""), listing.get("price", ""), listing.get("phone"), json.dumps(listing.get("photos", [])), photo_status, 1, datetime.now().astimezone().isoformat(timespec="seconds"), listing.get("owner_name"), listing.get("plate")))
        conn.commit()
    finally: conn.close()
    await update.effective_message.reply_text("✅ መኪናዎ ተመዝግቧል!", reply_markup=main_menu(uid))
    context.user_data.pop("vehicle_listing", None)
    return ConversationHandler.END

async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("❌ ሂደቱ ተሰርዟል።", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END

def main():
    if not TOKEN: raise RuntimeError("BOT_TOKEN missing")
    start_health_server()
    init_db()
    load_persistent_data()
    
    application = Application.builder().token(TOKEN).concurrent_updates(True).build()
    
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.Regex(r"^🚚 ጭነት መለጠፍ$"), menu_router),
            MessageHandler(filters.Regex(r"^🔎 ጭነት መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚛 የጭነት መኪና ማስመዝገብ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚛 የጭነት መኪና መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚗 የሽያጭና/ኪራይ መኪና መለጠፍ$"), menu_router),
            MessageHandler(filters.Regex(r"^🔎 የሽያጭና/ኪራይ መኪናዎችን ፈልግ$"), menu_router),
            MessageHandler(filters.Regex(r"^👤 የኔ መረጃ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚛 የጭነት መኪና ክትትል$"), menu_router),
            MessageHandler(filters.Regex(r"^🚙 የኪራይ መኪና ክትትል$"), menu_router),
            MessageHandler(filters.Regex(r"^📢 መልእክት ለደንበኞች ለመላክ$"), menu_router),
            MessageHandler(filters.Regex(r"^🤝 የንግድ አጋር / ደላላ ይመዝገቡ$"), menu_router),
            MessageHandler(filters.Regex(r"^📞 Support$"), menu_router),
            MessageHandler(filters.Regex(r"^ℹ️ About$"), menu_router),
            CallbackQueryHandler(connection_request, pattern=r"^connect_"),
            CallbackQueryHandler(truck_connection_request, pattern=r"^truckconnect_"),
            CallbackQueryHandler(tracking_share_gmaps_start, pattern=r"^tracking_share_gmaps$"),
            CallbackQueryHandler(tracking_stop_start, pattern=r"^tracking_stop_request$"),
            CallbackQueryHandler(find_vehicle_listings, pattern=r"^find_car_"),
        ],
        states={
            START_PHONE: [MessageHandler(filters.CONTACT, start_phone_input)],
            CARGO_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_from)],
            CARGO_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_to)],
            CARGO_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_type)],
            CARGO_VEHICLE: [CallbackQueryHandler(cargo_vehicle, pattern=r"^cargo_vehicle_"), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_vehicle_text)],
            CARGO_SIZE: [CallbackQueryHandler(cargo_size, pattern=r"^size_")],
            CARGO_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_weight)],
            CARGO_CALENDAR: [CallbackQueryHandler(cargo_calendar, pattern=r"^cal_")],
            CARGO_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_date)],
            CARGO_PHONE: [CallbackQueryHandler(cargo_phone, pattern=r"^phone_(ok|new)$"), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_phone)],
            CARGO_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_price)],
            TRUCK_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_type)],
            TRUCK_PLATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_plate)],
            TRUCK_CAPACITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_capacity)],
            TRUCK_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_address)],
            TRUCK_DRIVER: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_driver)],
            TRUCK_PHONE: [CallbackQueryHandler(truck_phone, pattern=r"^phone_(ok|new)$"), MessageHandler(filters.TEXT & ~filters.COMMAND, truck_phone)],
            VEHICLE_LIST_TYPE: [CallbackQueryHandler(vehicle_listing_type_action, pattern=r"^action_car_")],
            VEHICLE_LIST_LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, vehicle_listing_location)],
            VEHICLE_LIST_PLATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, vehicle_listing_plate)],
            VEHICLE_LIST_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, vehicle_listing_details)],
            VEHICLE_LIST_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, vehicle_listing_price)],
            VEHICLE_LIST_PHOTOS: [CallbackQueryHandler(vehicle_listing_photo_callback, pattern=r"^vehicle_photo_(done|none)$"), MessageHandler(filters.PHOTO, vehicle_listing_photo)],
            VEHICLE_LIST_OWNER: [MessageHandler(filters.TEXT & ~filters.COMMAND, vehicle_listing_owner)],
            CONNECT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, connect_name)],
            CONNECT_PHONE: [CallbackQueryHandler(connect_phone, pattern=r"^phone_(ok|new)$"), MessageHandler(filters.TEXT & ~filters.COMMAND, connect_phone)],
            CONNECT_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, connect_type)],
            SUPER_BROADCAST_TARGET: [CallbackQueryHandler(broadcast_target_choice, pattern=r"^broadcast_"), MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_text_received)],
            SUPER_BROADCAST_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_text_received)],
            SUPER_BROADCAST_PHOTO: [CallbackQueryHandler(broadcast_photo_callback, pattern=r"^broadcast_photo_"), MessageHandler(filters.PHOTO, broadcast_photo_received)],
            BROKER_SECTORS: [CallbackQueryHandler(broker_sector_callback, pattern=r"^broker_sector_")],
            BROKER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, broker_name)],
            BROKER_PHONE: [CallbackQueryHandler(broker_phone, pattern=r"^phone_(ok|new)$"), MessageHandler(filters.TEXT & ~filters.COMMAND, broker_phone)],
            BROKER_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, broker_address)],
            TRACK_SHARE_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_share_address)],
            TRACK_SHARE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_share_name)],
            TRACK_SHARE_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_share_phone)],
            TRACK_STOP_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_stop_name)],
            TRACK_STOP_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_stop_phone)],
            PHONE_CONFIRM: [CallbackQueryHandler(phone_confirm_callback, pattern=r"^phone_(ok|new)$"), MessageHandler((filters.CONTACT | filters.TEXT) & ~filters.COMMAND, phone_confirm_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    application.add_handler(conv_handler)
    
    async def keep_alive_job(context):
        try:
            conn = _db_connect(DB_PATH)
            conn.execute("SELECT 1")
            conn.close()
        except Exception as e:
            print(f"Keep alive db ping failed: {e}")
            
    if application.job_queue:
        application.job_queue.run_repeating(keep_alive_job, interval=600, first=10)
        
    print("🚀 TANA CARGO Bot is running...")
    application.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
