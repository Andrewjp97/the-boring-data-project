"""Push changed page docs to Firestore via Admin SDK BulkWriter (SPEC §10.8).

Only touches docs listed by diff.py. Credentials come from ADC (Workload
Identity Federation in CI); no key files, ever. Finishes by updating the
meta/sync doc, which is what download.py's checksums are compared against
conceptually — the local state dir is the operational copy.
"""

from __future__ import annotations

import datetime
import json
from typing import Any

from etl import config, download

# BulkWriter's default linear-backoff retry budget; a doc that still fails
# after this many attempts is a terminal failure and must fail the run.
MAX_WRITE_ATTEMPTS = 15


def _load_changed() -> list[dict]:
    path = config.DIFF_DIR / "changed.jsonl"
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _load_deleted() -> list[dict]:
    return json.loads((config.DIFF_DIR / "deleted.json").read_text())


def push(client: Any | None = None, dry_run: bool = False) -> dict:
    changed = _load_changed()
    deleted = _load_deleted()

    if dry_run:
        # --local/--dry-run still advances the no-op baseline so consecutive
        # local runs behave like consecutive CI weeks.
        download.commit_state_checksums()
        summary = {"upserts": len(changed), "deletes": len(deleted), "dryRun": True}
        print(json.dumps({"step": "push-firestore", **summary}, indent=2))
        return summary

    if client is None:
        from google.cloud import firestore  # local import: not needed for --dry-run

        client = firestore.Client()

    from google.cloud.firestore_v1 import SERVER_TIMESTAMP

    # BulkWriter's default handler retries then silently logs terminal
    # failures; a partially-pushed week must fail the run instead, so the
    # failure issue opens and next week re-processes from scratch.
    terminal_failures: list[str] = []

    def _on_write_error(error: Any, writer: Any) -> bool:
        if error.attempts < MAX_WRITE_ATTEMPTS:
            return True
        ref = getattr(error.operation, "reference", None)
        path = getattr(ref, "path", None) or getattr(ref, "_document_path", "<unknown>")
        terminal_failures.append(f"{path}: {error.message} (code {error.code})")
        return False

    writer = client.bulk_writer()
    writer.on_write_error(_on_write_error)
    for item in changed:
        doc = dict(item["doc"])
        doc["updatedAt"] = SERVER_TIMESTAMP
        ref = client.collection(item["collection"]).document(item["id"])
        writer.set(ref, doc)
    for item in deleted:
        writer.delete(client.collection(item["collection"]).document(item["id"]))
    writer.close()  # flushes; BulkWriter batches ~500 and retries with backoff
    if terminal_failures:
        preview = "; ".join(terminal_failures[:5])
        raise RuntimeError(
            f"push-firestore: {len(terminal_failures)} writes failed after "
            f"{MAX_WRITE_ATTEMPTS} attempts: {preview}"
        )

    # meta/sync: single doc read by ops/debugging, not by page renders.
    checksums_path = config.RAW_DIR / "checksums.json"
    pages_summary_path = config.PAGES_DIR / "summary.json"
    norm_summary_path = config.NORMALIZED_DIR / "summary.json"
    meta = {
        "lastSync": datetime.datetime.now(datetime.UTC).isoformat(),
        "checksums": json.loads(checksums_path.read_text()) if checksums_path.exists() else {},
        "counts": json.loads(pages_summary_path.read_text()) if pages_summary_path.exists() else {},
        "quarantineCount": (
            json.loads(norm_summary_path.read_text()).get("quarantined", 0)
            if norm_summary_path.exists()
            else 0
        ),
        "upserts": len(changed),
        "deletes": len(deleted),
    }
    client.collection(config.FIRESTORE_META_COLLECTION).document("sync").set(meta)

    # Docs are durably pushed: this week's downloads become the no-op baseline.
    download.commit_state_checksums()

    summary = {"upserts": len(changed), "deletes": len(deleted), "dryRun": False}
    print(json.dumps({"step": "push-firestore", **summary}, indent=2))
    return summary
