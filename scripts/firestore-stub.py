#!/usr/bin/env python3
"""Local Firestore REST stub for Phase 2-style verification without GCP.

Serves the ETL's page docs (etl/build/pages/pages.jsonl) over the tiny slice
of the Firestore REST surface the site uses (single-document GET). Point the
site at it via the emulator env var:

    python3 scripts/firestore-stub.py --port 8787 &
    cd site && GCP_PROJECT=local FIRESTORE_EMULATOR_HOST=localhost:8787 \
        node dist/server/entry.mjs
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PAGES_JSONL = REPO_ROOT / "etl" / "build" / "pages" / "pages.jsonl"

_PATH_RE = re.compile(
    r"^/v1/projects/[^/]+/databases/\(default\)/documents/([^/]+)/([^/?]+)"
)


def encode_value(v):
    if v is None:
        return {"nullValue": None}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, list):
        return {"arrayValue": {"values": [encode_value(x) for x in v]}}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {k: encode_value(x) for k, x in v.items()}}}
    raise TypeError(f"unsupported type {type(v)}")


def load_docs(pages_jsonl: Path = PAGES_JSONL) -> dict[tuple[str, str], dict]:
    docs: dict[tuple[str, str], dict] = {}
    with pages_jsonl.open(encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            docs[(item["collection"], item["id"])] = item["doc"]
    return docs


class Handler(BaseHTTPRequestHandler):
    docs: dict[tuple[str, str], dict] = {}

    def do_GET(self):  # noqa: N802
        m = _PATH_RE.match(self.path)
        doc = None
        if m:
            from urllib.parse import unquote

            doc = self.docs.get((m.group(1), unquote(m.group(2))))
        if doc is None:
            body = json.dumps({"error": {"code": 404, "status": "NOT_FOUND"}}).encode()
            self.send_response(404)
        else:
            body = json.dumps(
                {"name": self.path, "fields": encode_value(doc)["mapValue"]["fields"]}
            ).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # quiet
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument(
        "--pages",
        type=Path,
        default=PAGES_JSONL,
        help="pages.jsonl to serve (default: etl/build/pages/pages.jsonl)",
    )
    args = parser.parse_args()
    Handler.docs = load_docs(args.pages)
    print(f"firestore-stub: {len(Handler.docs)} docs on http://localhost:{args.port}")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
