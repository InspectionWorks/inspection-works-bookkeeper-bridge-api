import os
import json
from datetime import datetime
from typing import List, Optional, Literal
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, HttpUrl
import requests

API_TOKEN = os.environ.get("API_BEARER_TOKEN")
LOG_PATH = "/tmp/ingest_log.json"

# Optional Zapier relay hooks (no-code)
HOOKS = {
    "invoice": os.environ.get("ZAPIER_HOOK_INVOICE"),
    "payment": os.environ.get("ZAPIER_HOOK_PAYMENT"),
    "deposit": os.environ.get("ZAPIER_HOOK_DEPOSIT"),
    "close_package": os.environ.get("ZAPIER_HOOK_CLOSE_PACKAGE"),
    "drive_ingest": os.environ.get("ZAPIER_HOOK_DRIVE_INGEST"),
}

app = FastAPI(title="Inspection Works Bridge API", version="1.1.0")


# --- Security ---
def _auth(authorization: Optional[str]):
    if not API_TOKEN:
        raise HTTPException(500, "Server missing API_BEARER_TOKEN")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    token = authorization.split(" ", 1)[1]
    if token != API_TOKEN:
        raise HTTPException(403, "Invalid token")


# --- Helper: write to /tmp/ingest_log.json ---
def _append_ingest_log(entry: dict):
    try:
        logs = []
        if os.path.exists(LOG_PATH):
            with open(LOG_PATH, "r") as f:
                logs = json.load(f)
        logs.append(entry)
        with open(LOG_PATH, "w") as f:
            json.dump(logs[-500:], f, indent=2)  # keep last 500 entries
    except Exception as e:
        print("Error writing log:", e)


def _relay(name: str, payload: dict):
    url = HOOKS.get(name)
    if url:
        try:
            r = requests.post(url, json=payload, timeout=15)
            r.raise_for_status()
            return {"status": "ok", "relayed_to": name, "zapier_status": r.status_code, "zapier_body": r.text}
        except Exception as e:
            return {"status": "ok", "relayed_to": name, "zapier_error": str(e)}
    return {"status": "ok", "message": f"{name} accepted", "echo": payload}


# --- Payload Models ---
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
    file_url: HttpUrl
    file_type: Literal["invoice_pdf", "payments_csv", "jobs_csv"]


# --- Routes ---
@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/invoice")
def create_or_update_invoice(payload: InvoicePayload, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    return _relay("invoice", payload.dict())


@app.post("/payment")
def record_payment(payload: PaymentPayload, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    return _relay("payment", payload.dict())


@app.post("/deposit")
def create_deposit(payload: DepositPayload, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    return _relay("deposit", payload.dict())


@app.post("/close-package")
def export_close_package(payload: ClosePackagePayload, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    return _relay("close_package", payload.dict())


@app.post("/drive/ingest")
def ingest_drive_file(payload: DriveIngestPayload, authorization: Optional[str] = Header(None)):
    _auth(authorization)
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "file_url": str(payload.file_url),
        "file_type": payload.file_type,
    }
    _append_ingest_log(entry)
    return _relay("drive_ingest", payload.dict())


@app.get("/ingest-log")
def get_ingest_log():
    """Return the list of ingested files (last 500 entries)."""
    if not os.path.exists(LOG_PATH):
        return {"entries": []}
    with open(LOG_PATH, "r") as f:
        logs = json.load(f)
    return {"entries": logs}

import io, re
from pdfminer.high_level import extract_text

# -----------------------------------------
#  PDF Invoice Parser (Manual/Test Mode)
# -----------------------------------------

AUTO_PARSE_ON_INGEST = False  # 🔁 flip to True later to automate parsing

@app.post("/parse/invoice")
def parse_invoice(payload: DriveIngestPayload, authorization: Optional[str] = Header(None)):
    """Manually parse a Spectora invoice PDF and extract structured fields."""
    _auth(authorization)

    # 1. Download the PDF
    r = requests.get(str(payload.file_url))
    r.raise_for_status()
    tmp_path = "/tmp/tmp_invoice.pdf"
    with open(tmp_path, "wb") as f:
        f.write(r.content)

    # 2. Extract all text
    text = extract_text(tmp_path)

    # 3. Helper to extract text safely
    def find(pattern):
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else None

    # 4. Parse key fields
    client = find(r"Bill To\s*([A-Za-z\s']+)")
    email = find(r"([\w\.-]+@[\w\.-]+)")
    phone = find(r"(\d{3}[-\s]?\d{3}[-\s]?\d{4})")
    property_addr = find(r"Property\s*(.+?)\nDate")
    date = find(r"Date\s*([\d/]+)")
    order = find(r"Order\s*(\d+)")
    total = find(r"TOTAL\s*CAD\$\s*([\d,\.]+)")
    tech_fee = find(r"Technology Fee.*CAD\$\s*([\d,\.]+)")
    paid = find(r"Paid\s*\(.*\)\s*CAD\$\s*([\d,\.]+)")
    gst = find(r"GST.*CAD\$\s*([\d,\.]+)")

    # 5. Extract line items (skip tech fee)
    lines = re.findall(r"([A-Za-z\s]+)\s*CAD\$\s*([\d,\.]+)", text)
    items = []
    for desc, amt in lines:
        desc = desc.strip()
        if desc.lower().startswith("technology fee"): 
            continue
        items.append({"description": desc, "amount": float(amt.replace(',', ''))})

    # 6. Build structured result
    result = {
        "client": client,
        "email": email,
        "phone": phone,
        "property": property_addr,
        "date": date,
        "order": order,
        "total": float(total.replace(',', '')) if total else None,
        "gst": float(gst.replace(',', '')) if gst else None,
        "technology_fee": float(tech_fee.replace(',', '')) if tech_fee else 0.0,
        "paid_amount": float(paid.replace(',', '')) if paid else None,
        "revenue_total": (float(total.replace(',', '')) - float(tech_fee.replace(',', ''))) if total and tech_fee else None,
        "line_items": items
    }

    return {"status": "ok", "parsed": result}
