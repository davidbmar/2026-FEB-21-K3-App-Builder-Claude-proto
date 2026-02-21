import hashlib
import hmac
import os
import logging

from fastapi import FastAPI, Request, HTTPException

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI(title=os.environ.get("APP_NAME", "webhook"))


async def verify_signature(request: Request) -> bytes:
    """Verify HMAC-SHA256 signature if WEBHOOK_SECRET is set."""
    body = await request.body()
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if secret:
        sig_header = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            raise HTTPException(status_code=401, detail="Invalid signature")
    return body


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/webhook")
async def handle_webhook(request: Request):
    body = await verify_signature(request)
    try:
        payload = await request.json()
    except Exception:
        payload = {"raw": body.decode(errors="replace")}

    log.info("Received webhook: %s", str(payload)[:200])
    # TODO: process payload
    return {"received": True}
