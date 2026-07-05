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
```

To load a real Firestore once (Phase 2 bring-up): `gcloud auth application-default login`,
then `cd etl && uv run etl diff --full && GOOGLE_CLOUD_PROJECT=<project> uv run etl push-firestore`.

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
4. **GA4** property → `PUBLIC_GA_ID` variable; register the custom dimensions
   (`page_kind`, `make`, `model`, `model_year`, `recall_count_bucket`,
   `complaint_count_bucket`, `indexable`); link AdSense + Search Console; enable BigQuery
   daily export; set up the Funding Choices CMP message.
5. **AdSense** — apply once live + indexed; then set `PUBLIC_ADSENSE_CLIENT`, put the real
   publisher line in `site/public/ads.txt`, and flip `PUBLIC_ADS_ENABLED=true`.
6. **Amazon Associates** — set `PUBLIC_AMAZON_TAG` and replace the placeholder ASINs in
   `site/src/data/affiliate-map.json` (~15 hand-picked products).

## Future verticals (design only — SPEC Phase 5)

The adapter seam is: a new parser module (`etl/src/etl/parse.py` dataset entry + layout),
a page-builder module emitting docs into its own collection, and a route group under
`site/src/pages/`. Candidates: CPSC recalls, FAA registry. Not implemented in v1.
