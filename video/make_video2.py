"""Second demo video: customer ingestion, portfolio risk, underwriting rating and actuarial maths (live-run data)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import *   # canvas, bpmn, table, bullets, wrap, F, M, colours, bm, ROOT, assemble

D = json.load(open(ROOT / "video" / "capture2.json"))
P = D["portfolio"]


def hbars(d, items, x, y, w, rowh=44, maxv=None, fmt="{:,.0f}", size=24, label_w=330):
    maxv = maxv or max(v for _, v, *_ in items)
    for i, it in enumerate(items):
        lab, v = it[0], it[1]; col = it[2] if len(it) > 2 else ACC
        yy = y + i * rowh
        d.text((x, yy + 4), str(lab)[:26], font=F(size), fill=FG)
        d.rounded_rectangle((x + label_w, yy + 2, x + label_w + max(4, (w - label_w - 200) * v / maxv), yy + rowh - 8), 6, fill=col)
        d.text((x + label_w + max(4, (w - label_w - 200) * v / maxv) + 12, yy + 4), fmt.format(v), font=F(size), fill=MUT)
    return y + len(items) * rowh


def line_chart(d, x0, y0, w, h, xs, series, xlabel="", ylabel="", fmt="{:,.0f}"):
    ymax = max(max(s) for _, s, _ in series) * 1.08
    d.line([(x0, y0 + h), (x0 + w, y0 + h)], fill=MUT, width=2); d.line([(x0, y0), (x0, y0 + h)], fill=MUT, width=2)
    for k in range(5):
        yy = y0 + h - h * k / 4
        d.line([(x0, yy), (x0 + w, yy)], fill="#233047"); d.text((x0 - 12, yy), fmt.format(ymax * k / 4), font=F(18), fill=MUT, anchor="rm")
    for i, xv in enumerate(xs):
        d.text((x0 + w * i / (len(xs) - 1), y0 + h + 10), str(xv), font=F(18), fill=MUT, anchor="ma")
    for name, ys, col in series:
        pts = [(x0 + w * i / (len(xs) - 1), y0 + h - h * v / ymax) for i, v in enumerate(ys)]
        d.line(pts, fill=col, width=4)
        for p in pts: d.ellipse((p[0] - 5, p[1] - 5, p[0] + 5, p[1] + 5), fill=col)
        d.text((pts[-1][0] + 10, pts[-1][1] - 12), name, font=F(20), fill=col)
    d.text((x0 + w / 2, y0 + h + 40), xlabel, font=F(20), fill=MUT, anchor="ma")


scenes = []
def add(im, narr):
    scenes.append((im[0], narr, im[1]) if isinstance(im, tuple) else (im, narr, None))

# 1 title
im, d = canvas("Customers, portfolios and actuarial maths", "Second walkthrough of the agentic insurance agent")
bullets(d, ["Customer data ingestion with PII hashing, validation rejects and entity resolution",
            "360-degree profiles and segments feeding underwriting",
            "Portfolio exposure, VaR and TVaR, concentration limits and reserving",
            "Life-contingency pricing from first principles, tested against closed-form identities",
            "Mortality table and customer data are illustrative or synthetic; the numbers are real outputs of this code"], 80, 260, 36)
add(im, "In this second walkthrough we go deeper: customer data ingestion and profiles, portfolio risk for underwriting, and actuarial mathematics. The mortality table and customer data are illustrative or synthetic, but every number you see is a real output of the code, captured from a live run.")

# 2 onboarding bpmn
add(bpmn(bm.customer_onboarding(), ("ingest", "resolve", "steward", "profiles"), "Process 4: Customer onboarding", "Ingest, validate, resolve duplicates, human review, build profiles"),
    "The onboarding process starts when a data file arrives. Records are validated and personal identifiers are hashed. Entity resolution merges duplicates, a data steward reviews the uncertain pairs, and then customer profiles are built. If too many rows are rejected, the batch stops and the data owner is told.")

# 3 ingestion
b = D["batch"]
im, d = canvas("Ingestion: validate, reject with reasons, hash PII", f"Batch {b['batch_id']} from channel {b['channel']}")
for i, (lab, v, col) in enumerate([("Rows in", b["rows_in"], ACC), ("Accepted", b["rows_ok"], OK), ("Rejected", b["rows_rejected"], BAD)]):
    d.rounded_rectangle((80 + i * 400, 220, 440 + i * 400, 380), 16, fill=CARD, outline=col, width=3)
    d.text((260 + i * 400, 250), f"{v:,}", font=F(64, True), fill=col, anchor="ma"); d.text((260 + i * 400, 330), lab, font=F(28), fill=MUT, anchor="ma")
d.text((80, 430), "Why rows were rejected (by field)", font=F(32, True), fill=ACC)
hbars(d, [(r["field"], r["n"], WARN) for r in D["reject_reasons"][:6]], 80, 490, 1100)
bullets(d, ["Aadhaar checked with the Verhoeff checksum; PAN pattern; mobile, pincode and mixed date formats normalised",
            "Aadhaar and PAN stored only as salted hashes plus last four digits", "Format checks only: this is not identity verification"], 1000, 450, 28, width=800)
add(im, f"Ingestion read {b['rows_in']:,} rows. {b['rows_ok']:,} were accepted and {b['rows_rejected']} were rejected, each with a recorded reason, mostly bad email, PAN format, birth date or pincode. Aadhaar and PAN are never stored in the clear, only as salted hashes with the last four digits. These are format and checksum checks, not identity verification.")

# 4 entity resolution
q = {r["status"]: r["n"] for r in D["queue"]}; ov = D["customer_onboarding"]["vars"]
n_rev = sum(q.values())          # pairs the steward saw (the process variable is cleared to 0 after the decision)
im, d = canvas("Entity resolution: one golden record per person", f"{D['raw']:,} staged records resolved into {D['golden']:,} customers")
bullets(d, [f"{ov['autoMerged']} pairs auto-merged (score 0.92 or higher: shared PAN or Aadhaar hash, or name plus DOB plus contact agree)",
            f"{n_rev} pairs sent to a data steward task (score between 0.75 and 0.92); steward merged {q.get('merged', 0)}",
            "Name and birth date alone can never auto-merge; different birth dates never score as the same person"], 80, 230, 32)
d.text((80, 560), "Examples the steward saw", font=F(32, True), fill=ACC)
y = table(d, 80, 620, ["Record A", "Record B", "Score"], [(p["na"], p["nb"], f"{p['score']:.2f}") for p in D["sample_pairs"]], [520, 520, 200], 26)
d.text((80, y + 30), "Golden record uses survivorship: the latest non-empty value per field.", font=F(28), fill=MUT)
add(im, f"Entity resolution turned {D['raw']:,} staged records into {D['golden']:,} golden customers. {ov['autoMerged']} pairs merged automatically on strong evidence. {n_rev} uncertain pairs, such as these name typos, went to a human data steward. Name and birth date alone are never enough to merge automatically.")

# 5 profiles
im, d = canvas("Customer 360 profiles and segments", f"{ov['profilesBuilt']:,} profiles built; average data completeness {D['completeness']:.0%}")
seg = D["segments"][:8]
d.text((80, 215), "Customers by value and risk segment", font=F(30, True), fill=ACC)
hbars(d, [(s["segment"], s["n"], OK if "low-risk" in s["segment"] else WARN if "medium" in s["segment"] else BAD) for s in seg], 80, 270, 1050, maxv=seg[0]["n"], label_w=400)
d.text((1250, 215), "KYC readiness", font=F(30, True), fill=ACC)
hbars(d, [(k["kyc_level"], k["n"]) for k in D["kyc"]], 1250, 270, 600, label_w=200, size=22)
bullets(d, ["Features: age, tenure, products held, cover-to-income, claims, claim ratio, late payments", f"95th percentile cover-to-income: {D['cover_to_income_p95']:.1f}x"], 1250, 520, 26, width=600)
add(im, f"{ov['profilesBuilt']:,} customer profiles were built, combining demographics, policies held, claims and payment behaviour into a value and risk segment. The profiles also show KYC readiness and a cover to income ratio that feeds financial underwriting.")

# 6 portfolio bpmn
add(bpmn(bm.portfolio_risk_review(), ("metrics", "reserve", "limits", "committee"), "Process 5: Portfolio risk review", "A decision table compares risk metrics with appetite; breaches go to a committee"),
    "The portfolio risk review computes exposure, value at risk and concentration, runs reserving, and then a decision table compares the results with risk appetite. Within appetite ends the process. A breach goes to the underwriting committee, and a severe one is escalated to the chief risk officer first.")

# 7 portfolio metrics
H = D["agg_hist"]
im, d = canvas("Portfolio risk: where the tail sits", f"{P['policies']:,} policies, {P['customers']:,} customers, total sum insured {P['total_sum_insured']/1e9:.1f} bn")
x0, y0, w, h = 110, 250, 1080, 520
mx = max(H["counts"] + H["net_counts"]); n = len(H["counts"]); bw = w / n
xr = lambda v: x0 + (v - H["edges"][0]) / (H["edges"][-1] - H["edges"][0]) * w
for i, c in enumerate(H["counts"]):
    d.rectangle((x0 + i * bw, y0 + h - h * c / mx, x0 + (i + 1) * bw - 1, y0 + h), fill="#27567a")
for i, c in enumerate(H["net_counts"]):
    d.rectangle((x0 + i * bw, y0 + h - h * c / mx, x0 + (i + 1) * bw - 1, y0 + h), outline=OK)
for v, lab, col in [(H["mean"], "mean", MUT), (H["var995"], "VaR 99.5%", BAD), (H["net_var995"], "net of reinsurance", OK)]:
    d.line([(xr(v), y0), (xr(v), y0 + h)], fill=col, width=3); d.text((xr(v) + 6, y0 + (0 if col == BAD else 30 if col == MUT else 60)), f"{lab} {v:,.0f}m", font=F(22), fill=col)
d.text((x0, y0 + h + 12), "Simulated annual aggregate loss, INR millions (blue = gross, green outline = after excess-of-loss reinsurance)", font=F(20), fill=MUT)
rows = [("Expected loss ratio", f"{P['expected_loss_ratio']:.2f}"), ("VaR 99.5%", f"{P['var995']/1e6:,.0f}m"), ("TVaR 99%", f"{P['tvar99']/1e6:,.0f}m"),
        ("VaR % of own funds", f"{P['var995_pct_own_funds']}%"), ("Largest state", f"{P['max_state']} {P['max_state_share_pct']}%"), ("Top 10 customers", f"{P['top10_customer_share_pct']}% of SI")]
yy = 250
for k, v in rows:
    d.text((1300, yy), k, font=F(26), fill=MUT); d.text((1860, yy), v, font=F(28, True), fill=FG, anchor="ra"); yy += 52
d.text((1300, yy + 20), "Own funds assumed: 1.5 bn", font=F(24), fill=MUT)
d.text((1300, yy + 60), "Limits DMN: VaR above 15% of own funds", font=F(24), fill=WARN); d.text((1300, yy + 95), "-> committee review", font=F(24, True), fill=WARN)
add(im, f"Simulating a year of losses fifty thousand times gives this distribution. The expected loss ratio is {P['expected_loss_ratio']:.2f}. The ninety nine point five percent value at risk is {P['var995']/1e6:,.0f} million, which is {P['var995_pct_own_funds']} percent of the assumed own funds. Excess of loss reinsurance on large life covers pulls the tail in, shown in green. Because the figure is above the fifteen percent limit, the decision table sent it to the committee.")

# 8 concentration + reserving
R = D["reserving"]
im, d = canvas("Concentration and reserving", "Exposure by state and product; IBNR from chain-ladder and Bornhuetter-Ferguson")
d.text((80, 215), "Sum insured by state (%)", font=F(30, True), fill=ACC)
hbars(d, [(k, v, BAD if v > 20 else ACC) for k, v in list(D["state_share"].items())[:8]], 80, 270, 900, fmt="{:.1f}", label_w=120)
d.text((80, 660), "Product mix (% of sum insured)", font=F(30, True), fill=ACC)
hbars(d, list(D["product_si"].items()), 80, 710, 900, fmt="{:.1f}", label_w=200, rowh=40, size=22)
d.text((1100, 215), "Reserving (non-life)", font=F(30, True), fill=ACC)
bullets(d, [f"Chain-ladder reserve: {R['chain_ladder_reserve']/1e6:,.1f} m", f"Bornhuetter-Ferguson reserve: {R['bf_reserve']/1e6:,.1f} m",
            "Age-to-age factors: " + ", ".join(f"{f:.3f}" for f in R["factors"])], 1100, 270, 30, width=760)
d.rounded_rectangle((1100, 650, 1850, 800), 14, outline=WARN, width=3, fill=CARD)
d.text((1125, 665), "The ledger has no payment-development history,", font=F(26), fill=WARN); d.text((1125, 705), "so this triangle is synthetic. The method is real;", font=F(26), fill=WARN); d.text((1125, 745), "the reserve is not a claim about this book.", font=F(26), fill=WARN)
add(im, f"Concentration checks show the largest state share and the product mix. Reserving compares the chain ladder method, at {R['chain_ladder_reserve']/1e6:,.1f} million, with Bornhuetter Ferguson, at {R['bf_reserve']/1e6:,.1f} million. To be clear, the ledger has no payment development history, so this triangle is synthetic. The method is real; the reserve is not a statement about this book.")

# 9 actuarial pricing
pa = D["premium_by_age"]; I = D["identity"]
im, d = canvas("Actuarial pricing from first principles", "Term assurance, 20 years, sum assured 10 million, 6% interest, illustrative mortality table")
line_chart(d, 140, 250, 800, 400, pa["ages"], [("smoker x1.8", pa["smoker"], BAD), ("non-smoker", pa["nonsmoker"], OK)], "age at entry", "", "{:,.0f}")
d.text((140, 230), "Net annual premium (INR)", font=F(24), fill=MUT)
rp = D["reserve_path"]
line_chart(d, 1100, 250, 700, 400, list(range(0, 21, 5)), [("reserve", [rp[i] for i in range(0, 21, 5)], ACC)], "policy year", "", "{:,.0f}")
d.text((1100, 230), "Net premium reserve, entry age 35", font=F(24), fill=MUT)
pol = D["policies"]
d.text((80, 730), "Live policy runs through the engine (age 35):", font=F(28, True), fill=ACC)
for i, p in enumerate(pol):
    d.text((80, 780 + i * 44), f"{'Smoker' if p['smoker'] else 'Non-smoker'}: {p['ratingClass']}, mortality x{p['mortalityMultiple']}, gross premium INR {p['premium']:,.0f}", font=F(28), fill=FG)
d.text((1100, 730), "Checks the tests enforce", font=F(28, True), fill=ACC)
d.text((1100, 780), f"A(40) = {I['A40']:.10f}", font=M(24), fill=FG); d.text((1100, 815), f"1 - d*a(40) = {I['one_minus_d_a']:.10f}", font=M(24), fill=OK)
d.text((1100, 855), "reserve = 0 at issue and expiry; equivalence principle", font=F(22), fill=MUT)
add(im, f"Pricing uses the equivalence principle: the present value of premiums equals the present value of benefits, built from commutation functions. Smokers cost more at every age. The reserve rises through the policy term and returns to zero at expiry. Live through the engine, the thirty five year old non smoker was rated preferred at about {pol[0]['premium']:,.0f} rupees a year, and the smoker, at mortality times one point eight, at about {pol[1]['premium']:,.0f}. The tests also confirm the identity A equals one minus d times the annuity, to ten decimals.")

# 10 verified + limits
im, d = canvas("What is verified, and what is not")
d.text((80, 215), "Verified", font=F(36, True), fill=OK)
bullets(d, ["9 actuarial/customer tests: identities, GLM recovering known coefficients, chain-ladder recovering a known pattern, simulation mean vs theory",
            "Live engine run: 3,240 rows ingested, 182 auto-merged, 47 steward-reviewed, 2,915 profiles, portfolio routed to committee by the rules table"], 80, 270, 30)
d.text((80, 520), "Not production-ready", font=F(36, True), fill=WARN)
bullets(d, ["Mortality table, rating factors and the cover-to-income cap are illustrative placeholders",
            "Customers and the policy book are synthetic; reserving triangle is synthetic",
            "Simulation treats lines as independent; VaR is not a regulatory solvency figure",
            "No identity verification, no tuned match thresholds on real data"], 80, 575, 30)
d.text((80, 940), "github.com/herrrickshaw/agentic-insurance-agent", font=F(30), fill=MUT)
add(im, "What is verified: the actuarial identities and recovery tests pass, and the live run ingested, resolved, profiled and routed a portfolio to committee. What is not production ready: the mortality table and rating factors are illustrative, the customers and policy book are synthetic, the reserving triangle is synthetic, the simulation ignores correlation, and nothing here verifies real identities. Replace those inputs with your own data and tables before any real use.")

assemble(scenes, "agentic_insurance_demo_2.mp4")
