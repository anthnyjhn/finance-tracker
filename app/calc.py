"""All the calculations from the original spreadsheet, plus growth metrics.

Every function takes plain data (lists of dicts / config dict) so it can be unit-tested
without a database. `build_summary` stitches everything together.
"""
from __future__ import annotations

import calendar
import json
import math
from collections import defaultdict
from datetime import date, timedelta

from . import db
from . import tax as taxcalc

CATEGORY_LABELS = {
    "cash": "Cash",
    "savings": "Savings",
    "investment": "Investments",
    "super": "Superannuation",
    "property": "Properties",
    "other": "Other assets",
    "debt": "Debts",
}


def r2(x: float | None) -> float:
    return round(float(x or 0.0) + 0.0, 2)


def add_months(d: date, months: float) -> date:
    whole = int(math.floor(months))
    frac = months - whole
    y, m = divmod(d.month - 1 + whole, 12)
    y += d.year
    m += 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day) + timedelta(days=round(frac * 30.44))


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------- budget (Fortnight Budget sheet)

def budget(income: float, items: list[dict]) -> dict:
    fixed = [i for i in items if i["kind"] == "fixed"]
    pct = [i for i in items if i["kind"] == "percent"]
    fixed_total = sum(i["value"] or 0 for i in fixed)
    remainder = income - fixed_total
    pct_rows = [{**i, "amount": r2(remainder * (i["value"] or 0) / 100)} for i in pct]
    pct_total = sum(i["value"] or 0 for i in pct)
    allocated = fixed_total + sum(remainder * (i["value"] or 0) / 100 for i in pct)
    return {
        "income": r2(income),
        "fixed": fixed,
        "fixed_total": r2(fixed_total),
        "remainder": r2(remainder),
        "percent": pct_rows,
        "percent_total": round(pct_total, 4),
        "allocated": r2(allocated),
        "unallocated": r2(income - allocated),
        "balanced": abs(income - allocated) < 0.005,
    }


# ---------------------------------------------------------------- loans (Van / Outlander logs)

FREQ_MONTHS = {"weekly": 12 / 52, "fortnightly": 12 / 26, "monthly": 1.0}


def loan(ln: dict, sources: list[dict], payments: list[dict], today: date) -> dict:
    principal = sum(s["amount"] or 0 for s in sources)
    interest = ln.get("interest") or 0
    total = principal + interest
    paid = sum(p["amount"] or 0 for p in payments)
    payable = total - paid
    inst = ln.get("installment") or 0
    periods_left = math.ceil(round(payable / inst, 9)) if inst > 0 and payable > 0 else 0
    freq = ln.get("frequency") or "monthly"
    payoff = add_months(today, periods_left * FREQ_MONTHS.get(freq, 1.0)) if periods_left else None

    # Actual repayment pace over the last 6 months, from the payment log.
    since = today - timedelta(days=182)
    recent = sum(p["amount"] or 0 for p in payments if (parse_date(p["date"]) or date.min) >= since)
    pace = recent / 6
    pace_payoff = add_months(today, payable / pace) if pace > 0 and payable > 0 else None

    dates = [parse_date(p["date"]) for p in payments if parse_date(p["date"])]
    src_rows = []
    for s in sources:
        share = (s["amount"] or 0) / total if total else 0
        src_rows.append({
            **s,
            "share": share,
            "per_installment": r2(inst * share),
            "returned": r2(paid * share),
            "outstanding": r2(payable * share),
        })
    return {
        **ln,
        "principal": r2(principal),
        "total": r2(total),
        "interest_share": (interest / total) if total else 0,
        "paid": r2(paid),
        "payable": r2(payable),
        "progress": (paid / total) if total else 0,
        "periods_left": periods_left,
        "payoff_date": payoff.isoformat() if payoff else None,
        "monthly_pace": r2(pace),
        "pace_payoff_date": pace_payoff.isoformat() if pace_payoff else None,
        "last_payment": max(dates).isoformat() if dates else None,
        "payment_count": len(payments),
        "sources": src_rows,
    }


