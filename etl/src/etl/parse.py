"""Flat files -> typed parquet, one file per dataset.

Two stages per dataset:

1. Decode: stream each zip member through a cp1252 decoder with
   errors='replace' (SPEC §2.5 — files are latin-1/cp1252 with stray control
   characters), strip control chars (except tab), and write a clean UTF-8
   text file. Streaming keeps peak memory flat for the ~1 GB complaints file.

2. Parse: polars scans the decoded file as header-less TSV with column names
   taken from the dataset's *downloaded* layout file (fresh every run, so an
   upstream layout change fails loudly here instead of shifting columns).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import polars as pl

from etl import config
from etl.layouts import column_names, parse_layout_file

# Strip C0/C1 control chars except \t (field sep); \r\n handled per-line.
_CTRL = {c: None for c in range(32) if c != 9}
_CTRL.update({c: None for c in range(127, 160)})


def decode_dataset(ds: config.Dataset) -> Path:
    """Concatenate + decode all zip members of a dataset into one UTF-8 .tsv."""
    out_path = config.DECODED_DIR / f"{ds.name}.tsv"
    with out_path.open("w", encoding="utf-8", newline="\n") as out:
        for zip_url in ds.zips:
            zip_path = config.RAW_DIR / Path(zip_url).name
            with zipfile.ZipFile(zip_path) as zf:
                members = [m for m in zf.namelist() if not m.endswith("/")]
                if not members:
                    raise ValueError(f"{zip_path.name}: empty archive")
                for member in members:
                    with zf.open(member) as raw:
                        text = io.TextIOWrapper(raw, encoding="cp1252", errors="replace")
                        for line in text:
                            line = line.rstrip("\r\n").translate(_CTRL)
                            if line:
                                out.write(line)
                                out.write("\n")
    return out_path


def parse_dataset(ds: config.Dataset) -> Path:
    """Decoded TSV -> parquet with layout-derived column names (all Utf8)."""
    layout_path = config.RAW_DIR / Path(ds.layout_url).name
    if not layout_path.exists():
        layout_path = ds.layout_fallback
    cols = column_names(parse_layout_file(layout_path))

    tsv_path = config.DECODED_DIR / f"{ds.name}.tsv"
    lf = pl.scan_csv(
        tsv_path,
        separator="\t",
        has_header=False,
        new_columns=cols,
        schema={c: pl.Utf8 for c in cols},
        quote_char=None,
        truncate_ragged_lines=True,
        missing_utf8_is_empty_string=False,
    )
    # Empty strings -> null so downstream null-handling is uniform.
    lf = lf.with_columns(
        [
            pl.when(pl.col(c).str.strip_chars() == "").then(None).otherwise(pl.col(c)).alias(c)
            for c in cols
        ]
    )
    out_path = config.PARSED_DIR / f"{ds.name}.parquet"
    lf.sink_parquet(out_path)
    return out_path


def run() -> dict:
    config.ensure_dirs()
    counts: dict[str, int] = {}
    for ds in config.DATASETS.values():
        decode_dataset(ds)
        out = parse_dataset(ds)
        counts[ds.name] = pl.scan_parquet(out).select(pl.len()).collect().item()
        # Decoded TSVs are large; drop them as soon as parquet exists.
        (config.DECODED_DIR / f"{ds.name}.tsv").unlink(missing_ok=True)
    print({"step": "parse", "rows": counts})
    return {"rows": counts}
