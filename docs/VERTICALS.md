# Future verticals — the adapter seam (SPEC §11, Phase 5)

**Status: design only. Nothing in this document is implemented, and nothing here
changes v1 behavior.** SPEC Phase 5 asks for exactly this: document how a second
data vertical (CPSC recalls, FAA registry, …) would plug into the pipeline and
site, so that when one is worth building, the shape of the work is already known
and the NHTSA vertical doesn't get refactored speculatively in the meantime.

Everything below is grounded in the code as it exists today; file references are
the contract points a new vertical would touch.

---

## 1. What a "vertical" is

A vertical is one public-domain dataset family rendered as one entity-page URL
group: NHTSA is `/recalls/{make}/{model}/{year}/` + `/recall/{campaign}/`. A
second vertical is a *new parser, a new page-builder, and a new route group* —
and, deliberately, almost nothing else. The invariants that made v1 cheap and
maintenance-free are non-negotiable for any vertical:

1. **One page render = one Firestore document read.** The vertical's page
   builder emits fully denormalized, render-ready docs. No joins at request time.
2. **Cache TTL == sync cadence; the Hosting deploy is the purge.** A vertical
   that updates monthly still rides the weekly deploy; a vertical that would
   need sub-week freshness doesn't fit this architecture and shouldn't be built
   on it.
3. **Deterministic template prose only.** Slotted values from government data —
   zero LLM filler, for the same scaled-content-abuse and safety-hallucination
   reasons as v1 (SPEC §5.1).
4. **Precomputed `indexable`.** Every vertical defines its own thin-content
   rule in ETL; templates only read the flag.
5. **Integrity assertions run before any write.** Each vertical ships its own
   count floors / drift bounds / emptiness checks; a failing vertical must not
   block the others' pushes (see §3.4).

## 2. Where the seam already is

The pipeline is two halves. The back half is **already vertical-agnostic**
because it operates on an intermediate contract, not on NHTSA concepts:

| Stage | Module | Vertical-specific? |
| --- | --- | --- |
| download + checksum no-op | `etl/src/etl/download.py` | No — driven by `Dataset` entries in `config.DATASETS` |
| decode/parse flat files | `etl/src/etl/parse.py`, `layouts.py` | **Yes** — NHTSA cp1252 TSV + layout-file column maps |
| hygiene, canon, quarantine | `etl/src/etl/normalize.py` | **Yes** — vPIC/aliases make canon, PII scrub, vehicle scope |
| relational truth | `etl/src/etl/build_sqlite.py` | **Yes** — campaigns/complaints/investigations schema |
| page docs + manifest | `etl/src/etl/build_pages.py` | **Yes** — but its *outputs* are the generic contract |
| integrity gates | `etl/src/etl/verify.py` | Thresholds yes, framework no |
| diff vs last run | `etl/src/etl/diff.py` | **No** — pure manifest/JSONL transform |
| Firestore push | `etl/src/etl/push_firestore.py` | **No** — writes whatever `{id, collection, doc}` says |
| sitemaps + robots | `etl/src/etl/sitemaps.py` | **No** — reads slugs/`indexable`/`lastmod` from the manifest |
| weekly CI | `.github/workflows/sync.yml` | No — steps are the CLI verbs |

### 2.1 The intermediate contract (this is the seam)

A vertical's entire obligation to the shared machinery is to produce these two
files, exactly as `build_pages.py` does today:

```
build/pages/pages.jsonl      one line per doc:
                             {"id": <docId>, "collection": <str>, "doc": {…}}
build/pages/manifest.json    docId -> {"hash", "slug", "indexable", "kind",
                                       "collection"}   (+ "lastmod", stamped by diff)
```

Rules the contract encodes today (keep them):

- `docId = slug.replace('/', '__')` — mirrored in
  `site/src/lib/firestore.ts` `docIdForSlug()`. Slugs are produced by
  `normalize.slugify()`, which must stay in lockstep with
  `site/src/lib/slug.ts`.
- `hash` = sha256 of the canonical-JSON doc **excluding `updatedAt`**
  (`build_pages.canonical_hash`) so diffing is content-stable;
  `push_firestore` stamps `updatedAt` at write time.
- Every doc passes `enforce_size()` (< 800 KB, `config.MAX_DOC_BYTES`) with a
  vertical-appropriate shedding order — assert at build time, never discover in
  prod.
- Docs carry a `kind` string; the site sends it as the `page_kind` GA4 dim.
  New verticals add new kinds (e.g. `cpsc-product`), they never overload the
  existing ones.

Because `diff.py`, `push_firestore.py`, and `sitemaps.py` only ever see this
contract, **a second vertical requires zero changes to diffing, pushing, or
sitemap generation** beyond the manifest simply containing more entries.

### 2.2 The site seam