# ---------------------------------------------------------------- money parking

def parked(p: dict, entries: list[dict], today: date) -> dict:
    balance = sum(e["amount"] or 0 for e in entries)
    interest = sum(e["amount"] or 0 for e in entries if e.get("kind") == "interest")
    year_ago = today - timedelta(days=365)
    interest_12m = sum(e["amount"] or 0 for e in entries
                       if e.get("kind") == "interest" and (parse_date(e["date"]) or date.min) >= year_ago)
    deposits = sum(e["amount"] or 0 for e in entries if e.get("kind") != "interest")
    return {
        **p,
        "balance": r2(balance),
        "deposited": r2(deposits),
        "interest": r2(interest),
        "interest_12m": r2(interest_12m),
        "yield_estimate": (interest_12m / deposits) if deposits > 0 else 0,
    }


# ---------------------------------------------------------------- holdings (Crypto sheet)

def holding(h: dict, buys: list[dict]) -> dict:
    invested = sum(b["amount"] or 0 for b in buys)
    value = h.get("current_value") or 0
    by_platform: dict[str, float] = defaultdict(float)
    for b in buys:
        by_platform[b.get("platform") or "—"] += b["amount"] or 0
    units = sum(b.get("units") or 0 for b in buys)
    return {
        **h,
        "invested": r2(invested),
        "value": r2(value),
        "profit": r2(value - invested),
        "profit_pct": ((value - invested) / invested) if invested else 0,
        "by_platform": {k: r2(v) for k, v in by_platform.items()},
        "units_bought": units,
        "avg_cost": (invested / units) if units else None,
    }


# ---------------------------------------------------------------- net worth (Networth Calculator)

def networth(accounts: list[dict], holdings: list[dict], parks: list[dict], loans: list[dict]) -> dict:
    lines = []  # {group, name, amount, spendable}
    for a in accounts:
        amt = a["balance"] or 0
        if a["category"] == "debt":
            amt = -abs(amt)
        lines.append({"group": CATEGORY_LABELS.get(a["category"], "Other assets"), "name": a["name"],
                      "amount": amt, "spendable": bool(a["spendable"]), "kind": "account", "id": a["id"]})
    for h in holdings:
        lines.append({"group": "Investments", "name": h["name"], "amount": h["value"], "spendable": True,
                      "kind": "holding", "id": h["id"]})
    for p in parks:
        lines.append({"group": "Money parked", "name": p["name"], "amount": p["balance"], "spendable": True,
                      "kind": "parked", "id": p["id"]})
    for ln in loans:
        lines.append({"group": "Lent", "name": ln["name"], "amount": ln["payable"], "spendable": True,
                      "kind": "loan", "id": ln["id"]})

    groups: dict[str, float] = {}
    for line in lines:
        groups[line["group"]] = groups.get(line["group"], 0) + line["amount"]
    total = sum(l["amount"] for l in lines)
    spendable = sum(l["amount"] for l in lines if l["spendable"])
    assets = sum(l["amount"] for l in lines if l["amount"] > 0)
    liabilities = -sum(l["amount"] for l in lines if l["amount"] < 0)
    order = ["Savings", "Investments", "Superannuation", "Money parked", "Cash", "Lent", "Properties",
             "Other assets", "Debts"]
    return {
        "lines": [{**l, "amount": r2(l["amount"])} for l in lines],
        "groups": [{"name": g, "amount": r2(groups[g])} for g in order if g in groups],
        "total": r2(total),
        "spendable": r2(spendable),
        "assets": r2(assets),
        "liabilities": r2(liabilities),
    }


# ---------------------------------------------------------------- goals

def goal_progress(g: dict, current: float, monthly_savings: float, today: date) -> dict:
    target = g["target"] or 0
    left = target - current
    months_left = (left / monthly_savings) if monthly_savings else None
    eta = None
    if left <= 0:
        eta = today.isoformat()
    elif months_left is not None and months_left > 0:
        eta = add_months(today, months_left).isoformat()
    needed = None
    td = parse_date(g.get("target_date"))
    if td and left > 0:
        months = max((td - today).days / 30.44, 0.0001)
        needed = r2(left / months)
    return {
        **g,
        "current": r2(current),
        "left": r2(left),
        "progress": (current / target) if target else 0,
        "months_left": months_left,
        "eta": eta,
        "monthly_needed": needed,
        "achieved": left <= 0,
    }


