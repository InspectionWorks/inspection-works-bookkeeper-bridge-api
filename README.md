
# Inspection Works Bridge API (Starter)

A beginner-friendly server the Custom GPT can call. It validates a Bearer token and (optionally) relays each request to a **Zapier Webhook** so you can automate QuickBooks Online and Google Drive without writing code. Later, a developer can replace the relays with direct QBO/Drive SDK calls.

## 0) What you need
- A long random token for `API_BEARER_TOKEN` (store it as a secret).
- (Optional) Zapier **Catch Hook** URLs for: invoice, payment, deposit, close-package, drive/ingest.

## 1) Run it locally (quick test)
```
pip install -r requirements.txt
export API_BEARER_TOKEN=change-me
uvicorn main:app --reload --port 8080
```
Check: http://localhost:8080/health

## 2) One-click hosting options (no coding)
- **Railway.app** or **Render.com**: Create new project from this folder or GitHub repo.
  - Set environment variables from `.env.example` (at least `API_BEARER_TOKEN`).
  - Railway/Render will auto-detect and run the app.
- **Replit**: Create Python Repl, upload these files, add secret `API_BEARER_TOKEN`, then click Run.
- **Google Cloud Run** (optional):
  - Build with the provided `Dockerfile` and deploy (requires a Google account).

## 3) Connect the Custom GPT
- In GPT builder, upload `bridge_openapi.yaml` (from earlier step).
- Set the **Action** base URL to your hosted server URL (e.g., `https://your-app.onrender.com`).
- Under **Authentication**, choose Bearer and paste the same `API_BEARER_TOKEN`.

## 4) (Optional) Point to Zapier instead of coding
Create 5 **Catch Hook** triggers in Zapier and paste the URLs into these env vars:
- `ZAPIER_HOOK_INVOICE`
- `ZAPIER_HOOK_PAYMENT`
- `ZAPIER_HOOK_DEPOSIT`
- `ZAPIER_HOOK_CLOSE_PACKAGE`
- `ZAPIER_HOOK_DRIVE_INGEST`

Now, when the GPT calls `/invoice` etc., the Bridge forwards the JSON to your Zap, where you can perform QBO actions with built-in connectors.

## 5) Test an endpoint
```
curl -X POST https://YOUR-APP/ invoice  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json"  -d '{"customer":"Jane Smith","line_items":[{"item":"Home Inspection","amount":450,"tax_code":"GST_5"}],"invoice_date":"2025-11-06"}'
```

If a Zapier hook is set, the Zap runs; otherwise the API echoes back the payload (useful to confirm the GPT wiring).

## 6) Next step (optional): direct QBO integration
Replace the relay code with real QBO SDK calls. A developer only needs to edit functions in `main.py` where `_relay()` is used.
