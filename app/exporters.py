"""Export to standard formats (CSV, QIF, OFX, XLSX, JSON) and import CSV / OFX / QIF transactions.

Every money movement in the app (transactions, loan money lent and repaid, money parked,
investment purchases, plus an opening balance per account) is flattened into one ledger
so other finance apps can rebuild the same balances.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date, datetime
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from . import calc, db

# Account types used by QIF/OFX.
QIF_TYPES = {"cash": "Cash", "bank": "Bank", "asset": "Oth A", "liability": "Oth L", "invest": "Oth A"}


def ledger(conn, include_balances: bool = True) -> list[dict]:
    """Flatten everything into rows: date, account, account_type, payee, category, memo, amount."""
    today = date.today().isoformat()
    rows: list[dict] = []
    L = lambda t, **f: db.list_rows(conn, t, **f)  # noqa: E731
    acct_types = {}

    for a in L("accounts"):
        t = {"cash": "cash", "savings": "bank", "debt": "liability"}.get(a["category"], "asset")
        if a["category"] == "investment":
            t = "invest"
        acct_types[a["name"]] = t
        if include_balances and a["balance"]:
            amt = -abs(a["balance"]) if a["category"] == "debt" else a["balance"]
            rows.append(dict(date=today, account=a["name"], payee="Opening balance",
                             category="Opening Balance", memo=a.get("notes") or "", amount=amt))

    for t in L("transactions"):
        acct = t.get("account") or "Cash"
        acct_types.setdefault(acct, "bank")
        rows.append(dict(date=t["date"], account=acct, payee=t.get("description") or "",
                         category=t.get("category") or "", memo=t.get("notes") or "", amount=t["amount"] or 0))

    for ln in L("loans"):
        acct = f"Loan: {ln['name']}"
        acct_types[acct] = "asset"
        first = min((p["date"] for p in L("loan_payments", loan_id=ln["id"]) if p["date"]), default=today)
        for s in L("loan_sources", loan_id=ln["id"]):
            rows.append(dict(date=first, account=acct, payee=ln.get("borrower") or ln["name"],
                             category="Transfer", memo=f"Lent from {s['fund']}", amount=s["amount"] or 0))
        if ln.get("interest"):
            rows.append(dict(date=first, account=acct, payee=ln.get("borrower") or ln["name"],
                             category="Interest Income", memo="Interest", amount=ln["interest"]))
        for p in L("loan_payments", loan_id=ln["id"]):
            rows.append(dict(date=p["date"], account=acct, payee=ln.get("borrower") or ln["name"],
                             category="Transfer", memo=p.get("notes") or "Repayment received",
                             amount=-(p["amount"] or 0)))

    for pk in L("parked"):
        acct = f"Parked: {pk['name']}"
        acct_types[acct] = "asset"
        for e in L("parked_entries", parked_id=pk["id"]):
            rows.append(dict(date=e["date"], account=acct, payee=pk.get("holder") or pk["name"],
                             category="Interest Income" if e.get("kind") == "interest" else "Transfer",
                             memo=" ".join(x for x in [e.get("description"), e.get("wallet")] if x),
                             amount=e["amount"] or 0))

    for h in L("holdings"):
        acct = f"{h['name']} ({h.get('symbol') or h.get('kind') or 'holding'})"
        acct_types[acct] = "invest"
        buys = L("holding_buys", holding_id=h["id"])
        for b in buys:
            rows.append(dict(date=b["date"], account=acct, payee=b.get("platform") or "",
                             category="Transfer", memo=f"Buy {h['name']}" + (f" {b['units']} units" if b.get("units") else ""),
                             amount=b["amount"] or 0))
        if include_balances:
            diff = (h.get("current_value") or 0) - sum(b["amount"] or 0 for b in buys)
            if abs(diff) >= 0.005:
                rows.append(dict(date=today, account=acct, payee="Market value",
                                 category="Unrealized Gain/Loss", memo="Revaluation to current value", amount=round(diff, 2)))

    for r in rows:
        r["account_type"] = acct_types.get(r["account"], "bank")
        r["amount"] = round(float(r["amount"]), 2)
        r["id"] = hashlib.sha1(f"{r['account']}|{r['date']}|{r['payee']}|{r['memo']}|{r['amount']}|{len(rows)}".encode()).hexdigest()[:16]
    # make ids unique & stable for identical rows
    seen: dict[str, int] = {}
    for r in rows:
        n = seen.get(r["id"], 0)
        seen[r["id"]] = n + 1
        if n:
            r["id"] = f"{r['id']}-{n}"
    rows.sort(key=lambda r: (r["account"], r["date"] or ""))
    return rows


# ---------------------------------------------------------------- CSV

def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Account", "Payee", "Category", "Memo", "Amount", "Inflow", "Outflow"])
    for r in rows:
        a = r["amount"]
        w.writerow([r["date"], r["account"], r["payee"], r["category"], r["memo"], f"{a:.2f}",
                    f"{a:.2f}" if a > 0 else "", f"{-a:.2f}" if a < 0 else ""])
    return buf.getvalue()


# ---------------------------------------------------------------- QIF

def _qif_date(s: str) -> str:
    d = date.fromisoformat(s[:10])
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def to_qif(rows: list[dict]) -> str:
    out = ["!Option:AutoSwitch"]
    accounts: dict[str, list[dict]] = {}
    for r in rows:
        accounts.setdefault(r["account"], []).append(r)
    out.append("!Account")
    for name, rs in accounts.items():
        out += [f"N{name}", f"T{QIF_TYPES[rs[0]['account_type']]}", "^"]
    out.append("!Clear:AutoSwitch")
    for name, rs in accounts.items():
        qtype = QIF_TYPES[rs[0]["account_type"]]
        out += ["!Account", f"N{name}", f"T{qtype}", "^", f"!Type:{qtype}"]
        for r in rs:
            out.append(f"D{_qif_date(r['date'])}")
            out.append(f"T{r['amount']:.2f}")
            if r["payee"]:
                out.append(f"P{r['payee']}")
            if r["memo"]:
                out.append(f"M{r['memo']}")
            if r["category"]:
                out.append(f"L{r['category']}")
            out.append("^")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- OFX 2.x (XML)

def to_ofx(rows: list[dict], currency: str = "AUD") -> str:
    now = datetime.now().strftime("%Y%m%d%H%M%S")
    accounts: dict[str, list[dict]] = {}
    for r in rows:
        accounts.setdefault(r["account"], []).append(r)
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        '<?OFX OFXHEADER="200" VERSION="220" SECURITY="NONE" OLDFILEUID="NONE" NEWFILEUID="NONE"?>',
        "<OFX>",
        "<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>"
        f"<DTSERVER>{now}</DTSERVER><LANGUAGE>ENG</LANGUAGE></SONRS></SIGNONMSGSRSV1>",
        "<BANKMSGSRSV1>",
    ]
    for i, (name, rs) in enumerate(accounts.items(), 1):
        acct_id = re.sub(r"[^A-Za-z0-9]", "", name)[:22] or f"ACCT{i}"
        acct_type = "SAVINGS" if rs[0]["account_type"] in ("bank", "cash") else "MONEYMRKT"
        if rs[0]["account_type"] == "liability":
            acct_type = "CREDITLINE"
        dates = sorted(r["date"] for r in rs)
        bal = round(sum(r["amount"] for r in rs), 2)
        parts.append(f"<STMTTRNRS><TRNUID>{i}</TRNUID><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>")
        parts.append(f"<STMTRS><CURDEF>{escape(currency)}</CURDEF>")
        parts.append(f"<BANKACCTFROM><BANKID>FINANCETRACKER</BANKID><ACCTID>{escape(acct_id)}</ACCTID>"
                     f"<ACCTTYPE>{acct_type}</ACCTTYPE></BANKACCTFROM>")
        parts.append(f"<BANKTRANLIST><DTSTART>{dates[0].replace('-', '')}</DTSTART>"
                     f"<DTEND>{dates[-1].replace('-', '')}</DTEND>")
        for r in rs:
            ttype = "CREDIT" if r["amount"] >= 0 else "DEBIT"
            if r["category"] == "Interest Income":
                ttype = "INT"
            memo = " | ".join(x for x in [r["category"], r["memo"]] if x)
            parts.append(
                f"<STMTTRN><TRNTYPE>{ttype}</TRNTYPE><DTPOSTED>{r['date'].replace('-', '')}</DTPOSTED>"
                f"<TRNAMT>{r['amount']:.2f}</TRNAMT><FITID>{r['id']}</FITID>"
                f"<NAME>{escape((r['payee'] or r['category'] or 'Transaction')[:32])}</NAME>"
                f"<MEMO>{escape(memo[:255])}</MEMO></STMTTRN>")
        parts.append("</BANKTRANLIST>")
        parts.append(f"<LEDGERBAL><BALAMT>{bal:.2f}</BALAMT><DTASOF>{now}</DTASOF></LEDGERBAL>")
        parts.append(f"<!-- account name: {escape(name).replace('--', '-')} -->")
        parts.append("</STMTRS></STMTTRNRS>")
    parts += ["</BANKMSGSRSV1>", "</OFX>"]
    return "\n".join(parts) + "\n"


# ---------------------------------------------------------------- XLSX

def to_xlsx(conn) -> bytes:
    s = calc.build_summary(conn, record_snapshot=False)
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    bold = Font(bold=True)
    money = '"$"#,##0.00'

    def table(ws, start_row, headers, rows, money_cols=()):
        for j, h in enumerate(headers, 1):
            ws.cell(start_row, j, h).font = bold
        for i, row in enumerate(rows, start_row + 1):
            for j, v in enumerate(row, 1):
                c = ws.cell(i, j, v)
                if j - 1 in money_cols:
                    c.number_format = money
        return start_row + len(rows) + 2

    nw = s["networth"]
    r = table(ws, 1, ["Net worth overview", "Amount"],
              [[g["name"], g["amount"]] for g in nw["groups"]] + [["TOTAL NET WORTH", nw["total"]],
                                                                   ["SPENDABLE (all-in)", nw["spendable"]]], (1,))
    r = table(ws, r, ["Goal", "Target", "Current", "Left", "ETA"],
              [[g["name"], g["target"], g["current"], g["left"], g["eta"]] for g in s["goals"]], (1, 2, 3))
    r = table(ws, r, ["Loan", "Total", "Paid", "Payable", "Periods left", "Payoff date"],
              [[l["name"], l["total"], l["paid"], l["payable"], l["periods_left"], l["payoff_date"]] for l in s["loans"]],
              (1, 2, 3))
    r = table(ws, r, ["Forecast year", "Date", "Net worth", "Cash"],
              [[f"Year {x['year']}", x["date"], x["networth"], x["cash"]] for x in s["forecast"]["rows"]], (2, 3))
    t = s["tax"]
    table(ws, r, ["Tax estimate", "Amount"],
          [["Total income", t["total_income"]], ["Deductions", t["deductions"]], ["Taxable income", t["taxable"]],
           ["Income tax", t["income_tax"]], ["Medicare levy", t["medicare"]], ["Tax withheld", t["withheld"]],
           ["Estimated refund", t["refund"]]], (1,))

    for name in db.TABLES:
        rows = db.list_rows(conn, name)
        sh = wb.create_sheet(name[:31])
        cols = ["id", *db.TABLES[name]["cols"]]
        table(sh, 1, cols, [[row.get(c) for c in cols] for row in rows])

    led = wb.create_sheet("Ledger")
    table(led, 1, ["Date", "Account", "Payee", "Category", "Memo", "Amount"],
          [[x["date"], x["account"], x["payee"], x["category"], x["memo"], x["amount"]] for x in ledger(conn)], (5,))

    for sh in wb.worksheets:
        for col in sh.columns:
            width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
            sh.column_dimensions[get_column_letter(col[0].column)].width = min(max(width + 2, 10), 50)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- imports

def _num(s) -> float | None:
    if s is None:
        return None
    s = str(s).strip().replace(",", "").replace("$", "")
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def _date(s: str, dayfirst: bool = True) -> str | None:
    s = (s or "").strip().replace("'", "/")
    if not s:
        return None
    fmts = ["%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"]
    fmts += (["%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%m/%d/%Y", "%m/%d/%y"] if dayfirst
             else ["%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y"])
    fmts += ["%d %b %Y", "%d %B %Y", "%b %d, %Y"]
    s = s.split("T")[0].split(" 00:")[0].strip()
    for f in fmts:
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            continue
    return None


def parse_csv(text: str, dayfirst: bool = True) -> list[dict]:
    """Accepts this app's CSV and most bank / app exports (Date + Amount or Inflow/Outflow or Debit/Credit)."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames:
        return []
    norm = {f: (f or "").strip().lower() for f in reader.fieldnames}

    def pick(*names):
        for f, n in norm.items():
            if n in names:
                return f
        return None

    c_date = pick("date", "transaction date", "posted date", "posting date")
    c_amt = pick("amount", "value", "amount (aud)")
    c_in = pick("inflow", "credit", "deposit", "money in")
    c_out = pick("outflow", "debit", "withdrawal", "money out")
    c_desc = pick("payee", "description", "name", "narrative", "details", "merchant")
    c_cat = pick("category", "category name")
    c_acct = pick("account", "account name")
    c_memo = pick("memo", "notes", "note", "reference")
    if not c_date or not (c_amt or c_in or c_out):
        raise ValueError("CSV needs a Date column and an Amount (or Inflow/Outflow, Credit/Debit) column")
    out = []
    for row in reader:
        d = _date(row.get(c_date, ""), dayfirst)
        if not d:
            continue
        if c_amt and _num(row.get(c_amt)) is not None:
            amt = _num(row.get(c_amt))
        else:
            amt = (_num(row.get(c_in)) or 0) - abs(_num(row.get(c_out)) or 0)
        out.append({"date": d, "amount": round(amt, 2), "description": (row.get(c_desc) or "").strip() if c_desc else "",
                    "category": (row.get(c_cat) or "").strip() if c_cat else "",
                    "account": (row.get(c_acct) or "").strip() if c_acct else "",
                    "notes": (row.get(c_memo) or "").strip() if c_memo else ""})
    return out


