"""build.db -> denormalized, render-ready page documents (SPEC §4).

Outputs:
  build/pages/pages.jsonl     one line per doc: {"id", "collection", "doc"}
  build/pages/manifest.json   docId -> {"hash", "slug", "indexable", "kind"}
  site/public/search-index.json  make/model combos + year ranges for typeahead

Design rule enforced here: one page render == one Firestore document read.
Everything a template needs (recalls, complaint stats, samples, nav links,
indexable flag) is embedded in the doc. Hashes exclude updatedAt so diffing
is content-stable; push_firestore stamps updatedAt at write time.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from typing import Any

from etl import config


def truncate(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "…"


def _fmt_date(yyyymmdd: str | None) -> str | None:
    if not yyyymmdd or len(yyyymmdd) != 8 or not yyyymmdd.isdigit():
        return None
    return f"{yyyymmdd[0:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def doc_id_for_slug(slug: str) -> str:
    return slug.replace("/", "__")


def canonical_hash(doc: dict) -> str:
    payload = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _doc_size(doc: dict) -> int:
    return len(json.dumps(doc, ensure_ascii=False).encode("utf-8"))


def enforce_size(doc: dict, doc_id: str) -> dict:
    """Assert doc < MAX_DOC_BYTES, truncating per SPEC §4 before failing."""
    if _doc_size(doc) < config.MAX_DOC_BYTES:
        return doc
    # 1) shed complaint samples
    while doc.get("complaintSamples") and _doc_size(doc) >= config.MAX_DOC_BYTES:
        doc["complaintSamples"] = doc["complaintSamples"][:-1]
    # 2) shed recall notes
    if _doc_size(doc) >= config.MAX_DOC_BYTES:
        for r in doc.get("recalls", []):
            r.pop("notes", None)
    # 3) pathological fleets: shed affected-vehicle fan-out (campaign pages)
    while (
        doc.get("affectedVehicles")
        and len(doc["affectedVehicles"]) > 50
        and _doc_size(doc) >= config.MAX_DOC_BYTES
    ):
        doc["affectedVehicles"] = doc["affectedVehicles"][: len(doc["affectedVehicles"]) // 2]
    size = _doc_size(doc)
    assert size < config.MAX_DOC_BYTES, f"doc {doc_id} is {size} bytes after truncation"
    return doc


# --- Bulk loads --------------------------------------------------------------


def _load_recall_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT v.campno, v.make_slug, v.model_slug, v.year,
               v.make_display, v.model_display,
               c.component, c.report_date, c.affected, c.defect, c.consequence,
               c.action, c.notes, c.mfg_campno, c.manufacturer,
               c.do_not_drive, c.park_outside
        FROM campaign_vehicles v JOIN campaigns c USING (campno)
        """
    ).fetchall()


def _load_complaint_stats(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT make_slug, model_slug, year, component,
               COUNT(*) AS cnt,
               SUM(crash = 'Y') AS crashes, SUM(fire = 'Y') AS fires,
               SUM(COALESCE(injured, 0)) AS injuries,
               SUM(COALESCE(deaths, 0)) AS deaths
        FROM complaints
        GROUP BY make_slug, model_slug, year, component
        """
    ).fetchall()


def _load_complaint_samples(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM (
          SELECT make_slug, model_slug, year, component, fail_date, narrative,
                 ROW_NUMBER() OVER (
                   PARTITION BY make_slug, model_slug, year
                   ORDER BY fail_date DESC
                 ) AS rn
          FROM complaints WHERE narrative IS NOT NULL AND year IS NOT NULL
        ) WHERE rn <= ?
        """,
        (config.COMPLAINT_SAMPLE_MAX,),
    ).fetchall()


