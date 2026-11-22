"""Checks the app reproduces the numbers from the original Financial_Calculator.xlsx."""
import json
import os
from datetime import date

import pytest

os.environ["FINANCE_DB"] = "/tmp/finance-test.db"

from fastapi.testclient import TestClient  # noqa: E402

from app import calc, db, exporters, main  # noqa: E402
from app import tax  # noqa: E402

TODAY = date(2026, 11, 22)


@pytest.fixture()
def conn():
    if os.path.exists(os.environ["FINANCE_DB"]):
        os.remove(os.environ["FINANCE_DB"])
    db.DB_PATH = __import__("pathlib").Path(os.environ["FINANCE_DB"])
    db.init_db()
    with db.session() as c:
        db.restore_all(c, json.loads(main.SEED.read_text()))
    with db.session() as c:
        yield c


def test_spreadsheet_numbers(conn):
    s = calc.build_summary(conn, TODAY)
    b = s["budget"]
    assert b["fixed_total"] == 830 and b["balanced"]
    assert [p["amount"] for p in b["percent"]] == [971.53, 323.84]

    van, outl, beth = s["loans"]
    assert van["total"] == 7335 and van["paid"] == 4764.4 and van["payable"] == 2570.6 and van["periods_left"] == 12
    assert outl["paid"] == 5500 and outl["payable"] == 2730 and outl["periods_left"] == 11
    assert beth["payable"] == 150
    assert s["lent_total"] == 5450.6

    jay, mama = s["parked"]
    assert jay["balance"] == 25336 and mama["balance"] == 25000

    h = s["holdings"][0]
    assert h["invested"] == 10900 and h["profit"] == -591.75

    t = s["tax"]
    # Spreadsheet: taxable 66,191.29, tax 10,645.39, Medicare 1,320.68 (typed in), refund 479.93.
    # Corrected: whole-dollar taxable income, LITO applied, Medicare levy = 2% of taxable income.
    assert t["taxable"] == 66191 and t["gross_tax"] == 10645.30
    assert t["lito"] == pytest.approx(7.14, abs=0.01)
    assert t["medicare"] == 1323.82
    assert t["refund"] == pytest.approx(484.02, abs=0.01)
    # Jay's interest received in FY 2025-26 (Apr, May, Jun 2026) is flagged as assessable income.
    assert t["parked_interest"] == 126
    assert any("Tool Replaced (445.00)" in w for w in t["warnings"])

    nw = s["networth"]
    groups = {g["name"]: g["amount"] for g in nw["groups"]}
    assert groups["Savings"] == 5111.32 and groups["Lent"] == 5450.6 and groups["Properties"] == 5750
    # The spreadsheet's net worth used 9,535.02 for crypto; the app uses the crypto sheet's 10,308.25.
    assert nw["total"] == round(120063.58 - 9535.02 + 10308.25, 2)
    assert nw["spendable"] == round(nw["total"] - 19180, 2)

    fc = s["forecast"]
    assert fc["income"] == 4981.6 and fc["expenses"] == 1193.95
    assert fc["rows"][0]["networth"] == round(nw["total"] + 3787.65 * 12 + 4981.6 * 0.125, 2)


def test_exports_roundtrip(conn):
    rows = exporters.ledger(conn)
    by_acct = {}
    for r in rows:
        by_acct[r["account"]] = round(by_acct.get(r["account"], 0) + r["amount"], 2)
    assert by_acct["Loan: Lent for Van"] == 2570.6
    assert by_acct["Parked: Jay Parking"] == 25336
    assert by_acct["Bitcoin (BTC)"] == 10308.25

    csv_text = exporters.to_csv(rows)
    back = exporters.parse_csv(csv_text)
    assert len(back) == len(rows)
    ofx = exporters.to_ofx(rows)
    assert len(exporters.parse_ofx(ofx)) == len(rows)
    qif = exporters.to_qif(rows)
    parsed = exporters.parse_qif(qif)
    assert len(parsed) == len(rows)
    assert exporters.to_xlsx(conn)[:2] == b"PK"


