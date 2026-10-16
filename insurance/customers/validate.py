"""Record validation/normalisation. Validates FORMAT and checksums only; it does not verify identity with
UIDAI/NSDL (that needs an authorised KYC provider)."""
import hashlib
import os
import re
from datetime import date, datetime

from pydantic import BaseModel, field_validator

SALT = os.environ.get("PII_SALT", "dev-only-change-me")

_D = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],
      [5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]]
_P = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],
      [9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]
_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_valid(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def verhoeff_append(num: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[(i + 1) % 8][int(ch)]]
    return num + str(_INV[c])


def h(value: str) -> str:
    return hashlib.sha256((SALT + value).encode()).hexdigest()


DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y", "%m/%d/%Y"]
STATES = {"MH", "KA", "DL", "TN", "GJ", "UP", "WB", "RJ", "TS", "KL", "PB", "HR", "MP", "BR", "AP"}


class CustomerRecord(BaseModel):
    external_id: str = ""
    full_name: str
    dob: date
    gender: str = ""
    phone: str = ""
    email: str = ""
    pan: str = ""
    aadhaar: str = ""
    address: str = ""
    pincode: str = ""
    state: str = ""
    occupation: str = ""
    annual_income: float = 0.0
    smoker: bool = False

    @field_validator("full_name")
    @classmethod
    def _name(cls, v):
        v = re.sub(r"\s+", " ", v.strip())
        if len(v) < 3 or not re.fullmatch(r"[A-Za-z .'-]+", v):
            raise ValueError("invalid name")
        return v.title()

    @field_validator("dob", mode="before")
    @classmethod
    def _dob(cls, v):
        if isinstance(v, date):
            return v
        for f in DATE_FORMATS:
            try:
                d = datetime.strptime(str(v).strip(), f).date()
                break
            except ValueError:
                continue
        else:
            raise ValueError(f"unparseable dob {v!r}")
        age = (date.today() - d).days / 365.25
        if not 0 <= age <= 100:
            raise ValueError(f"implausible age {age:.0f}")
        return d

    @field_validator("gender", mode="before")
    @classmethod
    def _gender(cls, v):
        return {"m": "M", "male": "M", "f": "F", "female": "F"}.get(str(v).strip().lower(), "")

    @field_validator("phone", mode="before")
    @classmethod
    def _phone(cls, v):
        d = re.sub(r"\D", "", str(v or ""))
        d = d[-10:] if len(d) >= 10 else d
        if not d:
            return ""
        if not re.fullmatch(r"[6-9]\d{9}", d):
            raise ValueError("invalid mobile")
        return "+91" + d

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v):
        v = str(v or "").strip().lower()
        if v and not re.fullmatch(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", v):
            raise ValueError("invalid email")
        return v

    @field_validator("pan", mode="before")
    @classmethod
    def _pan(cls, v):
        v = str(v or "").strip().upper()
        if v and not re.fullmatch(r"[A-Z]{5}\d{4}[A-Z]", v):
            raise ValueError("invalid PAN format")
        return v

    @field_validator("aadhaar", mode="before")
    @classmethod
    def _aadhaar(cls, v):
        v = re.sub(r"\D", "", str(v or ""))
        if v and len(v) != 12:
            raise ValueError("aadhaar must be 12 digits")
        return v

    @field_validator("pincode", mode="before")
    @classmethod
    def _pin(cls, v):
        v = str(v or "").strip()
        if v and not re.fullmatch(r"[1-9]\d{5}", v):
            raise ValueError("invalid pincode")
        return v

    @field_validator("state", mode="before")
    @classmethod
    def _state(cls, v):
        return str(v or "").strip().upper()

    @field_validator("annual_income", mode="before")
    @classmethod
    def _inc(cls, v):
        x = float(str(v or 0).replace(",", "") or 0)
        if x < 0:
            raise ValueError("negative income")
        return x

    @field_validator("smoker", mode="before")
    @classmethod
    def _smk(cls, v):
        return str(v).strip().lower() in ("1", "y", "yes", "true", "smoker")
