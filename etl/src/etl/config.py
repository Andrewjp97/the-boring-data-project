"""Central configuration: paths, dataset definitions, tunables.

All working data lives under etl/work/ (raw downloads, decoded text, parquet)
and etl/build/ (build.db, page docs, manifests, state carried between runs).
Both are gitignored; CI persists etl/build/state/ via actions/cache.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ETL_ROOT = Path(__file__).resolve().parents[2]  # .../etl
REPO_ROOT = ETL_ROOT.parent

DATA_DIR = ETL_ROOT / "data"  # committed: aliases.yaml, layouts/, vpic_makes.json
WORK_DIR = Path(os.environ.get("ETL_WORK_DIR", ETL_ROOT / "work"))
BUILD_DIR = Path(os.environ.get("ETL_BUILD_DIR", ETL_ROOT / "build"))

RAW_DIR = WORK_DIR / "raw"
DECODED_DIR = WORK_DIR / "decoded"
PARSED_DIR = WORK_DIR / "parsed"
NORMALIZED_DIR = WORK_DIR / "normalized"

STATE_DIR = BUILD_DIR / "state"  # previous-run checksums + manifest (actions/cache)
PAGES_DIR = BUILD_DIR / "pages"
DIFF_DIR = BUILD_DIR / "diff"
SQLITE_PATH = BUILD_DIR / "build.db"

SITE_PUBLIC_DIR = REPO_ROOT / "site" / "public"
SITEMAPS_DIR = SITE_PUBLIC_DIR / "sitemaps"
SEARCH_INDEX_PATH = SITE_PUBLIC_DIR / "search-index.json"

SITE_URL = os.environ.get("SITE_URL", "https://recalllookup.example").rstrip("/")
SITE_NAME = os.environ.get("SITE_NAME", "RecallLookup")

STATIC_BASE = "https://static.nhtsa.gov/odi/ffdd"


@dataclass(frozen=True)
class Dataset:
    """One NHTSA flat-file dataset: N data zips + one layout .txt."""

    name: str
    zips: list[str]
    layout_url: str
    layout_fallback: Path = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "layout_fallback", DATA_DIR / "layouts" / Path(self.layout_url).name
        )


# URL note (verified 2026-07): NHTSA split the recalls flat file into
# PRE_2010/POST_2010 zips; the single FLAT_RCL.zip from older docs now 404s.
DATASETS: dict[str, Dataset] = {
    "recalls": Dataset(
        name="recalls",
        zips=[
            f"{STATIC_BASE}/rcl/FLAT_RCL_PRE_2010.zip",
            f"{STATIC_BASE}/rcl/FLAT_RCL_POST_2010.zip",
        ],
        layout_url=f"{STATIC_BASE}/rcl/RCL.txt",
    ),
    "complaints": Dataset(
        name="complaints",
        zips=[f"{STATIC_BASE}/cmpl/FLAT_CMPL.zip"],
        layout_url=f"{STATIC_BASE}/cmpl/CMPL.txt",
    ),
    "investigations": Dataset(
        name="investigations",
        zips=[f"{STATIC_BASE}/inv/FLAT_INV.zip"],
        layout_url=f"{STATIC_BASE}/inv/INV.txt",
    ),
}

VPIC_ALL_MAKES_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/GetAllMakes?format=json"
# Spot-check by campaign number, NOT recallsByVehicle: the API's vehicle index
# uses different make/model spellings than the flat files (e.g. 'INTERNATIONAL
# MOTORS' vs 'INTERNATIONAL'), which yields false "missing campaign" hits.
RECALLS_BY_CAMPAIGN_URL = "https://api.nhtsa.gov/recalls/campaignNumber"

# --- Page-build tunables -------------------------------------------------

MAX_DOC_BYTES = 800 * 1024  # assert well under Firestore's 1 MiB
TRUNCATE_TEXT_CHARS = 320  # defect/consequence/action on list pages
COMPLAINT_SAMPLE_MAX = 8
COMPLAINT_SAMPLE_CHARS = 300
COMPLAINT_STATS_TOP_N = 10
INDEXABLE_MIN_COMPLAINTS = 10  # zero-recall pages need >= this many complaints
NAV_ADJACENT_YEARS = 3
NAV_SIBLING_MODELS = 12
NAV_RELATED_BY_COMPONENT = 6
SITEMAP_SHARD_SIZE = 45_000

MIN_YEAR = 1949
MAX_YEAR = 2035

# --- Integrity assertion thresholds (SPEC §10) ---------------------------

# Vehicle-only scope (RCLTYPECD='V'): ~20.3k campaigns as of 2026-07.
# The SPEC's ">25,000" figure counted equipment/tire/child-seat campaigns too.
MIN_CAMPAIGN_COUNT = 18_000
MAX_CAMPAIGN_DRIFT = 0.10  # ±10% vs previous run
MAX_QUARANTINE_RATE = 0.03
MAX_DOC_COUNT_DRIFT = 0.10

FIRESTORE_PAGES_COLLECTION = "pages"
FIRESTORE_CAMPAIGNS_COLLECTION = "campaignPages"
FIRESTORE_META_COLLECTION = "meta"


def ensure_dirs() -> None:
    for d in (RAW_DIR, DECODED_DIR, PARSED_DIR, NORMALIZED_DIR, STATE_DIR, PAGES_DIR, DIFF_DIR):
        d.mkdir(parents=True, exist_ok=True)
