# SPEC — “RecallLookup” (working name)

## Programmatic SEO entity-page site on public NHTSA data — Firebase edition

**Owner:** Andrew Paterson / ZeroBound LLC
**Audience for this doc:** Claude Code (implementation agent)
**Status:** v2.0 — Firebase/GCP stack, deep GA4 instrumentation, Google display ads
**Prime directive:** Near-zero human maintenance after launch. Every recurring task runs unattended in CI. When a design choice trades elegance for hands-off operation, choose hands-off.

-----

## 1. Product summary

A reference site answering **”{year} {make} {model} recalls”** (+ complaints, investigations, “is my car recalled”) with one clean, fast, structured page per vehicle entity, built entirely on free public-domain NHTSA data. **60k–120k indexable pages** of long-tail queries. Weekly automated data sync. Monetized with Google AdSense display ads + contextual Amazon affiliate links.

### Architecture in one paragraph

Weekly GitHub Action downloads NHTSA flat files → Python ETL (polars) parses/normalizes into a local SQLite build artifact → diffs against last week → writes **denormalized page documents to Firestore** (one document per URL) → **Astro SSR app on Cloud Run**, fronted by **Firebase Hosting** (rewrite → Cloud Run) so every response rides the Firebase global CDN with week-long `s-maxage` caching → each page render costs **exactly one Firestore read**, and CDN cache absorbs repeats. GA4 (gtag + custom dimensions + BigQuery export) instruments everything; AdSense slots ship behind an env flag. Weekly CI finishes with a `firebase deploy --only hosting` which simultaneously publishes fresh sitemaps and flushes the CDN cache.

> **Why this shape on Firebase:** Firebase Hosting has no per-URL cache purge API, so we align the cache TTL (7 days) with the sync cadence (7 days) and use the deploy-flushes-CDN behavior as the purge mechanism. Firestore charges per document read, so the schema is denormalized to page-granularity — never joins, never N reads per render. These two decisions are what keep the bill near zero.

-----

## 2. Data sources (Vertical 1: NHTSA)

⚠️ **Claude Code: verify every URL at implementation time.** If a flat file 404s, check `https://www.nhtsa.gov/nhtsa-datasets-and-apis`. Prefer flat files for bulk; the JSON API is for CI spot-verification only.

|Dataset         |Source                                                |Format       |Approx size                    |Sync    |
|----------------|------------------------------------------------------|-------------|-------------------------------|--------|
|Recalls         |`https://static.nhtsa.gov/odi/ffdd/rcl/FLAT_RCL.zip`  |Tab-delimited|~1M rows (row = recall × model)|Weekly  |
|Complaints      |`https://static.nhtsa.gov/odi/ffdd/cmpl/FLAT_CMPL.zip`|Tab-delimited|~2M+ rows, ~1 GB unzipped      |Weekly  |
|Investigations  |`https://static.nhtsa.gov/odi/ffdd/inv/FLAT_INV.zip`  |Tab-delimited|Small                          |Weekly  |
|Make/model canon|vPIC `GetAllMakes` / `GetModelsForMake` (JSON)        |JSON         |Small                          |Monthly |
|Spot-check      |`https://api.nhtsa.gov/recalls/recallsByVehicle?...`  |JSON         |n/a                            |CI tests|

Field layouts live in sibling `*.txt` layout files (e.g. `RCL.txt`). **First ETL task: generate parser column maps from those layout files** rather than hardcoding. Key fields:

- **FLAT_RCL:** `CAMPNO`, `MAKETXT`, `MODELTXT`, `YEARTXT`, `MFGCAMPNO`, `COMPNAME`, `MFGTXT`, `POTAFF`, `RCDATE`, `DESC_DEFECT`, `CONEQUENCE_DEFECT` (NHTSA’s typo — verify), `CORRECTIVE_ACTION`, `NOTES`.
- **FLAT_CMPL:** `CMPLID`, `ODINO`, `MAKETXT`, `MODELTXT`, `YEARTXT`, `CRASH`, `FIRE`, `INJURED`, `DEATHS`, `COMPDESC`, `CDESCR` (narrative), `FAILDATE`, `MILES`, partial `VIN`.
- **FLAT_INV:** action number, make, model, year, `COMPNAME`, `SUBJECT`, `SUMMARY`, open/close dates, linked `CAMPNO`.

