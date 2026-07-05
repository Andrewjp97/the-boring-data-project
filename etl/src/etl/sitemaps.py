"""Sharded sitemaps from the diffed manifest (SPEC §5.2).

Only indexable URLs, <lastmod> from the per-doc lastmod stamped by diff.py,
45k URLs per shard, plus sitemap-index.xml. Also (re)writes robots.txt so the
sitemap reference always carries the configured SITE_URL.
"""

from __future__ import annotations

import json
from xml.sax.saxutils import escape

from etl import config


def _url(slug: str) -> str:
    return f"{config.SITE_URL}/{slug}/"


def run() -> dict:
    manifest = json.loads((config.STATE_DIR / "manifest.json").read_text())
    entries = sorted(
        (e["slug"], e.get("lastmod"))
        for e in manifest.values()
        if e.get("indexable", False)
    )

    config.SITEMAPS_DIR.mkdir(parents=True, exist_ok=True)
    for old in config.SITEMAPS_DIR.glob("sitemap*.xml"):
        old.unlink()

    shards: list[str] = []
    for i in range(0, len(entries), config.SITEMAP_SHARD_SIZE):
        shard_name = f"sitemap-{i // config.SITEMAP_SHARD_SIZE:03d}.xml"
        shards.append(shard_name)
        lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        lines.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
        for slug, lastmod in entries[i : i + config.SITEMAP_SHARD_SIZE]:
            lines.append("<url>")
            lines.append(f"<loc>{escape(_url(slug))}</loc>")
            if lastmod:
                lines.append(f"<lastmod>{lastmod}</lastmod>")
            lines.append("</url>")
        lines.append("</urlset>")
        (config.SITEMAPS_DIR / shard_name).write_text("\n".join(lines), encoding="utf-8")

    index_lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    index_lines.append('<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for shard in shards:
        index_lines.append(
            f"<sitemap><loc>{escape(config.SITE_URL)}/sitemaps/{shard}</loc></sitemap>"
        )
    index_lines.append("</sitemapindex>")
    (config.SITEMAPS_DIR / "sitemap-index.xml").write_text(
        "\n".join(index_lines), encoding="utf-8"
    )

    robots = "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            "Disallow: /vin/",  # decode-and-redirect utility page, no SEO value
            "",
            f"Sitemap: {config.SITE_URL}/sitemaps/sitemap-index.xml",
            "",
        ]
    )
    (config.SITE_PUBLIC_DIR / "robots.txt").write_text(robots, encoding="utf-8")

    summary = {"urls": len(entries), "shards": len(shards)}
    print(json.dumps({"step": "sitemaps", **summary}, indent=2))
    return summary