# ---------------------------------------------------------------- forecast (Networth Forecast)

def forecast(networth_now: float, spendable_now: float, recurring: list[dict], cfg: dict, today: date) -> dict:
    income = sum(r["amount"] or 0 for r in recurring if r["kind"] == "income")
    expenses = sum(r["amount"] or 0 for r in recurring if r["kind"] == "expense")
    surplus = income - expenses
    years = int(cfg.get("forecast_years") or 10)
    super_rate = float(cfg.get("super_rate") or 0)
    growth = float(cfg.get("growth_rate") or 0) / 100
    rows = []
    nw, cash = networth_now, spendable_now
    for y in range(1, years + 1):
        nw = nw * (1 + growth) + surplus * 12 + (income * super_rate if y == 1 else 0)
        cash = cash * (1 + growth) + surplus * 12
        rows.append({"year": y, "date": add_months(today, 12 * y).isoformat(), "networth": r2(nw), "cash": r2(cash)})
    return {
        "income": r2(income),
        "expenses": r2(expenses),
        "surplus": r2(surplus),
        "savings_rate": (surplus / income) if income else 0,
        "rows": rows,
    }


# ---------------------------------------------------------------- tax (Tax Return Calculator, AU) -> see tax.py

def parked_interest_in(entries: list[dict], start: date, end: date) -> float:
    return sum(e["amount"] or 0 for e in entries
               if e.get("kind") == "interest" and start <= (parse_date(e["date"]) or date.min) <= end)


# ---------------------------------------------------------------- cash flow (transactions)

def cashflow(txns: list[dict], today: date, months: int = 12) -> dict:
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    keys.reverse()
    per = {k: {"month": k, "income": 0.0, "expenses": 0.0} for k in keys}
    cats_month: dict[str, float] = defaultdict(float)
    cats_year: dict[str, float] = defaultdict(float)
    cur = keys[-1]
    for t in txns:
        k = (t["date"] or "")[:7]
        if k not in per:
            continue
        amt = t["amount"] or 0
        if (t.get("category") or "").lower() == "transfer":
            continue
        if amt >= 0:
            per[k]["income"] += amt
        else:
            per[k]["expenses"] += -amt
            cat = t.get("category") or "Uncategorised"
            cats_year[cat] += -amt
            if k == cur:
                cats_month[cat] += -amt
    rows = []
    for k in keys:
        r = per[k]
        net = r["income"] - r["expenses"]
        rows.append({"month": k, "income": r2(r["income"]), "expenses": r2(r["expenses"]), "net": r2(net),
                     "savings_rate": (net / r["income"]) if r["income"] else None})
    active = [r for r in rows if r["income"] or r["expenses"]]
    avg_exp = sum(r["expenses"] for r in active) / len(active) if active else 0
    avg_inc = sum(r["income"] for r in active) / len(active) if active else 0
    return {
        "months": rows,
        "this_month": rows[-1],
        "avg_income": r2(avg_inc),
        "avg_expenses": r2(avg_exp),
        "categories_month": sorted(([k, r2(v)] for k, v in cats_month.items()), key=lambda x: -x[1]),
        "categories_year": sorted(([k, r2(v)] for k, v in cats_year.items()), key=lambda x: -x[1]),
    }


# ---------------------------------------------------------------- history / growth

