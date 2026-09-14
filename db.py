"""SQLite persistence for the CKS9 event log.

Table: events(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, message TEXT)
"""
import os
import sqlite3
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("CKS9_DB", os.path.join(BASE_DIR, "events.db"))
_lock = threading.Lock()


def init_db():
    with _lock:
        con = sqlite3.connect(DB_PATH)
        try:
            con.execute("""CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                message TEXT NOT NULL
            )""")
            con.commit()
        finally:
            con.close()


def log_event(message):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        con = sqlite3.connect(DB_PATH)
        try:
            con.execute("INSERT INTO events (timestamp, message) VALUES (?, ?)",
                        (ts, str(message)))
            con.commit()
        finally:
            con.close()


def last_events(limit=50):
    with _lock:
        con = sqlite3.connect(DB_PATH)
        try:
            rows = con.execute(
                "SELECT id, timestamp, message FROM events ORDER BY id DESC LIMIT ?",
                (limit,)).fetchall()
        finally:
            con.close()
    return [{"id": r[0], "timestamp": r[1], "message": r[2]} for r in rows]