def parse_ofx(text: str) -> list[dict]:
    """Works for both SGML (OFX 1.x / QFX) and XML (OFX 2.x) bank statements."""
    out = []
    for block in re.findall(r"<STMTTRN>(.*?)</STMTTRN>", text, re.S | re.I):
        def tag(name):
            m = re.search(rf"<{name}>([^<\r\n]*)", block, re.I)
            return m.group(1).strip() if m else ""
        d = tag("DTPOSTED")[:8]
        amt = _num(tag("TRNAMT"))
        if len(d) != 8 or amt is None:
            continue
        out.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}", "amount": round(amt, 2),
                    "description": tag("NAME") or tag("PAYEE") or tag("MEMO"),
                    "category": "", "account": "", "notes": tag("MEMO")})
    return out


def parse_qif(text: str, dayfirst: bool = False) -> list[dict]:
    out, cur, account = [], {}, ""
    in_account_block = False
    for line in text.splitlines():
        if not line:
            continue
        if line.startswith("!"):
            in_account_block = line.strip().lower() == "!account"
            continue
        code, val = line[0], line[1:].strip()
        if code == "^":
            if in_account_block:
                in_account_block = False
            elif cur.get("date") and cur.get("amount") is not None:
                out.append({"description": "", "category": "", "notes": "", "account": account, **cur})
            cur = {}
            continue
        if in_account_block:
            if code == "N":
                account = val
            continue
        if code == "D":
            cur["date"] = _date(val, dayfirst)
        elif code in ("T", "U"):
            cur["amount"] = _num(val)
        elif code == "P":
            cur["description"] = val
        elif code == "M":
            cur["notes"] = val
        elif code == "L":
            cur["category"] = val
    return out
