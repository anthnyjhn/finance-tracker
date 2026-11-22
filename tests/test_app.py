"""Checks the app reproduces the numbers from the original Financial_Calculator.xlsx."""
import json
import os
from datetime import date

import pytest

os.environ["FINANCE_DB"] = "/tmp/finance-test.db"

from fastapi.testclient import TestClient  # noqa: E402

from app import calc, db, exporters, main  # noqa: E402

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
    assert t["taxable"] == 66191.29 and t["income_tax"] == 10645.39 and t["total_tax"] == 11966.07
    assert t["refund"] == 479.93

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
