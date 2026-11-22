"""SQLite storage: schema, connection helper and generic CRUD for the editable tables."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("FINANCE_DB", Path(__file__).resolve().parent.parent / "data" / "finance.db"))

# Each editable table: column -> type ("text", "real", "int", "date"). "id" is implicit.
# "parent" names the foreign-key column for child tables (cascade delete).
TABLES: dict[str, dict] = {
    "accounts": {
        "cols": {"name": "text", "category": "text", "balance": "real", "spendable": "int",
                 "notes": "text", "sort": "int"},
        "order": "sort, id",
    },
    "holdings": {
        "cols": {"name": "text", "symbol": "text", "kind": "text", "units": "real",
                 "current_value": "real", "notes": "text"},
        "order": "id",
    },
    "holding_buys": {
        "cols": {"holding_id": "int", "date": "date", "amount": "real", "units": "real",
                 "platform": "text", "notes": "text"},
        "parent": ("holding_id", "holdings"),
        "order": "date, id",
    },
    "parked": {
        "cols": {"name": "text", "holder": "text", "notes": "text"},
        "order": "id",
    },
    "parked_entries": {
        "cols": {"parked_id": "int", "date": "date", "amount": "real", "kind": "text",
                 "description": "text", "wallet": "text"},
        "parent": ("parked_id", "parked"),
        "order": "date, id",
    },
    "loans": {
        "cols": {"name": "text", "borrower": "text", "interest": "real", "installment": "real",
                 "frequency": "text", "notes": "text"},
        "order": "id",
    },
    "loan_sources": {
        "cols": {"loan_id": "int", "fund": "text", "amount": "real"},
        "parent": ("loan_id", "loans"),
        "order": "id",
    },
    "loan_payments": {
        "cols": {"loan_id": "int", "date": "date", "amount": "real", "notes": "text"},
        "parent": ("loan_id", "loans"),
        "order": "date, id",
    },
    "transactions": {
        "cols": {"date": "date", "description": "text", "amount": "real", "category": "text",
                 "account": "text", "notes": "text"},
        "order": "date DESC, id DESC",
    },
    "goals": {
        "cols": {"name": "text", "target": "real", "current": "real", "link": "text",
                 "target_date": "date", "notes": "text"},
        "order": "id",
    },
    "snapshots": {
        "cols": {"date": "date", "networth": "real", "spendable": "real", "breakdown": "text",
                 "notes": "text"},
        "order": "date",
    },
    "recurring": {
        "cols": {"kind": "text", "name": "text", "amount": "real"},
        "order": "kind DESC, id",
    },
    "budget_items": {
        "cols": {"kind": "text", "name": "text", "value": "real"},
        "order": "kind, id",
    },
    "tax_items": {
        "cols": {"section": "text", "name": "text", "amount": "real"},
        "order": "section, id",
    },
}

SQL_TYPES = {"text": "TEXT", "real": "REAL", "int": "INTEGER", "date": "TEXT"}

DEFAULT_CONFIG = {
    "currency": "AUD",
    "locale": "en-AU",
    "budget_income": 0.0,
    "budget_period": "fortnightly",
    "monthly_savings": 0.0,
    "forecast_years": 10,
    "super_rate": 0.125,
    "growth_rate": 0.0,
    "tax": {
        "year": "2025-26",
        "withheld": 0.0,
        "medicare": 0.0,
        "medicare_auto": False,
        "medicare_rate": 2.0,
        "brackets": [
            {"from": 0, "rate": 0},
            {"from": 18200, "rate": 16},
            {"from": 45000, "rate": 30},
            {"from": 135000, "rate": 37},
            {"from": 190000, "rate": 45},
        ],
    },
}


def _schema() -> str:
    parts = ["CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);"]
    for name, spec in TABLES.items():
        cols = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
        for c, t in spec["cols"].items():
            col = f"{c} {SQL_TYPES[t]}"
            if spec.get("parent") and spec["parent"][0] == c:
                col += f" NOT NULL REFERENCES {spec['parent'][1]}(id) ON DELETE CASCADE"
            cols.append(col)
        parts.append(f"CREATE TABLE IF NOT EXISTS {name} ({', '.join(cols)});")
    parts.append("CREATE UNIQUE INDEX IF NOT EXISTS snapshots_date ON snapshots(date);")
    return "\n".join(parts)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> bool:
    """Create tables. Returns True when the database is brand new (empty)."""
    with session() as conn:
        conn.executescript(_schema())
        new = conn.execute("SELECT COUNT(*) FROM config").fetchone()[0] == 0
    return new


# ---------- config ----------

def get_config(conn) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for row in conn.execute("SELECT key, value FROM config"):
        cfg[row["key"]] = json.loads(row["value"])
    return cfg


def set_config(conn, values: dict) -> None:
    for k, v in values.items():
        if k not in DEFAULT_CONFIG:
            raise KeyError(k)
        conn.execute("INSERT INTO config(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, json.dumps(v)))


# ---------- generic CRUD ----------

def _clean(table: str, data: dict) -> dict:
    cols = TABLES[table]["cols"]
    out = {}
    for k, v in data.items():
        if k not in cols:
            continue
        t = cols[k]
        if v == "" and t != "text":
            v = None
        if v is not None:
            if t == "real":
                v = float(v)
            elif t == "int":
                v = int(v)
            else:
                v = str(v)
        out[k] = v
    return out


def list_rows(conn, table: str, **filters) -> list[dict]:
    spec = TABLES[table]
    where, args = [], []
    for k, v in filters.items():
        if k in spec["cols"] and v is not None:
            where.append(f"{k} = ?")
            args.append(v)
    sql = f"SELECT * FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {spec['order']}"
    return [dict(r) for r in conn.execute(sql, args)]


def insert_row(conn, table: str, data: dict) -> dict:
    data = _clean(table, data)
    if not data:
        cur = conn.execute(f"INSERT INTO {table} DEFAULT VALUES")
    else:
        keys = ", ".join(data)
        marks = ", ".join("?" for _ in data)
        cur = conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({marks})", list(data.values()))
    return dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (cur.lastrowid,)).fetchone())


def update_row(conn, table: str, row_id: int, data: dict) -> dict | None:
    data = _clean(table, data)
    if data:
        sets = ", ".join(f"{k} = ?" for k in data)
        conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*data.values(), row_id])
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    return dict(row) if row else None


def delete_row(conn, table: str, row_id: int) -> None:
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))


def dump_all(conn) -> dict:
    return {
        "format": "finance-tracker-backup",
        "version": 1,
        "config": get_config(conn),
        "tables": {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY id")] for t in TABLES},
    }


def restore_all(conn, payload: dict) -> None:
    """Replace everything with the content of a backup produced by dump_all (or the seed file)."""
    if payload.get("format") != "finance-tracker-backup":
        raise ValueError("Not a finance-tracker backup file")
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for t in reversed(list(TABLES)):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM sqlite_sequence")
        conn.execute("DELETE FROM config")
        cfg = {k: v for k, v in (payload.get("config") or {}).items() if k in DEFAULT_CONFIG}
        set_config(conn, cfg)
        for t in TABLES:
            for row in payload.get("tables", {}).get(t, []):
                data = _clean(t, row)
                if "id" in row and row["id"] is not None:
                    data["id"] = int(row["id"])
                keys = ", ".join(data)
                marks = ", ".join("?" for _ in data)
                conn.execute(f"INSERT INTO {t} ({keys}) VALUES ({marks})", list(data.values()))
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
