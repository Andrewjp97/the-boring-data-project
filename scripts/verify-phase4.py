#!/usr/bin/env python3
"""Phase 4 acceptance check (SPEC §11): analytics + SEO + ads polish.

Everything that can be verified without the live GA4/AdSense consoles:

  - every page kind sends its applicable GA4 custom dimensions on the
    page_view config (page_kind, recall_count_bucket, complaint_count_bucket,
    indexable always; make/model/model_year where the entity has them,
    omitted — never null — elsewhere)
  - Consent Mode v2: default 'denied' for all four signals is queued *before*
    the gtag config call on every page; ads_data_redaction is set
  - PUBLIC_GA_DEBUG=true builds carry debug_mode on the config (for the
    DebugView run); normal builds don't
  - noindex on a zero-data page (robots meta + indexable:'false' dim);
    indexable pages carry no robots meta; all pages self-canonicalize
  - JSON-LD on 5 sampled year pages satisfies Google's structural
    requirements for BreadcrumbList and FAQPage rich results
  - AdSlot + AffiliateBlock render *only* behind their flags: the flags-off
    build has zero ad/consent-banner/affiliate bytes; the flags-on build has
    fixed-height ad slots, the AdSense loader, the Funding Choices CMP, and
    rel="sponsored" affiliate links with the FTC disclosure
  - event wiring: affiliate_click / vin_decode / outbound_nhtsa /
    search_used / related_link_click delegated hooks are present
  - CLS == 0 on the ads-enabled year page and home page, measured in
    headless Chromium with all external requests blocked (worst case:
    ad slots never fill; reserved space must hold)

Runs two site builds (flags off, flags on) against either the real ETL page
docs (etl/build/pages/pages.jsonl) or the checked-in fixtures
(scripts/fixtures/phase4-pages.jsonl), served through the Firestore stub.

Prereqs: `pnpm install` at the repo root. Run:
    python3 scripts/verify-phase4.py [--pages PATH] [--skip-cls]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_PAGES = REPO_ROOT / "etl" / "build" / "pages" / "pages.jsonl"
FIXTURE_PAGES = REPO_ROOT / "scripts" / "fixtures" / "phase4-pages.jsonl"
SERVER_ENTRY = REPO_ROOT / "site" / "dist" / "server" / "entry.mjs"

GA_ID = "G-TEST"
ADSENSE_CLIENT = "ca-pub-0000000000000000"
AMAZON_TAG = "phase4test-20"

SCRIPT_BLOCK_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
CONFIG_RE = re.compile(r"const config = (\{.*?\});")

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
            return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"server at {url} did not come up")


def fetch(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(base + path, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


# --- sampling ------------------------------------------------------------------


def load_pages(pages_jsonl: Path) -> list[dict]:
    with pages_jsonl.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def richness(doc: dict) -> int:
    if doc["kind"] == "campaign":
        return len(doc.get("affectedVehicles", []))
    return doc.get("recallCount", 0) + min(doc.get("complaintTotal", 0), 500)


def sample_slugs(items: list[dict]) -> dict:
    """Slugs for the harness: richest doc per kind, 5 indexable year pages
    for rich-results checks, and one noindex (zero-data) page."""
    docs = [it["doc"] for it in items]
    best: dict[str, dict] = {}
    for doc in docs:
        cur = best.get(doc["kind"])
        if cur is None or richness(doc) > richness(cur):
            best[doc["kind"]] = doc
    missing = {"make", "model", "year", "campaign"} - set(best)
    if missing:
        raise RuntimeError(f"pages jsonl has no docs of kind(s): {missing}")

    year_pages = sorted(
        (d for d in docs if d["kind"] == "year" and d.get("indexable")),
        key=richness,
        reverse=True,
    )
    noindex = next(
        (d for d in docs if d["kind"] == "year" and not d.get("indexable")),
        next((d for d in docs if not d.get("indexable")), None),
    )
    if len(year_pages) < 5:
        raise RuntimeError("need >= 5 indexable year pages for rich-results sampling")
    if noindex is None:
        raise RuntimeError("no noindex (indexable: false) page doc to verify against")
    return {
        "kinds": {k: best[k] for k in ("make", "model", "year", "campaign")},
        "rich_results": year_pages[:5],
        "noindex": noindex,
    }


# --- HTML dissection -----------------------------------------------------------


def gtag_script(html: str) -> str | None:
    for block in SCRIPT_BLOCK_RE.findall(html):
        if "gtag('consent'" in block:
            return block
    return None


def gtag_config(html: str) -> dict | None:
    m = CONFIG_RE.search(html)
    return json.loads(m.group(1)) if m else None


def jsonld_blocks(html: str) -> list[dict]:
    out = []
    for block in SCRIPT_BLOCK_RE.findall(html):
        if 'type="application/ld+json"' in block[: block.index(">") + 1].lower():
            out.append(json.loads(block[block.index(">") + 1 : block.rindex("<")]))
    return out


def check_dims(kind: str, html: str, doc: dict | None) -> None:
    """Custom dims ride the gtag config on every page kind (SPEC §7.3)."""
    config = gtag_config(html)
    check(config is not None, f"{kind}: gtag config present")
    if config is None:
        return
    check(config.get("page_kind") == kind if kind != "vin" else config.get("page_kind") == "utility",
          f"{kind}: page_kind dim (got {config.get('page_kind')!r})")
    for dim in ("recall_count_bucket", "complaint_count_bucket", "indexable"):
        check(isinstance(config.get(dim), str) and config[dim] != "",
              f"{kind}: {dim} dim present")
    check(all(v is not None for v in config.values()),
          f"{kind}: no null dim values sent to gtag")
    if doc is None:
        return
    if kind in ("make", "model", "year"):
        check(config.get("make") == doc["make"]["slug"], f"{kind}: make dim")
    if kind in ("model", "year"):
        check(config.get("model") == doc["model"]["slug"], f"{kind}: model dim")
    if kind == "year":
        check(config.get("model_year") == doc["year"], "year: model_year dim")
        check(config.get("indexable") == ("true" if doc.get("indexable") else "false"),
              "year: indexable dim matches doc")


def check_consent(kind: str, html: str) -> None:
    """Consent Mode v2: denied-by-default must be queued before config."""
    script = gtag_script(html)
    check(script is not None, f"{kind}: consent/gtag inline script present")
    if script is None:
        return
    consent_at = script.find("gtag('consent', 'default'")
    config_at = script.find("gtag('config'")
    check(0 <= consent_at < config_at, f"{kind}: consent default queued before config")
    consent_block = script[consent_at : script.index(")", consent_at)]
    for signal in ("ad_storage", "ad_user_data", "ad_personalization", "analytics_storage"):
        check(f"{signal}: 'denied'" in consent_block, f"{kind}: {signal} defaults to denied")
    check("wait_for_update" in consent_block, f"{kind}: consent waits for CMP update")
    check("gtag('set', 'ads_data_redaction', true)" in script,
          f"{kind}: ads_data_redaction set")


def check_rich_results(slug: str, html: str, expect_faq: bool) -> None:
    """Structural requirements from Google's BreadcrumbList / FAQPage docs."""
    blocks = jsonld_blocks(html)
    by_type = {b.get("@type"): b for b in blocks}
    crumbs = by_type.get("BreadcrumbList")
    check(crumbs is not None, f"{slug}: BreadcrumbList present")
    if crumbs:
        items = crumbs.get("itemListElement", [])
        check(len(items) >= 2, f"{slug}: breadcrumb has >= 2 items")
        check([it.get("position") for it in items] == list(range(1, len(items) + 1)),
              f"{slug}: breadcrumb positions are 1..n")
        check(all(it.get("@type") == "ListItem" and it.get("name") for it in items),
              f"{slug}: breadcrumb items typed + named")
        check(all(str(it.get("item", "")).startswith("http") for it in items),
              f"{slug}: breadcrumb items are absolute URLs")
    if not expect_faq:
        return
    faq = by_type.get("FAQPage")
    check(faq is not None, f"{slug}: FAQPage present")
    if faq:
        qs = faq.get("mainEntity", [])
        check(len(qs) >= 2, f"{slug}: FAQ has >= 2 questions")
        for q in qs:
            ok = (
                q.get("@type") == "Question"
                and q.get("name", "").strip()
                and q.get("acceptedAnswer", {}).get("@type") == "Answer"
                and q.get("acceptedAnswer", {}).get("text", "").strip()
            )
            check(bool(ok), f"{slug}: question complete ({q.get('name', '?')[:50]}…)")


