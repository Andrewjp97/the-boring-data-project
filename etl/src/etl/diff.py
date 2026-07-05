"""Diff current page docs vs previous run -> changed docs + deleted IDs.

Inputs:  build/pages/manifest.json (this run), build/state/manifest.json (prev)
Outputs: build/diff/changed.jsonl   docs to upsert (subset of pages.jsonl)
         build/diff/deleted.json    [{id, collection}] to delete
         build/state/manifest.json  updated manifest incl. per-doc lastmod,
                                    carried to the next run via actions/cache
"""

from __future__ import annotations

import datetime
import json

from etl import config


def run(force_full: bool = False) -> dict:
    manifest = json.loads((config.PAGES_DIR / "manifest.json").read_text())
    prev_path = config.STATE_DIR / "manifest.json"
    prev = json.loads(prev_path.read_text()) if prev_path.exists() and not force_full else {}

    today = datetime.date.today().isoformat()
    changed_ids: set[str] = set()
    for doc_id, entry in manifest.items():
        old = prev.get(doc_id)
        if old is None or old["hash"] != entry["hash"]:
            changed_ids.add(doc_id)
            entry["lastmod"] = today
        else:
            entry["lastmod"] = old.get("lastmod", today)

    deleted = [
        {"id": doc_id, "collection": entry.get("collection", config.FIRESTORE_PAGES_COLLECTION)}
        for doc_id, entry in prev.items()
        if doc_id not in manifest
    ]

    config.DIFF_DIR.mkdir(parents=True, exist_ok=True)
    n_written = 0
    with (
        (config.PAGES_DIR / "pages.jsonl").open(encoding="utf-8") as src,
        (config.DIFF_DIR / "changed.jsonl").open("w", encoding="utf-8") as out,
    ):
        for line in src:
            if json.loads(line)["id"] in changed_ids:
                out.write(line)
                n_written += 1
    (config.DIFF_DIR / "deleted.json").write_text(json.dumps(deleted, indent=1))

    slug_set_changed = set(manifest) != set(prev)
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    prev_path.write_text(json.dumps(manifest, indent=1))

    summary = {
        "changed": n_written,
        "deleted": len(deleted),
        "total": len(manifest),
        "slugSetChanged": slug_set_changed,
    }
    (config.DIFF_DIR / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({"step": "diff", **summary}, indent=2))
    return summary