def test_api(conn):
    with TestClient(main.app) as client:
        assert client.get("/api/summary").status_code == 200
        r = client.post("/api/t/transactions", json={"date": "2026-11-20", "description": "Groceries",
                                                     "amount": -85.5, "category": "Food", "account": "Savings"})
        assert r.status_code == 200
        cf = client.get("/api/summary").json()["cashflow"]
        assert cf["this_month"]["expenses"] == 85.5
        for fmt in ["csv", "qif", "ofx", "json", "xlsx"]:
            assert client.get(f"/api/export/{fmt}").status_code == 200, fmt
        csv_bytes = "Date,Description,Debit,Credit\n21/11/2026,Coffee,4.50,\n".encode()
        r = client.post("/api/import/transactions", files={"file": ("bank.csv", csv_bytes)})
        assert r.json() == {"added": 1, "skipped_duplicates": 0}
        r = client.post("/api/import/transactions", files={"file": ("bank.csv", csv_bytes)})
        assert r.json()["skipped_duplicates"] == 1
        backup = client.get("/api/export/json").content
        assert client.post("/api/import/backup", files={"file": ("b.json", backup)}).status_code == 200


def test_tax_rules():
    # LITO: full $700 to $37,500, nil from $66,667
    assert tax.lito(37500) == 700 and tax.lito(45000) == 325 and tax.lito(66667) == 0
    # Medicare levy shades in at 10c/$ above the low-income threshold
    assert tax.medicare_levy(28011, 2, 28011) == 0
    assert tax.medicare_levy(30000, 2, 28011) == pytest.approx(198.9)
    assert tax.medicare_levy(50000, 2, 28011) == 1000
    # HELP 2025-26: 15c/$ over 67,000; 8,700 + 17c over 125,000; capped at 10% of income
    h = tax.PRESETS["2025-26"]["help"]
    assert tax.help_repayment(67000, h) == 0
    assert tax.help_repayment(77000, h) == pytest.approx(1500)
    assert tax.help_repayment(135000, h) == pytest.approx(10400)
    assert tax.help_repayment(300000, h) == pytest.approx(30000)
    # MLS applies to the whole income once over a tier
    assert tax.mls(110000, tax.PRESETS["2025-26"]["mls_tiers"]) == (1100.0, 1.0)
    # Diminishing value: $540 phone, 3-year life, bought 1 Jan 2026 -> 181 days in FY 2025-26
    s, e = tax.fy_range("2025-26")
    assert tax.decline_in_value(540, 3, date(2026, 1, 1), s, e) == pytest.approx(540 * 2 / 3 * 181 / 365)
    # second year starts from the reduced value
    s2, e2 = tax.fy_range("2026-27")
    first = 540 * 2 / 3 * 181 / 365
    assert tax.decline_in_value(540, 3, date(2026, 1, 1), s2, e2) == pytest.approx((540 - first) * 2 / 3)
    # brackets
    assert tax.income_tax(45000, tax.RES_2526) == pytest.approx(4288)
    assert tax.income_tax(45000, tax.RES_2627) == pytest.approx(4020)
    assert tax.income_tax(50000, tax.FOREIGN) == pytest.approx(15000)


def test_standard_deduction_and_residency():
    items = [{"section": "income", "name": "Salary", "amount": 60000},
             {"section": "work_expense", "name": "Boots", "amount": 99}]
    cfg = {**tax.preset("2026-27"), "withheld": 0}
    r = tax.calculate(items, cfg)
    assert r["standard_used"] and r["work_expenses"] == 1000 and r["taxable"] == 59000
    r = tax.calculate(items, {**tax.preset("2025-26", "foreign"), "withheld": 0})
    assert r["lito"] == 0 and r["medicare"] == 0 and r["gross_tax"] == pytest.approx(59901 * 0.30)
