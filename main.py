import os
import json
import hashlib
import datetime
import pathlib
from typing import List, Optional, Literal

import requests
from fastapi import FastAPI, Header, HTTPException, Body
from pydantic import BaseModel, HttpUrl, AnyUrl

# ====== Auth / Env ======
API_TOKEN = os.environ.get("API_BEARER_TOKEN")

# Optional Zapier relay hooks (no-code)
HOOKS = {
    "invoice": os.environ.get("ZAPIER_HOOK_INVOICE"),
    "payment": os.environ.get("ZAPIER_HOOK_PAYMENT"),
    "deposit": os.environ.get("ZAPIER_HOOK_DEPOSIT"),
    "close_package": os.environ.get("ZAPIER_HOOK_CLOSE_PACKAGE"),
    "drive_ingest": os.environ.get("ZAPIER_HOOK_DRIVE_INGEST"),
}

# Where to stash downloaded files & a simple ingest log (Render's /tmp is writable)
BASE_DIR = pathlib.Path("/tmp/invoices")


# ====== FastAPI app ======
app = FastAPI(title="Inspection Works Bridge API", version="1.1.0")


# ====== Models ======
class LineItem(BaseModel):
    item: str
    description: Optional[str] = None
    quantity: Optional[float] = 1
    rate: Optional[float] = None
    amount: float
    tax_code: Optional[str] = None
    class_name: Optional[str] = None


class PaymentLine(BaseModel):
    type: Literal["Payment", "Fee"]
    ref: Optional[str] = None
    account: Optional[str] = None
    amount: float


class InvoicePayload(BaseModel):
    customer: str
    line_items: List[LineItem]
    invoice_date: str
    due_date: Optional[str] = None
    class_name: Optional[str] = None
    attachments: Optional[List[HttpUrl]] = None
    external_ref: Optional[str] = None


class PaymentPayload(BaseModel):
    entity_type: Literal["invoice", "customer"]
    entity_id: Optional[str] = None
    method: Literal["Stripe", "e-Transfer", "Cash", "Cheque", "Other"]
    amount: float
    deposit_account: str
    payout_batch_ref: Optional[str] = None
    note: Optional[str] = None


class DepositPayload(BaseModel):
    lines: List[PaymentLine]
    bank_account: str
    reference: Optional[str] = None
    date: str


class ClosePackagePayload(BaseModel):
    period_start: str
    period_end: str
    deliverables: Optional[List[str]] = None


class DriveIngestPayload(BaseModel):
    file_url: AnyUrl  # Google Drive download URL (from Apps Script)
    file_type: Literal["invoice_pdf", "payments_csv", "jobs_csv"] = "invoice_pdf"
    # Optional extras (recommended to send from Apps Script):
    display_path: Optional[str] = None   # e.g., "123 Main St/invoice.pdf"
    created_at: Optional[str] = None     # ISO timestamp from Drive if available


# ====== Helpers ======
def _auth(authorization: Optional[str]):
    if not API_TOKEN:
        raise HTTPException(500, "Server missing API_BEARER_TOKEN")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if token != API_TOKEN:
        raise HTTPException(403, "Invalid token")


def _relay(name: str, payload: dict):
    """
    Optional: forward payloads to Zapier if a hook is configured.
    We do this AFTER local handling so your ingestion works even without Zapier.
    """
    url = HOOKS.get(name)
    if not url:
        return {"relayed": False, "reason": "no_hook_configured"}

    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        return {
            "relayed": True,
            "status_code": r.status_code,
            "body": r.text[:500],
        }
    except Exception as e:
        return {"relayed": False, "error": str(e)}


def _month_dir() -> pathlib.Path:
    d = BASE_DIR / datetime.datetime.utcnow().strftime("%Y-%m")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_filename_from_url(url: str) -> str:
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return f"{h}.bin"  # we'll set .pdf below if type indicates PDF


# ====== Routes ======
@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/invoice")
def create_or_update_invoice(payload: InvoicePayload, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    relay = _relay("invoice", payload.dict())
    return {"status": "ok", "relay": relay}


@app.post("/payment")
def record_payment(payload: PaymentPayload, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    relay = _relay("payment", payload.dict())
    return {"status": "ok", "relay": relay}


@app.post("/deposit")
def create_deposit(payload: DepositPayload, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    relay = _relay("deposit", payload.dict())
    return {"status": "ok", "relay": relay}


@app.post("/close-package")
def export_close_package(payload: ClosePackagePayload, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    relay = _relay("close_package", payload.dict())
    return {"status": "ok", "relay": relay}


@app.post("/drive/ingest")
def ingest_drive_file(payload: DriveIngestPayload = Body(...), authorization: Optional[str] = Header(None)):
    """
    Downloads the file_url to /tmp/invoices/YYYY-MM/<hash>.pdf (or .csv),
    appends a line to /tmp/invoices/ingest.log.jsonl, and optionally relays to Zapier.
    """
    _auth(authorization)

    # 1) Download the file
    try:
        r = requests.get(str(payload.file_url), timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Fetch failed: {e}")

    # 2) Choose a filename and write it
    month_dir = _month_dir()
    fname = _safe_filename_from_url(str(payload.file_url))
    # Pick an extension based on file_type
    if payload.file_type == "invoice_pdf":
        fname = fname.replace(".bin", ".pdf")
    elif payload.file_type in ("payments_csv", "jobs_csv"):
        fname = fname.replace(".bin", ".csv")
    stored_path = month_dir / fname

    with open(stored_path, "wb") as f:
        f.write(r.content)

    # 3) Append a simple JSON record (one line per ingest)
    record = {
        "received_at": datetime.datetime.utcnow().isoformat(),
        "file_url": str(payload.file_url),
        "file_type": payload.file_type,
        "display_path": payload.display_path,
        "created_at": payload.created_at,
        "stored_path": str(stored_path),
        "bytes": len(r.content),
    }
    log_path = BASE_DIR / "ingest.log.jsonl"
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as lf:
        lf.write(json.dumps(record) + "\n")

    # 4) (Optional) relay to Zapier if configured
    relay = _relay("drive_ingest", record)

    return {"status": "stored", "stored_path": str(stored_path), "relay": relay}
