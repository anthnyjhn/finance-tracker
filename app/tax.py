"""Australian individual income tax estimate.

Covers: progressive rates (resident / foreign resident / working holiday maker), the low income tax
offset (LITO), Medicare levy with the low-income shade-in, Medicare levy surcharge (MLS), HELP/study
loan compulsory repayments (marginal system from 2025-26), work-related deductions with work-use %
and decline in value (depreciation) for items over $300, and the $1,000 standard deduction (2026-27+).

Rates are kept in config so they can be updated each year; PRESETS fills them in for known years.
Sources: ato.gov.au (tax rates, LITO, Medicare levy reduction, MLS thresholds, study loan thresholds,
tools & equipment deductions, standard deduction).
"""
from __future__ import annotations

import math
from datetime import date

RES_2526 = [{"from": 0, "rate": 0}, {"from": 18200, "rate": 16}, {"from": 45000, "rate": 30},
            {"from": 135000, "rate": 37}, {"from": 190000, "rate": 45}]
RES_2627 = [{"from": 0, "rate": 0}, {"from": 18200, "rate": 15}, {"from": 45000, "rate": 30},
            {"from": 135000, "rate": 37}, {"from": 190000, "rate": 45}]
FOREIGN = [{"from": 0, "rate": 30}, {"from": 135000, "rate": 37}, {"from": 190000, "rate": 45}]
WHM = [{"from": 0, "rate": 15}, {"from": 45000, "rate": 30}, {"from": 135000, "rate": 37}, {"from": 190000, "rate": 45}]

PRESETS = {
    "2025-26": {
        "brackets": {"resident": RES_2526, "foreign": FOREIGN, "whm": WHM},
        "medicare_low": 28011,
        "mls_tiers": [{"from": 101000, "rate": 1.0}, {"from": 118000, "rate": 1.25}, {"from": 158000, "rate": 1.5}],
        "help": {"threshold": 67000, "rate1": 15, "upper": 125000, "base2": 8700, "rate2": 17},
        "standard_deduction": 0,
        "notes": [],
    },
    "2026-27": {
        "brackets": {"resident": RES_2627, "foreign": FOREIGN, "whm": WHM},
        "medicare_low": 28011,
        "mls_tiers": [{"from": 105000, "rate": 1.0}, {"from": 118000, "rate": 1.25}, {"from": 158000, "rate": 1.5}],
        "help": {"threshold": 69528, "rate1": 15, "upper": 129717, "base2": 9028, "rate2": 17},
        "standard_deduction": 1000,
        "notes": ["Medicare levy low-income threshold and the upper Medicare levy surcharge tiers are carried over "
                  "from 2025-26 — check ato.gov.au once the 2026-27 figures are published."],
    },
}

IMMEDIATE_LIMIT = 300.0


def fy_range(year: str) -> tuple[date, date]:
    """'2025-26' -> (2025-07-01, 2026-06-30)."""
    try:
        start = int(str(year)[:4])
    except ValueError:
        today = date.today()
        start = today.year if today.month >= 7 else today.year - 1
    return date(start, 7, 1), date(start + 1, 6, 30)


def income_tax(taxable: float, brackets: list[dict]) -> float:
    bs = sorted(brackets, key=lambda b: b["from"])
    total = 0.0
    for i, b in enumerate(bs):
        lo = b["from"]
        hi = bs[i + 1]["from"] if i + 1 < len(bs) else math.inf
        if taxable > lo:
            total += (min(taxable, hi) - lo) * (b["rate"] or 0) / 100
    return total


def lito(taxable: float) -> float:
    """Low income tax offset: $700, less 5c/$ over $37,500, then 1.5c/$ over $45,000 (nil at $66,667)."""
    if taxable <= 37500:
        return 700.0
    if taxable <= 45000:
        return 700 - (taxable - 37500) * 0.05
    return max(0.0, 325 - (taxable - 45000) * 0.015)


def medicare_levy(taxable: float, rate: float, low: float) -> float:
    if taxable <= low:
        return 0.0
    return min(taxable * rate / 100, (taxable - low) * 0.10)


