# RecallLookup

Programmatic SEO reference site answering **"{year} {make} {model} recalls"** — one clean,
fast page per vehicle entity, built entirely on public-domain NHTSA data. See [SPEC.md](SPEC.md)
for the full design.

**Prime directive: near-zero human maintenance.** Every recurring task runs unattended in CI;
the only steady-state human touchpoint is the GitHub issue auto-opened when the weekly sync
fails its integrity assertions.

## Architecture

```
weekly GitHub Action
  └─ etl (Python/polars): download → parse → normalize → build.db → page docs
       └─ diff vs last week → Firestore upserts (changed docs only)
       └─ sitemaps + search-index.json → Firebase Hosting deploy (flushes CDN)

request path
  Firebase Hosting CDN (s-maxage=7d) → Cloud Run (Astro SSR) → 1 Firestore read
```

Key invariants:

- **One page render = one Firestore document read.** Documents are denormalized to page
  granularity in `etl/src/etl/build_pages.py`; the site never joins.
- **Cache TTL == sync cadence (7 days).** Firebase Hosting has no per-URL purge; the weekly
  Hosting deploy *is* the purge.
- **Integrity assertions run before any Firestore write** (`etl verify`): campaign count floor
  and drift, quarantine rate < 3%, no empty defect texts, doc-size and doc-count checks, plus a
  live spot-check against the NHTSA JSON API. Failure → issue auto-opened, old data keeps serving.

## Repo layout

| Path | What |
| --- | --- |
| `etl/` | Python 3.11+ (uv, polars, httpx). CLI: `uv run etl --help` |
| `site/` | Astro 5 SSR (`@astrojs/node`), Dockerfile for Cloud Run |
| `.github/workflows/` | `sync.yml` (weekly data), `deploy.yml` (push to main), `smoke.yml` |
| `firebase.json` / `firestore.rules` | Hosting rewrite → Cloud Run; deny-all client rules |
| `analytics/queries/` | Starter BigQuery queries against the GA4 export |

## Local development

```bash
# ETL — full local pipeline, no Firestore needed (~15 min, ~2 GB disk):
cd etl && uv sync
uv run etl all --local --force

# outputs: etl/build/build.db, etl/build/pages/pages.jsonl,
#          site/public/search-index.json, site/public/sitemaps/

# ETL tests / gates:
uv run pytest && uv run ruff check src tests && uv run mypy src

# Site:
pnpm install
pnpm --filter site test
pnpm --filter site build

# Site against real data without GCP: serve page docs through the Firestore
# emulator, or set FIRESTORE_ACCESS_TOKEN + GCP_PROJECT for a real project.

# Firestore security rules (deny-all client access), via the real emulator:
pnpm --filter site test:rules

# Phase 2 acceptance harness — all five page kinds end-to-end against the
# real page docs (needs `etl all --local` output + a site build):
python3 scripts/verify-site.py

# Phase 4 acceptance harness — analytics dims, consent mode, rich results,
# noindex, ad/affiliate flag matrix, CLS. Builds the site itself (twice) and
# runs against real page docs if present, else the checked-in fixtures:
python3 scripts/verify-phase4.py
```

## Phase 2 verification (SPEC §11) — verified July 2026

Run locally against a full real-data ETL build (123,884 page docs from live NHTSA files):

- **All five page kinds render** (home, make hub, model hub, year, campaign — plus `/vin/`
  and 404 recovery): `scripts/verify-site.py` passes — HTTP 200, `s-maxage=604800` on SSR
  pages, JSON-LD parses (BreadcrumbList everywhere, FAQPage on year pages), gtag present,
  zero client JS on entity pages beyond gtag/consent, 404s are `no-store`.
- **Lighthouse on the year template**: performance 100 / SEO 100 (gate: ≥95 / 100),
  accessibility 96, best-practices 96, CLS 0.
- **SSR latency**: 10–32 ms per render locally (gate: <500 ms cold; production adds
  Firestore RTT ≈ 10–30 ms in-region).
- **Firestore rules deny all client access**: `site/test/rules.test.ts` proves reads,
  writes, and deletes fail for unauthenticated *and* authenticated clients on `pages`,
  `campaignPages`, and `meta`, against the real Firestore emulator.

Deploy-time checks that need the live GCP project (do once at bring-up): second request to
`/recalls/honda/cr-v/2016/` returns `x-cache: HIT` from Hosting with <50 ms edge latency.

