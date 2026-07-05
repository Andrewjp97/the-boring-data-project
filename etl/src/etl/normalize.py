"""Data hygiene: canonicalization, PII scrub, quarantine (SPEC §2).

Pure helpers (slugify, sentence_case, scrub_pii, canonicalize_make, ...)
are kept free of I/O so tests can hit them directly with fixtures.

Quarantine philosophy: rows that fail make canonicalization or lack a model
go to quarantine.parquet with a reason — never silently dropped, never
pipeline-blocking. Non-vehicle rows (tires, equipment, child seats) are
*excluded by design*, not quarantined; they're counted separately.
"""

from __future__ import annotations

import bisect
import json
import re
from functools import lru_cache
from pathlib import Path

import polars as pl
import yaml

from etl import config

# --- Slugs & display names ------------------------------------------------


def slugify(value: str) -> str:
    """URL slug; must stay in lockstep with site/src/lib/slug.ts."""
    slug = value.strip().lower()
    slug = slug.replace("&", " and ")
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


_DISPLAY_SMALL_WORDS = {"of", "and", "the"}


def display_name(value: str, overrides: dict[str, str]) -> str:
    """ALL-CAPS NHTSA text -> human display, with per-token overrides."""
    upper = _collapse(value).upper()
    if upper in overrides:
        return overrides[upper]
    words = []
    for i, w in enumerate(upper.split(" ")):
        if w in overrides:
            words.append(overrides[w])
        elif i > 0 and w.lower() in _DISPLAY_SMALL_WORDS:
            words.append(w.lower())
        else:
            # Capitalize each hyphen-separated part: "CR-V" stays "CR-V" via
            # overrides; "F-150" is digits so unchanged; "AVEO-LT" -> "Aveo-Lt".
            words.append("-".join(p.capitalize() for p in w.split("-")))
    return " ".join(words)


def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


# --- Aliases + vPIC canon ---------------------------------------------------


@lru_cache(maxsize=1)
def load_aliases(path: Path | None = None) -> dict:
    p = path or (config.DATA_DIR / "aliases.yaml")
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {
        "makes": {k.upper(): v.upper() for k, v in (data.get("makes") or {}).items()},
        "models": {
            make.upper(): {k.upper(): v.upper() for k, v in (models or {}).items()}
            for make, models in (data.get("models") or {}).items()
        },
        "display": {k.upper(): v for k, v in (data.get("display") or {}).items()},
    }


@lru_cache(maxsize=1)
def load_vpic_makes(path: Path | None = None) -> frozenset[str]:
    p = path or (config.DATA_DIR / "vpic_makes.json")
    if not p.exists():
        return frozenset()
    return frozenset(m.upper() for m in json.loads(p.read_text(encoding="utf-8")))


@lru_cache(maxsize=4)
def _sorted_canon(canon: frozenset[str]) -> tuple[str, ...]:
    return tuple(sorted(canon))


_BOUNDARY = " ,.-/()&'"


def _canon_prefix_match(make: str, canon: frozenset[str]) -> bool:
    """Word-boundary prefix match against vPIC, both directions.

    NHTSA flat files carry short trade names ('FLEETWOOD', 'THOMAS BUILT
    BUSES'); vPIC carries long corporate names ('FLEETWOOD ENTERPRISES',
    'THOMAS BUILT'). Accept when either side extends the other at a word
    boundary — exact-match alone quarantines ~4% of real rows.
    """
    ordered = _sorted_canon(canon)
    i = bisect.bisect_left(ordered, make)
    # canon entries extending make: contiguous block at the insertion point
    j = i
    while j < len(ordered) and ordered[j].startswith(make):
        rest = ordered[j][len(make):]
        if rest == "" or rest[0] in _BOUNDARY:
            return True
        j += 1
    # canon entries that make extends: walk back over shorter prefixes
    j = i - 1
    while j >= 0 and make.startswith(ordered[j][0]):
        entry = ordered[j]
        if make.startswith(entry) and len(make) > len(entry) and make[len(entry)] in _BOUNDARY:
            return True
        j -= 1
    return False


# NHTSA placeholder tokens that must never match a make.
_JUNK_MAKES = frozenset({"UNKNOWN", "NA", "N/A", "NONE", "OTHER"})


def canonicalize_make(raw: str | None, aliases: dict, canon: frozenset[str]) -> str | None:
    """Return canonical make, or None -> quarantine.

    Resolution order: collapse/uppercase -> junk filter -> alias map ->
    vPIC exact membership -> vPIC word-boundary prefix match. Alias targets
    are trusted even if vPIC lags (aliases.yaml is reviewed).
    """
    if not raw or not raw.strip():
        return None
    make = _collapse(raw).upper()
    if make in _JUNK_MAKES:
        return None
    if make in aliases["makes"]:
        return aliases["makes"][make]
    if not canon or make in canon:
        return make
    if _canon_prefix_match(make, canon):
        return make
    return None


