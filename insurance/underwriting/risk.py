"""Underwriting risk rating. FACTORS ARE ILLUSTRATIVE PLACEHOLDERS: replace with reinsurer manuals / your
own mortality & morbidity experience studies before any real use."""

SMOKER_MULTIPLE = 1.8
OCCUPATION_MULTIPLE = {"miner": 1.5, "pilot": 1.3, "driver": 1.15, "construction": 1.25, "security": 1.2}
MAX_COVER_TO_INCOME = 25            # illustrative financial-underwriting cap
AGE_REFER = 65


def life_rating(age, smoker, occupation, annual_income, sum_insured):
    mult = 1.0 * (SMOKER_MULTIPLE if smoker else 1.0) * OCCUPATION_MULTIPLE.get((occupation or "").lower(), 1.0)
    flags = []
    if annual_income and sum_insured / annual_income > MAX_COVER_TO_INCOME:
        flags.append(f"cover/income {sum_insured / annual_income:.0f}x exceeds {MAX_COVER_TO_INCOME}x (financial underwriting)")
    if not annual_income:
        flags.append("income not declared")
    if age >= AGE_REFER:
        flags.append(f"age {age} >= {AGE_REFER}")
    if mult > 3:
        cls = "decline"
    elif flags:
        cls = "refer"
    elif mult <= 1.0:
        cls = "preferred"
    elif mult <= 1.5:
        cls = "standard"
    else:
        cls = "substandard"
    return {"mortality_multiple": round(mult, 3), "rating_class": cls, "flags": flags}