To load a real Firestore once (Phase 2 bring-up): `gcloud auth application-default login`,
then `cd etl && uv run etl diff --full && GOOGLE_CLOUD_PROJECT=<project> uv run etl push-firestore`.

## Phase 3 verification (SPEC §11) — automation, verified July 2026

The weekly pipeline's failure modes are covered by `etl/tests/test_automation.py`
(`uv run pytest tests/test_automation.py -q`) and were rehearsed end-to-end on live NHTSA
data as three consecutive "weeks" of `sync.yml` semantics:

1. **Week 1** — full pipeline (`etl all --local --force`): 24,376 campaigns, 123,884 page
   docs, quarantine 1.30%, every integrity assertion green, 123,884 upserts diffed,
   109,859 indexable URLs across 3 sitemap shards + index.
2. **Week 2** — same command, unchanged upstream files: exits `no-op week` at the download
   step. Zero parse/build/push work.
3. **Corrupt week** — recalls zip truncated after download (what the CI drill does):
   `etl parse` fails with `BadZipFile`, exit 1, and `build/state/` is byte-identical
   afterwards (sha256-verified) — old data keeps serving. Restoring the good file and
   re-running `etl download` correctly reports `changed: false` against the last *pushed*
   baseline: a failed week is never mistaken for a completed one.

- **No-change week no-ops at step 2**: `etl download` compares this week's checksums against
  `build/state/checksums.json`. That baseline is advanced *only* by a completed push
  (`push_firestore` calls `download.commit_state_checksums()`), so a failed week is
  re-processed from scratch instead of being skipped.
- **Corrupt-file failure leaves prod untouched**: a truncated zip fails at parse, integrity
  drift fails at verify — both before any Firestore write, with `build/state/` byte-identical
  after the failure (tests + local rehearsal). To rehearse the *full* alert path in CI, dispatch
  `sync.yml` with `drill: corrupt-file`: the run corrupts its own download, fails before the
  push, and auto-opens a `sync-drill`-labeled issue. Prod data and the CDN are never touched.
- **Partial pushes cannot pass silently**: BulkWriter's default swallows terminal per-document
  write errors; `push_firestore` now records them and fails the run (which opens the issue and
  keeps the no-op baseline un-advanced).
- **State survives quiet weeks**: `sync.yml` re-saves the `actions/cache` state entry on no-op
  weeks too — GitHub evicts caches untouched for 7 days, which is exactly the sync cadence.
- **Sitemaps conform to the protocol**: shards and index are schema-checked in tests
  (namespace, `<loc>`/`<lastmod>` shapes, shard size ≤ 45k, index ↔ shard integrity,
  exactly the indexable URL set).

Remaining production-only checks (need the live GCP project + domain, SPEC §11):
submit `sitemap-index.xml` in GSC and confirm it validates; watch the first two scheduled
Monday runs complete unattended; optionally run the `corrupt-file` drill once in the real
repo to see the issue arrive.

## Phase 4 verification (SPEC §11) — analytics + SEO + ads polish, verified July 2026

Everything checkable without the live GA4/AdSense consoles is automated in
`scripts/verify-phase4.py` (builds the site twice — flags off and on — and serves page docs
through the Firestore stub; uses `scripts/fixtures/phase4-pages.jsonl` when no real ETL
build is present). Verified locally, all checks green:

- **Custom dims on every page kind**: the gtag config on home/make/model/year/campaign/vin
  carries `page_kind`, `make`, `model`, `model_year`, `recall_count_bucket`,
  `complaint_count_bucket`, `indexable` (entity dims are *omitted*, never sent as null,
  where inapplicable). `PUBLIC_GA_DEBUG=true` builds add `debug_mode` to every hit so the
  one-time GA4 DebugView run is just: deploy a debug build, click through the five page
  kinds, watch the dims arrive.
- **Consent-denied path**: Consent Mode v2 default `denied` for all four signals (with
  `wait_for_update`) is queued *before* the config call on every page, and
  `ads_data_redaction` is set. The Funding Choices CMP loads only when
  `PUBLIC_ADSENSE_CLIENT` is set.
- **Rich results**: BreadcrumbList + FAQPage JSON-LD on 5 sampled year pages satisfies
  Google's structural requirements (typed ListItems with contiguous positions and absolute
  URLs; ≥2 complete Question/Answer pairs).
- **noindex**: a zero-data year page renders HTTP 200 with `noindex,follow`, stays
  self-canonical, and reports `indexable:'false'` in its dims; indexable pages carry no
  robots meta; the live smoke check now also fails if any sitemap-sampled URL ever carries
  noindex.