def mls(income: float, tiers: list[dict]) -> tuple[float, float]:
    rate = 0.0
    for t in sorted(tiers, key=lambda t: t["from"]):
        if income > t["from"]:
            rate = t["rate"]
    return income * rate / 100, rate


def help_repayment(income: float, h: dict) -> float:
    """Marginal system (2025-26 onwards). Top of the scale: 10% of total repayment income."""
    thr, upper = h["threshold"], h["upper"]
    if income <= thr:
        return 0.0
    if income <= upper:
        amount = (income - thr) * h["rate1"] / 100
    else:
        amount = h["base2"] + (income - upper) * h["rate2"] / 100
    return min(amount, income * 0.10) if income > upper else amount


def decline_in_value(cost: float, life: float, bought: date | None, fy_start: date, fy_end: date) -> float:
    """Diminishing value method: base value x (days held / 365) x (200% / effective life), per income year."""
    rate = 2 / life
    if not bought:
        return cost * rate  # assume first full year
    if bought > fy_end:
        return 0.0
    value = cost
    y = bought.year if bought.month >= 7 else bought.year - 1
    while True:
        s, e = date(y, 7, 1), date(y + 1, 6, 30)
        days = min((e - max(s, bought)).days + 1, 365)
        dec = min(value, value * rate * days / 365)
        if s >= fy_start:
            return dec
        value -= dec
        y += 1


def parse(d):
    try:
        return date.fromisoformat(str(d)[:10]) if d else None
    except ValueError:
        return None


def work_claims(items: list[dict], fy_start: date, fy_end: date) -> tuple[list[dict], list[str]]:
    rows, warnings, over = [], [], []
    for i in items:
        amount = i.get("amount") or 0
        pct = 100.0 if i.get("work_pct") is None else float(i["work_pct"])
        life = i.get("life") or None
        bought = parse(i.get("date"))
        if life:
            claim = decline_in_value(amount, float(life), bought, fy_start, fy_end) * pct / 100
            method = f"Depreciated over {life:g} yrs"
            if not bought:
                warnings.append(f"“{i['name']}”: add the purchase date — without it a full first year is assumed.")
        else:
            claim = amount * pct / 100
            method = "Immediate"
            if amount > IMMEDIATE_LIMIT:
                over.append(f"{i['name']} ({amount:,.2f})")
        if pct < 100 and not life and amount <= IMMEDIATE_LIMIT and pct <= 50:
            warnings.append(f"“{i['name']}”: an item under $300 must be used mainly (over 50%) for work to claim it immediately.")
        rows.append({**i, "work_pct": pct, "claim": round(claim, 2), "method": method})
    if over:
        warnings.append(
            "Over $300 and claimed in full: " + ", ".join(over) + ". That's fine for running costs (phone bill, "
            "laundry) or several cheaper items. But a single piece of equipment — or a set — costing over $300 "
            "(a tool, a phone handset) must be depreciated: enter its effective life and purchase date.")
    return rows, warnings