def canonicalize_model(make: str, raw_model: str | None, aliases: dict) -> str | None:
    if not raw_model or not raw_model.strip():
        return None
    model = _collapse(raw_model).upper()
    return aliases["models"].get(make, {}).get(model, model)


def parse_year(raw: str | None) -> int | None:
    """YEARTXT -> int model year; 9999/garbage/out-of-range -> None (SPEC §2.2)."""
    if raw is None:
        return None
    try:
        year = int(raw.strip())
    except ValueError:
        return None
    if year == 9999 or not (config.MIN_YEAR <= year <= config.MAX_YEAR):
        return None
    return year


# --- PII scrub (SPEC §2.3) --------------------------------------------------

# 17-char VIN: no I/O/Q. Case-insensitive because scrub runs after casing.
_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.IGNORECASE)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"
)
_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


def scrub_pii(text: str) -> str:
    text = _VIN_RE.sub("[REDACTED]", text)
    text = _EMAIL_RE.sub("[REDACTED]", text)
    text = _PHONE_RE.sub("[REDACTED]", text)
    return text


_SENTENCE_START = re.compile(r"(^|[.!?]\s+)([a-z])")


def sentence_case(text: str) -> str:
    """ALL-CAPS consumer narrative -> readable sentence case."""
    text = _collapse(text).lower()
    text = _SENTENCE_START.sub(lambda m: m.group(1) + m.group(2).upper(), text)
    text = re.sub(r"\bi\b", "I", text)
    return text


def clean_narrative(text: str | None) -> str | None:
    """Full complaint-narrative treatment: sentence case, then PII scrub."""
    if not text or not text.strip():
        return None
    return scrub_pii(sentence_case(text)).replace("[redacted]", "[REDACTED]")


# --- Dataset normalization ---------------------------------------------------


def _entity_lookup(
    frames: list[pl.LazyFrame], aliases: dict, canon: frozenset[str]
) -> pl.DataFrame:
    """Unique (make_raw, model_raw) -> canonical fields, computed once in Python."""
    uniq = (
        pl.concat([f.select("make_raw", "model_raw") for f in frames])
        .unique()
        .collect()
    )
    rows = []
    for make_raw, model_raw in uniq.iter_rows():
        make = canonicalize_make(make_raw, aliases, canon)
        model = canonicalize_model(make, model_raw, aliases) if make else None
        rows.append(
            {
                "make_raw": make_raw,
                "model_raw": model_raw,
                "make": make,
                "model": model,
                "make_slug": slugify(make) if make else None,
                "model_slug": slugify(model) if model else None,
                "make_display": display_name(make, aliases["display"]) if make else None,
                "model_display": display_name(model, aliases["display"]) if model else None,
            }
        )
    schema = {
        "make_raw": pl.Utf8, "model_raw": pl.Utf8, "make": pl.Utf8, "model": pl.Utf8,
        "make_slug": pl.Utf8, "model_slug": pl.Utf8,
        "make_display": pl.Utf8, "model_display": pl.Utf8,
    }
    return pl.DataFrame(rows, schema=schema)