def _load_investigations(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT action_number, make_slug, model_slug, year, subject, summary,
               open_date, close_date, campno
        FROM investigations
        """
    ).fetchall()


# --- Build -------------------------------------------------------------------


def run() -> dict:  # noqa: PLR0915
    config.ensure_dirs()
    conn = sqlite3.connect(config.SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        recall_rows = _load_recall_rows(conn)
        stat_rows = _load_complaint_stats(conn)
        sample_rows = _load_complaint_samples(conn)
        inv_rows = _load_investigations(conn)
    finally:
        conn.close()

    YearKey = tuple[str, str, int]  # (make_slug, model_slug, year)

    display: dict[tuple[str, str], tuple[str, str]] = {}
    recalls_by_year: dict[YearKey, dict[str, dict]] = defaultdict(dict)
    recalls_null_year: dict[tuple[str, str], dict[str, dict]] = defaultdict(dict)
    campaign_vehicles: dict[str, list[dict]] = defaultdict(list)
    campaign_info: dict[str, sqlite3.Row] = {}

    for r in recall_rows:
        pair = (r["make_slug"], r["model_slug"])
        display.setdefault(pair, (r["make_display"], r["model_display"]))
        campaign_info.setdefault(r["campno"], r)
        entry = {
            "campno": r["campno"],
            "component": r["component"],
            "reportDate": _fmt_date(r["report_date"]),
            "affected": r["affected"],
            "defect": truncate(r["defect"], config.TRUNCATE_TEXT_CHARS),
            "consequence": truncate(r["consequence"], config.TRUNCATE_TEXT_CHARS),
            "action": truncate(r["action"], config.TRUNCATE_TEXT_CHARS),
            "mfgCampno": r["mfg_campno"],
            "doNotDrive": r["do_not_drive"] == "Y",
            "parkOutside": r["park_outside"] == "Y",
        }
        if r["year"] is None:
            recalls_null_year[pair][r["campno"]] = entry
        else:
            recalls_by_year[(r["make_slug"], r["model_slug"], r["year"])][r["campno"]] = entry
        campaign_vehicles[r["campno"]].append(
            {
                "makeDisplay": r["make_display"],
                "modelDisplay": r["model_display"],
                "year": r["year"],
                "slug": (
                    f"recalls/{r['make_slug']}/{r['model_slug']}/{r['year']}"
                    if r["year"] is not None
                    else f"recalls/{r['make_slug']}/{r['model_slug']}"
                ),
            }
        )

    stats_by_year: dict[YearKey, list[dict]] = defaultdict(list)
    stats_pair_totals: dict[tuple[str, str], int] = defaultdict(int)
    complaint_years: set[YearKey] = set()
    for s in stat_rows:
        if s["year"] is None:
            stats_pair_totals[(s["make_slug"], s["model_slug"])] += s["cnt"]
            continue
        key = (s["make_slug"], s["model_slug"], s["year"])
        complaint_years.add(key)
        stats_pair_totals[(s["make_slug"], s["model_slug"])] += s["cnt"]
        stats_by_year[key].append(
            {
                "component": s["component"] or "UNKNOWN",
                "count": s["cnt"],
                "crashes": s["crashes"] or 0,
                "fires": s["fires"] or 0,
                "injuries": s["injuries"] or 0,
                "deaths": s["deaths"] or 0,
            }
        )

    samples_by_year: dict[YearKey, list[dict]] = defaultdict(list)
    for s in sample_rows:
        samples_by_year[(s["make_slug"], s["model_slug"], s["year"])].append(
            {
                "component": s["component"],
                "failDate": _fmt_date(s["fail_date"]),
                "narrative": truncate(s["narrative"], config.COMPLAINT_SAMPLE_CHARS),
            }
        )

    invs_by_year: dict[YearKey, list[dict]] = defaultdict(list)
    invs_by_pair: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for i in inv_rows:
        entry = {
            "actionNumber": i["action_number"],
            "subject": i["subject"],
            "summary": truncate(i["summary"], config.TRUNCATE_TEXT_CHARS),
            "openDate": _fmt_date(i["open_date"]),
            "closeDate": _fmt_date(i["close_date"]),
            "campno": i["campno"],
        }
        if i["year"] is not None:
            invs_by_year[(i["make_slug"], i["model_slug"], i["year"])].append(entry)
        invs_by_pair[(i["make_slug"], i["model_slug"])].append(entry)

    # Entity universe: any pair/year seen in recalls, complaints, or investigations.
    year_keys: set[YearKey] = set(recalls_by_year) | complaint_years | set(invs_by_year)
    pairs: set[tuple[str, str]] = (
        {(m, mo) for m, mo, _ in year_keys}
        | set(recalls_null_year)
        | set(stats_pair_totals)
        | set(invs_by_pair)
    )
    # Display names may be missing for complaint-only entities; recover them.
    if pairs - set(display):
        conn = sqlite3.connect(config.SQLITE_PATH)
        try:
            for table in ("complaints", "investigations"):
                sql = (
                    "SELECT DISTINCT make_slug, model_slug, make_display, model_display "
                    f"FROM {table}"  # noqa: S608
                )
                for row in conn.execute(sql):
                    display.setdefault((row[0], row[1]), (row[2], row[3]))
        finally:
            conn.close()
    pairs &= set(display)  # drop anything we can't name (shouldn't happen)
    year_keys = {k for k in year_keys if (k[0], k[1]) in pairs}

    years_by_pair: dict[tuple[str, str], list[int]] = defaultdict(list)
    for m, mo, y in year_keys:
        years_by_pair[(m, mo)].append(y)
    for v in years_by_pair.values():
        v.sort()

    models_by_make: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for m, mo in sorted(pairs):
        models_by_make[m].append((mo, display[(m, mo)][1]))

    # relatedByComponent: component -> year pages carrying a recall for it,
    # biggest campaigns first, used for cross-make internal links.
    pages_by_component: dict[str, list[tuple[int, str, str, str]]] = defaultdict(list)
    for (m, mo, y), campaigns in recalls_by_year.items():
        for c in campaigns.values():
            comp = (c["component"] or "").split(":")[0].strip()
            if comp:
                pages_by_component[comp].append(
                    (
                        c["affected"] or 0,
                        f"recalls/{m}/{mo}/{y}",
                        f"{y} {display[(m, mo)][0]} {display[(m, mo)][1]}",
                        m,
                    )
                )
    for lst in pages_by_component.values():
        lst.sort(reverse=True)

    def related_by_component(component: str | None, own_make: str, own_slug: str) -> list[dict]:
        comp = (component or "").split(":")[0].strip()
        out, seen_makes = [], {own_make}
        for _, slug, label, make in pages_by_component.get(comp, []):
            if slug == own_slug or make in seen_makes:
                continue
            seen_makes.add(make)
            out.append({"slug": slug, "display": label, "label": comp.title()})
            if len(out) >= config.NAV_RELATED_BY_COMPONENT:
                break
        return out

    docs_out = (config.PAGES_DIR / "pages.jsonl").open("w", encoding="utf-8")
    manifest: dict[str, dict] = {}
    kind_counts: dict[str, int] = defaultdict(int)
    largest: dict[str, Any] = {"id": None, "bytes": 0}

    def emit(slug: str, collection: str, doc: dict) -> None:
        doc_id = doc_id_for_slug(slug)
        doc = enforce_size(doc, doc_id)
        size = _doc_size(doc)
        if size > largest["bytes"]:
            largest.update({"id": doc_id, "bytes": size})
        manifest[doc_id] = {
            "hash": canonical_hash(doc),
            "slug": slug,
            "collection": collection,
            "indexable": bool(doc.get("indexable", True)),
            "kind": doc["kind"],
        }
        docs_out.write(
            json.dumps({"id": doc_id, "collection": collection, "doc": doc}, ensure_ascii=False)
            + "\n"
        )
        kind_counts[doc["kind"]] += 1

    def sort_recalls(campaigns: dict[str, dict]) -> list[dict]:
        return sorted(campaigns.values(), key=lambda r: r["reportDate"] or "", reverse=True)

    # --- year pages ---
    for m, mo, y in sorted(year_keys):
        make_disp, model_disp = display[(m, mo)]
        slug = f"recalls/{m}/{mo}/{y}"
        recalls = sort_recalls(recalls_by_year.get((m, mo, y), {}))
        stats = sorted(stats_by_year.get((m, mo, y), []), key=lambda s: -s["count"])
        total = sum(s["count"] for s in stats)
        years = years_by_pair[(m, mo)]
        siblings = [
            {"slug": s, "display": d}
            for s, d in models_by_make[m]
            if s != mo
        ][: config.NAV_SIBLING_MODELS]
        top_component = recalls[0]["component"] if recalls else None
        doc: dict[str, Any] = {
            "slug": slug,
            "kind": "year",
            "make": {"slug": m, "display": make_disp},
            "model": {"slug": mo, "display": model_disp},
            "year": y,
            "indexable": bool(recalls) or total >= config.INDEXABLE_MIN_COMPLAINTS,
            "recallCount": len(recalls),
            "totalAffected": sum(r["affected"] or 0 for r in recalls),
            "recalls": recalls,
            "complaintStats": stats[: config.COMPLAINT_STATS_TOP_N],
            "complaintTotal": total,
            "complaintSamples": samples_by_year.get((m, mo, y), []),
            "investigations": invs_by_year.get((m, mo, y), []),
            "nav": {
                "years": [
                    yy for yy in years
                    if yy != y and abs(yy - y) <= config.NAV_ADJACENT_YEARS
                ],
                "siblingModels": siblings,
                "relatedByComponent": related_by_component(top_component, m, slug),
            },
        }
        emit(slug, config.FIRESTORE_PAGES_COLLECTION, doc)

    # --- model pages ---
    for (m, mo) in sorted(pairs):
        make_disp, model_disp = display[(m, mo)]
        slug = f"recalls/{m}/{mo}"
        years = years_by_pair.get((m, mo), [])
        year_rows: list[dict[str, Any]] = []
        for y in years:
            yr_recalls = recalls_by_year.get((m, mo, y), {})
            yr_total = sum(s["count"] for s in stats_by_year.get((m, mo, y), []))
            year_rows.append(
                {"year": y, "recallCount": len(yr_recalls), "complaintCount": yr_total}
            )
        null_recalls = sort_recalls(recalls_null_year.get((m, mo), {}))
        total_recalls = sum(r["recallCount"] for r in year_rows) + len(null_recalls)
        total_complaints = stats_pair_totals.get((m, mo), 0)
        doc = {
            "slug": slug,
            "kind": "model",
            "make": {"slug": m, "display": make_disp},
            "model": {"slug": mo, "display": model_disp},
            "year": None,
            "indexable": total_recalls > 0 or total_complaints >= config.INDEXABLE_MIN_COMPLAINTS,
            "recallCount": total_recalls,
            "complaintTotal": total_complaints,
            "yearRows": year_rows,
            "unknownYearRecalls": null_recalls,  # YEARTXT=9999 surfaces here (SPEC §2.2)
            "investigations": invs_by_pair.get((m, mo), [])[:10],
            "nav": {
                "years": years,
                "siblingModels": [
                    {"slug": s, "display": d} for s, d in models_by_make[m] if s != mo
                ][: config.NAV_SIBLING_MODELS],
                "relatedByComponent": [],
            },
        }
        emit(slug, config.FIRESTORE_PAGES_COLLECTION, doc)

    # --- make pages ---
    for m, models in sorted(models_by_make.items()):
        make_disp = display[(m, models[0][0])][0]
        slug = f"recalls/{m}"
        model_rows: list[dict[str, Any]] = []
        for mo, d in models:
            years = years_by_pair.get((m, mo), [])
            n_recalls = sum(
                len(recalls_by_year.get((m, mo, y), {})) for y in years
            ) + len(recalls_null_year.get((m, mo), {}))
            model_rows.append(
                {
                    "slug": mo,
                    "display": d,
                    "recallCount": n_recalls,
                    "complaintCount": stats_pair_totals.get((m, mo), 0),
                    "yearMin": years[0] if years else None,
                    "yearMax": years[-1] if years else None,
                }
            )
        model_rows.sort(key=lambda r: -(r["recallCount"] + r["complaintCount"]))
        doc = {
            "slug": slug,
            "kind": "make",
            "make": {"slug": m, "display": make_disp},
            "model": None,
            "year": None,
            "indexable": True,
            "recallCount": sum(r["recallCount"] for r in model_rows),
            "complaintTotal": sum(r["complaintCount"] for r in model_rows),
            "models": model_rows,
            "nav": {"years": [], "siblingModels": [], "relatedByComponent": []},
        }
        emit(slug, config.FIRESTORE_PAGES_COLLECTION, doc)

    # --- campaign pages (full untruncated text) ---
    for campno, info in sorted(campaign_info.items()):
        slug = f"recall/{campno}"
        vehicles = sorted(
            {json.dumps(v, sort_keys=True) for v in campaign_vehicles[campno]}
        )
        doc = {
            "slug": slug,
            "kind": "campaign",
            "campno": campno,
            "make": None,
            "model": None,
            "year": None,
            "indexable": True,
            "component": info["component"],
            "manufacturer": info["manufacturer"],
            "reportDate": _fmt_date(info["report_date"]),
            "affected": info["affected"],
            "mfgCampno": info["mfg_campno"],
            "defect": info["defect"],
            "consequence": info["consequence"],
            "action": info["action"],
            "notes": info["notes"],
            "doNotDrive": info["do_not_drive"] == "Y",
            "parkOutside": info["park_outside"] == "Y",
            "affectedVehicles": [json.loads(v) for v in vehicles],
        }
        emit(slug, config.FIRESTORE_CAMPAIGNS_COLLECTION, doc)

    docs_out.close()
    (config.PAGES_DIR / "manifest.json").write_text(json.dumps(manifest, indent=1))

    # --- search index (SPEC §4: zero Firestore reads for typeahead) ---
    config.SEARCH_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    search_index = [
        {
            "make": display[(m, mo)][0],
            "model": display[(m, mo)][1],
            "slug": f"recalls/{m}/{mo}",
            "years": [years_by_pair[(m, mo)][0], years_by_pair[(m, mo)][-1]]
            if years_by_pair.get((m, mo))
            else [],
        }
        for (m, mo) in sorted(pairs)
    ]
    config.SEARCH_INDEX_PATH.write_text(
        json.dumps(search_index, ensure_ascii=False, separators=(",", ":"))
    )

    summary = {
        "docs": dict(kind_counts),
        "totalDocs": len(manifest),
        "largestDocKB": round(largest["bytes"] / 1024, 1),
        "largestDocId": largest["id"],
        "searchIndexEntries": len(search_index),
    }
    (config.PAGES_DIR / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({"step": "build-pages", **summary}, indent=2))
    return summary
