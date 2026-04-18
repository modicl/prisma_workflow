"""
PRISMA — Lambda trigger for S3 PUT events.

Wakes up the agent backend when a new job is uploaded to S3.

Trigger configuration:
  Event source : S3 bucket (prisma-workflow), prefix: jobs/
  Runtime      : Python 3.12
  Memory       : 128 MB
  Timeout      : 10 seconds

Required environment variables:
  BACKEND_INTERNAL_URL  — Base URL of the agent backend, e.g. http://backend:8000
  INTERNAL_TOKEN        — Secret token checked by /internal/run (must match backend)
"""

import json
import os
import urllib.error
import urllib.request


BACKEND_URL = os.environ["BACKEND_INTERNAL_URL"].rstrip("/")
INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "")


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
            req = urllib.request.Request(
                url,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "X-Internal-Token": INTERNAL_TOKEN,
                },
                data=b"{}",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = resp.read().decode()
                print(f"Backend response {resp.status}: {body}")
            processed += 1
        except urllib.error.HTTPError as exc:
            msg = f"HTTP {exc.code} for session {session_id}: {exc.read().decode()}"
            print(f"ERROR: {msg}")
            errors.append(msg)
        except Exception as exc:
            msg = f"Failed to trigger session {session_id}: {exc}"
            print(f"ERROR: {msg}")
            errors.append(msg)

    if errors:
        # Raising causes Lambda to retry (up to 2 retries for async invocations)
        raise RuntimeError(f"Trigger errors: {errors}")

    return {"processed": processed}