- **Route group:** a new directory under `site/src/pages/` (e.g.
  `site/src/pages/product-recalls/[brand]/…`). Astro file routing means no
  central router to touch.
- **Doc fetch:** `site/src/lib/firestore.ts` `collectionForSlug()` is the one
  switch statement that maps a URL slug prefix to a Firestore collection —
  today `recall/ → campaignPages`, else `pages`. A new vertical adds one
  branch and one collection.
- **Firestore collections:** one or two new top-level collections per vertical
  (list pages + detail pages), named in `etl/src/etl/config.py` alongside
  `FIRESTORE_PAGES_COLLECTION`. `firestore.rules` stays deny-all — new
  collections are covered by the existing `match /{document=**}` deny.
- **SEO builders:** `site/src/lib/seo.ts` gains title/meta/JSON-LD builders for
  the new kinds. Same JSON-LD discipline: `BreadcrumbList` everywhere,
  `FAQPage` only where the Q&A is genuinely templatable, no fabricated schema
  properties.
- **Analytics:** `site/src/lib/ga.ts` `buildPageDims()` already omits
  inapplicable dims, so new kinds send `page_kind` + whatever entity dims make
  sense; register any new custom dimensions in GA4 once, at vertical launch.
- **Search:** the typeahead index (`site/public/search-index.json`) is
  NHTSA-shaped (`make`/`model`/`years`). Per-vertical search gets its own
  static index file and its own island on that vertical's hub pages — do not
  force one cross-vertical search UI in v1 of a new vertical.
- **Shared chrome:** `Layout.astro`, `AdSlot`, `AffiliateBlock`, consent,
  caching (`site/src/lib/cache.ts`) are reused as-is. Ad placement policy
  (never above the answer box) applies per template.

### 2.3 CI seam

`sync.yml` calls CLI verbs (`etl download` … `etl sitemaps`); the verbs loop
over registered datasets. A monthly-cadence vertical (e.g. FAA registry) still
runs in the weekly job. Note: `download.py` records checksums per file but the
no-op decision compares the whole set, so today any changed dataset reprocesses
everything. With NHTSA changing nearly every week, a slower vertical would be
re-parsed weekly for nothing — making the no-op (and the state baseline)
per-vertical is part of the §3 refactor, not something to retrofit before then.

## 3. The refactor to do when (and only when) vertical #2 lands

Do not pre-build this. When a second vertical is approved:

1. **Move NHTSA-specific modules into a package:**
   `etl/src/etl/verticals/nhtsa/` gets `parse.py`, `normalize.py`,
   `build_sqlite.py`, `build_pages.py` (imports fixed, logic untouched).
   Shared modules (`download`, `diff`, `push_firestore`, `sitemaps`,
   `layouts`, `verify` framework, `config` paths) stay where they are.
2. **Define the vertical interface** — a small `Protocol`, not a framework:

   ```python
   class Vertical(Protocol):
       name: str                       # 'nhtsa', 'cpsc', …
       datasets: dict[str, Dataset]    # merged into download's checksum set
       def parse(self) -> None: ...    # raw downloads -> work/parsed/<name>/
       def normalize(self) -> None: ...# hygiene, canon, quarantine
       def build(self) -> None: ...    # -> pages.jsonl lines + manifest entries
       def integrity_checks(self) -> list[str]  # failures, [] if green
   ```

   `build` appends to the shared `pages.jsonl`/`manifest.json`; doc IDs are
   naturally namespaced because slugs embed the route group.
3. **CLI:** `etl all --vertical nhtsa --vertical cpsc` (default: all
   registered). Registration is a dict in `config.py`, mirroring `DATASETS`.
4. **Failure isolation:** a vertical whose integrity checks fail is excluded
   from the diff/push and reported in the auto-issue; green verticals still
   push. (Today `verify.run()` is all-or-nothing, which is correct with one
   vertical.) The no-op baseline (`commit_state_checksums`) must then advance
   per-vertical, keyed by the vertical's dataset files.
5. **Per-vertical thresholds** move next to the vertical (count floor, drift,
   quarantine rate) — the §10 framework (fail before write, issue on failure)
   is shared.