### Data hygiene rules (this is the real work — encode all of it)

1. **Make/model normalization.** Files contain `CHEVY` vs `CHEVROLET`, `MERCEDES BENZ` vs `MERCEDES-BENZ`, trim levels jammed into model names, trailing whitespace. Canonicalize against vPIC + a hand-maintained `data/aliases.yaml`. Unmatched rows → `quarantine` table, count surfaced in CI summary. **Never silently dropped, never pipeline-blocking.**
1. **`YEARTXT = 9999`** = unknown/all years → `year = NULL`, surfaced on the model page, not year pages.
1. **PII scrub of complaint narratives** (`CDESCR` is raw ALL-CAPS consumer text): sentence-case for display; regex-redact 17-char VINs, phone numbers, emails → `[REDACTED]`. Truncate to 300 chars, no expand.
1. **Dedupe recalls:** one `CAMPNO` spans many make/model/year rows — store the campaign once during build, then denormalize into page docs.
1. **Encoding:** latin-1/cp1252 with stray control chars — decode with `errors="replace"`, strip control chars.

-----

## 3. Repository structure

Monorepo: pnpm workspace (site) + uv-managed Python package (ETL).

```
recall-lookup/
├── SPEC.md
├── etl/                                # Python 3.12, uv, polars, httpx, pytest
│   ├── src/etl/
│   │   ├── download.py                 # fetch + checksum flat files; no-op exit if unchanged
│   │   ├── layouts.py                  # NHTSA layout .txt → column maps
│   │   ├── parse.py                    # flat files → normalized rows (polars lazy)
│   │   ├── normalize.py                # aliases, PII scrub, quarantine
│   │   ├── build_sqlite.py             # relational build.db (local truth for diffing/tests)
│   │   ├── build_pages.py              # build.db → denormalized page JSON docs
│   │   ├── diff.py                     # page docs vs previous build → changed/deleted doc IDs
│   │   ├── push_firestore.py           # Admin SDK BulkWriter upserts/deletes, changed docs only
│   │   └── sitemaps.py                 # sharded sitemaps → site/public/sitemaps/
│   ├── data/aliases.yaml
│   └── tests/
├── site/                               # Astro 5.x SSR, @astrojs/node adapter, Dockerfile
│   ├── astro.config.mjs
│   ├── Dockerfile                      # node:22-slim, standalone server, PORT env for Cloud Run
│   ├── public/
│   │   ├── ads.txt                     # AdSense publisher line
│   │   ├── robots.txt
│   │   └── sitemaps/                   # written by ETL, deployed by Hosting
│   ├── src/
│   │   ├── pages/
│   │   │   ├── index.astro                              # search + browse makes (prerendered)
│   │   │   ├── recalls/[make]/index.astro               # make hub
│   │   │   ├── recalls/[make]/[model]/index.astro       # model hub
│   │   │   ├── recalls/[make]/[model]/[year]/index.astro# ★ PRIMARY money page
│   │   │   ├── recall/[campaign]/index.astro            # campaign detail
│   │   │   └── vin/index.astro                          # client-side VIN decode → redirect
│   │   ├── components/                 # RecallCard, ComplaintStats, Breadcrumbs, AdSlot,
│   │   │                               # AffiliateBlock, Analytics, ConsentBanner
│   │   ├── lib/firestore.ts            # REST-based single-doc fetch (see §6)
│   │   ├── lib/seo.ts                  # titles, metas, JSON-LD builders
│   │   └── lib/ga.ts                   # gtag helpers, custom dims, event wrappers
│   └── test/                           # vitest: seo builders, firestore layer (emulator)
├── .github/workflows/
│   ├── sync.yml                        # weekly: ETL → Firestore → sitemaps → hosting deploy
│   ├── deploy.yml                      # push to main: build container → Cloud Run + Hosting
│   └── smoke.yml                       # post-sync: 20 sampled URLs → 200 + JSON-LD + gtag present
├── firebase.json                       # hosting config, rewrites, headers
├── .firebaserc
└── README.md
```

