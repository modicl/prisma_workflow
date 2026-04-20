"""
PRISMA — Lambda trigger for S3 PUT events.

Wakes up the agent backend when a new job is uploaded to S3.

Trigger configuration:
  Event source : S3 bucket (prisma-workflow), prefix: jobs/
  Runtime      : Python 3.12
  Memory       : 128 MB
  Timeout      : 60 seconds (debe ser > API_TIMEOUT * RETRY_ATTEMPTS)

Required environment variables:
  BACKEND_INTERNAL_URL  — Base URL of the agent backend, e.g. http://backend:8000
  INTERNAL_TOKEN        — Secret token checked by /internal/run (must match backend)

Optional environment variables:
  API_TIMEOUT      — Seconds to wait for each HTTP attempt (default: 30)
  RETRY_ATTEMPTS   — Number of attempts before giving up (default: 3)
"""

import json
import os
import time
import urllib.error
import urllib.request


BACKEND_URL = os.environ["BACKEND_INTERNAL_URL"].rstrip("/")
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "")
TIMEOUT_SECONDS = int(os.getenv("API_TIMEOUT", "30"))
RETRY_ATTEMPTS = int(os.getenv("RETRY_ATTEMPTS", "3"))


def _post_with_retry(url: str) -> dict:
    """POST to url with exponential backoff. Raises on final failure."""
    req = urllib.request.Request(
        url,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Internal-Token": INTERNAL_TOKEN,
        },
        data=b"{}",
    )
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            if attempt == RETRY_ATTEMPTS:
                raise
            delay = 2 ** attempt  # 2s, 4s
            print(f"Attempt {attempt}/{RETRY_ATTEMPTS} failed ({e}), retrying in {delay}s...")
            time.sleep(delay)


def handler(event: dict, context) -> dict:
    processed = 0
    errors = []

    for record in event.get("Records", []):
        key: str = record["s3"]["object"]["key"]  # e.g. jobs/{session_id}/paci.pdf
        filename = key.split("/")[-1]

        # Only trigger on the PACI file to avoid double invocation per session
        if not filename.startswith("paci"):
            print(f"Skipping non-PACI key: {key}")
            continue

        parts = key.split("/")
        if len(parts) < 2:
            print(f"Unexpected key format, skipping: {key}")
            continue

        session_id = parts[1]
        url = f"{BACKEND_URL}/chat/internal/run/{session_id}"
        print(f"Triggering session {session_id} via {url}")

        try:
            body = _post_with_retry(url)
            print(f"Backend response: {body}")
            processed += 1
        except Exception as exc:
            msg = f"Failed to trigger session {session_id} after {RETRY_ATTEMPTS} attempts: {exc}"
            print(f"ERROR: {msg}")
            errors.append(msg)

    if errors:
        # Raising causes Lambda to retry (up to 2 retries for async invocations)
        raise RuntimeError(f"Trigger errors: {errors}")

    return {"processed": processed}
