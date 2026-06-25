"""Snapshot utilities for Biotech Risk Scout scan outputs."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


SNAPSHOT_DATE_FORMAT = "%Y%m%d"


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp for snapshot metadata."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def snapshot_filename(timestamp: str | None = None) -> str:
    """Return the standard dated snapshot filename."""
    if timestamp is None:
        stamp = datetime.now(timezone.utc).strftime(SNAPSHOT_DATE_FORMAT)
    else:
        stamp = timestamp[:10].replace("-", "")
    return f"scan-{stamp}.json"


def build_snapshot_payload(records: list[dict[str, Any]], timestamp: str | None = None) -> dict[str, Any]:
    """Build a stable snapshot payload with metadata and records."""
    run_at = timestamp or utc_timestamp()
    return {
        "generated_at": run_at,
        "record_count": len(records),
        "records": records,
    }


def write_snapshot(records: list[dict[str, Any]], snapshot_dir: str, timestamp: str | None = None) -> dict[str, str]:
    """Write a dated snapshot and latest.json into snapshot_dir.

    Returns paths for the dated snapshot and latest snapshot.
    """
    os.makedirs(snapshot_dir, exist_ok=True)
    payload = build_snapshot_payload(records, timestamp=timestamp)

    dated_path = os.path.join(snapshot_dir, snapshot_filename(payload["generated_at"]))
    latest_path = os.path.join(snapshot_dir, "latest.json")

    for path in (dated_path, latest_path):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")

    return {"snapshot_path": dated_path, "latest_path": latest_path}
