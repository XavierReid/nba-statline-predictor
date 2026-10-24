"""Append-only, content-addressed store for raw API payloads (provenance for rating work).

Every payload we fetch is written once under data/raw/<endpoint>/<sha[:2]>/<sha>.json.gz and
recorded in data/raw/manifest.jsonl (endpoint, params, fetched_at, sha256, row count, status).
The directory is gitignored (NBA.com Terms of Use s.9: statistics are for private, non-commercial
use; do not commit or distribute them). Nothing here is ever rewritten or deleted.
"""
import gzip
import hashlib
import json
import os
import time
from datetime import datetime, timezone

RAW_DIR = os.environ.get("RAW_DIR", "data/raw")
MANIFEST = os.path.join(RAW_DIR, "manifest.jsonl")


def _canonical(payload) -> bytes:
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()


def record(endpoint: str, params: dict, payload, status: str = "ok", error: str = None) -> dict:
    os.makedirs(RAW_DIR, exist_ok=True)
    entry = {"endpoint": endpoint, "params": params,
             "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": status}
    if payload is not None:
        blob = _canonical(payload)
        sha = hashlib.sha256(blob).hexdigest()
        path = os.path.join(RAW_DIR, endpoint, sha[:2], sha + ".json.gz")
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with gzip.open(path, "wb") as f:
                f.write(blob)
        entry.update(sha256=sha, path=path, rows=len(payload) if hasattr(payload, "__len__") else None)
    if error:
        entry["error"] = error[:300]
    with open(MANIFEST, "a") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def captured(endpoint: str, fn, retries: int = 3):
    """Wrap an nba_client fetch function: call it (with retries), save its result, return it."""
    def wrapper(*args, **kwargs):
        params = {"args": [str(a) for a in args], "kwargs": {k: str(v) for k, v in kwargs.items()}}
        last = None
        for attempt in range(1, retries + 1):
            try:
                out = fn(*args, **kwargs)
                record(endpoint, params, out)
                return out
            except Exception as e:                      # network/timeouts are routine for this API
                last = e
                record(endpoint, {**params, "attempt": attempt}, None, status="error", error=repr(e))
                time.sleep(5 * attempt)
        raise last
    return wrapper


def load_manifest() -> list:
    if not os.path.exists(MANIFEST):
        return []
    return [json.loads(l) for l in open(MANIFEST) if l.strip()]


def read_payload(entry: dict):
    with gzip.open(entry["path"], "rb") as f:
        return json.loads(f.read())


def verify_all() -> dict:
    """Recompute every stored payload's hash; returns {checked, bad}. Provenance self-check."""
    checked, bad = 0, []
    for e in load_manifest():
        if e.get("status") != "ok" or "path" not in e:
            continue
        with gzip.open(e["path"], "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() != e["sha256"]:
                bad.append(e["path"])
        checked += 1
    return {"checked": checked, "bad": bad}
