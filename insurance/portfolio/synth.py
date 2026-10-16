"""Deterministic synthetic customers (dirty on purpose) and policy book for demos/tests."""
import csv
import random
from datetime import date, timedelta

from ..customers.validate import verhoeff_append

FIRST = ["Asha", "Ravi", "Meera", "Arjun", "Priya", "Karan", "Neha", "Vikram", "Sneha", "Rahul", "Anita", "Suresh", "Divya", "Manoj", "Pooja", "Amit"]
LAST = ["Rao", "Kumar", "Nair", "Sharma", "Iyer", "Patel", "Singh", "Reddy", "Das", "Mehta", "Gupta", "Joshi"]
OCC = ["office", "teacher", "driver", "construction", "doctor", "pilot", "security", "business", "miner"]
CHANNELS = ["agent_app", "broker_portal", "whatsapp", "email", "branch_scan", "call_centre"]
STATES = {"MH": 0.22, "KA": 0.18, "DL": 0.12, "TN": 0.14, "GJ": 0.1, "UP": 0.08, "WB": 0.06, "TS": 0.1}


def make_customers_csv(path, n=2000, seed=11, dup_rate=0.06, bad_rate=0.04, ambiguous_rate=0.02):
    rnd = random.Random(seed)
    rows = []
    for i in range(n):
        name = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
        dob = date(1950, 1, 1) + timedelta(days=rnd.randint(0, 365 * 50))
        pan = "".join(rnd.choices("ABCDEFGHJKLMNPQRSTUVWXYZ", k=5)) + f"{rnd.randint(0, 9999):04d}" + rnd.choice("ABCDEFGH")
        aad = verhoeff_append(str(rnd.randint(2 * 10**10, 9 * 10**10 + 9 * 10**9))[:11])
        st = rnd.choices(list(STATES), weights=STATES.values())[0]
        rows.append(dict(customer_id=f"X{i:05d}", name=name, date_of_birth=dob.strftime(rnd.choice(["%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y"])),
                         gender=rnd.choice(["M", "F", "male", "female"]), mobile=f"{rnd.choice(['98', '97', '88', '70'])}{rnd.randint(10**7, 10**8 - 1)}",
                         email=f"{name.split()[0].lower()}.{name.split()[1].lower()}{rnd.randint(1, 999)}@example.com",
                         pan=pan, aadhaar=aad, pincode=str(rnd.randint(110001, 799999)), state=st, occupation=rnd.choice(OCC),
                         income=str(round(rnd.lognormvariate(13.3, 0.6), -3)), smoker=rnd.choice(["no"] * 4 + ["yes"])))
    out = list(rows)
    for r in rnd.sample(rows, int(n * dup_rate)):               # duplicates from another channel, messier
        d = dict(r); d["customer_id"] = "D" + r["customer_id"]
        d["name"] = r["name"].upper() if rnd.random() < .5 else r["name"].replace("a", "aa", 1)
        d["mobile"] = "+91 " + r["mobile"]
        if rnd.random() < .5: d["pan"] = ""
        out.append(d)
    for r in rnd.sample(rows, int(n * ambiguous_rate)):         # same person? name typo, phone missing, new pincode, same email
        d = dict(r); d["customer_id"] = "A" + r["customer_id"]
        d["name"] = r["name"].replace("a", "aa", 1); d["mobile"] = ""; d["pan"] = ""; d["aadhaar"] = ""
        d["pincode"] = str(int(r["pincode"]) % 100000 + 100000)
        out.append(d)
    for r in rnd.sample(out, int(len(out) * bad_rate)):          # corrupt a field
        r[rnd.choice(["date_of_birth", "mobile", "email", "pan", "pincode"])] = "??"
    rnd.shuffle(out)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(out)
    return len(out)


def make_policy_book(con, seed=5):
    """Create 0-3 policies per golden customer with sum insured tied to income; simulated claim/lateness history."""
    from ..actuarial import mortality as mo
    rnd = random.Random(seed)
    cm = mo.Commutation(mo.MortalityTable.gompertz_makeham(), 0.06)
    n = 0
    for c in con.execute("SELECT * FROM customers").fetchall():
        c = dict(c)
        age = date.today().year - int(c["dob"][:4])
        for prod in rnd.sample(["term_life", "health", "motor", "home"], rnd.choice([0, 1, 1, 1, 2, 3])):
            inc = c["annual_income"] or 600_000
            si = {"term_life": inc * rnd.choice([8, 10, 15, 20]), "health": rnd.choice([500_000, 1_000_000, 2_500_000]),
                  "motor": rnd.choice([400_000, 800_000, 1_500_000]), "home": inc * rnd.choice([3, 5, 8])}[prod]
            term = rnd.choice([10, 20, 25]) if prod == "term_life" else 1
            if prod == "term_life" and age + term > 100:
                continue
            prem = (mo.term_gross_premium(cm, max(age, 18), term, si, 2000, 300, 0.1) if prod == "term_life"
                    else si * {"health": 0.02, "motor": 0.035, "home": 0.004}[prod])
            lam = {"term_life": 0.0, "health": 0.12, "motor": 0.09 * (1.6 if age < 25 else 1), "home": 0.03}[prod]
            n_cl = sum(rnd.random() < lam for _ in range(5)) if lam else 0
            paid = sum(rnd.lognormvariate({"health": 11.0, "motor": 10.8, "home": 11.5}[prod], 0.9) for _ in range(n_cl))
            con.execute("INSERT INTO policy_book VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (f"P{n:06d}", c["golden_id"], prod, si, round(prem, 2), term, date.today().year - rnd.randint(0, 6),
                         "in_force", n_cl, round(paid, 2), sum(rnd.random() < 0.08 for _ in range(5)), c["state"],
                         rnd.choice(["preferred", "standard", "standard", "substandard"])))
            n += 1
    con.commit()
    return n
