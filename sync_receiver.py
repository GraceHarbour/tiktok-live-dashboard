"""Private Cloud Run receiver for authorized Backstage snapshots."""

import json

from fastapi import FastAPI, HTTPException, Request

from server_dashboard import database

app = FastAPI(title="Grace Harbour Backstage Receiver")


@app.get("/healthz")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/internal/backstage/snapshot")
async def receive_backstage_snapshot(request: Request) -> dict[str, object]:
    """Cloud Run IAM authenticates the reader before this handler runs."""
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as error:
        raise HTTPException(400, "The sync payload must be valid JSON.") from error
    if not isinstance(payload, dict):
        raise HTTPException(400, "The sync payload must be an object.")

    saved = database().import_snapshot_payload(payload)
    return {
        "saved": True,
        "source": saved["source"],
        "captured_at": saved["captured_at"],
    }
