"""Refresh the vendored vPIC make canon (monthly step in sync.yml)."""

from __future__ import annotations

import json

import httpx

from etl import config

# Refuse to shrink the canon dramatically — a half-broken vPIC response must
# not start quarantining half the dataset next run.
MIN_MAKES = 5_000


def run() -> dict:
    with httpx.Client(timeout=60.0) as client:
        resp = client.get(config.VPIC_ALL_MAKES_URL)
        resp.raise_for_status()
        results = resp.json().get("Results", [])
    names = sorted({r["Make_Name"].strip().upper() for r in results if r.get("Make_Name")})
    if len(names) < MIN_MAKES:
        raise ValueError(f"vPIC returned only {len(names)} makes; keeping existing canon")
    path = config.DATA_DIR / "vpic_makes.json"
    path.write_text(json.dumps(names, indent=0), encoding="utf-8")
    print(json.dumps({"step": "vpic-refresh", "makes": len(names)}, indent=2))
    return {"makes": len(names)}
