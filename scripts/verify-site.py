#!/usr/bin/env python3
"""Phase 2 acceptance check (SPEC §11): all five page kinds render end-to-end.

Boots the local Firestore REST stub (real ETL page docs) plus the built Astro
server, then asserts against each page kind:

  - HTTP 200 with non-trivial HTML
  - Cache-Control: s-maxage=604800 on SSR entity pages (CDN TTL == sync cadence)
  - every JSON-LD block parses; year pages carry BreadcrumbList + FAQPage
  - gtag present when the site was built with PUBLIC_GA_ID
  - zero client JS on entity pages beyond gtag/consent/ads (SPEC §6);
    the search island is allowed only on home/hub/404 pages
  - unknown URLs return 404 with no-store

Prereqs: `uv run etl all --local` (page docs) and a site build:
    cd site && PUBLIC_GA_ID=G-TEST pnpm build
Run:
    python3 scripts/verify-site.py
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PAGES_JSONL = REPO_ROOT / "etl" / "build" / "pages" / "pages.jsonl"
SERVER_ENTRY = REPO_ROOT / "site" / "dist" / "server" / "entry.mjs"

SCRIPT_RE = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("  ok  " if cond else "  FAIL") + f"  {label}")
    if not cond:
        failures.append(label)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except urllib.error.HTTPError:
            return  # server is up; status is checked later
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"server at {url} did not come up")


def fetch(base: str, path: str) -> tuple[int, dict[str, str], str]:
    req = urllib.request.Request(base + path)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, headers, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        headers = {k.lower(): v for k, v in e.headers.items()}
        return e.code, headers, e.read().decode("utf-8", errors="replace")


def pick_sample_slugs() -> dict[str, str]:
    """One slug per page kind out of the real page docs; prefer a year page
    that actually has recalls so the render exercises every section."""
    best: dict[str, tuple[int, str]] = {}
    with PAGES_JSONL.open(encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            doc = item["doc"]
            kind = doc["kind"]
            richness = doc.get("recallCount", 0) + min(doc.get("complaintTotal", 0), 500)
            if kind == "campaign":
                richness = len(doc.get("affectedVehicles", []))
            cur = best.get(kind)
            if cur is None or richness > cur[0]:
                best[kind] = (richness, doc["slug"])
    missing = {"make", "model", "year", "campaign"} - set(best)
    if missing:
        raise RuntimeError(f"pages.jsonl has no docs of kind(s): {missing}")
    return {kind: slug for kind, (_, slug) in best.items()}


def classify_scripts(html: str) -> dict[str, int]:
    counts = {"jsonld": 0, "gtag": 0, "consent": 0, "ads": 0, "search": 0, "vin": 0, "other": 0}
    for block in SCRIPT_BLOCK_RE.findall(html):
        tag = block[: block.index(">") + 1].lower()
        body = block
        if 'type="application/ld+json"' in tag:
            counts["jsonld"] += 1
        elif "googletagmanager.com" in tag or "gtag(" in body:
            counts["gtag"] += 1
        elif "fundingchoicesmessages" in tag or "googlefcPresent" in body:
            counts["consent"] += 1
        elif "adsbygoogle" in tag or "adsbygoogle" in body:
            counts["ads"] += 1
        elif "vehicle-search" in body:
            counts["search"] += 1
        elif "vin-form" in body or "vin-input" in body:
            counts["vin"] += 1
        else:
            counts["other"] += 1
    return counts


def jsonld_types(html: str) -> list[str]:
    types = []
    for block in SCRIPT_BLOCK_RE.findall(html):
        if 'application/ld+json' not in block[: block.index(">") + 1]:
            continue
        payload = block[block.index(">") + 1 : block.rindex("<")]
        obj = json.loads(payload)  # raises on invalid JSON-LD → test failure
        types.append(obj.get("@type", "?"))
    return types


def main() -> int:
    if not PAGES_JSONL.exists():
        print(f"missing {PAGES_JSONL} — run `uv run etl all --local` first", file=sys.stderr)
        return 2
    if not SERVER_ENTRY.exists():
        print(f"missing {SERVER_ENTRY} — run `pnpm --filter site build` first", file=sys.stderr)
        return 2

    slugs = pick_sample_slugs()
    print(f"sample slugs: {json.dumps(slugs, indent=2)}")

    stub_port, site_port = free_port(), free_port()
    stub_script = REPO_ROOT / "scripts" / "firestore-stub.py"
    stub = subprocess.Popen(
        [sys.executable, str(stub_script), "--port", str(stub_port)],
        stdout=subprocess.DEVNULL,
    )
    site = subprocess.Popen(
        ["node", str(SERVER_ENTRY)],
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOST": "127.0.0.1",
            "PORT": str(site_port),
            "GCP_PROJECT": "local",
            "FIRESTORE_EMULATOR_HOST": f"127.0.0.1:{stub_port}",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{site_port}"
    try:
        wait_http(f"http://127.0.0.1:{stub_port}/v1/x")
        wait_http(base + "/")

        pages = [
            ("home", "/", False),
            ("make", f"/{slugs['make']}/", True),
            ("model", f"/{slugs['model']}/", True),
            ("year", f"/{slugs['year']}/", True),
            ("campaign", f"/{slugs['campaign']}/", True),
            ("vin", "/vin/", False),
        ]

        home_html = fetch(base, "/")[2]
        gtag_built_in = "gtag(" in home_html
        if not gtag_built_in:
            print("note: site built without PUBLIC_GA_ID — gtag checks relaxed")

        for kind, path, is_ssr_entity in pages:
            status, headers, html = fetch(base, path)
            print(f"\n{kind}  {path}")
            check(status == 200, f"{kind}: HTTP 200 (got {status})")
            check(len(html) > 2000, f"{kind}: non-trivial HTML ({len(html)} bytes)")
            check("<h1" in html, f"{kind}: has <h1>")

            if is_ssr_entity:
                cc = headers.get("cache-control", "")
                check(
                    "s-maxage=604800" in cc,
                    f"{kind}: Cache-Control s-maxage=604800 (got {cc!r})",
                )

            try:
                types = jsonld_types(html)
                check(True, f"{kind}: JSON-LD parses ({types})")
            except json.JSONDecodeError as e:
                check(False, f"{kind}: JSON-LD parse error: {e}")
                types = []
            if kind == "year":
                check("BreadcrumbList" in types, "year: BreadcrumbList JSON-LD")
                check("FAQPage" in types, "year: FAQPage JSON-LD")
            if kind in ("make", "model", "campaign"):
                check("BreadcrumbList" in types, f"{kind}: BreadcrumbList JSON-LD")

            counts = classify_scripts(html)
            if gtag_built_in:
                check(counts["gtag"] >= 1, f"{kind}: gtag present")
            check(counts["other"] == 0, f"{kind}: no unexpected scripts ({counts})")
            if kind in ("year", "campaign"):
                check(
                    counts["search"] == 0 and counts["vin"] == 0,
                    f"{kind}: no search/vin islands on entity page",
                )
            if kind != "vin":
                check(counts["vin"] == 0, f"{kind}: no VIN decoder script")

        # 404 path: unknown vehicle → 404 status, no-store, recovery UI
        status, headers, html = fetch(base, "/recalls/nonexistent-make/xyz/1900/")
        print("\n404  /recalls/nonexistent-make/xyz/1900/")
        check(status == 404, f"404: HTTP 404 (got {status})")
        check("no-store" in headers.get("cache-control", ""), "404: Cache-Control no-store")
        check("vehicle-search" in html, "404: search recovery UI present")
    finally:
        stub.terminate()
        site.terminate()
        stub.wait(timeout=10)
        site.wait(timeout=10)

    print(f"\n{'PASS' if not failures else 'FAIL'}: {len(failures)} failure(s)")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
