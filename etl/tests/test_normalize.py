import pytest

from etl.normalize import (
    canonicalize_make,
    canonicalize_model,
    clean_narrative,
    display_name,
    load_aliases,
    load_vpic_makes,
    parse_year,
    scrub_pii,
    sentence_case,
    slugify,
)

ALIASES = load_aliases()
CANON = load_vpic_makes()


# SPEC Phase 1 acceptance: >= 20 alias fixtures.
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CHEVY", "CHEVROLET"),
        ("CHEV", "CHEVROLET"),
        ("chevy ", "CHEVROLET"),
        ("CHEVROLET", "CHEVROLET"),
        ("MERCEDES BENZ", "MERCEDES-BENZ"),
        ("MERCEDES  BENZ", "MERCEDES-BENZ"),  # double space collapsed
        ("MERCEDESBENZ", "MERCEDES-BENZ"),
        ("MERCEDES", "MERCEDES-BENZ"),
        ("VW", "VOLKSWAGEN"),
        ("VOLKSWAGEN", "VOLKSWAGEN"),
        ("LANDROVER", "LAND ROVER"),
        ("ALFA", "ALFA ROMEO"),
        ("HARLEY DAVIDSON", "HARLEY-DAVIDSON"),
        ("ROLLS ROYCE", "ROLLS-ROYCE"),
        ("DATSUN", "NISSAN"),
        ("NISSAN/DATSUN", "NISSAN"),
        ("GM", "GMC"),
        ("GENERAL MOTORS", "GMC"),
        ("KIA MOTORS", "KIA"),
        ("HYUNDAI MOTOR", "HYUNDAI"),
        ("SUBARU OF AMERICA", "SUBARU"),
        ("MITSUBISHI MOTORS", "MITSUBISHI"),
        ("TESLA MOTORS", "TESLA"),
        ("DODGE RAM", "RAM"),
        ("  HONDA  ", "HONDA"),  # trailing whitespace
        ("TOYOTA", "TOYOTA"),
    ],
)
def test_make_aliases(raw: str, expected: str):
    assert canonicalize_make(raw, ALIASES, CANON) == expected


def test_unmatched_make_quarantines():
    assert canonicalize_make("ZZZZ NOT A REAL MAKE", ALIASES, CANON) is None
    assert canonicalize_make("", ALIASES, CANON) is None
    assert canonicalize_make(None, ALIASES, CANON) is None
    assert canonicalize_make("   ", ALIASES, CANON) is None


def test_empty_canon_accepts_any_nonempty_make():
    assert canonicalize_make("OBSCURE COACHWORKS", ALIASES, frozenset()) == (
        "OBSCURE COACHWORKS"
    )


def test_model_aliases():
    assert canonicalize_model("HONDA", "CRV", ALIASES) == "CR-V"
    assert canonicalize_model("HONDA", "CR V", ALIASES) == "CR-V"
    assert canonicalize_model("TOYOTA", "RAV 4", ALIASES) == "RAV4"
    assert canonicalize_model("FORD", "F150", ALIASES) == "F-150"
    assert canonicalize_model("HONDA", "  ACCORD  ", ALIASES) == "ACCORD"
    assert canonicalize_model("HONDA", None, ALIASES) is None
    assert canonicalize_model("HONDA", " ", ALIASES) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2016", 2016),
        (" 2016 ", 2016),
        ("9999", None),  # SPEC §2.2: unknown/all years
        ("0", None),
        ("1899", None),
        ("3000", None),
        ("ABCD", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_year(raw, expected):
    assert parse_year(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CR-V", "cr-v"),
        ("SILVERADO 1500", "silverado-1500"),
        ("MERCEDES-BENZ", "mercedes-benz"),
        ("  F-150  ", "f-150"),
        ("TOWN & COUNTRY", "town-and-country"),
        ("ID.4", "id-4"),
        ("C/K 1500", "c-k-1500"),
    ],
)
def test_slugify(raw: str, expected: str):
    assert slugify(raw) == expected