-----

## 4. Data model — Firestore (denormalized, read-optimized)

**Design rule: one page render = one document read.** No joins, no subcollection fan-out on the request path. The relational truth lives only in the ETL’s local `build.db`; Firestore stores render-ready documents.

```
Collection: pages          # doc ID = URL slug with '/' → '__', e.g. 'recalls__honda__cr-v__2016'
{
  slug: 'recalls/honda/cr-v/2016',
  kind: 'year' | 'model' | 'make' | 'campaign',
  make: { slug: 'honda', display: 'Honda' },
  model: { slug: 'cr-v', display: 'CR-V' },      // null on make/campaign pages
  year: 2016,                                     // null except year pages
  indexable: true,                                // §5.1 thin-content rule, precomputed in ETL
  recallCount: 4,
  totalAffected: 412000,
  recalls: [                                      // full render payload, ordered newest-first
    { campno, component, reportDate, affected,
      defect, consequence, action, mfgCampno }    // defect/consequence/action pre-truncated
  ],
  complaintStats: [                               // top 10 components
    { component, count, crashes, fires, injuries, deaths }
  ],
  complaintTotal: 187,
  complaintSamples: [                             // ≤ 8, scrubbed, ≤300 chars each
    { component, failDate, narrative }
  ],
  investigations: [ { actionNumber, subject, summary, openDate, closeDate, campno } ],
  nav: {                                          // precomputed internal links (§5, item 8)
    years: [2014, 2015, 2017, 2018],
    siblingModels: [{ slug, display }],
    relatedByComponent: [{ slug, display, label }]
  },
  updatedAt: <timestamp>
}

Collection: campaignPages   # kind='campaign', full untruncated defect/consequence/action text,
                            # affectedVehicles: [{ makeDisplay, modelDisplay, year, slug }]

Collection: meta            # doc 'sync': { lastSync, rclChecksum, counts, quarantineCount }
```

**Constraints & sizing:**

- Firestore doc limit is 1 MiB. `build_pages.py` asserts every doc < 800 KB; if a model-year exceeds it (a few pathological fleets will), truncate `complaintSamples` first, then recall `notes`. Assert, don’t discover in prod.
- ~120k page docs initial load ≈ 120k writes ≈ **cents** on Blaze. Weekly diffs touch a few thousand docs.
- **Search typeahead needs zero Firestore reads:** ETL emits `site/public/search-index.json` (all make/model combos + year ranges, ~10k entries, ~300 KB gzipped), fetched lazily on first keystroke on hub pages, fuzzy-matched client-side. Hosting CDN serves it.
- Firestore Security Rules: deny all client access (`allow read, write: if false;`). Only the Cloud Run service account (server-side) and the ETL service account touch Firestore. No client SDK ships to the browser.

-----

## 5. URL scheme, page template, content strategy

### URLs

```
/                                   home (prerendered)
/recalls/honda/                     make hub
/recalls/honda/cr-v/                model hub: per-year table, top components
/recalls/honda/cr-v/2016/           ★ PRIMARY page
/recall/23V123000/                  campaign detail (canonical home of full defect text)
/vin/                               client-side VIN year/make/model decode → redirect
```

### Primary page section order (deliberate: answer first, ads after first content block)

1. **H1:** `{Year} {Make} {Model} Recalls ({N} Recalls, {M} Complaints)`
1. **Answer box** — 2–3 deterministic template sentences: “The 2016 Honda CR-V has 4 NHTSA recall campaigns affecting an estimated 412,000 vehicles… Last updated {date}.”
1. **Recall list** — `RecallCard` per campaign: component badge, date, units affected, truncated defect/consequence/action, link to `/recall/{campno}/`.
1. **AdSlot #1** (below the fold of first meaningful content — never above the answer box).
1. **Complaint statistics** — component table with crash/fire/injury/death counts + inline SVG bar chart (no chart lib).
1. **Sample complaints** (≤8 scrubbed narratives).
1. **Investigations** (if any).
1. **“Check your specific VIN” CTA** → outbound to NHTSA’s official VIN tool (genuinely useful; trust signal).
1. **Internal links** — ±3 adjacent years, sibling models, same-component recalls across makes (all precomputed in `nav`).
1. **AffiliateBlock** — component-keyword-mapped Amazon picks (OBD-II scanners for ELECTRICAL/ENGINE, repair manual search for the model). `rel="sponsored"`, labeled, FTC disclosure line.
1. **AdSlot #2** (end of content).
1. **Sources & methodology footer** — exact NHTSA datasets, last-sync date, disclaimers.