Estimated size: the refactor itself is file moves plus ~200 lines of glue; the
real cost of any vertical is its normalize step (entity canon is always the
actual work — v1's alias/quarantine machinery took the bulk of Phase 1).

## 4. Candidate: CPSC product recalls

⚠️ Verify all endpoints at implementation time, per SPEC §2's standing rule.

- **Source:** CPSC Recall Database REST API,
  `https://www.saferproducts.gov/RestWebServices/Recall?format=json` — the
  full JSON dump is small (~thousands of recalls, one request or a few paged
  ones; no flat-file/layout machinery needed). SaferProducts.gov incident
  reports are the complaints analog if a bulk/API path exists — verify;
  incident narratives would need the same PII scrub as `CDESCR`.
- **Entity model:** recalls are product-level with `Products[]` (name, brand,
  model number, category), `Hazards[]`, `Remedies[]`, `Manufacturers[]`. There
  is **no year/make/model canon** — the vPIC-equivalent doesn't exist, so the
  canonicalization target is brand + product category, driven by a
  hand-maintained `aliases-cpsc.yaml` and the same quarantine philosophy
  (unmatched → quarantined, never dropped, never blocking).
- **URL scheme (page kinds):**

  ```
  /product-recalls/                      hub (kind: cpsc-home)
  /product-recalls/{category}/           category hub (kind: cpsc-category)
  /product-recalls/{brand}/              brand hub (kind: cpsc-brand)
  /product-recalls/{brand}/{recall-id}/  detail — canonical home of full text
  ```

  Query class: "{brand} {product} recall", "is {product} recalled". Corpus is
  ~10–20k pages — an order of magnitude smaller than NHTSA, which is fine; the
  marginal cost per vertical is near zero once live.
- **Page doc sketch (detail):** recall number, date, brand/product/category
  display + slugs, hazard + injury text, remedy, units, sold-at/importer,
  `images[]` URLs (hotlink? verify CPSC CDN terms — likely mirror nothing,
  link only), `nav` with same-brand and same-category recalls, `indexable`
  (true — CPSC recalls all have substantive text; thin-content risk is low).
- **Risks / open questions:** brand normalization quality (the real work);
  remedy status changes over time (recalls are updated in place — the content
  hash handles re-pushes); affiliate fit is weaker than automotive (no OBD-II
  equivalent; category-mapped picks possible but curate skeptically).

## 5. Candidate: FAA (registry + airworthiness directives)

⚠️ Verify all endpoints at implementation time.

- **Sources:** aircraft registry bulk download
  `https://registry.faa.gov/database/ReleasableAircraft.zip` (CSV: `MASTER`,
  `ACFTREF`, `ENGINE`, …; refreshed roughly daily, but monthly sync cadence is
  plenty). Airworthiness Directives — the recall analog — from the FAA DRS
  (Dynamic Regulatory System); confirm a bulk/queryable path before committing:
  **if ADs have no reliable bulk export, this vertical is registry-only or
  dead** — ADs are the content with search demand.
- **Entity model:** manufacturer/model from `ACFTREF` is the entity canon
  (better than CPSC: the FAA maintains it). Two natural page groups:

  ```
  /aircraft/{manufacturer}/{model}/      model page: AD list, fleet count,
                                         production years   (kind: faa-model)
  /aircraft/n/{n-number}/                tail-number page   (kind: faa-tail)
  ```

  Query class: "{manufacturer} {model} airworthiness directives", "N{number}
  owner/history".
- **Caution — the tail-number pages are a scope decision, not a freebie:**
  ~300k active registrations would roughly triple the site's doc count, and
  registry `MASTER` includes registrant names/addresses. That's public record,
  but republishing owner PII invites removal requests — the opposite of
  zero-maintenance. **Design position: v1 of this vertical ships model pages
  only; tail-number pages, if ever, render registration status without
  registrant identity.**
- **Risks:** AD applicability is serial-number-ranged and legally load-bearing
  — templates must link the authoritative AD document and disclaim, same
  posture as the NHTSA VIN-tool CTA; audience is far smaller than automotive
  (weigh demand before building).

## 6. What a vertical must answer before it gets built

A one-page go/no-go per candidate, in this order (cheapest kill first):

1. **License & attribution** — public domain or unambiguous reuse right?
2. **Bulk data path** — flat files or full-dump API, stable URLs, checksummable?
   (No scraping. Ever.)
3. **Entity canon** — does an authority list exist (vPIC, ACFTREF), or is it
   hand-maintained aliases? Estimate quarantine rate on a sample.
4. **Search demand** — is there a long-tail query class with the entity in the
   query? (This is the whole thesis; NHTSA works because "{year} {make}
   {model} recalls" is typed verbatim.)
5. **Thin-content rule** — what makes a page `indexable`? If most pages would
   be noindex, don't build.
6. **PII surface** — narratives to scrub? Registrant identities to withhold?
7. **Update cadence** ≤ weekly? (Faster-than-weekly freshness breaks invariant 2.)
8. **Monetization fit** — AdSense category eligibility + affiliate mapping, or
   accept it as traffic/authority play.

## 7. Non-goals (unchanged from SPEC §11a)

No user accounts, no LLM prose, no cross-vertical search UI in a vertical's
v1, no non-US data, no scraping of sources without bulk exports. And Phase 5
itself ships **no code**: this document is the deliverable.