def test_display_name():
    d = ALIASES["display"]
    assert display_name("HONDA", d) == "Honda"
    assert display_name("BMW", d) == "BMW"
    assert display_name("MERCEDES-BENZ", d) == "Mercedes-Benz"
    assert display_name("LAND ROVER", d) == "Land Rover"
    assert display_name("CR-V", d) == "CR-V"
    assert display_name("SILVERADO 1500", d) == "Silverado 1500"
    assert display_name("TOWN AND COUNTRY", d) == "Town and Country"


class TestPiiScrub:
    def test_vin_redacted(self):
        assert scrub_pii("vin 1HGRM4870GL123456 failed") == "vin [REDACTED] failed"
        assert scrub_pii("vin 1hgrm4870gl123456 failed") == "vin [REDACTED] failed"

    def test_vin_like_with_ioq_not_redacted(self):
        # I, O, Q never appear in real VINs; 17-char words containing them stay.
        assert "IIIIIIIIIIIIIIIII" in scrub_pii("word IIIIIIIIIIIIIIIII here")

    def test_phone_redacted(self):
        assert scrub_pii("call (555) 123-4567 now") == "call [REDACTED] now"
        assert scrub_pii("call 555-123-4567 now") == "call [REDACTED] now"
        assert scrub_pii("call 555.123.4567 now") == "call [REDACTED] now"
        assert scrub_pii("call 1-555-123-4567 now") == "call [REDACTED] now"

    def test_email_redacted(self):
        assert scrub_pii("email john.doe@example.com ok") == "email [REDACTED] ok"

    def test_mileage_not_redacted(self):
        assert scrub_pii("at 123456 miles") == "at 123456 miles"

    def test_sentence_case(self):
        assert (
            sentence_case("THE CAR STALLED. I WAS SCARED! WHAT NOW?")
            == "The car stalled. I was scared! What now?"
        )

    def test_clean_narrative_end_to_end(self):
        raw = (
            "MY CAR STALLED. VIN 1HGRM4870GL123456. CALL ME AT (555) 123-4567 "
            "OR EMAIL JOHN.DOE@EXAMPLE.COM."
        )
        out = clean_narrative(raw)
        assert out is not None
        assert "1HGRM4870GL123456".lower() not in out.lower()
        assert "555" not in out
        assert "example.com" not in out.lower()
        assert out.count("[REDACTED]") == 3
        assert out.startswith("My car stalled.")

    def test_clean_narrative_empty(self):
        assert clean_narrative(None) is None
        assert clean_narrative("   ") is None


def test_cp1252_garbage_survives_decoding(built_pages):
    """The \\x92 curly quote and \\x00 control char from the fixture zip."""
    import polars as pl

    from etl import config

    recalls = pl.read_parquet(config.NORMALIZED_DIR / "recalls.parquet")
    defect = recalls.filter(pl.col("campno") == "24V456000")["defect"][0]
    assert "’CAUSING’" in defect  # cp1252 0x92 -> right single quote
    assert "\x00" not in defect  # control chars stripped
    assert "SHORT CIRCUIT" in defect

    complaints = pl.read_parquet(config.NORMALIZED_DIR / "complaints.parquet")
    narr = complaints.filter(pl.col("cmplid") == "100003")["narrative"][0]
    assert "wouldn’t start" in narr


def test_quarantine_and_exclusions(built_pages):
    import json

    import polars as pl

    from etl import config

    q = pl.read_parquet(config.NORMALIZED_DIR / "quarantine.parquet")
    assert q.filter(
        (pl.col("make_raw") == "ZZZZ NOT A REAL MAKE")
        & (pl.col("quarantine_reason") == "unmatched_make")
    ).height == 1
    # Non-vehicle rows are excluded by design, not quarantined.
    assert q.filter(pl.col("make_raw") == "ACME EQUIPMENT").height == 0
    assert q.filter(pl.col("make_raw") == "GOODYEAR").height == 0

    summary = json.loads((config.NORMALIZED_DIR / "summary.json").read_text())
    assert summary["quarantined"] == 1
    assert 0 < summary["quarantine_rate"] < 0.03
