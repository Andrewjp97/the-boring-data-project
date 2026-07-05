"""End-to-end pipeline tests over the fixture universe (see conftest.py)."""

import json
import sqlite3

import pytest

from etl import config, diff, sitemaps, verify
from etl.build_pages import doc_id_for_slug, enforce_size, truncate
from etl.push_firestore import push


def _load_docs() -> dict[str, dict]:
    docs = {}
    with (config.PAGES_DIR / "pages.jsonl").open(encoding="utf-8") as f:
        for line in f:
            item = json.loads(line)
            docs[item["id"]] = item
    return docs


class TestBuildSqlite:
    def test_campaign_dedupe(self, built_pages):
        conn = sqlite3.connect(config.SQLITE_PATH)
        try:
            # 23V123000 spans 3 model years but is stored once (SPEC §2.4)
            n = conn.execute(
                "SELECT COUNT(*) FROM campaigns WHERE campno='23V123000'"
            ).fetchone()[0]
            assert n == 1
            fanout = conn.execute(
                "SELECT COUNT(*) FROM campaign_vehicles WHERE campno='23V123000'"
            ).fetchone()[0]
            assert fanout == 3
            # equipment recall and quarantined make never made it in
            for gone in ("23E001000", "20V222000"):
                n = conn.execute(
                    "SELECT COUNT(*) FROM campaigns WHERE campno=?", (gone,)
                ).fetchone()[0]
                assert n == 0
        finally:
            conn.close()


class TestBuildPages:
    def test_year_page_shape(self, built_pages):
        docs = _load_docs()
        item = docs[doc_id_for_slug("recalls/honda/cr-v/2016")]
        assert item["collection"] == "pages"
        doc = item["doc"]
        assert doc["kind"] == "year"
        assert doc["make"] == {"slug": "honda", "display": "Honda"}
        assert doc["model"] == {"slug": "cr-v", "display": "CR-V"}
        assert doc["year"] == 2016
        assert doc["recallCount"] == 2
        assert doc["totalAffected"] == 412000 + 5000
        # newest first
        assert [r["campno"] for r in doc["recalls"]] == ["24V456000", "23V123000"]
        assert doc["indexable"] is True
        assert doc["complaintTotal"] == 2
        stats = {s["component"]: s for s in doc["complaintStats"]}
        assert stats["ELECTRICAL SYSTEM"]["crashes"] == 1
        assert stats["ELECTRICAL SYSTEM"]["injuries"] == 2
        assert 1 <= len(doc["complaintSamples"]) <= config.COMPLAINT_SAMPLE_MAX
        for s in doc["complaintSamples"]:
            assert len(s["narrative"]) <= config.COMPLAINT_SAMPLE_CHARS + 1
            assert "1HGRM4870GL123456" not in s["narrative"].upper()
        assert doc["investigations"][0]["actionNumber"] == "PE23012"
        assert doc["nav"]["years"] == [2015, 2017]

    def test_indexable_rules(self, built_pages):
        docs = _load_docs()
        # zero recalls + 30 complaints -> indexable
        mazda = docs[doc_id_for_slug("recalls/mazda/cx-30/2021")]["doc"]
        assert mazda["recallCount"] == 0
        assert mazda["complaintTotal"] == 30
        assert mazda["indexable"] is True
        # zero recalls + 3 complaints -> noindex
        kia = docs[doc_id_for_slug("recalls/kia/soul/2022")]["doc"]
        assert kia["indexable"] is False

    def test_unknown_year_recall_surfaces_on_model_page(self, built_pages):
        docs = _load_docs()
        model = docs[doc_id_for_slug("recalls/mercedes-benz/c-class")]["doc"]
        assert [r["campno"] for r in model["unknownYearRecalls"]] == ["21V111000"]
        # ...and no year page exists for it
        assert not any(
            i.startswith(doc_id_for_slug("recalls/mercedes-benz/c-class/"))
            for i in docs
        )

    def test_campaign_page_full_text(self, built_pages):
        docs = _load_docs()
        item = docs[doc_id_for_slug("recall/23V123000")]
        assert item["collection"] == "campaignPages"
        doc = item["doc"]
        assert doc["kind"] == "campaign"
        assert doc["defect"] == "FUEL PUMP MAY FAIL CAUSING ENGINE STALL"
        years = sorted(v["year"] for v in doc["affectedVehicles"])
        assert years == [2015, 2016, 2017]

    def test_make_page_and_alias_flow(self, built_pages):
        docs = _load_docs()
        chevy = docs[doc_id_for_slug("recalls/chevrolet")]["doc"]
        assert chevy["make"]["display"] == "Chevrolet"
        assert chevy["models"][0]["slug"] == "silverado-1500"

    def test_search_index(self, built_pages):
        idx = json.loads(config.SEARCH_INDEX_PATH.read_text())
        entries = {e["slug"]: e for e in idx}
        assert entries["recalls/honda/cr-v"]["years"] == [2015, 2017]
        assert entries["recalls/chevrolet/silverado-1500"]["make"] == "Chevrolet"

    def test_truncate(self):
        assert truncate(None, 10) is None
        assert truncate("short", 10) == "short"
        out = truncate("word " * 100, config.TRUNCATE_TEXT_CHARS)
        assert len(out) <= config.TRUNCATE_TEXT_CHARS
        assert out.endswith("…")

    def test_enforce_size_truncates_samples_then_notes(self):
        big = "x" * 200_000
        doc = {
            "kind": "year",
            "recalls": [{"campno": "1", "notes": big}],
            "complaintSamples": [{"narrative": big} for _ in range(5)],
        }
        out = enforce_size(dict(doc), "test-doc")
        assert len(json.dumps(out).encode()) < config.MAX_DOC_BYTES
        assert len(out["complaintSamples"]) < 5

    def test_all_docs_under_size_limit(self, built_pages):
        for item in _load_docs().values():
            assert (
                len(json.dumps(item["doc"], ensure_ascii=False).encode("utf-8"))
                < config.MAX_DOC_BYTES
            )


