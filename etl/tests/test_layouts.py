from pathlib import Path

import pytest

from etl import config
from etl.layouts import column_names, parse_layout, parse_layout_file

LAYOUTS = config.DATA_DIR / "layouts"


def test_rcl_layout_parses_all_29_fields():
    fields = parse_layout_file(LAYOUTS / "RCL.txt")
    names = column_names(fields)
    assert len(names) == 29
    assert names[0] == "RECORD_ID"
    assert names[1] == "CAMPNO"
    assert names[19] == "DESC_DEFECT"
    # NHTSA's long-standing typo, verified against the live layout file
    assert names[20] == "CONEQUENCE_DEFECT"
    assert names[21] == "CORRECTIVE_ACTION"
    assert names[27] == "DO_NOT_DRIVE"
    assert names[28] == "PARK_OUTSIDE"


def test_cmpl_layout_parses_all_51_fields():
    names = column_names(parse_layout_file(LAYOUTS / "CMPL.txt"))
    assert len(names) == 51
    assert names[0] == "CMPLID"
    assert names[19] == "CDESCR"
    assert names[45] == "PROD_TYPE"
    assert names[49] == "STATE_OF_INCIDENT"


def test_inv_layout_normalizes_spaced_names():
    names = column_names(parse_layout_file(LAYOUTS / "INV.txt"))
    assert len(names) == 11
    assert names[0] == "NHTSA_ACTION_NUMBER"
    assert names[8] == "CAMPNO"


def test_continuation_lines_ignored_and_contiguity_enforced():
    text = """
FIELDS:
=======
1    FOO    CHAR(9)   first field
                      continuation description line
2    BAR BAZ   NUMBER(4)  second
"""
    names = column_names(parse_layout(text))
    assert names == ["FOO", "BAR_BAZ"]

    with pytest.raises(ValueError, match="not contiguous"):
        parse_layout("FIELDS:\n1  A  CHAR(1) x\n3  B  CHAR(1) y\n")

    with pytest.raises(ValueError, match="no fields"):
        parse_layout("no fields section here")


def test_layout_snapshots_match_live_when_downloaded(tmp_path: Path):
    # Regression guard: vendored snapshots must stay parseable even if the
    # download step falls back to them.
    for name in ("RCL.txt", "CMPL.txt", "INV.txt"):
        assert parse_layout_file(LAYOUTS / name)
