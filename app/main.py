"""FastAPI app: JSON API + static single-page UI."""
from __future__ import annotations

import base64
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import calc, db, exporters
from . import tax as taxcalc

BASE = Path(__file__).resolve().parent
SEED = BASE / "seed.json"


@asynccontextmanager
async def lifespan(_app):
    if db.init_db() and SEED.exists() and os.environ.get("SEED_ON_FIRST_RUN", "1") == "1":
        with db.session() as conn:
            db.restore_all(conn, json.loads(SEED.read_text()))
    yield


app = FastAPI(title="Finance Tracker", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)

AUTH_USER = os.environ.get("APP_USERNAME", "")
AUTH_PASS = os.environ.get("APP_PASSWORD", "")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Optional HTTP Basic auth — enabled when APP_PASSWORD is set."""
    if AUTH_PASS and request.url.path != "/healthz":
        ok = False
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, AUTH_USER or user) and secrets.compare_digest(pw, AUTH_PASS)
            except Exception:
                ok = False
        if not ok:
            return Response("Authentication required", 401, {"WWW-Authenticate": 'Basic realm="Finance Tracker"'})
    return await call_next(request)




@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---------------------------------------------------------------- summary & config

@app.get("/api/summary")
def summary():
    with db.session() as conn:
        return calc.build_summary(conn)


@app.get("/api/config")
def get_config():
    with db.session() as conn:
        return db.get_config(conn)


@app.put("/api/config")
def put_config(values: dict = Body(...)):
    with db.session() as conn:
        try:
            db.set_config(conn, values)
        except KeyError as e:
            raise HTTPException(400, f"Unknown setting {e}")
        return db.get_config(conn)


@app.get("/api/tax/presets")
def tax_presets():
    return {"years": list(taxcalc.PRESETS), "residency": ["resident", "foreign", "whm"]}


@app.post("/api/tax/preset")
def apply_tax_preset(year: str = Body(...), residency: str = Body("resident")):
    if year not in taxcalc.PRESETS or residency not in ("resident", "foreign", "whm"):
        raise HTTPException(400, "Unknown year or residency")
    with db.session() as conn:
        cfg = db.get_config(conn)
        db.set_config(conn, {"tax": {**cfg["tax"], **taxcalc.preset(year, residency)}})
        return db.get_config(conn)


# ---------------------------------------------------------------- generic CRUD

def _table(name: str) -> str:
    if name not in db.TABLES:
        raise HTTPException(404, "Unknown table")
    return name


@app.get("/api/t/{table}")
def list_rows(table: str, request: Request):
    with db.session() as conn:
        return db.list_rows(conn, _table(table), **dict(request.query_params))


@app.post("/api/t/{table}")
def create_row(table: str, data: dict = Body(...)):
    with db.session() as conn:
        try:
            return db.insert_row(conn, _table(table), data)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e))


@app.put("/api/t/{table}/{row_id}")
def update_row(table: str, row_id: int, data: dict = Body(...)):
    with db.session() as conn:
        try:
            row = db.update_row(conn, _table(table), row_id, data)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e))
        if not row:
            raise HTTPException(404, "Not found")
        return row


@app.delete("/api/t/{table}/{row_id}")
def delete_row(table: str, row_id: int):
    with db.session() as conn:
        db.delete_row(conn, _table(table), row_id)
    return {"ok": True}


# ---------------------------------------------------------------- export

def _download(content, media: str, filename: str):
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/export/{fmt}")
def export(fmt: str, balances: int = 1, account: str | None = None):
    stamp = date.today().isoformat()
    with db.session() as conn:
        if fmt == "json":
            return _download(json.dumps(db.dump_all(conn), indent=1), "application/json",
                             f"finance-backup-{stamp}.json")
        if fmt == "xlsx":
            return _download(exporters.to_xlsx(conn),
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             f"finance-{stamp}.xlsx")
        rows = exporters.ledger(conn, include_balances=bool(balances))
        if account:
            rows = [r for r in rows if r["account"] == account]
        currency = db.get_config(conn).get("currency", "AUD")
    if fmt == "csv":
        return _download(exporters.to_csv(rows), "text/csv", f"finance-transactions-{stamp}.csv")
    if fmt == "qif":
        return _download(exporters.to_qif(rows), "application/qif", f"finance-{stamp}.qif")
    if fmt == "ofx":
        if not rows:
            raise HTTPException(400, "Nothing to export")
        return _download(exporters.to_ofx(rows, currency), "application/x-ofx", f"finance-{stamp}.ofx")
    raise HTTPException(404, "Unknown format")


@app.get("/api/ledger/accounts")
def ledger_accounts():
    with db.session() as conn:
        return sorted({r["account"] for r in exporters.ledger(conn)})


# ---------------------------------------------------------------- import

@app.post("/api/import/backup")
async def import_backup(file: UploadFile = File(...)):
    try:
        payload = json.loads((await file.read()).decode("utf-8-sig"))
        with db.session() as conn:
            db.restore_all(conn, payload)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, f"Could not restore backup: {e}")
    return {"ok": True}


@app.post("/api/import/transactions")
async def import_transactions(file: UploadFile = File(...), account: str = Form(""), dayfirst: int = Form(1),
                              dry_run: int = Form(0)):
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    name = (file.filename or "").lower()
    try:
        if name.endswith((".ofx", ".qfx")) or "<OFX>" in text.upper()[:2000]:
            rows = exporters.parse_ofx(text)
        elif name.endswith(".qif") or text.lstrip().startswith("!"):
            rows = exporters.parse_qif(text, dayfirst=bool(dayfirst))
        else:
            rows = exporters.parse_csv(text, dayfirst=bool(dayfirst))
    except ValueError as e:
        raise HTTPException(400, str(e))
    for r in rows:
        if account:
            r["account"] = account
    if dry_run:
        return {"count": len(rows), "preview": rows[:20]}
    with db.session() as conn:
        existing = {(t["date"], round(t["amount"] or 0, 2), t["description"] or "", t["account"] or "")
                    for t in db.list_rows(conn, "transactions")}
        added = skipped = 0
        for r in rows:
            key = (r["date"], round(r["amount"], 2), r["description"], r["account"])
            if key in existing:
                skipped += 1
                continue
            db.insert_row(conn, "transactions", r)
            existing.add(key)
            added += 1
    return {"added": added, "skipped_duplicates": skipped}


@app.post("/api/reset")
def reset(which: str = Body("seed", embed=True)):
    """Reset to the spreadsheet data ("seed") or wipe everything ("empty")."""
    with db.session() as conn:
        if which == "seed" and SEED.exists():
            db.restore_all(conn, json.loads(SEED.read_text()))
        else:
            db.restore_all(conn, {"format": "finance-tracker-backup", "tables": {}})
    return {"ok": True}


# ---------------------------------------------------------------- UI

app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.exception_handler(404)
async def not_found(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": getattr(exc, "detail", "Not found")}, 404)
    return FileResponse(BASE / "static" / "index.html")