- **Ads/affiliate flag matrix**: flags-off builds contain zero AdSense/CMP/affiliate bytes.
  Flags-on builds render both year-page slots (fixed-height reservation, labeled, never
  above the answer box), one slot on hub/campaign pages, the AdSense loader with the client
  id, and the AffiliateBlock with `rel="sponsored"`, the `affiliate_click` event, and the
  FTC disclosure.
- **CLS = 0** measured in headless Chromium (mobile viewport, all external requests blocked
  so ad slots never fill — the worst case for reserved space) on the ads-enabled year page
  and home page.
- **Found & fixed**: `<AdSlot slot="…">` never rendered — `slot` is Astro's reserved
  named-slot attribute, so every ad unit was silently assigned to a nonexistent layout slot
  and dropped. The prop is now `slotId`; the harness pins the rendered output so it can't
  regress.

Remaining console-only steps (SPEC §12, after the site is live): create the GA4 property and
register the seven custom dimensions, run the DebugView pass with a `PUBLIC_GA_DEBUG=true`
build, enable the BigQuery daily export and confirm the first `events_*` table lands
(`analytics/queries/*.sql` are ready against it), run Google's Rich Results Test on 5
production URLs, apply to AdSense once indexed, then set `PUBLIC_ADSENSE_CLIENT` /
`PUBLIC_ADS_ENABLED` and the real `ads.txt` line.

## Data notes (verified July 2026)

- NHTSA split the recalls flat file: `FLAT_RCL_PRE_2010.zip` + `FLAT_RCL_POST_2010.zip`
  (the single `FLAT_RCL.zip` in older docs 404s).
- Parser column maps are generated from the live layout files (`RCL.txt`, `CMPL.txt`,
  `INV.txt`) each run; vendored snapshots in `etl/data/layouts/` are the fallback and test
  fixtures. `CONEQUENCE_DEFECT` is NHTSA's long-standing typo — real.
- Vehicle-only scope (`RCLTYPECD='V'`, `PROD_TYPE='V'`): ~20k campaigns, ~2.1M complaints,
  ~96k page docs. Non-vehicle rows are excluded by design; unmatched makes are quarantined
  (never silently dropped) and surfaced in the CI job summary.
- Pre-1977 campaigns keep their entire description in `NOTES`; the ETL promotes it to
  `defect` so pages have content and the no-empty-text assertion holds.

## One-time human setup (SPEC §12)

1. **Domain** — buy it; set `SITE_URL` repo variable; DNS-verify GSC + Bing WMT.
2. **Firebase/GCP project** (separate from any existing project): enable Blaze, Firestore,
   Cloud Run, Artifact Registry (`site` Docker repo, us-central1), Cloud Build.
   **Set a $25/mo budget alert** in Billing → Budgets (console step, no Terraform).
3. **Workload Identity Federation**: create a pool/provider for GitHub OIDC and two service
   accounts — `etl-sync` (Firestore write + Hosting deploy) and `deploy` (Cloud Run deploy +
   Hosting). Set repo Actions variables: `GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`, `GCP_ETL_SA`,
   `GCP_DEPLOY_SA`, `SITE_URL`.
4. **GA4** property → done: `G-ZM0SQZ4EHB` is the default `PUBLIC_GA_ID` in `deploy.yml`
   (a repo variable overrides it). Still to do in the console: register the custom
   dimensions (`page_kind`, `make`, `model`, `model_year`, `recall_count_bucket`,
   `complaint_count_bucket`, `indexable`); link AdSense + Search Console; enable BigQuery
   daily export; set up the Funding Choices CMP message.
5. **AdSense** — account `ca-pub-2804638991683725` is wired: real publisher line in
   `site/public/ads.txt`, and the `google-adsense-account` verification meta tag renders
   on every page whenever `PUBLIC_ADSENSE_CLIENT` is set (the `deploy.yml` default),
   independent of `PUBLIC_ADS_ENABLED` — so the site verifies during the application
   while ads stay off. Apply once live + indexed, then flip `PUBLIC_ADS_ENABLED=true`.
6. **Amazon Associates** — set `PUBLIC_AMAZON_TAG` and replace the placeholder ASINs in
   `site/src/data/affiliate-map.json` (~15 hand-picked products).

## Future verticals (design only — SPEC Phase 5)

The adapter seam is: a new parser module (`etl/src/etl/parse.py` dataset entry + layout),
a page-builder module emitting docs into its own collection, and a route group under
`site/src/pages/`. Candidates: CPSC recalls, FAA registry. Not implemented in v1.