def growth(snaps: list[dict], today: date) -> dict:
    s = sorted((x for x in snaps if parse_date(x["date"])), key=lambda x: x["date"])
    if not s:
        return {}
    latest = s[-1]

    def at_or_before(d: date):
        best = None
        for x in s:
            if parse_date(x["date"]) <= d:
                best = x
        return best

    def delta(ref):
        if not ref or ref is latest:
            return None
        diff = latest["networth"] - ref["networth"]
        return {"from": ref["date"], "change": r2(diff),
                "pct": (diff / ref["networth"]) if ref["networth"] else None}

    out = {
        "previous": delta(s[-2] if len(s) > 1 else None),
        "month": delta(at_or_before(today - timedelta(days=30)) or s[0]),
        "year": delta(at_or_before(today - timedelta(days=365)) or s[0]),
        "ytd": delta(at_or_before(date(today.year, 1, 1)) or s[0]),
    }
    first = s[0]
    days = (parse_date(latest["date"]) - parse_date(first["date"])).days
    if days >= 28:
        out["avg_monthly"] = r2((latest["networth"] - first["networth"]) / (days / 30.44))
    return out


# ---------------------------------------------------------------- everything

def build_summary(conn, today: date | None = None, record_snapshot: bool = True) -> dict:
    today = today or date.today()
    cfg = db.get_config(conn)
    L = lambda t, **f: db.list_rows(conn, t, **f)  # noqa: E731

    loans = [loan(ln, L("loan_sources", loan_id=ln["id"]), L("loan_payments", loan_id=ln["id"]), today)
             for ln in L("loans")]
    parks = [parked(p, L("parked_entries", parked_id=p["id"]), today) for p in L("parked")]
    holds = [holding(h, L("holding_buys", holding_id=h["id"])) for h in L("holdings")]
    accounts = L("accounts")
    nw = networth(accounts, holds, parks, loans)

    if record_snapshot:
        conn.execute(
            "INSERT INTO snapshots(date, networth, spendable, breakdown, notes) VALUES(?,?,?,?,?) "
            "ON CONFLICT(date) DO UPDATE SET networth=excluded.networth, spendable=excluded.spendable, "
            "breakdown=excluded.breakdown",
            (today.isoformat(), nw["total"], nw["spendable"],
             json.dumps({g["name"]: g["amount"] for g in nw["groups"]}), "Auto"))
    snaps = L("snapshots")

    monthly_savings = float(cfg.get("monthly_savings") or 0)
    linked = {
        "networth": nw["total"],
        "spendable": nw["spendable"],
        **{f"account:{a['id']}": a["balance"] or 0 for a in accounts},
        **{f"holding:{h['id']}": h["value"] for h in holds},
        **{f"parked:{p['id']}": p["balance"] for p in parks},
    }
    goals = [goal_progress(g, linked.get(g.get("link") or "", g.get("current") or 0), monthly_savings, today)
             for g in L("goals")]

    txns = L("transactions")
    cf = cashflow(txns, today)
    fc = forecast(nw["total"], nw["spendable"], L("recurring"), cfg, today)
    monthly_exp = cf["avg_expenses"] or fc["expenses"]
    liquid = sum(l["amount"] for l in nw["lines"] if l["group"] in ("Cash", "Savings"))

    return {
        "today": today.isoformat(),
        "config": cfg,
        "networth": nw,
        "loans": loans,
        "parked": parks,
        "holdings": holds,
        "holdings_total": {
            "invested": r2(sum(h["invested"] for h in holds)),
            "value": r2(sum(h["value"] for h in holds)),
            "profit": r2(sum(h["profit"] for h in holds)),
        },
        "lent_total": r2(sum(l["payable"] for l in loans)),
        "goals": goals,
        "budget": budget(float(cfg.get("budget_income") or 0), L("budget_items")),
        "forecast": fc,
        "tax": taxcalc.calculate(L("tax_items"), cfg.get("tax") or {},
                                 parked_interest_in(L("parked_entries"), *taxcalc.fy_range((cfg.get("tax") or {}).get("year")))),
        "cashflow": cf,
        "snapshots": snaps,
        "growth": growth(snaps, today),
        "health": {
            "liquid": r2(liquid),
            "monthly_expenses": r2(monthly_exp),
            "emergency_months": (liquid / monthly_exp) if monthly_exp else None,
            "debt_ratio": (nw["liabilities"] / nw["assets"]) if nw["assets"] else 0,
        },
    }
