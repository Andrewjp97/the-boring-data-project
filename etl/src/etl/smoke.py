"""Post-deploy smoke check (SPEC §10, smoke job).

Samples N indexable URLs from the state manifest (changed docs preferred),
fetches each from the live site, and asserts: HTTP 200, gtag present,
JSON-LD parses, and the page shows either a recall list or the valid
empty state. Exits non-zero on any failure.
"""

from __future__ import annotations

import json
import os
import random
import re
import sys

import httpx

from etl import config


def sample_slugs(n: int = 20) -> list[str]:
    manifest = json.loads((config.STATE_DIR / "manifest.json").read_text())
    changed_path = config.DIFF_DIR / "changed.jsonl"
    preferred: list[str] = []
    if changed_path.exists():
        with changed_path.open(encoding="utf-8") as f:
            for line in f:
                item = json.loads(line)
                entry = manifest.get(item["id"])
                if entry and entry.get("indexable"):
                    preferred.append(entry["slug"])
    pool = preferred or [e["slug"] for e in manifest.values() if e.get("indexable")]
    random.shuffle(pool)
    return pool[:n]


def check_url(client: httpx.Client, url: str) -> list[str]:
    problems: list[str] = []
    try:
        resp = client.get(url)
    except httpx.HTTPError as err:
        return [f"{url}: request failed ({err})"]
    if resp.status_code != 200:
        return [f"{url}: HTTP {resp.status_code}"]
    html = resp.text
    # The gtag snippet only renders when GA4 is configured (Analytics.astro
    # gates on PUBLIC_GA_ID) — a pre-Phase-4 deploy legitimately has none.
    if os.environ.get("PUBLIC_GA_ID"):
        if "googletagmanager.com/gtag" not in html and "gtag(" not in html:
            problems.append(f"{url}: gtag snippet missing")
    # Sampled URLs come from the sitemap/manifest indexable set — a robots
    # noindex here means the indexable flag and the template disagree.
    if re.search(r'<meta name="robots" content="[^"]*noindex', html):
        problems.append(f"{url}: sitemap URL carries robots noindex")
    ld_blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL
    )
    if not ld_blocks:
        problems.append(f"{url}: no JSON-LD")
    for block in ld_blocks:
        try:
            json.loads(block)
        except ValueError:
            problems.append(f"{url}: JSON-LD does not parse")
    lowered = html.lower()
    has_content = (
        "recall-card" in html  # entity page with recall list
        or "no nhtsa recalls on record" in lowered  # valid empty state
        or "nhtsa recall " in lowered  # campaign detail page
    )
    if not has_content:
        problems.append(f"{url}: neither recall list nor valid empty state")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    n = int(args[0]) if args else 20
    slugs = sample_slugs(n)
    if not slugs:
        print("smoke: no slugs to sample (empty manifest?)")
        return 1
    failures: list[str] = []
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for slug in slugs:
            url = f"{config.SITE_URL}/{slug}/"
            problems = check_url(client, url)
            print(("FAIL " if problems else "ok   ") + url)
            failures.extend(problems)
    for p in failures:
        print(p)
    print(f"smoke: {len(slugs) - len(failures)}/{len(slugs)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
