"""`etl` command-line entry point.

    etl download [--force]        fetch flat files, detect no-op weeks
    etl parse                     decode + parse to parquet
    etl normalize                 hygiene, PII scrub, quarantine
    etl build-sqlite              relational build.db
    etl build-pages               page docs + manifest + search index
    etl verify [--spot-check]     integrity assertions (SPEC §10)
    etl diff [--full]             changed/deleted docs vs previous run
    etl push-firestore [--dry-run]
    etl sitemaps                  sharded sitemaps + robots.txt
    etl vpic-refresh              refresh vendored make canon
    etl smoke [N]                 sample live URLs post-deploy
    etl all [--local] [--force]   download..sitemaps; --local skips the push
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="etl", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("download")
    p.add_argument("--force", action="store_true")
    sub.add_parser("parse")
    sub.add_parser("normalize")
    sub.add_parser("build-sqlite")
    sub.add_parser("build-pages")
    p = sub.add_parser("verify")
    p.add_argument("--spot-check", action="store_true")
    p = sub.add_parser("diff")
    p.add_argument("--full", action="store_true")
    p = sub.add_parser("push-firestore")
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("sitemaps")
    sub.add_parser("vpic-refresh")
    p = sub.add_parser("smoke")
    p.add_argument("n", nargs="?", type=int, default=20)
    p = sub.add_parser("all")
    p.add_argument("--local", action="store_true", help="skip the Firestore push")
    p.add_argument("--force", action="store_true", help="run even if downloads unchanged")
    p.add_argument("--spot-check", action="store_true")

    args = parser.parse_args(argv)

    # Imports are lazy so `etl download` doesn't pay for firestore imports etc.
    if args.cmd == "download":
        from etl import download

        download.run(force=args.force)
    elif args.cmd == "parse":
        from etl import parse

        parse.run()
    elif args.cmd == "normalize":
        from etl import normalize

        normalize.run()
    elif args.cmd == "build-sqlite":
        from etl import build_sqlite

        build_sqlite.run()
    elif args.cmd == "build-pages":
        from etl import build_pages

        build_pages.run()
    elif args.cmd == "verify":
        from etl import verify

        verify.run(spot_check=args.spot_check)
    elif args.cmd == "diff":
        from etl import diff

        diff.run(force_full=args.full)
    elif args.cmd == "push-firestore":
        from etl import push_firestore

        push_firestore.push(dry_run=args.dry_run)
    elif args.cmd == "sitemaps":
        from etl import sitemaps

        sitemaps.run()
    elif args.cmd == "vpic-refresh":
        from etl import vpic

        vpic.run()
    elif args.cmd == "smoke":
        from etl import smoke

        return smoke.main([str(args.n)])
    elif args.cmd == "all":
        from etl import (
            build_pages,
            build_sqlite,
            diff,
            download,
            normalize,
            parse,
            push_firestore,
            sitemaps,
            verify,
        )

        result = download.run(force=args.force)
        if not result["changed"]:
            print("etl all: downloads unchanged — no-op week, exiting")
            return 0
        parse.run()
        normalize.run()
        build_sqlite.run()
        build_pages.run()
        verify.run(spot_check=args.spot_check)
        diff.run()
        push_firestore.push(dry_run=args.local)
        sitemaps.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