# --- build + serve --------------------------------------------------------------


def build_site(env_flags: dict[str, str]) -> None:
    print(f"\n=== building site with {env_flags} ===")
    subprocess.run(
        ["pnpm", "--filter", "site", "build"],
        cwd=REPO_ROOT,
        env={**os.environ, **env_flags},
        check=True,
        stdout=subprocess.DEVNULL,
    )


class Servers:
    def __init__(self, pages_jsonl: Path):
        self.pages_jsonl = pages_jsonl
        self.procs: list[subprocess.Popen] = []
        self.base = ""

    def __enter__(self) -> "Servers":
        stub_port, site_port = free_port(), free_port()
        self.procs.append(
            subprocess.Popen(
                [sys.executable, str(REPO_ROOT / "scripts" / "firestore-stub.py"),
                 "--port", str(stub_port), "--pages", str(self.pages_jsonl)],
                stdout=subprocess.DEVNULL,
            )
        )
        self.procs.append(
            subprocess.Popen(
                ["node", str(SERVER_ENTRY)],
                env={**os.environ, "HOST": "127.0.0.1", "PORT": str(site_port),
                     "GCP_PROJECT": "local",
                     "FIRESTORE_EMULATOR_HOST": f"127.0.0.1:{stub_port}"},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
        wait_http(f"http://127.0.0.1:{stub_port}/v1/x")
        self.base = f"http://127.0.0.1:{site_port}"
        wait_http(self.base + "/")
        return self

    def __exit__(self, *exc) -> None:
        for proc in self.procs:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


# --- phases ---------------------------------------------------------------------


def phase_flags_off(samples: dict, pages_jsonl: Path) -> None:
    build_site({"PUBLIC_GA_ID": GA_ID, "PUBLIC_GA_DEBUG": "", "PUBLIC_ADS_ENABLED": "",
                "PUBLIC_ADSENSE_CLIENT": "", "PUBLIC_AMAZON_TAG": ""})
    with Servers(pages_jsonl) as servers:
        base = servers.base
        pages = [("home", "/", None), ("vin", "/vin/", None)] + [
            (kind, f"/{doc['slug']}/", doc) for kind, doc in samples["kinds"].items()
        ]
        for kind, path, doc in pages:
            status, html = fetch(base, path)
            print(f"\n[flags off] {kind}  {path}")
            check(status == 200, f"{kind}: HTTP 200 (got {status})")
            check_dims(kind, html, doc)
            check_consent(kind, html)
            config = gtag_config(html) or {}
            check("debug_mode" not in config, f"{kind}: no debug_mode in normal build")
            for marker, what in (
                ("adsbygoogle", "AdSense"),
                ("google-adsense-account", "AdSense verification meta"),
                ("fundingchoicesmessages", "consent banner"),
                ("amazon.com", "affiliate links"),
            ):
                check(marker not in html, f"{kind}: no {what} when flags off")

        # noindex: zero-data page carries robots meta; rich page doesn't
        noindex_doc = samples["noindex"]
        status, html = fetch(base, f"/{noindex_doc['slug']}/")
        print(f"\n[flags off] noindex  /{noindex_doc['slug']}/")
        check(status == 200, "noindex page: HTTP 200")
        check('<meta name="robots" content="noindex,follow">' in html,
              "noindex page: robots noindex,follow meta")
        check('rel="canonical"' in html, "noindex page: still self-canonical")
        check((gtag_config(html) or {}).get("indexable") == "false",
              "noindex page: indexable dim is 'false'")
        rich_doc = samples["kinds"]["year"]
        _, rich_html = fetch(base, f"/{rich_doc['slug']}/")
        check('name="robots"' not in rich_html, "indexable year page: no robots meta")

        # rich results on 5 sampled indexable year pages
        print("\n[flags off] rich results (5 sampled year pages)")
        for doc in samples["rich_results"]:
            _, html = fetch(base, f"/{doc['slug']}/")
            check_rich_results(doc["slug"], html, expect_faq=True)

        # event wiring (delegated listeners, SPEC §7.4)
        print("\n[flags off] event wiring")
        _, year_html = fetch(base, f"/{rich_doc['slug']}/")
        check(year_html.count('data-evt="outbound_nhtsa"') >= 2,
              "year: outbound_nhtsa on VIN CTA + footer")
        nav = rich_doc.get("nav", {})
        for key, link_type in (("years", "year"), ("siblingModels", "sibling"),
                               ("relatedByComponent", "component")):
            if nav.get(key):
                # Astro entity-encodes the JSON in the attribute value
                check(f"&quot;link_type&quot;:&quot;{link_type}&quot;" in year_html,
                      f"year: related_link_click link_type={link_type}")
        _, vin_html = fetch(base, "/vin/")
        check("vin_decode" in vin_html, "vin: vin_decode event dispatch")
        check('name="robots" content="noindex,follow"' in vin_html, "vin: noindex utility page")
        _, home_html = fetch(base, "/")
        check("search_used" in home_html, "home: search_used event dispatch")


def phase_flags_on(samples: dict, pages_jsonl: Path, skip_cls: bool) -> None:
    build_site({"PUBLIC_GA_ID": GA_ID, "PUBLIC_GA_DEBUG": "true",
                "PUBLIC_ADS_ENABLED": "true", "PUBLIC_ADSENSE_CLIENT": ADSENSE_CLIENT,
                "PUBLIC_AMAZON_TAG": AMAZON_TAG})
    with Servers(pages_jsonl) as servers:
        base = servers.base
        year_doc = samples["kinds"]["year"]
        year_path = f"/{year_doc['slug']}/"
        status, html = fetch(base, year_path)
        print(f"\n[flags on] year  {year_path}")
        check(status == 200, "year: HTTP 200")

        check(f"adsbygoogle.js?client={ADSENSE_CLIENT}" in html,
              "year: AdSense loader in head with client id")
        check(f'<meta name="google-adsense-account" content="{ADSENSE_CLIENT}">' in html,
              "year: AdSense account verification meta tag")
        slots = re.findall(r'class="ad-slot" style="min-height:(\d+)px"', html)
        check(len(slots) == 2, f"year: two fixed-height ad slots (got {len(slots)})")
        check(html.count('data-ad-client="' + ADSENSE_CLIENT + '"') == 2,
              "year: ad units carry the client id")
        check('data-ad-slot="year-page-1"' in html and 'data-ad-slot="year-page-2"' in html,
              "year: both ad-unit slot ids present")
        check(html.count(">Advertisement<") == 2, "year: ad slots labeled 'Advertisement'")
        answer_at = html.find("answer-box")
        first_ad_at = html.find('class="ad-slot"')
        check(0 <= answer_at < first_ad_at, "year: no ad above the answer box")

        check("fundingchoicesmessages.google.com" in html, "year: Funding Choices CMP loads")

        check(f"tag={AMAZON_TAG}" in html, "year: affiliate links carry the Associates tag")
        check('rel="sponsored nofollow noopener"' in html, "year: affiliate rel=sponsored")
        check('data-evt="affiliate_click"' in html, "year: affiliate_click event wired")
        check("As an Amazon Associate we earn from qualifying purchases" in html,
              "year: FTC disclosure present")

        config = gtag_config(html) or {}
        check(config.get("debug_mode") is True, "year: debug_mode on PUBLIC_GA_DEBUG build")
        check_consent("year", html)

        for kind in ("make", "model", "campaign"):
            _, khtml = fetch(base, f"/{samples['kinds'][kind]['slug']}/")
            n = len(re.findall(r'class="ad-slot"', khtml))
            check(n == 1, f"{kind}: one fixed-height ad slot (got {n})")

        if skip_cls:
            print("\n[flags on] CLS check skipped (--skip-cls)")
            return
        print("\n[flags on] CLS in headless Chromium (external requests blocked)")
        result = subprocess.run(
            ["node", str(REPO_ROOT / "scripts" / "measure-cls.mjs"),
             base + year_path, base + "/"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        check(result.returncode == 0, f"CLS runner exits 0 ({result.stderr.strip()[:200]})")
        for line in result.stdout.strip().splitlines():
            measurement = json.loads(line)
            check(measurement["cls"] < 0.01,
                  f"CLS < 0.01 on {measurement['url']} (got {measurement['cls']})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=Path,
                        default=REAL_PAGES if REAL_PAGES.exists() else FIXTURE_PAGES)
    parser.add_argument("--skip-cls", action="store_true",
                        help="skip the headless-Chromium CLS measurement")
    args = parser.parse_args()

    print(f"page docs: {args.pages}")
    samples = sample_slugs(load_pages(args.pages))
    print(f"sampled kinds: { {k: d['slug'] for k, d in samples['kinds'].items()} }")
    print(f"noindex page: {samples['noindex']['slug']}")

    phase_flags_off(samples, args.pages)
    phase_flags_on(samples, args.pages, skip_cls=args.skip_cls)

    print(f"\n{'PASS' if not failures else 'FAIL'}: {len(failures)} failure(s)")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
