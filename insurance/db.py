"""Shared SQLite schema for customers, profiles, policy book and portfolio snapshots."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_batches(batch_id TEXT PRIMARY KEY, source TEXT, channel TEXT, ts REAL,
  rows_in INTEGER, rows_ok INTEGER, rows_rejected INTEGER);
CREATE TABLE IF NOT EXISTS customers_raw(raw_id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT, row_no INTEGER,
  external_id TEXT, full_name TEXT, dob TEXT, gender TEXT, phone TEXT, email TEXT, pan_hash TEXT, pan_last4 TEXT,
  aadhaar_hash TEXT, aadhaar_last4 TEXT, aadhaar_valid INTEGER, address TEXT, pincode TEXT, state TEXT,
  occupation TEXT, annual_income REAL, smoker INTEGER, channel TEXT, golden_id TEXT);
CREATE TABLE IF NOT EXISTS rejects(batch_id TEXT, row_no INTEGER, reasons TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS customers(golden_id TEXT PRIMARY KEY, full_name TEXT, dob TEXT, gender TEXT, phone TEXT,
  email TEXT, pan_last4 TEXT, aadhaar_last4 TEXT, aadhaar_valid INTEGER, pincode TEXT, state TEXT, occupation TEXT,
  annual_income REAL, smoker INTEGER, channels TEXT, source_records INTEGER);
CREATE TABLE IF NOT EXISTS match_queue(id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT, raw_a INTEGER, raw_b INTEGER,
  score REAL, status TEXT DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS profiles(golden_id TEXT PRIMARY KEY, age INTEGER, tenure_years REAL, n_policies INTEGER,
  total_sum_insured REAL, claims_5y INTEGER, claim_ratio REAL, late_payments INTEGER, cover_to_income REAL,
  completeness REAL, kyc_level TEXT, value_tier TEXT, risk_tier TEXT, segment TEXT, updated_at REAL);
CREATE TABLE IF NOT EXISTS policy_book(policy_id TEXT PRIMARY KEY, golden_id TEXT, product TEXT, sum_insured REAL,
  premium REAL, term_years INTEGER, start_year INTEGER, status TEXT, claims_5y INTEGER, claims_paid_5y REAL,
  late_payments INTEGER, state TEXT, risk_class TEXT);
CREATE TABLE IF NOT EXISTS portfolio_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, label TEXT, metrics TEXT);
"""


def ensure_schema(con):
    con.executescript(SCHEMA)
    con.commit()
    return con


def connect(path=":memory:"):
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return ensure_schema(con)