class TestVerify:
    def test_passes_on_fixture_build(self, built_pages):
        result = verify.run()
        assert result["failures"] == []
        assert (config.STATE_DIR / "baseline.json").exists()

    def test_fails_on_campaign_drift(self, built_pages):
        (config.STATE_DIR / "baseline.json").write_text(
            json.dumps({"campaigns": 1000, "totalDocs": 1000})
        )
        with pytest.raises(verify.IntegrityError, match="drifted"):
            verify.run()

    def test_fails_on_empty_texts(self, built_pages):
        conn = sqlite3.connect(config.SQLITE_PATH)
        conn.execute("UPDATE campaigns SET defect=NULL, action='' WHERE campno='23V123000'")
        conn.commit()
        conn.close()
        with pytest.raises(verify.IntegrityError, match="empty defect"):
            verify.run()


class TestDiff:
    def test_first_run_all_changed_then_noop(self, built_pages):
        s1 = diff.run()
        assert s1["changed"] == s1["total"] > 0
        assert s1["deleted"] == 0
        s2 = diff.run()
        assert s2["changed"] == 0
        assert s2["deleted"] == 0
        assert s2["slugSetChanged"] is False

    def test_deletion_detected(self, built_pages):
        diff.run()
        manifest = json.loads((config.PAGES_DIR / "manifest.json").read_text())
        gone_id = doc_id_for_slug("recalls/kia/soul/2022")
        manifest.pop(gone_id)
        (config.PAGES_DIR / "manifest.json").write_text(json.dumps(manifest))
        s = diff.run()
        assert {"id": gone_id, "collection": "pages"} in json.loads(
            (config.DIFF_DIR / "deleted.json").read_text()
        )
        assert s["deleted"] == 1

    def test_lastmod_carried_forward(self, built_pages):
        diff.run()
        state = json.loads((config.STATE_DIR / "manifest.json").read_text())
        for entry in state.values():
            entry["lastmod"] = "2020-01-01"
        (config.STATE_DIR / "manifest.json").write_text(json.dumps(state))
        diff.run()  # nothing changed -> lastmod preserved
        state2 = json.loads((config.STATE_DIR / "manifest.json").read_text())
        assert all(e["lastmod"] == "2020-01-01" for e in state2.values())


class TestPushFirestore:
    def test_dry_run_counts(self, built_pages):
        diff.run()
        summary = push(dry_run=True)
        assert summary["dryRun"] is True
        assert summary["upserts"] > 0


class TestSitemaps:
    def test_only_indexable_urls(self, built_pages):
        diff.run()
        sitemaps.run()
        shard = (config.SITEMAPS_DIR / "sitemap-000.xml").read_text()
        assert f"{config.SITE_URL}/recalls/honda/cr-v/2016/" in shard
        assert "recalls/kia/soul/2022" not in shard  # noindex page
        assert "<lastmod>" in shard
        index = (config.SITEMAPS_DIR / "sitemap-index.xml").read_text()
        assert "sitemap-000.xml" in index
        robots = (config.SITE_PUBLIC_DIR / "robots.txt").read_text()
        assert "sitemap-index.xml" in robots