def _split_quarantine(df: pl.DataFrame, dataset: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    reason = (
        pl.when(pl.col("make").is_null())
        .then(pl.lit("unmatched_make"))
        .when(pl.col("model").is_null())
        .then(pl.lit("missing_model"))
        .otherwise(pl.lit(None))
        .alias("quarantine_reason")
    )
    df = df.with_columns(reason)
    bad = df.filter(pl.col("quarantine_reason").is_not_null()).select(
        pl.lit(dataset).alias("dataset"),
        "quarantine_reason",
        "make_raw",
        "model_raw",
        "year_raw",
    )
    good = df.filter(pl.col("quarantine_reason").is_null()).drop("quarantine_reason")
    return good, bad


def _year_col() -> pl.Expr:
    y = pl.col("year_raw").str.strip_chars().cast(pl.Int32, strict=False)
    return (
        pl.when(y.is_null() | (y == 9999) | (y < config.MIN_YEAR) | (y > config.MAX_YEAR))
        .then(None)
        .otherwise(y)
        .alias("year")
    )


def run() -> dict:
    config.ensure_dirs()
    aliases = load_aliases()
    canon = load_vpic_makes()

    recalls = pl.scan_parquet(config.PARSED_DIR / "recalls.parquet")
    complaints = pl.scan_parquet(config.PARSED_DIR / "complaints.parquet")
    invs = pl.scan_parquet(config.PARSED_DIR / "investigations.parquet")

    # Vehicle-only filters (excluded-by-design, counted below).
    recalls_total = recalls.select(pl.len()).collect().item()
    complaints_total = complaints.select(pl.len()).collect().item()
    recalls = recalls.filter(pl.col("RCLTYPECD").str.strip_chars() == "V")
    complaints = complaints.filter(pl.col("PROD_TYPE").str.strip_chars() == "V")

    recalls = recalls.rename(
        {"MAKETXT": "make_raw", "MODELTXT": "model_raw", "YEARTXT": "year_raw"}
    )
    complaints = complaints.rename(
        {"MAKETXT": "make_raw", "MODELTXT": "model_raw", "YEARTXT": "year_raw"}
    )
    invs = invs.rename({"MAKE": "make_raw", "MODEL": "model_raw", "YEAR": "year_raw"})

    lookup = _entity_lookup([recalls, complaints, invs], aliases, canon).lazy()

    counts: dict[str, int] = {}
    quarantines: list[pl.DataFrame] = []

    def finish(lf: pl.LazyFrame, dataset: str, keep: list[pl.Expr]) -> None:
        df = (
            lf.join(lookup, on=["make_raw", "model_raw"], how="left")
            .with_columns(_year_col())
            .collect()
        )
        good, bad = _split_quarantine(df, dataset)
        good.select(keep).write_parquet(config.NORMALIZED_DIR / f"{dataset}.parquet")
        quarantines.append(bad)
        counts[dataset] = good.height
        counts[f"{dataset}_quarantined"] = bad.height

    entity_cols = [
        pl.col(c)
        for c in (
            "make", "model", "year", "make_slug", "model_slug",
            "make_display", "model_display",
        )
    ]

    finish(
        recalls,
        "recalls",
        [
            pl.col("CAMPNO").alias("campno"),
            *entity_cols,
            pl.col("MFGCAMPNO").alias("mfg_campno"),
            pl.col("COMPNAME").alias("component"),
            pl.col("MFGTXT").alias("manufacturer"),
            pl.col("POTAFF").cast(pl.Int64, strict=False).alias("affected"),
            pl.col("RCDATE").alias("report_date"),
            pl.col("DESC_DEFECT").alias("defect"),
            pl.col("CONEQUENCE_DEFECT").alias("consequence"),  # NHTSA's typo, verified
            pl.col("CORRECTIVE_ACTION").alias("action"),
            pl.col("NOTES").alias("notes"),
            pl.col("DO_NOT_DRIVE").alias("do_not_drive"),
            pl.col("PARK_OUTSIDE").alias("park_outside"),
        ],
    )

    finish(
        complaints,
        "complaints",
        [
            pl.col("CMPLID").alias("cmplid"),
            pl.col("ODINO").alias("odino"),
            *entity_cols,
            pl.col("CRASH").alias("crash"),
            pl.col("FIRE").alias("fire"),
            pl.col("INJURED").cast(pl.Int32, strict=False).fill_null(0).alias("injured"),
            pl.col("DEATHS").cast(pl.Int32, strict=False).fill_null(0).alias("deaths"),
            pl.col("COMPDESC").alias("component"),
            pl.col("FAILDATE").alias("fail_date"),
            pl.col("CDESCR")
            .map_elements(clean_narrative, return_dtype=pl.Utf8)
            .alias("narrative"),
        ],
    )

    finish(
        invs,
        "investigations",
        [
            pl.col("NHTSA_ACTION_NUMBER").alias("action_number"),
            *entity_cols,
            pl.col("COMPNAME").alias("component"),
            pl.col("SUBJECT").alias("subject"),
            pl.col("SUMMARY").alias("summary"),
            pl.col("ODATE").alias("open_date"),
            pl.col("CDATE").alias("close_date"),
            pl.col("CAMPNO").alias("campno"),
        ],
    )

    quarantine = pl.concat(quarantines) if quarantines else pl.DataFrame()
    quarantine.write_parquet(config.NORMALIZED_DIR / "quarantine.parquet")

    kept = counts["recalls"] + counts["complaints"] + counts["investigations"]
    quarantined = quarantine.height
    rate = quarantined / (kept + quarantined) if (kept + quarantined) else 0.0
    summary = {
        "counts": counts,
        "non_vehicle_excluded": {
            "recalls": recalls_total,  # totals pre-filter, for the job summary
            "complaints": complaints_total,
        },
        "quarantined": quarantined,
        "quarantine_rate": round(rate, 5),
    }
    (config.NORMALIZED_DIR / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({"step": "normalize", **summary}, indent=2))
    return summary
