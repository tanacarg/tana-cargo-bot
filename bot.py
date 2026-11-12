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
from urllib.parse import urlparse, unquote, quote, parse_qs
import json
import hashlib
import re
import shutil
import math
from html import unescape as html_unescape
from html.parser import HTMLParser

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
GOOGLE_TRANSLATE_API_KEY = os.getenv("GOOGLE_TRANSLATE_API_KEY", "").strip()
SUPER_ADMIN_USER_ID = os.getenv("SUPER_ADMIN_USER_ID")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")

SUPPORT_PHONE = "0960011010"
SUPPORT_USERNAME = "tanacargosupportbot"
BOT_USERNAME = "tanacargobot"
CHANNEL_USERNAME = "tanapage"

# 👇 የፎቶው File ID እዚህ ገብቷል
START_IMAGE_ID = "AgACAgQAAxkBAAFWw_Vq389auQ7yoeq8rNETEyhfn02JagACCw9rG-tuAVNIehm8-b92jwEAAwIAA3kAAz0E"

TRACCAR_URL = (os.getenv("TRACCAR_URL") or "https://demo2.traccar.org").rstrip("/")
TRACCAR_TOKEN = os.getenv("TRACCAR_TOKEN", "")
TRACCAR_DEVICE_ID = os.getenv("TRACCAR_DEVICE_ID", "92643104")
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL") or ("https://" + os.getenv("RENDER_EXTERNAL_HOSTNAME", "") if os.getenv("RENDER_EXTERNAL_HOSTNAME") else "")).rstrip("/")

CONVERSATION_TIMEOUT_SECONDS = 600
CARGO_EXPIRY_HOURS = 72
MESSAGE_AUTO_DELETE_SECONDS = 1800
# Neon PostgreSQL connection. DATABASE_URL must be set in Render Environment.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_PATH = None  # retained as a compatibility name; all storage uses Neon PostgreSQL.

class _CompatRow(dict):
    """Dict-like PostgreSQL row with SQLite-compatible numeric indexing."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)

class _CompatCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None
    def execute(self, sql, params=None):
        sql = sql.replace("?", "%s")
        # Preserve the one SQLite INSERT OR REPLACE operation in this bot.
        if "INSERT OR REPLACE INTO PENDING_LISTING_BROADCASTS" in sql.upper():
            sql = sql.replace("INSERT OR REPLACE INTO", "INSERT INTO")
            sql = sql.rstrip().rstrip(";") + " ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload, created_at=EXCLUDED.created_at, status=EXCLUDED.status"
        # Capture generated IDs for legacy lastrowid callers.
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
        raise RuntimeError("DATABASE_URL is missing. Set the Neon PostgreSQL connection string in Render Environment.")
    return _CompatConnection(psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10))

# ==================================================
# TRACCAR API
# ==================================================

def _cached_traccar_location(device_id: str):
    try:
        conn = _db_connect(DB_PATH, timeout=5)
        conn.row_factory = dict_row
        try:
            row = conn.execute("SELECT * FROM gps_last_known WHERE device_id=?", (str(device_id),)).fetchone()
            if not row:
                return None
            return {
                "latitude": row["latitude"],
                "longitude": row["longitude"],
                "address": row["address"] or "የመጨረሻ የተመዘገበ ቦታ",
                "timestamp": row["timestamp"] or row["cached_at"],
                "speed_kmh": float(row.get("speed_kmh") or 0),
                "course": float(row.get("course") or 0),
                "is_live": False,
                "status": "offline",
            }
        finally:
            conn.close()
    except Exception as e:
        print(f"GPS cache read error: {e}")
        return None

def _save_traccar_location(device_id: str, location: dict):
    try:
        conn = _db_connect(DB_PATH, timeout=5)
        try:
            conn.execute("""INSERT INTO gps_last_known
                (device_id,latitude,longitude,address,timestamp,cached_at,speed_kmh,course)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(device_id) DO UPDATE SET
                    latitude=excluded.latitude, longitude=excluded.longitude,
                    address=excluded.address, timestamp=excluded.timestamp,
                    cached_at=excluded.cached_at, speed_kmh=excluded.speed_kmh, course=excluded.course""",
                (str(device_id), float(location["latitude"]), float(location["longitude"]),
                 location.get("address", ""), location.get("timestamp", ""),
                 datetime.now().astimezone().isoformat(timespec="seconds"),
                 float(location.get("speed_kmh") or 0), float(location.get("course") or 0)))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"GPS cache write error: {e}")

def get_traccar_device_id_for_user(user_id=None):
    """Resolve a driver's Traccar uniqueId; fall back to the legacy single-device setting.

    For a driver with Telegram ID 123, Render may define TRACCAR_DEVICE_ID_123.
    The value must exactly match that driver's Traccar Client Device Identifier.
    """
    if user_id is not None:
        try:
            per_user_key = f"TRACCAR_DEVICE_ID_{int(user_id)}"
            per_user_value = os.getenv(per_user_key, "").strip()
            if per_user_value:
                return per_user_value
        except (TypeError, ValueError):
            pass
    return str(TRACCAR_DEVICE_ID).strip()


def get_traccar_location(device_id: str):
    """Return latest Traccar point; fall back to persisted last-known point offline."""
    if not TRACCAR_TOKEN:
        print("TRACCAR_TOKEN environment variable is missing.")
        return _cached_traccar_location(device_id)
    try:
        headers = {"Authorization": f"Bearer {TRACCAR_TOKEN}"}
        url_dev = f"{TRACCAR_URL}/api/devices"
        response = requests.get(url_dev, headers=headers, params={"uniqueId": str(device_id)}, timeout=10)
        if response.status_code != 200:
            return _cached_traccar_location(device_id)
        devices = response.json()
        if not devices:
            return _cached_traccar_location(device_id)
        device = devices[0]
        position_id = device.get("positionId")
        if not position_id:
            return _cached_traccar_location(device_id)
        url_pos = f"{TRACCAR_URL}/api/positions?id={position_id}"
        pos_response = requests.get(url_pos, headers=headers, timeout=10)
        if pos_response.status_code != 200:
            return _cached_traccar_location(device_id)
        positions = pos_response.json()
        if not positions:
            return _cached_traccar_location(device_id)
        pos = positions[0]
        fix_time = pos.get("fixTime") or pos.get("deviceTime") or ""
        status = str(device.get("status") or "unknown").lower()
        is_live = status == "online"
        try:
            fix_dt = datetime.fromisoformat(str(fix_time).replace("Z", "+00:00"))
            if fix_dt.tzinfo is None:
                fix_dt = fix_dt.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - fix_dt.astimezone(timezone.utc)).total_seconds() > 180:
                is_live = False
        except (TypeError, ValueError):
            is_live = False
        location = {
            "latitude": pos.get("latitude"),
            "longitude": pos.get("longitude"),
            "address": pos.get("address") or "አድራሻ አልተገኘም",
            "timestamp": str(fix_time)[:19],
            "speed_kmh": round(float(pos.get("speed") or 0) * 1.852, 2),
            "course": float(pos.get("course") or 0),
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


def estimate_offline_position(location):
    """Project an approximate position from last recorded speed and course."""
    try:
        lat, lon = float(location["latitude"]), float(location["longitude"])
        speed = max(0.0, min(float(location.get("speed_kmh") or 0), 120.0))
        course = float(location.get("course") or 0) % 360
        stamp = datetime.fromisoformat(str(location.get("timestamp") or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=timezone.utc)
        elapsed = min(max(0.0, (datetime.now(timezone.utc)-stamp.astimezone(timezone.utc)).total_seconds()), 21600)
        if speed < 3 or elapsed < 60: return None
        distance = speed * elapsed / 3600.0
        earth=6371.0088; bearing=math.radians(course); a=math.radians(lat); b=math.radians(lon); ang=distance/earth
        lat2=math.asin(math.sin(a)*math.cos(ang)+math.cos(a)*math.sin(ang)*math.cos(b))
        lon2=b+math.atan2(math.sin(bearing)*math.sin(ang)*math.cos(a),math.cos(ang)-math.sin(a)*math.sin(lat2))
        return {"estimated_latitude":math.degrees(lat2),"estimated_longitude":((math.degrees(lon2)+540)%360)-180,
                "estimated_distance_km":round(distance,1),"elapsed_minutes":int(elapsed//60),"estimated_speed_kmh":round(speed,1)}
    except (TypeError,ValueError,OverflowError): return None

# ==================================================
# ETHIOPIAN CALENDAR
# ==================================================

def is_ethiopian_leap_year(et_year):
    return (et_year % 4) == 3

def is_gregorian_leap_year(gc_year):
    return (gc_year % 4 == 0 and gc_year % 100 != 0) or (gc_year % 400 == 0)

def et_new_year_gregorian(et_year):
    gc_year = et_year + 7
    start_day = 12 if is_gregorian_leap_year(gc_year + 1) else 11
    return datetime(gc_year, 9, start_day)

def et_to_gc(et_year, et_month, et_day):
    max_day = 6 if et_month == 13 and is_ethiopian_leap_year(et_year) else 5 if et_month == 13 else 30
    if et_month < 1 or et_month > 13 or et_day < 1 or et_day > max_day:
        raise ValueError("Invalid Ethiopian date")
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
        if day > max_day:
            raise ValueError("Gregorian date conversion outside Ethiopian year")
    return candidate_year, month, day

def gc_to_et(gc_year, gc_month, gc_day):
    return gc_to_et_date(datetime(gc_year, gc_month, gc_day))

def parse_flexible_date(date_text):
    raw = date_text.strip().split("\n")[0].strip()
    raw = " ".join(raw.split())
    for sep in ["-", ",", ".", " "]:
        raw = raw.replace(sep, "/")
    parts = [p.strip() for p in raw.split("/") if p.strip()]
    if len(parts) != 3:
        return None, "የቀን ቅርጸቱ ትክክል አይደለም። ምሳሌ፦ 11/02/2019"
    try:
        day, month, year = (int(p) for p in parts)
    except ValueError:
        return None, "ቀን/ወር/ዓመት በቁጥር መሆን አለበት።"
    if year < 1900 or year > 2100:
        return None, "ዓመት ትክክል አይደለም።"
    return {"day": day, "month": month, "year": year}, ""

def convert_calendar(date_parts, calendar):
    day, month, year = date_parts["day"], date_parts["month"], date_parts["year"]
    try:
        if calendar == "ET":
            if month < 1 or month > 13:
                return "", "", "ወር 1 እና 13 መካከል መሆን አለበት።"
            max_day = 6 if month == 13 and is_ethiopian_leap_year(year) else 5 if month == 13 else 30
            if day < 1 or day > max_day:
                return "", "", f"በዚህ ወር ቀኑ 1 እና {max_day} መካከል መሆን አለበት።"
            gc_obj = et_to_gc(year, month, day)
            et_year, et_month, et_day = year, month, day
        else:
            gc_obj = datetime(year, month, day)
            et_year, et_month, et_day = gc_to_et_date(gc_obj)
    except (ValueError, OverflowError):
        return "", "", "ያስገቡት ቀን ትክክል አይደለም።"
    et_date = f"{et_day:02d}/{et_month:02d}/{et_year}"
    gc_date = f"{gc_obj.day:02d}/{gc_obj.month:02d}/{gc_obj.year}"
    return et_date, gc_date, ""

# ==================================================
# DATABASE
# ==================================================

def init_db():
    # PostgreSQL is hosted by Neon; there is no local SQLite file or parent directory.
    # _db_connect validates DATABASE_URL and opens the remote PostgreSQL connection.
    conn = _db_connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY, name TEXT, username TEXT,
            owner_name TEXT, owner_phone TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cargo_posts (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, user_id BIGINT,
            from_location TEXT, to_location TEXT, cargo_type TEXT,
            vehicle TEXT, size INTEGER, size_name TEXT, weight TEXT,
            date_et TEXT, date_gc TEXT, phone TEXT, price REAL,
            price_display TEXT, price_status TEXT, active INTEGER DEFAULT 1,
            timestamp TEXT, expiry_date TEXT, poster_type TEXT DEFAULT 'customer'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS truck_posts (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, user_id BIGINT,
            truck_type TEXT, plate TEXT, capacity TEXT, route TEXT,
            address TEXT, driver TEXT, phone TEXT, active INTEGER DEFAULT 1,
            timestamp TEXT, expiry_date TEXT, poster_type TEXT DEFAULT 'customer'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS brokers (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, user_id BIGINT,
            name TEXT, username TEXT, phone TEXT, address TEXT,
            active INTEGER DEFAULT 1, approved INTEGER DEFAULT 0,
            timestamp TEXT, sectors TEXT DEFAULT '[]'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS machinery_listings (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            user_id BIGINT NOT NULL, machinery_type TEXT NOT NULL, listing_mode TEXT NOT NULL,
            location TEXT NOT NULL, details TEXT DEFAULT '', price TEXT DEFAULT '',
            phone TEXT NOT NULL, photo_ids TEXT DEFAULT '[]', active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tracking_sessions (
            tracking_code TEXT PRIMARY KEY,
            request_type TEXT NOT NULL,
            driver_user_id BIGINT NOT NULL,
            customer_user_id BIGINT NOT NULL,
            origin TEXT DEFAULT '',
            destination TEXT DEFAULT '',
            cargo_description TEXT DEFAULT '',
            truck_description TEXT DEFAULT '',
            status TEXT DEFAULT 'ready',
            latitude REAL,
            longitude REAL,
            location_updated_at TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
    """)
    for column, definition in (
        ("customer_name", "TEXT DEFAULT ''"),
        ("customer_phone", "TEXT DEFAULT ''"),
        ("driver_phone", "TEXT DEFAULT ''"),
    ):
        cursor.execute(f"ALTER TABLE tracking_sessions ADD COLUMN IF NOT EXISTS {column} {definition}")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rental_tracking (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            requester_id BIGINT NOT NULL,
            requester_name TEXT DEFAULT '',
            target_name TEXT NOT NULL,
            target_phone TEXT NOT NULL,
            target_user_id BIGINT,
            map_url TEXT DEFAULT '',
            traccar_url TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL,
            approved_at TEXT,
            stopped_at TEXT
        )
    """)
    cursor.execute("ALTER TABLE brokers ADD COLUMN IF NOT EXISTS sectors TEXT DEFAULT '[]'")
    cursor.execute("ALTER TABLE rental_tracking ADD COLUMN IF NOT EXISTS share_token TEXT")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vehicle_listings (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            user_id BIGINT NOT NULL,
            category TEXT NOT NULL,
            vehicle_type TEXT NOT NULL,
            location TEXT NOT NULL,
            details TEXT DEFAULT '',
            price TEXT DEFAULT '',
            phone TEXT NOT NULL,
            photo_ids TEXT DEFAULT '[]',
            photo_status TEXT DEFAULT 'provided',
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
    # Extra fields used by Bajaj / motorcycle listings. Safe for older databases.
    for column, definition in (("owner_name", "TEXT DEFAULT ''"), ("plate", "TEXT DEFAULT ''")):
        cursor.execute(f"ALTER TABLE vehicle_listings ADD COLUMN IF NOT EXISTS {column} {definition}")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS property_listings (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            user_id BIGINT NOT NULL,
            property_type TEXT NOT NULL,
            listing_mode TEXT NOT NULL,
            location TEXT NOT NULL,
            size TEXT NOT NULL,
            price TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            legal_role TEXT NOT NULL,
            document_ids TEXT NOT NULL DEFAULT '[]',
            photo_ids TEXT NOT NULL DEFAULT '[]',
            phone TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            reviewed_by BIGINT,
            reviewed_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS property_inquiries (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            listing_id BIGINT NOT NULL REFERENCES property_listings(id),
            buyer_id BIGINT NOT NULL, buyer_name TEXT NOT NULL,
            buyer_phone TEXT NOT NULL, message TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new', created_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tender_subscribers (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            user_id BIGINT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            telegram_name TEXT DEFAULT '',
            phone TEXT NOT NULL,
            address TEXT NOT NULL,
            categories TEXT NOT NULL DEFAULT '[]',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS info_category_subscriptions (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            user_id BIGINT NOT NULL, category TEXT NOT NULL, full_name TEXT NOT NULL,
            phone TEXT NOT NULL, telegram_name TEXT DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL, UNIQUE(user_id, category)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS property_connector_listings (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, user_id BIGINT NOT NULL,
            category TEXT NOT NULL, mode TEXT NOT NULL, full_name TEXT NOT NULL, phone TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '', photo_ids TEXT NOT NULL DEFAULT '[]',
            active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tender_broadcast_log (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            admin_user_id BIGINT NOT NULL,
            category TEXT NOT NULL,
            message_text TEXT NOT NULL DEFAULT '',
            photo_file_id TEXT DEFAULT '',
            sent_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vehicle_inquiries (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            listing_id INTEGER NOT NULL,
            owner_id BIGINT NOT NULL,
            buyer_id BIGINT NOT NULL,
            buyer_name TEXT NOT NULL,
            buyer_username TEXT DEFAULT '',
            buyer_phone TEXT NOT NULL,
            category TEXT NOT NULL,
            vehicle_type TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_broadcast_log (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            admin_id BIGINT NOT NULL,
            target_user_id BIGINT,
            message_text TEXT NOT NULL,
            sent_count INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_preferences (
            user_id BIGINT PRIMARY KEY,
            show_private INTEGER NOT NULL DEFAULT 1
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_media_links (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            platform TEXT NOT NULL,
            media_type TEXT NOT NULL,
            organization_name TEXT NOT NULL,
            media_url TEXT NOT NULL,
            created_by BIGINT NOT NULL,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS media_posts (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            source_url TEXT NOT NULL, source_name TEXT DEFAULT '', platform TEXT DEFAULT '',
            post_url TEXT DEFAULT '', post_text TEXT NOT NULL, post_date TEXT DEFAULT '',
            created_at TEXT NOT NULL, UNIQUE(source_url, post_url)
        )
    """)
    # Organization directory and independent monitoring sources. Existing media tables remain intact.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitored_organizations (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            name TEXT NOT NULL, region TEXT NOT NULL DEFAULT '',
            org_type TEXT NOT NULL DEFAULT '', created_by BIGINT,
            created_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(name, region)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitored_sources (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            organization_id BIGINT NOT NULL REFERENCES monitored_organizations(id) ON DELETE CASCADE,
            platform TEXT NOT NULL, source_url TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1, last_checked_at TEXT,
            last_success_at TEXT, last_result TEXT NOT NULL DEFAULT 'pending',
            last_error TEXT DEFAULT '', last_new_at TEXT, baseline_done INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            UNIQUE(organization_id, platform, source_url)
        )
    """)
    cursor.execute("ALTER TABLE monitored_sources ADD COLUMN IF NOT EXISTS baseline_done INTEGER NOT NULL DEFAULT 0")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS organization_announcements (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            organization_id BIGINT NOT NULL REFERENCES monitored_organizations(id) ON DELETE CASCADE,
            source_id BIGINT NOT NULL REFERENCES monitored_sources(id) ON DELETE CASCADE,
            category TEXT NOT NULL DEFAULT 'other', title TEXT DEFAULT '',
            post_text TEXT NOT NULL, post_url TEXT NOT NULL, post_date TEXT DEFAULT '',
            content_hash TEXT NOT NULL, created_at TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'pending', approved_by BIGINT, approved_at TEXT,
            reference_number TEXT DEFAULT '', digest_sent_at TEXT, translated_text TEXT DEFAULT '',
            UNIQUE(source_id, content_hash)
        )
    """)
    cursor.execute("ALTER TABLE organization_announcements ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'archived'")
    cursor.execute("ALTER TABLE organization_announcements ALTER COLUMN review_status SET DEFAULT 'pending'")
    cursor.execute("ALTER TABLE organization_announcements ADD COLUMN IF NOT EXISTS approved_by BIGINT")
    cursor.execute("ALTER TABLE organization_announcements ADD COLUMN IF NOT EXISTS reference_number TEXT DEFAULT ''")
    cursor.execute("ALTER TABLE organization_announcements ADD COLUMN IF NOT EXISTS digest_sent_at TEXT")
    cursor.execute("ALTER TABLE organization_announcements ADD COLUMN IF NOT EXISTS translated_text TEXT DEFAULT ''")
    cursor.execute("ALTER TABLE organization_announcements ADD COLUMN IF NOT EXISTS approved_at TEXT")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_listing_broadcasts (
            id INTEGER PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        )
    """)
    cursor.execute("ALTER TABLE pending_listing_broadcasts ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending'")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS connection_requests (
            request_index INTEGER PRIMARY KEY,
            payload TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gps_admin_requests (
            request_type TEXT NOT NULL,
            request_index INTEGER NOT NULL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            PRIMARY KEY (request_type, request_index)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pending_gmaps_shares (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            phone TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gps_last_known (
            device_id TEXT PRIMARY KEY,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            address TEXT DEFAULT '',
            timestamp TEXT DEFAULT '',
            cached_at TEXT NOT NULL,
            speed_kmh REAL DEFAULT 0,
            course REAL DEFAULT 0
        )
    """)
    cursor.execute("ALTER TABLE gps_last_known ADD COLUMN IF NOT EXISTS speed_kmh REAL DEFAULT 0")
    cursor.execute("ALTER TABLE gps_last_known ADD COLUMN IF NOT EXISTS course REAL DEFAULT 0")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS public_tracking_tokens (
            token TEXT PRIMARY KEY,
            device_id TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            label TEXT DEFAULT 'TANA CARGO, LOGISTICS & INFORMATION SERVICES',
            created_at TEXT NOT NULL,
            expires_at TEXT
        )
    """)
    # Telegram account IDs can exceed PostgreSQL's 32-bit INTEGER range.
    # Widen every Telegram-user-ID column so registration and later actions work.
    for table, column in (
        ("users", "user_id"), ("cargo_posts", "user_id"),
        ("truck_posts", "user_id"), ("brokers", "user_id"),
        ("tracking_sessions", "driver_user_id"),
        ("tracking_sessions", "customer_user_id"),
        ("rental_tracking", "requester_id"),
        ("rental_tracking", "target_user_id"),
        ("vehicle_listings", "user_id"), ("machinery_listings", "user_id"),
        ("vehicle_inquiries", "owner_id"),
        ("vehicle_inquiries", "buyer_id"),
        ("admin_broadcast_log", "admin_id"),
        ("admin_broadcast_log", "target_user_id"),
        ("admin_preferences", "user_id"),
    ):
        cursor.execute(
            f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE BIGINT USING "{column}"::BIGINT'
        )
    # Auditable soft-delete archive; records are retained for recovery.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS content_deletion_archive (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            target_table TEXT NOT NULL, target_id BIGINT NOT NULL,
            snapshot_json TEXT NOT NULL, reason TEXT NOT NULL,
            deleted_by BIGINT NOT NULL, deleted_at TEXT NOT NULL,
            restore_until TEXT NOT NULL, restored_at TEXT, restored_by BIGINT,
            permanently_deleted_at TEXT, permanently_deleted_by BIGINT
        )
    """)
    cursor.execute("ALTER TABLE media_posts ADD COLUMN IF NOT EXISTS is_deleted INTEGER NOT NULL DEFAULT 0")
    cursor.execute("ALTER TABLE media_posts ADD COLUMN IF NOT EXISTS deleted_at TEXT")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            referrer_id BIGINT NOT NULL,
            referred_id BIGINT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
    """)
    # Starter directory: verified official public portals/channels. Admins can add more sources anytime.
    seed_orgs = [
        ("Ethiopian e-Government Procurement (e-GP)", "ኢትዮጵያ", "government", [("Website", "https://egp.gov.et/")]),
        ("Amhara Regional Government Portal", "አማራ", "government", [("Website", "https://www.amhara.gov.et/")]),
        ("Amhara Communication", "አማራ", "government", [("Facebook", "https://facebook.com/AmharaCommunications"), ("Telegram", "https://t.me/amharacommunications"), ("Website", "https://www.amharacomm.gov.et")]),
        ("Industrial Parks Development Corporation (IPDC)", "ኢትዮጵያ / አማራ", "government", [("Website", "https://ipdc.gov.et/"), ("Facebook", "https://www.facebook.com/IPDCEthiopiaofficial"), ("Telegram", "https://t.me/ipdcofficial")]),
        ("Amhara Investment Commission", "አማራ", "government", [("Website", "https://investinamhara.gov.et/"), ("Facebook", "https://www.facebook.com/investamhara")]),
        ("Addis Ababa City Communication", "አዲስ አበባ", "government", [("Telegram", "https://t.me/AAcommu"), ("Facebook", "https://www.facebook.com/profile.php?id=100066583024934"), ("Website", "https://cityaddisababa.gov.et")]),
    ]
    for org_name, org_region, org_type, org_sources in seed_orgs:
        cursor.execute("""INSERT INTO monitored_organizations(name,region,org_type,created_at,active)
            VALUES(?,?,?,?,1) ON CONFLICT(name,region) DO NOTHING""",
            (org_name,org_region,org_type,datetime.now(timezone.utc).isoformat()))
        org_row=cursor.execute("SELECT id FROM monitored_organizations WHERE name=? AND region=?",(org_name,org_region)).fetchone()
        if org_row:
            for source_platform, source_url in org_sources:
                cursor.execute("""INSERT INTO monitored_sources(organization_id,platform,source_url,created_at)
                    VALUES(?,?,?,?) ON CONFLICT(organization_id,platform,source_url) DO NOTHING""",
                    (org_row["id"],source_platform,source_url,datetime.now(timezone.utc).isoformat()))
    # Preserve links saved by the previous media-directory version.
    legacy_sources=cursor.execute("SELECT platform,organization_name,media_url FROM admin_media_links WHERE active=1").fetchall()
    for legacy in legacy_sources:
        legacy_name=(legacy["organization_name"] or "ያልታወቀ ድርጅት").strip()
        legacy_region="ያልተወሰነ"
        cursor.execute("""INSERT INTO monitored_organizations(name,region,org_type,created_at,active)
            VALUES(?,?,?,?,1) ON CONFLICT(name,region) DO NOTHING""",
            (legacy_name,legacy_region,"other",datetime.now(timezone.utc).isoformat()))
        legacy_org=cursor.execute("SELECT id FROM monitored_organizations WHERE name=? AND region=?",(legacy_name,legacy_region)).fetchone()
        if legacy_org:
            cursor.execute("""INSERT INTO monitored_sources(organization_id,platform,source_url,created_at)
                VALUES(?,?,?,?) ON CONFLICT(organization_id,platform,source_url) DO NOTHING""",
                (legacy_org["id"],legacy["platform"],legacy["media_url"],datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()

def db_record_referral(referrer_id, referred_id):
    if not referrer_id or not referred_id or int(referrer_id) == int(referred_id):
        return False
    conn = _db_connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?, ?, ?) ON CONFLICT(referred_id) DO NOTHING",
            (int(referrer_id), int(referred_id), datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()

def db_referral_count(user_id):
    conn = _db_connect(DB_PATH)
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM referrals WHERE referrer_id=?", (int(user_id),)).fetchone()
        return int(row["n"] if row else 0)
    finally:
        conn.close()

async def show_invite_link(update, context):
    """Show one simple invite message and Telegram's friend-picker share action."""
    bot_link = f"https://t.me/{BOT_USERNAME}"
    share_text = (
        "TANA CARGO, LOGISTICS & INFORMATION SERVICES | ጣና ጭነት\n"
        "የጭነት ባለቤቶችንና ጫኝ መኪኖችን የሚያገናኝ አገልግሎት።\n"
        f"{bot_link}"
    )
    share_url = (
        "https://t.me/share/url?url=" + quote(bot_link, safe="")
        + "&text=" + quote(share_text, safe="")
    )
    text = (
        "👥 ጓደኛችህን ምረጥ፣ ጋብዝ — INVITE FRIENDS\n\n"
        "TANA CARGO, LOGISTICS & INFORMATION SERVICES | ጣና ጭነት\n"
        "የጭነት ባለቤቶችንና ጫኝ መኪኖችን የሚያገናኝ አገልግሎት።"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("👥 ጓደኞችን ምረጥና ጋብዝ", url=share_url)
    ]])
    await update.effective_message.reply_text(
        text, reply_markup=keyboard, disable_web_page_preview=True
    )

def db_save_user(user_id, name, username, owner_name=None, owner_phone=None):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, name, username, owner_name, owner_phone)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            name = excluded.name, username = excluded.username,
            owner_name = COALESCE(excluded.owner_name, users.owner_name),
            owner_phone = COALESCE(excluded.owner_phone, users.owner_phone)
    """, (user_id, name, username, owner_name, owner_phone))
    conn.commit()
    conn.close()

def get_total_users_count():
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        real_users = cursor.fetchone()[0]
        return 7000 + real_users
    finally:
        conn.close()

def db_save_cargo(cargo):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO cargo_posts (
            user_id, from_location, to_location, cargo_type, vehicle,
            size, size_name, weight, date_et, date_gc, phone, price,
            price_display, price_status, active, timestamp, expiry_date, poster_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        cargo["user_id"], cargo["from"], cargo["to"], cargo["type"],
        cargo["vehicle"], cargo["size"], cargo["size_name"], cargo["weight"],
        cargo.get("date_et", ""), cargo.get("date_gc", ""), cargo["phone"],
        cargo["price"], cargo["price_display"], cargo["price_status"],
        1, cargo["timestamp"], cargo.get("expiry_date", ""),
        cargo.get("poster_type", "customer")
    ))
    conn.commit()
    record_id = cursor.lastrowid
    conn.close()
    return record_id

def db_save_truck(truck):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO truck_posts (
            user_id, truck_type, plate, capacity, route, address,
            driver, phone, active, timestamp, expiry_date, poster_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        truck["user_id"], truck["type"], truck["plate"], truck["capacity"],
        truck["route"], truck["address"], truck["driver"], truck["phone"],
        1, truck["timestamp"], truck.get("expiry_date", ""),
        truck.get("poster_type", "customer")
    ))
    conn.commit()
    record_id = cursor.lastrowid
    conn.close()
    return record_id

def db_save_broker(broker):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO brokers (
            user_id, name, username, phone, address, active, approved, timestamp, sectors
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        broker["user_id"], broker["name"], broker["username"],
        broker["phone"], broker["address"], 1, 1,
        broker["timestamp"], json.dumps(broker.get("sectors", []), ensure_ascii=False)
    ))
    conn.commit()
    record_id = cursor.lastrowid
    conn.close()
    return record_id

def db_update_post_active(table, record_id, active):
    if table not in ("cargo_posts", "truck_posts"):
        return
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        conn.execute(f"UPDATE {table} SET active=? WHERE id=?", (1 if active else 0, int(record_id)))
        conn.commit()
    finally:
        conn.close()

def db_save_connection_request(request_index, request):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        conn.execute("""INSERT INTO connection_requests (request_index,payload,updated_at)
            VALUES (?,?,?) ON CONFLICT(request_index) DO UPDATE SET
            payload=excluded.payload, updated_at=excluded.updated_at""",
            (int(request_index), json.dumps(request, ensure_ascii=False),
             datetime.now().astimezone().isoformat(timespec="seconds")))
        conn.commit()
    finally:
        conn.close()

def db_save_gps_admin_request(request_type, request_index, payload, status=None):
    request_type = str(request_type)
    request_index = int(request_index)
    request_status = status or payload.get("status") or "pending"
    created_at = payload.get("timestamp") or payload.get("created_at") or datetime.now().astimezone().isoformat(timespec="seconds")
    conn = _db_connect(DB_PATH, timeout=10)
    try:
        conn.execute("""INSERT INTO gps_admin_requests
            (request_type,request_index,payload,status,created_at) VALUES (?,?,?,?,?)
            ON CONFLICT(request_type,request_index) DO UPDATE SET
            payload=excluded.payload,status=excluded.status,created_at=excluded.created_at""",
            (request_type, request_index, json.dumps(payload, ensure_ascii=False), request_status, created_at))
        conn.commit()
    finally:
        conn.close()

def db_update_gps_admin_request(request_type, request_index, payload, status):
    db_save_gps_admin_request(request_type, request_index, payload, status=status)

def db_save_pending_gmaps_share(payload):
    conn = _db_connect(DB_PATH, timeout=10)
    try:
        cur = conn.execute("INSERT INTO pending_gmaps_shares (phone,payload,created_at) VALUES (?,?,?)",
            (str(payload.get("phone", "")), json.dumps(payload, ensure_ascii=False),
             datetime.now().astimezone().isoformat(timespec="seconds")))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()

def db_delete_pending_gmaps_share(record_id):
    if not record_id:
        return
    conn = _db_connect(DB_PATH, timeout=10)
    try:
        conn.execute("DELETE FROM pending_gmaps_shares WHERE id=?", (int(record_id),))
        conn.commit()
    finally:
        conn.close()

def db_create_tracking_session(req, request_index):
    if req.get("type") == "cargo":
        cargo_index = req.get("cargo_index")
        if not isinstance(cargo_index, int) or not (0 <= cargo_index < len(cargo_posts)):
            raise ValueError("Cargo record not available for tracking")
        cargo = cargo_posts[cargo_index]
        driver_user_id = int(req["requester_id"])
        customer_user_id = int(req["cargo_owner_id"])
        origin = str(cargo.get("from", ""))
        destination = str(cargo.get("to", ""))
        cargo_description = str(cargo.get("type", ""))
        truck_description = str(req.get("requester_truck_type", ""))
    else:
        truck_index = req.get("truck_index")
        if not isinstance(truck_index, int) or not (0 <= truck_index < len(truck_posts)):
            raise ValueError("Truck record not available for tracking")
        truck = truck_posts[truck_index]
        driver_user_id = int(req["truck_owner_id"])
        customer_user_id = int(req["requester_id"])
        origin = str(req.get("requester_cargo_info", ""))
        destination = str(truck.get("route", ""))
        cargo_description = str(req.get("requester_cargo_info", ""))
        truck_description = str(truck.get("type", ""))

    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        cursor = conn.cursor()
        for _ in range(12):
            code = "TC" + secrets.token_hex(4).upper()
            exists = cursor.execute(
                "SELECT 1 FROM tracking_sessions WHERE tracking_code = ?", (code,)
            ).fetchone()
            if exists:
                continue
            cursor.execute("""
                INSERT INTO tracking_sessions (
                    tracking_code, request_type, driver_user_id, customer_user_id,
                    origin, destination, cargo_description, truck_description,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?)
            """, (
                code, str(req.get("type", "cargo")), driver_user_id, customer_user_id,
                origin, destination, cargo_description, truck_description,
                datetime.now().astimezone().isoformat(timespec="seconds")
            ))
            conn.commit()
            return code
        raise RuntimeError("Unable to generate a unique tracking code")
    finally:
        conn.close()

def db_get_tracking(tracking_code):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    conn.row_factory = dict_row
    try:
        row = conn.execute(
            "SELECT * FROM tracking_sessions WHERE tracking_code = ?",
            (tracking_code.upper().strip(),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def db_get_driver_tracking(driver_user_id):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    conn.row_factory = dict_row
    try:
        rows = conn.execute("""
            SELECT tracking_code, origin, destination, cargo_description, status,
                   location_updated_at
            FROM tracking_sessions
            WHERE driver_user_id = ? AND status IN ('ready', 'in_transit')
            ORDER BY created_at DESC
        """, (int(driver_user_id),)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

def db_update_tracking_location(tracking_code, latitude, longitude):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        conn.execute("""
            UPDATE tracking_sessions
            SET latitude = ?, longitude = ?, status = 'in_transit',
                location_updated_at = ?
            WHERE tracking_code = ?
        """, (
            float(latitude), float(longitude),
            datetime.now().astimezone().isoformat(timespec="seconds"),
            tracking_code.upper().strip()
        ))
        conn.commit()
    finally:
        conn.close()

def db_finish_tracking(tracking_code):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        conn.execute("""
            UPDATE tracking_sessions SET status = 'delivered', completed_at = ?
            WHERE tracking_code = ?
        """, (
            datetime.now().astimezone().isoformat(timespec="seconds"),
            tracking_code.upper().strip()
        ))
        conn.commit()
    finally:
        conn.close()

def find_user_by_phone(phone):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        clean_phone = phone.strip()
        if clean_phone.startswith("0"):
            phone_variants = [clean_phone, "+251" + clean_phone[1:], "251" + clean_phone[1:]]
        elif clean_phone.startswith("+251"):
            phone_variants = [clean_phone, "0" + clean_phone[4:], "251" + clean_phone[4:]]
        elif clean_phone.startswith("251"):
            phone_variants = [clean_phone, "0" + clean_phone[3:], "+" + clean_phone]
        else:
            phone_variants = [clean_phone]
            
        for p in phone_variants:
            row = conn.execute(
                "SELECT user_id FROM users WHERE owner_phone = ? LIMIT 1", (p,)
            ).fetchone()
            if row:
                return int(row[0])
                
        for p in phone_variants:
            row = conn.execute(
                "SELECT driver_user_id FROM tracking_sessions WHERE customer_phone = ? AND status IN ('ready','in_transit') ORDER BY created_at DESC LIMIT 1",
                (p,)
            ).fetchone()
            if row and row[0]:
                return int(row[0])
                
        for p in phone_variants:
            row = conn.execute(
                "SELECT user_id FROM cargo_posts WHERE phone = ? LIMIT 1", (p,)
            ).fetchone()
            if row:
                return int(row[0])
                
        for p in phone_variants:
            row = conn.execute(
                "SELECT user_id FROM truck_posts WHERE phone = ? LIMIT 1", (p,)
            ).fetchone()
            if row:
                return int(row[0])
            
    finally:
        conn.close()
    return None

def find_registered_user_by_phone(phone):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        clean = str(phone).strip()
        if clean.startswith("0"):
            variants = [clean, "+251" + clean[1:], "251" + clean[1:]]
        elif clean.startswith("+251"):
            variants = [clean, "0" + clean[4:], "251" + clean[4:]]
        elif clean.startswith("251"):
            variants = [clean, "0" + clean[3:], "+" + clean]
        else:
            variants = [clean]
        for candidate in variants:
            row = conn.execute(
                "SELECT user_id FROM users WHERE owner_phone = ? LIMIT 1",
                (candidate,)
            ).fetchone()
            if row:
                return int(row[0])
        return None
    finally:
        conn.close()

def find_driver_by_customer_phone(phone):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    conn.row_factory = dict_row
    try:
        row = conn.execute("""
            SELECT driver_user_id, tracking_code, origin, destination, customer_name
            FROM tracking_sessions
            WHERE customer_phone = ? AND status IN ('ready','in_transit')
            ORDER BY created_at DESC LIMIT 1
        """, (phone,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def db_create_rental_request(requester_id, requester_name, target_name, target_phone):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        share_token = secrets.token_urlsafe(18)
        cur = conn.execute("""
            INSERT INTO rental_tracking
                (requester_id, requester_name, target_name, target_phone, created_at, share_token)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (int(requester_id), requester_name, target_name, target_phone,
              datetime.now().astimezone().isoformat(timespec="seconds"), share_token))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()

def db_get_rental_request(request_id):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    conn.row_factory = dict_row
    try:
        row = conn.execute("SELECT * FROM rental_tracking WHERE id = ?",
                           (int(request_id),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def db_get_rental_request_by_token(token):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    conn.row_factory = dict_row
    try:
        row = conn.execute(
            "SELECT * FROM rental_tracking WHERE share_token = ?", (str(token),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def phones_match(phone_a, phone_b):
    def norm(p):
        p = "".join(ch for ch in str(p or "") if ch.isdigit())
        if p.startswith("251"):
            p = "0" + p[3:]
        return p
    return bool(norm(phone_a)) and norm(phone_a) == norm(phone_b)

def db_update_rental_request(request_id, **fields):
    allowed = {"target_user_id", "map_url", "traccar_url", "status",
               "approved_at", "stopped_at"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    sql = ", ".join(f"{key} = ?" for key in fields)
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        conn.execute(f"UPDATE rental_tracking SET {sql} WHERE id = ?",
                     (*fields.values(), int(request_id)))
        conn.commit()
    finally:
        conn.close()

def db_list_tracking_sessions():
    conn = _db_connect(DB_PATH, check_same_thread=False)
    conn.row_factory = dict_row
    try:
        rows = conn.execute(
            "SELECT * FROM tracking_sessions ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def db_list_rental_requests(active_only=False):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    conn.row_factory = dict_row
    try:
        sql = "SELECT * FROM rental_tracking"
        if active_only:
            sql += " WHERE status IN ('pending','approved')"
        sql += " ORDER BY id DESC LIMIT 200"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()

def db_list_registered_users(limit=5000):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    conn.row_factory = dict_row
    try:
        rows = conn.execute(
            "SELECT user_id, name, username, owner_phone FROM users "
            "WHERE owner_phone IS NOT NULL AND owner_phone != '' "
            "ORDER BY user_id LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
# ==================================================
# MEMORY
# ==================================================

users = {}
cargo_posts = []
truck_posts = []
brokers = []
connection_requests = []
pending_deletes = []
pending_track_requests = []
gmaps_share_requests = []
tracking_stops = []
pending_gmaps_shares = []
pending_listing_broadcasts = {}
next_listing_broadcast_id = 1


def load_persistent_data():
    """Restore users, posts, brokers, connection requests and pending broadcasts from SQLite."""
    global cargo_posts, truck_posts, brokers, connection_requests, users
    global pending_listing_broadcasts, next_listing_broadcast_id
    global gmaps_share_requests, tracking_stops, pending_gmaps_shares
    conn = _db_connect(DB_PATH, check_same_thread=False)
    conn.row_factory = dict_row
    try:
        users = {}
        for row in conn.execute("SELECT * FROM users"):
            r = dict(row)
            users[int(r["user_id"])] = {
                "id": int(r["user_id"]), "name": r.get("name") or "",
                "username": r.get("username") or "",
                "owner": {"name": r.get("owner_name") or r.get("name") or "",
                          "phone": r.get("owner_phone") or "", "user_id": int(r["user_id"])}
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
                "timestamp": r["timestamp"],
                "sectors": json.loads(r.get("sectors") or "[]")
            })
        connection_requests = []
        for row in conn.execute("SELECT payload FROM connection_requests ORDER BY request_index"):
            try:
                connection_requests.append(json.loads(row["payload"]))
            except (TypeError, json.JSONDecodeError):
                continue
        pending_listing_broadcasts = {}
        for row in conn.execute("SELECT id,payload FROM pending_listing_broadcasts WHERE status='pending' ORDER BY id"):
            try:
                pending_listing_broadcasts[int(row["id"])] = json.loads(row["payload"])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        last_listing_id = conn.execute("SELECT COALESCE(MAX(id),0) FROM pending_listing_broadcasts").fetchone()[0]
        next_listing_broadcast_id = int(last_listing_id or 0) + 1
        gmaps_share_requests = []
        tracking_stops = []
        for row in conn.execute("SELECT request_type,request_index,payload FROM gps_admin_requests ORDER BY request_type,request_index"):
            try:
                payload = json.loads(row["payload"])
                if row["request_type"] == "gmaps":
                    gmaps_share_requests.append(payload)
                elif row["request_type"] == "stop":
                    tracking_stops.append(payload)
            except (TypeError, json.JSONDecodeError):
                continue
        pending_gmaps_shares = []
        for row in conn.execute("SELECT id,payload FROM pending_gmaps_shares ORDER BY id"):
            try:
                payload = json.loads(row["payload"])
                payload["_db_id"] = int(row["id"])
                pending_gmaps_shares.append(payload)
            except (TypeError, json.JSONDecodeError):
                continue
        print(f"✅ Restored {len(users)} users, {len(cargo_posts)} cargo, {len(truck_posts)} trucks, {len(brokers)} brokers, {len(connection_requests)} connection requests, {len(pending_listing_broadcasts)} pending listing broadcasts, {len(gmaps_share_requests)} Google Maps requests, {len(tracking_stops)} GPS stop requests and {len(pending_gmaps_shares)} pending GPS deliveries")
    finally:
        conn.close()

# ==================================================
# STATES
# ==================================================

(
    CARGO_FROM, CARGO_TO, CARGO_TYPE, CARGO_VEHICLE, CARGO_SIZE,
    CARGO_WEIGHT, CARGO_CALENDAR, CARGO_DATE, CARGO_PHONE, CARGO_PRICE,
) = range(10)

(
    TRUCK_TYPE, TRUCK_PLATE, TRUCK_CAPACITY, TRUCK_ROUTE,
    TRUCK_ADDRESS, TRUCK_DRIVER, TRUCK_PHONE,
) = range(10, 17)

OWNER_NAME, OWNER_PHONE = range(17, 19)
(
    CONNECT_NAME, CONNECT_PHONE, CONNECT_TYPE,
) = range(19, 22)

(
    BROKER_NAME, BROKER_PHONE, BROKER_ADDRESS,
) = range(22, 25)

(
    TRACK_REQ_PHONE, TRACK_REQ_ROUTE, TRACK_REQ_LOCATION,
    CUST_TRACK_PHONE_ONLY
) = range(25, 29)

(
    TRACK_SHARE_ADDRESS, TRACK_SHARE_NAME, TRACK_SHARE_PHONE,
    TRACK_STOP_NAME, TRACK_STOP_PHONE
) = range(29, 34)

START_PHONE = 34
RENTAL_TARGET_NAME = 35
RENTAL_TARGET_PHONE = 36
SUPER_BROADCAST_TEXT = 37
VEHICLE_LIST_TYPE, VEHICLE_LIST_LOCATION, VEHICLE_LIST_DETAILS, VEHICLE_LIST_PRICE, VEHICLE_LIST_PHOTOS = range(38, 43)
VEHICLE_ADMIN_EDIT = 43
PHONE_CONFIRM = 50
VEHICLE_BUYER_NAME = 51
VEHICLE_LIST_PLATE = 52
VEHICLE_LIST_OWNER = 53
BROKER_SECTORS = 54
MACH_TYPE, MACH_MODE, MACH_LOCATION, MACH_DETAILS, MACH_PRICE, MACH_PHONE, MACH_PHOTOS, MACH_SEARCH = range(55, 63)
PROPERTY_TYPE, PROPERTY_MODE, PROPERTY_LOCATION, PROPERTY_SIZE, PROPERTY_PRICE, PROPERTY_DETAILS, PROPERTY_ROLE, PROPERTY_DOCS, PROPERTY_PHOTOS = range(63, 72)
TENDER_NAME, TENDER_PHONE, TENDER_ADDRESS, TENDER_TYPES = range(72, 76)
TENDER_BROADCAST_CATEGORY, TENDER_BROADCAST_CONTENT = range(76, 78)
INFO_SUB_CATEGORY, INFO_SUB_NAME = range(78, 80)
PROPERTY_HUB_KIND, PROPERTY_HUB_MODE, PROPERTY_HUB_NAME, PROPERTY_HUB_FIELD, PROPERTY_HUB_PHOTOS = range(80, 85)

# ==================================================
# MENUS
# ==================================================

def get_admin_pii_visibility(user_id):
    try:
        conn = _db_connect(DB_PATH, timeout=5)
        try:
            row = conn.execute("SELECT show_private FROM admin_preferences WHERE user_id=?", (int(user_id),)).fetchone()
            return bool(row[0]) if row else True
        finally:
            conn.close()
    except Exception:
        return True

def set_admin_pii_visibility(user_id, visible):
    conn = _db_connect(DB_PATH, timeout=5)
    try:
        conn.execute("""INSERT INTO admin_preferences (user_id,show_private) VALUES (?,?)
            ON CONFLICT(user_id) DO UPDATE SET show_private=excluded.show_private""",
            (int(user_id), 1 if visible else 0))
        conn.commit()
    finally:
        conn.close()

async def toggle_admin_pii(update, context):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Admin ብቻ ይህን ማስተካከል ይችላል።")
        return ConversationHandler.END
    new_value = not get_admin_pii_visibility(uid)
    set_admin_pii_visibility(uid, new_value)
    status = "ON — የግል መረጃ ይታያል" if new_value else "OFF — የግል መረጃ ይደበቃል"
    await update.message.reply_text(f"✅ የአድሚን የግል መረጃ ማሳያ፦ {status}", reply_markup=main_menu(uid))
    return ConversationHandler.END

# Folder-based navigation: legacy service buttons remain available inside folders.
MAIN_FOLDERS = [
    "🚛 ጭነትና የጭነት መኪና ማገናኛ",
    "🚗 መኪናና ማሽነሪ ሽያጭ/ኪራይ ማገናኛ",
    "🏠 የመኖሪያ፣ የንግድና የድርጅት ቦታዎች ሻጭና ገዢ ማገናኛ",
    "📢 የግዢና የሽያጭ፣ የግንባታ፣ የሥራ ፍለጋ መረጃዎችን ፈላጊ ከሆኑ ተጭነው ይመዝገቡ",
    "__legacy_broker_registration_disabled__",
    "👥 ወዳጆችን ጋብዝ",
]

FOLDER_BUTTONS = {
    MAIN_FOLDERS[0]: [
        ["🚚 ጭነት መለጠፍ", "🔎 ጭነት መፈለግ"],
        ["🚛 የጭነት መኪና ማስመዝገብ", "🚛 የጭነት መኪና መፈለግ"],
        ["GPS\n🚛 የጭነት ክትትል 🌏"],
    ],
    MAIN_FOLDERS[1]: [
        ["🚐 የኪራይ መኪና ማስመዝገብ", "🔎 የኪራይ መኪና መፈለግ"],
        ["🚘 የሽያጭ መኪና ማስመዝገብ", "🔎 የሽያጭ መኪና መፈለግ"],
        ["🚜 የኮንስትራክሽን ማሽነሪ ሽያጭ/ኪራይ", "🔎 ማሽነሪ ፈልግ"],
        ["GPS\n🚘 የኪራይ ክትትል 🌏"],
    ],
    MAIN_FOLDERS[2]: [
        ["🏠 ንብረት ለመሸጥ/ለመግዛት መመዝገብ"],
        ["🔎 ንብረት ፈልግ"],
    ],
    MAIN_FOLDERS[3]: [
        ["🛒 የግዢና የሽያጭ ጨረታ መረጃ ለማግኘት 👈 ተጭነው ይመዝገቡ"],
        ["🏗️ የግንባታ ጨረታ መረጃ ለማግኘት 👈 ተጭነው ይመዝገቡ"],
        ["💼 የሥራ ፍለጋ መረጃ ለማግኘት 👈 ተጭነው ይመዝገቡ"],
    ],
    MAIN_FOLDERS[4]: [],
    MAIN_FOLDERS[5]: [
        ["👥 ጓደኛ ጋብዝ"], ["👤 የኔ መረጃ"],
    ],
}


def folder_menu(folder, user_id=None):
    rows = [list(r) for r in FOLDER_BUTTONS.get(folder, [])]
    if is_admin(user_id) if user_id else False:
        if folder == MAIN_FOLDERS[0]:
            rows.extend([["📚 የክትትል መዝገብ"], ["🤝 የግንኙነት ጥያቄዎች"], ["📍 የGPS ጥያቄዎች"]])
        elif folder == MAIN_FOLDERS[3]:
            rows.extend([["📡 TANA — የሚዲያ ምንጭ ክትትልና ውጫዊ ማጋራት"], ["📢 መረጃ ለደንበኞች ለመላክ"]])
    rows.append(["⬅️ ወደ መጀመሪያ ገፅ"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


PAGE_TWO_BUTTON = "➡️ የመኪናና ማሽነሪ ሽያጭና ኪራይ ለማግኘት 👈 ይጫኑ"
PAGE_ONE_BUTTON = "⬅️ ወደ መጀመሪያ ገፅ"
INFO_MENU_BUTTON = "📢 በየቀኑ የሚወጡ የኮንስትራክሽን፣ የጨረታ መረጃዎችን ለማግኘት 👈ተጭነው ይመዝገቡ"


def main_menu(user_id=None, page=1):
    """Two-page service menu. Admin-only tools stay in a separate menu."""
    admin = bool(user_id and is_admin(user_id))
    if page == 2:
        rows = [
            ["🚐 የኪራይ መኪና ማስመዝገብ", "🔎 የኪራይ መኪና መፈለግ"],
            ["🚘 የሽያጭ መኪና ማስመዝገብ", "🔎 የሽያጭ መኪና መፈለግ"],
            ["🚜 የኮንስትራክሽን ማሽነሪ ሽያጭ/ኪራይ"],
            ["🔎 ማሽነሪ ሽያጭ መፈለግ", "🔎 ማሽነሪ ኪራይ መፈለግ"],
            ["GPS\n🚘 የኪራይ ክትትል 🌏"],
            [PAGE_ONE_BUTTON],
        ]
    else:
        rows = [
            ["🚚 ጭነት መለጠፍ", "🚛 የጭነት መኪና ማስመዝገብ"],
            ["🔎 ጭነት መፈለግ", "🚛 የጭነት መኪና መፈለግ"],
            ["GPS\n🚛 የጭነት ክትትል 🌏"],
            ["👥 ጓደኛ ጋብዝ"],
        ]
        if not admin:
            rows.extend([
                [INFO_MENU_BUTTON],
                [PAGE_TWO_BUTTON],
                ["💳 የአገልግሎት ክፍያ መረጃ", "📞 Support", "ℹ️ About"],
            ])
        else:
            rows.append([PAGE_TWO_BUTTON])
    if admin:
        rows.append(["🛠️ የአስተዳደር ምናሌ"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def size_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 ሙሉ ጭነት 100%", callback_data="size_100")],
        [InlineKeyboardButton("🟡 ግማሽ ጭነት 50%", callback_data="size_50")],
        [InlineKeyboardButton("🟠 እሩብ ጭነት 25%", callback_data="size_25")],
    ])

def vehicle_keyboard(prefix="vehicle"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚛 ተሳቢ", callback_data=f"{prefix}_ተሳቢ")],
        [InlineKeyboardButton("🚛 ካሶኒ", callback_data=f"{prefix}_ካሶኒ")],
        [InlineKeyboardButton("🚛 ኦባማ", callback_data=f"{prefix}_ኦባማ")],
        [InlineKeyboardButton("🚛 Isuzu", callback_data=f"{prefix}_isuzu")],
        [InlineKeyboardButton("✍️ ሌላ", callback_data=f"{prefix}_other")],
    ])

def calendar_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇪🇹 የኢትዮጵያ ካላንደር (EC)", callback_data="cal_et")],
    ])

def route_choice_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🌍 በማንኛውም ቦታ", callback_data="route_anywhere")],
        [InlineKeyboardButton("📍 በርሶ ምርጫ", callback_data="route_custom")],
    ])

# ==================================================
# HELPERS
# ==================================================

def is_user_registered(user_id):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        row = conn.execute("SELECT owner_phone FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row and row[0]:
            return True
    finally:
        conn.close()
    return False

def get_registered_phone(user_id):
    """Return the phone number shared by this user during /start registration."""
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        row = conn.execute("SELECT owner_phone FROM users WHERE user_id = ?", (int(user_id),)).fetchone()
        return (row[0] or "").strip() if row else ""
    finally:
        conn.close()

async def ask_registered_phone_confirmation(update, context, target):
    phone = get_registered_phone(update.effective_user.id)
    context.user_data["phone_confirm_target"] = target
    if not phone:
        await update.effective_message.reply_text(
            "⚠️ የተመዘገበ ስልክ አልተገኘም። የራስዎን contact ያጋሩ ወይም ስልክ ቁጥርዎን ይጻፉ።",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📱 ስልኬን አጋራ", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
        )
        return PHONE_CONFIRM
    context.user_data.setdefault("phone_confirm_values", {})[target] = phone
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ ትክክል ነው", callback_data="phone_ok"), InlineKeyboardButton("✍️ አዲስ ስልክ ጻፍ", callback_data="phone_new")]])
    await update.effective_message.reply_text(f"📞 የተመዘገበ ስልክ ቁጥርዎ፦ {phone}\n\nይህን ስልክ እንጠቀም?", reply_markup=kb)
    return PHONE_CONFIRM

async def continue_after_phone_confirmation(update, context, phone):
    target = context.user_data.pop("phone_confirm_target", "")
    values = context.user_data.pop("phone_confirm_values", {})
    values[target] = phone
    if target == "cargo":
        cargo = context.user_data.setdefault("cargo", {})
        cargo["phone"] = phone
        await update.effective_message.reply_text(
            "📅 የተመረጠው ቀን፦\n"
            f"🇪🇹 ET: {cargo.get('date_et', 'N/A')}\n🌍 GC: {cargo.get('date_gc', 'N/A')}\n\n"
            "9️⃣ የመጫኛ ዋጋ ይጻፉ። በብር ወይም ‘በስምምነት’ ብለው ይጻፉ።"
        )
        return CARGO_PRICE
    if target == "truck":
        return await complete_truck_registration(update, context, phone)
    if target == "vehicle_listing":
        listing = context.user_data.get("vehicle_listing", {})
        listing["phone"] = phone
        photo_status = listing.get("photo_status", "provided" if listing.get("photos") else "missing")
        return await save_vehicle_listing(update, context, photo_status=photo_status)
    if target == "machinery":
        context.user_data.setdefault("machinery", {})["phone"] = phone
        await update.effective_message.reply_text("📷 የማሽነሪውን ፎቶ ይላኩ። ፎቶ ከሌለ ዝለል ብለው ይጻፉ።")
        return MACH_PHOTOS
    if target == "property":
        return await save_property_listing(update, context, phone)
    if target == "broker":
        context.user_data.setdefault("broker", {})["phone"] = phone
        await update.effective_message.reply_text("3️⃣ የሚሰሩበትን አድራሻ ይጻፉ።\nምሳሌ፦ ባህርዳር / ጎንደር / አዲስ አበባ")
        return BROKER_ADDRESS
    if target == "connect_info":
        context.user_data.setdefault("connect_info", {})["phone"] = phone
        await update.effective_message.reply_text("3️⃣ የሚገኙበትን አድራሻ ይጻፉ።\nምሳሌ፦ ባህርዳር / ጎንደር / አዲስ አበባ")
        return CONNECT_TYPE
    if target == "vehicle_buyer":
        buyer = context.user_data.pop("vehicle_buyer", {})
        listing_id = buyer.get("listing_id")
        conn = _db_connect(DB_PATH, check_same_thread=False)
        conn.row_factory = dict_row
        try:
            rec = conn.execute("SELECT * FROM vehicle_listings WHERE id=? AND active=1", (listing_id,)).fetchone()
            rec = dict(rec) if rec else None
        finally:
            conn.close()
        if not rec:
            await update.effective_message.reply_text("❌ ይህ ማስታወቂያ ከአሁን በኋላ አይገኝም።", reply_markup=main_menu(update.effective_user.id))
            return ConversationHandler.END
        buyer_name = buyer.get("name", update.effective_user.full_name)
        buyer_id = update.effective_user.id
        buyer_username = update.effective_user.username or ""
        kind = {"rental": "የኪራይ መኪና", "sale": "የሽያጭ መኪና"}.get(rec.get("category"), "መኪና")
        request_label = "የኪራይ" if rec.get("category") == "rental" else "የግዢ"
        inquiry_id = None
        conn = _db_connect(DB_PATH, timeout=10)
        try:
            cur = conn.execute("""INSERT INTO vehicle_inquiries
                (listing_id,owner_id,buyer_id,buyer_name,buyer_username,buyer_phone,category,vehicle_type,created_at,status)
                VALUES (?,?,?,?,?,?,?,?,?,'new')""",
                (listing_id, int(rec["user_id"]), buyer_id, buyer_name, buyer_username, phone,
                 rec.get("category", ""), rec.get("vehicle_type", ""),
                 datetime.now().astimezone().isoformat(timespec="seconds")))
            inquiry_id = cur.lastrowid
            conn.commit()
        finally:
            conn.close()
        await update.effective_message.reply_text(f"✅ {request_label} ጥያቄዎ ተመዝግቧል። የTANA CARGO, LOGISTICS & INFORMATION SERVICES ቡድን ጥያቄዎን ይከታተላል።", reply_markup=main_menu(buyer_id))
        notice = f"📩 አዲስ {request_label} ጥያቄ\nየጥያቄ መዝገብ #{inquiry_id}\nየማስታወቂያ መዝገብ #{listing_id} — {kind}\nአይነት፦ {rec.get('vehicle_type')}\nፈላጊ፦ {buyer_name}\nስልክ፦ {phone}\nTelegram ID፦ {buyer_id}"
        for aid in get_admin_ids():
            try: await context.bot.send_message(chat_id=aid, text=notice)
            except Exception: pass
        try: await context.bot.send_message(chat_id=rec['user_id'], text=notice + "\n\nየፈላጊውን ደንበኛ በTANA CARGO, LOGISTICS & INFORMATION SERVICES ድጋፍ ቡድን በኩል ያነጋግሩ።")
        except Exception: pass
        return ConversationHandler.END
    return ConversationHandler.END

async def phone_confirm_callback(update, context):
    q = update.callback_query
    await q.answer()
    if q.data == "phone_new":
        await q.message.reply_text(
            "📱 አዲሱን የራስዎን ስልክ ቁጥር ያጋሩ ወይም ይጻፉ።",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📱 ስልኬን አጋራ", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
        )
        return PHONE_CONFIRM
    target = context.user_data.get("phone_confirm_target", "")
    phone = context.user_data.get("phone_confirm_values", {}).get(target) or get_registered_phone(update.effective_user.id)
    if not phone:
        await q.message.reply_text("⚠️ ስልክ አልተገኘም። እባክዎ contact ያጋሩ።")
        return PHONE_CONFIRM
    user = update.effective_user
    try:
        db_save_user(user.id, user.full_name, user.username or "", owner_name=user.full_name, owner_phone=phone)
    except Exception as e:
        print(f"PHONE UPDATE ERROR: {e}")
    users.setdefault(user.id, {"name": user.full_name, "username": user.username or "", "id": user.id})
    users[user.id].setdefault("owner", {})["phone"] = phone
    return await continue_after_phone_confirmation(update, context, phone)

async def phone_confirm_input(update, context):
    if update.message.contact:
        contact = update.message.contact
        if contact.user_id and contact.user_id != update.effective_user.id:
            await update.message.reply_text("❌ የራስዎን contact ብቻ ያጋሩ።")
            return PHONE_CONFIRM
        phone = contact.phone_number
        if phone.startswith("+251"):
            phone = "0" + phone[4:]
        elif phone.startswith("251"):
            phone = "0" + phone[3:]
    else:
        phone = (update.message.text or "").strip().split("\n")[0].strip()
    valid, error = validate_phone(phone)
    if not valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return PHONE_CONFIRM
    target = context.user_data.get("phone_confirm_target", "")
    context.user_data.setdefault("phone_confirm_values", {})[target] = phone
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ ትክክል ነው", callback_data="phone_ok"), InlineKeyboardButton("✍️ እንደገና ጻፍ", callback_data="phone_new")]])
    await update.message.reply_text(f"📞 ያስገቡት ስልክ ቁጥር፦ {phone}\n\nይህ ትክክል ነው?", reply_markup=kb)
    return PHONE_CONFIRM

def sanitize_public_text(value):
    """Hide Ethiopian phone-number patterns accidentally entered in public details."""
    text = str(value or "")
    return re.sub(r"(?<!\d)(?:\+?251|0)(?:9|7)\d{8}(?!\d)", "[ስልክ ተደብቋል]", text)

def normalize(text):
    if text is None:
        return ""
    return str(text).strip().lower().replace(" ", "")

def vehicle_match(truck_type, requested_vehicle):
    if not requested_vehicle:
        return True
    return normalize(truck_type) == normalize(requested_vehicle)

def is_broker(user_id):
    for b in brokers:
        if b.get("user_id") == user_id and b.get("approved") and b.get("active"):
            return True
    return False

def get_poster_label(user_id):
    if is_broker(user_id):
        return "🏷️ Broker"
    return "🏷️ Customer"

def payment_methods_text():
    return (
        "💳 የአገልግሎት ክፍያ መረጃ\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🏦 Commercial Bank of Ethiopia (CBE)\n"
        "🔢 1000031098231\n👤 Solomon Sisay\n\n"
        "📱 Telebirr\n📱 0918132914\n👤 Solomon Sisay\n\n"
        "💰 CBE Birr\n📱 0918132914\n👤 Solomon Sisay\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📌 የክፍያ ጥያቄዎች ካሉዎት በSupport ምናሌ ያግኙን።\n\n"
        "🙏 እኛን ስለመረጡን እናመሰግናለን!"
    )

def other_party_id(req):
    if req.get("type") == "truck":
        return req["truck_owner_id"]
    return req["cargo_owner_id"]

def validate_phone(phone):
    phone = phone.strip()
    if not (phone.startswith("09") or phone.startswith("07")):
        return False, "ስልክ ቁጥሩ በ09 ወይም በ07 መጀመር አለበት።"
    if len(phone) != 10:
        return False, "ስልክ ቁጥሩ 10 ዲጂት መሆን አለበት።"
    if not phone.isdigit():
        return False, "ስልክ ቁጥር ቁጥር ብቻ መሆን አለበት።"
    return True, ""

def validate_plate(plate):
    plate = plate.strip()
    if not plate.isdigit():
        return False, "ታርጋ ቁጥር ብቻ መሆን አለበት።"
    return True, ""

def validate_weight(weight):
    if not any(u in weight for u in ["ቶን", "ቢያጆ", "ኩንታል"]):
        return False, "ክብደቱን በቶን፣ በቢያጆ ወይም በኩንታል ይግለጹ።"
    return True, ""

def is_super_admin(user_id):
    return bool(SUPER_ADMIN_USER_ID) and str(user_id) == str(SUPER_ADMIN_USER_ID)

def is_admin(user_id):
    if is_super_admin(user_id):
        return True
    return bool(ADMIN_USER_ID) and str(user_id) == str(ADMIN_USER_ID)

def get_admin_ids():
    ids = []
    if SUPER_ADMIN_USER_ID:
        ids.append(int(SUPER_ADMIN_USER_ID))
    if ADMIN_USER_ID and str(ADMIN_USER_ID) != str(SUPER_ADMIN_USER_ID):
        ids.append(int(ADMIN_USER_ID))
    return ids

def schedule_delete(chat_id, message_id):
    delete_at = datetime.now() + timedelta(seconds=MESSAGE_AUTO_DELETE_SECONDS)
    pending_deletes.append((chat_id, message_id, delete_at))

async def auto_delete_messages(context):
    now = datetime.now()
    remaining = []
    for chat_id, msg_id, delete_at in pending_deletes:
        if now >= delete_at:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass
        else:
            remaining.append((chat_id, msg_id, delete_at))
    pending_deletes.clear()
    pending_deletes.extend(remaining)

def is_duplicate_broker_post(new_item, is_truck, broker_user_id):
    if is_truck:
        for t in truck_posts:
            if (t.get("active") and t.get("poster_type") == "broker"
                and t.get("plate") == new_item.get("plate")
                and t.get("user_id") != broker_user_id):
                return True
    else:
        for c in cargo_posts:
            if (c.get("active") and c.get("poster_type") == "broker"
                and normalize(c.get("from")) == normalize(new_item.get("from"))
                and normalize(c.get("to")) == normalize(new_item.get("to"))
                and c.get("user_id") != broker_user_id):
                return True
    return False

def is_already_selected(item_type, item_index):
    for req in connection_requests:
        if req.get("type") == item_type:
            key = f"{item_type}_index"
            if (req.get(key) == item_index
                and req.get("status") in ["pending", "admin_confirming", "approved"]):
                return True
    return False

def is_selected_by_other(item_type, item_index, requester_id):
    for req in connection_requests:
        if req.get("type") == item_type:
            key = f"{item_type}_index"
            if (req.get(key) == item_index
                and req.get("requester_id") != requester_id
                and req.get("status") in ["pending", "admin_confirming", "approved"]):
                return True
    return False
# ==================================================
# BROKER LISTING BROADCAST (ADMIN APPROVAL REQUIRED)
# ==================================================

def build_broker_listing_message(kind, data):
    """Build a privacy-safe listing broadcast."""
    lines = ["📢 አዲስ ማስታወቂያ", ""]
    if kind == "cargo":
        lines += [
            f"📍 መነሻ፦ {data.get('from', '—')}",
            f"📍 መድረሻ፦ {data.get('to', '—')}",
            f"📦 የጭነት አይነት፦ {data.get('type', '—')}",
            f"🚛 የሚፈለገው መኪና፦ {data.get('vehicle', '—')}",
            f"📊 መጠን፦ {data.get('size_name', '—')} ({data.get('size', '—')}%)",
            f"⚖️ ክብደት፦ {data.get('weight', '—')}",
            f"📅 ቀን፦ {data.get('date_et', '—')} (ET)",
            f"💰 ዋጋ፦ {data.get('price_display', '—')}",
        ]
    elif kind == "truck":
        lines += [
            f"🚛 የመኪና አይነት፦ {data.get('type', '—')}",
            f"⚖️ የመጫን አቅም፦ {data.get('capacity', '—')}",
        ]
        route_value = (data.get("route") or "").strip()
        if route_value and route_value not in ("—", "-"):
            lines.append(f"🛣️ መንገድ፦ {route_value}")
    else:
        # Future rental, sale and other categories can use public_details.
        for label, value in (data.get("public_details") or {}).items():
            lines.append(f"{label}፦ {value}")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━",
        "ከTANACARGO & LOGISTIC የተላከ",
        "የተላከልዎትን መረጃ ለማየትና ተጨማሪ መረጃ ለማግኘት፦",
        f"📞 {SUPPORT_PHONE}",
        f"Telegram: @{SUPPORT_USERNAME}",
        "TANA CARGO, LOGISTICS & INFORMATION SERVICES | ጣና ጭነት",
    ]
    return "\n".join(lines)


async def queue_broker_listing_broadcast(context, kind, data, photo_id=None):
    """Queue a broker listing for admin approval before broadcasting."""
    global next_listing_broadcast_id
    item_id = next_listing_broadcast_id
    next_listing_broadcast_id += 1

    safe_data = dict(data)
    # Never disclose personal identity, contact, vehicle plate or address.
    for private_key in (
        "user_id", "phone", "plate", "address", "driver",
        "name", "owner_name", "username"
    ):
        safe_data.pop(private_key, None)

    pending_listing_broadcasts[item_id] = {
        "kind": kind,
        "data": safe_data,
        "photo_id": photo_id,
        "poster_id": data.get("user_id"),
        "created_at": datetime.now().isoformat(),
    }
    conn = _db_connect(DB_PATH, timeout=10)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO pending_listing_broadcasts (id,payload,created_at,status) VALUES (?,?,?,'pending')",
            (item_id, json.dumps(pending_listing_broadcasts[item_id], ensure_ascii=False),
             pending_listing_broadcasts[item_id]["created_at"])
        )
        conn.commit()
    finally:
        conn.close()

    preview = build_broker_listing_message(kind, safe_data)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 ለሁሉም ደንበኞች ላክ", callback_data=f"listingall_{item_id}")],
        [InlineKeyboardButton("🏷️ ለደላሎች ብቻ ላክ", callback_data=f"listingbrokers_{item_id}")],
        [InlineKeyboardButton("❌ አትላክ", callback_data=f"listingreject_{item_id}")],
    ])

    for admin_id in get_admin_ids():
        try:
            if photo_id:
                await context.bot.send_photo(
                    chat_id=admin_id, photo=photo_id, caption=preview,
                    reply_markup=keyboard
                )
            else:
                await context.bot.send_message(
                    chat_id=admin_id, text=preview, reply_markup=keyboard
                )
        except Exception as exc:
            print(f"LISTING APPROVAL NOTIFICATION ERROR {admin_id}: {exc}")


async def admin_listing_broadcast_action(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer(
            "ይህን ማድረግ የሚችሉት Admin ብቻ ናቸው።",
            show_alert=True
        )
        return

    action, raw_id = query.data[len("listing"):].split("_", 1)
    item_id = int(raw_id)
    item = pending_listing_broadcasts.get(item_id)
    if not item:
        conn = _db_connect(DB_PATH, timeout=10)
        try:
            row = conn.execute("SELECT payload FROM pending_listing_broadcasts WHERE id=? AND status='pending'", (item_id,)).fetchone()
            item = json.loads(row[0]) if row else None
            if item:
                pending_listing_broadcasts[item_id] = item
        except (TypeError, ValueError, json.JSONDecodeError):
            item = None
        finally:
            conn.close()
    if not item:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if action == "reject":
        pending_listing_broadcasts.pop(item_id, None)
        conn = _db_connect(DB_PATH, timeout=10)
        try:
            conn.execute("UPDATE pending_listing_broadcasts SET status='rejected' WHERE id=?", (item_id,))
            conn.commit()
        finally:
            conn.close()
        if query.message.photo:
            await query.edit_message_caption(caption="❌ ማስታወቂያው አልተላከም።")
        else:
            await query.edit_message_text("❌ ማስታወቂያው አልተላከም።")
        return

    message = build_broker_listing_message(item["kind"], item["data"])
    sent, failed = 0, 0
    if action == "all":
        recipients = {int(u["user_id"]) for u in db_list_registered_users() if u.get("user_id")}
    elif action == "brokers":
        recipients = {
            int(b.get("user_id")) for b in brokers
            if b.get("user_id") and b.get("approved") and b.get("active", True)
        }
    else:
        await query.answer("የመላኪያ አድማጭ አልተመረጠም።", show_alert=True)
        return
    for recipient_id in recipients:
        try:
            if item.get("photo_id"):
                await context.bot.send_photo(
                    chat_id=recipient_id, photo=item["photo_id"],
                    caption=message
                )
            else:
                await context.bot.send_message(
                    chat_id=recipient_id, text=message
                )
            sent += 1
        except Exception as exc:
            failed += 1
            print(f"LISTING BROADCAST FAILED user={recipient_id}: {exc}")

    pending_listing_broadcasts.pop(item_id, None)
    conn = _db_connect(DB_PATH, timeout=10)
    try:
        conn.execute("UPDATE pending_listing_broadcasts SET status='sent' WHERE id=?", (item_id,))
        conn.commit()
    finally:
        conn.close()
    result = (
        f"✅ ማስታወቂያው ተልኳል።\n"
        f"📨 ተሳክቶ የተላከ፦ {sent}\n"
        f"⚠️ ያልተሳካ፦ {failed}"
    )
    if query.message.photo:
        await query.edit_message_caption(caption=result)
    else:
        await query.edit_message_text(result)


# ==================================================
# CARGO POSTING
# ==================================================

async def cargo_start(update, context):
    context.user_data.pop("truck", None)
    context.user_data.pop("broker", None)
    context.user_data.pop("owner", None)
    context.user_data.pop("connect_info", None)
    context.user_data["cargo"] = {}
    await update.message.reply_text(
        "🚚 ጭነት መለጠፍ\n\n1️⃣ ጭነቱ የሚነሳበትን ቦታ ይጻፉ።\nምሳሌ፦ ባህር ዳር"
    )
    return CARGO_FROM

async def cargo_from(update, context):
    context.user_data["cargo"]["from"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የሚደርስበትን ቦታ ይጻፉ።")
    return CARGO_TO

async def cargo_to(update, context):
    context.user_data["cargo"]["to"] = update.message.text.strip()
    await update.message.reply_text("3️⃣ የጭነቱን አይነት ይጻፉ።\nምሳሌ፦ ሲሚንቶ / እህል / ፍራሽ")
    return CARGO_TYPE

async def cargo_type(update, context):
    context.user_data["cargo"]["type"] = update.message.text.strip()
    await update.message.reply_text(
        "4️⃣ ለዚህ ጭነት የሚፈልጉትን የመኪና አይነት ይምረጡ።",
        reply_markup=vehicle_keyboard("cargo_vehicle")
    )
    return CARGO_VEHICLE

async def cargo_vehicle(update, context):
    query = update.callback_query
    await query.answer()
    value = query.data.replace("cargo_vehicle_", "")
    if value == "other":
        await query.edit_message_text("✍️ የሚፈልጉትን የመኪና አይነት ይጻፉ።")
        context.user_data["cargo"]["vehicle_waiting"] = True
        return CARGO_VEHICLE
    context.user_data["cargo"]["vehicle"] = value
    await query.edit_message_text(
        f"🚛 የተመረጠው መኪና፦ {value}\n\n5️⃣ የጭነቱን መጠን ይምረጡ።",
        reply_markup=size_keyboard()
    )
    return CARGO_SIZE

async def cargo_vehicle_text(update, context):
    cargo = context.user_data["cargo"]
    if not cargo.get("vehicle_waiting"):
        return
    cargo["vehicle"] = update.message.text.strip()
    cargo["vehicle_waiting"] = False
    await update.message.reply_text(
        f"🚛 የተፈለገው መኪና፦ {cargo['vehicle']}\n\n5️⃣ የጭነቱን መጠን ይምረጡ።",
        reply_markup=size_keyboard()
    )
    return CARGO_SIZE

async def cargo_size(update, context):
    query = update.callback_query
    await query.answer()
    sizes = {
        "size_100": (100, "ሙሉ ጭነት"),
        "size_50": (50, "ግማሽ ጭነት"),
        "size_25": (25, "እሩብ ጭነት"),
    }
    if query.data not in sizes:
        return CARGO_SIZE
    size, size_name = sizes[query.data]
    context.user_data["cargo"]["size"] = size
    context.user_data["cargo"]["size_name"] = size_name
    await query.edit_message_text(
        f"📦 የተመረጠው፦ {size_name} ({size}%)\n\n"
        "6️⃣ የጭነቱን ክብደት ይግለጹ።\n"
        "በቶን፣ በቢያጆ፣ በኩንታል\nምሳሌ፦ 5 ቶን / 10 ቢያጆ / 10 ኩንታል"
    )
    return CARGO_WEIGHT

async def cargo_weight(update, context):
    weight = update.message.text.strip()
    is_valid, error = validate_weight(weight)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 5 ቶን / 10 ቢያጆ / 10 ኩንታል")
        return CARGO_WEIGHT
    context.user_data["cargo"]["weight"] = weight
    await update.message.reply_text(
        "7️⃣ **እባክዎ የሚጫንበትን ቀን ካላንደር ይምረጡ።**\n\n"
        "የሚጠቀሙበትን ካላንደር ይምረጡ፦",
        reply_markup=calendar_keyboard()
    )
    return CARGO_CALENDAR

async def cargo_calendar(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "cal_et":
        context.user_data["cargo"]["calendar"] = "ET"
        await query.edit_message_text(
            "🇪🇹 **የኢትዮጵያ ካላንደር ተመርጧል።**\n\n"
            "እባክዎ ቀኑን በዚህ ቅርጸት ይጻፉ፦ ቀን/ወር/ዓመት\n"
            "ምሳሌ፦ 26/1/2019 ወይም 26-1-2019 ወይም 26,1,2019"
        )
    else:
        return CARGO_CALENDAR
    return CARGO_DATE

async def cargo_date(update, context):
    date_text = update.message.text.strip()
    calendar = context.user_data.get("cargo", {}).get("calendar", "ET")
    date_parts, error = parse_flexible_date(date_text)
    if error:
        await update.message.reply_text(f"❌ {error}\n\nእባክዎ ቀኑን በቀን/ወር/ዓመት ቅርጽ እንደገና ያስገቡ።")
        return CARGO_DATE
    et_date, gc_date, error = convert_calendar(date_parts, calendar)
    if error:
        await update.message.reply_text(f"❌ {error}\n\nእባክዎ ትክክለኛውን ቀን ያስገቡ።")
        return CARGO_DATE
    try:
        gc_obj = datetime.strptime(gc_date, "%d/%m/%Y")
        today = datetime.now(ZoneInfo("Africa/Addis_Ababa")).date()
        given_date = gc_obj.date()
    except (ValueError, TypeError):
        await update.message.reply_text("❌ የቀን ስሌት ስህተት። እባክዎ ቀኑን እንደገና ያስገቡ።")
        return CARGO_DATE
    if given_date < today:
        await update.message.reply_text(
            "❌ ያለፈ ቀን አይቀበልም።\n\n"
            "ዛሬን ወይም ወደፊት ያለ ቀን በኢትዮጵያ ካላንደር (ቀን/ወር/ዓ.ም.) ያስገቡ።\n"
            "ምሳሌ፦ 11/02/2019"
        )
        return CARGO_DATE
    max_date = today + timedelta(days=30)
    if given_date > max_date:
        await update.message.reply_text(
            "❌ ከ30 ቀናት በላይ የሆነ ቀን አይቀበልም።\n\n"
            "እባክዎ ዛሬን ወይም በሚቀጥሉት 30 ቀናት ውስጥ ያለ ቀን ያስገቡ።"
        )
        return CARGO_DATE
    cargo = context.user_data.setdefault("cargo", {})
    cargo["date"] = date_text
    cargo["date_et"] = et_date
    cargo["date_gc"] = gc_date
    cargo["expiry_date"] = (gc_obj + timedelta(hours=CARGO_EXPIRY_HOURS)).isoformat()
    return await ask_registered_phone_confirmation(update, context, "cargo")

async def cargo_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return CARGO_PHONE
    context.user_data["cargo"]["phone"] = phone
    await update.message.reply_text(
        "9️⃣ የመጫኛ ዋጋ ይጻፉ።\n"
        "በብር ብቻ ወይም **በስምምነት** ብለው ይጻፉ።\n"
        "ምሳሌ፦ 55000 ወይም በስምምነት"
    )
    return CARGO_PRICE

async def cargo_price(update, context):
    raw = update.message.text.strip().split("\n")[0].strip()
    if raw in ["በስምምነት", "ስምምነት"]:
        price = None
        price_status = "agreement"
        price_display = "በስምምነት"
    else:
        try:
            price = float(raw.replace(",", "").replace("ብር", "").strip())
            price_status = "fixed"
            price_display = f"{price:,.2f} ብር"
        except ValueError:
            await update.message.reply_text(
                "❌ እባክዎ ዋጋውን በቁጥር ወይም 'በስምምነት' ብለው ይጻፉ።\n"
                "ምሳሌ፦ 55000 ወይም በስምምነት"
            )
            return CARGO_PRICE
        if price <= 0:
            await update.message.reply_text("❌ ዋጋው ከ0 በላይ መሆን አለበት።")
            return CARGO_PRICE
    cargo = context.user_data["cargo"]
    cargo["price"] = price
    cargo["price_status"] = price_status
    cargo["price_display"] = price_display
    cargo["user_id"] = update.effective_user.id
    cargo["active"] = True
    cargo["timestamp"] = datetime.now().isoformat()
    cargo["poster_type"] = "broker" if is_broker(update.effective_user.id) else "customer"

    if cargo["poster_type"] == "broker":
        if is_duplicate_broker_post(cargo, is_truck=False,
                                     broker_user_id=update.effective_user.id):
            await update.message.reply_text(
                "❌ **ይቅርታ ይህንን ጭነት ሌላ ደላላ (broker) ለጥፎታል።**\n\n"
                "እባክዎ ሌላ ጭነት ካለዎት ይለጥፉ።"
            )
            return CARGO_PRICE

    cargo_posts.append(cargo.copy())
    try:
        cargo_id = db_save_cargo(cargo)
        cargo_posts[-1]["id"] = cargo_id
    except Exception as e:
        print(f"DB SAVE CARGO ERROR: {e}")

    await queue_broker_listing_broadcast(context, "cargo", cargo)

    owner_name = users.get(update.effective_user.id, {}).get(
        "name", update.effective_user.full_name
    )
    poster_label = get_poster_label(update.effective_user.id)

    await update.message.reply_text(
        "✅ ጭነትዎ በትክክል ተመዝግቧል!\n\n"
        f"👤 የጭነት ባለቤት፦ {owner_name}\n"
        f"{poster_label}\n"
        f"📍 መነሻ፦ {cargo['from']}\n"
        f"📍 መድረሻ፦ {cargo['to']}\n"
        f"📦 አይነት፦ {cargo['type']}\n"
        f"🚛 የሚፈለገው መኪና፦ {cargo['vehicle']}\n"
        f"📊 መጠን፦ {cargo['size_name']} ({cargo['size']}%)\n"
        f"⚖️ ክብደት፦ {cargo['weight']}\n"
        f"📅 ቀን (ET)፦ {cargo.get('date_et', 'N/A')}\n"
        f"📅 ቀን (GC)፦ {cargo.get('date_gc', 'N/A')}\n"
        f"💰 የመጫኛ ዋጋ፦ {price_display}\n\n"
        "🔒 የስልክ ቁጥርዎ ለሌሎች ተጠቃሚዎች አይታይም።\n\n"
        "ውድ ደንበኛች የጭነተዎን መዳረሻ በgoogle map ለመከታተል የስልክዎን location & የኢንተርኔት ዳታዎን አብርተው ከታች👇 የጭነት ክትትል የምትለዋን ምናሌ ተጭነው የኔን ጭነት መከታተያ ኮድ ጠይቅ(ደንበኛ) የምትለውን ተጭነው መመሪያውን በመከተል ጭነተዎን በጎግል ማፕ ይከታተሉ። የበለጠ መረጃ ለማግኘት 👉 @tanacargopage ይጠቀሙ።",
        reply_markup=main_menu(update.effective_user.id)
    )

    for truck in truck_posts:
        if not truck.get("active", True):
            continue
        if vehicle_match(truck.get("type"), cargo.get("vehicle")):
            try:
                await context.bot.send_message(
                    chat_id=truck["user_id"],
                    text=(
                        "🔔 ተስማሚ አዲስ ጭነት ተገኝቷል!\n\n"
                        f"📍 {cargo['from']} ➡️ {cargo['to']}\n"
                        f"📦 {cargo['type']}\n"
                        f"🚛 የሚፈለገው፦ {cargo['vehicle']}\n"
                        f"📊 {cargo['size_name']}\n"
                        f"⚖️ {cargo['weight']}\n"
                        f"📅 {cargo.get('date_et', 'N/A')} (ET)\n"
                        f"💰 {price_display}\n\n"
                        "🔎 ለማየት የጭነት መፈለግን ይጫኑ።"
                    )
                )
            except Exception:
                pass

    return ConversationHandler.END
# ==================================================
# TRUCK REGISTRATION
# ==================================================

async def truck_start(update, context):
    context.user_data.pop("cargo", None)
    context.user_data.pop("broker", None)
    context.user_data.pop("owner", None)
    context.user_data.pop("connect_info", None)
    context.user_data["truck"] = {}
    await update.message.reply_text(
        "🚛 የጭነት መኪና ማስመዝገብ\n\n1️⃣ የመኪናውን አይነት ይጻፉ።\nምሳሌ፦ Isuzu / ካሶኒ / ኦባማ"
    )
    return TRUCK_TYPE

async def truck_type(update, context):
    context.user_data["truck"]["type"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የመኪናውን ታርጋ ይጻፉ።\nቁጥር ብቻ መሆን አለበት።\nምሳሌ፦ 12345")
    return TRUCK_PLATE

async def truck_plate(update, context):
    plate = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_plate(plate)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 12345")
        return TRUCK_PLATE
    context.user_data["truck"]["plate"] = plate
    await update.message.reply_text("3️⃣ የመጫን አቅምና የጭነት አይነት ይግለጹ።\nምሳሌ፦ 30 ቶን / 200 ኩንታል / 50 ቢያጆ")
    return TRUCK_CAPACITY

async def truck_capacity(update, context):
    capacity = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_weight(capacity)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 30 ቶን / 200 ኩንታል / 50 ቢያጆ")
        return TRUCK_CAPACITY
    context.user_data["truck"]["capacity"] = capacity
    context.user_data["truck"]["route"] = "—"
    await update.message.reply_text("4️⃣ የመኪናውን አድራሻ/የሚገኝበትን ቦታ ይጻፉ።")
    return TRUCK_ADDRESS

async def truck_route_choice(update, context):
    query = update.callback_query
    await query.answer()
    if query.data == "route_anywhere":
        context.user_data["truck"]["route"] = "በማንኛውም ቦታ"
        context.user_data["truck"]["route_type"] = "anywhere"
        await query.edit_message_text("✅ **በማንኛውም ቦታ** ተመርጧል።\n\n5️⃣ የመኪናውን አድራሻ ይጻፉ።")
        return TRUCK_ADDRESS
    elif query.data == "route_custom":
        context.user_data["truck"]["route_type"] = "custom"
        await query.edit_message_text("📍 **በርሶ ምርጫ** ተመርጧል።\n\nለምሳሌ፦ ጅማ → ባህርዳር → አዲስ አበባ")
        return TRUCK_ROUTE
    return TRUCK_ROUTE

async def truck_route(update, context):
    context.user_data["truck"]["route"] = update.message.text.strip()
    await update.message.reply_text("5️⃣ የመኪናውን አድራሻ ይጻፉ።")
    return TRUCK_ADDRESS

async def truck_address(update, context):
    context.user_data["truck"]["address"] = update.message.text.strip()
    await update.message.reply_text("6️⃣ የሹፌሩን ስም ይጻፉ።")
    return TRUCK_DRIVER

async def truck_driver(update, context):
    context.user_data["truck"]["driver"] = update.message.text.strip()
    return await ask_registered_phone_confirmation(update, context, "truck")

async def truck_phone(update, context):
    # Backward-compatible handler for users entering this legacy state.
    phone = get_registered_phone(update.effective_user.id)
    if not phone:
        await update.message.reply_text("⚠️ /start በመጫን የራስዎን contact ያጋሩ።")
        return TRUCK_PHONE
    return await complete_truck_registration(update, context, phone)

async def complete_truck_registration(update, context, phone):
    truck = context.user_data["truck"]
    truck["phone"] = phone
    truck["user_id"] = update.effective_user.id
    truck["active"] = True
    truck["timestamp"] = datetime.now().isoformat()
    truck["poster_type"] = "broker" if is_broker(update.effective_user.id) else "customer"
    expiry_date = datetime.now() + timedelta(hours=CARGO_EXPIRY_HOURS)
    truck["expiry_date"] = expiry_date.isoformat()

    if truck["poster_type"] == "broker" and is_duplicate_broker_post(
        truck, is_truck=True, broker_user_id=update.effective_user.id
    ):
        await update.effective_message.reply_text("❌ ይህንን መኪና ቀድሞ አስመዝግበዋል።")
        return TRUCK_DRIVER

    truck_posts.append(truck.copy())
    try:
        truck_id = db_save_truck(truck)
        truck_posts[-1]["id"] = truck_id
    except Exception as e:
        print(f"DB SAVE TRUCK ERROR: {e}")
    await queue_broker_listing_broadcast(context, "truck", truck)

    owner_name = users.get(update.effective_user.id, {}).get("name", update.effective_user.full_name)
    poster_label = get_poster_label(update.effective_user.id)
    await update.effective_message.reply_text(
        "✅ መኪናዎ በትክክል ተመዝግቧል!\n\n"
        f"👤 የመኪና ባለቤት፦ {owner_name}\n{poster_label}\n"
        f"🚛 አይነት፦ {truck['type']}\n🔢 ታርጋ፦ {truck['plate']}\n"
        f"⚖️ አቅም፦ {truck['capacity']}\n"
        f"📍 አድራሻ፦ {truck['address']}\n👨‍✈️ ሹፌር፦ {truck['driver']}\n"
        f"📱 በTANA CARGO, LOGISTICS & INFORMATION SERVICES የተመዘገበ ኮንታክት፦ {phone}\n\n"
        "🔒 ታርጋና ስልክ ቁጥር ለሌሎች ተጠቃሚዎች አይታይም።\n\n"
        "ጉዞ ሲጀምሩ GPS/Location ያብሩ፤ ጉዞው ሲጠናቀቅ ያጥፉ።\n"
        "ለበለጠ መረጃ 👉 @tanacargopage ይጠቀሙ።",
        reply_markup=main_menu(update.effective_user.id)
    )

    route_display = (truck.get("route") or "").strip()
    route_line = f"🛣️ መንገድ፦ {route_display}\n" if route_display and route_display not in ("—", "-") else ""
    for cargo in cargo_posts:
        if cargo.get("active", True) and vehicle_match(truck.get("type"), cargo.get("vehicle")):
            try:
                await context.bot.send_message(
                    chat_id=cargo["user_id"],
                    text=("🔔 ተስማሚ የጭነት መኪና ተገኝቷል!\n\n"
                          f"🚛 አይነት፦ {truck['type']}\n⚖️ አቅም፦ {truck['capacity']}\n"
                          f"{route_line}📍 አድራሻ፦ {truck['address']}\n\n"
                          "🚛 መኪና መፈለግን ይጫኑ።")
                )
            except Exception:
                pass
    return ConversationHandler.END

async def show_vehicle_registry(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Admin ብቻ ይህን መዝገብ ማየት ይችላል።")
        return
    conn = _db_connect(DB_PATH, check_same_thread=False)
    conn.row_factory = dict_row
    try:
        rows = conn.execute("SELECT * FROM vehicle_listings WHERE category IN ('rental','sale') ORDER BY id DESC LIMIT 30").fetchall()
        records = [dict(r) for r in rows]
    finally:
        conn.close()
    if not records:
        await update.message.reply_text("🗂️ የኪራይና የሽያጭ መኪና መዝገብ ባዶ ነው።")
        return
    show_private = get_admin_pii_visibility(update.effective_user.id)
    for rec in records:
        cat = {"rental": "ኪራይ መኪና", "sale": "ሽያጭ መኪና"}.get(rec.get("category"), "መኪና")
        status = "ንቁ" if rec.get("active") else "ተዘግቷል"
        owner_display = rec.get("owner_name") or "—" if show_private else "የተደበቀ"
        plate_display = rec.get("plate") or "—" if show_private else "የተደበቀ"
        phone_display = rec.get("phone") or "—" if show_private else "የተደበቀ"
        username_display = users.get(int(rec.get("user_id") or 0), {}).get("username") or "የለም" if show_private else "የተደበቀ"
        msg = (f"🗂️ መዝገብ #{rec['id']} — {cat}\n🚘 {rec.get('vehicle_type')}\n📍 {rec.get('location')}\n"
               f"📝 {rec.get('details')}\n💰 {rec.get('price')}\n👤 {owner_display}\n🔗 @{username_display}\n🔢 ታርጋ፦ {plate_display}\n📱 {phone_display}\n"
               f"🆔 User ID: {rec.get('user_id') if show_private else 'የተደበቀ'}\n📷 {rec.get('photo_status')}\nሁኔታ: {status}")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ ዝርዝር አስተካክል", callback_data=f"vehicleedit_{rec['id']}")],
            [InlineKeyboardButton("🗑️ መዝገቡን ሰርዝ", callback_data=f"vehicledelete_{rec['id']}")]
        ])
        await update.message.reply_text(msg, reply_markup=kb)
        try:
            photo_ids = json.loads(rec.get("photo_ids") or "[]")
            for photo_id in photo_ids[:2]:
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=photo_id, caption=f"📷 መዝገብ #{rec['id']}")
        except Exception as e:
            print(f"Admin vehicle photo display failed: {e}")

async def admin_vehicle_edit_start(update, context):
    q = update.callback_query
    await q.answer()
    if not is_admin(update.effective_user.id):
        await q.answer("Admin ብቻ።", show_alert=True)
        return
    rid = int(q.data.split("_")[-1])
    context.user_data["vehicle_edit_id"] = rid
    await q.message.reply_text("ምን ማስተካከል ይፈልጋሉ?", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("🚘 አይነት/ሞዴል", callback_data=f"vehiclefield_vehicle_type_{rid}")],
        [InlineKeyboardButton("📍 ቦታ", callback_data=f"vehiclefield_location_{rid}")],
        [InlineKeyboardButton("📝 ተጨማሪ መረጃ", callback_data=f"vehiclefield_details_{rid}")],
        [InlineKeyboardButton("💰 ዋጋ", callback_data=f"vehiclefield_price_{rid}")],
        [InlineKeyboardButton("🔢 ታርጋ", callback_data=f"vehiclefield_plate_{rid}")],
        [InlineKeyboardButton("👤 ባለቤት ስም", callback_data=f"vehiclefield_owner_name_{rid}")],
        [InlineKeyboardButton("📞 ስልክ", callback_data=f"vehiclefield_phone_{rid}")]
    ]))

async def admin_vehicle_edit_field(update, context):
    q = update.callback_query
    await q.answer()
    if not is_admin(update.effective_user.id):
        await q.answer("Admin ብቻ።", show_alert=True)
        return
    field_part, rid_part = q.data.rsplit("_", 1)
    field = field_part[len("vehiclefield_"):]
    rid = int(rid_part)
    context.user_data["vehicle_edit_id"] = rid
    context.user_data["vehicle_edit_field"] = field
    prompts = {
        "vehicle_type": "አዲሱን የተሽከርካሪ አይነት/ሞዴል ይጻፉ።",
        "location": "አዲሱን ቦታ ይጻፉ።",
        "details": "አዲሱን ተጨማሪ መረጃ ይጻፉ።",
        "price": "አዲሱን ዋጋ ይጻፉ።",
        "plate": "አዲሱን ታርጋ ይጻፉ።",
        "owner_name": "አዲሱን የባለቤት ሙሉ ስም ይጻፉ።",
        "phone": "አዲሱን ስልክ ቁጥር ይጻፉ።"
    }
    await q.message.reply_text(prompts.get(field, "አዲሱን መረጃ ይጻፉ።"))

async def admin_vehicle_edit_save(update, context):
    if not is_admin(update.effective_user.id):
        context.user_data.pop("vehicle_edit_id", None)
        context.user_data.pop("vehicle_edit_field", None)
        return
    rid = context.user_data.get("vehicle_edit_id")
    field = context.user_data.get("vehicle_edit_field")
    allowed_fields = {"details", "price", "vehicle_type", "location", "plate", "owner_name", "phone"}
    if not rid or field not in allowed_fields:
        return
    value = update.message.text.strip()
    if not value:
        await update.message.reply_text("❌ ባዶ መረጃ አይቀበልም።")
        return
    if field == "phone":
        valid, error = validate_phone(value)
        if not valid:
            await update.message.reply_text(f"❌ {error}")
            return
    if field == "plate" and len(value) < 3:
        await update.message.reply_text("❌ ታርጋው ቢያንስ 3 ቁምፊ መሆን አለበት።")
        return
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        cur = conn.execute(f"UPDATE vehicle_listings SET {field}=? WHERE id=?", (value, int(rid)))
        conn.commit()
    finally:
        conn.close()
    context.user_data.pop("vehicle_edit_id", None)
    context.user_data.pop("vehicle_edit_field", None)
    await update.message.reply_text("✅ የመኪና መዝገቡ ተስተካክሏል።", reply_markup=main_menu(update.effective_user.id))

async def admin_vehicle_delete(update, context):
    q = update.callback_query
    await q.answer()
    if not is_admin(update.effective_user.id):
        await q.answer("Admin ብቻ።", show_alert=True)
        return
    rid = int(q.data.split("_")[-1])
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        conn.execute("UPDATE vehicle_listings SET active=0 WHERE id=?", (rid,))
        conn.commit()
    finally:
        conn.close()
    await q.edit_message_text(f"✅ የመኪና መዝገብ #{rid} ተዘግቷል/ተሰርዟል።")

# ==================================================
# RENTAL / FOR-SALE VEHICLE LISTINGS
# ==================================================

VEHICLE_PHOTO_DONE = "vehicle_photo_done"
VEHICLE_PHOTO_NONE = "vehicle_photo_none"

async def vehicle_listing_start(update, context, category):
    context.user_data["vehicle_listing"] = {"category": category, "photos": []}
    label = "የኪራይ" if category == "rental" else "የሽያጭ"
    await update.message.reply_text(f"{label} መኪና ማስመዝገብ\n\n1️⃣ የመኪናውን አይነት/ሞዴል ይጻፉ።")
    return VEHICLE_LIST_TYPE

async def vehicle_listing_type(update, context):
    context.user_data["vehicle_listing"]["vehicle_type"] = update.message.text.strip()
    await update.message.reply_text("2️⃣ የመኪናውን ታርጋ ቁጥር ይጻፉ።")
    return VEHICLE_LIST_PLATE

async def vehicle_listing_plate(update, context):
    plate = (update.message.text or "").strip()
    if len(plate) < 3:
        await update.message.reply_text("❌ እባክዎ ትክክለኛ ታርጋ ቁጥር ያስገቡ።")
        return VEHICLE_LIST_PLATE
    context.user_data["vehicle_listing"]["plate"] = plate
    await update.message.reply_text("3️⃣ መኪናው የሚገኝበትን ከተማ/ቦታ ይጻፉ።")
    return VEHICLE_LIST_LOCATION

async def vehicle_listing_location(update, context):
    context.user_data["vehicle_listing"]["location"] = update.message.text.strip()
    await update.message.reply_text("4️⃣ ስለ መኪናው ተጨማሪ መረጃ ይጻፉ።\nምሳሌ፦ የሞዴል ዓመት፣ ሁኔታ፣ የሞተር አይነት።")
    return VEHICLE_LIST_DETAILS

async def vehicle_listing_details(update, context):
    listing = context.user_data["vehicle_listing"]
    listing["details"] = update.message.text.strip()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📷 ፎቶ የለኝም", callback_data=VEHICLE_PHOTO_NONE)]])
    await update.message.reply_text(
        "5️⃣ የመኪናውን 1 ወይም 2 ፎቶ ይላኩ።\n"
        "🔒 ታርጋው በህዝብ ፍለጋ እንዳይታይ በፎቶው ላይ ይሸፍኑት።\n"
        "ፎቶ ከሌለዎት ከታች ያለውን አማራጭ ይጫኑ።",
        reply_markup=kb
    )
    return VEHICLE_LIST_PHOTOS

async def vehicle_listing_ask_price(update, context):
    listing = context.user_data.get("vehicle_listing", {})
    label = "የኪራይ ዋጋ" if listing.get("category") == "rental" else "የሽያጭ ዋጋ"
    await update.effective_message.reply_text(
        f"6️⃣ {label} ይጻፉ።\n"
        "ምሳሌ፦ በቀን 500 ብር ወይም በወር 30,000 ብር" if listing.get("category") == "rental" else
        f"6️⃣ {label} ይጻፉ።\nምሳሌ፦ 1,500,000 ብር"
    )
    return VEHICLE_LIST_PRICE

async def vehicle_listing_price(update, context):
    listing = context.user_data["vehicle_listing"]
    price = (update.message.text or "").strip()
    if not price:
        await update.message.reply_text("❌ እባክዎ ዋጋውን ያስገቡ።")
        return VEHICLE_LIST_PRICE
    listing["price"] = price
    await update.message.reply_text("7️⃣ የተሽከርካሪው ባለቤት ሙሉ ስም ይጻፉ።")
    return VEHICLE_LIST_OWNER

async def vehicle_listing_owner(update, context):
    owner_name = (update.message.text or "").strip()
    if len(owner_name) < 2:
        await update.message.reply_text("እባክዎ የባለቤቱን ሙሉ ስም ያስገቡ።")
        return VEHICLE_LIST_OWNER
    context.user_data["vehicle_listing"]["owner_name"] = owner_name
    return await ask_registered_phone_confirmation(update, context, "vehicle_listing")

async def vehicle_listing_photo(update, context):
    listing = context.user_data.get("vehicle_listing", {})
    photos = listing.setdefault("photos", [])
    if not update.message.photo:
        await update.message.reply_text("❌ እባክዎ የመኪናውን ፎቶ ይላኩ ወይም ‘ፎቶ የለኝም’ የሚለውን ይጫኑ።")
        return VEHICLE_LIST_PHOTOS
    if len(photos) >= 2:
        await update.message.reply_text("ከ2 ፎቶ በላይ መላክ አይቻልም።")
        return VEHICLE_LIST_PHOTOS
    photos.append(update.message.photo[-1].file_id)
    listing["photo_status"] = "provided"
    if len(photos) == 1:
        await update.message.reply_text(
            "✅ 1ኛ ፎቶ ተቀብለናል። 2ኛ ፎቶ ይላኩ ወይም ከታች ያለውን ‘ፎቶ ጨርሻለሁ’ ይጫኑ።",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("ፎቶ ጨርሻለሁ", callback_data=VEHICLE_PHOTO_DONE)]])
        )
        return VEHICLE_LIST_PHOTOS
    return await vehicle_listing_ask_price(update, context)

async def vehicle_listing_photo_callback(update, context):
    q = update.callback_query
    await q.answer()
    listing = context.user_data.get("vehicle_listing", {})
    if q.data == VEHICLE_PHOTO_NONE:
        listing["photo_status"] = "missing"
        listing["photos"] = []
    else:
        listing["photo_status"] = "provided"
    return await vehicle_listing_ask_price(update, context)

async def save_vehicle_listing(update, context, photo_status):
    listing = context.user_data.get("vehicle_listing", {})
    uid = update.effective_user.id
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        cur = conn.execute(
            "INSERT INTO vehicle_listings (user_id,category,vehicle_type,location,details,price,phone,photo_ids,photo_status,active,created_at,owner_name,plate) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, listing.get("category"), listing.get("vehicle_type"), listing.get("location"), listing.get("details"), listing.get("price"), listing.get("phone"), json.dumps(listing.get("photos", [])), photo_status, 1, datetime.now().astimezone().isoformat(timespec="seconds"), listing.get("owner_name"), listing.get("plate"))
        )
        listing_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    public_kind = "የኪራይ መኪና" if listing.get("category") == "rental" else "የሽያጭ መኪና"
    await queue_broker_listing_broadcast(context, "vehicle", {
        "user_id": uid, "poster_type": "broker" if is_broker(uid) else "customer",
        "public_details": {"🚘 አይነት": listing.get("vehicle_type"), "📍 ቦታ": listing.get("location"),
                           "📝 ዝርዝር": sanitize_public_text(listing.get("details")), "💰 ዋጋ": listing.get("price"),
                           "📌 ምድብ": public_kind}
    }, photo_id=(listing.get("photos") or [None])[0])
    cat_label = "የኪራይ" if listing.get("category") == "rental" else "የሽያጭ"
    photo_label = "ፎቶ ተቀምጧል" if photo_status == "provided" else "ፎቶ የለም — ለጊዜው"
    msg = (f"✅ {cat_label} መኪናዎ ተመዝግቧል።\n\n🚘 {listing.get('vehicle_type')}\n📍 {listing.get('location')}\n"
           f"📝 {listing.get('details')}\n💰 {listing.get('price')}\n📷 {photo_label}\n"
           f"📱 የተመዘገበ ኮንታክት፦ {listing.get('phone')}\n🔒 ስልክ ቁጥሩ በህዝብ ፍለጋ አይታይም።")
    if hasattr(update, "callback_query") and update.callback_query:
        await update.callback_query.message.reply_text(msg, reply_markup=main_menu(uid))
    else:
        await update.message.reply_text(msg, reply_markup=main_menu(uid))
    for aid in get_admin_ids():
        try:
            admin_caption = (f"🆕 አዲስ {cat_label} መኪና ተመዝግቧል።\nመዝገብ #{listing_id}\n"
                f"🚘 {listing.get('vehicle_type')}\n📍 {listing.get('location')}\n📝 {listing.get('details')}\n"
                f"💰 {listing.get('price')}\n👤 {listing.get('owner_name')}\n🔢 ታርጋ፦ {listing.get('plate')}\n🆔 {uid}\n📱 {listing.get('phone')}\n"
                f"📷 {photo_label}")
            if photo_status == "missing":
                await context.bot.send_message(chat_id=aid, text="⚠️ የፎቶ ማስረጃ የሌለው መዝገብ!\n" + admin_caption)
            elif listing.get("photos"):
                await context.bot.send_photo(chat_id=aid, photo=listing["photos"][0], caption=admin_caption)
                if len(listing["photos"]) > 1:
                    await context.bot.send_photo(chat_id=aid, photo=listing["photos"][1], caption=f"የመዝገብ #{listing_id} 2ኛ ፎቶ")
        except Exception as e:
            print(f"Vehicle listing admin notice failed for {aid}: {e}")
    context.user_data.pop("vehicle_listing", None)
    return ConversationHandler.END

async def find_vehicle_listings(update, context, category):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    conn.row_factory = dict_row
    try:
        rows = conn.execute("SELECT * FROM vehicle_listings WHERE category=? AND active=1 ORDER BY id DESC LIMIT 30", (category,)).fetchall()
        records = [dict(r) for r in rows]
    finally:
        conn.close()
    label = {"rental": "የኪራይ", "sale": "የሽያጭ"}.get(category, "የመኪና")
    if not records:
        await update.message.reply_text(f"🔎 አሁን የተመዘገበ {label} የለም።", reply_markup=main_menu(update.effective_user.id))
        return
    result_noun = "መኪናዎች"
    await update.message.reply_text(f"🔎 የቅርብ ጊዜ {label} {result_noun} ({len(records)})")
    for rec in records:
        try:
            photos = json.loads(rec.get("photo_ids") or "[]")
            if not isinstance(photos, list):
                photos = []
        except (TypeError, ValueError, json.JSONDecodeError):
            photos = []
        photo_state = "📷 ፎቶ የለም — ለጊዜው" if rec.get("photo_status") == "missing" else "📷 ፎቶ አለ"
        icon = "🚘"
        public_details_text = sanitize_public_text(rec.get("details", ""))
        text_msg = (f"{icon} {rec['vehicle_type']}\n📍 {rec['location']}\n📝 {public_details_text}\n"
                    f"💰 {rec['price']}\n{photo_state}\n🔒 የባለቤቱ ስም፣ ስልክና ታርጋ በህዝብ ፍለጋ አይታይም።")
        if is_admin(update.effective_user.id) and get_admin_pii_visibility(update.effective_user.id):
            owner_username = users.get(int(rec.get("user_id") or 0), {}).get("username") or "የለም"
            text_msg += (f"\n👤 ባለቤት፦ {rec.get('owner_name') or '—'}"
                         f"\n🔗 Username፦ @{owner_username}"
                         f"\n📞 ስልክ፦ {rec.get('phone') or '—'}"
                         f"\n🔢 ታርጋ፦ {rec.get('plate') or '—'}")
        buyer_action = "ለመከራየት" if category == "rental" else "ለመግዛት"
        buyer_kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"📩 {buyer_action} ግንኙነት ጠይቅ", callback_data=f"vehiclebuyer_{rec['id']}")]])
        show_listing_photos = bool(photos)  # PII toggle hides names/phones/plates, not vehicle photos.
        try:
            if show_listing_photos:
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=photos[0], caption=text_msg, reply_markup=buyer_kb)
                for photo_id in photos[1:2]:
                    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=photo_id)
            else:
                await update.message.reply_text(text_msg, reply_markup=buyer_kb)
        except Exception:
            await update.message.reply_text(text_msg, reply_markup=buyer_kb)

async def vehicle_buyer_start(update, context):
    q = update.callback_query
    await q.answer()
    try:
        listing_id = int(q.data.split("_")[-1])
    except (ValueError, IndexError):
        return ConversationHandler.END
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        row = conn.execute("SELECT id,user_id,category,vehicle_type,active FROM vehicle_listings WHERE id=?", (listing_id,)).fetchone()
    finally:
        conn.close()
    if not row or not row[4]:
        await q.message.reply_text("❌ ይህ ማስታወቂያ ከአሁን በኋላ አይገኝም።")
        return ConversationHandler.END
    if int(row[1]) == update.effective_user.id:
        await q.answer("የራስዎን ማስታወቂያ መጠየቅ አይችሉም።", show_alert=True)
        return ConversationHandler.END
    context.user_data["vehicle_buyer"] = {"listing_id": listing_id, "owner_id": int(row[1]), "category": row[2], "vehicle_type": row[3]}
    await q.message.reply_text("📩 የግዢ ጥያቄ\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።")
    return VEHICLE_BUYER_NAME

async def vehicle_buyer_name(update, context):
    name = (update.message.text or "").strip()
    if len(name) < 2:
        await update.message.reply_text("እባክዎ ሙሉ ስምዎን ያስገቡ።")
        return VEHICLE_BUYER_NAME
    context.user_data["vehicle_buyer"]["name"] = name
    return await ask_registered_phone_confirmation(update, context, "vehicle_buyer")

# ==================================================
# CONSTRUCTION MACHINERY LISTINGS
# ==================================================

MACHINERY_TYPES = [
    "🚜 ኤክስካቫተር (Excavator)",
    "🚧 ሎደር (Loader)",
    "🚜 ቡልዶዘር (Bulldozer)",
    "🛣️ ግሬደር (Grader)",
    "🚧 ሮለር (Roller)",
    "⚙️ ሌላ",
]

async def machinery_start(update, context):
    context.user_data["machinery"] = {"photos": []}
    kb = ReplyKeyboardMarkup([[x] for x in MACHINERY_TYPES], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("🚜 የማሽነሪውን አይነት ይምረጡ።", reply_markup=kb)
    return MACH_TYPE

async def machinery_type(update, context):
    value = update.message.text.strip()
    if value not in MACHINERY_TYPES:
        await update.message.reply_text("ከተዘረዘሩት የማሽነሪ አይነቶች አንዱን ይምረጡ።")
        return MACH_TYPE
    context.user_data["machinery"]["type"] = value
    await update.message.reply_text("ሽያጭ ወይስ ኪራይ?", reply_markup=ReplyKeyboardMarkup([["ሽያጭ", "ኪራይ"]], resize_keyboard=True, one_time_keyboard=True))
    return MACH_MODE

async def machinery_mode(update, context):
    value=update.message.text.strip()
    if value not in ("ሽያጭ", "ኪራይ"):
        await update.message.reply_text("እባክዎ ሽያጭ ወይም ኪራይ ይምረጡ።")
        return MACH_MODE
    context.user_data["machinery"]["mode"] = "sale" if value == "ሽያጭ" else "rental"
    await update.message.reply_text("📍 ማሽነሪው የሚገኝበትን ከተማ/አካባቢ ይጻፉ።")
    return MACH_LOCATION

async def machinery_location(update, context):
    context.user_data["machinery"]["location"] = update.message.text.strip()
    await update.message.reply_text("📝 ስለ ማሽነሪው ሁኔታ፣ ሞዴልና ዝርዝር መረጃ ይጻፉ።")
    return MACH_DETAILS

async def machinery_details(update, context):
    context.user_data["machinery"]["details"] = update.message.text.strip()
    await update.message.reply_text("💰 ዋጋውን ይጻፉ (በስምምነት ከሆነ በስምምነት ብለው ይጻፉ)።")
    return MACH_PRICE

async def machinery_price(update, context):
    context.user_data["machinery"]["price"] = update.message.text.strip()
    return await ask_registered_phone_confirmation(update, context, "machinery")

async def machinery_phone(update, context):
    phone=update.message.text.strip().split("\n")[0].strip()
    valid, error=validate_phone(phone)
    if not valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return MACH_PHONE
    context.user_data["machinery"]["phone"] = phone
    await update.message.reply_text("📷 የማሽነሪውን ፎቶ ይላኩ። ፎቶ ከሌለ ዝለል ብለው ይጻፉ።")
    return MACH_PHOTOS

async def machinery_photos(update, context):
    data=context.user_data.get("machinery", {})
    if update.message.photo:
        data.setdefault("photos", []).append(update.message.photo[-1].file_id)
        if len(data["photos"]) < 5:
            await update.message.reply_text("✅ ፎቶ ተቀብሏል። ተጨማሪ ፎቶ ይላኩ ወይም 'ዝለል' ብለው ይጻፉ።")
            return MACH_PHOTOS
    elif update.message.text.strip() != "ዝለል":
        await update.message.reply_text("ፎቶ ይላኩ ወይም 'ዝለል' ብለው ይጻፉ።")
        return MACH_PHOTOS
    d=data; uid=update.effective_user.id
    conn=_db_connect(DB_PATH)
    try:
        cur=conn.cursor()
        cur.execute("INSERT INTO machinery_listings (user_id,machinery_type,listing_mode,location,details,price,phone,photo_ids,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (uid,d["type"],d["mode"],d["location"],d["details"],d["price"],d["phone"],json.dumps(d.get("photos",[])),datetime.now(timezone.utc).isoformat()))
        conn.commit(); listing_id=cur.lastrowid
    finally: conn.close()
    await update.message.reply_text(f"✅ የማሽነሪ ማስታወቂያዎ ተመዝግቧል። ቁጥር #{listing_id}\n📞 የተመዘገበ ስልክ፦ {d['phone']}\nማስታወቂያው ለፍለጋ ዝግጁ ነው።", reply_markup=main_menu(uid))
    notice=(f"🚜 አዲስ የማሽነሪ {('ሽያጭ' if d['mode']=='sale' else 'ኪራይ')}\nአይነት: {d['type']}\nቦታ: {d['location']}\nዝርዝር: {d['details']}\nዋጋ: {d['price']}\nስልክ: {d['phone']}\nUser ID: {uid}")
    for aid in get_admin_ids():
        try:
            await context.bot.send_message(chat_id=aid,text=notice)
            for photo in d.get("photos",[]): await context.bot.send_photo(chat_id=aid,photo=photo)
        except Exception as e: print(f"Machinery admin notice failed: {e}")
    context.user_data.pop("machinery",None)
    return ConversationHandler.END

async def find_machinery(update, context):
    await update.message.reply_text("🔎 የሚፈልጉትን ይጻፉ፤ ለምሳሌ: ኪራይ, Excavator, ባህርዳር። ምንም ማጣሪያ ካልፈለጉ 'ሁሉም' ይጻፉ።")
    return MACH_SEARCH

async def machinery_search_results(update, context):
    term=update.message.text.strip()
    conn=_db_connect(DB_PATH)
    try:
        if term == "ሁሉም":
            selected_mode = context.user_data.get("machinery_search_mode")
            if selected_mode:
                rows=conn.execute("SELECT * FROM machinery_listings WHERE active=1 AND listing_mode=? ORDER BY id DESC LIMIT 20", (selected_mode,)).fetchall()
            else:
                rows=conn.execute("SELECT * FROM machinery_listings WHERE active=1 ORDER BY id DESC LIMIT 20").fetchall()
        else:
            mode = context.user_data.get("machinery_search_mode") or ("rental" if "ኪራይ" in term else ("sale" if "ሽያጭ" in term else None))
            needle=term.replace("ኪራይ", "").replace("ሽያጭ", "").strip()
            if mode:
                rows=conn.execute("SELECT * FROM machinery_listings WHERE active=1 AND listing_mode=? AND (machinery_type ILIKE ? OR location ILIKE ? OR details ILIKE ?) ORDER BY id DESC LIMIT 20", (mode, f"%{needle}%", f"%{needle}%", f"%{needle}%")).fetchall() if needle else conn.execute("SELECT * FROM machinery_listings WHERE active=1 AND listing_mode=? ORDER BY id DESC LIMIT 20",(mode,)).fetchall()
            else:
                rows=conn.execute("SELECT * FROM machinery_listings WHERE active=1 AND (machinery_type ILIKE ? OR location ILIKE ? OR details ILIKE ?) ORDER BY id DESC LIMIT 20", (f"%{term}%",f"%{term}%",f"%{term}%")).fetchall()
    finally: conn.close()
    if not rows:
        await update.message.reply_text("🔎 እስካሁን የተመዘገበ የማሽነሪ ማስታወቂያ የለም።",reply_markup=main_menu(update.effective_user.id)); return
    for r in rows:
        photos=json.loads(r["photo_ids"] or "[]")
        msg=(f"🚜 {r['machinery_type']} — {'ሽያጭ' if r['listing_mode']=='sale' else 'ኪራይ'}\n📍 {r['location']}\n📝 {r['details']}\n💰 {r['price']}\n📞 ስልክ: {r['phone']}\n#{r['id']}")
        if photos: await update.message.reply_photo(photo=photos[0],caption=msg)
        else: await update.message.reply_text(msg)

# ==================================================
# PROPERTY MARKET — RESIDENTIAL / COMMERCIAL
# ==================================================
async def property_start(update, context):
    context.user_data["property_listing"] = {"documents": [], "photos": []}
    kb = ReplyKeyboardMarkup([["🏠 መኖሪያ ቤት/አፓርታማ/መሬት"], ["🏢 የንግድ ንብረት/መጋዘን/ፋብሪካ"], ["❌ ሰርዝ"]], resize_keyboard=True)
    await update.effective_message.reply_text("🏠 የንብረት ምዝገባ\n\n1️⃣ የንብረቱን አይነት ይምረጡ። ክፍት መሬትን በሚጠቀሙበት ዓላማ መሰረት ይመድቡ።", reply_markup=kb)
    return PROPERTY_TYPE

async def property_type_input(update, context):
    val=(update.message.text or "").strip()
    if val == "❌ ሰርዝ":
        context.user_data.pop("property_listing",None)
        await update.message.reply_text("ምዝገባው ተሰርዟል።",reply_markup=main_menu(update.effective_user.id)); return ConversationHandler.END
    if val.startswith("🏠"):
        kind="residential"
    elif val.startswith("🏢"):
        kind="commercial"
    else:
        await update.message.reply_text("ከተሰጡት ሁለት የንብረት አይነቶች አንዱን ይምረጡ።"); return PROPERTY_TYPE
    context.user_data["property_listing"]["property_type"]=kind
    await update.message.reply_text("2️⃣ ንብረቱን ለመሸጥ ነው ወይስ ለማከራየት?",reply_markup=ReplyKeyboardMarkup([["💰 ለሽያጭ"],["🏠 ለኪራይ"]],resize_keyboard=True))
    return PROPERTY_MODE

async def property_mode_input(update, context):
    val=(update.message.text or "").strip()
    if "ሽያጭ" in val: mode="sale"
    elif "ኪራይ" in val: mode="rent"
    else:
        await update.message.reply_text("‘ለሽያጭ’ ወይም ‘ለኪራይ’ ይምረጡ።"); return PROPERTY_MODE
    context.user_data["property_listing"]["listing_mode"]=mode
    await update.message.reply_text("3️⃣ ክልል፣ ከተማ፣ ክፍለ ከተማ/አካባቢ በዝርዝር ይጻፉ።",reply_markup=ReplyKeyboardRemove())
    return PROPERTY_LOCATION

async def property_location_input(update, context):
    val=(update.message.text or "").strip()
    if len(val)<3:
        await update.message.reply_text("እባክዎ የንብረቱን ቦታ በዝርዝር ያስገቡ።"); return PROPERTY_LOCATION
    context.user_data["property_listing"]["location"]=val
    await update.message.reply_text("4️⃣ የንብረቱን ስፋት እና መለኪያ ይጻፉ። ምሳሌ፦ 200 ካሬ ሜትር / 3 መኝታ ቤት")
    return PROPERTY_SIZE

async def property_size_input(update, context):
    val=(update.message.text or "").strip()
    if len(val)<2:
        await update.message.reply_text("ስፋቱን/መጠኑን ያስገቡ።"); return PROPERTY_SIZE
    context.user_data["property_listing"]["size"]=val
    await update.message.reply_text("5️⃣ የሽያጭ/ኪራይ ዋጋውን በብር ያስገቡ።")
    return PROPERTY_PRICE

async def property_price_input(update, context):
    val=(update.message.text or "").strip()
    if len(val)<1:
        await update.message.reply_text("ዋጋውን ያስገቡ።"); return PROPERTY_PRICE
    context.user_data["property_listing"]["price"]=val
    await update.message.reply_text("6️⃣ ስለ ንብረቱ ሁኔታ፣ የግንባታ ዓመት፣ አገልግሎት እና ሌሎች ዝርዝሮች ይጻፉ።")
    return PROPERTY_DETAILS

async def property_details_input(update, context):
    context.user_data["property_listing"]["details"]=(update.message.text or "").strip()
    kb=ReplyKeyboardMarkup([["👤 የንብረቱ ባለቤት ነኝ"],["📑 በሕጋዊ ውክልና እየሸጥኩ/አከራይቼ ነው"]],resize_keyboard=True)
    await update.message.reply_text("7️⃣ ንብረቱን የሚያስመዘግቡት እርስዎ ባለቤት ስለሆኑ ነው ወይስ ሕጋዊ ውክልና አለዎት? ያለ ባለቤትነት ወይም ሕጋዊ ውክልና ማስመዝገብ አይፈቀድም።",reply_markup=kb)
    return PROPERTY_ROLE

async def property_role_input(update, context):
    val=(update.message.text or "").strip()
    if val.startswith("👤"): role="owner"
    elif val.startswith("📑"): role="authorized_agent"
    else:
        await update.message.reply_text("ባለቤት ወይም ሕጋዊ ወኪል ከሚሉት አንዱን ይምረጡ።"); return PROPERTY_ROLE
    context.user_data["property_listing"]["legal_role"]=role
    await update.message.reply_text("8️⃣ የባለቤትነት ወይም የውክልና ማረጋገጫ ሰነድ ፎቶ/PDF ይላኩ። ሰነዱ ለAdmin ግምገማ ብቻ ነው፤ ለሕዝብ አይታይም። ከ1 በላይ ሰነድ መላክ ይችላሉ። ሲጨርሱ ‘ሰነድ ጨርሻለሁ’ ይጻፉ።",reply_markup=ReplyKeyboardMarkup([["ሰነድ ጨርሻለሁ"]],resize_keyboard=True))
    return PROPERTY_DOCS

async def property_docs_input(update, context):
    listing=context.user_data["property_listing"]
    if (update.message.text or "").strip()=="ሰነድ ጨርሻለሁ":
        if not listing.get("documents"):
            await update.message.reply_text("⚠️ ቢያንስ አንድ የማረጋገጫ ሰነድ ይላኩ።"); return PROPERTY_DOCS
        await update.message.reply_text("9️⃣ የንብረቱን ፎቶዎች ይላኩ። ሲጨርሱ ‘ፎቶ ጨርሻለሁ’ ይጻፉ።")
        return PROPERTY_PHOTOS
    if update.message.photo:
        fid=update.message.photo[-1].file_id
    elif update.message.document:
        fid=update.message.document.file_id
    else:
        await update.message.reply_text("ፎቶ ወይም PDF ሰነድ ይላኩ፤ ሲጨርሱ ‘ሰነድ ጨርሻለሁ’ ይጻፉ።"); return PROPERTY_DOCS
    listing.setdefault("documents",[]).append(fid)
    await update.message.reply_text(f"✅ ሰነድ {len(listing['documents'])} ተቀብለናል። ሌላ ይላኩ ወይም ‘ሰነድ ጨርሻለሁ’ ይጻፉ።")
    return PROPERTY_DOCS

async def property_photos_input(update, context):
    listing=context.user_data["property_listing"]
    if (update.message.text or "").strip()=="ፎቶ ጨርሻለሁ":
        if not listing.get("photos"):
            await update.message.reply_text("⚠️ ቢያንስ አንድ የንብረት ፎቶ ይላኩ።")
            return PROPERTY_PHOTOS
        return await ask_registered_phone_confirmation(update,context,"property")
    if not update.message.photo:
        await update.message.reply_text("የንብረቱን ፎቶ ይላኩ ወይም ‘ፎቶ ጨርሻለሁ’ ይጻፉ።"); return PROPERTY_PHOTOS
    if len(listing.setdefault("photos",[]))>=8:
        await update.message.reply_text("እስከ 8 ፎቶ ብቻ ይቻላል።"); return PROPERTY_PHOTOS
    listing["photos"].append(update.message.photo[-1].file_id)
    await update.message.reply_text(f"✅ ፎቶ {len(listing['photos'])} ተቀብለናል። ሌላ ይላኩ ወይም ‘ፎቶ ጨርሻለሁ’ ይጻፉ።")
    return PROPERTY_PHOTOS

async def save_property_listing(update, context, phone):
    listing=context.user_data.get("property_listing",{})
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        cur=conn.execute("""INSERT INTO property_listings
            (user_id,property_type,listing_mode,location,size,price,details,legal_role,document_ids,photo_ids,phone,status,active,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,'pending',0,?)""",
            (update.effective_user.id,listing["property_type"],listing["listing_mode"],listing["location"],listing["size"],listing["price"],listing.get("details",""),listing["legal_role"],json.dumps(listing["documents"]),json.dumps(listing["photos"]),phone,datetime.now(timezone.utc).isoformat()))
        rid=cur.lastrowid; conn.commit()
    finally: conn.close()
    label="መኖሪያ" if listing["property_type"]=="residential" else "የንግድ"
    await update.effective_message.reply_text(f"✅ የንብረት ማስመዝገብ ተቀብለናል። መዝገብ #{rid}\n\n🔎 Admin ሰነዶቹን ከመረመረና ካጸደቀ በኋላ ብቻ በሕዝብ ፍለጋ ይታያል። ይህ ምዝገባ የባለቤትነት ሕጋዊነት ዋስትና አይደለም።",reply_markup=main_menu(update.effective_user.id))
    for aid in get_admin_ids():
        try:
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ አጽድቅ",callback_data=f"property_approve_{rid}"),InlineKeyboardButton("❌ ውድቅ",callback_data=f"property_reject_{rid}")]])
            await context.bot.send_message(chat_id=aid,text=f"🏠 አዲስ የንብረት ምዝገባ — ለAdmin ግምገማ\nመዝገብ #{rid}\nዓይነት፦ {label}\nአገልግሎት፦ {listing['listing_mode']}\nቦታ፦ {listing['location']}\nስፋት፦ {listing['size']}\nዋጋ፦ {listing['price']}\nሚና፦ {listing['legal_role']}\nስልክ፦ {phone}\nUser ID፦ {update.effective_user.id}",reply_markup=kb)
            for fid in listing["documents"]:
                try: await context.bot.send_document(chat_id=aid,document=fid,caption=f"የንብረት #{rid} የማረጋገጫ ሰነድ")
                except Exception: await context.bot.send_photo(chat_id=aid,photo=fid,caption=f"የንብረት #{rid} የማረጋገጫ ሰነድ")
            for fid in listing["photos"]:
                await context.bot.send_photo(chat_id=aid,photo=fid,caption=f"የንብረት #{rid} ፎቶ")
        except Exception as e: print(f"PROPERTY ADMIN REVIEW SEND ERROR: {e}")
    context.user_data.pop("property_listing",None)
    return ConversationHandler.END

async def property_search(update, context):
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        rows=conn.execute("SELECT * FROM property_listings WHERE active=1 AND status='approved' ORDER BY id DESC LIMIT 20").fetchall()
    finally: conn.close()
    if not rows:
        await update.effective_message.reply_text("🔎 እስካሁን በAdmin የጸደቀ የንብረት ማስታወቂያ የለም።",reply_markup=main_menu(update.effective_user.id)); return
    for row in rows:
        label="መኖሪያ" if row["property_type"]=="residential" else "የንግድ"
        photos=json.loads(row["photo_ids"] or "[]")
        msg=f"🏠 {label} — {'ሽያጭ' if row['listing_mode']=='sale' else 'ኪራይ'}\n📍 {row['location']}\n📐 {row['size']}\n💰 {row['price']}\n📝 {row['details']}\n🔖 መዝገብ #{row['id']}\n🔒 የባለቤት ስልክ ቁጥር በይፋ አይታይም። ለጥያቄ Admin/Support ያነጋግሩ።"
        if photos: await update.effective_message.reply_photo(photo=photos[0],caption=msg)
        else: await update.effective_message.reply_text(msg)

async def show_property_admin_queue(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin/Super Admin ብቻ።")
        return
    conn=_db_connect(DB_PATH,timeout=10)
    try: rows=conn.execute("SELECT id,property_type,listing_mode,location,size,price,user_id FROM property_listings WHERE status='pending' ORDER BY id DESC LIMIT 20").fetchall()
    finally: conn.close()
    if not rows:
        await update.effective_message.reply_text("📭 ለAdmin ምርመራ የሚጠብቅ የንብረት ምዝገባ የለም።")
        return
    for row in rows:
        kind="መኖሪያ" if row["property_type"]=="residential" else "የንግድ"
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ አጽድቅ",callback_data=f"property_approve_{row['id']}"),InlineKeyboardButton("❌ ውድቅ",callback_data=f"property_reject_{row['id']}")]])
        await update.effective_message.reply_text(f"🏠 የንብረት መዝገብ #{row['id']}\nዓይነት፦ {kind}\nአገልግሎት፦ {row['listing_mode']}\nቦታ፦ {row['location']}\nስፋት፦ {row['size']}\nዋጋ፦ {row['price']}\nUser ID፦ {row['user_id']}\n\nማስታወሻ፦ የሰነዶቹን ፎቶ/PDF ከምዝገባ ማሳወቂያ ጋር ያረጋግጡ።",reply_markup=kb)

async def property_admin_review_callback(update, context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin/Super Admin ብቻ።",show_alert=True)
    parts=q.data.split("_"); action=parts[1]; rid=int(parts[2])
    status="approved" if action=="approve" else "rejected"
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        cur=conn.execute("UPDATE property_listings SET status=?,active=?,reviewed_by=?,reviewed_at=? WHERE id=? AND status='pending'",(status,1 if status=="approved" else 0,q.from_user.id,datetime.now(timezone.utc).isoformat(),rid))
        conn.commit()
        row=conn.execute("SELECT user_id FROM property_listings WHERE id=?",(rid,)).fetchone()
    finally: conn.close()
    if cur.rowcount!=1: return await q.edit_message_text("ይህ መዝገብ ቀድሞ ተመርምሯል።")
    if row:
        try: await context.bot.send_message(chat_id=row["user_id"],text=f"{'✅ የንብረት ማስታወቂያዎ ጸድቋል።' if status=='approved' else '❌ የንብረት ማስታወቂያዎ አልጸደቀም።'}\nመዝገብ #{rid}")
        except Exception: pass
    return await q.edit_message_text(f"{'✅ ጸድቋል' if status=='approved' else '❌ ውድቅ ተደርጓል'} — መዝገብ #{rid}")

# ==================================================
# BROKER REGISTRATION
# ==================================================

async def broker_start(update, context):
    context.user_data.pop("cargo", None)
    context.user_data.pop("truck", None)
    context.user_data.pop("owner", None)
    context.user_data.pop("connect_info", None)
    context.user_data["broker"] = {"sectors": []}
    await update.message.reply_text(
        "🤝 **ህጋዊ ደላላ ምዝገባ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።",
        reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    return BROKER_NAME

def broker_sector_keyboard(selected):
    choices = [
        ("freight", "🚚 የጭነት ደላላ"),
        ("machinery", "🚜 የኮንስትራክሽን ማሽነሪ ደላላ"),
        ("cars", "🚗 የመኪና ሽያጭና ኪራይ ደላላ"),
        ("other", "🏢 ሌሎች የንግድ አጋሮች"),
        ("all", "🌐 ሁሉንም"),
    ]
    rows = []
    for key, label in choices:
        mark = "✅ " if key in selected else "▫️ "
        rows.append([InlineKeyboardButton(mark + label, callback_data=f"broker_sector_{key}")])
    rows.append([InlineKeyboardButton("➡️ ቀጣይ", callback_data="broker_sector_done")])
    return InlineKeyboardMarkup(rows)

async def broker_sector_callback(update, context):
    query = update.callback_query
    selected = context.user_data.setdefault("broker", {}).setdefault("sectors", [])
    key = query.data.replace("broker_sector_", "", 1)
    mapping = {
        "freight": "የጭነት ደላላ",
        "machinery": "የኮንስትራክሽን ማሽነሪ ደላላ",
        "cars": "የመኪና ሽያጭና ኪራይ ደላላ",
        "other": "ሌሎች የንግድ አጋሮች",
    }
    if key == "done":
        if not selected:
            await query.answer("ቢያንስ አንድ ዘርፍ ይምረጡ።", show_alert=True)
            return BROKER_SECTORS
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("1️⃣ ሙሉ ስምዎን ይጻፉ።")
        return BROKER_NAME
    await query.answer()
    if key == "all":
        selected[:] = ["ሁሉንም"] if "ሁሉንም" not in selected else []
    elif key in mapping:
        if "ሁሉንም" in selected:
            selected.remove("ሁሉንም")
        label = mapping[key]
        if label in selected:
            selected.remove(label)
        else:
            selected.append(label)
    await query.edit_message_reply_markup(reply_markup=broker_sector_keyboard(selected))
    return BROKER_SECTORS

async def broker_name(update, context):
    context.user_data["broker"]["name"] = update.message.text.strip()
    return await ask_registered_phone_confirmation(update, context, "broker")

async def broker_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return BROKER_PHONE
    context.user_data["broker"]["phone"] = phone
    await update.message.reply_text(
        "3️⃣ የሚሰሩበትን አድራሻ ይጻፉ።\nምሳሌ፦ አዲስ አበባ / ባህርዳር / ጎንደር"
    )
    return BROKER_ADDRESS

async def broker_address(update, context):
    address = update.message.text.strip()
    user = update.effective_user
    context.user_data["broker"]["address"] = address
    broker = {
        "user_id": user.id,
        "name": context.user_data["broker"]["name"],
        "username": user.username or "",
        "phone": context.user_data["broker"]["phone"],
        "address": address,
        "sectors": context.user_data["broker"].get("sectors", []),
        "timestamp": datetime.now().isoformat(),
    }
    brokers.append(broker)
    try:
        broker["id"] = db_save_broker(broker)
    except Exception as e:
        print(f"DB SAVE BROKER ERROR: {e}")
    await update.message.reply_text(
        "እናመሰግናለን! ምዝገባዎን በተሳካ ሁኔታ ጨርሰዋል። "
        "አስፈላጊ መረጃዎችን ወደሚጠቀሙበት Telegram እንልክልዎታለን።\nለበለጠ መረጃ @tanapage Join ያድርጉ።",
        reply_markup=main_menu(user.id)
    )
    return ConversationHandler.END

async def send_admin_broker_notification(context, broker):
    notification_text = (
        "🆕 **TANA CARGO, LOGISTICS & INFORMATION SERVICES — አዲስ የጭነት ደላላ ተመዝግቧል!**\n\n"
        f"👤 **ስም:** {broker['name']}\n"
        f"🔗 **Username:** @{broker['username'] or 'የለም'}\n"
        f"🆔 **Telegram ID:** {broker['user_id']}\n"
        f"📞 **ስልክ:** {broker['phone']}\n"
        f"📍 **አድራሻ:** {broker['address']}\n"
        f"🤝 **የንግድ ዘርፎች:** {', '.join(broker.get('sectors', []))}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📌 **የጭነት ደላላ መሆኑን ያረጋግጡ።**\n"
        "✅ Right = አረጋግጥ\n"
        "❌ X = ውድቅ አድርግ"
    )
    broker_idx = len(brokers) - 1
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Right", callback_data=f"brokerright_{broker_idx}"),
            InlineKeyboardButton("❌ X አጥፋ", callback_data=f"brokerxdelete_{broker_idx}"),
        ]
    ])
    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=notification_text,
                reply_markup=kb
            )
        except Exception:
            pass

async def broker_right_confirm(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return
    try:
        index = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if index < 0 or index >= len(brokers):
        return
    broker = brokers[index]
    if broker.get("approved"):
        await query.answer("⚠️ ይህ ቀድሞ ጸድቋል!", show_alert=True)
        return
    broker["approved"] = True
    broker["active"] = True
    if broker.get("id"):
        conn = _db_connect(DB_PATH, check_same_thread=False)
        try:
            conn.execute("UPDATE brokers SET approved=1, active=1 WHERE id=?", (broker["id"],))
            conn.commit()
        finally:
            conn.close()
    await query.edit_message_text("✅ **የጭነት ደላላው ጸድቋል!**\n\nለደላላው ማሳወቂያ ተልኳል።")
    broker_user_id = broker.get("user_id")
    if broker_user_id:
        try:
            approval_text = (
                "🎉 **እንኳን ደስ አለዎት!** 🎉\n\n"
                "**የጣና ጭነት አባል ሆነዋል!**\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "📦 **ጭነት ካለዎት** ወይም\n"
                "🚛 **የጭነት መኪና ካለዎት**\n\n"
                "✅ **በነፃ ይለጥፉ!**\n\n"
                "🔒 መረጃዎ **100% የተጠበቀ** ነው።\n\n"
                "💰 ደንበኛ ፈልገን ስናገናኛቸው ብቻ "
                "ከሚያገኙት ኮሚሽን ላይ **5% ብቻ** "
                "ለጣና ጭነት ይከፍላሉ።\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "🙏 እኛን ስለመረጡ እናመሰግናለን!"
            )
            await context.bot.send_message(chat_id=broker_user_id, text=approval_text)
        except Exception:
            pass

async def broker_x_delete(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ!", show_alert=True)
        return
    try:
        index = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if index < 0 or index >= len(brokers):
        return
    broker = brokers[index]
    broker["active"] = False
    broker["approved"] = False
    if broker.get("id"):
        conn = _db_connect(DB_PATH, check_same_thread=False)
        try:
            conn.execute("UPDATE brokers SET approved=0, active=0 WHERE id=?", (broker["id"],))
            conn.commit()
        finally:
            conn.close()
    await query.edit_message_text(
        "❌ **ውድቅ ተደርጓል!**\n\n"
        "ይህ የጭነት ደላላ በ Super Admin ውድቅ ተደርጓል።"
    )
    broker_user_id = broker.get("user_id")
    if broker_user_id:
        try:
            rejection_text = (
                "❌ **የጭነት ደላላ ምዝገባ ውድቅ ተደርጓል።**\n\n"
                "የጣና ካርጎ አባላቶች ባገኙት መረጃ መሰረት "
                "እርሶ **የጭነት ደላላ አይደሉም**።\n\n"
                "ገፃችንን ስለጎበኙ እናመሰግናለን! 🙏"
            )
            await context.bot.send_message(chat_id=broker_user_id, text=rejection_text)
        except Exception:
            pass

async def show_broker_registry(update, context):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ Admin ብቻ ይህን ማየት ይችላል።")
        return
    if not brokers:
        await update.message.reply_text("📋 እስካሁን የተመዘገበ የጭነት ደላላ የለም።")
        return
    show_private = get_admin_pii_visibility(user_id)
    for i, broker in enumerate(brokers):
        if not broker.get("approved"):
            status = "⏳ በመጠበቅ"
        elif broker.get("active"):
            status = "✅ ጸድቋል"
        else:
            status = "❌ ውድቅ"
        text = (
            f"📋 **የጭነት ደላላ #{i + 1}**\n\n"
            f"👤 ስም: {broker.get('name', 'N/A') if show_private else 'የተደበቀ'}\n"
            f"🔗 Username: @{(broker.get('username') or 'የለም') if show_private else 'የተደበቀ'}\n"
            f"🆔 Telegram ID: {broker.get('user_id', 'N/A') if show_private else 'የተደበቀ'}\n"
            f"📞 ስልክ: {broker.get('phone', 'N/A') if show_private else 'የተደበቀ'}\n"
            f"📍 አድራሻ: {broker.get('address', 'N/A') if show_private else 'የተደበቀ'}\n"
            f"📌 ሁኔታ: {status}\n"
        )
        buttons = []
        if is_super_admin(user_id):
            buttons.append([
                InlineKeyboardButton(
                    f"🗑️ #{i + 1} አጥፋ (Super)",
                    callback_data=f"admindeletebroker_{i}"
                )
            ])
        if buttons:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await update.message.reply_text(text)

async def admin_delete_broker(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ!", show_alert=True)
        return
    try:
        index = int(query.data.split("_")[-1])
    except ValueError:
        return
    if index < 0 or index >= len(brokers):
        return
    brokers[index]["active"] = False
    brokers[index]["approved"] = False
    if brokers[index].get("id"):
        conn = _db_connect(DB_PATH, check_same_thread=False)
        try:
            conn.execute("UPDATE brokers SET active=0, approved=0 WHERE id=?", (brokers[index]["id"],))
            conn.commit()
        finally:
            conn.close()
    await query.edit_message_text("✅ **የጭነት ደላላው ተሰርዟል።**")
# ==================================================
# FIND CARGO / TRUCK
# ==================================================

async def find_cargo(update, context):
    active_cargos = [c for c in cargo_posts if c.get("active", True)]
    is_admin_user = is_admin(update.effective_user.id)
    if not active_cargos:
        await update.message.reply_text(
            "📦 በአሁኑ ጊዜ ተስማሚ ጭነት የለም።\n\n"
            "🔎 ጭነት እያፈላለግን ነው።\n"
            "🔔 ተስማሚ ጭነት ሲገኝ እናሳውቅዎታለን።",
            reply_markup=main_menu(update.effective_user.id)
        )
        return
    for i, cargo in enumerate(active_cargos):
        cargo_id = f"{i + 1:03d}"
        price_display = cargo.get("price_display", "በስምምነት")
        real_index = cargo_posts.index(cargo)
        is_selected = is_already_selected("cargo", real_index)
        status_mark = "❌ " if is_selected else ""
        admin_info = ""
        if is_admin_user and get_admin_pii_visibility(update.effective_user.id):
            owner_name = users.get(cargo["user_id"], {}).get("name", "N/A")
            owner_username = users.get(cargo["user_id"], {}).get("username", "")
            admin_info = (
                f"👤 ስም: {owner_name}\n"
                f"🔗 @{owner_username or 'የለም'}\n"
                f"🆔 ID: {cargo['user_id']}\n"
                f"📞 ስልክ: {cargo.get('phone', 'N/A')}\n"
            )
        poster_label = "🏷️ Broker" if cargo.get("poster_type") == "broker" else "🏷️ Customer"
        msg = (
            f"{status_mark}📦 **ጭነት {cargo_id}**\n"
            f"{poster_label}\n"
            f"📍 መነሻ፦ {cargo['from']}\n"
            f"📍 መድረሻ፦ {cargo['to']}\n"
            f"📦 አይነት፦ {cargo['type']}\n"
            f"🚛 የሚፈለግ መኪና፦ {cargo['vehicle']}\n"
            f"📊 መጠን፦ {cargo['size_name']} ({cargo['size']}%)\n"
            f"⚖️ ክብደት፦ {cargo['weight']}\n"
            f"📅 ቀን (ET)፦ {cargo.get('date_et', 'N/A')}\n"
            f"📅 ቀን (GC)፦ {cargo.get('date_gc', 'N/A')}\n"
            f"💰 ዋጋ፦ {price_display}\n"
            f"{admin_info}"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        if is_selected:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"❌ ጭነት {cargo_id} ተመርጧል",
                    callback_data="already_selected"
                )
            ]])
        else:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"የመረጡት ጭነት {cargo_id} ከሆነ እዚጋ ያጫኑ 👈",
                    callback_data=f"connect_{real_index}"
                )
            ]])
        await update.message.reply_text(msg, reply_markup=kb)

async def find_truck(update, context):
    active_trucks = [t for t in truck_posts if t.get("active", True)]
    is_admin_user = is_admin(update.effective_user.id)
    if not active_trucks:
        await update.message.reply_text(
            "🚛 በአሁኑ ጊዜ ተስማሚ የጭነት መኪና የለም።\n\n"
            "🔎 መኪና እያፈላለግን ነው።\n"
            "🔔 ተስማሚ መኪና ሲገኝ እናሳውቅዎታለን።",
            reply_markup=main_menu(update.effective_user.id)
        )
        return
    for i, truck in enumerate(active_trucks):
        truck_id = f"{i + 1:03d}"
        real_index = truck_posts.index(truck)
        is_selected = is_already_selected("truck", real_index)
        status_mark = "❌ " if is_selected else ""
        admin_info = ""
        if is_admin_user and get_admin_pii_visibility(update.effective_user.id):
            owner_name = users.get(truck["user_id"], {}).get("name", "N/A")
            owner_username = users.get(truck["user_id"], {}).get("username", "")
            admin_info = (
                f"👤 ስም: {owner_name}\n"
                f"🔗 @{owner_username or 'የለም'}\n"
                f"🆔 ID: {truck['user_id']}\n"
                f"📞 ስልክ: {truck.get('phone', 'N/A')}\n"
                f"🔢 ታርጋ: {truck.get('plate', 'N/A')}\n"
                f"👨‍✈️ ሹፌር: {truck.get('driver', 'N/A')}\n"
            )
        poster_label = "🏷️ Broker" if truck.get("poster_type") == "broker" else "🏷️ Customer"
        msg = (
            f"{status_mark}🚛 **የጭነት መኪና {truck_id}**\n"
            f"{poster_label}\n"
            f"🔹 አይነት፦ {truck['type']}\n"
            f"⚖️ አቅም፦ {truck['capacity']}\n"
            f"📍 አድራሻ፦ {truck['address']}\n"
            f"{admin_info}"
            "━━━━━━━━━━━━━━━━━━━━"
        )
        if is_selected:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"❌ መኪና {truck_id} ተመርጧል",
                    callback_data="already_selected"
                )
            ]])
        else:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"የመረጡት የጭነት መኪና {truck_id} ከሆነ ይጫኑ 👈",
                    callback_data=f"truckconnect_{real_index}"
                )
            ]])
        await update.message.reply_text(msg, reply_markup=kb)

async def already_selected_callback(update, context):
    query = update.callback_query
    await query.answer(
        "❌ ይቅርታ የመረጡት ጭነት/የጭነት መኪና በሌላ ደንበኛችን የግንኙነት ጥያቄ ተይዞበታል።\n\n"
        "እባክዎ ሌላ ተስማሚ ይምረጡ።",
        show_alert=True
    )

# ==================================================
# CONNECTION REQUEST
# ==================================================

async def connection_request(update, context):
    query = update.callback_query
    await query.answer()
    index = int(query.data.split("_")[1])
    if index < 0 or index >= len(cargo_posts):
        await query.edit_message_text("❌ ይህ ጭነት ከአሁን በኋላ አይገኝም።")
        return ConversationHandler.END
    cargo = cargo_posts[index]
    if not cargo.get("active", True):
        await query.edit_message_text("❌ ይህ ጭነት ተወግዷል።")
        return ConversationHandler.END
    requester = update.effective_user
    if is_selected_by_other("cargo", index, requester.id):
        await query.answer("❌ ይቅርታ የመረጡት ጭነት በሌላ ደንበኛችን የግንኙነት ጥያቄ ተይዞበታል።", show_alert=True)
        return ConversationHandler.END
    if cargo["user_id"] == requester.id:
        await query.answer("❌ የራስዎን ጭነት መጠየቅ አይችሉም።", show_alert=True)
        return ConversationHandler.END
    for req in connection_requests:
        if (req.get("type") == "cargo" and req.get("cargo_index") == index
            and req["requester_id"] == requester.id
            and req["status"] in ["pending", "admin_confirming", "approved"]):
            await query.answer("⚠️ ይህን ጭነት አስቀድመው ጠይቀዋል።", show_alert=True)
            return ConversationHandler.END
    context.user_data["connect_info"] = {
        "type": "cargo",
        "item_index": index,
        "requester_id": requester.id,
        "requester_name": requester.full_name,
        "requester_username": requester.username or "",
    }
    await query.edit_message_text("📝 **መረጃዎን ያስገቡ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።")
    return CONNECT_NAME

async def truck_connection_request(update, context):
    query = update.callback_query
    await query.answer()
    index = int(query.data.split("_")[1])
    if index < 0 or index >= len(truck_posts):
        await query.edit_message_text("❌ ይህ መኪና ከአሁን በኋላ አይገኝም።")
        return ConversationHandler.END
    truck = truck_posts[index]
    if not truck.get("active", True):
        await query.edit_message_text("❌ ይህ መኪና ተወግዷል።")
        return ConversationHandler.END
    requester = update.effective_user
    if is_selected_by_other("truck", index, requester.id):
        await query.answer("❌ ይቅርታ የመረጡት የጭነት መኪና በሌላ ደንበኛችን የግንኙነት ጥያቄ ተይዞበታል።", show_alert=True)
        return ConversationHandler.END
    if truck["user_id"] == requester.id:
        await query.answer("❌ የራስዎን መኪና መጠየቅ አይችሉም።", show_alert=True)
        return ConversationHandler.END
    for req in connection_requests:
        if (req.get("type") == "truck" and req.get("truck_index") == index
            and req["requester_id"] == requester.id
            and req["status"] in ["pending", "admin_confirming", "approved"]):
            await query.answer("⚠️ ይህን መኪና አስቀድመው ጠይቀዋል።", show_alert=True)
            return ConversationHandler.END
    context.user_data["connect_info"] = {
        "type": "truck",
        "item_index": index,
        "requester_id": requester.id,
        "requester_name": requester.full_name,
        "requester_username": requester.username or "",
    }
    await query.edit_message_text("📝 **መረጃዎን ያስገቡ**\n\n1️⃣ ሙሉ ስምዎን ይጻፉ።")
    return CONNECT_NAME

async def connect_name(update, context):
    info = context.user_data.get("connect_info", {})
    info["name"] = update.message.text.strip().split("\n")[0].strip()
    context.user_data["connect_info"] = info
    return await ask_registered_phone_confirmation(update, context, "connect_info")

async def connect_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return CONNECT_PHONE
    info = context.user_data.get("connect_info", {})
    info["phone"] = phone
    context.user_data["connect_info"] = info
    await update.message.reply_text(
        "3️⃣ **የሚገኙበትን አድራሻ** ይጻፉ።\n"
        "ምሳሌ፦ አዲስ አበባ / ባህርዳር / ጎንደር"
    )
    return CONNECT_TYPE

async def connect_type(update, context):
    info = context.user_data.get("connect_info", {})
    text = update.message.text.strip().split("\n")[0].strip()
    info["requester_address"] = text
    context.user_data["connect_info"] = info
    requester = update.effective_user
    if info.get("type") == "cargo":
        cargo = cargo_posts[info["item_index"]]
        request = {
            "type": "cargo",
            "cargo_index": info["item_index"],
            "cargo_owner_id": cargo["user_id"],
            "requester_id": requester.id,
            "requester_name": info.get("name", requester.full_name),
            "requester_username": info.get("requester_username", ""),
            "requester_phone": info.get("phone", ""),
            "requester_address": info.get("requester_address", ""),
            "requester_plate": "",
            "status": "admin_confirming",
            "full_info_shared": False, "admin_right_confirmed": False,
            "seeker_notified": False, "owner_notified": False,
            "owner_2_notified": False,
            "created_at": datetime.now().isoformat(),
        }
        msg_text = (
            "✅ **እናመሰግናለን!**\n\n"
            "📌 ምዝገባዎን ጨርሰዋል።\n\n"
            "⏳ ከተወሰኑ ደቂቃዎች በኋላ እንደውልልዎታለን።\n\n"
            "ውድ ደንበኛችን ባለጭነት ጋር ተስማምተው ጭነተዎን ጭነው ጉዞ ከመጀመርዎ በፊት ባለጭነት እርሶ የጫኑለትን ጭነት መከታተል እንዲችል የስልክዎን location ና የኢንተርኔት ዳታ አብርተው የጭነት ክትትል የምትለዋን ምናሌ ተጭነው አዲስ ጉዞ ክፈት (ሹፌር) የምትለዋን ተጭነው መረጃ ያጋሩ ጉዞ ሲጨርሱ location off ያድርጉ። ለበለጠ መረጃ 👉 @tanacargopage ይጠቀሙ።\n\n"
            "🙏 እናመሰግናለን!"
        )
    else:
        truck = truck_posts[info["item_index"]]
        request = {
            "type": "truck",
            "truck_index": info["item_index"],
            "truck_owner_id": truck["user_id"],
            "requester_id": requester.id,
            "requester_name": info.get("name", requester.full_name),
            "requester_username": info.get("requester_username", ""),
            "requester_phone": info.get("phone", ""),
            "requester_address": info.get("requester_address", ""),
            "requester_route": "",
            "status": "admin_confirming",
            "full_info_shared": False, "admin_right_confirmed": False,
            "seeker_notified": False, "owner_notified": False,
            "owner_2_notified": False,
            "created_at": datetime.now().isoformat(),
        }
        msg_text = (
            "✅ **እናመሰግናለን!**\n\n"
            "📌 ምዝገባዎን ጨርሰዋል።\n\n"
            "⏳ ከተወሰኑ ደቂቃዎች በኋላ እንደውልልዎታለን።\n\n"
            "ውድ ደንበኛችን ጭነተዎን በgoogle map መከታተል ከፈለጉ የጭነት ክትትል የምትለዋን ምናሌ ተጭነው የኔን ጭነት መከታተል ኮድ ጠይቅ (ደንበኛ) የምትለዋን ተጭነው በመመሪያው መሰረት ይሙሉና ጭነተዎን ይከታተሉ። ለበለጠ መረጃ 👉 @tanacargopage ይጠቀሙ።\n\n"
            "🙏 እናመሰግናለን!"
        )
    connection_requests.append(request)
    req_index = len(connection_requests) - 1
    try:
        db_save_connection_request(req_index, request)
    except Exception as e:
        print(f"CONNECTION REQUEST SAVE ERROR: {e}")
    context.user_data.pop("connect_info", None)
    await update.message.reply_text(msg_text, reply_markup=main_menu(requester.id))
    await send_admin_notification(context, req_index)
    return ConversationHandler.END
async def send_admin_notification(context, req_index):
    # If any configured admin has switched privacy OFF, keep the push alert masked.
    show_private = all(get_admin_pii_visibility(admin_id) for admin_id in get_admin_ids())
    req = connection_requests[req_index]
    if req.get("admin_notified"):
        return
    req["admin_notified"] = True
    if req.get("type") == "cargo":
        cargo = cargo_posts[req["cargo_index"]]
        cargo_owner_name = users.get(cargo["user_id"], {}).get("name", "N/A")
        cargo_owner_username = users.get(cargo["user_id"], {}).get("username", "")
        cargo_owner_phone = cargo.get("phone", "N/A")
        poster_label = "🏷️ Broker" if cargo.get("poster_type") == "broker" else "🏷️ Customer"
        notification_text = (
            "🔔 **TANA CARGO, LOGISTICS & INFORMATION SERVICES — አዲስ ግንኙነት ጥያቄ**\n\n"
            f"📌 Request ID: {req_index}\n\n"
            f"📦 **የጭነት ዝርዝር:** {poster_label}\n"
            f"   • መነሻ: {cargo['from']}\n"
            f"   • መድረሻ: {cargo['to']}\n"
            f"   • አይነት: {cargo['type']}\n"
            f"   • ክብደት: {cargo['weight']}\n"
            f"   • ዋጋ: {cargo.get('price_display', 'N/A')}\n\n"
            f"👤 **የጭነት ባለቤት:**\n"
            f"   • ስም: {cargo_owner_name if show_private else 'የተደበቀ'}\n"
            f"   • Username: @{(cargo_owner_username or 'የለም') if show_private else 'የተደበቀ'}\n"
            f"   • ስልክ: {cargo_owner_phone if show_private else 'የተደበቀ'}\n\n"
            f"👤 **ጠያቂ:**\n"
            f"   • ስም: {req.get('requester_name', 'N/A') if show_private else 'የተደበቀ'}\n"
            f"   • Username: @{(req.get('requester_username', '') or 'የለም') if show_private else 'የተደበቀ'}\n"
            f"   • ስልክ: {req.get('requester_phone', 'N/A') if show_private else 'የተደበቀ'}\n"
            f"   • አድራሻ: {req.get('requester_address', 'N/A') if show_private else 'የተደበቀ'}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 **የግንኙነት ጥያቄ ያቀረቡ ደንበኞች አሉ!**\n\n"
            "⏰ **እባክዎን ቶሎ ያናግሯቸው።**\n\n"
            "👉 ወደ **🤝 የግንኙነት ጥያቄዎች** ምናሌ በመግባት ይመልከቱ።"
        )
    else:
        truck = truck_posts[req["truck_index"]]
        truck_owner_name = users.get(truck["user_id"], {}).get("name", "N/A")
        truck_owner_username = users.get(truck["user_id"], {}).get("username", "")
        truck_owner_phone = truck.get("phone", "N/A")
        poster_label = "🏷️ Broker" if truck.get("poster_type") == "broker" else "🏷️ Customer"
        notification_text = (
            "🔔 **TANA CARGO, LOGISTICS & INFORMATION SERVICES — አዲስ ግንኙነት ጥያቄ**\n\n"
            f"📌 Request ID: {req_index}\n\n"
            f"🚛 **የመኪና ዝርዝር:** {poster_label}\n"
            f"   • አይነት: {truck['type']}\n"
            f"   • አቅም: {truck['capacity']}\n"
            f"   • ታርጋ: {truck.get('plate', 'N/A') if show_private else 'የተደበቀ'}\n"
            f"   • መንገድ: {truck.get('route', 'N/A')}\n\n"
            f"👤 **የመኪና ባለቤት:**\n"
            f"   • ስም: {truck_owner_name if show_private else 'የተደበቀ'}\n"
            f"   • Username: @{(truck_owner_username or 'የለም') if show_private else 'የተደበቀ'}\n"
            f"   • ስልክ: {truck_owner_phone if show_private else 'የተደበቀ'}\n\n"
            f"👤 **ጠያቂ:**\n"
            f"   • ስም: {req.get('requester_name', 'N/A') if show_private else 'የተደበቀ'}\n"
            f"   • Username: @{(req.get('requester_username', '') or 'የለም') if show_private else 'የተደበቀ'}\n"
            f"   • ስልክ: {req.get('requester_phone', 'N/A') if show_private else 'የተደበቀ'}\n"
            f"   • አድራሻ: {req.get('requester_address', 'N/A') if show_private else 'የተደበቀ'}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "📌 **የግንኙነት ጥያቄ ያቀረቡ ደንበኞች አሉ!**\n\n"
            "⏰ **እባክዎን ቶሎ ያናግሯቸው።**\n\n"
            "👉 ወደ **🤝 የግንኙነት ጥያቄዎች** ምናሌ በመግባት ይመልከቱ።"
        )
    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(chat_id=admin_id, text=notification_text)
        except Exception:
            pass

async def admin_right_confirm(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return
    try:
        index = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if index < 0 or index >= len(connection_requests):
        return
    req = connection_requests[index]
    if req.get("admin_right_confirmed"):
        if not req.get("full_info_shared"):
            await share_full_information(index, context)
            await query.answer("የቀድሞ ማጽደቂያው ተጠናቋል።", show_alert=True)
        else:
            await query.answer("⚠️ ይህ ቀድሞ ተረጋግጧል!", show_alert=True)
        return
    req["admin_right_confirmed"] = True
    req["status"] = "approved"
    try: db_save_connection_request(index, req)
    except Exception as e: print(f"CONNECTION APPROVAL SAVE ERROR: {e}")
    if req.get("type") == "cargo":
        ci = req.get("cargo_index")
        if isinstance(ci, int) and 0 <= ci < len(cargo_posts):
            cargo_posts[ci]["active"] = False
            if cargo_posts[ci].get("id"):
                db_update_post_active("cargo_posts", cargo_posts[ci]["id"], False)
    else:
        ti = req.get("truck_index")
        if isinstance(ti, int) and 0 <= ti < len(truck_posts):
            truck_posts[ti]["active"] = False
            if truck_posts[ti].get("id"):
                db_update_post_active("truck_posts", truck_posts[ti]["id"], False)
    await query.edit_message_text(
        "✅ **Right ተረጋግጧል!**\n\n"
        "📌 ጭነቱ/መኪናው ከገበያ ተወግዷል።\n"
        "📌 መረጃው **የግንኙነት ጥያቄዎች** ምናሌ ውስጥ ብቻ ይቀመጣል።\n"
        "📌 ለሁለቱም ወገኖች ማሳወቂያ ተልኳል።"
    )
    await share_full_information(index, context)

async def admin_x_delete(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ ማጥፋት ይችላል!", show_alert=True)
        return
    try:
        index = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if index < 0 or index >= len(connection_requests):
        return
    req = connection_requests[index]
    req["status"] = "deleted"
    try: db_save_connection_request(index, req)
    except Exception as e: print(f"CONNECTION DELETE SAVE ERROR: {e}")
    if req.get("type") == "cargo":
        ci = req.get("cargo_index")
        if isinstance(ci, int) and 0 <= ci < len(cargo_posts):
            cargo_posts[ci]["active"] = True
            if cargo_posts[ci].get("id"):
                db_update_post_active("cargo_posts", cargo_posts[ci]["id"], True)
    else:
        ti = req.get("truck_index")
        if isinstance(ti, int) and 0 <= ti < len(truck_posts):
            truck_posts[ti]["active"] = True
            if truck_posts[ti].get("id"):
                db_update_post_active("truck_posts", truck_posts[ti]["id"], True)
    await query.edit_message_text("❌ **ተሰርዟል!**\n\nይህ ጥያቄ በ Super Admin ተሰርዟል።\n\n📌 ጭነት/መኪናው ነፃ ሆኗል።")
    try:
        await context.bot.send_message(
            chat_id=req["requester_id"],
            text="❌ **ይቅርታ**\n\nየጠየቁት ጭነት/መኪና ውድቅ ሆኗል።\n\nእባክዎ ሌላ ተስማሚ ጭነት/መኪና ይምረጡ።"
        )
    except Exception:
        pass

async def admin_clear_notification(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return
    try:
        await query.delete_message()
    except Exception:
        try:
            await query.edit_message_text("🗑️ ማሳወቂያው ጸድቷል።")
        except Exception:
            pass

async def show_connection_requests(update, context):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text(
            "📨 **ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ**\n\nማንኛውም መረጃ ወይም ፎቶ ለማስተላለፍ ይላኩ።\n\n📌 Admin ወይም Super Admin ብቻ ያየዋል።",
            reply_markup=main_menu(user_id)
        )
        context.user_data["sending_admin_message"] = True
        return
    received = []
    for i, req in enumerate(connection_requests):
        if req["status"] not in ["deleted"]:
            received.append((i, req))
    conn = _db_connect(DB_PATH, timeout=10)
    conn.row_factory = dict_row
    try:
        vehicle_inquiries = [dict(r) for r in conn.execute(
            """SELECT vi.*, vl.owner_name AS listing_owner_name, vl.phone AS listing_owner_phone,
                      vl.plate AS listing_plate
               FROM vehicle_inquiries vi
               LEFT JOIN vehicle_listings vl ON vl.id=vi.listing_id
               WHERE vi.status='new' AND vi.category IN ('rental','sale') ORDER BY vi.id DESC LIMIT 100"""
        ).fetchall()]
        property_inquiries = [dict(r) for r in conn.execute(
            """SELECT pi.*, pl.property_type, pl.listing_mode, pl.location AS listing_location,
                      pl.price AS listing_price, pl.phone AS listing_owner_phone
               FROM property_inquiries pi
               LEFT JOIN property_listings pl ON pl.id=pi.listing_id
               WHERE pi.status='new' ORDER BY pi.id DESC LIMIT 100"""
        ).fetchall()]
    finally:
        conn.close()
    if not received and not vehicle_inquiries and not property_inquiries:
        await update.message.reply_text("🤝 እስካሁን የጭነት፣ የመኪና/ማሽነሪ ወይም የቤት/አፓርታማ ሽያጭና ኪራይ ግንኙነት ጥያቄ የለም።", reply_markup=main_menu(user_id))
        return
    show_private = get_admin_pii_visibility(user_id)
    for i, req in received:
        if req.get("type") == "truck":
            truck = truck_posts[req["truck_index"]]
            truck_owner_name = users.get(req["truck_owner_id"], {}).get("name", "N/A")
            truck_owner_username = users.get(req["truck_owner_id"], {}).get("username", "")
            truck_owner_phone = truck.get("phone", "N/A")
            truck_poster = "🏷️ Broker" if truck.get("poster_type") == "broker" else "🏷️ Customer"
            text = (
                f"📥 **ጥያቄ #{i + 1}**\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🚛 **የመኪና ባለቤት:** {truck_poster}\n"
                f"   • ስም: {truck_owner_name if show_private else 'የተደበቀ'}\n"
                f"   • Username: @{(truck_owner_username or 'የለም') if show_private else 'የተደበቀ'}\n"
                f"   • ስልክ: {truck_owner_phone if show_private else 'የተደበቀ'}\n"
                f"   • አይነት: {truck['type']}\n"
                f"   • አቅም: {truck['capacity']}\n"
                f"   • ታርጋ: {truck.get('plate', 'N/A') if show_private else 'የተደበቀ'}\n"
                f"   • መንገድ: {truck.get('route', 'N/A')}\n"
                f"   • ሹፌር: {truck.get('driver', 'N/A') if show_private else 'የተደበቀ'}\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **የጭነት ፈላጊ ደንበኛ:**\n"
                f"   • ስም: {req.get('requester_name', 'N/A') if show_private else 'የተደበቀ'}\n"
                f"   • Username: @{(req.get('requester_username', '') or 'የለም') if show_private else 'የተደበቀ'}\n"
                f"   • ስልክ: {req.get('requester_phone', 'N/A') if show_private else 'የተደበቀ'}\n"
                f"   • አድራሻ: {req.get('requester_address', 'N/A') if show_private else 'የተደበቀ'}\n\n"
                f"📌 ሁኔታ: {req['status']}"
            )
        else:
            cargo = cargo_posts[req["cargo_index"]]
            cargo_owner_name = users.get(req["cargo_owner_id"], {}).get("name", "N/A")
            cargo_owner_username = users.get(req["cargo_owner_id"], {}).get("username", "")
            cargo_owner_phone = cargo.get("phone", "N/A")
            cargo_poster = "🏷️ Broker" if cargo.get("poster_type") == "broker" else "🏷️ Customer"
            text = (
                f"📥 **ጥያቄ #{i + 1}**\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 **የጭነት ባለቤት:** {cargo_poster}\n"
                f"   • ስም: {cargo_owner_name if show_private else 'የተደበቀ'}\n"
                f"   • Username: @{(cargo_owner_username or 'የለም') if show_private else 'የተደበቀ'}\n"
                f"   • ስልክ: {cargo_owner_phone if show_private else 'የተደበቀ'}\n"
                f"   • መነሻ: {cargo['from']}\n"
                f"   • መድረሻ: {cargo['to']}\n"
                f"   • አይነት: {cargo['type']}\n"
                f"   • ክብደት: {cargo['weight']}\n"
                f"   • ዋጋ: {cargo.get('price_display', 'N/A')}\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 **የመኪና ፈላጊ ደንበኛ:**\n"
                f"   • ስም: {req.get('requester_name', 'N/A') if show_private else 'የተደበቀ'}\n"
                f"   • Username: @{(req.get('requester_username', '') or 'የለም') if show_private else 'የተደበቀ'}\n"
                f"   • ስልክ: {req.get('requester_phone', 'N/A') if show_private else 'የተደበቀ'}\n"
                f"   • አድራሻ: {req.get('requester_address', 'N/A') if show_private else 'የተደበቀ'}\n\n"
                f"📌 ሁኔታ: {req['status']}"
            )
        buttons = [[
            InlineKeyboardButton("✅ Right", callback_data=f"adminright_{i}"),
            InlineKeyboardButton("❌ X አጥፋ", callback_data=f"adminxdelete_{i}"),
        ]]
        if is_super_admin(user_id):
            buttons.append([
                InlineKeyboardButton(f"🗑️ ሙሉ በሙሉ አጥፋ #{i + 1} (Super)", callback_data=f"admindeleteconnection_{i}")
            ])
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

    for inquiry in vehicle_inquiries:
        category_label = {"rental": "የኪራይ መኪና", "sale": "የሽያጭ መኪና"}.get(inquiry.get("category"), "መኪና")
        owner = users.get(int(inquiry.get("owner_id") or 0), {})
        buyer_username = inquiry.get("buyer_username") or "የለም"
        owner_username = owner.get("username") or "የለም"
        msg = (
            f"📩 የተሽከርካሪ ጥያቄ #{inquiry['id']} — {category_label}\n"
            f"🚘 አይነት፦ {inquiry.get('vehicle_type') or '—'}\n"
            f"🗂️ ማስታወቂያ መዝገብ፦ #{inquiry.get('listing_id')}\n\n"
            f"👤 ባለቤት፦ {(inquiry.get('listing_owner_name') or owner.get('name', '—')) if show_private else 'የተደበቀ'}\n"
            f"🔗 ባለቤት Username፦ @{owner_username if show_private else 'የተደበቀ'}\n"
            f"📞 ባለቤት ስልክ፦ {(inquiry.get('listing_owner_phone') or '—') if show_private else 'የተደበቀ'}\n"
            f"🔢 ታርጋ፦ {(inquiry.get('listing_plate') or '—') if show_private else 'የተደበቀ'}\n\n"
            f"👤 ፈላጊ፦ {inquiry.get('buyer_name') if show_private else 'የተደበቀ'}\n"
            f"🔗 Username፦ @{buyer_username if show_private else 'የተደበቀ'}\n"
            f"📞 ስልክ፦ {inquiry.get('buyer_phone') if show_private else 'የተደበቀ'}\n"
            f"🕒 {inquiry.get('created_at')}"
        )
        await update.message.reply_text(msg)

    for inquiry in property_inquiries:
        property_kind = "መኖሪያ" if inquiry.get("property_type") == "residential" else "የንግድ/የድርጅት"
        listing_mode = "ሽያጭ" if inquiry.get("listing_mode") == "sale" else "ኪራይ"
        msg = (
            f"🏠 የንብረት ግንኙነት ጥያቄ #{inquiry['id']}\n"
            f"🏷️ ዓይነት፦ {property_kind} — {listing_mode}\n"
            f"📍 የንብረቱ ቦታ፦ {inquiry.get('listing_location') or '—'}\n"
            f"💰 ዋጋ፦ {inquiry.get('listing_price') or '—'}\n"
            f"🆔 የማስታወቂያ ቁጥር፦ #{inquiry.get('listing_id')}\n\n"
            f"👤 ጠያቂ፦ {inquiry.get('buyer_name') if show_private else 'የተደበቀ'}\n"
            f"📞 ስልክ፦ {inquiry.get('buyer_phone') if show_private else 'የተደበቀ'}\n"
            f"📝 መልዕክት፦ {inquiry.get('message') or '—'}\n"
            f"🕒 ቀን፦ {inquiry.get('created_at') or '—'}"
        )
        await update.message.reply_text(msg)

async def show_gps_requests(update, context):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Admin ብቻ የGPS ጥያቄዎችን ማየት ይችላል።")
        return
    show_private = get_admin_pii_visibility(uid)
    pending = []
    for i, info in enumerate(gmaps_share_requests):
        if info.get("status", "pending") == "pending":
            pending.append(("gmaps", i, info))
    for i, info in enumerate(tracking_stops):
        if info.get("status", "pending") == "pending":
            pending.append(("stop", i, info))
    if not pending:
        await update.message.reply_text("📍 አሁን የሚጠብቅ የGPS ጥያቄ የለም።", reply_markup=main_menu(uid))
        return
    await update.message.reply_text(f"📍 የሚጠብቁ የGPS ጥያቄዎች፦ {len(pending)}")
    for kind, index, info in pending:
        if kind == "gmaps":
            msg = (
                f"🗺️ Google Maps ጥያቄ #{index + 1}\n"
                f"👤 ላኪ፦ {info.get('sender_name', '—') if show_private else 'የተደበቀ'}\n"
                f"📍 አድራሻ፦ {info.get('address', '—') if show_private else 'የተደበቀ'}\n"
                f"🎯 ተቀባይ፦ {info.get('name', '—') if show_private else 'የተደበቀ'}\n"
                f"📞 ስልክ፦ {info.get('phone', '—') if show_private else 'የተደበቀ'}\n"
                f"🕒 {info.get('timestamp', '—')}"
            )
            buttons = [[
                InlineKeyboardButton("✅ Approve & Send Link", callback_data=f"approvegmaps_{index}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"rejectgmaps_{index}")
            ]]
        else:
            msg = (
                f"🛑 የክትትል ማቆሚያ ጥያቄ #{index + 1}\n"
                f"👤 ጠያቂ፦ {info.get('requester_name', '—') if show_private else 'የተደበቀ'}\n"
                f"🎯 የሚከታተለው ሰው፦ {info.get('name', '—') if show_private else 'የተደበቀ'}\n"
                f"📞 ስልክ፦ {info.get('phone', '—') if show_private else 'የተደበቀ'}\n"
                f"🕒 {info.get('timestamp', '—')}"
            )
            buttons = [[
                InlineKeyboardButton("✅ ክትትል አቁም", callback_data=f"approvestop_{index}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"rejectstop_{index}")
            ]]
        await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(buttons))


async def admin_delete_connection(update, context):
    query = update.callback_query
    await query.answer()
    if not is_super_admin(update.effective_user.id):
        await query.answer("❌ Super Admin ብቻ!", show_alert=True)
        return
    index = int(query.data.split("_")[-1])
    if index < 0 or index >= len(connection_requests):
        return
    req = connection_requests[index]
    req["status"] = "deleted"
    try: db_save_connection_request(index, req)
    except Exception as e: print(f"CONNECTION DELETE SAVE ERROR: {e}")
    # Re-open the cargo/truck listing when the request itself is removed.
    if req.get("type") == "cargo":
        ci = req.get("cargo_index")
        if isinstance(ci, int) and 0 <= ci < len(cargo_posts):
            cargo_posts[ci]["active"] = True
            if cargo_posts[ci].get("id"):
                db_update_post_active("cargo_posts", cargo_posts[ci]["id"], True)
    elif req.get("type") == "truck":
        ti = req.get("truck_index")
        if isinstance(ti, int) and 0 <= ti < len(truck_posts):
            truck_posts[ti]["active"] = True
            if truck_posts[ti].get("id"):
                db_update_post_active("truck_posts", truck_posts[ti]["id"], True)
    await query.edit_message_text("✅ ጥያቄው ተሰርዟል፤ ጭነቱ/መኪናው እንደገና ንቁ ሆኗል።")

# ==================================================
# ADMIN MESSAGE HANDLING
# ==================================================

async def admin_photo_router(update, context):
    if context.user_data.get("media_compose_photo_waiting") and update.effective_user and is_admin(update.effective_user.id):
        context.user_data.pop("media_compose_photo_waiting",None)
        photo=update.message.photo[-1].file_id if update.message.photo else None
        if not photo: return await update.message.reply_text("እባክዎ የፎቶ ፋይል ይላኩ።")
        caption=(update.message.caption or "").strip()
        branded=(caption+"\n\n" if caption else "")+f"🚛 TANA CARGO, LOGISTICS & INFORMATION SERVICES | ጣና ጭነት\n📞 {SUPPORT_PHONE}\n🤖 https://t.me/{BOT_USERNAME}\n📢 https://t.me/{CHANNEL_USERNAME}"
        await update.message.reply_photo(photo=photo,caption=branded[:1024])
        return
    if context.user_data.get("sending_admin_message"):
        return await handle_user_photo(update, context)
    return

async def handle_user_message(update, context):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    username = update.effective_user.username or "የለም"
    name = update.effective_user.full_name
    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "📨 **TANA CARGO, LOGISTICS & INFORMATION SERVICES — አዲስ መልዕክት**\n\n"
                    f"👤 ስም፦ {name}\n"
                    f"🔗 Username፦ @{username}\n"
                    f"🆔 Telegram ID፦ {user_id}\n\n"
                    f"💬 **መልዕክት፦**\n{text}"
                )
            )
        except Exception:
            pass
    await update.message.reply_text(
        "✅ መልዕክትዎ ለ Admin/Super Admin ተልኳል።\n\n📸 ፎቶም መላክ ይችላሉ!",
        reply_markup=main_menu(user_id)
    )

async def handle_user_photo(update, context):
    user_id = update.effective_user.id
    username = update.effective_user.username or "የለም"
    name = update.effective_user.full_name
    caption = update.message.caption or ""
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_type = "photo"
    elif update.message.document:
        file_id = update.message.document.file_id
        file_type = "document"
    else:
        return
    for admin_id in get_admin_ids():
        try:
            text = (
                "📸 **TANA CARGO, LOGISTICS & INFORMATION SERVICES — አዲስ ፎቶ/ስክሪንሾት**\n\n"
                f"👤 ስም፦ {name}\n"
                f"🔗 Username፦ @{username}\n"
                f"🆔 Telegram ID፦ {user_id}\n"
            )
            if caption:
                text += f"\n💬 **ጽሑፍ፦**\n{caption}"
            if file_type == "photo":
                await context.bot.send_photo(chat_id=admin_id, photo=file_id, caption=text)
            else:
                await context.bot.send_document(chat_id=admin_id, document=file_id, caption=text)
        except Exception:
            pass
    await update.message.reply_text(
        "✅ ፎቶ/ስክሪንሾትዎ ለ Admin/Super Admin ተልኳል።\n\n📌 ሌላ መልዕክት ወይም ፎቶ መላክ ይችላሉ።",
        reply_markup=main_menu(user_id)
    )

async def _start_admin_message_mode(update, context):
    user_id = update.effective_user.id
    await update.message.reply_text(
        "📨 **ወደ ጣና ጭነት መረጃና ፎቶ ለመላክ**\n\nማንኛውም መረጃ ወይም ፎቶ ለማስተላለፍ ይላኩ።\n\n📌 Admin ወይም Super Admin ብቻ ያየዋል።",
        reply_markup=main_menu(user_id)
    )
    context.user_data["sending_admin_message"] = True

# ==================================================

# ==================================================
# PRIVATE TANA CARGO, LOGISTICS & INFORMATION SERVICES LIVE TRACKING LINKS
# ==================================================

def create_public_tracking_link(label="TANA CARGO, LOGISTICS & INFORMATION SERVICES", device_id=None):
    """Create a branded live page bound to the selected Traccar device."""
    token = secrets.token_urlsafe(24)
    device_id = str(device_id or TRACCAR_DEVICE_ID).strip()
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        conn.execute(
            "INSERT INTO public_tracking_tokens (token, device_id, status, label, created_at) VALUES (?, ?, 'active', ?, ?)",
            (token, device_id, label, datetime.now().astimezone().isoformat(timespec="seconds"))
        )
        conn.commit()
    finally:
        conn.close()
    if not PUBLIC_BASE_URL:
        return token, ""
    return token, f"{PUBLIC_BASE_URL}/track/{token}"

def get_public_tracking_token(token):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    conn.row_factory = dict_row
    try:
        row = conn.execute("SELECT * FROM public_tracking_tokens WHERE token=? AND status='active'", (token,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def revoke_public_tracking_token(token):
    if not token:
        return
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        conn.execute("UPDATE public_tracking_tokens SET status='revoked' WHERE token=?", (token,))
        conn.commit()
    finally:
        conn.close()

def username_of_user(user_id):
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        row = conn.execute("SELECT username FROM users WHERE user_id=?", (int(user_id),)).fetchone()
        return (row[0] or "") if row else ""
    except Exception:
        return ""
    finally:
        conn.close()

# ==================================================
# RENTAL TRACKING + ADMIN/SUPER ADMIN MESSAGING
# ==================================================

def build_live_tracking_url(user_id=None):
    # Give viewers the branded TANA CARGO, LOGISTICS & INFORMATION SERVICES page, never the Traccar login/dashboard.
    device_id = get_traccar_device_id_for_user(user_id)
    _token, url = create_public_tracking_link("Cargo/Truck tracking", device_id)
    return url

async def rental_tracking_start(update, context):
    context.user_data["rental_tracking"] = {}
    await update.message.reply_text(
        "🚐 **TANA CARGO, LOGISTICS & INFORMATION SERVICES — የኪራይ መኪና GPS**\n\n"
        "የክትትል መረጃው እንዲደርሰው የሚፈልጉትን ደንበኛ ስም ያስገቡ።"
    )
    return RENTAL_TARGET_NAME

async def rental_target_name(update, context):
    name = (update.message.text or "").strip()
    if not name:
        await update.message.reply_text("❌ እባክዎ ስም ያስገቡ።")
        return RENTAL_TARGET_NAME
    context.user_data.setdefault("rental_tracking", {})["target_name"] = name
    await update.message.reply_text(
        "📞 የደንበኛውን ስልክ ቁጥር ያስገቡ።\nምሳሌ፦ 0912345678"
    )
    return RENTAL_TARGET_PHONE

async def rental_target_phone(update, context):
    phone = (update.message.text or "").strip().split("\n")[0]
    valid, err = validate_phone(phone)
    if not valid:
        await update.message.reply_text(f"❌ {err}\nምሳሌ፦ 0912345678")
        return RENTAL_TARGET_PHONE
    info = context.user_data.get("rental_tracking", {})
    rid = db_create_rental_request(
        update.effective_user.id, update.effective_user.full_name,
        info.get("target_name", ""), phone
    )
    context.user_data.pop("rental_tracking", None)
    await update.message.reply_text(
        "✅ ምዝገባው በትክክል ተከናውኗል።\n\n"
        "የTANA CARGO, LOGISTICS & INFORMATION SERVICES ቡድን ጥያቄዎን እንዲያጸድቅ ተልኳል። "
        "ከጸደቀ በኋላ የክትትል መረጃው ለተመረጡት ሰው እንዲደርስ እናደርጋለን።",
        reply_markup=main_menu(update.effective_user.id)
    )
    req = db_get_rental_request(rid)
    registered_target = find_registered_user_by_phone(req["target_phone"])
    target_username = username_of_user(registered_target) if registered_target else ""
    admin_text = (
        "🚐 **TANA CARGO, LOGISTICS & INFORMATION SERVICES — አዲስ የኪራይ መኪና GPS ጥያቄ**\n\n"
        f"🆔 መዝገብ: #{rid}\n"
        f"👤 ጠያቂ: {req['requester_name']} (ID: {req['requester_id']})\n"
        f"🎯 ክትትል የሚፈለገው: {req['target_name']}\n"
        f"📞 ስልክ: {req['target_phone']}\n"
        f"🔹 Telegram: @{target_username if target_username else 'ገና አልተመዘገበም'}\n"
        f"🕒 ቀን: {req['created_at']}\n\n"
        "የክትትል መረጃው ከመላኩ በፊት ፈቃድ ያስፈልጋል።"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"rentapprove_{rid}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"rentreject_{rid}")
    ]])
    for aid in get_admin_ids():
        try:
            await context.bot.send_message(aid, admin_text, reply_markup=kb)
        except Exception as e:
            print(f"Rental admin notify failed for {aid}: {e}")
    return ConversationHandler.END

async def admin_rental_approve(update, context):
    q = update.callback_query
    await q.answer()
    if not is_admin(update.effective_user.id):
        await q.answer("Admin ብቻ ይህን ማድረግ ይችላል።", show_alert=True)
        return
    rid = int(q.data.split("_")[-1])
    rec = db_get_rental_request(rid)
    if not rec or rec["status"] != "pending":
        await q.edit_message_text("ይህ ጥያቄ አስቀድሞ ተወስኗል ወይም አልተገኘም።")
        return
    tracking_device_id = get_traccar_device_id_for_user(rec["requester_id"])
    loc = get_traccar_location(tracking_device_id)
    map_url = ""
    if loc and loc.get("latitude") is not None and loc.get("longitude") is not None:
        map_url = f"https://maps.google.com/?q={loc['latitude']},{loc['longitude']}"
    public_token, live_url = create_public_tracking_link(f"Rental #{rid}", tracking_device_id)
    target_uid = find_registered_user_by_phone(rec["target_phone"])
    db_update_rental_request(
        rid, target_user_id=target_uid, map_url=map_url,
        traccar_url=live_url or f"TOKEN:{public_token}", status="approved",
        approved_at=datetime.now().astimezone().isoformat(timespec="seconds")
    )
    await q.edit_message_text(
        f"✅ የኪራይ መኪና GPS ጥያቄ #{rid} ጸድቋል።\n"
        f"🎯 {rec['target_name']}\n"
        f"📞 {rec['target_phone']}\n"
        f"👤 Telegram user: {target_uid or 'ገና አልተገኘም'}"
    )
    if target_uid:
        try:
            await send_rental_tracking_to_user(context, rid, target_uid)
        except Exception as e:
            print(f"Could not send approved rental tracking to user {target_uid}: {e}")
            target_uid = None
    if not target_uid:
        approved_rec = db_get_rental_request(rid)
        deep_link = f"https://t.me/{BOT_USERNAME}?start=rent_{approved_rec['share_token']}"
        share_text = (
            "🚐 TANA CARGO, LOGISTICS & INFORMATION SERVICES | የቀጥታ ክትትል\n"
            "የክትትል መረጃውን ለመቀበል ከታች ያለውን ሊንክ ይጫኑ።\n"
            f"{deep_link}\n\n"
            "👉 @tanacargobot"
        )
        try:
            await context.bot.send_message(
                rec["requester_id"],
                "✅ ጥያቄው ጸድቋል። ተቀባዩ በTANA CARGO, LOGISTICS & INFORMATION SERVICES ላይ ካልተመዘገበ "
                "ይህን መልዕክት Copy በማድረግ ለእርሱ ያጋሩት።\n\n"
                + share_text
            )
        except Exception:
            pass
    else:
        try:
            await context.bot.send_message(
                rec["requester_id"],
                f"✅ የክትትል መረጃው ለ{rec['target_name']} በTANA CARGO, LOGISTICS & INFORMATION SERVICES ቦት ተልኳል።"
            )
        except Exception:
            pass

async def send_rental_tracking_to_user(context, rid, user_id):
    rec = db_get_rental_request(rid)
    if not rec or rec["status"] != "approved":
        return
    stored_url = rec.get("traccar_url") or ""
    token = stored_url.split("TOKEN:", 1)[1] if stored_url.startswith("TOKEN:") else ""
    live_url = f"{PUBLIC_BASE_URL}/track/{token}" if token and PUBLIC_BASE_URL else stored_url
    if not live_url or live_url.startswith("TOKEN:"):
        await context.bot.send_message(user_id, "⚠️ የTANA CARGO, LOGISTICS & INFORMATION SERVICES Live Tracking ገጽ አድራሻ አልተዋቀረም። Admin እንዲያጣራ ያሳውቁ።")
        return
    username = username_of_user(user_id)
    text = (
        "🚐 **TANA CARGO, LOGISTICS & INFORMATION SERVICES — የኪራይ ቀጥታ ክትትል**\n\n"
        f"👤 ለ: {rec['target_name']}\n"
        f"🔹 Telegram: @{username}\n" if username else
        "🚐 **TANA CARGO, LOGISTICS & INFORMATION SERVICES — የኪራይ ቀጥታ ክትትል**\n\n"
        f"👤 ለ: {rec['target_name']}\n"
    )
    text += (
        "የክትትል መረጃው በTANA CARGO, LOGISTICS & INFORMATION SERVICES ቡድን ተፈቅዷል።\n"
        f"🗺️ [TANA CARGO, LOGISTICS & INFORMATION SERVICES Live Tracking ክፈት]({live_url})\n"
        "👉 @tanacargobot"
    )
    await context.bot.send_message(user_id, text, disable_web_page_preview=True)
    if rec.get("map_url"):
        await context.bot.send_message(
            user_id, f"📍 የመጨረሻ የተገኘ ቦታ: {rec['map_url']}",
            disable_web_page_preview=True
        )

async def admin_rental_reject(update, context):
    q = update.callback_query
    await q.answer()
    if not is_admin(update.effective_user.id):
        await q.answer("Admin ብቻ።", show_alert=True)
        return
    rid = int(q.data.split("_")[-1])
    rec = db_get_rental_request(rid)
    if not rec or rec["status"] != "pending":
        await q.edit_message_text("ይህ ጥያቄ አስቀድሞ ተወስኗል።")
        return
    db_update_rental_request(rid, status="rejected")
    await q.edit_message_text(f"❌ የኪራይ መኪና GPS ጥያቄ #{rid} ውድቅ ተደርጓል።")
    try:
        await context.bot.send_message(rec["requester_id"], "❌ የክትትል ጥያቄዎ አልጸደቀም።")
    except Exception:
        pass

async def show_tracking_registry(update, context):
    if not is_admin(update.effective_user.id):
        return
    rental_rows = db_list_rental_requests()
    cargo_rows = db_list_tracking_sessions()
    await update.message.reply_text(
        f"📚 የክትትል መዝገብ\n🚐 የኪራይ ጥያቄዎች: {len(rental_rows)}\n"
        f"🚚 የጭነት/መኪና ክትትሎች: {len(cargo_rows)}"
    )
    for r in rental_rows:
        txt = (
            f"🚐 #{r['id']} | {r['status']}\n"
            f"👤 ጠያቂ: {r['requester_name']} / ID {r['requester_id']}\n"
            f"🎯 ተቀባይ: {r['target_name']}\n📞 {r['target_phone']}\n"
            f"🕒 {r['created_at']}\n"
            f"📍 የተቀባይ Telegram ID: {r['target_user_id'] or 'የለም'}"
        )
        buttons = []
        if r["status"] == "approved" and r.get("traccar_url"):
            saved = r["traccar_url"]
            if saved.startswith("TOKEN:") and PUBLIC_BASE_URL:
                saved = f"{PUBLIC_BASE_URL}/track/{saved.split('TOKEN:', 1)[1]}"
            if saved.startswith("http"):
                buttons.append(InlineKeyboardButton("🗺️ Live ክፈት", url=saved))
            buttons.append(InlineKeyboardButton("🛑 ክትትል አቁም", callback_data=f"rentstop_{r['id']}"))
        await update.message.reply_text(
            txt, reply_markup=InlineKeyboardMarkup([buttons]) if buttons else None
        )
    for r in cargo_rows:
        txt = (
            f"🚚 የጭነት/መኪና ክትትል — {r.get('tracking_code')}\n"
            f"👤 ሹፌር/ላኪ ID: {r.get('driver_user_id')}\n"
            f"👤 ደንበኛ ID: {r.get('customer_user_id')}\n"
            f"📞 ደንበኛ: {r.get('customer_name') or 'N/A'} / {r.get('customer_phone') or 'N/A'}\n"
            f"📞 ሹፌር: {r.get('driver_phone') or 'N/A'}\n"
            f"📍 {r.get('origin') or 'N/A'} ➜ {r.get('destination') or 'N/A'}\n"
            f"🚦 ሁኔታ: {r.get('status')}\n"
            f"🕒 የመጨረሻ አድራሻ ማዘመኛ: {r.get('location_updated_at') or 'N/A'}"
        )
        kb = None
        if r.get("status") in ("ready", "in_transit"):
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🗺️ Live ክፈት", url=build_live_tracking_url())
            ]])
        await update.message.reply_text(txt, reply_markup=kb)


async def admin_rental_stop(update, context):
    q = update.callback_query
    await q.answer()
    if not is_admin(update.effective_user.id):
        await q.answer("Admin ብቻ።", show_alert=True)
        return
    rid = int(q.data.split("_")[-1])
    rec = db_get_rental_request(rid)
    if not rec or rec["status"] != "approved":
        await q.edit_message_text("ንቁ የክትትል መዝገብ አልተገኘም።")
        return
    db_update_rental_request(
        rid, status="stopped",
        stopped_at=datetime.now().astimezone().isoformat(timespec="seconds")
    )
    old_url = rec.get("traccar_url") or ""
    if old_url.startswith("TOKEN:"):
        revoke_public_tracking_token(old_url.split("TOKEN:", 1)[1])
    # Remove the recipient mapping from the active record while retaining audit history.
    db_update_rental_request(rid, target_user_id=None, traccar_url="", map_url="")
    await q.edit_message_text(f"🛑 የክትትል ፈቃድ #{rid} ተዘግቷል።")
    for uid in (rec.get("target_user_id"), rec.get("requester_id")):
        if uid:
            try:
                await context.bot.send_message(uid, "🛑 የTANA CARGO, LOGISTICS & INFORMATION SERVICES ክትትል ፈቃድ በአድሚን ተዘግቷል።")
            except Exception:
                pass

ADMIN_BROADCAST_MENU = "📣 መልእክት ላክ"
ADMIN_INFO_STORAGE = "🗂️ ጠቅላላ መረጃ ማከማቻ"

async def admin_broadcast_menu(update, context):
    if not is_admin(update.effective_user.id):
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 የግዢና የሽያጭ ጨረታ ተመዝጋቢዎች", callback_data="adm_send_purchase_sales")],
        [InlineKeyboardButton("🏗️ የግንባታ ጨረታ ተመዝጋቢዎች", callback_data="adm_send_construction")],
        [InlineKeyboardButton("💼 የሥራ ፍለጋ ተመዝጋቢዎች", callback_data="adm_send_jobs")],
        [InlineKeyboardButton("🤝 የድለላ መረጃ ተመዝጋቢዎች", callback_data="adm_send_broker_info")],
        [InlineKeyboardButton("👥 ሁሉም የተመዘገቡ ደንበኞች", callback_data="adm_send_all")],
        [InlineKeyboardButton("📱 በስልክ ቁጥር ደንበኛ ፈልግ", callback_data="adm_send_phone")],
        [InlineKeyboardButton("👤 ደንበኞችን ከዝርዝር መምረጥ", callback_data="adm_send_people")],
    ])
    await update.message.reply_text("📣 የመልእክት ማሰራጫ\nለማን መላክ ይፈልጋሉ?", reply_markup=kb)

async def admin_broadcast_audience_callback(update, context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id):
        return await q.answer("Admin/Super Admin ብቻ።", show_alert=True)
    mode=q.data.replace("adm_send_", "")
    if mode in ("purchase_sales", "construction", "jobs", "broker_info"):
        conn=_db_connect(DB_PATH,timeout=10)
        try:
            rows=conn.execute("SELECT user_id,full_name,phone,telegram_name FROM info_category_subscriptions WHERE active=1 AND category=? ORDER BY full_name",(mode,)).fetchall()
        finally: conn.close()
        users=[{"user_id":int(r["user_id"]),"name":r["full_name"],"phone":r["phone"],"username":r["telegram_name"]} for r in rows]
        if not users:
            return await q.message.reply_text("በዚህ ዘርፍ ንቁ የተመዘገቡ ደንበኞች አልተገኙም።")
        context.user_data["broadcast_mode"]="info:"+mode
        context.user_data["broadcast_admin_mode"]=True
        context.user_data["broadcast_category_label"]={"purchase_sales":"የግዢና የሽያጭ መረጃ","construction":"የግንባታ ጨረታ መረጃ","jobs":"የሥራ ፍለጋ መረጃ","broker_info":"የድለላ መረጃ"}[mode]
        context.user_data["broadcast_users"]=users
        context.user_data["broadcast_selected_users"]=[]
        await q.message.reply_text(f"{context.user_data['broadcast_category_label']} ተመዝጋቢዎች፦ {len(users)}\nስም፣ ስልክና Telegram username እያዩ የሚላክላቸውን ይምረጡ።")
        await admin_broadcast_people_page(q.message,context,0)
        return
    if mode == "all":
        users=[{"user_id":int(u["user_id"]),"name":u.get("name"),"phone":u.get("owner_phone"),"username":u.get("username")} for u in db_list_registered_users()]
        context.user_data["broadcast_mode"]="all"
        context.user_data["broadcast_admin_mode"]=True
        context.user_data["broadcast_users"]=users
        context.user_data["broadcast_selected_users"]=[]
        if not users: return await q.message.reply_text("የተመዘገቡ ደንበኞች አልተገኙም።")
        await admin_broadcast_people_page(q.message,context,0)
        return
    if mode == "phone":
        context.user_data["admin_phone_lookup_waiting"] = True
        await q.message.reply_text("📱 በ /start የተመዘገበውን የደንበኛ ስልክ ቁጥር ያስገቡ። Telegram በማንኛውም የማይታወቅ ቁጥር ላይ አካውንት ማፈለግ አይችልም።")
        return
    if mode == "tender":
        return await tender_broadcast_start_from_callback(q, context)
    if mode == "brokers":
        context.user_data["broadcast_mode"]="brokers"
        context.user_data["broadcast_admin_mode"]=True
        await q.message.reply_text("📝 ለተፈቀዱ ደላሎች የሚላከውን መልእክት ይጻፉ።")
        return
    users=db_list_registered_users()
    if not users:
        return await q.message.reply_text("የተመዘገቡ ተጠቃሚዎች አልተገኙም።")
    context.user_data["broadcast_users"]=users
    context.user_data["broadcast_admin_mode"]=True
    context.user_data["broadcast_selected_users"]=[]
    await admin_broadcast_people_page(q.message,context,0)

async def tender_broadcast_start_from_callback(q, context):
    kb=ReplyKeyboardMarkup([["የግዢ ጨረታ"],["የሽያጭ ጨረታ"],["የግንባታ ጨረታ"],["ሁሉም መረጃ"],["❌ ሰርዝ"]],resize_keyboard=True)
    await q.message.reply_text("📢 መረጃውን ለየትኛው የተመዝጋቢ ቡድን መላክ ይፈልጋሉ?",reply_markup=kb)

async def admin_broadcast_people_page(message,context,offset=0):
    users=context.user_data.get("broadcast_users",[]); selected=set(context.user_data.get("broadcast_selected_users",[])); size=12
    rows=[]
    for u in users[offset:offset+size]:
        uid=int(u["user_id"]); mark="☑️" if uid in selected else "⬜"
        label=(u.get("name") or "ተጠቃሚ")[:18]
        phone=(u.get("phone") or "ስልክ የለም")[:16]
        username=("@"+u.get("username")) if u.get("username") else "username የለም"
        rows.append([InlineKeyboardButton(f"{mark} {label} | {phone} | {username[:14]}",callback_data=f"adm_person_toggle_{uid}")])
    nav=[]
    if offset: nav.append(InlineKeyboardButton("⬅️",callback_data=f"adm_people_page_{max(0,offset-size)}"))
    if offset+size<len(users): nav.append(InlineKeyboardButton("➡️",callback_data=f"adm_people_page_{offset+size}"))
    if nav: rows.append(nav)
    rows.append([InlineKeyboardButton("✅ የተመረጡትን ቀጥል",callback_data="adm_people_done")])
    await message.reply_text(f"👤 ተቀባዮችን ይምረጡ። የተመረጡ፦ {len(selected)}",reply_markup=InlineKeyboardMarkup(rows))

async def admin_broadcast_people_callback(update,context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin ብቻ።",show_alert=True)
    data=q.data
    if data.startswith("adm_people_page_"):
        offset=int(data.rsplit("_",1)[1]); users=context.user_data.get("broadcast_users",[]); selected=set(context.user_data.get("broadcast_selected_users",[])); size=12; rows=[]
        for u in users[offset:offset+size]:
            uid=int(u["user_id"]); mark="☑️" if uid in selected else "⬜"
            label=(u.get("name") or "ተጠቃሚ")[:18]; phone=(u.get("phone") or "ስልክ የለም")[:16]
            username=("@"+u.get("username")) if u.get("username") else "username የለም"
            rows.append([InlineKeyboardButton(f"{mark} {label} | {phone} | {username[:14]}",callback_data=f"adm_person_toggle_{uid}")])
        nav=[]
        if offset: nav.append(InlineKeyboardButton("⬅️",callback_data=f"adm_people_page_{max(0,offset-size)}"))
        if offset+size<len(users): nav.append(InlineKeyboardButton("➡️",callback_data=f"adm_people_page_{offset+size}"))
        if nav: rows.append(nav)
        rows.append([InlineKeyboardButton("✅ የተመረጡትን ቀጥል",callback_data="adm_people_done")])
        return await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows))
    if data.startswith("adm_person_toggle_"):
        uid=int(data.rsplit("_",1)[1]); chosen=set(context.user_data.get("broadcast_selected_users",[]))
        chosen.symmetric_difference_update({uid}); context.user_data["broadcast_selected_users"]=list(chosen)
        users=context.user_data.get("broadcast_users",[]); idx=next((i for i,u in enumerate(users) if int(u["user_id"])==uid),0)
        offset=(idx//12)*12
        return await admin_broadcast_people_page(q.message,context,offset)
    if data=="adm_people_done":
        chosen=context.user_data.get("broadcast_selected_users",[])
        if not chosen: return await q.answer("ቢያንስ አንድ ተቀባይ ይምረጡ።",show_alert=True)
        context.user_data["broadcast_mode"]="selected"; context.user_data["broadcast_target_ids"]=chosen
        await q.message.reply_text(f"📝 ለ{len(chosen)} የተመረጡ ግለሰቦች የሚላከውን መልእክት ይጻፉ።")

async def admin_info_subscribers(update, context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin ብቻ።",show_alert=True)
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        rows=conn.execute("SELECT user_id,category,full_name,phone,telegram_name FROM info_category_subscriptions WHERE active=1 ORDER BY category,full_name LIMIT 80").fetchall()
    finally: conn.close()
    if not rows: return await q.message.reply_text("ንቁ የመረጃ ተመዝጋቢዎች የሉም።")
    labels={"purchase_sales":"ግዢ/ሽያጭ","construction":"ግንባታ","jobs":"ሥራ","broker_info":"ድለላ"}
    for r in rows:
        cat=r["category"]
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ ከዚህ ዘርፍ አስወግድ",callback_data=f"infosub_remove_{cat}_{r['user_id']}")]])
        await q.message.reply_text(f"👤 {r['full_name']}\n📞 {r['phone']}\nTelegram: @{r['telegram_name'] or 'የለም'}\nዘርፍ፦ {labels.get(cat,cat)}\nID፦ {r['user_id']}",reply_markup=kb)

async def admin_info_subscriber_remove(update, context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin ብቻ።",show_alert=True)
    parts=q.data.split("_"); category="_".join(parts[2:-1]); uid=parts[-1]
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        cur=conn.execute("UPDATE info_category_subscriptions SET active=0 WHERE user_id=? AND category=? AND active=1",(int(uid),category)); conn.commit()
    finally: conn.close()
    await q.edit_message_text("✅ ተጠቃሚው ከዚህ የመረጃ ዘርፍ ብቻ ተወግዷል።" if cur.rowcount else "ይህ ምዝገባ አልተገኘም ወይም ቀድሞ ተወግዷል።")

async def admin_info_storage(update,context):
    if not is_admin(update.effective_user.id): return
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        sub_counts=conn.execute("SELECT category,COUNT(*) AS n FROM info_category_subscriptions WHERE active=1 GROUP BY category").fetchall()
        rows=conn.execute("""SELECT a.id,a.category,a.title,a.post_text,a.translated_text,a.post_url,a.post_date,
            o.name AS org_name,o.region,s.platform FROM organization_announcements a
            JOIN monitored_organizations o ON o.id=a.organization_id
            JOIN monitored_sources s ON s.id=a.source_id
            WHERE a.review_status='approved' ORDER BY a.id DESC LIMIT 30""").fetchall()
        media_rows=conn.execute("""SELECT id,source_name,platform,post_url,post_text,post_date
            FROM media_posts WHERE COALESCE(is_deleted,0)=0 ORDER BY id DESC LIMIT 30""").fetchall()
    finally: conn.close()
    counts={r["category"]:r["n"] for r in sub_counts}
    summary=("ETHIOPIA BISNESS HUB\n"
        f"🛒 የግዢና የሽያጭ ተመዝጋቢዎች፦ {counts.get('purchase_sales',0)}\n"
        f"🏗️ የግንባታ ጨረታ ተመዝጋቢዎች፦ {counts.get('construction',0)}\n"
        f"💼 የሥራ ፍለጋ ተመዝጋቢዎች፦ {counts.get('jobs',0)}\n"
        f"🤝 የድለላ መረጃ ተመዝጋቢዎች፦ {counts.get('broker_info',0)}\n"
        f"📡 በሚዲያ ምንጮች የተሰበሰቡ መረጃዎች፦ {len(media_rows)}\n\n")
    await update.message.reply_text("🗂️ ጠቅላላ መረጃ ማከማቻ\n"+summary,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👥 ተመዝጋቢዎችን አሳይ/ከዘርፍ አስወግድ",callback_data="infosub_list")]]))
    if not rows and not media_rows:
        return await update.message.reply_text("ገና የተፈቀዱ ወይም የተሰበሰቡ መረጃዎች የሉም። ከሚዲያ ምንጭ ክትትል ክፍል ምንጮችን ጨምረው መረጃ ያስሰብስቡ።")
    combined=[dict(r) for r in rows]
    # Negative IDs distinguish collected media_posts from approved announcements.
    for r in media_rows:
        combined.append({"id":-int(r["id"]),"category":"media","title":r["source_name"],
            "post_text":r["post_text"],"translated_text":"","post_url":r["post_url"],
            "post_date":r["post_date"],"org_name":r["source_name"],"region":"","platform":r["platform"]})
    combined.sort(key=lambda x: str(x.get("post_date") or ""), reverse=True)
    context.user_data["admin_info_rows"]=combined[:50]
    context.user_data["admin_info_selected"]=[]
    # Send collected source text as ordinary messages so admins can long-press and copy it.
    for r in context.user_data["admin_info_rows"]:
        if r.get("category")=="media":
            body=f"📡 {r.get('platform','')} | {r.get('org_name','')}\n\n{r.get('post_text','')}\n\n🔗 {r.get('post_url','')}"
            await update.message.reply_text(body[:3900],disable_web_page_preview=True)
    await admin_info_storage_page(update.message,context,0)

async def admin_info_storage_page(message,context,offset=0):
    rows=context.user_data.get("admin_info_rows",[]); selected=set(context.user_data.get("admin_info_selected",[])); size=8; kb=[]
    for r in rows[offset:offset+size]:
        rid=int(r["id"]); title=(r.get("title") or r.get("post_text") or "ያልተሰየመ መረጃ").replace("\n"," ")[:42]
        mark="☑️" if rid in selected else "⬜"
        kb.append([InlineKeyboardButton(f"{mark} #{rid} {title}",callback_data=f"adm_info_toggle_{rid}")])
    nav=[]
    if offset: nav.append(InlineKeyboardButton("⬅️",callback_data=f"adm_info_page_{max(0,offset-size)}"))
    if offset+size<len(rows): nav.append(InlineKeyboardButton("➡️",callback_data=f"adm_info_page_{offset+size}"))
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton("📨 የተመረጡትን ለመላክ ቀጥል",callback_data="adm_info_done")])
    await message.reply_text(f"🗂️ ጠቅላላ መረጃ ማከማቻ\nአንድ ወይም ብዙ መረጃዎችን ይምረጡ። የተመረጡ፦ {len(selected)}",reply_markup=InlineKeyboardMarkup(kb))

async def admin_info_storage_callback(update,context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin ብቻ።",show_alert=True)
    data=q.data; rows=context.user_data.get("admin_info_rows",[])
    if data.startswith("adm_info_page_"):
        offset=int(data.rsplit("_",1)[1]); selected=set(context.user_data.get("admin_info_selected",[])); size=8; kb=[]
        for r in rows[offset:offset+size]:
            rid=int(r["id"]); title=(r.get("title") or r.get("post_text") or "መረጃ").replace("\n"," ")[:42]; mark="☑️" if rid in selected else "⬜"
            kb.append([InlineKeyboardButton(f"{mark} #{rid} {title}",callback_data=f"adm_info_toggle_{rid}")])
        nav=[]
        if offset: nav.append(InlineKeyboardButton("⬅️",callback_data=f"adm_info_page_{max(0,offset-size)}"))
        if offset+size<len(rows): nav.append(InlineKeyboardButton("➡️",callback_data=f"adm_info_page_{offset+size}"))
        if nav: kb.append(nav)
        kb.append([InlineKeyboardButton("📨 የተመረጡትን ለመላክ ቀጥል",callback_data="adm_info_done")])
        return await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))
    if data.startswith("adm_info_toggle_"):
        rid=int(data.rsplit("_",1)[1]); selected=set(context.user_data.get("admin_info_selected",[])); selected.symmetric_difference_update({rid}); context.user_data["admin_info_selected"]=list(selected)
        idx=next((i for i,r in enumerate(rows) if int(r["id"])==rid),0)
        return await admin_info_storage_page(q.message,context,(idx//8)*8)
    if data=="adm_info_done":
        selected=context.user_data.get("admin_info_selected",[])
        if not selected: return await q.answer("ቢያንስ አንድ መረጃ ይምረጡ።",show_alert=True)
        chosen=[r for r in rows if int(r["id"]) in set(selected)]
        context.user_data["admin_info_selected_payload"]=[{"id":int(r["id"]),"text":(r.get("translated_text") or r.get("post_text") or ""),"url":r.get("post_url") or "","title":r.get("title") or ""} for r in chosen]
        kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 የግዢና ሽያጭ ተመዝጋቢዎች",callback_data="adm_info_aud_purchase_sales")],
            [InlineKeyboardButton("🏗️ የግንባታ ጨረታ ተመዝጋቢዎች",callback_data="adm_info_aud_construction")],
            [InlineKeyboardButton("💼 የሥራ ፍለጋ ተመዝጋቢዎች",callback_data="adm_info_aud_jobs")],
            [InlineKeyboardButton("🤝 የድለላ መረጃ ተመዝጋቢዎች",callback_data="adm_info_aud_broker_info")],
            [InlineKeyboardButton("👤 ለተመረጡ ግለሰቦች",callback_data="adm_info_aud_people")]
        ])
        await q.message.reply_text(f"✅ {len(chosen)} መረጃዎች ተመርጠዋል። የመላኪያ ቡድን ይምረጡ።",reply_markup=kb)

async def admin_info_audience_callback(update,context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin ብቻ።",show_alert=True)
    mode=q.data.replace("adm_info_aud_", "", 1)
    payload=context.user_data.get("admin_info_selected_payload",[])
    if not payload: return await q.message.reply_text("የተመረጡ መረጃዎች አልተገኙም።")
    if mode in ("purchase_sales", "construction", "jobs", "broker_info"):
        conn=_db_connect(DB_PATH,timeout=10)
        try: targets=[int(r[0]) for r in conn.execute("SELECT user_id FROM info_category_subscriptions WHERE active=1 AND category=?",(mode,)).fetchall()]
        finally: conn.close()
    elif mode=="tender":
        conn=_db_connect(DB_PATH,timeout=10)
        try: targets=[int(r[0]) for r in conn.execute("SELECT user_id FROM tender_subscribers WHERE active=1").fetchall()]
        finally: conn.close()
    elif mode=="brokers":
        targets=sorted({int(b["user_id"]) for b in brokers if b.get("user_id") and b.get("approved") and b.get("active")})
        if not targets:
            conn=_db_connect(DB_PATH,timeout=10)
            try: targets=[int(r[0]) for r in conn.execute("SELECT DISTINCT user_id FROM brokers WHERE active=1 AND approved=1").fetchall()]
            finally: conn.close()
    else:
        targets=context.user_data.get("broadcast_selected_users",[])
    if not targets: return await q.message.reply_text("ለዚህ ቡድን የተመዘገቡ ተቀባዮች አልተገኙም።")
    chunks=[]
    for i,item in enumerate(payload,1):
        chunks.append(f"{i}. {item['title'] or 'መረጃ'}\n{item['text'][:2500]}\n{item['url']}")
    body=("ከTANA CARGO, LOGISTICS & INFORMATION SERVICES\n\n"
          + "\n\n────────────\n\n".join(chunks)
          + "\n\nለበለጠ መረጃ @tanapage Join ያድርጉ\nበ0960011010 ይደውሉ")
    sent=failed=0
    for uid in targets:
        try:
            await context.bot.send_message(chat_id=int(uid),text=body[:4000],disable_web_page_preview=True)
            sent+=1; await asyncio.sleep(0.05)
        except Exception as exc:
            failed+=1; logger.warning("Admin info broadcast failed for %s: %s",uid,exc)
    await q.message.reply_text(f"📨 ማሰራጨቱ ተጠናቋል።\nየተመረጡ መረጃዎች፦ {len(payload)}\nተልኳል፦ {sent}\nአልደረሰም፦ {failed}")
    for key in ("admin_info_rows","admin_info_selected","admin_info_selected_payload","broadcast_selected_users","broadcast_users"):
        context.user_data.pop(key,None)

async def super_broadcast_start(update, context):
    if not is_super_admin(update.effective_user.id):
        await update.message.reply_text("❌ Super Admin ብቻ ይህን ምናሌ መጠቀም ይችላል።")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 ለሁሉም ደንበኞች", callback_data="broadcast_all")],
        [InlineKeyboardButton("🏷️ ለደላሎች ብቻ", callback_data="broadcast_brokers")],
        [InlineKeyboardButton("👤 ለአንድ ደንበኛ", callback_data="broadcast_one")]
    ])
    await update.message.reply_text("📣 መልዕክቱን ለማን መላክ ይፈልጋሉ?", reply_markup=kb)

async def super_broadcast_choice(update, context):
    q = update.callback_query
    await q.answer()
    if not is_admin(update.effective_user.id):
        await q.answer("Admin/Super Admin ብቻ።", show_alert=True)
        return
    context.user_data["broadcast_mode"] = q.data.replace("broadcast_", "")
    if q.data in ("broadcast_all", "broadcast_brokers"):
        context.user_data.pop("broadcast_target_id", None)
        audience = "ለሁሉም ደንበኞች" if q.data == "broadcast_all" else "ለተመዘገቡ ደላሎች ብቻ"
        await q.message.reply_text(f"📝 {audience} የሚላከውን መልዕክት ይጻፉ።")
        return
    users_list = db_list_registered_users()
    if not users_list:
        await q.message.reply_text("የተመዘገበ ደንበኛ አልተገኘም።")
        return
    context.user_data["broadcast_users"] = users_list
    await send_broadcast_user_page(q.message, context, 0)

async def send_broadcast_user_page(message, context, offset):
    users_list = context.user_data.get("broadcast_users") or db_list_registered_users()
    page_size = 20
    page = users_list[offset:offset + page_size]
    rows = []
    for u in page:
        label = (u.get("name") or "ደንበኛ")[:22]
        uname = f" @{u['username']}" if u.get("username") else ""
        rows.append([InlineKeyboardButton(
            f"{label}{uname}", callback_data=f"broadcast_user_{u['user_id']}"
        )])
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ ቀዳሚ", callback_data=f"broadcast_page_{max(0, offset-page_size)}"))
    if offset + page_size < len(users_list):
        nav.append(InlineKeyboardButton("ቀጣይ ➡️", callback_data=f"broadcast_page_{offset+page_size}"))
    if nav:
        rows.append(nav)
    await message.reply_text(
        f"📲 ደንበኛ ይምረጡ። ({offset+1}-{min(offset+page_size, len(users_list))} / {len(users_list)})",
        reply_markup=InlineKeyboardMarkup(rows)
    )

async def super_broadcast_page(update, context):
    q = update.callback_query
    await q.answer()
    if not is_admin(update.effective_user.id):
        await q.answer("Admin/Super Admin ብቻ።", show_alert=True)
        return
    offset = int(q.data.split("_")[-1])
    users_list = context.user_data.get("broadcast_users") or db_list_registered_users()
    context.user_data["broadcast_users"] = users_list
    page_size = 20
    page = users_list[offset:offset + page_size]
    rows = []
    for u in page:
        label = (u.get("name") or "ደንበኛ")[:22]
        uname = f" @{u['username']}" if u.get("username") else ""
        rows.append([InlineKeyboardButton(f"{label}{uname}", callback_data=f"broadcast_user_{u['user_id']}")])
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ ቀዳሚ", callback_data=f"broadcast_page_{max(0, offset-page_size)}"))
    if offset + page_size < len(users_list):
        nav.append(InlineKeyboardButton("ቀጣይ ➡️", callback_data=f"broadcast_page_{offset+page_size}"))
    if nav:
        rows.append(nav)
    await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(rows))

async def super_broadcast_select_user(update, context):
    q = update.callback_query
    await q.answer()
    if not is_admin(update.effective_user.id):
        await q.answer("Admin/Super Admin ብቻ።", show_alert=True)
        return
    uid = int(q.data.split("_")[-1])
    context.user_data["broadcast_target_id"] = uid
    await q.message.reply_text("📝 ለተመረጠው ደንበኛ የሚላከውን መልዕክት ይጻፉ።")

async def super_broadcast_send_text(update, context):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    msg = (update.message.text or "").strip()
    if not msg:
        await update.message.reply_text("❌ መልዕክቱ ባዶ መሆን የለበትም።")
        return SUPER_BROADCAST_TEXT
    mode = context.user_data.get("broadcast_mode")
    if mode == "all":
        targets = [u["user_id"] for u in db_list_registered_users()]
    elif mode == "brokers":
        targets = sorted({int(b.get("user_id")) for b in brokers if b.get("user_id") and b.get("approved") and b.get("active")})
        if not targets:
            conn = _db_connect(DB_PATH, check_same_thread=False)
            try:
                rows = conn.execute("SELECT DISTINCT user_id FROM brokers WHERE active=1 AND approved=1").fetchall()
                targets = [int(r[0]) for r in rows]
            finally:
                conn.close()
    elif mode == "selected":
        targets = context.user_data.get("broadcast_target_ids", [])
    elif mode and mode.startswith("info:"):
        category=mode.split(":",1)[1]
        conn=_db_connect(DB_PATH,timeout=10)
        try:
            if category == "all":
                rows=conn.execute("SELECT DISTINCT user_id FROM info_category_subscriptions WHERE active=1").fetchall()
                targets=[int(r[0]) for r in rows]
                targets=list(dict.fromkeys(targets+[int(u["user_id"]) for u in db_list_registered_users()]))
            else:
                rows=conn.execute("SELECT user_id FROM info_category_subscriptions WHERE active=1 AND category=?",(category,)).fetchall()
                targets=[int(r[0]) for r in rows]
        finally: conn.close()
    else:
        targets = [context.user_data.get("broadcast_target_id")]
    sent = failed = 0
    for uid in targets:
        if not uid:
            continue
        try:
            await context.bot.send_message(
                chat_id=int(uid), text=(f"ከTANA CARGO, LOGISTICS & INFORMATION SERVICES\n\n{msg}\n\n"
                    "ለበለጠ መረጃ @tanapage Join ያድርጉ\n"
                    "በ0960011010 ይደውሉ")
            )
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    conn = _db_connect(DB_PATH, check_same_thread=False)
    try:
        conn.execute(
            "INSERT INTO admin_broadcast_log (admin_id, target_user_id, message_text, sent_count, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (update.effective_user.id,
             None if mode in ("all", "brokers", "selected") else context.user_data.get("broadcast_target_id"),
             msg, sent, datetime.now().astimezone().isoformat(timespec="seconds"))
        )
        conn.commit()
    finally:
        conn.close()
    context.user_data.pop("broadcast_mode", None)
    context.user_data.pop("broadcast_target_id", None)
    context.user_data.pop("broadcast_target_ids", None)
    context.user_data.pop("broadcast_admin_mode", None)
    await update.message.reply_text(
        f"✅ መልዕክቱ ተልኳል።\n📨 የደረሰ: {sent}\n⚠️ ያልደረሰ: {failed}",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END

async def admin_broadcast_text_router(update, context):
    """Receive text entered after an inline admin broadcast audience selection."""
    if not is_admin(update.effective_user.id):
        return
    if context.user_data.get("admin_phone_lookup_waiting"):
        phone=(update.message.text or "").strip()
        conn=_db_connect(DB_PATH,timeout=10)
        try:
            rows=conn.execute("SELECT user_id,owner_phone,name,username FROM users WHERE owner_phone IS NOT NULL AND owner_phone <> ''").fetchall()
        finally: conn.close()
        match=next((r for r in rows if phones_match(r["owner_phone"],phone)),None)
        context.user_data.pop("admin_phone_lookup_waiting",None)
        if not match:
            await update.message.reply_text("❌ ይህ ቁጥር በቦቱ የተመዘገበ ደንበኛ ላይ አልተገኘም። ደንበኛው በ /start ስልኩን ካጋራ ብቻ መፈለግ ይቻላል።")
            return
        context.user_data["broadcast_mode"]="single"
        context.user_data["broadcast_target_id"]=int(match["user_id"])
        context.user_data["broadcast_admin_mode"]=True
        await update.message.reply_text(f"✅ ደንበኛ ተገኝቷል፦ {match.get('name') or 'ስም የለም'} · @{match.get('username') or 'username የለም'}\n📝 የሚላከውን መልዕክት ይጻፉ።")
        return
    if context.user_data.get("broadcast_admin_mode") and context.user_data.get("broadcast_mode"):
        return await super_broadcast_send_text(update, context)

async def handle_rental_deeplink(update, context, share_token):
    rec = db_get_rental_request_by_token(share_token)
    if not rec or rec["status"] != "approved":
        await update.message.reply_text("ይህ የክትትል ሊንክ አይሰራም ወይም ፈቃዱ ተዘግቷል።")
        return ConversationHandler.END
    if is_user_registered(update.effective_user.id):
        conn = _db_connect(DB_PATH, check_same_thread=False)
        try:
            row = conn.execute("SELECT owner_phone FROM users WHERE user_id = ?",
                               (update.effective_user.id,)).fetchone()
        finally:
            conn.close()
        if not row or not phones_match(row[0], rec["target_phone"]):
            await update.message.reply_text("❌ ይህ የክትትል ሊንክ ለሌላ ደንበኛ የተላከ ነው።")
            return ConversationHandler.END
        db_update_rental_request(rec["id"], target_user_id=update.effective_user.id)
        await send_rental_tracking_to_user(context, rec["id"], update.effective_user.id)
        return ConversationHandler.END
    context.user_data["pending_rental_share_token"] = share_token
    await update.message.reply_text(
        "🚐 **TANA CARGO, LOGISTICS & INFORMATION SERVICES**\nየክትትል መረጃውን ለመቀበል የራስዎን ስልክ ቁጥር ያጋሩ።",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("📱 ስልኬን አጋራ", request_contact=True)]],
            resize_keyboard=True, one_time_keyboard=True
        )
    )
    return START_PHONE


# OWNER
# ==================================================

async def owner_start(update, context):
    context.user_data.pop("cargo", None)
    context.user_data.pop("truck", None)
    context.user_data.pop("broker", None)
    context.user_data.pop("connect_info", None)
    context.user_data["owner"] = {}
    await update.message.reply_text("📦 የጭነት ባለቤት ምዝገባ\n\n1️⃣ ስምዎን ይጻፉ።")
    return OWNER_NAME

async def owner_name(update, context):
    context.user_data["owner"]["name"] = update.message.text.strip()
    await update.message.reply_text(
        "2️⃣ ስልክ ቁጥርዎን ይጻፉ።\nበ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\nምሳሌ፦ 0912345678"
    )
    return OWNER_PHONE

async def owner_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return OWNER_PHONE
    owner = context.user_data["owner"]
    owner["phone"] = phone
    owner["user_id"] = update.effective_user.id
    user = update.effective_user
    users.setdefault(user.id, {"name": user.full_name, "username": user.username or "", "id": user.id})
    users[user.id]["owner"] = owner.copy()
    try:
        db_save_user(user.id, user.full_name, user.username or "", owner_name=owner["name"], owner_phone=owner["phone"])
    except Exception as e:
        print(f"DB SAVE OWNER ERROR: {e}")
    await update.message.reply_text(
        f"✅ ምዝገባዎ ተጠናቋል!\n\n👤 {owner['name']}\n📞 {owner['phone']}",
        reply_markup=main_menu(user.id)
    )
    return ConversationHandler.END

async def tracking_menu(update, context):
    context.user_data.pop("tracking_update_code", None)
    context.user_data.pop("tracking_lookup_waiting", None)
    context.user_data.pop("gmaps_share", None)
    context.user_data.pop("tracking_stop", None)
    context.user_data.pop("track_new", None)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🗺️ የጎግል መረጃ ለባለጭነቶች (ለባለንብረቶች) ለመላክ ይህን ይጫኑ 👈",
            callback_data="tracking_share_gmaps"
        )],
        [InlineKeyboardButton("🛑 ክትትል አቁም", callback_data="tracking_stop_request")],
    ])
    await update.effective_message.reply_text(
        "📍 **TANA CARGO, LOGISTICS & INFORMATION SERVICES — የጭነት ክትትል**\n\n"
        "🗺️ የGoogle Maps መረጃ ለባለጭነቶች ለመላክ ይጫኑ።\n"
        "🛑 ክትትል ለማቆም ይጫኑ።\n\n"
        "✅ ማንኛውም ሰው ጭነት ባይለጥፍም የክትትል አገልግሎቱን መጠቀም ይችላል።",
        reply_markup=keyboard
    )

async def tracking_share_gmaps_start(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["gmaps_share"] = {}
    await query.message.reply_text(
        "🗺️ **የጎግል መረጃ ለባለጭነቶች (ለባለንብረቶች) ለመላክ**\n\n"
        "1️⃣ እባክዎ **የራስዎን አድራሻ** ይጻፉ።\n"
        "ምሳሌ፦ አዲስ አበባ / ባህርዳር",
        reply_markup=ReplyKeyboardRemove()
    )
    return TRACK_SHARE_ADDRESS

async def tracking_share_address(update, context):
    address = update.message.text.strip().split("\n")[0].strip()
    if not address:
        await update.message.reply_text("❌ እባክዎ አድራሻዎን ይጻፉ።")
        return TRACK_SHARE_ADDRESS
    context.user_data["gmaps_share"]["address"] = address
    await update.message.reply_text("2️⃣ **መረጃው የሚላክለት ሰው ስም** ይጻፉ።")
    return TRACK_SHARE_NAME

async def tracking_share_name(update, context):
    name = update.message.text.strip().split("\n")[0].strip()
    if not name:
        await update.message.reply_text("❌ እባክዎ ስም ይጻፉ።")
        return TRACK_SHARE_NAME
    context.user_data["gmaps_share"]["name"] = name
    await update.message.reply_text(
        "3️⃣ **መረጃው የሚላክለት ሰው ስልክ ቁጥር** ይጻፉ።\n"
        "በ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\n"
        "ምሳሌ፦ 0912345678"
    )
    return TRACK_SHARE_PHONE

async def tracking_share_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return TRACK_SHARE_PHONE
    info = context.user_data.get("gmaps_share", {})
    info["phone"] = phone
    info["sender_id"] = update.effective_user.id
    info["sender_name"] = update.effective_user.full_name
    info["device_id"] = get_traccar_device_id_for_user(update.effective_user.id)
    info["timestamp"] = datetime.now().astimezone().isoformat()
    info["status"] = "pending"
    gmaps_share_requests.append(info)
    idx = len(gmaps_share_requests) - 1
    try:
        db_save_gps_admin_request("gmaps", idx, info)
    except Exception as e:
        print(f"GPS GMAPS REQUEST SAVE ERROR: {e}")
    context.user_data.pop("gmaps_share", None)

    await update.message.reply_text(
        "✅ **የGPS ማጋሪያ ጥያቄዎ ተመዝግቧል።**\n\n"
        "⏳ አድሚን ካጸደቀው በኋላ የGoogle Maps ክትትል ሊንኩ ይደርሳል።",
        reply_markup=main_menu(update.effective_user.id)
    )

    tracking_device_id = str(info.get("device_id") or TRACCAR_DEVICE_ID)
    loc = get_traccar_location(tracking_device_id)
    map_url = ""
    if loc and loc.get("latitude") is not None and loc.get("longitude") is not None:
        map_url = f"https://maps.google.com/?q={loc['latitude']},{loc['longitude']}"

    text = (
        "🗺️ **TANA CARGO, LOGISTICS & INFORMATION SERVICES — አዲስ የGoogle Maps ማጋሪያ ጥያቄ**\n\n"
        f"👤 ላኪ: {info['sender_name']} (ID: {info['sender_id']})\n"
        f"📍 ላኪ አድራሻ: {info['address']}\n\n"
        f"📤 **መረጃው የሚላክለት ሰው:**\n"
        f"   • ስም: {info['name']}\n"
        f"   • ስልክ: {info['phone']}\n\n"
    )
    if map_url:
        text += f"📍 [የመጨረሻ የተገኘ GPS ነጥብ — Snapshot]({map_url})\n\n"
    text += "📌 ይህ ከላይ ያለው ነጥብ የአሁኑ/የመጨረሻ GPS Snapshot ነው። Approve ሲደረግ በየ15 ሰከንድ የሚዘምን TANA CARGO, LOGISTICS & INFORMATION SERVICES Live Tracking ሊንክ ይላካል። ለማጽደቅ ይፈልጋሉ?"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve & Send Link", callback_data=f"approvegmaps_{idx}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"rejectgmaps_{idx}")
    ]])
    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(
                chat_id=admin_id, text=text, reply_markup=kb,
                disable_web_page_preview=True
            )
        except Exception:
            pass
    return ConversationHandler.END

async def admin_approve_gmaps(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return
    try:
        idx = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if idx < 0 or idx >= len(gmaps_share_requests):
        return
    info = gmaps_share_requests[idx]
    if info.get("status") in ("approved", "rejected"):
        await query.answer("ይህ የGPS ጥያቄ አስቀድሞ ተወስኗል።", show_alert=True)
        return
    info["approved"] = True
    info["status"] = "approved"

    tracking_device_id = str(info.get("device_id") or TRACCAR_DEVICE_ID)
    gmaps_token, traccar_live_url = create_public_tracking_link(f"Location share {idx}", tracking_device_id)
    info["tracking_token"] = gmaps_token
    loc = get_traccar_location(tracking_device_id)
    if loc and loc.get("latitude") is not None and loc.get("longitude") is not None:
        lat = loc["latitude"]
        lon = loc["longitude"]
        map_url = f"https://maps.google.com/?q={lat},{lon}"
        map_address = loc.get("address", "")
    else:
        lat = None
        lon = None
        map_url = ""
        map_address = "የGPS ቦታ ገና አልተገኘም። ሊንኩ ሲከፈት አዲስ መረጃ ይፈልጋል።"
        # Keep the public tracking page URL: it can start showing updates when GPS returns.
    try:
        db_update_gps_admin_request("gmaps", idx, info, "approved")
    except Exception as e:
        print(f"GPS GMAPS APPROVAL SAVE ERROR: {e}")

    await query.edit_message_text(
        f"✅ **ጸድቋል!**\n\n"
        f"📤 ለ {info['name']} ({info['phone']})\n"
        f"📤 ለ {info['sender_name']}\n\n"
        "የGPS ጥያቄው ጸድቋል፤ ሊንኩ ለተቀባዩ እንዲደርስ ሂደቱ ተጀምሯል።"
    )

    recipient_user_id = find_user_by_phone(info["phone"])

    if recipient_user_id and traccar_live_url:
        try:
            await context.bot.send_message(
                chat_id=recipient_user_id,
                text=(
                    "🗺️ **TANA CARGO, LOGISTICS & INFORMATION SERVICES 🚛 የተላከ**\n\n"
                    "የGPS መረጃው ከTraccar Client ወደ TANA CARGO, LOGISTICS & INFORMATION SERVICES ሲስተም ይመጣል። "
                    f"🗺️ [TANA CARGO, LOGISTICS & INFORMATION SERVICES Live Tracking ክፈት]({traccar_live_url})\n\n"
                    f"📍 የመጨረሻ አድራሻ: {map_address}\n"
                    "👉 @tanacargobot"
                ),
                disable_web_page_preview=False
            )
        except Exception as e:
            print(f"Error sending live location: {e}")
            try:
                await context.bot.send_message(
                    chat_id=recipient_user_id,
                    text=(
                        "🗺️ **TANA CARGO, LOGISTICS & INFORMATION SERVICES 🚛 የተላከ**\n\n"
                        f"🗺️ [TANA CARGO, LOGISTICS & INFORMATION SERVICES Live Tracking]({traccar_live_url})\n\n"
                        f"📍 አድራሻ: {map_address}"
                    ),
                    disable_web_page_preview=False
                )
            except Exception:
                pass

    try:
        if recipient_user_id and traccar_live_url:
            await context.bot.send_message(
                chat_id=info["sender_id"],
                text=(
                    "✅ **መረጃዎ ጸድቋል!**\n\n"
                    "የTANA CARGO, LOGISTICS & INFORMATION SERVICES የቀጥታ ክትትል ሊንክ ለተቀባዩ በTelegram ተልኳል።\n\n"
                    f"🗺️ [TANA CARGO, LOGISTICS & INFORMATION SERVICES Live Tracking]({traccar_live_url})\n"
                    ""
                ),
                disable_web_page_preview=False
            )
        elif not recipient_user_id:
            bot_link = f"https://t.me/{BOT_USERNAME}"
            share_text = (
                "ከTANA CARGO, LOGISTICS & INFORMATION SERVICES የተላከ\n"
                "የTANA CARGO, LOGISTICS & INFORMATION SERVICES Live GPS Tracking መረጃ ለማግኘት እባክዎ "
                "የTANA CARGO, LOGISTICS & INFORMATION SERVICES ቦትን ይክፈቱ፣ Start ይጫኑና ምዝገባዎን ያጠናቅቁ።\n"
                f"{bot_link}\n"
                f"👈 @{BOT_USERNAME} ተጭነው በነፃ ይመዝገቡ።"
            )
            share_url = (
                "https://t.me/share/url?url=" + quote(bot_link, safe="")
                + "&text=" + quote(share_text, safe="")
            )
            share_keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("📤 ለደንበኛው Share አድርግ", url=share_url)
            ]])
            await context.bot.send_message(
                chat_id=info["sender_id"],
                text=(
                    "⚠️ **የተቀባዩ ምዝገባ አልተገኘም**\n\n"
                    "የTANA CARGO, LOGISTICS & INFORMATION SERVICES Live GPS Tracking ለማጋራት የፈለጉት ደንበኛ "
                    f"👉 @{BOT_USERNAME} ላይ ስላልተመዘገበ፣ መረጃዎን በTelegram ቀጥታ መላክ አልቻልንም።\n\n"
                    "እባክዎ ከታች ያለውን **Share** ምልክት ተጭነው ወይም የቦቱን ሊንክ Copy አድርገው ለደንበኛው ይላኩ።\n\n"
                    "🔗 **ከTANA CARGO, LOGISTICS & INFORMATION SERVICES የተላከ**\n"
                    f"👉 {bot_link}\n\n"
                    f"👈 @{BOT_USERNAME} ተጭነው በነፃ ይመዝገቡ፤ "
                    "የተለያዩ የTANA CARGO, LOGISTICS & INFORMATION SERVICES አገልግሎቶችንም ይጠቀሙ።"
                ),
                reply_markup=share_keyboard,
                disable_web_page_preview=True
            )
        elif not traccar_live_url:
            await context.bot.send_message(
                chat_id=info["sender_id"],
                text=(
                    "✅ መረጃዎ ጸድቋል!\n\n"
                    "⚠️ የLive Tracking ሊንክ አልተዘጋጀም። Render Environment ውስጥ PUBLIC_BASE_URL/RENDER_EXTERNAL_URL እና TRACCAR_TOKEN እንዲሁም ትክክለኛ TRACCAR_DEVICE_ID መኖራቸውን Admin ያረጋግጥ። ከዚህ የተለየው Google Maps ሊንክ የአንድ ጊዜ ቦታ ብቻ ነው።"
                )
            )
    except Exception as e:
        print(f"Error notifying tracking requester: {e}")

    if not recipient_user_id:
        pending_share = {
            "phone": info["phone"],
            "name": info["name"],
            "lat": lat,
            "lon": lon,
            "address": map_address,
            "traccar_url": traccar_live_url
        }
        try:
            pending_share["_db_id"] = db_save_pending_gmaps_share(pending_share)
        except Exception as e:
            print(f"PENDING GPS SHARE SAVE ERROR: {e}")
        pending_gmaps_shares.append(pending_share)
        
        for admin_id in get_admin_ids():
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"⚠️ **ማሳወቂያ**\n\n"
                        f"ለ **{info['name']} ({info['phone']})** መልዕክት ለመላክ አልተቻለም።\n\n"
                        "ተጠቃሚው ቦቱን አልተመዘገበም ወይም የስልክ ቁጥሩ አልተገኘም።\n\n"
                        "👉 **እባክዎ ደውለው TANA CARGO, LOGISTICS & INFORMATION SERVICES ላይ Telegram በሚጠቀምበት ስልክ እንዲመዘገብ ያድርጉት!**\n\n"
                        "👉 **ወይም እባክዎ በእጅ ያስተላልፉ።**\n\n"
                        "✅ **መረጃው ተቀምጧል። ተጠቃሚው ስልክ ቁጥሩን አስመዝግቦ እንደጨረሰ ወዲያውኑ በራስ-ሰር ይደርሰዋል።**"
                    ),
                    parse_mode="Markdown"
                )
            except Exception:
                pass

async def admin_reject_gmaps(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return
    try:
        idx = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if idx < 0 or idx >= len(gmaps_share_requests):
        return
    info = gmaps_share_requests[idx]
    if info.get("status") in ("approved", "rejected"):
        await query.answer("ይህ የGPS ጥያቄ አስቀድሞ ተወስኗል።", show_alert=True)
        return
    info["status"] = "rejected"
    try:
        db_update_gps_admin_request("gmaps", idx, info, "rejected")
    except Exception as e:
        print(f"GPS GMAPS REJECTION SAVE ERROR: {e}")
    await query.edit_message_text("❌ ጥያቄው ውድቅ ተደርጓል።")
    try:
        await context.bot.send_message(
            chat_id=info["sender_id"],
            text="❌ የGoogle Maps ማጋሪያ ጥያቄዎ በአድሚን ውድቅ ተደርጓል።"
        )
    except Exception:
        pass

async def tracking_stop_start(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data["tracking_stop"] = {}
    await query.message.reply_text(
        "🛑 **ክትትል ማቆም**\n\n"
        "1️⃣ እባክዎ **የሚከታተለው ሰው ስም** ይጻፉ።\n"
        "ምሳሌ፦ አበበ ከበደ",
        reply_markup=ReplyKeyboardRemove()
    )
    return TRACK_STOP_NAME

async def tracking_stop_name(update, context):
    name = update.message.text.strip().split("\n")[0].strip()
    if not name:
        await update.message.reply_text("❌ እባክዎ ሙሉ ስም ይጻፉ።")
        return TRACK_STOP_NAME
    context.user_data["tracking_stop"]["name"] = name

    # Stop requests are checked against the phone the same Telegram user
    # registered through /start. Never accept an unverified phone here.
    registered_phone = get_registered_phone(update.effective_user.id)
    if not registered_phone:
        await update.message.reply_text(
            "⚠️ በStart ሲመዘገቡ ያስገቡት ስልክ ቁጥር አልተገኘም።\n"
            "እባክዎ መጀመሪያ ስልክዎን በBot ውስጥ ያስመዝግቡ።",
            reply_markup=main_menu(update.effective_user.id)
        )
        context.user_data.pop("tracking_stop", None)
        return ConversationHandler.END

    context.user_data["tracking_stop"]["registered_phone"] = registered_phone
    await update.message.reply_text(
        "2️⃣ **የስልክ ቁጥርዎን ያስገቡ።**\n"
        f"📱 **በStart ሲመዘገቡ ያስመዘገቡት ስልክ፦ {registered_phone}**\n\n"
        "እባክዎ ከላይ ከታየው ጋር የሚዛመደውን ስልክ ቁጥር ያስገቡ።"
    )
    return TRACK_STOP_PHONE

async def tracking_stop_phone(update, context):
    phone = update.message.text.strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return TRACK_STOP_PHONE

    info = context.user_data.get("tracking_stop", {})
    registered_phone = str(info.get("registered_phone") or get_registered_phone(update.effective_user.id)).strip()
    # Compare normalized Ethiopian mobile numbers (09..., +2519..., 2519...).
    def _phone_digits(value):
        digits = re.sub(r"\D", "", str(value or ""))
        if digits.startswith("251"):
            digits = "0" + digits[3:]
        return digits
    if not registered_phone or _phone_digits(phone) != _phone_digits(registered_phone):
        await update.message.reply_text(
            "❌ **የተሳሳተ ስልክ ቁጥር ሞልተዋል።**\n\n"
            f"በStart ሲመዘገቡ ያስመዘገቡት ስልክ፦ {registered_phone or 'አልተገኘም'}\n"
            "እባክዎ ትክክለኛውን ስልክ እንደገና ያስገቡ።"
        )
        return TRACK_STOP_PHONE

    info["phone"] = phone
    info["requester_id"] = update.effective_user.id
    info["requester_name"] = update.effective_user.full_name
    info["timestamp"] = datetime.now().astimezone().isoformat()
    info["status"] = "pending"
    tracking_stops.append(info)
    idx = len(tracking_stops) - 1
    try:
        db_save_gps_admin_request("stop", idx, info)
    except Exception as e:
        print(f"GPS STOP REQUEST SAVE ERROR: {e}")
    context.user_data.pop("tracking_stop", None)

    await update.message.reply_text(
        "✅ **የክትትል ማቆሚያ ጥያቄዎ ለአድሚን ተልኳል።**\n\n"
        "⏳ አድሚን ሲያጸድቅ የክትትሉ ባለቤት ማሳወቂያ ይደርሰዋል።",
        reply_markup=main_menu(update.effective_user.id)
    )

    text = (
        "🛑 **TANA CARGO, LOGISTICS & INFORMATION SERVICES — አዲስ የክትትል ማቆሚያ ጥያቄ**\n\n"
        f"👤 ጠያቂ: {info['requester_name']} (ID: {info['requester_id']})\n"
        f"📋 የሚከታተለው ሰው ስም: {info['name']}\n"
        f"📞 ስልክ: {info['phone']}\n\n"
        "📌 አጽድቀው ክትትሉ እንዲቆም ይፈልጋሉ?"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ ክትትል አቁም (Right)", callback_data=f"approvestop_{idx}"),
        InlineKeyboardButton("❌ Reject", callback_data=f"rejectstop_{idx}")
    ]])
    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, reply_markup=kb)
        except Exception:
            pass
    return ConversationHandler.END

async def admin_approve_stop_request(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return
    try:
        idx = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if idx < 0 or idx >= len(tracking_stops):
        return
    info = tracking_stops[idx]
    if info.get("status") in ("approved", "rejected"):
        await query.answer("ይህ የክትትል ጥያቄ አስቀድሞ ተወስኗል።", show_alert=True)
        return

    driver_info = find_driver_by_customer_phone(info["phone"])
    if not driver_info:
        await query.edit_message_text(
            "⚠️ በዚህ ስልክ ቁጥር ንቁ የክትትል ጉዞ አልተገኘም።\n\n"
            "ጥያቄው አልተፈጸመም እና በGPS ጥያቄዎች መዝገብ ውስጥ እንደ በመጠባበቅ ላይ ይቆያል። ስልኩን አረጋግጠው እንደገና ይሞክሩ።"
        )
        return

    info["status"] = "approved"
    try:
        db_update_gps_admin_request("stop", idx, info, "approved")
    except Exception as e:
        print(f"GPS STOP APPROVAL SAVE ERROR: {e}")

    if driver_info:
        db_finish_tracking(driver_info["tracking_code"])
        driver_id = int(driver_info["driver_user_id"])
        try:
            await context.bot.send_message(
                chat_id=driver_id,
                text=(
                    f"🛑 **ክትትል ቁማል**\n\n"
                    f"🔑 ኮድ: {driver_info['tracking_code']}\n"
                    f"👤 የተከታተለው ሰው: {info['name']}\n"
                    f"📞 ስልክ: {info['phone']}\n\n"
                    f"ጭነትዎ መዳረሻው ላይ ደርሷል። አመሰግናለሁ!"
                )
            )
        except Exception:
            pass
        await query.edit_message_text(
            f"✅ **ክትትል ተቆሟል!**\n\n"
            f"🔑 ኮድ: {driver_info['tracking_code']}\n"
            f"👤 ሹፌር: {driver_info.get('customer_name') or 'N/A'}\n\n"
            "ለሹፌሩ ማሳወቂያ ተልኳል።"
        )
    try:
        await context.bot.send_message(
            chat_id=info["requester_id"],
            text=f"✅ **ክትትል ተቆሟል!**\n\n📞 {info['phone']} ለሚለው ስልክ ክትትል ተዘግቷል።"
        )
    except Exception:
        pass

async def admin_reject_stop_request(update, context):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.answer("❌ Admin ብቻ።", show_alert=True)
        return
    try:
        idx = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    if idx < 0 or idx >= len(tracking_stops):
        return
    info = tracking_stops[idx]
    if info.get("status") in ("approved", "rejected"):
        await query.answer("ይህ የክትትል ጥያቄ አስቀድሞ ተወስኗል።", show_alert=True)
        return
    info["status"] = "rejected"
    try:
        db_update_gps_admin_request("stop", idx, info, "rejected")
    except Exception as e:
        print(f"GPS STOP REJECTION SAVE ERROR: {e}")
    await query.edit_message_text("❌ የክትትል ማቆሚያ ጥያቄው ውድቅ ተደርጓል።")
    try:
        await context.bot.send_message(
            chat_id=info["requester_id"],
            text="❌ የክትትል ማቆሚያ ጥያቄዎ በአድሚን ውድቅ ተደርጓል።"
        )
    except Exception:
        pass

# ==================================================
# SHARE FULL INFORMATION
# ==================================================

async def share_full_information(index, context):
    req = connection_requests[index]
    if req.get("full_info_shared"):
        return
    if not req.get("admin_right_confirmed"):
        return
    try:
        tracking_code = db_create_tracking_session(req, index)
        req["tracking_code"] = tracking_code
    except Exception as e:
        tracking_code = None
        print(f"TRACKING SESSION CREATE ERROR: {e}")
    req["full_info_shared"] = True
    req["status"] = "completed"
    try: db_save_connection_request(index, req)
    except Exception as e: print(f"CONNECTION COMPLETION SAVE ERROR: {e}")
    if req.get("type") == "truck":
        ti = req.get("truck_index")
        if isinstance(ti, int) and 0 <= ti < len(truck_posts):
            truck_posts[ti]["active"] = False
    else:
        ci = req.get("cargo_index")
        if isinstance(ci, int) and 0 <= ci < len(cargo_posts):
            cargo_posts[ci]["active"] = False
    requester_id = req["requester_id"]
    owner_id = other_party_id(req)
    requester_name = req.get("requester_name", "N/A")
    requester_phone = req.get("requester_phone", "N/A")
    if req.get("type") == "truck":
        truck = truck_posts[req["truck_index"]]
        owner_name = users.get(owner_id, {}).get("name", "የመኪና ባለቤት")
        owner_phone = truck.get("phone", "")
        requester_msg = (
            "✅ **TANA CARGO, LOGISTICS & INFORMATION SERVICES — ተጠናቋል!**\n\n🔓 የግል መረጃ ተከፍቷል።\n\n"
            f"👤 የመኪና ባለቤት፦ {owner_name}\n📞 ስልክ፦ {owner_phone or 'የለም'}\n"
            f"🚛 መኪና፦ {truck['type']}\n🔢 ታርጋ፦ {truck.get('plate', 'የለም')}\n\n🤝 አሁን በቀጥታ ተገናኙ።"
        )
        owner_msg = (
            "✅ **TANA CARGO, LOGISTICS & INFORMATION SERVICES — ተጠናቋል!**\n\n🔓 የግል መረጃ ተከፍቷል።\n\n"
            f"👤 ጠያቂ፦ {requester_name}\n📞 ስልክ፦ {requester_phone}\n\n🤝 አሁን በቀጥታ ተገናኙ።"
        )
    else:
        cargo = cargo_posts[req["cargo_index"]]
        owner_name = users.get(owner_id, {}).get("name", "የጭነት ባለቤት")
        owner_phone = cargo.get("phone", "")
        requester_msg = (
            "✅ **TANA CARGO, LOGISTICS & INFORMATION SERVICES — ተጠናቋል!**\n\n🔓 የግል መረጃ ተከፍቷል።\n\n"
            f"👤 የጭነት ባለቤት፦ {owner_name}\n📞 ስልክ፦ {owner_phone or 'የለም'}\n\n"
            f"📦 {cargo['type']}\n📍 {cargo['from']} ➡️ {cargo['to']}\n\n🤝 አሁን በቀጥታ ተገናኙ።"
        )
        owner_msg = (
            "✅ **TANA CARGO, LOGISTICS & INFORMATION SERVICES — ተጠናቋል!**\n\n🔓 የግል መረጃ ተከፍቷል።\n\n"
            f"👤 የመኪና ጠያቂ፦ {requester_name}\n📞 ስልክ፦ {requester_phone}\n\n🤝 አሁን በቀጥታ ተገናኙ።"
        )
    for uid, msg in [(requester_id, requester_msg), (owner_id, owner_msg)]:
        if tracking_code:
            if uid == int(req.get("requester_id", 0)) and req.get("type") == "cargo":
                msg += (
                    f"\n\n📍 **የጭነት ክትትል ኮድ፦ {tracking_code}**\n"
                    "🚛 እርስዎ የመኪናውን ቦታ ለመላክ በTANA CARGO, LOGISTICS & INFORMATION SERVICES ምናሌ ውስጥ «📍 የጭነት ክትትል» ይጫኑ።"
                )
            elif uid == int(req.get("truck_owner_id", -1)) and req.get("type") == "truck":
                msg += (
                    f"\n\n📍 **የጭነት ክትትል ኮድ፦ {tracking_code}**\n"
                    "🚛 እርስዎ የመኪናውን ቦታ ለመላክ በTANA CARGO, LOGISTICS & INFORMATION SERVICES ምናሌ ውስጥ «📍 የጭነት ክትትል» ይጫኑ።"
                )
            else:
                msg += (
                    f"\n\n📍 **የጭነት ክትትል ኮድ፦ {tracking_code}**\n"
                    "የመኪናውን የመጨረሻ የተላከ ቦታ ለማየት በTANA CARGO, LOGISTICS & INFORMATION SERVICES ምናሌ ውስጥ «📍 የጭነት ክትትል» ይጫኑ።"
                )
        try:
            await context.bot.send_message(chat_id=uid, text=msg)
        except Exception:
            pass

# ==================================================
# STALE STATE CLEANER
# ==================================================

def clear_stale_user_state(context, keep=()):
    for key in ("cargo", "truck", "broker", "owner",
                "connect_info", "sending_admin_message",
                "tracking_update_code", "tracking_lookup_waiting",
                "track_new", "cust_track_phone_only", "vehicle_listing",
                "gmaps_share", "tracking_stop", "micro_listing", "vehicle_buyer",
                "phone_confirm_target", "phone_confirm_values"):
        if key not in keep:
            context.user_data.pop(key, None)

# ==================================================
# CONNECT FALLBACK
# ==================================================

async def connect_fallback_router(update, context):
    info = context.user_data.get("connect_info")
    if not info:
        return
    text = update.message.text.strip().split("\n")[0].strip()
    if not text:
        return
    name = (info.get("name") or "").strip()
    phone = (info.get("phone") or "").strip()
    address = (info.get("requester_address") or "").strip()
    if not name:
        info["name"] = text
        context.user_data["connect_info"] = info
        await update.message.reply_text("2️⃣ **ስልክ ቁጥርዎን** ይጻፉ።\nበ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\nምሳሌ፦ 0912345678")
        return
    if not phone:
        is_valid, error = validate_phone(text)
        if not is_valid:
            await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
            return
        info["phone"] = text
        context.user_data["connect_info"] = info
        await update.message.reply_text("3️⃣ **የሚገኙበትን አድራሻ** ይጻፉ።\nምሳሌ፦ አዲስ አበባ / ባህርዳር / ጎንደር")
        return
    if not address:
        await connect_type(update, context)
        return

async def menu_tools_fallback(update, context):
    """Fallback handler for Invite and Admin Media menu buttons."""
    if not update.effective_user or not update.message:
        return
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    try:
        if text == "👥 ጓደኛ ጋብዝ":
            if not is_user_registered(uid):
                await update.message.reply_text("⚠️ መጀመሪያ /start በመጫን ምዝገባዎን ያጠናቁ።")
                return
            await show_invite_link(update, context)
            return
        if text == "📡 የሚዲያ መረጃ":
            if not is_admin(uid):
                await update.message.reply_text("❌ የሚዲያ መረጃ ምናሌን Admin/Super Admin ብቻ ይጠቀማሉ።")
                return
            await show_admin_media_menu(update, context)
            return
    except Exception:
        logger.exception("Invite/Media menu fallback failed for user_id=%s", uid)
        await update.message.reply_text("⚠️ ችግር ተፈጥሯል። /start ይጫኑና እንደገና ይሞክሩ።")

# ==================================================
# TEXT ROUTER
# ==================================================

async def text_router(update, context):
    if update.effective_user is None:
        return
    user_id = update.effective_user.id
    cleanup_pending = context.user_data.get("cleanup_pending")
    if cleanup_pending and is_admin(user_id):
        reason = (update.message.text or "").strip()
        if len(reason) < 5:
            await update.message.reply_text("⚠️ ምክንያቱን ቢያንስ 5 ፊደላት ያስገቡ።")
            return
        context.user_data.pop("cleanup_pending", None)
        ok = db_soft_delete_content(cleanup_pending["table"], cleanup_pending["id"], reason, user_id)
        await update.message.reply_text("✅ መረጃው በSoft Delete ተወግዷል። ለ30 ቀናት መመለስ ይቻላል።" if ok else "⚠️ መዝገቡ አልተወገደም፤ ምናልባት ቀድሞ ተወግዷል።")
        return
    
    if not is_user_registered(user_id):
        await update.message.reply_text("⚠️ **እባክዎ መጀመሪያ /start የሚለውን ተጭነው ስልክ ቁጥርዎን ያስመዝግቡ።**")
        return

    if context.user_data.get("org_add_step"):
        return await admin_org_text_router(update, context)
    if context.user_data.get("media_add_step") or context.user_data.get("media_compose_waiting"):
        return await admin_media_text_router(update, context)
    if context.user_data.get("broadcast_mode") and is_super_admin(user_id):
        return await super_broadcast_send_text(update, context)
    if context.user_data.get("vehicle_edit_id"):
        return await admin_vehicle_edit_save(update, context)
    if context.user_data.get("sending_admin_message"):
        return await handle_user_message(update, context)
    if (context.user_data.get("tracking_lookup_waiting")
        or context.user_data.get("tracking_update_code")
        or context.user_data.get("track_new")):
        return await tracking_text_handler(update, context)
    if context.user_data.get("connect_info"):
        return await connect_fallback_router(update, context)

# ==================================================
# TRACKING TEXT HANDLER
# ==================================================

async def tracking_text_handler(update, context):
    if not update.effective_user or not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if context.user_data.get("tracking_update_code") and text == "❌ ክትትሉን ሰርዝ":
        context.user_data.pop("tracking_update_code", None)
        await update.message.reply_text("ክትትሉ ተሰርዟል።", reply_markup=ReplyKeyboardRemove())
        return
    if context.user_data.get("track_new") and text == "❌ ሰርዝ":
        context.user_data.pop("track_new", None)
        await update.message.reply_text("ጉዞ መክፈቱ ተሰርዟል።", reply_markup=ReplyKeyboardRemove())
        return
    if not context.user_data.get("tracking_lookup_waiting"):
        return
    if text == "❌ ክትትሉን ሰርዝ":
        context.user_data.pop("tracking_lookup_waiting", None)
        await update.message.reply_text("የክትትል ፍለጋው ተሰርዟል።", reply_markup=ReplyKeyboardRemove())
        return
    code = "".join(text.upper().split())
    if len(code) < 6 or len(code) > 20:
        await update.message.reply_text("❌ የክትትል ኮዱ ትክክል አይመስልም።")
        return
    record = db_get_tracking(code)
    if not record:
        await update.message.reply_text("❌ ይህ የክትትል ኮድ አልተገኘም።")
        return
    context.user_data.pop("tracking_lookup_waiting", None)
    await tracking_show_result(update, context, record)

async def tracking_show_result(update, context, record):
    code = record["tracking_code"]
    status_labels = {"ready": "ጉዞ ለመጀመር ዝግጁ", "in_transit": "በመንገድ ላይ", "delivered": "ጉዞው ተጠናቋል"}
    msg = (
        f"📦 **TANA CARGO, LOGISTICS & INFORMATION SERVICES — የቀጥታ ክትትል**\n\n"
        f"🔑 ኮድ፦ `{code}`\n"
        f"📍 መነሻ/መረጃ፦ {record.get('origin') or 'አልተገለጸም'}\n"
        f"🏁 መድረሻ/መንገድ፦ {record.get('destination') or 'አልተገለጸም'}\n"
        f"📊 ሁኔታ፦ {status_labels.get(record.get('status'), record.get('status'))}\n"
    )
    tracking_device_id = get_traccar_device_id_for_user(record.get("driver_user_id"))
    loc = get_traccar_location(tracking_device_id)
    if loc and loc.get("latitude") is not None and loc.get("longitude") is not None:
        lat = float(loc["latitude"])
        lon = float(loc["longitude"])
        map_url = f"https://maps.google.com/?q={lat},{lon}"
        live_label = "🟢 ሹፌሩ ኦንላይን ነው" if loc.get("is_live") else "📴 ሹፌሩ ኦፍላይን ነው — የመጨረሻ ቦታ ነው"
        msg += (
            f"\n{live_label}\n"
            f"📍 **የመጨረሻ ቦታ፦** {loc.get('address')}\n"
            f"🕒 **የተላከበት ሰዓት፦** {loc.get('timestamp')}\n"
            f"🗺️ [በካርታ ላይ ለማየት እዚህ ይጫኑ]({map_url})\n\n"
            "⚠️ ሹፌሩ ኦፍላይን ሲሆን ቦታው በራስ-ሰር አይዘምንም።"
        )
        try:
            await update.effective_message.reply_location(
                latitude=lat,
                longitude=lon
            )
        except Exception:
            pass
    elif record.get("latitude") is not None:
        lat = float(record["latitude"])
        lon = float(record["longitude"])
        map_url = f"https://maps.google.com/?q={lat},{lon}"
        msg += (f"\n📴 የቀጥታ GPS ግንኙነት አሁን አልተገኘም።\n"
                f"📍 የመጨረሻ የተመዘገበ ቦታ፦ {lat:.6f}, {lon:.6f}\n"
                f"🕒 የቦታ ዝመና፦ {record.get('location_updated_at') or 'አልተገለጸም'}\n"
                f"🗺️ [በካርታ ላይ ለማየት]({map_url})")
        try:
            await update.effective_message.reply_location(
                latitude=lat,
                longitude=lon
            )
        except Exception:
            pass
    else:
        msg += "\n📍 ሹፌሩ እስካሁን የGPS ቦታ አልላከም።"
    kb = None
    if record.get('status') != 'delivered':
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛑 ክትትል አቁም (Stop Request)", callback_data=f"stop_req:{code}")
        ]])
    await update.effective_message.reply_text(msg, disable_web_page_preview=True, reply_markup=kb)

async def tracking_location_handler(update, context):
    code = context.user_data.get("tracking_update_code")
    if not code or not update.effective_user or not update.message or not update.message.location:
        return
    record = db_get_tracking(code)
    if not record or int(record["driver_user_id"]) != update.effective_user.id:
        context.user_data.pop("tracking_update_code", None)
        await update.message.reply_text("❌ የክትትል ፈቃዱ አልተረጋገጠም።")
        return
    if record["status"] == "delivered":
        context.user_data.pop("tracking_update_code", None)
        await update.message.reply_text("✅ ጉዞው ተዘግቷል።", reply_markup=ReplyKeyboardRemove())
        return
    loc = update.message.location
    db_update_tracking_location(code, loc.latitude, loc.longitude)
    context.user_data.pop("tracking_update_code", None)
    await update.message.reply_text(
        f"✅ የጉዞ {code} ቦታ ተመዝግቧል።\n\n📍 ኬክሮስ፦ {loc.latitude:.6f}\n📍 ኬንትሮስ፦ {loc.longitude:.6f}\nደንበኛው ይህን ቦታ በክትትል ኮዱ ማየት ይችላል።",
        reply_markup=ReplyKeyboardRemove()
    )

# ==================================================
# BASIC COMMANDS (የተሻሻለው start እና profile)
# ==================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    context.user_data.clear()
    user = update.effective_user
    # Capture the Telegram identity on the very first /start, before phone registration.
    # This lets authorized admins message users later by their stored numeric Telegram ID.
    try:
        db_save_user(user.id, user.full_name, user.username or "")
    except Exception as e:
        print(f"TELEGRAM PROFILE CAPTURE ERROR: {e}")
    if args and args[0].startswith("ref_"):
        try:
            referrer_id = int(args[0].split("_", 1)[1])
            if db_record_referral(referrer_id, user.id):
                try:
                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text=f"🎉 አዲስ ተጠቃሚ በግብዣ ሊንክህ TANA CARGO, LOGISTICS & INFORMATION SERVICES ተቀላቅሏል።\nጠቅላላ ግብዣዎች፦ {db_referral_count(referrer_id)}"
                    )
                except Exception:
                    pass
        except (ValueError, IndexError):
            pass
    if args and args[0].startswith("rent_"):
        try:
            share_token = args[0].split("_", 1)[1]
            return await handle_rental_deeplink(update, context, share_token)
        except (ValueError, IndexError):
            await update.message.reply_text("የክትትል ሊንኩ ትክክል አይደለም።")
            return ConversationHandler.END
    
    # ተጠቃሚው ቀድሞ ስልክ ቁጥር ካስመዘገበ በቀጥታ ምናሌውን ይስጠው
    if is_user_registered(user.id):
        users.setdefault(user.id, {"name": user.full_name, "username": user.username or "", "id": user.id})
        try:
            db_save_user(user.id, user.full_name, user.username or "")
        except Exception as e:
            print(f"DB SAVE USER ERROR: {e}")
            
        total_users = get_total_users_count()
        
        try:
            await update.message.reply_photo(
                photo=START_IMAGE_ID,
                caption=(
                    "👋 **እንኳን ወደ TANA CARGO, LOGISTICS & INFORMATION SERVICES በደህና መጡ!**\n\n"
                    f"👥 **እስካሁን የተቀላቀሉ ተጠቃሚዎች፦** {total_users:,}\n\n"
                    "👇 ከታች ካለው ምናሌ የፈለጉትን ይምረጡ፦"
                ),
                reply_markup=main_menu(user.id),
                parse_mode="Markdown"
            )
        except Exception:
            await update.message.reply_text(
                "👋 **እንኳን ደህና መጡ ወደ TANA CARGO, LOGISTICS & INFORMATION SERVICES!**\n\n"
                f"👥 **እስካሁን የተቀላቀሉ ተጠቃሚዎች፦** {total_users:,}\n\n"
                "👇 ከታች ካለው ምናሌ የፈለጉትን ይምረጡ፦",
                reply_markup=main_menu(user.id),
                parse_mode="Markdown"
            )
            
        return ConversationHandler.END
    
    # ስልክ ከሌለው ፎቶውን አድርጎ ስልክ እንዲያስገባ ይጠይቀው
    try:
        await update.message.reply_photo(
            photo=START_IMAGE_ID,
            caption=(
                "🚛 **TANA CARGO, LOGISTICS & INFORMATION SERVICES** 📦\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "👋 **እንኳን ወደ TANA CARGO, LOGISTICS & INFORMATION SERVICES ጣና ጭነት በደህና መጡ!**\n\n"
                "START 👈 ይጫኑ፤ ስልክ ያጋሩ። 📱\n\n"
                "📱 **እባክዎ ለመጀመር የራስዎን ስልክ ቁጥር ያስመዝግቡ።**\n\n"
                "🔸 በ09 ወይም በ07 የሚጀምር እና 10 ዲጂት ያለው መሆን አለበት።\n"
                "🔸 ምሳሌ፦ `0912345678`\n\n"
                "⚠️ **ማሳሰቢያ፦** ይህ ስልክ ቁጥርዎ በTANA CARGO, LOGISTICS & INFORMATION SERVICES ለሚደረጉ ግንኙነቶች ይጠቅማል።"
            ),
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📱 ስልኬን አጋራ", request_contact=True)]],
                resize_keyboard=True, one_time_keyboard=True
            ),
            parse_mode="Markdown"
        )
    except Exception:
        await update.message.reply_text(
            "🚛 **TANA CARGO, LOGISTICS & INFORMATION SERVICES | ጣና ጭነት** 📦\n\n"
            "👋 **እንኳን ደህና መጡ!**\n\n"
            "📱 **እባክዎ ለመጀመር የራስዎን ስልክ ቁጥር ያስመዝግቡ።**\n"
            "🔸 ምሳሌ፦ `0912345678`",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton("📱 ስልኬን አጋራ", request_contact=True)]],
                resize_keyboard=True, one_time_keyboard=True
            ),
            parse_mode="Markdown"
        )
        
    return START_PHONE

async def start_phone_input(update, context):
    if update.message.contact:
        contact = update.message.contact
        if contact.user_id and contact.user_id != update.effective_user.id:
            await update.message.reply_text("❌ እባክዎ የራስዎን contact ብቻ ያጋሩ።")
            return START_PHONE
        phone = contact.phone_number
        if phone.startswith("+251"):
            phone = "0" + phone[4:]
        elif phone.startswith("251"):
            phone = "0" + phone[3:]
    else:
        phone = (update.message.text or "").strip().split("\n")[0].strip()
    is_valid, error = validate_phone(phone)
    if not is_valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678")
        return START_PHONE
    
    user = update.effective_user
    pending_share_token = context.user_data.get("pending_rental_share_token")
    if pending_share_token:
        pending_rec = db_get_rental_request_by_token(pending_share_token)
        if not pending_rec or pending_rec.get("status") != "approved":
            context.user_data.pop("pending_rental_share_token", None)
            await update.message.reply_text("ይህ የክትትል ፈቃድ አሁን አይሰራም።")
            return ConversationHandler.END
        if not phones_match(phone, pending_rec["target_phone"]):
            await update.message.reply_text(
                "❌ የተጋሩት ስልክ ቁጥር ከተጠቀሰው ደንበኛ ጋር አይዛመድም። "
                "እባክዎ ትክክለኛውን የራስዎን contact ያጋሩ።"
            )
            return START_PHONE
    users.setdefault(user.id, {"name": user.full_name, "username": user.username or "", "id": user.id})
    users[user.id]["owner"] = {"phone": phone, "name": user.full_name, "user_id": user.id}
    
    try:
        db_save_user(user.id, user.full_name, user.username or "", owner_name=user.full_name, owner_phone=phone)
        if not is_user_registered(user.id):
            raise RuntimeError("User registration was not found after database save")
    except Exception as e:
        print(f"DB SAVE USER ERROR: {e}")
        await update.message.reply_text(
            "❌ ምዝገባዎን በዳታቤዝ ላይ ማስቀመጥ አልተቻለም።\n"
            "እባክዎ ከጥቂት ጊዜ በኋላ /start በድጋሚ ይጫኑ።"
        )
        return START_PHONE
        
    total_users = get_total_users_count()
    
    # አዲስ ምዝገባ ካለ በመጠባበቅ ላይ ያለ መረጃ እንዳለ ማረጋገጥ
    pending_share_token = context.user_data.pop("pending_rental_share_token", None)
    if pending_share_token:
        try:
            rec = db_get_rental_request_by_token(pending_share_token)
            if rec and rec.get("status") == "approved":
                db_update_rental_request(rec["id"], target_user_id=user.id)
                await send_rental_tracking_to_user(context, rec["id"], user.id)
        except Exception as e:
            print(f"Pending rental link delivery failed: {e}")

    pending_infos = [p for p in pending_gmaps_shares if p["phone"] == phone]
    for p_info in pending_infos:
        try:
            tracking_link = p_info.get("traccar_url") or ""
            if tracking_link:
                tracking_text = f"🗺️ [TANA CARGO, LOGISTICS & INFORMATION SERVICES Live Tracking]({tracking_link})"
            elif p_info.get("lat") is not None and p_info.get("lon") is not None:
                fallback_map = f"https://www.google.com/maps?q={p_info['lat']},{p_info['lon']}"
                tracking_text = f"🗺️ [በGoogle Maps ክፈት]({fallback_map})"
            else:
                tracking_text = "📴 አሁን የመጨረሻ GPS ቦታ አልተመዘገበም። ሹፌሩ GPS እና ኢንተርኔት ሲያበራ መረጃው ይታያል።"
            await context.bot.send_message(
                chat_id=user.id,
                text=(
                    "🎉 **TANA CARGO, LOGISTICS & INFORMATION SERVICES — የተፈቀደ የክትትል መረጃ!**\n\n"
                    f"{tracking_text}\n\n"
                    f"📍 የመጨረሻ አድራሻ: {p_info.get('address') or 'ገና አልተገኘም'}\n"
                    "👉 @tanacargobot"
                ),
                disable_web_page_preview=False
            )
            pending_gmaps_shares.remove(p_info)
            try:
                db_delete_pending_gmaps_share(p_info.get("_db_id"))
            except Exception as e:
                print(f"PENDING GPS SHARE DELETE ERROR: {e}")
            
            for admin_id in get_admin_ids():
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=f"✅ **ተጠቃሚው ስልክ ቁጥሩን አስመዝግቧል። በመጠባበቅ ላይ የነበረው መረጃ በተሳካ ሁኔታ ተልኳል።**\n\n👤 ስም: {p_info['name']}\n📞 ስልክ: {phone}"
                    )
                except Exception:
                    pass
        except Exception as e:
            print(f"Error sending pending gmaps info: {e}")

    try:
        await update.message.reply_photo(
            photo=START_IMAGE_ID,
            caption=(
                "✅ **ስልክ ቁጥርዎ በትክክል ተመዝግቧል!**\n\n"
                f"📞 **{phone}**\n\n"
                "እናመሰግናለን ምዝገባዎን አጠናቀዋል።\n"
                "ከታች ካሉት ምናሌዎች የፈለጉትን መጠቀም ይችላሉ።\n\n"
                f"👥 የተቀላቀሉ ተጠቃሚዎች፦ {total_users:,}\n"
                "📢 ሙሉ መረጃ፦ @tanacargopage"
            ),
            reply_markup=main_menu(user.id),
            parse_mode="Markdown"
        )
    except Exception:
        await update.message.reply_text(
            "✅ **ስልክ ቁጥርዎ በትክክል ተመዝግቧል!**\n\n"
            "👇 ከታች ካለው ምናሌ የፈለጉትን ይምረጡ፦",
            reply_markup=main_menu(user.id)
        )
        
    return ConversationHandler.END

async def profile(update, context):
    user_id = update.effective_user.id
    user = users.get(user_id, {"name": update.effective_user.full_name, "username": update.effective_user.username or "", "id": user_id})
    
    total_users = get_total_users_count()
    
    message = (
        "👤 **የኔ መረጃ**\n\n"
        f"👤 ስም፦ {user['name']}\n🔗 Username፦ @{user['username'] or 'የለም'}\n🆔 Telegram ID፦ {user['id']}\n"
        f"\n👥 **ጠቅላላ ተጠቃሚዎች፦** {total_users:,}\n"
    )
    if "owner" in user:
        owner = user["owner"]
        message += f"\n📦 የጭነት ባለቤት ምዝገባ፦ ✅\n👤 {owner.get('name', 'N/A')}\n📞 {owner.get('phone', 'N/A')}\n"
    if is_broker(user_id):
        message += "\n🏷️ የጭነት ደላላ: ✅ ጸድቋል\n"
    my_cargo = [c for c in cargo_posts if c["user_id"] == user_id]
    my_trucks = [t for t in truck_posts if t["user_id"] == user_id]
    message += f"\n🚚 የለጠፉት ጭነት፦ {len(my_cargo)}\n🚛 የተመዘገቡ መኪኖች፦ {len(my_trucks)}"
    
    await update.message.reply_text(message, reply_markup=main_menu(user_id))

async def support_start(update, context):
    await update.message.reply_text(
        "📞 TANA CARGO, LOGISTICS & INFORMATION SERVICES የጣና ጭነት እገዛ\n\nለእገዛ ለማግኘት @tanacargosupportbot ይጠቀሙ።\n\n"
        f"☎️ {SUPPORT_PHONE} | Telegram: @tanacargosupportbot\n\n🕐 24 ሰዓት / 7 ቀን በመስመር ላይ ነን።",
        reply_markup=main_menu(update.effective_user.id)
    )
    return ConversationHandler.END

async def about(update, context):
    await update.message.reply_text(
        "🚚 **TANA CARGO, LOGISTICS & INFORMATION SERVICES | ጣና ጭነት**\n\n"
        "የጭነት ባለቤቶችንና የጭነት መኪና ባለቤቶችን ለማገናኘት፣ የጭነት ማጓጓዣ ሂደትን "
        "ለማቀላጠፍ እና ቀላልና የተደራጁ አገልግሎት ለመስጠት የተዘጋጀ የጭነት ማገናኛ አገልግሎት ነው።\n\n"
        "🤝 ጭነት ያለዎት? ከተመዘገቡ የጭነት መኪናዎች ጋር ይገናኙ።\n\n"
        "🚛 የጭነት መኪና አለዎት? የሚፈልጉትን ጭነት ይፈልጉ።\n\n"
        "❤️ TANA CARGO, LOGISTICS & INFORMATION SERVICES — ጭነትንና መኪናን እናገናኛለን።\n\n🙏 እኛን ስለመረጡ እናመሰግናለን።",
        reply_markup=main_menu(update.effective_user.id)
    )

async def service_payment(update, context):
    await update.message.reply_text(payment_methods_text(), reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END

async def cancel(update, context):
    context.user_data.clear()
    await update.message.reply_text("❌ ሂደቱ ተሰርዟል።", reply_markup=main_menu(update.effective_user.id))
    return ConversationHandler.END

# ==================================================
# ERROR / HEALTH / EXPIRY
# ==================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"TANA CARGO, LOGISTICS & INFORMATION SERVICES ERROR: {context.error}")
    try:
        print(f"ERROR UPDATE: {update}")
    except Exception:
        pass

class HealthHandler(BaseHTTPRequestHandler):
    def _send(self, status, content_type, body):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path.strip("/")
        if path in ("", "health", "healthz"):
            return self._send(200, "text/plain; charset=utf-8", "TANA CARGO, LOGISTICS & INFORMATION SERVICES is running")
        parts = path.split("/")
        if len(parts) == 2 and parts[0] in ("track", "api-track"):
            token = unquote(parts[1])
            record = get_public_tracking_token(token)
            if not record:
                return self._send(404, "text/html; charset=utf-8", "<h2>TANA CARGO, LOGISTICS & INFORMATION SERVICES</h2><p>ይህ የክትትል ሊንክ ተዘግቷል ወይም አልተገኘም።</p>")
            if parts[0] == "api-track":
                loc = get_traccar_location(record["device_id"])
                if not loc:
                    return self._send(503, "application/json; charset=utf-8", json.dumps({"ok": False, "message": "GPS መረጃ ገና አልተገኘም።"}, ensure_ascii=False))
                payload = {"ok": True, **loc}
                if not loc.get("is_live"):
                    payload.update(estimate_offline_position(loc) or {})
                return self._send(200, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False))
            page = r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TANA CARGO, LOGISTICS & INFORMATION SERVICES Live Tracking</title><link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"><style>body{margin:0;background:#f2f5f7;font-family:Arial,sans-serif;color:#17212b}.top{background:#174d75;color:white;padding:18px 16px}.top b{font-size:21px}.wrap{padding:16px;max-width:900px;margin:auto}.card{background:white;border-radius:14px;padding:16px;box-shadow:0 2px 12px #0001;margin-bottom:12px}#map{height:58vh;min-height:330px;border-radius:12px;background:#e4e9ed;overflow:hidden}.muted{color:#657786;font-size:14px}.car{font-size:28px;text-shadow:0 1px 3px #fff}.warn{color:#9a5b00;font-weight:600}</style></head><body><div class="top"><b>🚐 TANA CARGO, LOGISTICS & INFORMATION SERVICES | ጣና ጭነት</b><div>LIVE GPS TRACKING</div></div><div class="wrap"><div class="card"><b>Vehicle Live Location</b><p id="status" class="muted">Waiting for GPS data...</p><div id="map"></div><p class="muted">Last Updated: <span id="time">-</span></p><p id="estimate" class="warn"></p></div><div class="muted">TANA CARGO, LOGISTICS & INFORMATION SERVICES tracking page.</div></div><script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>const api='/api-track/TOKEN_VALUE';const map=L.map('map').setView([9,39],6);L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(map);let car=null,last=null;const carIcon=L.divIcon({className:'',html:'<div class="car">🚚</div>',iconSize:[36,36],iconAnchor:[18,18]});async function refresh(){try{const r=await fetch(api,{cache:'no-store'});const d=await r.json();if(!d.ok)throw Error('GPS unavailable');const live=!!d.is_live;const lat=Number(live?d.latitude:(d.estimated_latitude??d.latitude));const lon=Number(live?d.longitude:(d.estimated_longitude??d.longitude));if(!Number.isFinite(lat)||!Number.isFinite(lon))throw Error('bad coordinates');document.getElementById('status').textContent=live?'🟢 LIVE GPS — Vehicle online':'📴 OFFLINE — Estimated last-known area';document.getElementById('time').textContent=(d.timestamp||'-').replace('T',' ');document.getElementById('estimate').textContent=live?'':(d.estimated_distance_km!==undefined?('Estimated movement: '+d.estimated_distance_km+' km over '+d.elapsed_minutes+' minutes, based on last recorded speed ('+d.estimated_speed_kmh+' km/h) and direction. Approximate only; not road routing or live GPS.'): 'No reliable movement estimate; showing last recorded GPS position.');if(!car)car=L.marker([lat,lon],{icon:carIcon}).addTo(map);else car.setLatLng([lat,lon]);if(!last)last=L.circleMarker([d.latitude,d.longitude],{radius:7,color:'#d97706'}).addTo(map).bindPopup('Last recorded GPS position');else last.setLatLng([d.latitude,d.longitude]);map.setView([lat,lon],live?15:13)}catch(e){document.getElementById('status').textContent='Unable to load GPS data. Please try again.'}}refresh();setInterval(refresh,15000);</script></body></html>'''
            page = page.replace("TOKEN_VALUE", token)
            return self._send(200, "text/html; charset=utf-8", page)
        return self._send(404, "text/plain; charset=utf-8", "Not found")

    def log_message(self, format, *args):
        return

def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"✅ TANA CARGO, LOGISTICS & INFORMATION SERVICES health server listening on port {port}")
    return server

async def clean_expired_posts(context):
    now = datetime.now()
    expired_cargos = []
    expired_trucks = []
    for i, cargo in enumerate(cargo_posts):
        if not cargo.get("active", True):
            continue
        ed = cargo.get("expiry_date")
        if ed:
            try:
                if now >= datetime.fromisoformat(ed):
                    expired_cargos.append(i)
            except Exception:
                pass
    for i, truck in enumerate(truck_posts):
        if not truck.get("active", True):
            continue
        ed = truck.get("expiry_date")
        if ed:
            try:
                if now >= datetime.fromisoformat(ed):
                    expired_trucks.append(i)
            except Exception:
                pass
    for i in expired_cargos:
        cargo_posts[i]["active"] = False
        if cargo_posts[i].get("id"):
            db_update_post_active("cargo_posts", cargo_posts[i]["id"], False)
        try:
            await context.bot.send_message(chat_id=cargo_posts[i]["user_id"], text=f"⏰ **የጭነት ጊዜ ገደብ**\n\n📍 {cargo_posts[i]['from']} ➡️ {cargo_posts[i]['to']}\n📦 {cargo_posts[i]['type']}\n\n72 ሰዓት ስለሞላ ተወግዷል።")
        except Exception:
            pass
    for i in expired_trucks:
        truck_posts[i]["active"] = False
        if truck_posts[i].get("id"):
            db_update_post_active("truck_posts", truck_posts[i]["id"], False)
        try:
            await context.bot.send_message(chat_id=truck_posts[i]["user_id"], text=f"⏰ **የመኪና ጊዜ ገደብ**\n\n🚛 {truck_posts[i]['type']}\n\n72 ሰዓት ስለሞላ ተወግዷል።")
        except Exception:
            pass

# ==================================================
# ADMIN MEDIA DIRECTORY (Telegram / Facebook)
# ==================================================

MEDIA_TYPES = {
    "tg_channel": ("Telegram", "Channel"),
    "tg_group": ("Telegram", "Group"),
    "fb_page": ("Facebook", "Page"),
    "fb_group": ("Facebook", "Group"),
    "web_site": ("Website", "Website"),
}

def db_media_add(platform, media_type, name, url, admin_id):
    conn = _db_connect(DB_PATH, timeout=10)
    try:
        conn.execute("INSERT INTO admin_media_links (platform,media_type,organization_name,media_url,created_by,created_at,active) VALUES (?,?,?,?,?,?,1)",
                     (platform, media_type, name, url, int(admin_id), datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()

def db_media_list(platform=None):
    conn = _db_connect(DB_PATH, timeout=10)
    try:
        if platform:
            return conn.execute("SELECT id,platform,media_type,organization_name,media_url FROM admin_media_links WHERE active=1 AND LOWER(platform)=LOWER(?) ORDER BY id DESC", (platform,)).fetchall()
        return conn.execute("SELECT id,platform,media_type,organization_name,media_url FROM admin_media_links WHERE active=1 ORDER BY id DESC").fetchall()
    finally:
        conn.close()


MEDIA_KEYWORDS=(
    "tender", "bid", "procurement", "quotation", "rfq", "rfi", "r after", "purchase",
    "vacancy", "job", "employment", "recruitment", "career", "auction", "disposal",
    "sale", "for sale", "selling", "rent", "rental", "lease", "ማስታወቂያ", "ጨረታ",
    "ግዥ", "ግዢ", "የግዥ", "የጨረታ", "የሥራ ማስታወቂያ", "ሥራ ክፍት",
    "ቅጥር", "ሽያጭ", "ይሸጣል", "ይከራያል", "ኪራይ", "ሐራጅ", "የሥራ ዕድል",
    "truck", "cargo", "vehicle", "excavator", "bulldozer", "grader", "loader", "generator",
    "warehouse", "apartment", "property", "freight", "haulage", "logistics", "machinery",
    "construction", "livestock", "agricultural", "factory", "equipment", "disposal", "scrap",
    "ማሽነሪ", "ጭነት", "መጋዘን", "ቤት", "መሬት", "ፋብሪካ", "ግንባታ", "ግብርና", "ሐራጅ"
)
class _VisibleTextParser(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ("script","style","noscript"): self.skip+=1
    def handle_endtag(self,tag):
        if tag in ("script","style","noscript") and self.skip: self.skip-=1
    def handle_data(self,data):
        if not self.skip and data.strip(): self.parts.append(data.strip())
def _trade_post(text):
    low=(text or "").lower(); return len(low)>=35 and any(k in low for k in MEDIA_KEYWORDS)
def db_save_media_post(source_url,source_name,platform,post_url,post_text,post_date=""):
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        conn.execute("""INSERT INTO media_posts(source_url,source_name,platform,post_url,post_text,post_date,created_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_url,post_url) DO NOTHING""",(source_url,source_name,platform,post_url,post_text[:12000],post_date,datetime.now(timezone.utc).isoformat())); conn.commit()
    finally: conn.close()
def db_recent_media_posts(limit=20):
    conn=_db_connect(DB_PATH,timeout=10)
    try: return conn.execute("SELECT source_name,platform,post_url,post_text,post_date FROM media_posts WHERE COALESCE(is_deleted,0)=0 ORDER BY id DESC LIMIT ?",(int(limit),)).fetchall()
    finally: conn.close()
def _import_public_media_link(row):
    url=row["media_url"]; name=row["organization_name"]; platform=(row["platform"] or "").lower(); count=0
    try:
        if platform=="telegram":
            path=urlparse(url).path.strip("/"); channel=path.split("/")[0]
            if not channel or channel.startswith("+"): return 0,"Private/invite-only Telegram links are not readable"
            resp=requests.get(f"https://t.me/s/{channel}",timeout=15,headers={"User-Agent":"Mozilla/5.0"})
            if resp.status_code!=200: return 0,f"HTTP {resp.status_code}"
            texts=re.findall(r'<div[^>]*class="[^"]*tgme_widget_message_text[^"]*"[^>]*>([\s\S]*?)</div>',resp.text,re.I)
            links=re.findall(r'<a[^>]*class="[^"]*tgme_widget_message_date[^"]*"[^>]*href="([^"]+)"',resp.text,re.I)
            for i,raw in enumerate(texts):
                parser=_VisibleTextParser(); parser.feed(raw); body=html_unescape(" ".join(parser.parts))
                if _trade_post(body): db_save_media_post(url,name,"Telegram",links[i] if i<len(links) else url,body); count+=1
        elif platform=="facebook":
            token=os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN","").strip()
            if not token: return 0,"FACEBOOK_PAGE_ACCESS_TOKEN is required for Meta Graph API access"
            m=re.search(r"facebook\.com/(?:pages/)?([^/?]+)(?:/(\d+))?",url,re.I)
            if not m: return 0,"Page ID/name not recognized"
            page_id=m.group(2) or m.group(1)
            resp=requests.get(f"https://graph.facebook.com/{os.getenv('FACEBOOK_GRAPH_API_VERSION', 'v26.0')}/{page_id}/posts",params={"fields":"message,permalink_url,created_time","limit":50,"access_token":token},timeout=15); data=resp.json()
            if resp.status_code!=200: return 0,str(data.get("error",{}).get("message","Graph API error"))[:160]
            for post in data.get("data",[]):
                body=post.get("message","")
                if _trade_post(body): db_save_media_post(url,name,"Facebook",post.get("permalink_url",url),body,post.get("created_time","")); count+=1
        else:
            resp=requests.get(url,timeout=15,headers={"User-Agent":"Mozilla/5.0"})
            if resp.status_code!=200: return 0,f"HTTP {resp.status_code}"
            parser=_VisibleTextParser(); parser.feed(resp.text); body=" ".join(parser.parts)
            if _trade_post(body): db_save_media_post(url,name,"Website",url,body); count+=1
        return count,""
    except Exception as exc: return 0,str(exc)[:160]
# ==================================================
# ORGANIZATION DIRECTORY + AUTOMATIC ANNOUNCEMENT MONITOR
# ==================================================

ORG_TYPE_LABELS = {
    "government": "መንግስት ተቋም", "education": "ዩኒቨርሲቲ/ትምህርት",
    "factory": "ፋብሪካ/ኢንዱስትሪ", "construction": "ኮንስትራክሽን",
    "business": "የግል ድርጅት/ንግድ", "transport": "ትራንስፖርት/ሎጂስቲክስ",
    "agriculture": "ግብርና", "property": "ሪል እስቴት/ንብረት",
    "other": "ሌላ"
}

ANNOUNCEMENT_TERMS = {
    "vehicles": ("vehicle", "car", "pickup", "truck", "bus", "trailer", "ambulance", "automobile", "መኪና", "ተሽከርካሪ", "የመኪና"),
    "machinery": ("excavator", "bulldozer", "grader", "loader", "roller", "crane", "generator", "forklift", "machinery", "equipment", "ማሽነሪ", "ጀነሬተር"),
    "cargo": ("freight", "haulage", "cargo", "logistics", "transport", "truck rental", "delivery", "ጭነት", "ማጓጓዣ", "ትራንስፖርት"),
    "property": ("property", "house", "apartment", "land", "warehouse", "office", "building for rent", "real estate", "ቤት", "መሬት", "መጋዘን", "ኪራይ ቤት"),
    "construction": ("construction", "road work", "building work", "water works", "electrical works", "renovation", "maintenance", "ግንባታ", "መንገድ", "የግንባታ"),
    "goods": ("procurement", "supply", "goods", "stationery", "furniture", "computer", "electronics", "tyre", "tires", "fuel", "lubricant", "uniform", "food supply", "ግዥ", "አቅርቦት", "ዕቃ"),
    "services": ("service", "consulting", "training", "printing", "security", "cleaning", "catering", "audit", "ጽዳት", "አገልግሎት", "ስልጠና"),
    "industrial_agri": ("factory", "industrial", "raw material", "agricultural", "livestock", "farm", "production input", "ፋብሪካ", "ግብርና", "እንስሳት"),
    "auction": ("auction", "disposal", "scrap", "surplus", "ሐራጅ", "ማስወገድ"),
    "job": ("vacancy", "job", "employment", "recruitment", "career", "ቅጥር", "የሥራ ማስታወቂያ", "ሥራ ክፍት"),
}
CATEGORY_AM = {
    "vehicles":"ተሽከርካሪ", "machinery":"ማሽነሪና መሣሪያ", "cargo":"ጭነትና ሎጂስቲክስ",
    "property":"ቤት/መሬት/መጋዘን", "construction":"ግንባታና መሠረተ ልማት",
    "goods":"አጠቃላይ ዕቃና አቅርቦት", "services":"አገልግሎት",
    "industrial_agri":"ኢንዱስትሪና ግብርና", "auction":"ሐራጅና ንብረት ማስወገድ",
    "job":"የሥራ ማስታወቂያ", "tender":"ጨረታ/ግዥ", "other":"ሌላ"
}

def _announcement_category(text):
    low=(text or "").lower()
    # Formal tender language takes precedence unless a more specific asset category is clear.
    for category in ("auction", "vehicles", "machinery", "cargo", "property", "construction", "industrial_agri", "services", "goods", "job"):
        if any(term in low for term in ANNOUNCEMENT_TERMS[category]): return category
    if any(term in low for term in ("tender", "bid", "procurement", "quotation", "rfq", "ጨረታ", "የግዥ")):
        return "tender"
    return "other"

def db_org_upsert(name, region, org_type, admin_id):
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        conn.execute("""INSERT INTO monitored_organizations(name,region,org_type,created_by,created_at,active)
            VALUES(?,?,?,?,?,1) ON CONFLICT(name,region) DO UPDATE SET org_type=EXCLUDED.org_type,active=1""",
            (name.strip(),region.strip(),org_type.strip(),int(admin_id),datetime.now(timezone.utc).isoformat()))
        row=conn.execute("SELECT id FROM monitored_organizations WHERE LOWER(name)=LOWER(?) AND LOWER(region)=LOWER(?)",(name.strip(),region.strip())).fetchone()
        conn.commit(); return int(row["id"])
    finally: conn.close()

def db_source_add(org_id, platform, url):
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        conn.execute("""INSERT INTO monitored_sources(organization_id,platform,source_url,created_at)
            VALUES(?,?,?,?) ON CONFLICT(organization_id,platform,source_url) DO UPDATE SET active=1""",
            (int(org_id),platform,url,datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally: conn.close()

def db_org_directory():
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        return conn.execute("""SELECT o.id,o.name,o.region,o.org_type,s.id AS source_id,s.platform,s.source_url,
            s.last_result,s.last_checked_at,s.last_error
            FROM monitored_organizations o LEFT JOIN monitored_sources s ON s.organization_id=o.id AND s.active=1
            WHERE o.active=1 ORDER BY o.region,o.org_type,o.name,s.platform""").fetchall()
    finally: conn.close()

def db_monitor_sources():
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        return conn.execute("""SELECT s.id,s.platform,s.source_url,o.id AS organization_id,o.name AS organization_name,
            o.region,o.org_type,s.baseline_done FROM monitored_sources s JOIN monitored_organizations o ON o.id=s.organization_id
            WHERE s.active=1 AND o.active=1 ORDER BY s.id""").fetchall()
    finally: conn.close()

def db_record_source_check(source_id, result, error="", new_count=0):
    now=datetime.now(timezone.utc).isoformat(); conn=_db_connect(DB_PATH,timeout=10)
    try:
        conn.execute("""UPDATE monitored_sources SET last_checked_at=?,last_result=?,last_error=?,
            last_success_at=CASE WHEN ? THEN ? ELSE last_success_at END,
            last_new_at=CASE WHEN ? THEN ? ELSE last_new_at END WHERE id=?""",
            (now,result,(error or "")[:500],result in ("new","none"),now,
             bool(new_count),now,int(source_id)))
        conn.commit()
    finally: conn.close()

def db_mark_source_baseline(source_id):
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        conn.execute("UPDATE monitored_sources SET baseline_done=1 WHERE id=?",(int(source_id),))
        conn.commit()
    finally: conn.close()

def translate_notice_to_amharic(text):
    """Translate public notice text when a Google Cloud Translation API key is configured."""
    if not GOOGLE_TRANSLATE_API_KEY or not text:
        return ""
    try:
        response=requests.post("https://translation.googleapis.com/language/translate/v2",
            params={"key":GOOGLE_TRANSLATE_API_KEY},
            json={"q":text[:4500],"target":"am","format":"text"},timeout=18)
        if response.status_code!=200:
            logger.warning("Google Translate API returned HTTP %s",response.status_code)
            return ""
        translated=response.json().get("data",{}).get("translations",[{}])[0].get("translatedText","")
        return html_unescape(translated).strip()[:12000]
    except Exception:
        logger.exception("Notice translation failed")
        return ""

def db_save_announcement(source, post_url, post_text, post_date="", review_status="pending"):
    body=(post_text or "").strip()
    if len(body)<30 or not any(k in body.lower() for k in MEDIA_KEYWORDS): return False
    digest=hashlib.sha256((post_url+"\n"+body).encode("utf-8",errors="ignore")).hexdigest()
    ref_match=re.search(r"(?:tender\s*(?:no|number)?|bid\s*(?:no|number)?|reference\s*(?:no|number)?|ref\.?|ጨረታ\s*ቁጥር)\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{4,})",body,re.I)
    reference=(ref_match.group(1).strip(".,;:") if ref_match else "")[:100]
    title=body[:180]
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        # Global duplicate suppression across registered feeds: exact source URL, issuer+reference, or issuer+title.
        duplicate=conn.execute("""SELECT id FROM organization_announcements WHERE post_url=?
            OR (organization_id=? AND reference_number<>'' AND reference_number=?)
            OR (organization_id=? AND LOWER(title)=LOWER(?)) LIMIT 1""",
            (post_url,int(source["organization_id"]),reference,int(source["organization_id"]),title)).fetchone()
        if duplicate: return False
        translated=translate_notice_to_amharic(body)
        cur=conn.execute("""INSERT INTO organization_announcements(organization_id,source_id,category,title,post_text,
            post_url,post_date,content_hash,created_at,reference_number,review_status,translated_text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id,content_hash) DO NOTHING""",
            (source["organization_id"],source["id"],_announcement_category(body),title,body[:12000],post_url,
             post_date or "",digest,datetime.now(timezone.utc).isoformat(),reference,review_status,translated))
        conn.commit(); return cur.rowcount>0
    finally: conn.close()

def _fetch_source_announcements(source):
    """Fetch only public/authorized content. Returns (posts, error)."""
    url=source["source_url"]; platform=(source["platform"] or "").lower(); posts=[]
    try:
        if platform=="telegram":
            path=urlparse(url).path.strip("/"); channel=path.split("/")[0]
            if not channel or channel.startswith("+"): return [],"Private/invite-only Telegram source needs authorized access"
            resp=requests.get(f"https://t.me/s/{channel}",timeout=20,headers={"User-Agent":"Mozilla/5.0"})
            if resp.status_code!=200: return [],f"HTTP {resp.status_code}"
            texts=re.findall(r'<div[^>]*class="[^"]*tgme_widget_message_text[^"]*"[^>]*>([\s\S]*?)</div>',resp.text,re.I)
            links=re.findall(r'<a[^>]*class="[^"]*tgme_widget_message_date[^"]*"[^>]*href="([^"]+)"',resp.text,re.I)
            for i,raw in enumerate(texts):
                parser=_VisibleTextParser(); parser.feed(raw); body=html_unescape(" ".join(parser.parts))
                if _trade_post(body): posts.append((links[i] if i<len(links) else url,body,""))
        elif platform=="facebook":
            token=os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN","").strip()
            if not token: return [],"FACEBOOK_PAGE_ACCESS_TOKEN is required for Meta Graph API access"
            parsed_url=urlparse(url)
            query_id=(parse_qs(parsed_url.query).get("id") or [None])[0]
            m=re.search(r"facebook\.com/(?:pages/)?([^/?]+)(?:/(\d+))?",url,re.I)
            if not m and not query_id: return [],"Facebook Page ID/name not recognized"
            page_id=query_id or m.group(2) or m.group(1)
            resp=requests.get(f"https://graph.facebook.com/{os.getenv('FACEBOOK_GRAPH_API_VERSION', 'v26.0')}/{page_id}/posts",params={"fields":"message,permalink_url,created_time","limit":50,"access_token":token},timeout=20)
            data=resp.json()
            if resp.status_code!=200: return [],str(data.get("error",{}).get("message","Graph API error"))[:250]
            for post in data.get("data",[]):
                body=post.get("message","")
                if _trade_post(body): posts.append((post.get("permalink_url",url),body,post.get("created_time","")))
        else:
            resp=requests.get(url,timeout=20,headers={"User-Agent":"Mozilla/5.0"})
            if resp.status_code!=200: return [],f"HTTP {resp.status_code}"
            ctype=resp.headers.get("content-type","").lower()
            if "html" not in ctype and "xml" not in ctype: return [],f"Unsupported content type: {ctype[:80]}"
            parser=_VisibleTextParser(); parser.feed(resp.text); body=html_unescape(" ".join(parser.parts))
            if _trade_post(body):
                # Hashing the page content makes changed public notices detectable even when the page URL stays the same.
                h=hashlib.sha256(body.encode("utf-8",errors="ignore")).hexdigest()[:16]
                posts.append((url+"#notice-"+h,body,""))
        return posts,""
    except Exception as exc: return [],str(exc)[:250]

async def monitor_registered_sources(context):
    """Runs hourly. New matching announcements are sent to configured bot admins."""
    try:
        sources=db_monitor_sources()
    except Exception:
        logger.exception("Could not load monitored sources")
        return
    for source in sources:
        try:
            posts,error=await asyncio.to_thread(_fetch_source_announcements,source)
            if error:
                db_record_source_check(source["id"],"error",error,0)
                logger.warning("Source monitor error (%s): %s",source["source_url"],error)
                continue
            added=[]
            first_scan=not bool(source.get("baseline_done"))
            for post_url,body,post_date in posts:
                if db_save_announcement(source,post_url,body,post_date,review_status=("archived" if first_scan else "pending")) and not first_scan:
                    added.append((post_url,body,post_date))
            if first_scan:
                db_mark_source_baseline(source["id"])
            db_record_source_check(source["id"],"new" if added else "none","",len(added))
        except Exception:
            logger.exception("Unexpected monitoring failure for source id=%s",source.get("id"))
            try: db_record_source_check(source["id"],"error","Unexpected monitor error",0)
            except Exception: pass

def notice_kind_label(body):
    low=(body or "").lower()
    formal=("tender","bid","procurement","quotation","request for proposal","rfp","rfq","ጨረታ","የግዥ")
    if any(term in low for term in formal): return "መደበኛ ጨረታ/ግዥ"
    if any(term in low for term in ("auction","disposal","ሐራጅ")): return "ሐራጅ/ንብረት ማስወገድ"
    if any(term in low for term in ("rent","rental","lease","for rent","ኪራይ","ይከራያል")): return "መደበኛ የኪራይ ማስታወቂያ"
    if any(term in low for term in ("for sale","sale","selling","ይሸጣል","ሽያጭ")): return "መደበኛ የሽያጭ ማስታወቂያ"
    return "ሌላ የንግድ ማስታወቂያ"

def extract_notice_key_lines(body):
    """Surface source wording for dates, bid security, fees and requirements without guessing."""
    terms=("deadline","closing","submission","bid security","bid bond","cpo","document fee","fee",
        "የመዝጊያ","መዝጊያ","ማስከበሪያ","ዋስትና","የሰነድ ዋጋ","የመጨረሻ","ብቃት","መስፈርት")
    lines=[]
    for line in (body or "").splitlines():
        clean=" ".join(line.split()).strip(" •-–")
        if clean and any(term in clean.lower() for term in terms):
            if clean not in lines: lines.append(clean[:260])
        if len(lines)>=8: break
    return lines

def db_get_announcement_by_url(source_id, post_url):
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        return conn.execute("SELECT id FROM organization_announcements WHERE source_id=? AND post_url=? ORDER BY id DESC LIMIT 1",(int(source_id),post_url)).fetchone()
    finally: conn.close()

def db_get_announcement(announcement_id):
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        return conn.execute("""SELECT a.*,o.name AS organization_name,o.region,s.platform
            FROM organization_announcements a JOIN monitored_organizations o ON o.id=a.organization_id
            JOIN monitored_sources s ON s.id=a.source_id WHERE a.id=? AND a.review_status<>'deleted' """,(int(announcement_id),)).fetchone()
    finally: conn.close()

def db_set_announcement_review(announcement_id,status,admin_id):
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        cur=conn.execute("UPDATE organization_announcements SET review_status=?,approved_by=?,approved_at=? WHERE id=? AND review_status='pending'",
            (status,int(admin_id),datetime.now(timezone.utc).isoformat() if status=='approved' else None,int(announcement_id)))
        conn.commit()
        return cur.rowcount == 1
    finally: conn.close()

async def announcement_review_callback(update,context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin/Super Admin ብቻ።",show_alert=True)
    try: action,raw_id=q.data.split("_",1); ann_id=int(raw_id)
    except Exception: return await q.answer("የማስታወቂያ መለያው ትክክል አይደለም።",show_alert=True)
    ann=db_get_announcement(ann_id)
    if not ann: return await q.edit_message_reply_markup(reply_markup=None)
    if action=="annreject":
        if not db_set_announcement_review(ann_id,"rejected",q.from_user.id):
            return await q.answer("ይህ ማስታወቂያ ቀድሞ ተመርምሯል።",show_alert=True)
        return await q.edit_message_reply_markup(reply_markup=None)
    if action!="annapprove": return
    if not db_set_announcement_review(ann_id,"approved",q.from_user.id):
        return await q.answer("ይህ ማስታወቂያ ቀድሞ ተመርምሯል።",show_alert=True)
    category=CATEGORY_AM.get(ann["category"],ann["category"])
    text=(f"🚛 TANA CARGO, LOGISTICS & INFORMATION SERVICES — የተጸደቀ የንግድ ዕድል / ለማጋራት የተዘጋጀ\n\n📂 ምድብ፦ {category}\n"
        f"🗂️ ዓይነት፦ {notice_kind_label(ann['post_text'])}\n🏢 አውጪ/ሻጭ፦ {ann['organization_name']}\n📍 ቦታ፦ {ann['region']}\n"
        f"📅 የታተመበት፦ {ann['post_date'] or 'አልተገለጸም'}\n\n"
        f"📝 ማጠቃለያ፦ {category} ጋር የተያያዘ አዲስ ዕድል ነው። የመዝጊያ ቀን፣ ዋጋ፣ የጨረታ ዋስትናና መስፈርቶችን በዋናው ማስታወቂያ ያረጋግጡ።\n\n"
        f"📅/💰 የቀን፣ የዋስትናና የክፍያ መረጃ፦\n" + ("\n".join("• "+x for x in extract_notice_key_lines(ann['post_text'])) or "በተሰበሰበው ጽሑፍ ውስጥ አልተገኘም።") +
        (f"\n\n🇪🇹 የአማርኛ ትርጉም፦\n{ann['translated_text'][:1800]}" if ann.get('translated_text') else "\n\nℹ️ ሙሉ ትርጉም ለማብራት GOOGLE_TRANSLATE_API_KEY ያስፈልጋል።") +
        f"\n\n📄 ዋናው ጽሑፍ፦\n{(ann['post_text'] or '')[:1200]}\n\n🔗 ዋና ምንጭ፦ {ann['post_url']}\n\n"
        "⚠️ ይህ ለደላላ ኔትወርክ ለማጋራት የተዘጋጀ ረቂቅ ነው። ራሱ ለሶስተኛ ወገኖች አይላክም።")
    await q.edit_message_reply_markup(reply_markup=None)
    await q.message.reply_text("✅ ተጸድቋል። ለደላላ ኔትወርክ የሚጋራ ረቂቅ ከታች ተዘጋጅቷል፤ ምንም መልዕክት ለሶስተኛ ወገን አልተላከም።")
    await q.message.reply_text(text,disable_web_page_preview=True)

async def daily_dealer_digest(context):
    """Daily admin review digest; nothing is sent to dealers until an admin approves it."""
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        rows=conn.execute("""SELECT a.id,a.category,a.title,a.post_text,a.translated_text,a.post_url,a.post_date,
            o.name AS organization_name,o.region,s.platform FROM organization_announcements a
            JOIN monitored_organizations o ON o.id=a.organization_id JOIN monitored_sources s ON s.id=a.source_id
            WHERE a.review_status='pending' AND a.digest_sent_at IS NULL ORDER BY a.created_at DESC LIMIT 80""").fetchall()
    finally: conn.close()
    counts={key:0 for key in ("vehicles","machinery","cargo","property","construction","goods","services","industrial_agri","auction")}
    for row in rows:
        if row["category"] in counts: counts[row["category"]]+=1
    summary="\n".join((f"• {CATEGORY_AM[k]}፦ {v} አዲስ የተገኙ" if v else f"• {CATEGORY_AM[k]}፦ አዲስ የተገኘ ማስታወቂያ የለም") for k,v in counts.items())
    delivered_to_admin=False
    for admin_id in get_admin_ids():
        try:
            await context.bot.send_message(chat_id=admin_id,text=(f"📊 የዕለቱ የTANA CARGO, LOGISTICS & INFORMATION SERVICES የገበያ ዕድሎች\n\nለAdmin ማጽደቅ የሚጠብቁ፦ {len(rows)}\n\n{summary}\n\n"
                "ማስታወሻ፦ ምንጮቹ በቦቱ ውስጥ የተመዘገቡ የሕዝብ/ፈቃድ ያላቸው ሊንኮች ናቸው፤ ዋናው ሰነድና የመዝጊያ ቀን በAdmin መረጋገጥ አለባቸው።"))
            for row in rows:
                cat=CATEGORY_AM.get(row['category'],row['category'])
                key_lines=extract_notice_key_lines(row['post_text'])
                msg=(f"📌 {cat}\n🗂️ ዓይነት፦ {notice_kind_label(row['post_text'])}\n🏢 {row['organization_name']}\n📍 {row['region']}\n"
                    f"📅 የታተመበት፦ {row['post_date'] or 'ቀን አልተገለጸም'}\n"
                    + ("⏰/💰 የመዝጊያ/ክፍያ/ዋስትና መረጃ፦\n"+"\n".join("• "+x for x in key_lines)+"\n\n" if key_lines else "⏰/💰 የመዝጊያና የዋስትና መረጃ ከዋናው ሰነድ ያረጋግጡ።\n\n")
                    + (f"🇪🇹 የአማርኛ ትርጉም፦\n{row['translated_text'][:1500]}\n\n" if row.get('translated_text') else "ℹ️ ሙሉ ትርጉም ለማብራት GOOGLE_TRANSLATE_API_KEY ያስፈልጋል።\n\n")
                    + f"📄 ዋና ጽሑፍ፦\n{(row['post_text'] or '')[:1200]}\n\n🔗 {row['post_url']}\n"
                    + "የመዝጊያ ቀንና የመጀመሪያ ሰነድ ማረጋገጥ አለበት።")
                kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ አጽድቅና ለማጋራት አዘጋጅ",callback_data=f"annapprove_{row['id']}"),InlineKeyboardButton("❌ ውድቅ",callback_data=f"annreject_{row['id']}")]])
                await context.bot.send_message(chat_id=admin_id,text=msg,reply_markup=kb,disable_web_page_preview=True)
            delivered_to_admin=True
        except Exception: logger.exception("Daily dealer digest admin delivery failed")
    if rows and delivered_to_admin:
        conn=_db_connect(DB_PATH,timeout=10)
        try:
            ids=[int(row["id"]) for row in rows]
            conn.execute("UPDATE organization_announcements SET digest_sent_at=? WHERE id=ANY(?)",
                (datetime.now(timezone.utc).isoformat(),ids))
            conn.commit()
        finally: conn.close()

async def admin_org_directory_callback(update,context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin ብቻ።",show_alert=True)
    rows=db_org_directory()
    if not rows: return await q.message.reply_text("ገና ድርጅት አልተመዘገበም። ➕ አዲስ ድርጅት ይጨምሩ።")
    grouped={}
    for r in rows: grouped.setdefault((r["region"],r["org_type"],r["name"]),[]).append(r)
    for (region,typ,name),items in grouped.items():
        lines=[f"🏢 {name}\n📍 {region} | {ORG_TYPE_LABELS.get(typ,typ)}"]
        buttons=[]
        for r in items:
            if not r["source_id"]: continue
            icon="🟢" if r["last_result"]=="new" else ("🔴" if r["last_result"]=="error" else "🟡")
            lines.append(f"{icon} {r['platform']} — {r['source_url']}")
            buttons.append([InlineKeyboardButton(f"🔗 {r['platform']} ክፈት",url=r["source_url"])])
        await q.message.reply_text("\n".join(lines),reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,disable_web_page_preview=True)

async def admin_org_add_start(update,context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin ብቻ።",show_alert=True)
    context.user_data["org_add_step"]="name"
    await q.message.reply_text("የድርጅቱን ሙሉ ስም ያስገቡ።")

async def admin_org_text_router(update,context):
    msg=update.message; uid=update.effective_user.id; value=(msg.text or "").strip()
    if not is_admin(uid): context.user_data.pop("org_add_step",None); return
    step=context.user_data.get("org_add_step")
    if step=="name":
        if len(value)<2: return await msg.reply_text("ትክክለኛ የድርጅት ስም ያስገቡ።")
        context.user_data["org_name"]=value; context.user_data["org_add_step"]="region"
        return await msg.reply_text("ክልል/ከተማ ያስገቡ። (ለምሳሌ፦ አማራ — ባህር ዳር፣ አዲስ አበባ)")
    if step=="region":
        context.user_data["org_region"]=value; context.user_data["org_add_step"]="type"
        return await msg.reply_text("የድርጅቱን ዘርፍ ያስገቡ። (ለምሳሌ፦ መንግስት፣ ዩኒቨርሲቲ፣ ፋብሪካ፣ ኮንስትራክሽን፣ ንግድ)")
    if step=="type":
        context.user_data["org_type"]=value; context.user_data["org_add_step"]="platform"
        return await msg.reply_text("የምንጩን መድረክ ያስገቡ፦ Telegram ወይም Facebook ወይም Website")
    if step=="platform":
        low=value.lower()
        if "telegram" in low or "ቴሌግራም" in low: platform="Telegram"
        elif "facebook" in low or "ፌስቡክ" in low: platform="Facebook"
        elif "website" in low or "web" in low or "ድረገጽ" in low: platform="Website"
        else: return await msg.reply_text("Telegram፣ Facebook ወይም Website ከሚሉት አንዱን ያስገቡ።")
        context.user_data["org_platform"]=platform; context.user_data["org_add_step"]="url"
        return await msg.reply_text("የዚያን ድርጅት የምንጭ ሊንክ ያስገቡ።")
    if step=="url":
        parsed=urlparse(value)
        if parsed.scheme not in ("http","https") or not parsed.netloc: return await msg.reply_text("ትክክለኛ http/https ሊንክ ያስገቡ።")
        org_id=db_org_upsert(context.user_data["org_name"],context.user_data["org_region"],context.user_data["org_type"],uid)
        db_source_add(org_id,context.user_data["org_platform"],value)
        for key in ("org_add_step","org_name","org_region","org_type","org_platform"): context.user_data.pop(key,None)
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("➕ ለዚህ ድርጅት ሌላ ምንጭ ጨምር",callback_data="org_add")],
            [InlineKeyboardButton("📚 የድርጅት መዝገብ አሳይ",callback_data="org_directory")]])
        return await msg.reply_text("✅ ድርጅቱና የምንጭ ሊንኩ ተመዝግበዋል። ሌሎች Facebook/Telegram/Website ሊንኮችንም በተመሳሳይ ስም በመጨመር በአንድ ድርጅት ስር ማስቀመጥ ይችላሉ።",reply_markup=kb)

def db_recent_org_announcements(limit=20):
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        return conn.execute("""SELECT a.id,a.category,a.post_text,a.translated_text,a.post_url,a.post_date,o.name AS organization_name,
            o.region,s.platform FROM organization_announcements a
            JOIN monitored_organizations o ON o.id=a.organization_id
            JOIN monitored_sources s ON s.id=a.source_id
            WHERE a.review_status='pending' ORDER BY a.id DESC LIMIT ?""",(int(limit),)).fetchall()
    finally: conn.close()

async def admin_media_import_callback(update,context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin only",show_alert=True)
    await q.message.reply_text("⏳ የተመዘገቡ ምንጮችን አሁን እየፈተሽኩ ነው...")
    await monitor_registered_sources(context)
    await daily_dealer_digest(context)
    await q.message.reply_text("✅ ፍተሻው ተጠናቋል። አዲስ የተገኙ ዕድሎች ለAdmin ማጽደቅ ቀርበዋል።")

async def admin_media_saved_callback(update,context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin only",show_alert=True)
    posts=db_recent_org_announcements(20)
    if not posts: return await q.message.reply_text("ገና አዲስ ማስታወቂያ አልተገኘም።")
    for post in posts:
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ አጽድቅና ለማጋራት አዘጋጅ",callback_data=f"annapprove_{post['id']}"),InlineKeyboardButton("❌ ውድቅ",callback_data=f"annreject_{post['id']}")]])
        translated_part = ("🇪🇹 የአማርኛ ትርጉም፦\n" + str(post.get('translated_text') or '')[:1200] + "\n\n") if post.get('translated_text') else ""
        message_text = (
            f"📌 {CATEGORY_AM.get(post['category'], post['category'])} — {post['organization_name']} / {post['region']}\n"
            f"📅 {post['post_date'] or 'ቀን አልተገለጸም'}\n\n"
            f"{translated_part}{(post['post_text'] or '')[:1200]}\n\n"
            f"🔗 {post['post_url']}\n"
            "የመዝጊያ ቀንና ክፍትነት ከዋናው ምንጭ ያረጋግጡ።"
        )
        await q.message.reply_text(message_text, reply_markup=kb, disable_web_page_preview=True)

# ==================================================
# ADMIN CONTENT CLEANUP — AUDITABLE SOFT DELETE
# ==================================================
CLEANUP_TABLES = {
    "organization_announcements": {"label": "የተሰበሰቡ የጨረታ/ንግድ መረጃዎች", "status_column": "review_status"},
    "media_posts": {"label": "የተሰበሰቡ ሚዲያ ፖስቶች", "status_column": "is_deleted"},
    "monitored_sources": {"label": "የተመዘገቡ የመረጃ ምንጮች", "status_column": "active"},
    "admin_media_links": {"label": "የማስታወቂያ መድረሻዎች", "status_column": "active"},
    "property_listings": {"label": "የንብረት ማስታወቂያዎች", "status_column": "active"},
}

def _cleanup_row(table, record_id):
    if table not in CLEANUP_TABLES:
        raise ValueError("Unsupported cleanup target")
    conn = _db_connect(DB_PATH, timeout=10)
    try:
        row = conn.execute(f'SELECT * FROM "{table}" WHERE id=?', (int(record_id),)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def db_soft_delete_content(table, record_id, reason, admin_id):
    if table not in CLEANUP_TABLES:
        raise ValueError("Unsupported cleanup target")
    row = _cleanup_row(table, record_id)
    if not row:
        return False
    if table == "organization_announcements" and row.get("review_status") == "deleted":
        return False
    if table == "media_posts" and int(row.get("is_deleted") or 0) == 1:
        return False
    if table in ("monitored_sources", "admin_media_links", "property_listings") and int(row.get("active") or 0) == 0:
        return False
    now = datetime.now(timezone.utc)
    conn = _db_connect(DB_PATH, timeout=10)
    try:
        conn.execute("""INSERT INTO content_deletion_archive
            (target_table,target_id,snapshot_json,reason,deleted_by,deleted_at,restore_until)
            VALUES (?,?,?,?,?,?,?)""",
            (table, int(record_id), json.dumps(row, ensure_ascii=False, default=str), str(reason)[:1000],
             int(admin_id), now.isoformat(), (now + timedelta(days=30)).isoformat()))
        if table == "organization_announcements":
            conn.execute("UPDATE organization_announcements SET review_status='deleted' WHERE id=?", (int(record_id),))
        elif table == "media_posts":
            conn.execute("UPDATE media_posts SET is_deleted=1, deleted_at=? WHERE id=?", (now.isoformat(), int(record_id)))
        else:
            conn.execute(f'UPDATE "{table}" SET active=0 WHERE id=?', (int(record_id),))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def db_restore_content(archive_id, admin_id):
    conn = _db_connect(DB_PATH, timeout=10)
    try:
        archive = conn.execute("SELECT * FROM content_deletion_archive WHERE id=? AND restored_at IS NULL AND permanently_deleted_at IS NULL", (int(archive_id),)).fetchone()
        if not archive:
            return False, "የተመረጠው የማገገሚያ መዝገብ አልተገኘም።"
        table = archive["target_table"]
        if table not in CLEANUP_TABLES:
            return False, "ይህ የመረጃ አይነት መመለስ አይቻልም።"
        snapshot = json.loads(archive["snapshot_json"] or "{}")
        rid = int(archive["target_id"])
        if table == "organization_announcements":
            conn.execute("UPDATE organization_announcements SET review_status=? WHERE id=?", (snapshot.get("review_status") or "pending", rid))
        elif table == "media_posts":
            conn.execute("UPDATE media_posts SET is_deleted=0, deleted_at=NULL WHERE id=?", (rid,))
        else:
            conn.execute(f'UPDATE "{table}" SET active=1 WHERE id=?', (rid,))
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE content_deletion_archive SET restored_at=?, restored_by=? WHERE id=?", (now, int(admin_id), int(archive_id)))
        conn.commit()
        return True, "መረጃው በተሳካ ሁኔታ ተመልሷል።"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

async def show_admin_cleanup_menu(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin/Super Admin ብቻ ይህን ምናሌ መጠቀም ይችላሉ።")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ የማይጠቅሙ መረጃዎችን ምረጥ", callback_data="cleanup_list")],
        [InlineKeyboardButton("♻️ የተሰረዙ መረጃዎችን መመለስ", callback_data="cleanup_restore_list")],
        [InlineKeyboardButton("📜 የመሰረዝ ታሪክ", callback_data="cleanup_history")],
    ])
    await update.effective_message.reply_text(
        "🗑️ የመረጃ ማጽዳትና ማስወገድ\n\n"
        "መረጃዎች Soft Delete ይደረጋሉ፤ ለ30 ቀናት ሊመለሱ ይችላሉ።\n"
        "Admin እና Super Admin ማስወገድ/መመለስ ይችላሉ። ቋሚ ማጥፋት በዚህ ስሪት አልተከፈተም።\n\n"
        "የደንበኛ፣ የሹፌር፣ የጭነት፣ የGPS እና የክፍያ ዋና መዝገቦች ከዚህ ምናሌ አይሰረዙም።",
        reply_markup=kb)

async def cleanup_callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not is_admin(uid):
        return await q.answer("Admin ብቻ።", show_alert=True)
    if q.data == "cleanup_list":
        buttons = [[InlineKeyboardButton(spec["label"], callback_data=f"cleanup_type_{table}")] for table, spec in CLEANUP_TABLES.items()]
        buttons.append([InlineKeyboardButton("⬅️ ተመለስ", callback_data="cleanup_home")])
        return await q.edit_message_text("የሚያጸዱትን የመረጃ አይነት ይምረጡ፦", reply_markup=InlineKeyboardMarkup(buttons))
    if q.data.startswith("cleanup_type_"):
        table = q.data.replace("cleanup_type_", "", 1)
        if table not in CLEANUP_TABLES:
            return
        conn = _db_connect(DB_PATH, timeout=10)
        try:
            if table == "organization_announcements":
                rows = conn.execute("SELECT id,title,post_text FROM organization_announcements WHERE review_status<>'deleted' ORDER BY id DESC LIMIT 15").fetchall()
            elif table == "media_posts":
                rows = conn.execute("SELECT id,source_name,post_text FROM media_posts WHERE COALESCE(is_deleted,0)=0 ORDER BY id DESC LIMIT 15").fetchall()
            elif table == "monitored_sources":
                rows = conn.execute("SELECT id,platform,source_url FROM monitored_sources WHERE active=1 ORDER BY id DESC LIMIT 15").fetchall()
            elif table == "property_listings":
                rows = conn.execute("SELECT id,property_type,location FROM property_listings WHERE active=1 ORDER BY id DESC LIMIT 15").fetchall()
            else:
                rows = conn.execute("SELECT id,platform,organization_name,media_url FROM admin_media_links WHERE active=1 ORDER BY id DESC LIMIT 15").fetchall()
        finally:
            conn.close()
        if not rows:
            return await q.edit_message_text("በዚህ ምድብ ሊጸዳ የሚችል መረጃ የለም።")
        buttons = []
        for row in rows:
            row = dict(row)
            label = str(row.get("title") or row.get("source_name") or row.get("organization_name") or row.get("location") or row.get("platform") or row.get("source_url") or "መረጃ")[:45]
            buttons.append([InlineKeyboardButton(f"🗑️ #{row['id']} — {label}", callback_data=f"cleanup_pick_{table}_{row['id']}")])
        buttons.append([InlineKeyboardButton("⬅️ ተመለስ", callback_data="cleanup_list")])
        return await q.edit_message_text("ለማስወገድ መዝገቡን ይምረጡ፦", reply_markup=InlineKeyboardMarkup(buttons))
    if q.data.startswith("cleanup_pick_"):
        parts = q.data.split("_")
        table, rid = "_".join(parts[2:-1]), int(parts[-1])
        if table not in CLEANUP_TABLES:
            return
        row = _cleanup_row(table, rid)
        if not row:
            return await q.edit_message_text("መዝገቡ አልተገኘም።")
        context.user_data["cleanup_pending"] = {"table": table, "id": rid}
        preview = str(row.get("title") or row.get("post_text") or row.get("source_url") or row.get("media_url") or row.get("organization_name") or row.get("location") or "")[:600]
        return await q.message.reply_text(f"🔍 የመረጃ ማረጋገጫ\nዓይነት፦ {CLEANUP_TABLES[table]['label']}\nመዝገብ፦ #{rid}\n\n{preview}\n\nለምን ማስወገድ እንደፈለጉ ምክንያቱን ይጻፉ።")
    if q.data == "cleanup_restore_list":
        conn = _db_connect(DB_PATH, timeout=10)
        try:
            rows = conn.execute("SELECT id,target_table,target_id FROM content_deletion_archive WHERE restored_at IS NULL AND permanently_deleted_at IS NULL ORDER BY id DESC LIMIT 20").fetchall()
        finally:
            conn.close()
        if not rows:
            return await q.edit_message_text("የተመለስ መረጃ የለም።")
        buttons = []
        for row in rows:
            label = CLEANUP_TABLES.get(row["target_table"], {}).get("label", row["target_table"])
            buttons.append([InlineKeyboardButton(f"♻️ #{row['id']} {label} — መዝገብ {row['target_id']}", callback_data=f"cleanup_restore_{row['id']}")])
        return await q.edit_message_text("ለመመለስ የተሰረዘውን ይምረጡ፦", reply_markup=InlineKeyboardMarkup(buttons))
    if q.data.startswith("cleanup_restore_"):
        ok, msg = db_restore_content(int(q.data.rsplit("_", 1)[1]), uid)
        return await q.edit_message_text(("✅ " if ok else "⚠️ ") + msg)
    if q.data == "cleanup_history":
        conn = _db_connect(DB_PATH, timeout=10)
        try:
            rows = conn.execute("SELECT target_table,target_id,reason,deleted_by,deleted_at,restored_at FROM content_deletion_archive ORDER BY id DESC LIMIT 15").fetchall()
        finally:
            conn.close()
        if not rows:
            return await q.edit_message_text("የመሰረዝ ታሪክ ገና የለም።")
        lines = []
        for row in rows:
            label = CLEANUP_TABLES.get(row["target_table"], {}).get("label", row["target_table"])
            status = "ተመልሷል" if row["restored_at"] else "ተወግዷል"
            lines.append(f"• #{row['target_id']} {label}\n  በ፦ {row['deleted_by']} | {row['deleted_at']}\n  ምክንያት፦ {row['reason']}\n  {status}")
        return await q.edit_message_text("📜 የመሰረዝ ታሪክ\n\n" + "\n\n".join(lines))
    if q.data == "cleanup_home":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ የማይጠቅሙ መረጃዎችን ምረጥ", callback_data="cleanup_list")],
            [InlineKeyboardButton("♻️ የተሰረዙ መረጃዎችን መመለስ", callback_data="cleanup_restore_list")],
            [InlineKeyboardButton("📜 የመሰረዝ ታሪክ", callback_data="cleanup_history")],
        ])
        return await q.edit_message_text("🗑️ የመረጃ ማጽዳትና ማስወገድ", reply_markup=kb)

async def show_admin_media_menu(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ ይህን ምናሌ Admin/Super Admin ብቻ ይጠቀማሉ።")
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📘 Facebook ምንጮች", callback_data="media_platform_facebook")],
        [InlineKeyboardButton("✈️ Telegram ምንጮች", callback_data="media_platform_telegram")],
        [InlineKeyboardButton("🌐 Website ምንጮች", callback_data="media_platform_website")],
        [InlineKeyboardButton("➕ ድርጅትና ምንጭ ጨምር", callback_data="org_add")],
        [InlineKeyboardButton("📚 የተመዘገቡ ድርጅቶች/ምንጮች", callback_data="org_directory")],
        [InlineKeyboardButton("🔎 ምንጮችን አሁን ፈትሽ", callback_data="media_import")],
        [InlineKeyboardButton("🕒 የተገኙ አዲስ ዕድሎች / የAdmin ማጽደቅ", callback_data="media_saved")],
        [InlineKeyboardButton("📢 TANA CARGO, LOGISTICS & INFORMATION SERVICES Post / Share አዘጋጅ", callback_data="media_compose")]
    ])
    await update.message.reply_text("📡 TANA — የሚዲያ ምንጭ ክትትልና ውጫዊ ማጋራት\n\nFacebook፣ Telegram እና Website ላይ የሚገኙ የጨረታ፣ የግዥ፣ የሽያጭ፣ የኪራይና ሌሎች የንግድ ዕድሎች ይሰበሰባሉ። ምንጮች በየ60 ደቂቃው ይፈተሻሉ፤ ለደላሎች የሚላኩት በAdmin/Super Admin ከጸደቁ በኋላ ብቻ ነው።\n\n📢 Post / Share የTANA CARGO, LOGISTICS & INFORMATION SERVICES የራሱ የማስታወቂያ ዝግጅት ሆኖ የተለየ ነው።", reply_markup=kb)

async def admin_media_platform_callback(update, context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin ብቻ።", show_alert=True)
    if q.data == "media_home":
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("TELEGRAM",callback_data="media_platform_telegram"),InlineKeyboardButton("FACEBOOK",callback_data="media_platform_facebook")],[InlineKeyboardButton("WEBSITE",callback_data="media_platform_website")],[InlineKeyboardButton("📥 ፖስቶችን ፈልግና አስቀምጥ",callback_data="media_import")],[InlineKeyboardButton("📚 የተሰበሰቡ ፖስቶችን አሳይ",callback_data="media_saved")],[InlineKeyboardButton("📢 Post / Share መልዕክት አዘጋጅ",callback_data="media_compose")]])
        return await q.edit_message_text("📡 TANA — የሚዲያ ምንጭ ምድቦች",reply_markup=kb)
    platform=q.data.rsplit("_",1)[-1]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ አዲስ Channel/Page/Group ጨምር", callback_data=f"media_add_{platform}")],
        [InlineKeyboardButton("📋 የተመዘገቡትን አሳይ", callback_data=f"media_list_{platform}")],
        [InlineKeyboardButton("⬅️ ተመለስ", callback_data="media_home")]
    ])
    await q.edit_message_text(f"{platform.upper()} — የሚዲያ መረጃ", reply_markup=kb)

async def admin_media_add_start(update, context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin ብቻ።", show_alert=True)
    platform=q.data.rsplit("_",1)[-1]
    context.user_data["media_add_platform"]=platform
    types = ([InlineKeyboardButton("Telegram Channel", callback_data="media_type_tg_channel"), InlineKeyboardButton("Telegram Group", callback_data="media_type_tg_group")] if platform=="telegram" else ([InlineKeyboardButton("Facebook Page", callback_data="media_type_fb_page"), InlineKeyboardButton("Facebook Group", callback_data="media_type_fb_group")] if platform=="facebook" else [InlineKeyboardButton("Website", callback_data="media_type_web_site")]))
    await q.edit_message_text("የሚዲያውን ዓይነት ይምረጡ።", reply_markup=InlineKeyboardMarkup([types]))

async def admin_media_type_callback(update, context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin ብቻ።", show_alert=True)
    kind=q.data.replace("media_type_","")
    if kind not in MEDIA_TYPES: return
    context.user_data["media_add_type"]=kind
    context.user_data["media_add_step"]="name"
    await q.message.reply_text("የድርጅቱን/Channel/Page ስም ያስገቡ።")

async def admin_media_list_callback(update, context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin ብቻ።", show_alert=True)
    platform=q.data.rsplit("_",1)[-1]
    rows=db_media_list(platform)
    if not rows: return await q.message.reply_text("የተመዘገበ ሚዲያ ገና የለም።")
    for row in rows:
        label=f"{row['organization_name']} — {row['platform']} {row['media_type']}\n{row['media_url']}"
        await q.message.reply_text(label, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Open Link", url=row['media_url'])]]))

async def admin_media_compose_callback(update, context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin ብቻ።", show_alert=True)
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("📝 ያለ ፎቶ",callback_data="media_compose_text"),
        InlineKeyboardButton("🖼️ ከፎቶ ጋር",callback_data="media_compose_photo")]])
    await q.message.reply_text("የTANA CARGO, LOGISTICS & INFORMATION SERVICES Post / Share ዓይነት ይምረጡ፦",reply_markup=kb)

async def admin_media_compose_mode_callback(update,context):
    q=update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): return await q.answer("Admin ብቻ።",show_alert=True)
    context.user_data.pop("media_compose_waiting",None)
    context.user_data.pop("media_compose_photo_waiting",None)
    if q.data=="media_compose_text":
        context.user_data["media_compose_waiting"]=True
        return await q.message.reply_text("ለPost/Share የሚፈልጉትን ጽሑፍ ይላኩ። የTANA CARGO, LOGISTICS & INFORMATION SERVICES ስልክና መገለጫ በመጨረሻ ይጨመራሉ።")
    context.user_data["media_compose_photo_waiting"]=True
    await q.message.reply_text("🖼️ ለማስታወቂያው የሚፈልጉትን ፎቶ ከcaption ጋር ይላኩ።")

async def admin_media_text_router(update, context):
    msg=update.message; uid=update.effective_user.id; value=(msg.text or "").strip()
    if not is_admin(uid):
        context.user_data.pop("media_add_step",None); context.user_data.pop("media_compose_waiting",None); return
    step=context.user_data.get("media_add_step")
    if step=="name":
        if len(value)<2: return await msg.reply_text("እባክዎ ትክክለኛ ስም ያስገቡ።")
        context.user_data["media_add_name"]=value; context.user_data["media_add_step"]="url"
        return await msg.reply_text("የTelegram/Facebook ሊንኩን ያስገቡ (https:// ወይም https://t.me/...)።")
    if step=="url":
        from urllib.parse import urlparse
        parsed=urlparse(value)
        if parsed.scheme not in ("http","https") or not parsed.netloc:
            return await msg.reply_text("❌ ትክክለኛ የhttp/https ሊንክ ያስገቡ።")
        kind=context.user_data.get("media_add_type"); platform, media_type=MEDIA_TYPES[kind]
        db_media_add(platform,media_type,context.user_data.pop("media_add_name"),value,uid)
        context.user_data.pop("media_add_step",None); context.user_data.pop("media_add_type",None); context.user_data.pop("media_add_platform",None)
        return await msg.reply_text("✅ የሚዲያ መረጃው በዳታቤዝ ተመዝግቧል።", reply_markup=main_menu(uid))
    if context.user_data.get("media_compose_waiting"):
        context.user_data.pop("media_compose_waiting",None)
        branded=value+f"\n\n🚛 TANA CARGO, LOGISTICS & INFORMATION SERVICES | ጣና ጭነት\n📞 {SUPPORT_PHONE}\n🤖 https://t.me/{BOT_USERNAME}\n📢 https://t.me/{CHANNEL_USERNAME}"
        rows=db_media_list()
        await msg.reply_text("📋 የተዘጋጀው መልዕክት፦ (በስልክዎ ላይ ጽሑፉን ተጭነው Copy ያድርጉ)\n\n"+branded)
        for row in rows:
            await msg.reply_text(f"{row['organization_name']} ({row['platform']} {row['media_type']})", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Open Link",url=row['media_url'])]]))
        return

# ==================================================
# TENDER INFORMATION SUBSCRIPTION
# ==================================================
async def tender_subscribe_start(update, context):
    context.user_data["tender_subscriber"] = {}
    await update.message.reply_text("📢 የጨረታ መረጃ ማሳወቂያ ምዝገባ\n\n1️⃣ ሙሉ ስምዎን ያስገቡ።")
    return TENDER_NAME

async def tender_name_input(update, context):
    value=(update.message.text or "").strip()
    if len(value)<2:
        await update.message.reply_text("እባክዎ ሙሉ ስምዎን ያስገቡ።"); return TENDER_NAME
    context.user_data["tender_subscriber"]["name"]=value
    await update.message.reply_text("2️⃣ ስልክ ቁጥርዎን ያስገቡ።")
    return TENDER_PHONE

async def tender_phone_input(update, context):
    value=(update.message.text or "").strip()
    valid,error=validate_phone(value)
    if not valid:
        await update.message.reply_text(f"❌ {error}\nምሳሌ፦ 0912345678"); return TENDER_PHONE
    context.user_data["tender_subscriber"]["phone"]=value
    await update.message.reply_text("3️⃣ ክልል፣ ከተማ እና አድራሻዎን ያስገቡ።")
    return TENDER_ADDRESS

async def tender_address_input(update, context):
    value=(update.message.text or "").strip()
    if len(value)<2:
        await update.message.reply_text("አድራሻዎን ያስገቡ።"); return TENDER_ADDRESS
    context.user_data["tender_subscriber"]["address"]=value
    kb=ReplyKeyboardMarkup([["የግዢ ጨረታ"],["የሽያጭ ጨረታ"],["የግንባታ ጨረታ"],["ሁሉም መረጃ"],["ቀጣይ"],["❌ ሰርዝ"]],resize_keyboard=True)
    context.user_data["tender_subscriber"]["categories"]=[]
    await update.message.reply_text("4️⃣ ማግኘት የሚፈልጉትን የጨረታ መረጃ ይምረጡ። ከአንድ በላይ መምረጥ ይችላሉ።",reply_markup=kb)
    return TENDER_TYPES

async def tender_types_input(update, context):
    value=(update.message.text or "").strip()
    if value=="❌ ሰርዝ":
        context.user_data.pop("tender_subscriber",None)
        await update.message.reply_text("ምዝገባው ተሰርዟል።",reply_markup=main_menu(update.effective_user.id)); return ConversationHandler.END
    data=context.user_data["tender_subscriber"]
    if value=="ቀጣይ":
        if not data.get("categories"):
            await update.message.reply_text("ቢያንስ አንድ የመረጃ አይነት ይምረጡ።"); return TENDER_TYPES
        user=update.effective_user
        conn=_db_connect(DB_PATH)
        try:
            conn.execute("""INSERT INTO tender_subscribers(user_id,full_name,telegram_name,phone,address,categories,active,created_at)
                VALUES(?,?,?,?,?,?,1,?) ON CONFLICT(user_id) DO UPDATE SET full_name=excluded.full_name,telegram_name=excluded.telegram_name,phone=excluded.phone,address=excluded.address,categories=excluded.categories,active=1,created_at=excluded.created_at""",
                (user.id,data["name"],user.username or "",data["phone"],data["address"],json.dumps(data["categories"],ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
            conn.commit()
        finally: conn.close()
        await update.message.reply_text("✅ የጨረታ መረጃ ማሳወቂያ ምዝገባዎ ተሳክቷል። በመረጡት ዓይነት መሠረት መረጃ ይደርስዎታል።\nለበለጠ መረጃ @tanapage Join ያድርጉ።",reply_markup=main_menu(user.id))
        context.user_data.pop("tender_subscriber",None)
        return ConversationHandler.END
    allowed={"የግዢ ጨረታ","የሽያጭ ጨረታ","የግንባታ ጨረታ","ሁሉም መረጃ"}
    if value not in allowed:
        await update.message.reply_text("ከምናሌው ውስጥ ይምረጡ።"); return TENDER_TYPES
    selected=data["categories"]
    if value=="ሁሉም መረጃ": selected[:]=["ሁሉም መረጃ"]
    else:
        if "ሁሉም መረጃ" in selected: selected.remove("ሁሉም መረጃ")
        if value in selected: selected.remove(value)
        else: selected.append(value)
    await update.message.reply_text("የተመረጡት፦ " + (", ".join(selected) or "ምንም የለም") + "\nሌላ ይምረጡ ወይም ‘ቀጣይ’ ይጫኑ።")
    return TENDER_TYPES

async def tender_broadcast_start(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("❌ Admin/Super Admin ብቻ ይህን ሥራ ማከናወን ይችላሉ።")
        return ConversationHandler.END
    kb = ReplyKeyboardMarkup([["የግዢ ጨረታ"], ["የሽያጭ ጨረታ"], ["የግንባታ ጨረታ"], ["ሁሉም መረጃ"], ["❌ ሰርዝ"]], resize_keyboard=True)
    await update.effective_message.reply_text("📢 መረጃውን ለየትኛው የተመዝጋቢ ቡድን መላክ ይፈልጋሉ?", reply_markup=kb)
    return TENDER_BROADCAST_CATEGORY

async def tender_broadcast_category(update, context):
    category=(update.message.text or "").strip()
    if category == "❌ ሰርዝ":
        await update.message.reply_text("የጨረታ ማሰራጫው ተሰርዟል።", reply_markup=main_menu(update.effective_user.id))
        return ConversationHandler.END
    allowed={"የግዢ ጨረታ", "የሽያጭ ጨረታ", "የግንባታ ጨረታ", "ሁሉም መረጃ"}
    if category not in allowed:
        await update.message.reply_text("ከምናሌው ውስጥ ይምረጡ።")
        return TENDER_BROADCAST_CATEGORY
    context.user_data["tender_broadcast"]={"category":category}
    await update.message.reply_text("📝 የሚላከውን መረጃ በጽሑፍ ይላኩ፤ ፎቶ ካለ ከጽሑፉ ጋር ፎቶ መላክ ይችላሉ።", reply_markup=ReplyKeyboardRemove())
    return TENDER_BROADCAST_CONTENT

async def tender_broadcast_content(update, context):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    data=context.user_data.get("tender_broadcast") or {}
    if update.message.photo:
        data["photo_file_id"]=update.message.photo[-1].file_id
        data["text"]=(update.message.caption or "").strip()
    else:
        data["photo_file_id"]=""
        data["text"]=(update.message.text or "").strip()
    if not data.get("text") and not data.get("photo_file_id"):
        await update.message.reply_text("እባክዎ ጽሑፍ ወይም ፎቶ ያለው መልዕክት ይላኩ።")
        return TENDER_BROADCAST_CONTENT
    context.user_data["tender_broadcast"]=data
    category=data["category"]
    preview="📢 የጨረታ መረጃ ቅድመ እይታ\n\n"+f"ተቀባዮች፦ {category}\n\n"+(data.get("text") or "(ፎቶ)")
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ አረጋግጥና ላክ",callback_data="tender_send_confirm"), InlineKeyboardButton("❌ ሰርዝ",callback_data="tender_send_cancel")]])
    if data.get("photo_file_id"):
        await update.message.reply_photo(photo=data["photo_file_id"],caption=preview,reply_markup=kb)
    else:
        await update.message.reply_text(preview,reply_markup=kb)
    return ConversationHandler.END

async def tender_broadcast_callback(update, context):
    q=update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.answer("Admin ብቻ።",show_alert=True); return
    if q.data=="tender_send_cancel":
        context.user_data.pop("tender_broadcast",None)
        await q.edit_message_caption(caption="የጨረታ ማሰራጫው ተሰርዟል።") if q.message.photo else await q.edit_message_text("የጨረታ ማሰራጫው ተሰርዟል።")
        return
    data=context.user_data.get("tender_broadcast")
    if not data:
        await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text("የማሰራጫ መረጃው አልተገኘም። እባክዎ እንደገና ይጀምሩ።")
        return
    category=data["category"]
    conn=_db_connect(DB_PATH,timeout=15)
    try:
        rows=conn.execute("SELECT user_id,categories FROM tender_subscribers WHERE active=1 ORDER BY id").fetchall()
    finally:
        conn.close()
    sent=failed=0
    for row in rows:
        try:
            cats=json.loads(row["categories"] or "[]")
            if category!="ሁሉም መረጃ" and "ሁሉም መረጃ" not in cats and category not in cats:
                continue
            if data.get("photo_file_id"):
                await context.bot.send_photo(chat_id=row["user_id"],photo=data["photo_file_id"],caption=data.get("text") or None)
            else:
                await context.bot.send_message(chat_id=row["user_id"],text=data["text"])
            sent+=1
        except Exception as exc:
            failed+=1
            logger.warning("Tender broadcast failed for user %s: %s",row["user_id"],exc)
    log_conn=_db_connect(DB_PATH,timeout=10)
    try:
        log_conn.execute("INSERT INTO tender_broadcast_log(admin_user_id,category,message_text,photo_file_id,sent_count,failed_count,created_at) VALUES(?,?,?,?,?,?,?)",(q.from_user.id,category,data.get("text", ""),data.get("photo_file_id", ""),sent,failed,datetime.now(timezone.utc).isoformat()))
        log_conn.commit()
    finally:
        log_conn.close()
    context.user_data.pop("tender_broadcast",None)
    try:
        await q.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    await q.message.reply_text(f"✅ የጨረታ መረጃ ማሰራጨት ተጠናቋል።\n📨 ተልኳል፦ {sent}\n⚠️ አልደረሰም፦ {failed}",reply_markup=main_menu(q.from_user.id))

# ==================================================
# NEW INFORMATION SUBSCRIPTIONS + PROPERTY CONNECTOR
# ==================================================
INFO_CATEGORIES = {
    "🛒 የግዢና የሽያጭ ጨረታ መረጃ ለማግኘት 👈 ተጭነው ይመዝገቡ": "purchase_sales",
    "🏗️ የግንባታ ጨረታ መረጃ ለማግኘት 👈 ተጭነው ይመዝገቡ": "construction",
    "💼 የሥራ ፍለጋ መረጃ ለማግኘት 👈 ተጭነው ይመዝገቡ": "jobs",
    "🤝 የድለላ መረጃዎችን ለማግኘት 👈 ተጭነው ይመዝገቡ": "broker_info",
}

async def info_subscription_start(update, context, category):
    context.user_data['info_subscription'] = {'category': category}
    phone = get_registered_phone(update.effective_user.id)
    if not phone:
        await update.effective_message.reply_text('⚠️ በ /start የተመዘገበ ስልክ አልተገኘም። መጀመሪያ /start ያድርጉ።')
        return ConversationHandler.END
    await update.effective_message.reply_text(f'📞 የተመዘገበ ስልክዎ፦ {phone}\n\nሙሉ ስምዎን ይጻፉ።', reply_markup=ReplyKeyboardRemove())
    return INFO_SUB_NAME

async def info_subscription_name(update, context):
    name=(update.message.text or '').strip()
    if len(name)<2:
        await update.message.reply_text('እባክዎ ሙሉ ስምዎን ያስገቡ።'); return INFO_SUB_NAME
    uid=update.effective_user.id; category=context.user_data['info_subscription']['category']; phone=get_registered_phone(uid)
    conn=_db_connect(DB_PATH, timeout=10)
    try:
        conn.execute("""INSERT INTO info_category_subscriptions(user_id,category,full_name,phone,telegram_name,active,created_at)
        VALUES(?,?,?,?,?,1,?) ON CONFLICT(user_id,category) DO UPDATE SET full_name=EXCLUDED.full_name,phone=EXCLUDED.phone,telegram_name=EXCLUDED.telegram_name,active=1,created_at=EXCLUDED.created_at""",
        (uid,category,name,phone,update.effective_user.username or '',datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally: conn.close()
    context.user_data.pop('info_subscription',None)
    await update.message.reply_text('✅ ምዝገባዎ ተጠናቋል። ከዚህ ዘርፍ ጠቃሚ መረጃዎችን ወደ Telegram አካውንትዎ እንልክልዎታለን።',reply_markup=main_menu(uid))
    return ConversationHandler.END

PROPERTY_HUB_CATEGORIES={
 '🏡 መኖሪያ ቤት':'residential_house', '🏢 አፓርታማ/ኮንዶሚኒየም':'apartment',
 '🏬 የንግድ ሱቅ':'shop', '🏭 የድርጅት/የንግድ ቦታ':'business_premises'}
async def property_hub_start(update,context):
    kb=ReplyKeyboardMarkup([[k] for k in PROPERTY_HUB_CATEGORIES]+[['⬅️ ወደ መጀመሪያ ገፅ']],resize_keyboard=True)
    await update.message.reply_text('የሚመዘግቡትን የንብረት አይነት ይምረጡ።',reply_markup=kb)
    return PROPERTY_HUB_KIND
async def property_hub_kind(update,context):
    kind=(update.message.text or '').strip()
    if kind not in PROPERTY_HUB_CATEGORIES:
        await update.message.reply_text('ከተሰጡት የንብረት አይነቶች ይምረጡ።'); return PROPERTY_HUB_KIND
    context.user_data['property_hub']={'category':PROPERTY_HUB_CATEGORIES[kind],'category_label':kind}
    await update.message.reply_text('ሻጭ ነዎት ወይስ ገዢ?',reply_markup=ReplyKeyboardMarkup([['🟢 ሻጭ','🔵 ገዢ'],['⬅️ ወደ መጀመሪያ ገፅ']],resize_keyboard=True))
    return PROPERTY_HUB_MODE
async def property_hub_mode(update,context):
    mode=(update.message.text or '').strip()
    if mode not in ('🟢 ሻጭ','🔵 ገዢ'):
        await update.message.reply_text('ሻጭ ወይም ገዢ ይምረጡ።'); return PROPERTY_HUB_MODE
    d=context.user_data['property_hub']; d['mode']='seller' if mode=='🟢 ሻጭ' else 'buyer'
    phone=get_registered_phone(update.effective_user.id)
    if not phone:
        await update.message.reply_text('⚠️ የተመዘገበ ስልክ አልተገኘም። /start ያድርጉ።'); return ConversationHandler.END
    d['phone']=phone
    await update.message.reply_text(f'📞 የተመዘገበ ስልክዎ፦ {phone}\nሙሉ ስምዎን ይጻፉ።',reply_markup=ReplyKeyboardRemove())
    return PROPERTY_HUB_NAME
async def property_hub_name(update,context):
    name=(update.message.text or '').strip()
    if len(name)<2:
        await update.message.reply_text('ሙሉ ስምዎን ያስገቡ።'); return PROPERTY_HUB_NAME
    d=context.user_data['property_hub']; d['full_name']=name
    if d['mode']=='buyer':
        return await property_hub_save(update,context)
    d['fields']=['አድራሻ/ቦታ','ስፋት (በካሬ ሜትር)' if d['category']!='apartment' else 'የቤቱ አቀማመጥ (Ground/G+1/G+2)']
    if d['category']=='apartment': d['fields']+=['የመኝታ ክፍሎች ብዛት']
    elif d['category']=='residential_house': d['fields']+=['የቤት አይነት (Villa/G+2/G+1 ወዘተ፤ Villa ከሆነ ቡልኬት ወይም የጭቃ ቤት መሆኑን ይግለጹ)']
    elif d['category']=='business_premises': d['fields']+=['የህንፃ አይነት/ዝርዝር (ከሌለ የለውም)']
    d['fields']+=['ዋጋ (በብር)']
    d['field_index']=0
    await update.message.reply_text('እባክዎ '+d['fields'][0]+' ያስገቡ።')
    return PROPERTY_HUB_FIELD
async def property_hub_field(update,context):
    val=(update.message.text or '').strip()
    if not val:
        await update.message.reply_text('እባክዎ መረጃውን ያስገቡ።'); return PROPERTY_HUB_FIELD
    d=context.user_data['property_hub']; i=d['field_index']; d.setdefault('details',[]).append(d['fields'][i]+': '+val); i+=1; d['field_index']=i
    if i<len(d['fields']):
        await update.message.reply_text('እባክዎ '+d['fields'][i]+' ያስገቡ።'); return PROPERTY_HUB_FIELD
    d['photos']=[]
    await update.message.reply_text('📷 የንብረቱን ፎቶዎች ይላኩ። ፎቶ ከሌለዎት “የለኝም” ብለው ይጻፉ።')
    return PROPERTY_HUB_PHOTOS
async def property_hub_photos(update,context):
    d=context.user_data['property_hub']
    if update.message.photo:
        if len(d.setdefault('photos',[]))>=8:
            await update.message.reply_text('እስከ 8 ፎቶ ብቻ መላክ ይችላሉ።'); return PROPERTY_HUB_PHOTOS
        d['photos'].append(update.message.photo[-1].file_id)
        await update.message.reply_text(f'✅ ፎቶ {len(d["photos"])} ተቀብለናል። ሌላ ፎቶ ይላኩ ወይም “ፎቶ ጨርሻለሁ” ብለው ይጻፉ።')
        return PROPERTY_HUB_PHOTOS
    text=(update.message.text or '').strip()
    if text not in ('የለኝም','ፎቶ ጨርሻለሁ'):
        await update.message.reply_text('ፎቶ ይላኩ ወይም “የለኝም” / “ፎቶ ጨርሻለሁ” ብለው ይጻፉ።'); return PROPERTY_HUB_PHOTOS
    return await property_hub_save(update,context)
async def property_hub_save(update,context):
    d=context.user_data['property_hub']; uid=update.effective_user.id
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        cur=conn.execute('INSERT INTO property_connector_listings(user_id,category,mode,full_name,phone,details,photo_ids,created_at) VALUES(?,?,?,?,?,?,?,?) RETURNING id',
        (uid,d['category'],d['mode'],d['full_name'],d['phone'],'; '.join(d.get('details',[])),json.dumps(d.get('photos',[])),datetime.now(timezone.utc).isoformat()))
        row=cur.fetchone(); rid=row['id'] if row else 0; conn.commit()
    finally: conn.close()
    for aid in get_admin_ids():
        try:
            await context.bot.send_message(chat_id=aid,text=f'🏠 አዲስ የንብረት {d["mode"]} ምዝገባ #{rid}\nአይነት፦ {d["category_label"]}\nስም፦ {d["full_name"]}\nስልክ፦ {d["phone"]}\nዝርዝር፦ {"; ".join(d.get("details",[])) or "ገዢ ምዝገባ"}\nTelegram ID፦ {uid}')
            for fid in d.get('photos',[]):
                try: await context.bot.send_photo(chat_id=aid,photo=fid,caption=f'የንብረት ምዝገባ #{rid} ፎቶ')
                except Exception: pass
        except Exception as e: print('PROPERTY HUB ADMIN NOTIFY:',e)
    context.user_data.pop('property_hub',None)
    await update.effective_message.reply_text('✅ ምዝገባዎ ተጠናቋል። መረጃዎን ተቀብለናል፤ ተስማሚ ገዢ/ሻጭ ሲገኝ በተመዘገበው ስልክዎ እናገኝዎታለን።\nለበለጠ መረጃ @tanapage Join ያድርጉ።',reply_markup=main_menu(uid))
    return ConversationHandler.END

async def show_property_connector_registry(update, context):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text('Admin/Super Admin ብቻ ይህን መዝገብ ማየት ይችላሉ።'); return
    conn=_db_connect(DB_PATH,timeout=10)
    try:
        rows=conn.execute('SELECT id,category,mode,full_name,phone,details,photo_ids,created_at,user_id FROM property_connector_listings WHERE active=1 ORDER BY id DESC LIMIT 40').fetchall()
    finally: conn.close()
    if not rows:
        await update.effective_message.reply_text('🏠 ገና የንብረት ሻጭ/ገዢ ምዝገባ የለም።'); return
    labels={'residential_house':'መኖሪያ ቤት','apartment':'አፓርታማ/ኮንዶሚኒየም','shop':'የንግድ ሱቅ','business_premises':'የድርጅት/የንግድ ቦታ'}
    for r in rows:
        photos=json.loads(r['photo_ids'] or '[]')
        msg=f'🏠 መዝገብ #{r["id"]}\nዓይነት፦ {labels.get(r["category"],r["category"])}\nሚና፦ {"ሻጭ" if r["mode"]=="seller" else "ገዢ"}\nስም፦ {r["full_name"]}\nስልክ፦ {r["phone"]}\nዝርዝር፦ {r["details"] or "—"}\nTelegram ID፦ {r["user_id"]}\nቀን፦ {r["created_at"]}'
        await update.effective_message.reply_text(msg)
        for fid in photos:
            try: await update.effective_message.reply_photo(photo=fid,caption=f'የንብረት #{r["id"]} ፎቶ')
            except Exception: pass

# ==================================================
# MENU ROUTER & INTERRUPT
# ==================================================

MASTER_MENU_REGEX = (
    r"^(🚚 ጭነት መለጠፍ|🔎 ጭነት መፈለግ|🚛 የጭነት መኪና ማስመዝገብ|🚛 የጭነት መኪና መፈለግ|"
    r"🚐 የኪራይ መኪና ማስመዝገብ|🔎 የኪራይ መኪና መፈለግ|🚘 የሽያጭ መኪና ማስመዝገብ|🔎 የሽያጭ መኪና መፈለግ|🚜 የኮንስትራክሽን ማሽነሪ ሽያጭ/ኪራይ|🔎 ማሽነሪ ፈልግ|"
    r"👤 የኔ መረጃ|GPS\n🚛 የጭነት ክትትል 🌏|GPS\n🚘 የኪራይ ክትትል 🌏|📚 የክትትል መዝገብ|📣 መልዕክት ላክ|"
    r"📢 መረጃ ለደንበኞች ለመላክ|📢 መልዕክት ላክ — ለሁሉም ደንበኞች|📢 መልዕክት ላክ — ለደላሎች ብቻ|"
    r"🤝 የግንኙነት ጥያቄዎች|📍 የGPS ጥያቄዎች|"
    r"👥 ወዳጆችን ጋብዝ|👥 ጓደኛ ጋብዝ|📡 TANA — የሚዲያ ምንጭ ክትትልና ውጫዊ ማጋራት|"
    r"📣 መልእክት ላክ|🗂️ ጠቅላላ መረጃ ማከማቻ|➡️ የመኪናና ማሽነሪ ሽያጭና ኪራይ ለማግኘት 👈 ይጫኑ|⬅️ ወደ መጀመሪያ ገፅ|🔎 ማሽነሪ ሽያጭ መፈለግ|🔎 ማሽነሪ ኪራይ መፈለግ|🛒 የግዢና የሽያጭ ጨረታ መረጃ ለማግኘት 👈 ተጭነው ይመዝገቡ|🏗️ የግንባታ ጨረታ መረጃ ለማግኘት 👈 ተጭነው ይመዝገቡ|💼 የሥራ ፍለጋ መረጃ ለማግኘት 👈 ተጭነው ይመዝገቡ|🤝 የድለላ መረጃዎችን ለማግኘት 👈 ተጭነው ይመዝገቡ|📢 በየቀኑ የሚወጡ የኮንስትራክሽን፣ የጨረታ መረጃዎችን ለማግኘት 👈ተጭነው ይመዝገቡ|📡 TANA — የሚዲያ ምንጭ ክትትልና ውጫዊ ማጋራት|💳 የአገልግሎት ክፍያ መረጃ|📞 Support|ℹ️ About|🗑️ የመረጃ ማጽዳትና ማስወገድ|📨 ለጨረታ መረጃ መመዝገብ|📢 የጨረታ መረጃ ለተመዝጋቢዎች ላክ|⬅️ ወደ መጀመሪያ ገፅ|🛠️ የአስተዳደር ምናሌ|" + "|".join(re.escape(x) for x in MAIN_FOLDERS) + r")$"
)

async def menu_router(update, context):
    text = update.message.text
    uid = update.effective_user.id
    
    if not is_user_registered(uid):
        await update.message.reply_text("⚠️ **እባክዎ መጀመሪያ /start የሚለውን ተጭነው ስልክ ቁጥርዎን ያስመዝግቡ።**")
        return ConversationHandler.END

    context.user_data["subscribed"] = True
    clear_stale_user_state(context)
    if text == PAGE_TWO_BUTTON:
        await update.message.reply_text("🚗 የመኪናና ማሽነሪ ሽያጭና ኪራይ አገልግሎቶች", reply_markup=main_menu(uid, page=2))
        return ConversationHandler.END
    if text == PAGE_ONE_BUTTON:
        await update.message.reply_text("🚛 የጭነት አገልግሎቶች", reply_markup=main_menu(uid, page=1))
        return ConversationHandler.END
    if text == INFO_MENU_BUTTON and not is_admin(uid):
        kb = ReplyKeyboardMarkup([
            ["🛒 የግዢና የሽያጭ ጨረታ መረጃ ለማግኘት 👈 ተጭነው ይመዝገቡ"],
            ["🏗️ የግንባታ ጨረታ መረጃ ለማግኘት 👈 ተጭነው ይመዝገቡ"],
            ["💼 የሥራ ፍለጋ መረጃ ለማግኘት 👈 ተጭነው ይመዝገቡ"],
            ["🤝 የድለላ መረጃዎችን ለማግኘት 👈 ተጭነው ይመዝገቡ"],
            [PAGE_ONE_BUTTON],
        ], resize_keyboard=True)
        await update.message.reply_text("የሚፈልጉትን የመረጃ አገልግሎት ይምረጡ።", reply_markup=kb)
        return ConversationHandler.END
    if text == "⬅️ ወደ መጀመሪያ ገፅ":
        await update.message.reply_text("ዋና ምናሌ", reply_markup=main_menu(uid))
        return ConversationHandler.END
    if text == ADMIN_BROADCAST_MENU and is_admin(uid):
        await admin_broadcast_menu(update, context)
        return ConversationHandler.END
    if text == ADMIN_INFO_STORAGE and is_admin(uid):
        await admin_info_storage(update, context)
        return ConversationHandler.END
    if text == "🛠️ የአስተዳደር ምናሌ" and is_admin(uid):
        admin_rows = [
            ["📣 መልእክት ላክ"],
            ["🗂️ ጠቅላላ መረጃ ማከማቻ"],
            ["📡 TANA — የሚዲያ ምንጭ ክትትልና ውጫዊ ማጋራት"],
            ["🗑️ የመረጃ ማጽዳትና ማስወገድ"],
            ["🤝 የግንኙነት ጥያቄዎች"],
            ["📍 የGPS ጥያቄዎች"],
            ["📚 የክትትል መዝገብ"],
            ["⬅️ ወደ መጀመሪያ ገፅ"],
        ]
        await update.message.reply_text("🛠️ የአስተዳደር ምናሌ", reply_markup=ReplyKeyboardMarkup(admin_rows, resize_keyboard=True))
        return ConversationHandler.END
    if text == "🚚 ጭነት መለጠፍ":
        return await cargo_start(update, context)
    if text == "🔎 ጭነት መፈለግ":
        await find_cargo(update, context)
        return ConversationHandler.END
    if text == "🚛 የጭነት መኪና ማስመዝገብ":
        return await truck_start(update, context)
    if text == "🚛 የጭነት መኪና መፈለግ":
        await find_truck(update, context)
        return ConversationHandler.END
    if text == "🚐 የኪራይ መኪና ማስመዝገብ":
        return await vehicle_listing_start(update, context, "rental")
    if text == "🔎 የኪራይ መኪና መፈለግ":
        await find_vehicle_listings(update, context, "rental")
        return ConversationHandler.END
    if text == "🚘 የሽያጭ መኪና ማስመዝገብ":
        return await vehicle_listing_start(update, context, "sale")
    if text == "🔎 የሽያጭ መኪና መፈለግ":
        await find_vehicle_listings(update, context, "sale")
        return ConversationHandler.END
    if text == "🚜 የኮንስትራክሽን ማሽነሪ ሽያጭ/ኪራይ":
        return await machinery_start(update, context)
    if text in ("🔎 ማሽነሪ ፈልግ", "🔎 ማሽነሪ ሽያጭ መፈለግ", "🔎 ማሽነሪ ኪራይ መፈለግ"):
        mode = "sale" if text == "🔎 ማሽነሪ ሽያጭ መፈለግ" else ("rental" if text == "🔎 ማሽነሪ ኪራይ መፈለግ" else None)
        context.user_data["machinery_search_mode"] = mode
        await update.message.reply_text("🔎 የሚፈልጉትን ማሽነሪ አይነት ወይም ቦታ ይጻፉ፤ ሁሉንም ለማየት ‘ሁሉም’ ይጻፉ።")
        return MACH_SEARCH
    if text in INFO_CATEGORIES:
        return await info_subscription_start(update, context, INFO_CATEGORIES[text])
    if text == "📢 መረጃ ለደንበኞች ለመላክ" and is_super_admin(uid):
        await super_broadcast_start(update, context)
        return ConversationHandler.END
    if text == "📢 መልዕክት ላክ — ለሁሉም ደንበኞች" and is_super_admin(uid):
        context.user_data["broadcast_mode"] = "all"
        await update.message.reply_text("📝 ለሁሉም ደንበኞች የሚላከውን መልዕክት ይጻፉ።")
        return SUPER_BROADCAST_TEXT
    if text == "📢 መልዕክት ላክ — ለደላሎች ብቻ" and is_super_admin(uid):
        context.user_data["broadcast_mode"] = "brokers"
        await update.message.reply_text("📝 ለተመዘገቡ ደላሎች ብቻ የሚላከውን መልዕክት ይጻፉ።")
        return SUPER_BROADCAST_TEXT
    if text in ("👁️ የግል መረጃ ON", "🙈 የግል መረጃ OFF"):
        return await toggle_admin_pii(update, context)
    if text == "👤 የኔ መረጃ":
        await profile(update, context)
        return ConversationHandler.END
    if text == "👥 ጓደኛ ጋብዝ":
        await show_invite_link(update, context)
        return ConversationHandler.END
    if text in ("📡 የሚዲያ መረጃ", "📡 TANA — የሚዲያ ምንጭ ክትትልና ውጫዊ ማጋራት") and is_admin(uid):
        await show_admin_media_menu(update, context)
        return ConversationHandler.END
    if text == "🗑️ የመረጃ ማጽዳትና ማስወገድ" and is_admin(uid):
        await show_admin_cleanup_menu(update, context)
        return ConversationHandler.END
    if text == "GPS\n🚛 የጭነት ክትትል 🌏":
        await tracking_menu(update, context)
        return ConversationHandler.END
    if text == "GPS\n🚘 የኪራይ ክትትል 🌏":
        return await rental_tracking_start(update, context)
    if text == "📚 የክትትል መዝገብ" and is_admin(uid):
        await show_tracking_registry(update, context)
        return ConversationHandler.END
    if text == "📣 መልዕክት ላክ" and is_super_admin(uid):
        await super_broadcast_start(update, context)
        return ConversationHandler.END
    if text == "🤝 የግንኙነት ጥያቄዎች":
        await show_connection_requests(update, context)
        return ConversationHandler.END
    if text == "📍 የGPS ጥያቄዎች" and is_admin(uid):
        await show_gps_requests(update, context)
        return ConversationHandler.END
    if text in INFO_CATEGORIES:
        return await info_subscription_start(update, context, INFO_CATEGORIES[text])
    if text == "📨 ለጨረታ መረጃ መመዝገብ":
        return await tender_subscribe_start(update, context)
    if text == "📢 የጨረታ መረጃ ለተመዝጋቢዎች ላክ" and is_admin(uid):
        return await tender_broadcast_start(update, context)
    if text == "💳 የአገልግሎት ክፍያ መረጃ":
        await service_payment(update, context)
        return ConversationHandler.END
    if text == "📞 Support":
        await support_start(update, context)
        return ConversationHandler.END
    if text == "ℹ️ About":
        await about(update, context)
        return ConversationHandler.END
    return ConversationHandler.END

async def menu_interrupt(update, context):
    clear_stale_user_state(context)
    text = update.message.text
    if text in (PAGE_TWO_BUTTON, PAGE_ONE_BUTTON, INFO_MENU_BUTTON, "📡 TANA — የሚዲያ ምንጭ ክትትልና ውጫዊ ማጋራት"):
        return await menu_router(update, context)
    if text in ("⬅️ ወደ መጀመሪያ ገፅ", "🛠️ የአስተዳደር ምናሌ", "📨 ለጨረታ መረጃ መመዝገብ", "📢 የጨረታ መረጃ ለተመዝጋቢዎች ላክ") or text in INFO_CATEGORIES:
        return await menu_router(update, context)
    if text == "🚚 ጭነት መለጠፍ":
        return await cargo_start(update, context)
    if text == "🔎 ጭነት መፈለግ":
        await find_cargo(update, context)
        return ConversationHandler.END
    if text == "🚛 የጭነት መኪና ማስመዝገብ":
        return await truck_start(update, context)
    if text == "🚛 የጭነት መኪና መፈለግ":
        await find_truck(update, context)
        return ConversationHandler.END
    if text == "🚐 የኪራይ መኪና ማስመዝገብ":
        return await vehicle_listing_start(update, context, "rental")
    if text == "🔎 የኪራይ መኪና መፈለግ":
        await find_vehicle_listings(update, context, "rental")
        return ConversationHandler.END
    if text == "🚘 የሽያጭ መኪና ማስመዝገብ":
        return await vehicle_listing_start(update, context, "sale")
    if text == "🔎 የሽያጭ መኪና መፈለግ":
        await find_vehicle_listings(update, context, "sale")
        return ConversationHandler.END
    if text == "🚜 የኮንስትራክሽን ማሽነሪ ሽያጭ/ኪራይ":
        return await machinery_start(update, context)
    if text in ("🔎 ማሽነሪ ፈልግ", "🔎 ማሽነሪ ሽያጭ መፈለግ", "🔎 ማሽነሪ ኪራይ መፈለግ"):
        context.user_data["machinery_search_mode"] = "sale" if text == "🔎 ማሽነሪ ሽያጭ መፈለግ" else ("rental" if text == "🔎 ማሽነሪ ኪራይ መፈለግ" else None)
        await update.message.reply_text("🔎 የሚፈልጉትን ማሽነሪ አይነት ወይም ቦታ ይጻፉ፤ ሁሉንም ለማየት ‘ሁሉም’ ይጻፉ።")
        return MACH_SEARCH
    if text == "📢 መረጃ ለደንበኞች ለመላክ" and is_super_admin(update.effective_user.id):
        await super_broadcast_start(update, context)
        return ConversationHandler.END
    if text == "📢 መልዕክት ላክ — ለሁሉም ደንበኞች" and is_super_admin(update.effective_user.id):
        context.user_data["broadcast_mode"] = "all"
        await update.message.reply_text("📝 ለሁሉም ደንበኞች የሚላከውን መልዕክት ይጻፉ።")
        return SUPER_BROADCAST_TEXT
    if text == "📢 መልዕክት ላክ — ለደላሎች ብቻ" and is_super_admin(update.effective_user.id):
        context.user_data["broadcast_mode"] = "brokers"
        await update.message.reply_text("📝 ለተመዘገቡ ደላሎች ብቻ የሚላከውን መልዕክት ይጻፉ።")
        return SUPER_BROADCAST_TEXT
    if text in ("👁️ የግል መረጃ ON", "🙈 የግል መረጃ OFF"):
        return await toggle_admin_pii(update, context)
    if text == "👤 የኔ መረጃ":
        await profile(update, context)
        return ConversationHandler.END
    if text == "👥 ጓደኛ ጋብዝ":
        await show_invite_link(update, context)
        return ConversationHandler.END
    if text in ("📡 የሚዲያ መረጃ", "📡 TANA — የሚዲያ ምንጭ ክትትልና ውጫዊ ማጋራት") and is_admin(update.effective_user.id):
        await show_admin_media_menu(update, context)
        return ConversationHandler.END
    if text == "🗑️ የመረጃ ማጽዳትና ማስወገድ" and is_admin(update.effective_user.id):
        await show_admin_cleanup_menu(update, context)
        return ConversationHandler.END
    if text == "🗑️ የመረጃ ማጽዳትና ማስወገድ" and is_admin(update.effective_user.id):
        await show_admin_cleanup_menu(update, context)
        return ConversationHandler.END
    if text == "GPS\n🚛 የጭነት ክትትል 🌏":
        await tracking_menu(update, context)
        return ConversationHandler.END
    if text == "GPS\n🚘 የኪራይ ክትትል 🌏":
        return await rental_tracking_start(update, context)
    if text == "📚 የክትትል መዝገብ" and is_admin(update.effective_user.id):
        await show_tracking_registry(update, context)
        return ConversationHandler.END
    if text == "📣 መልዕክት ላክ" and is_admin(update.effective_user.id):
        await super_broadcast_start(update, context)
        return ConversationHandler.END
    if text == "🤝 የግንኙነት ጥያቄዎች":
        await show_connection_requests(update, context)
        return ConversationHandler.END
    if text == "📍 የGPS ጥያቄዎች" and is_admin(update.effective_user.id):
        await show_gps_requests(update, context)
        return ConversationHandler.END
    if text == "💳 የአገልግሎት ክፍያ መረጃ":
        await service_payment(update, context)
        return ConversationHandler.END
    if text == "📞 Support":
        await support_start(update, context)
        return ConversationHandler.END
    if text == "ℹ️ About":
        await about(update, context)
        return ConversationHandler.END
    return ConversationHandler.END

# ==================================================
# MAIN
# ==================================================

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")
    start_health_server()
    try:
        init_db()
        load_persistent_data()
        print("✅ Neon PostgreSQL database initialized successfully.")
    except Exception as e:
        print(f"❌ DB INIT ERROR: {e}")
        raise RuntimeError("TANA CARGO, LOGISTICS & INFORMATION SERVICES database could not be initialized; stopping to protect stored data.") from e

    application = Application.builder().token(TOKEN).concurrent_updates(False).build()
    application.add_error_handler(error_handler)

    application.add_handler(CommandHandler("cargo", cargo_start))
    application.add_handler(CommandHandler("truck", truck_start))
    application.add_handler(CommandHandler("findcargo", find_cargo))
    application.add_handler(CommandHandler("findtruck", find_truck))
    application.add_handler(CommandHandler("myprofile", profile))
    application.add_handler(CommandHandler("support", support_start))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(CommandHandler("owner", owner_start))
    application.add_handler(CommandHandler("broker", broker_start))
    application.add_handler(CommandHandler("track", tracking_menu))
    application.add_handler(CommandHandler("invite", show_invite_link))

    master_conversation = ConversationHandler(
        entry_points=[
            CommandHandler("start", start), # 👈 እዚህ ብቻ መሆን አለበት
            MessageHandler(filters.Regex(r"^🛠️ የአስተዳደር ምናሌ$"), menu_router),
            MessageHandler(filters.Regex(r"^📣 መልእክት ላክ$"), menu_router),
            MessageHandler(filters.Regex(r"^🗂️ ጠቅላላ መረጃ ማከማቻ$"), menu_router),
            MessageHandler(filters.Regex(r"^➡️ የመኪናና ማሽነሪ ሽያጭና ኪራይ ለማግኘት 👈 ይጫኑ$"), menu_router),
            MessageHandler(filters.Regex(r"^⬅️ ወደ መጀመሪያ ገፅ$"), menu_router),
            MessageHandler(filters.Regex(r"^📢 በየቀኑ የሚወጡ የኮንስትራክሽን፣ የጨረታ መረጃዎችን ለማግኘት 👈ተጭነው ይመዝገቡ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚚 ጭነት መለጠፍ$"), menu_router),
            MessageHandler(filters.Regex(r"^🔎 ጭነት መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚛 የጭነት መኪና ማስመዝገብ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚛 የጭነት መኪና መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚐 የኪራይ መኪና ማስመዝገብ$"), menu_router),
            MessageHandler(filters.Regex(r"^🔎 የኪራይ መኪና መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚘 የሽያጭ መኪና ማስመዝገብ$"), menu_router),
            MessageHandler(filters.Regex(r"^🔎 የሽያጭ መኪና መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^🚜 የኮንስትራክሽን ማሽነሪ ሽያጭ/ኪራይ$"), menu_router),
            MessageHandler(filters.Regex(r"^📢 የጨረታ መረጃ ለተመዝጋቢዎች ላክ$"), menu_router),
            MessageHandler(filters.Regex(r"^🔎 ማሽነሪ ፈልግ$"), menu_router),
            MessageHandler(filters.Regex(r"^🔎 ማሽነሪ ሽያጭ መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^🔎 ማሽነሪ ኪራይ መፈለግ$"), menu_router),
            MessageHandler(filters.Regex(r"^(🛒 የግዢና የሽያጭ ጨረታ መረጃ ለማግኘት 👈 ተጭነው ይመዝገቡ|🏗️ የግንባታ ጨረታ መረጃ ለማግኘት 👈 ተጭነው ይመዝገቡ|💼 የሥራ ፍለጋ መረጃ ለማግኘት 👈 ተጭነው ይመዝገቡ|🤝 የድለላ መረጃዎችን ለማግኘት 👈 ተጭነው ይመዝገቡ)$"), menu_router),
            MessageHandler(filters.Regex(r"^📢 በየቀኑ የሚወጡ የኮንስትራክሽን፣ የጨረታ መረጃዎችን ለማግኘት 👈ተጭነው ይመዝገቡ$"), menu_router),
            MessageHandler(filters.Regex(r"^📢 መረጃ ለደንበኞች ለመላክ$"), menu_router),
            MessageHandler(filters.Regex(r"^📢 መልዕክት ላክ — ለሁሉም ደንበኞች$"), menu_router),
            MessageHandler(filters.Regex(r"^📢 መልዕክት ላክ — ለደላሎች ብቻ$"), menu_router),
            MessageHandler(filters.Regex(r"^👁️ የግል መረጃ ON$"), menu_router),
            MessageHandler(filters.Regex(r"^🙈 የግል መረጃ OFF$"), menu_router),
            MessageHandler(filters.Regex(r"^👤 የኔ መረጃ$"), menu_router),
            MessageHandler(filters.Regex(r"^👥 ጓደኛ ጋብዝ$"), menu_router),
            MessageHandler(filters.Regex(r"^📡 TANA — የሚዲያ ምንጭ ክትትልና ውጫዊ ማጋራት$"), menu_router),
            MessageHandler(filters.Regex(r"^🗑️ የመረጃ ማጽዳትና ማስወገድ$"), menu_router),
            MessageHandler(filters.Regex(r"^GPS\n🚛 የጭነት ክትትል 🌏$"), menu_router),
            MessageHandler(filters.Regex(r"^GPS\n🚘 የኪራይ ክትትል 🌏$"), menu_router),
            MessageHandler(filters.Regex(r"^📚 የክትትል መዝገብ$"), menu_router),
            MessageHandler(filters.Regex(r"^📣 መልዕክት ላክ$"), menu_router),
            MessageHandler(filters.Regex(r"^🤝 የግንኙነት ጥያቄዎች$"), menu_router),
            MessageHandler(filters.Regex(r"^📍 የGPS ጥያቄዎች$"), menu_router),
            MessageHandler(filters.Regex(r"^💳 የአገልግሎት ክፍያ መረጃ$"), menu_router),
            MessageHandler(filters.Regex(r"^📞 Support$"), menu_router),
            MessageHandler(filters.Regex(r"^ℹ️ About$"), menu_router),
            CommandHandler("owner", owner_start),
            CommandHandler("broker", broker_start),
            CommandHandler("cargo", cargo_start),
            CommandHandler("truck", truck_start),
            CallbackQueryHandler(vehicle_buyer_start, pattern=r"^vehiclebuyer_\d+$"),
            CallbackQueryHandler(connection_request, pattern=r"^connect_\d+$"),
            CallbackQueryHandler(truck_connection_request, pattern=r"^truckconnect_\d+$"),
            CallbackQueryHandler(tracking_share_gmaps_start, pattern=r"^tracking_share_gmaps$"),
            CallbackQueryHandler(tracking_stop_start, pattern=r"^tracking_stop_request$"),
        ],
        states={
            CARGO_FROM: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_from)],
            CARGO_TO: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_to)],
            CARGO_TYPE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_type)],
            CARGO_VEHICLE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), CallbackQueryHandler(cargo_vehicle, pattern=r"^cargo_vehicle_"), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_vehicle_text)],
            CARGO_SIZE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), CallbackQueryHandler(cargo_size, pattern=r"^size_")],
            CARGO_WEIGHT: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_weight)],
            CARGO_CALENDAR: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), CallbackQueryHandler(cargo_calendar, pattern=r"^cal_")],
            CARGO_DATE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_date)],
            CARGO_PHONE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_phone)],
            CARGO_PRICE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, cargo_price)],
            TRUCK_TYPE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, truck_type)],
            TRUCK_PLATE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, truck_plate)],
            TRUCK_CAPACITY: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, truck_capacity)],
            TRUCK_ROUTE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), CallbackQueryHandler(truck_route_choice, pattern=r"^route_"), MessageHandler(filters.TEXT & ~filters.COMMAND, truck_route)],
            TRUCK_ADDRESS: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, truck_address)],
            TRUCK_DRIVER: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, truck_driver)],
            TRUCK_PHONE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, truck_phone)],
            OWNER_NAME: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, owner_name)],
            OWNER_PHONE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, owner_phone)],
            BROKER_SECTORS: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), CallbackQueryHandler(broker_sector_callback, pattern=r"^broker_sector_(freight|machinery|cars|other|all|done)$")],
            BROKER_NAME: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, broker_name)],
            BROKER_PHONE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, broker_phone)],
            BROKER_ADDRESS: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, broker_address)],
            CONNECT_NAME: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, connect_name)],
            CONNECT_PHONE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, connect_phone)],
            CONNECT_TYPE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, connect_type)],
            TRACK_SHARE_ADDRESS: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_share_address)],
            TRACK_SHARE_NAME: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_share_name)],
            TRACK_SHARE_PHONE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_share_phone)],
            TRACK_STOP_NAME: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_stop_name)],
            TRACK_STOP_PHONE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, tracking_stop_phone)],
            RENTAL_TARGET_NAME: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, rental_target_name)],
            RENTAL_TARGET_PHONE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, rental_target_phone)],
            SUPER_BROADCAST_TEXT: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, super_broadcast_send_text)],
            MACH_TYPE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, machinery_type)],
            MACH_MODE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, machinery_mode)],
            MACH_LOCATION: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, machinery_location)],
            MACH_DETAILS: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, machinery_details)],
            MACH_PRICE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, machinery_price)],
            MACH_PHONE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, machinery_phone)],
            MACH_PHOTOS: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.PHOTO, machinery_photos), MessageHandler(filters.TEXT & ~filters.COMMAND, machinery_photos)],
            MACH_SEARCH: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, machinery_search_results)],
            TENDER_NAME: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, tender_name_input)],
            TENDER_PHONE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, tender_phone_input)],
            TENDER_ADDRESS: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, tender_address_input)],
            TENDER_TYPES: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, tender_types_input)],
            TENDER_BROADCAST_CATEGORY: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, tender_broadcast_category)],
            TENDER_BROADCAST_CONTENT: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, tender_broadcast_content)],
            PROPERTY_TYPE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, property_type_input)],
            PROPERTY_MODE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, property_mode_input)],
            PROPERTY_LOCATION: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, property_location_input)],
            PROPERTY_SIZE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, property_size_input)],
            PROPERTY_PRICE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, property_price_input)],
            PROPERTY_DETAILS: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, property_details_input)],
            PROPERTY_ROLE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, property_role_input)],
            PROPERTY_DOCS: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler((filters.PHOTO | filters.Document.ALL | filters.TEXT) & ~filters.COMMAND, property_docs_input)],
            PROPERTY_PHOTOS: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler((filters.PHOTO | filters.TEXT) & ~filters.COMMAND, property_photos_input)],
            INFO_SUB_NAME: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, info_subscription_name)],
            PROPERTY_HUB_KIND: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, property_hub_kind)],
            PROPERTY_HUB_MODE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, property_hub_mode)],
            PROPERTY_HUB_NAME: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, property_hub_name)],
            PROPERTY_HUB_FIELD: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, property_hub_field)],
            PROPERTY_HUB_PHOTOS: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler((filters.PHOTO | filters.TEXT) & ~filters.COMMAND, property_hub_photos)],
            VEHICLE_LIST_TYPE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, vehicle_listing_type)],
            VEHICLE_LIST_PLATE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, vehicle_listing_plate)],
            VEHICLE_LIST_LOCATION: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, vehicle_listing_location)],
            VEHICLE_LIST_DETAILS: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, vehicle_listing_details)],
            VEHICLE_LIST_PRICE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, vehicle_listing_price)],
            VEHICLE_LIST_OWNER: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, vehicle_listing_owner)],
            VEHICLE_LIST_PHOTOS: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), CallbackQueryHandler(vehicle_listing_photo_callback, pattern=r"^vehicle_photo_(done|none)$"), MessageHandler(filters.PHOTO, vehicle_listing_photo)],
            VEHICLE_BUYER_NAME: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler(filters.TEXT & ~filters.COMMAND, vehicle_buyer_name)],
            PHONE_CONFIRM: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), CallbackQueryHandler(phone_confirm_callback, pattern=r"^phone_(ok|new)$"), MessageHandler((filters.CONTACT | filters.TEXT) & ~filters.COMMAND, phone_confirm_input)],
            START_PHONE: [MessageHandler(filters.Regex(MASTER_MENU_REGEX), menu_interrupt), MessageHandler((filters.CONTACT | filters.TEXT) & ~filters.COMMAND, start_phone_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
        conversation_timeout=CONVERSATION_TIMEOUT_SECONDS,
    )
    application.add_handler(master_conversation)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_text_router), group=2)

    # Google Maps share callbacks
    application.add_handler(CallbackQueryHandler(admin_approve_gmaps, pattern=r"^approvegmaps_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_reject_gmaps, pattern=r"^rejectgmaps_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_approve_stop_request, pattern=r"^approvestop_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_reject_stop_request, pattern=r"^rejectstop_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_rental_approve, pattern=r"^rentapprove_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_rental_reject, pattern=r"^rentreject_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_rental_stop, pattern=r"^rentstop_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_listing_broadcast_action, pattern=r"^listing(all|brokers|reject)_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_broadcast_audience_callback, pattern=r"^adm_send_(tender|brokers|people|phone|purchase_sales|construction|jobs|broker_info|all)$"))
    application.add_handler(CallbackQueryHandler(admin_info_subscribers, pattern=r"^infosub_list$"))
    application.add_handler(CallbackQueryHandler(admin_info_subscriber_remove, pattern=r"^infosub_remove_(purchase_sales|construction|jobs|broker_info)_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_broadcast_people_callback, pattern=r"^adm_(people_page_\d+|person_toggle_\d+|people_done)$"))
    application.add_handler(CallbackQueryHandler(admin_info_storage_callback, pattern=r"^adm_info_(page_\d+|toggle_-?\d+|done)$"))
    application.add_handler(CallbackQueryHandler(admin_info_audience_callback, pattern=r"^adm_info_aud_(tender|brokers|people|purchase_sales|construction|jobs|broker_info)$"))
    application.add_handler(CallbackQueryHandler(super_broadcast_choice, pattern=r"^broadcast_(all|brokers|one)$"))
    application.add_handler(CallbackQueryHandler(super_broadcast_page, pattern=r"^broadcast_page_\d+$"))
    application.add_handler(CallbackQueryHandler(super_broadcast_select_user, pattern=r"^broadcast_user_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_org_add_start, pattern=r"^org_add$"))
    application.add_handler(CallbackQueryHandler(announcement_review_callback, pattern=r"^ann(approve|reject)_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_org_directory_callback, pattern=r"^org_directory$"))
    application.add_handler(CallbackQueryHandler(admin_media_platform_callback, pattern=r"^media_platform_(telegram|facebook|website)$|^media_home$"))
    application.add_handler(CallbackQueryHandler(admin_media_add_start, pattern=r"^media_add_(telegram|facebook|website)$"))
    application.add_handler(CallbackQueryHandler(admin_media_type_callback, pattern=r"^media_type_(tg_channel|tg_group|fb_page|fb_group|web_site)$"))
    application.add_handler(CallbackQueryHandler(admin_media_list_callback, pattern=r"^media_list_(telegram|facebook|website)$"))
    application.add_handler(CallbackQueryHandler(admin_media_compose_callback, pattern=r"^media_compose$"))
    application.add_handler(CallbackQueryHandler(admin_media_compose_mode_callback, pattern=r"^media_compose_(text|photo)$"))
    application.add_handler(CallbackQueryHandler(admin_media_import_callback, pattern=r"^media_import$"))
    application.add_handler(CallbackQueryHandler(admin_media_saved_callback, pattern=r"^media_saved$"))
    application.add_handler(CallbackQueryHandler(cleanup_callback, pattern=r"^cleanup_(list|restore_list|history|home|type_.+|pick_.+|restore_\d+)$"))
    application.add_handler(CallbackQueryHandler(property_admin_review_callback, pattern=r"^property_(approve|reject)_\d+$"))
    application.add_handler(CallbackQueryHandler(tender_broadcast_callback, pattern=r"^tender_send_(confirm|cancel)$"))

    application.add_handler(MessageHandler(filters.LOCATION, tracking_location_handler), group=1)

    # Connection callbacks
    application.add_handler(CallbackQueryHandler(already_selected_callback, pattern=r"^already_selected$"))
    application.add_handler(CallbackQueryHandler(subscribed_check_callback, pattern=r"^subscribed_check$"))

    # Broker
    application.add_handler(CallbackQueryHandler(broker_right_confirm, pattern=r"^brokerright_\d+$"))
    application.add_handler(CallbackQueryHandler(broker_x_delete, pattern=r"^brokerxdelete_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_delete_broker, pattern=r"^admindeletebroker_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_vehicle_edit_start, pattern=r"^vehicleedit_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_vehicle_edit_field, pattern=r"^vehiclefield_(details|price|vehicle_type|location|plate|owner_name|phone)_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_vehicle_delete, pattern=r"^vehicledelete_\d+$"))

    # Admin right/X
    application.add_handler(CallbackQueryHandler(admin_right_confirm, pattern=r"^adminright_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_x_delete, pattern=r"^adminxdelete_\d+$"))
    application.add_handler(CallbackQueryHandler(admin_clear_notification, pattern=r"^adminclear_\d+$"))

    application.add_handler(CallbackQueryHandler(admin_delete_connection, pattern=r"^admindeleteconnection_\d+$"))

    application.add_handler(MessageHandler(filters.PHOTO, admin_photo_router))
    application.add_handler(MessageHandler(filters.Document.ALL & ~filters.COMMAND, admin_photo_router))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    application.add_handler(MessageHandler(
        filters.Regex(r"^(👥 ጓደኛ ጋብዝ|📡 TANA — የሚዲያ ምንጭ ክትትልና ውጫዊ ማጋራት)$"),
        menu_tools_fallback
    ), group=1)

    if application.job_queue:
        application.job_queue.run_repeating(clean_expired_posts, interval=3600, first=60)
        application.job_queue.run_repeating(auto_delete_messages, interval=60, first=30)
        application.job_queue.run_repeating(monitor_registered_sources, interval=3600, first=90, name="organization_source_monitor")
        application.job_queue.run_daily(daily_dealer_digest, time=__import__("datetime").time(hour=8, minute=0, tzinfo=ZoneInfo("Africa/Addis_Ababa")), name="daily_dealer_digest")
        print("✅ Expiry cleaner + Auto-delete + 60-minute source monitor + 08:00 EAT admin digest started")

    print("TANA CARGO, LOGISTICS & INFORMATION SERVICES Bot is starting...")
    application.run_polling(drop_pending_updates=True)
async def check_subscription(user_id, context):
    return True

async def subscribed_check_callback(update, context):
    query = update.callback_query
    user_id = update.effective_user.id
    context.user_data["subscribed"] = True
    await query.answer("✅ አመሰግናለሁ! Subscribe ማድረግዎን አረጋግጠናል።", show_alert=True)
    try:
        await query.edit_message_text("✅ **አመሰግናለሁ!**\n\nSubscribe ማድረግዎን አረጋግጠናል።\n\n👉 አሁን የፈለጉትን ምናሌ በመንካት ይቀጥሉ!")
    except Exception:
        pass
    await context.bot.send_message(
        chat_id=user_id,
        text="👇 አሁን የፈለጉትን ምናሌ ይንኩ፦",
        reply_markup=main_menu(user_id)
    )
if __name__ == "__main__":
    main()
