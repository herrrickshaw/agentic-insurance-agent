"""Batch ingestion: CSV/JSONL -> validate -> hash PII -> stage in customers_raw; rejects keep the reasons."""
import csv
import json
import time
import uuid
from pathlib import Path

from pydantic import ValidationError

from .validate import CustomerRecord, h, verhoeff_valid

ALIASES = {"name": "full_name", "customer_name": "full_name", "date_of_birth": "dob", "birth_date": "dob",
           "mobile": "phone", "phone_number": "phone", "pan_no": "pan", "pan_number": "pan", "uid": "aadhaar",
           "aadhaar_no": "aadhaar", "pin": "pincode", "zip": "pincode", "income": "annual_income",
           "id": "external_id", "customer_id": "external_id"}


def read_rows(path):
    p = Path(path)
    if p.suffix == ".jsonl":
        for line in p.read_text().splitlines():
            if line.strip():
                yield json.loads(line)
    else:
        with open(p, newline="", encoding="utf-8-sig") as f:
            yield from csv.DictReader(f)


def normalise_keys(row):
    return {ALIASES.get(k.strip().lower().replace(" ", "_"), k.strip().lower().replace(" ", "_")): v for k, v in row.items()}


def ingest_file(con, path, channel="unknown", batch_id=None):
    batch_id = batch_id or "B" + uuid.uuid4().hex[:8]
    n_in = n_ok = 0
    for i, row in enumerate(read_rows(path), start=2):  # row 1 is the header
        n_in += 1
        row = normalise_keys(row)
        try:
            rec = CustomerRecord(**{k: v for k, v in row.items() if k in CustomerRecord.model_fields})
        except ValidationError as e:
            reasons = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
            con.execute("INSERT INTO rejects VALUES(?,?,?,?)", (batch_id, i, reasons, json.dumps({k: v for k, v in row.items() if k not in ('pan', 'aadhaar')})))
            continue
        n_ok += 1
        con.execute(
            "INSERT INTO customers_raw(batch_id,row_no,external_id,full_name,dob,gender,phone,email,pan_hash,pan_last4,"
            "aadhaar_hash,aadhaar_last4,aadhaar_valid,address,pincode,state,occupation,annual_income,smoker,channel)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (batch_id, i, rec.external_id, rec.full_name, rec.dob.isoformat(), rec.gender, rec.phone, rec.email,
             h(rec.pan) if rec.pan else None, rec.pan[-4:] if rec.pan else None,
             h(rec.aadhaar) if rec.aadhaar else None, rec.aadhaar[-4:] if rec.aadhaar else None,
             int(verhoeff_valid(rec.aadhaar)) if rec.aadhaar else None, rec.address, rec.pincode, rec.state,
             rec.occupation, rec.annual_income, int(rec.smoker), channel))
    con.execute("INSERT INTO ingest_batches VALUES(?,?,?,?,?,?,?)",
                (batch_id, str(path), channel, time.time(), n_in, n_ok, n_in - n_ok))
    con.commit()
    return {"batch_id": batch_id, "rows_in": n_in, "accepted": n_ok, "rejected": n_in - n_ok,
            "reject_rate": round((n_in - n_ok) / max(n_in, 1), 4)}