def calculate(items: list[dict], cfg: dict, parked_interest: float = 0.0) -> dict:
    year = cfg.get("year") or "2025-26"
    fy_start, fy_end = fy_range(year)
    residency = cfg.get("residency") or "resident"
    resident = residency == "resident"

    inc = [i for i in items if i["section"] == "income"]
    ded = [i for i in items if i["section"] == "deduction"]
    work = [i for i in items if i["section"] == "work_expense"]

    total_income = sum(i["amount"] or 0 for i in inc)
    work_rows, warnings = work_claims(work, fy_start, fy_end)
    work_itemised = sum(r["claim"] for r in work_rows)
    standard = float(cfg.get("standard_deduction") or 0) if resident else 0.0
    use_standard = standard > work_itemised
    work_claim = standard if use_standard else work_itemised
    other_ded = sum(i["amount"] or 0 for i in ded)
    deductions = work_claim + other_ded
    taxable = max(0.0, math.floor(total_income - deductions))  # the ATO taxes whole dollars
    taxable_exact = total_income - deductions

    gross_tax = income_tax(taxable, cfg.get("brackets") or [])
    offset = min(lito(taxable), gross_tax) if (resident and cfg.get("lito", True)) else 0.0
    net_tax = gross_tax - offset

    mode = cfg.get("medicare_mode") or "auto"
    if mode == "exempt" or (mode == "auto" and not resident):
        medicare = 0.0
    elif mode == "manual":
        medicare = float(cfg.get("medicare") or 0)
    else:
        medicare = medicare_levy(taxable, float(cfg.get("medicare_rate") or 2), float(cfg.get("medicare_low") or 0))

    surcharge, surcharge_rate = (0.0, 0.0)
    if resident and not cfg.get("private_cover", True):
        surcharge, surcharge_rate = mls(taxable, cfg.get("mls_tiers") or [])

    repay_income = taxable + float(cfg.get("help_extra_income") or 0)
    help_amt = help_repayment(repay_income, cfg.get("help") or {}) if cfg.get("help_debt") else 0.0

    total = net_tax + medicare + surcharge + help_amt
    withheld = float(cfg.get("withheld") or 0)

    included = any("family loan" in (i.get("name") or "").lower() for i in inc)
    if parked_interest > 0.005 and not included:
        warnings.insert(0, f"You received {parked_interest:,.2f} interest on money parked with family during {year}. "
                           "Interest from a private loan is assessable income — add it under Annual income if it isn't included.")
    if use_standard:
        warnings.append(f"Your work expenses ({work_itemised:,.2f}) are under the {standard:,.0f} standard deduction, so the standard deduction is used.")
    help_cfg = cfg.get("help") or {}
    if not cfg.get("help_debt") and resident:
        warnings.append("If you have a HELP/HECS (or other study) loan, tick “I have a study loan” — "
                        f"repayments start once repayment income is over {help_cfg.get('threshold', 0):,.0f}.")
    warnings += PRESETS.get(year, {}).get("notes", [])
    if year not in PRESETS:
        warnings.append(f"No built-in rates for {year} — check the brackets and thresholds are current.")

    marginal = next((b["rate"] for b in sorted(cfg.get("brackets") or [], key=lambda b: -b["from"]) if taxable > b["from"]), 0)
    if resident and medicare:
        marginal_note = f"{marginal}% + {cfg.get('medicare_rate', 2):g}% Medicare"
    else:
        marginal_note = f"{marginal}%"
    return {
        "year": year,
        "fy_start": fy_start.isoformat(),
        "fy_end": fy_end.isoformat(),
        "residency": residency,
        "total_income": round(total_income, 2),
        "salary": round((inc[0]["amount"] or 0) if inc else 0, 2),
        "work_rows": work_rows,
        "work_itemised": round(work_itemised, 2),
        "work_expenses": round(work_claim, 2),
        "standard_used": use_standard,
        "other_deductions": round(other_ded, 2),
        "deductions": round(deductions, 2),
        "taxable": taxable,
        "taxable_exact": round(taxable_exact, 2),
        "gross_tax": round(gross_tax, 2),
        "lito": round(offset, 2),
        "income_tax": round(net_tax, 2),
        "medicare": round(medicare, 2),
        "mls": round(surcharge, 2),
        "mls_rate": surcharge_rate,
        "help": round(help_amt, 2),
        "help_income": round(repay_income, 2),
        "total_tax": round(total, 2),
        "withheld": round(withheld, 2),
        "refund": round(withheld - total, 2),
        "effective_rate": (total / total_income) if total_income else 0,
        "marginal_rate": marginal,
        "marginal_note": marginal_note,
        "parked_interest": round(parked_interest, 2),
        "parked_interest_included": included,
        "warnings": warnings,
    }


def preset(year: str, residency: str = "resident") -> dict:
    p = PRESETS[year]
    resident = residency == "resident"
    return {
        "year": year,
        "residency": residency,
        "brackets": p["brackets"][residency],
        "medicare_low": p["medicare_low"],
        "mls_tiers": p["mls_tiers"],
        "help": p["help"],
        "standard_deduction": p["standard_deduction"] if resident else 0,
        "lito": resident,
        "medicare_mode": "auto",
    }