### 5.1 Thin-content policy — the SEO make-or-break

- **Zero-recall pages still render** (“No NHTSA recalls on record for the 2021 Mazda CX-30 — here’s what that means…”) but get `noindex` **unless** they carry ≥ 10 complaints. Precomputed as `indexable` in ETL; the template just reads it.
- Year pages exist only for years present in the data. No speculative enumeration.
- **All prose is deterministic templates with slotted values — zero LLM-generated filler.** 100k pages of LLM prose is a scaled-content-abuse flag, costs money, and can hallucinate safety information. Templates + real government data is the programmatic pattern Google’s guidance explicitly tolerates. (Optional later human task: hand-write intros for the top ~40 hub pages.)
- Canonicals: every page self-canonical; no cross-canonicalization games; internal search results `noindex,follow`.

### 5.2 SEO plumbing

- **Title:** `{Year} {Make} {Model} Recalls & Complaints (Updated {Month Year}) | {SiteName}` — updated-date in title lifts CTR on this query class.
- **JSON-LD:** `BreadcrumbList` everywhere; `FAQPage` on year pages (2–3 templated Q&As); `Dataset` on the methodology page. Do **not** emit `Vehicle` schema with fabricated properties.
- **Sitemaps:** sharded at 45k URLs, `sitemap-index.xml`, `<lastmod>` from doc `updatedAt`, only `indexable: true` URLs. Written by ETL into `site/public/sitemaps/`, published by the weekly Hosting deploy, referenced in robots.txt. Ping Google on change.
- **GSC + Bing WMT** verified via DNS at setup (human task, §12).

-----

## 6. Runtime architecture (Firebase / GCP)

### Topology

- **Astro 5 SSR** (`@astrojs/node`, standalone) in a slim container on **Cloud Run** (min instances 0, max 3, concurrency 80, 512 MB). Prerender only `/`, `/vin/`, methodology/about/privacy.
- **Firebase Hosting** in front, `firebase.json` rewrite `** → Cloud Run service`. Hosting’s CDN caches Cloud Run responses when they carry `s-maxage`.
- **Cache policy:** every SSR response sets `Cache-Control: public, s-maxage=604800, stale-while-revalidate=86400`. TTL == sync cadence, and the weekly Hosting deploy flushes the whole CDN anyway — that deploy **is** the purge mechanism (Firebase offers no per-URL purge). Result: Cloud Run + Firestore see each URL roughly once per week per edge; cold-start latency is hidden behind the CDN for all repeat traffic.
- **Firestore access from Astro:** use the **Firestore REST API with a cached service-account access token** (metadata server on Cloud Run), not the full Admin SDK — keeps the container light and cold starts ~fast. One `GET /v1/projects/{p}/databases/(default)/documents/pages/{docId}` per render. 404 from Firestore → nearest-match suggestion from the static search index + HTTP 404.
- **Zero client JS on entity pages** except: gtag snippet, consent banner, AdSense loader (flag-gated). The search island (vanilla JS, ~1 kB + lazy index fetch) ships only on home/hub pages.

### firebase.json essentials

```json
{
  "hosting": {
    "public": "site/public-deploy",
    "rewrites": [{ "source": "**", "run": { "serviceId": "recall-site", "region": "us-central1" } }],
    "headers": [
      { "source": "/sitemaps/**", "headers": [{ "key": "Cache-Control", "value": "public, max-age=3600" }] },
      { "source": "/search-index.json", "headers": [{ "key": "Cache-Control", "value": "public, max-age=86400" }] }
    ]
  }
}
```

(Static files in `public-deploy/` — ads.txt, robots.txt, sitemaps, search index — are served by Hosting directly and never hit Cloud Run.)

