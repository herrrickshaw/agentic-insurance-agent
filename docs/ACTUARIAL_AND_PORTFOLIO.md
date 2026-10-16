# Customer data, portfolio risk and actuarial maths

## Workflows (BPMN + DMN, `models/`)
| Process | What it does | Human tasks |
|---|---|---|
| `customer_onboarding` | Ingest CSV/JSONL → validate/normalise → hash PII → entity resolution → build 360 profiles and segments. Rejects above 20% stop the batch and notify the data owner. | Data steward reviews candidate duplicates |
| `portfolio_risk_review` | Exposure, VaR/TVaR, concentration → reserving → `portfolio-limits` DMN (ok / review / escalate) → committee → guideline update | Underwriting committee (CRO notified on escalate) |
| `policy_issuance` (extended) | Adds an **actuarial risk-rating** step; underwriting DMN consumes the rating class; term-life premium comes from the equivalence principle | Underwriter on refer |

## Code (`insurance/`)
- `customers/validate.py`: format and checksum validation (PAN pattern, Aadhaar Verhoeff checksum, mobile, pincode, multi-format dates). Aadhaar/PAN are stored as salted SHA-256 plus last four digits only. **This is not identity verification**; real KYC needs an authorised provider.
- `customers/ingest.py`: batch ingest with a reject table (row number, reasons) and source-channel lineage.
- `customers/resolve.py`: blocking on government-ID hash, phone, email, DOB+initial; Jaro-Winkler name score normalised over the evidence present; ≥0.92 auto-merge, 0.75–0.92 steward queue; name+DOB alone can never auto-merge; union-find clusters; survivorship = latest non-empty value.
- `customers/profile.py`: 360 profile (age, tenure, cover-to-income, claims, claim ratio, late payments, completeness, KYC readiness) and a value × risk segment.
- `underwriting/risk.py`: life rating (mortality multiple, financial-underwriting cap, referral flags). **Factors are illustrative placeholders.**
- `actuarial/mortality.py`: Gompertz–Makeham table, commutation functions (D, N, C, M), term assurance and annuities, net and gross premium (initial/renewal expense, commission), prospective reserve.
- `actuarial/glm.py`: Poisson (log link, exposure offset) frequency and Gamma severity GLMs by IRLS, pure premium.
- `actuarial/reserving.py`: chain-ladder and Bornhuetter–Ferguson.
- `actuarial/credibility.py`: Bühlmann–Straub.
- `actuarial/simulation.py`: collective risk model (Poisson / negative binomial frequency, lognormal or empirical severity), VaR/TVaR, per-claim excess-of-loss and quota-share reinsurance.
- `portfolio/risk.py`: exposure and concentration (state share, HHI, top-10 customers), per-policy expected loss, simulated portfolio VaR/TVaR (gross and net of reinsurance), reserving run.

## How it was verified
- `insurance/tests`: 9 tests. Actuarial results are checked against identities, not just "runs": `A_x = 1 − d·ä_x`, the annuity recursion, the equivalence principle, reserve = 0 at issue and at expiry, GLM recovers known coefficients from simulated data, chain-ladder recovers a known development pattern, simulated aggregate mean matches λ·E[X].
- Live run on Operaton (3,240 synthetic rows): 3,142 accepted, 98 rejected with reasons, 182 auto-merged, 47 sent to a steward task, 2,915 golden profiles, portfolio review routed to the committee task by the DMN, non-smoker vs smoker term-life premiums priced from the table.

## Limits you must know about
- **Mortality table is illustrative** (Gompertz–Makeham parameters I chose), not IALM 2012-14 or any experience table. Premiums are demonstrations of the method, not usable rates. Load a real table with `MortalityTable.from_qx`.
- Underwriting multiples, occupation loadings and the 25× cover-to-income cap are placeholders.
- Customers and the policy book are **synthetic**; the policy book is seeded by the worker only when `DEMO_SEED=1`. Production needs the policy-admin feed.
- Portfolio simulation treats lines as independent (no catastrophe or correlation), uses fixed assumed frequencies/severities per product, and a single own-funds figure. VaR here is not a regulatory solvency calculation (not IRDAI RBC or Solvency II).
- The reserving triangle is **synthetic** because the ledger holds no payment-development history. Chain-ladder has no Mack standard error yet.
- No GLM is fitted on the synthetic policy book; the GLM module is tested on simulated data with known truth.
- Entity-resolution thresholds are untuned; duplicates in the synthetic data are injected, so match quality on real data is unknown.
