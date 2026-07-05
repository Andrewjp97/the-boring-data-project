"""Shared fixtures: a miniature NHTSA universe exercised end-to-end.

Fixture flat files are generated as cp1252 bytes (including stray control
chars and curly quotes) and zipped exactly like NHTSA ships them; config
paths are redirected into tmp_path so tests never touch etl/work or the
real site/public outputs.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from etl import config
from etl.layouts import column_names, parse_layout_file

RCL_COLS = column_names(parse_layout_file(config.DATA_DIR / "layouts" / "RCL.txt"))
CMPL_COLS = column_names(parse_layout_file(config.DATA_DIR / "layouts" / "CMPL.txt"))
INV_COLS = column_names(parse_layout_file(config.DATA_DIR / "layouts" / "INV.txt"))


def row(cols: list[str], **values: str) -> str:
    unknown = set(values) - set(cols)
    assert not unknown, f"unknown columns: {unknown}"
    return "\t".join(values.get(c, "") for c in cols)


def rcl_row(**values: str) -> str:
    defaults = {
        "RECORD_ID": "1",
        "CAMPNO": "23V123000",
        "MAKETXT": "HONDA",
        "MODELTXT": "CR-V",
        "YEARTXT": "2016",
        "COMPNAME": "FUEL SYSTEM, GASOLINE",
        "MFGNAME": "Honda (American Honda Motor Co.)",
        "RCLTYPECD": "V",
        "POTAFF": "412000",
        "MFGTXT": "Honda (American Honda Motor Co.)",
        "RCDATE": "20230415",
        "DESC_DEFECT": "FUEL PUMP MAY FAIL CAUSING ENGINE STALL",
        "CONEQUENCE_DEFECT": "AN ENGINE STALL INCREASES THE RISK OF A CRASH",
        "CORRECTIVE_ACTION": "DEALERS WILL REPLACE THE FUEL PUMP FREE OF CHARGE",
    }
    defaults.update(values)
    return row(RCL_COLS, **defaults)


def cmpl_row(**values: str) -> str:
    defaults = {
        "CMPLID": "100001",
        "ODINO": "11500001",
        "MFR_NAME": "Honda (American Honda Motor Co.)",
        "MAKETXT": "HONDA",
        "MODELTXT": "CR-V",
        "YEARTXT": "2016",
        "CRASH": "N",
        "FIRE": "N",
        "INJURED": "0",
        "DEATHS": "0",
        "COMPDESC": "FUEL/PROPULSION SYSTEM",
        "FAILDATE": "20230101",
        "CDESCR": "THE VEHICLE STALLED ON THE HIGHWAY WITHOUT WARNING.",
        "PROD_TYPE": "V",
        "CMPL_TYPE": "IVOQ",
    }
    defaults.update(values)
    return row(CMPL_COLS, **defaults)


def inv_row(**values: str) -> str:
    defaults = {
        "NHTSA_ACTION_NUMBER": "PE23012",
        "MAKE": "HONDA",
        "MODEL": "CR-V",
        "YEAR": "2016",
        "COMPNAME": "FUEL SYSTEM, GASOLINE",
        "MFR_NAME": "Honda (American Honda Motor Co.)",
        "ODATE": "20230301",
        "CDATE": "",
        "CAMPNO": "23V123000",
        "SUBJECT": "Loss of motive power",
        "SUMMARY": "ODI opened this investigation after reports of stalling.",
    }
    defaults.update(values)
    return row(INV_COLS, **defaults)


# One CAMPNO across three model years + a sibling model -> dedupe fixture.
RCL_ROWS = [
    rcl_row(RECORD_ID="1", YEARTXT="2015"),
    rcl_row(RECORD_ID="2", YEARTXT="2016"),
    rcl_row(RECORD_ID="3", YEARTXT="2017"),
    rcl_row(RECORD_ID="4", CAMPNO="24V456000", YEARTXT="2016",
            COMPNAME="ELECTRICAL SYSTEM", POTAFF="5000", RCDATE="20240110",
            DESC_DEFECT="WIRING HARNESS MAY CHAFE \x92CAUSING\x92 A SHORT\x00 CIRCUIT",
            CONEQUENCE_DEFECT="A SHORT CIRCUIT INCREASES THE RISK OF FIRE",
            CORRECTIVE_ACTION="DEALERS WILL INSPECT AND REPAIR THE HARNESS"),
    # Alias exercise: CHEVY -> CHEVROLET
    rcl_row(RECORD_ID="5", CAMPNO="22V789000", MAKETXT="CHEVY", MODELTXT="SILVERADO 1500",
            YEARTXT="2020", COMPNAME="ELECTRICAL SYSTEM", POTAFF="90000",
            DESC_DEFECT="BATTERY CABLE MAY LOOSEN",
            CONEQUENCE_DEFECT="LOSS OF POWER INCREASES THE RISK OF A CRASH",
            CORRECTIVE_ACTION="DEALERS WILL TIGHTEN THE CABLE"),
    # MERCEDES BENZ -> MERCEDES-BENZ, unknown year 9999 -> model page only
    rcl_row(RECORD_ID="6", CAMPNO="21V111000", MAKETXT="MERCEDES BENZ", MODELTXT="C-CLASS",
            YEARTXT="9999", COMPNAME="AIR BAGS", POTAFF="12000",
            DESC_DEFECT="AIR BAG INFLATOR MAY RUPTURE",
            CONEQUENCE_DEFECT="METAL FRAGMENTS INCREASE THE RISK OF INJURY",
            CORRECTIVE_ACTION="DEALERS WILL REPLACE THE INFLATOR"),
    # Non-vehicle recall: excluded by design, not quarantined
    rcl_row(RECORD_ID="7", CAMPNO="23E001000", MAKETXT="ACME EQUIPMENT",
            MODELTXT="TRAILER HITCH", YEARTXT="9999", RCLTYPECD="E",
            DESC_DEFECT="HITCH MAY CRACK",
            CONEQUENCE_DEFECT="DETACHMENT RISK",
            CORRECTIVE_ACTION="REPLACE HITCH"),
    # Unmatched make -> quarantine
    rcl_row(RECORD_ID="8", CAMPNO="20V222000", MAKETXT="ZZZZ NOT A REAL MAKE",
            MODELTXT="WIDGET", YEARTXT="2019",
            DESC_DEFECT="X", CONEQUENCE_DEFECT="Y", CORRECTIVE_ACTION="Z"),
]

CMPL_ROWS = [
    # PII-laden narrative
    cmpl_row(CMPLID="100001",
             CDESCR="MY CAR STALLED. VIN 1HGRM4870GL123456. CALL ME AT (555) 123-4567 "
                    "OR EMAIL JOHN.DOE@EXAMPLE.COM. I WAS SCARED."),
    cmpl_row(CMPLID="100002", CRASH="Y", INJURED="2",
             COMPDESC="ELECTRICAL SYSTEM", FAILDATE="20230215",
             CDESCR="SMOKE CAME FROM THE DASHBOARD WHILE DRIVING."),
    # Alias + cp1252 narrative
    cmpl_row(CMPLID="100003", MAKETXT="CHEVY", MODELTXT="SILVERADO 1500", YEARTXT="2020",
             COMPDESC="ELECTRICAL SYSTEM", FAILDATE="20230330",
             CDESCR="TRUCK WOULDN\x92T START AFTER PARKING OVERNIGHT."),
    # Non-vehicle complaint: excluded
    cmpl_row(CMPLID="100004", PROD_TYPE="T", MAKETXT="GOODYEAR", MODELTXT="WRANGLER",
             CDESCR="TIRE FAILED."),
    # 30 complaints for a zero-recall vehicle -> indexable (>= 10); the volume
    # also keeps the fixture-wide quarantine rate under the 3% threshold.
    *[
        cmpl_row(CMPLID=str(200000 + i), MAKETXT="MAZDA", MODELTXT="CX-30", YEARTXT="2021",
                 COMPDESC="FORWARD COLLISION AVOIDANCE", FAILDATE=f"202303{(i % 28) + 1:02d}",
                 CDESCR="THE AUTOMATIC BRAKING ACTIVATED WITH NOTHING IN THE ROAD.")
        for i in range(30)
    ],
    # 3 complaints only -> zero-recall page stays noindex
    *[
        cmpl_row(CMPLID=str(300000 + i), MAKETXT="KIA", MODELTXT="SOUL", YEARTXT="2022",
                 COMPDESC="STRUCTURE", FAILDATE=f"202304{i + 10:02d}",
                 CDESCR="RATTLE FROM THE REAR HATCH.")
        for i in range(3)
    ],
]

INV_ROWS = [inv_row()]


def _write_zip(path: Path, member: str, rows: list[str]) -> None:
    # latin-1 maps \x00-\xff 1:1, so "\x92" in fixtures lands as raw byte 0x92
    # — exactly the stray-cp1252-byte shape the decoder must handle.
    data = ("\n".join(rows) + "\n").encode("latin-1", errors="replace")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member, data)


@pytest.fixture()
def etl_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect all ETL working dirs into tmp_path and lay down fixture zips."""
    for name, sub in {
        "WORK_DIR": "work",
        "BUILD_DIR": "build",
        "RAW_DIR": "work/raw",
        "DECODED_DIR": "work/decoded",
        "PARSED_DIR": "work/parsed",
        "NORMALIZED_DIR": "work/normalized",
        "STATE_DIR": "build/state",
        "PAGES_DIR": "build/pages",
        "DIFF_DIR": "build/diff",
    }.items():
        monkeypatch.setattr(config, name, tmp_path / sub)
    monkeypatch.setattr(config, "SQLITE_PATH", tmp_path / "build/build.db")
    monkeypatch.setattr(config, "SITE_PUBLIC_DIR", tmp_path / "site-public")
    monkeypatch.setattr(config, "SITEMAPS_DIR", tmp_path / "site-public/sitemaps")
    monkeypatch.setattr(config, "SEARCH_INDEX_PATH", tmp_path / "site-public/search-index.json")
    # Fixture set is tiny; drop prod-scale floors so verify() is testable.
    monkeypatch.setattr(config, "MIN_CAMPAIGN_COUNT", 3)
    config.ensure_dirs()

    _write_zip(config.RAW_DIR / "FLAT_RCL_PRE_2010.zip", "FLAT_RCL_PRE_2010.txt", [])
    _write_zip(config.RAW_DIR / "FLAT_RCL_POST_2010.zip", "FLAT_RCL_POST_2010.txt", RCL_ROWS)
    _write_zip(config.RAW_DIR / "FLAT_CMPL.zip", "FLAT_CMPL.txt", CMPL_ROWS)
    _write_zip(config.RAW_DIR / "FLAT_INV.zip", "FLAT_INV.txt", INV_ROWS)
    return tmp_path


@pytest.fixture()
def built_pages(etl_env: Path) -> Path:
    """Run parse -> normalize -> build-sqlite -> build-pages on the fixtures."""
    from etl import build_pages, build_sqlite, normalize, parse

    parse.run()
    normalize.run()
    build_sqlite.run()
    build_pages.run()
    return etl_env