### Cost forecast (Blaze)

CDN-absorbed traffic means Cloud Run/Firestore scale with *unique URLs per week*, not pageviews. At 200k pageviews/mo expect: Cloud Run < $5, Firestore reads ~free, Hosting egress ~$1–3 (pages are ~30 KB), GA4/BigQuery export free tier. **~$5–10/mo steady state**, $0-ish pre-traffic. Set a **GCP budget alert at $25/mo** (implement via Terraform-free console step, note in README).

-----

## 7. Analytics — GA4, instrumented deep

This is a first-class deliverable, not an afterthought. Everything routes through `lib/ga.ts`.

1. **Property setup (human does in console, code assumes):** GA4 web property, gtag ID in env `PUBLIC_GA_ID`. Link GA4 ↔ AdSense, GA4 ↔ Search Console, enable **BigQuery daily export** (free tier).
1. **Consent Mode v2 first.** gtag consent defaults `denied` for `ad_storage`/`ad_user_data`/`ad_personalization`/`analytics_storage`; a Google-certified CMP banner (Funding Choices) updates consent. AdSense requires this for EEA regardless — build it once, correctly. Analytics still receives cookieless pings when denied.
1. **Custom dimensions (register in GA4 admin, send on every `page_view`):**
- `page_kind` (year/model/make/campaign/home)
- `make`, `model`, `model_year`
- `recall_count_bucket` (0 / 1–2 / 3–5 / 6+), `complaint_count_bucket`
- `indexable` (true/false — lets you verify noindex pages get no organic entrances)
1. **Events:** `affiliate_click` (params: component, asin_group), `vin_decode` (success/fail), `outbound_nhtsa`, `search_used` (query_length, matched), `related_link_click` (link_type: year/sibling/component). All via delegated listeners in one tiny script — no per-component JS.
1. **BigQuery:** the daily export enables the queries that actually drive iteration — revenue-proxy per make (ad impressions × page_kind), which components correlate with affiliate clicks, crawl-vs-traffic gap analysis joined against GSC’s BigQuery export (enable that too). Ship 3 saved queries in `analytics/queries/*.sql` as starters.
1. **Definition of done:** DebugView shows page_view with all custom dims on all five page kinds; consent-denied mode verified; events fire; BQ export landing.

-----

## 8. Google display ads

1. **AdSense** (this is what “Google display ads” means at this scale; Ad Manager only becomes relevant far later — the `AdSlot` component abstracts the swap).
1. `AdSlot.astro`: renders the async AdSense unit *only when* `PUBLIC_ADS_ENABLED=true` **and** consent granted; reserves fixed height (CLS = 0); two placements per §5 order, never above the answer box; lazy via AdSense’s built-in lazy loading.
1. `public/ads.txt` with the publisher ID; env `PUBLIC_ADSENSE_CLIENT`.
1. **Auto ads OFF** — manual placements only; auto ads wreck programmatic-page UX and CLS.
1. Launch flow: site live + indexed → apply to AdSense (needs real traffic + the privacy/about pages from §9) → flip flag. No code change.
1. Amazon Associates `AffiliateBlock`: static `affiliate-map.json` mapping component keywords → 2–3 curated ASINs (~15 products hand-picked once, human task). FTC disclosure component wherever it renders.

-----

## 9. Legal/compliance pages & behavior

- **Methodology/About:** data provenance, sync cadence, “not affiliated with NHTSA; not legal or safety advice — verify with NHTSA’s VIN tool and your dealer.” Footer-linked everywhere.
- NHTSA data is public domain (17 U.S.C. § 105); attribute anyway on methodology page.
- PII scrub (§2.3) mandatory before any narrative renders.
- **Privacy policy** covering GA4 + AdSense cookies; CMP per §7.2. Cookie disclosure list.
- Nominative use of make names only; **no manufacturer logos.**

-----

## 10. ETL pipeline & CI (`sync.yml`)

Weekly cron `0 9 * * 1` + `workflow_dispatch`. Auth to GCP via **Workload Identity Federation** — no service-account key files in the repo, ever.

