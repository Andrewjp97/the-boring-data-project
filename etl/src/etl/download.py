"""Download NHTSA flat files + layout files, checksum, detect no-op weeks.

Writes everything under work/raw/. Emits raw/checksums.json and compares it
with build/state/checksums.json (the previous run, restored from actions/cache
in CI). If nothing changed, `changed` is False and the pipeline can exit as a
no-op before doing any real work.

The state copy is only advanced by commit_state_checksums(), which
push_firestore.push() calls after the docs have shipped — a week counts as
"seen" only once it fully processed and pushed, so a failed run is re-processed
from scratch the following week instead of being skipped as a no-op.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

import httpx

from etl import config

CHUNK = 1 << 20


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _fetch(client: httpx.Client, url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with client.stream("GET", url) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as out:
            for chunk in resp.iter_bytes(CHUNK):
                out.write(chunk)
    tmp.replace(dest)


def _write_github_output(changed: bool) -> None:
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")


def commit_state_checksums() -> None:
    """Promote this run's raw checksums to the no-op baseline for next week."""
    src = config.RAW_DIR / "checksums.json"
    if src.exists():
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, config.STATE_DIR / "checksums.json")


def run(force: bool = False, retries: int = 3) -> dict:
    """Download all datasets. Returns {'changed': bool, 'checksums': {...}}."""
    config.ensure_dirs()
    checksums: dict[str, str] = {}

    with httpx.Client(timeout=httpx.Timeout(60.0, read=300.0), follow_redirects=True) as client:
        for ds in config.DATASETS.values():
            for url in [*ds.zips, ds.layout_url]:
                dest = config.RAW_DIR / Path(url).name
                last_err: Exception | None = None
                for attempt in range(retries):
                    try:
                        _fetch(client, url, dest)
                        last_err = None
                        break
                    except httpx.HTTPError as err:  # noqa: PERF203
                        last_err = err
                        print(f"download attempt {attempt + 1} failed for {url}: {err}")
                if last_err is not None:
                    # Layout files: fall back to the vendored snapshot so a
                    # transient .txt outage doesn't kill the run.
                    if url == ds.layout_url and ds.layout_fallback.exists():
                        shutil.copy(ds.layout_fallback, dest)
                        print(f"using vendored layout fallback for {url}")
                    else:
                        raise last_err
                checksums[dest.name] = _sha256(dest)

    (config.RAW_DIR / "checksums.json").write_text(json.dumps(checksums, indent=2))

    prev_path = config.STATE_DIR / "checksums.json"
    prev = json.loads(prev_path.read_text()) if prev_path.exists() else None
    changed = force or prev != checksums

    _write_github_output(changed)
    result = {"changed": changed, "checksums": checksums}
    print(json.dumps({"step": "download", **result}, indent=2))
    return result
