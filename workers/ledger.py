"""SQLite system-of-record for policies, claims and payments (swap for Postgres in prod)."""
import os
import sqlite3
import time

DB_PATH = os.environ.get("LEDGER_DB", "ledger.db")
SCHEMA = """
CREATE TABLE IF NOT EXISTS policies(policy_number TEXT PRIMARY KEY, applicant TEXT, product TEXT,
  sum_insured INTEGER, premium REAL, status TEXT, start_date TEXT, end_date TEXT);
CREATE TABLE IF NOT EXISTS claims(claim_number TEXT PRIMARY KEY, policy_number TEXT, amount INTEGER,
  approved_amount INTEGER, status TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, ref TEXT, amount REAL,
  due_date TEXT, status TEXT, paid_amount REAL, payment_ref TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, ref TEXT, event TEXT);
"""


def connect(path=None):
    con = sqlite3.connect(path or DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def log(con, ref, event):
    con.execute("INSERT INTO events(ts,ref,event) VALUES(?,?,?)", (time.time(), ref, event))
    con.commit()


def next_number(con, prefix, table, col):
    n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] + 1
    return f"{prefix}-{time.strftime('%Y')}-{n:06d}"
