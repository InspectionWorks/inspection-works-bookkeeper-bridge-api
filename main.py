
import os
from typing import List, Optional, Literal
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, HttpUrl
import requests

API_TOKEN = os.environ.get("API_BEARER_TOKEN")

# Optional Zapier relay hooks (no-code)
HOOKS = {
    "invoice": os.environ.get("ZAPIER_HOOK_INVOICE"),
    "payment": os.environ.get("ZAPIER_HOOK_PAYMENT"),
    "deposit": os.environ.get("ZAPIER_HOOK_DEPOSIT"),
    "close_package": os.environ.get("ZAPIER_HOOK_CLOSE_PACKAGE"),
    "drive_ingest": os.environ.get("ZAPIER_HOOK_DRIVE_INGEST"),
}

app = FastAPI(title="Inspection Works Bridge API", version="1.0.0")

def _auth(authorization: Optional[str]):
    if not API_TOKEN:
        raise HTTPException(500, "Server missing API_BEARER_TOKEN")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    token = authorization.split(" ", 1)[1]
    if token != API_TOKEN:
        raise HTTPException(403, "Invalid token")

def _relay(name: str, payload: dict):
    url = HOOKS.get(name)
    if url:
        try:
            r = requests.post(url, json=payload, timeout=15)
            r.raise_for_status()
            return {"status": "ok", "relayed_to": name, "zapier_status": r.status_code, "zapier_body": r.text}
        except Exception as e:
            return {"status": "ok", "relayed_to": name, "zapier_error": str(e)}
    # No hook configured: just echo (useful for testing)
    return {"status": "ok", "message": f"{name} accepted", "echo": payload}

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
    return _relay("drive_ingest", payload.dict())
