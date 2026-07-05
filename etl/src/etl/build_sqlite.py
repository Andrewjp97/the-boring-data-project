"""Normalized parquet -> relational build.db (local truth for diffing/tests).

Campaigns are deduped here (SPEC §2.4): one row per CAMPNO in `campaigns`,
with the make/model/year fan-out in `campaign_vehicles`. Firestore never sees
this schema — build_pages.py denormalizes it back out to page docs.
"""

from __future__ import annotations

import json
import sqlite3

import polars as pl

from etl import config

_SCHEMA = """
DROP TABLE IF EXISTS campaigns;
DROP TABLE IF EXISTS campaign_vehicles;
DROP TABLE IF EXISTS complaints;
DROP TABLE IF EXISTS investigations;
DROP TABLE IF EXISTS quarantine;
DROP TABLE IF EXISTS build_meta;

CREATE TABLE campaigns (
  campno TEXT PRIMARY KEY,
  mfg_campno TEXT, component TEXT, manufacturer TEXT,
  affected INTEGER, report_date TEXT,
  defect TEXT, consequence TEXT, action TEXT, notes TEXT,
  do_not_drive TEXT, park_outside TEXT
);
CREATE TABLE campaign_vehicles (
  campno TEXT NOT NULL,
  make TEXT NOT NULL, model TEXT NOT NULL, year INTEGER,
  make_slug TEXT NOT NULL, model_slug TEXT NOT NULL,
  make_display TEXT NOT NULL, model_display TEXT NOT NULL,
  PRIMARY KEY (campno, make_slug, model_slug, year)
);
CREATE INDEX idx_cv_entity ON campaign_vehicles (make_slug, model_slug, year);
CREATE TABLE complaints (
  cmplid TEXT, odino TEXT,
  make TEXT, model TEXT, year INTEGER,
  make_slug TEXT, model_slug TEXT, make_display TEXT, model_display TEXT,
  crash TEXT, fire TEXT, injured INTEGER, deaths INTEGER,
  component TEXT, fail_date TEXT, narrative TEXT
);
CREATE INDEX idx_cmpl_entity ON complaints (make_slug, model_slug, year);
CREATE TABLE investigations (
  action_number TEXT,
  make TEXT, model TEXT, year INTEGER,
  make_slug TEXT, model_slug TEXT, make_display TEXT, model_display TEXT,
  component TEXT, subject TEXT, summary TEXT,
  open_date TEXT, close_date TEXT, campno TEXT
);
CREATE INDEX idx_inv_entity ON investigations (make_slug, model_slug, year);
CREATE TABLE quarantine (
  dataset TEXT, quarantine_reason TEXT,
  make_raw TEXT, model_raw TEXT, year_raw TEXT
);
CREATE TABLE build_meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _insert_df(conn: sqlite3.Connection, table: str, df: pl.DataFrame) -> None:
    if df.height == 0:
        return
    cols = df.columns
    placeholders = ",".join("?" * len(cols))
    sql = f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({placeholders})"
    conn.executemany(sql, df.iter_rows())


def run() -> dict:
    config.ensure_dirs()
    recalls = pl.read_parquet(config.NORMALIZED_DIR / "recalls.parquet")
    complaints = pl.read_parquet(config.NORMALIZED_DIR / "complaints.parquet")
    invs = pl.read_parquet(config.NORMALIZED_DIR / "investigations.parquet")
    quarantine = pl.read_parquet(config.NORMALIZED_DIR / "quarantine.parquet")

    # Dedupe campaigns: keep the row with the longest defect text per CAMPNO
    # (rows are recall x model; texts occasionally differ in whitespace only).
    campaigns = (
        recalls.filter(pl.col("campno").is_not_null())
        .sort(pl.col("defect").str.len_chars().fill_null(0), descending=True)
        .unique(subset=["campno"], keep="first")
        .select(
            "campno", "mfg_campno", "component", "manufacturer", "affected",
            "report_date", "defect", "consequence", "action", "notes",
            "do_not_drive", "park_outside",
        )
    )

    # Pre-1977 campaigns carry their entire description in NOTES with empty
    # defect/action fields — promote NOTES to defect so the §10 "no empty
    # texts" assertion holds and the pages have real content.
    def _empty(col: str) -> pl.Expr:
        return pl.col(col).str.strip_chars().fill_null("") == ""

    promote = _empty("defect") & _empty("action") & ~_empty("notes")
    campaigns = campaigns.with_columns(
        pl.when(promote).then(pl.col("notes")).otherwise(pl.col("defect")).alias("defect"),
        pl.when(promote).then(None).otherwise(pl.col("notes")).alias("notes"),
    )
    # A campaign with no defect, no action, and no notes is unrenderable —
    # drop it (counted below) rather than block the pipeline forever on a
    # handful of 1960s records.
    dropped_empty = campaigns.filter(_empty("defect") & _empty("action"))
    campaigns = campaigns.filter(~(_empty("defect") & _empty("action")))
    dropped_ids = set(dropped_empty["campno"].to_list())
    vehicles = (
        recalls.filter(pl.col("campno").is_not_null() & ~pl.col("campno").is_in(dropped_ids))
        .select(
            "campno", "make", "model", "year",
            "make_slug", "model_slug", "make_display", "model_display",
        )
        .unique()
    )

    config.SQLITE_PATH.unlink(missing_ok=True)
    conn = sqlite3.connect(config.SQLITE_PATH)
    try:
        conn.executescript(_SCHEMA)
        _insert_df(conn, "campaigns", campaigns)
        _insert_df(conn, "campaign_vehicles", vehicles)
        _insert_df(conn, "complaints", complaints)
        _insert_df(conn, "investigations", invs)
        _insert_df(conn, "quarantine", quarantine)

        summary_path = config.NORMALIZED_DIR / "summary.json"
        if summary_path.exists():
            conn.execute(
                "INSERT INTO build_meta (key, value) VALUES ('normalize_summary', ?)",
                (summary_path.read_text(),),
            )
        conn.commit()

        tables = ("campaigns", "campaign_vehicles", "complaints", "investigations", "quarantine")
        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
            for t in tables
        }
        counts["campaigns_dropped_no_text"] = len(dropped_ids)
    finally:
        conn.close()

    print(json.dumps({"step": "build-sqlite", "counts": counts}, indent=2))
    return {"counts": counts}
