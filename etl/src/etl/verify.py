"""Integrity assertions (SPEC §10) — run BEFORE any Firestore write.

If any check fails the pipeline stops, old data keeps serving, and CI opens
an issue. Baseline for drift checks is build/state/baseline.json from the
previous successful run (restored via actions/cache); first run has no
baseline, so drift checks pass vacuously.
"""

from __future__ import annotations

import json
import random
import sqlite3

import httpx

from etl import config


class IntegrityError(AssertionError):
    pass


def _check(cond: bool, msg: str, failures: list[str]) -> None:
    if not cond:
        failures.append(msg)


def run(spot_check: bool = False) -> dict:
    conn = sqlite3.connect(config.SQLITE_PATH)
    try:
        campaigns = conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
        empty_texts = conn.execute(
            """SELECT COUNT(*) FROM campaigns
               WHERE (defect IS NULL OR TRIM(defect) = '')
                 AND (action IS NULL OR TRIM(action) = '')"""
        ).fetchone()[0]
        quarantined = conn.execute("SELECT COUNT(*) FROM quarantine").fetchone()[0]
        kept = sum(
            conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in ("campaign_vehicles", "complaints", "investigations")
        )
    finally:
        conn.close()

    manifest = json.loads((config.PAGES_DIR / "manifest.json").read_text())
    total_docs = len(manifest)
    quarantine_rate = quarantined / (kept + quarantined) if (kept + quarantined) else 0.0

    baseline_path = config.STATE_DIR / "baseline.json"
    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else None

    failures: list[str] = []
    _check(
        campaigns > config.MIN_CAMPAIGN_COUNT,
        f"campaign count {campaigns} <= {config.MIN_CAMPAIGN_COUNT}",
        failures,
    )
    _check(
        quarantine_rate < config.MAX_QUARANTINE_RATE,
        f"quarantine rate {quarantine_rate:.2%} >= {config.MAX_QUARANTINE_RATE:.0%}",
        failures,
    )
    _check(
        empty_texts == 0,
        f"{empty_texts} campaigns with empty defect AND empty corrective action",
        failures,
    )
    if baseline:
        for name, current, prev, tol in (
            ("campaign count", campaigns, baseline["campaigns"], config.MAX_CAMPAIGN_DRIFT),
            ("total docs", total_docs, baseline["totalDocs"], config.MAX_DOC_COUNT_DRIFT),
        ):
            if prev and abs(current - prev) / prev > tol:
                failures.append(f"{name} drifted {current} vs {prev} (> ±{tol:.0%})")

    if spot_check:
        failures.extend(_spot_check_api())

    result = {
        "campaigns": campaigns,
        "totalDocs": total_docs,
        "quarantineRate": round(quarantine_rate, 5),
        "failures": failures,
    }
    print(json.dumps({"step": "verify", **result}, indent=2))
    if failures:
        raise IntegrityError("; ".join(failures))

    # Checks passed: this run becomes the next baseline.
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps({"campaigns": campaigns, "totalDocs": total_docs}))
    return result


def _spot_check_api(n: int = 5) -> list[str]:
    """Verify N random campaign numbers exist in the live NHTSA API (SPEC §10).

    This gate exists to catch systemic parse breakage (a column shift would
    turn every campaign number into garbage), so it fails only when a
    MAJORITY of the sampled campaigns are missing from the API. Individual
    misses and component-label differences are advisory: one campaign spans
    many make/model rows with different components, and the API occasionally
    lags fresh campaigns.
    """
    conn = sqlite3.connect(config.SQLITE_PATH)
    try:
        rows = conn.execute(
            """SELECT DISTINCT c.campno, c.component
               FROM campaigns c JOIN campaign_vehicles v USING (campno)
               WHERE v.year IS NOT NULL AND v.year >= 2012
               ORDER BY RANDOM() LIMIT ?""",
            (n * 4,),
        ).fetchall()
    finally:
        conn.close()

    missing: list[str] = []
    checked = 0
    with httpx.Client(timeout=30.0) as client:
        random.shuffle(rows)
        for campno, component in rows:
            if checked >= n:
                break
            try:
                resp = client.get(
                    config.RECALLS_BY_CAMPAIGN_URL, params={"campaignNumber": campno}
                )
                resp.raise_for_status()
                results = resp.json().get("results", [])
            except (httpx.HTTPError, ValueError) as err:
                print(f"spot-check skipped for {campno}: API error {err}")
                continue
            checked += 1
            if not any(r.get("NHTSACampaignNumber") == campno for r in results):
                missing.append(f"spot-check: campaign {campno} not in NHTSA API")
                continue
            head = (component or "").split(":")[0].strip().upper()
            if head and not any(
                head in (r.get("Component") or "").upper() for r in results
            ):
                print(
                    f"spot-check advisory: {campno} component {component!r} "
                    "not among API components (multi-component campaign?)"
                )
    if checked == 0:
        print("spot-check: API unreachable, skipping (non-fatal)")
        return []
    if len(missing) * 2 > checked:
        return missing
    for msg in missing:
        print(f"{msg} (advisory: below majority threshold, {checked} checked)")
    return []
