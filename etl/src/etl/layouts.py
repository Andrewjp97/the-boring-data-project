"""Parse NHTSA layout .txt files into ordered column maps.

The flat files ship with sibling layout files (RCL.txt, CMPL.txt, INV.txt)
describing tab-delimited fields. Per SPEC §2 we generate parser column maps
from those files instead of hardcoding, so a layout change shows up as a
loud parse failure rather than silently shifted columns.

Layout lines look like:

    20       DESC_DEFECT         CHAR(6000)  Defect Summary
    1        NHTSA ACTION NUMBER CHAR(10)    NHTSA Identification Number

Names may contain spaces (normalized to underscores) and columns may be
separated by tabs or runs of spaces. Description continuation lines carry
no leading field number and are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_FIELD_RE = re.compile(
    r"^\s*(?P<num>\d{1,3})\s+"
    r"(?P<name>[A-Z][A-Z0-9_' ]*?)\s+"
    r"(?P<type>CHAR|NUMBER)\s*\(\s*(?P<size>\d+)\s*\)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LayoutField:
    number: int
    name: str
    kind: str  # 'CHAR' | 'NUMBER'
    size: int


def _normalize_name(raw: str) -> str:
    name = re.sub(r"\s+", "_", raw.strip().upper())
    return re.sub(r"[^A-Z0-9_]", "", name)


def parse_layout(text: str) -> list[LayoutField]:
    """Extract the ordered field list from a layout file's text."""
    fields: dict[int, LayoutField] = {}
    in_fields = False
    for raw_line in text.splitlines():
        line = raw_line.replace("\t", "    ")
        if re.match(r"^\s*FIELDS\s*:?\s*$", line, re.IGNORECASE):
            in_fields = True
            continue
        if not in_fields:
            continue
        m = _FIELD_RE.match(line)
        if not m:
            continue
        num = int(m.group("num"))
        if num in fields:
            raise ValueError(f"duplicate field number {num} in layout")
        fields[num] = LayoutField(
            number=num,
            name=_normalize_name(m.group("name")),
            kind=m.group("type").upper(),
            size=int(m.group("size")),
        )

    if not fields:
        raise ValueError("no fields parsed from layout file")
    ordered = [fields[i] for i in sorted(fields)]
    expected = list(range(1, len(ordered) + 1))
    if [f.number for f in ordered] != expected:
        raise ValueError(
            f"layout field numbers not contiguous: {[f.number for f in ordered]}"
        )
    return ordered


def parse_layout_file(path: Path) -> list[LayoutField]:
    # Layout files themselves are ASCII-ish; be permissive.
    return parse_layout(path.read_text(encoding="cp1252", errors="replace"))


def column_names(fields: list[LayoutField]) -> list[str]:
    return [f.name for f in fields]