```
jobs:
  sync:
    1. restore previous build.db + page-docs manifest (actions/cache)
    2. etl download      # checksums vs meta doc; unchanged → exit "no-op"
    3. etl parse         # polars lazy, chunked
    4. etl normalize     # aliases, PII scrub, quarantine report
    5. etl build-sqlite  # + integrity assertions (below)
    6. etl build-pages   # page JSON docs + search-index.json + indexable flags
    7. etl diff          # → changed_docs/, deleted_ids.txt
    8. etl push-firestore# BulkWriter, 500/batch, 3× retry exp backoff; then update meta/sync
    9. etl sitemaps      # only if slug set changed
   10. firebase deploy --only hosting   # publishes sitemaps + search index, flushes CDN
   11. ping Google sitemap endpoint
   12. job summary: rows in/out, quarantine %, changed docs, largest doc KB
  smoke (needs: sync):
    curl 20 sampled changed URLs → assert 200, gtag present, JSON-LD parses,
    recall list non-empty OR valid empty-state
```

**Integrity assertions — fail before step 8, old data keeps serving (this is what makes it maintenance-free):**

- Campaign count within ±10% of last run and > 25,000 absolute.
- Quarantine rate < 3%.
- Zero campaigns with empty defect AND empty corrective action.
- Every page doc < 800 KB; total docs within ±10% of last run.
- Spot-check 5 random campaigns against the live NHTSA JSON API (field-level match on affected count + component).

On failure: GitHub issue auto-opened with the job summary attached. **That issue is the only human touchpoint in steady state.**

`deploy.yml` (on push to main): build container → Artifact Registry → `gcloud run deploy` → `firebase deploy --only hosting`. Vitest + pytest + ruff + mypy gate both workflows. Firestore emulator used in site tests.

-----

## 11. Implementation phases & acceptance criteria

**Phase 1 — ETL core.** `uv run etl all --local` produces build.db + page docs locally; campaign count > 25k; quarantine < 3%; pytest covers layout parsing, ≥20 alias fixtures (CHEVY→CHEVROLET, 9999-year, cp1252 garbage), PII scrub fixtures, doc-size assertion.

**Phase 2 — Site + Firestore.** All five page kinds render from a manually-pushed Firestore against real data. `/recalls/honda/cr-v/2016/` < 500 ms cold / < 50 ms CDN-hit (verify `x-cache: HIT` header from Hosting on second request); Lighthouse ≥ 95 perf / 100 SEO on the year template; zero client JS on entity pages beyond gtag+consent (devtools verify); Firestore rules deny client access (emulator test).

**Phase 3 — Automation.** Two consecutive scheduled `sync.yml` runs complete unattended; a no-change week no-ops at step 2; corrupt-file failure test opens an issue and leaves prod untouched; sitemap index validates in GSC.

**Phase 4 — Analytics + SEO + ads polish.** GA4 DebugView shows all custom dims on all page kinds; consent-denied path verified; BigQuery export landing; Rich Results Test passes FAQ+Breadcrumb on 5 sampled pages; noindex verified on a zero-data page; AdSlot/AffiliateBlock render correctly behind flags with CLS 0.

**Phase 5 — future verticals (design only, no build).** Document the adapter seam (new parser + page-builder module + route group). Candidates: CPSC recalls, FAA registry. Do not implement.

## 11a. Non-goals (v1)

No user accounts, comments, email capture; no LLM page prose; no VIN-level recall-status lookups (manufacturer APIs — we link to NHTSA’s tool); no non-US data; no complaint archive beyond the 8-sample window; no Ad Manager.

## 12. Human-required setup (answer/do before Phase 2)

1. **Domain** — buy before GSC/AdSense setup; DNS-verify GSC + Bing.
1. **Firebase/GCP project** — recommend a **separate project from your ABSN-directory Firebase** (clean billing/analytics, sellable asset later). Enable Blaze, budget alert $25.
1. **GA4 property + BigQuery link + Funding Choices CMP** — console clicks, ~30 min, per §7.
1. **AdSense application** — Phase 4 gate (needs live indexed site).
1. **Amazon Associates tag** + hand-pick the ~15 affiliate products for `affiliate-map.json`.