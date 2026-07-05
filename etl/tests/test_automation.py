"""Phase 3 (SPEC §11) automation mechanics: no-op weeks, corrupt-file failure
before any push, terminal write failures surfacing, sitemap validity.

These tests simulate consecutive weekly sync.yml runs over the fixture
universe: build/state/ plays the role of the actions/cache payload carried
between runs.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
import zipfile
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from etl import config, diff, download, parse, sitemaps, verify
from etl.push_firestore import push

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def fake_fetcher(contents: dict[str, bytes]):
    """Stand-in for download._fetch serving canned bytes per URL basename."""

    def _fetch(client, url, dest):
        name = url.rsplit("/", 1)[1]
        dest.write_bytes(contents.get(name, b"layout stub" if name.endswith(".txt") else b"zip"))

    return _fetch


@pytest.fixture()
def canned_downloads(etl_env, monkeypatch):
    contents = {"FLAT_RCL_POST_2010.zip": b"week-one recall bytes"}
    monkeypatch.setattr(download, "_fetch", fake_fetcher(contents))
    # No real HTTP happens, but keep the client from ever trying the network.
    return contents


class TestNoOpWeeks:
    def test_second_week_noops_only_after_push(self, canned_downloads, monkeypatch, tmp_path):
        gh_output = tmp_path / "github_output"
        monkeypatch.setenv("GITHUB_OUTPUT", str(gh_output))

        # Week 1: no previous state -> changed.
        assert download.run()["changed"] is True

        # Same downloads again, but the week-1 pipeline never pushed:
        # still changed, so the failed/incomplete week is re-processed.
        assert download.run()["changed"] is True

        # Push completes (dry-run == --local) -> checksums become the baseline.
        (config.DIFF_DIR / "changed.jsonl").write_text("")
        (config.DIFF_DIR / "deleted.json").write_text("[]")
        push(dry_run=True)
        assert (config.STATE_DIR / "checksums.json").exists()

        # Week 2, unchanged downloads: no-op at step 2.
        assert download.run()["changed"] is False
        # --force overrides for manual dispatch.
        assert download.run(force=True)["changed"] is True

        lines = gh_output.read_text().strip().splitlines()
        assert lines == ["changed=true", "changed=true", "changed=false", "changed=true"]

    def test_new_data_flips_changed(self, canned_downloads):
        download.run()
        (config.DIFF_DIR / "changed.jsonl").write_text("")
        (config.DIFF_DIR / "deleted.json").write_text("[]")
        push(dry_run=True)
        assert download.run()["changed"] is False

        canned_downloads["FLAT_RCL_POST_2010.zip"] = b"week-two recall bytes"
        assert download.run()["changed"] is True


class TestCorruptFileWeek:
    """SPEC §11 Phase 3: a corrupt download must fail before any Firestore
    write, leaving last week's state (and therefore prod) untouched."""

    def test_truncated_zip_fails_parse_and_leaves_state_alone(self, built_pages):
        # Week 1 completed: state carries manifest + baseline + checksums.
        verify.run()
        diff.run()
        (config.RAW_DIR / "checksums.json").write_text('{"FLAT_RCL_POST_2010.zip": "aaaa"}')
        push(dry_run=True)
        state_before = {
            p.name: p.read_bytes() for p in config.STATE_DIR.iterdir() if p.is_file()
        }
        assert set(state_before) == {"manifest.json", "baseline.json", "checksums.json"}

        # Week 2 ships a truncated zip (the sync.yml corrupt-file drill).
        zip_path = config.RAW_DIR / "FLAT_RCL_POST_2010.zip"
        zip_path.write_bytes(zip_path.read_bytes()[:100])
        with pytest.raises(Exception) as excinfo:
            parse.run()
        assert isinstance(excinfo.value, (zipfile.BadZipFile, OSError, ValueError))

        state_after = {
            p.name: p.read_bytes() for p in config.STATE_DIR.iterdir() if p.is_file()
        }
        assert state_after == state_before  # old data keeps serving

    def test_gutted_data_fails_verify_drift_gate(self, built_pages, monkeypatch):
        # Week 1 baseline.
        verify.run()
        diff.run()
        state_manifest_before = (config.STATE_DIR / "manifest.json").read_bytes()

        # Week 2 parses fine but lost most campaigns (silent upstream break):
        # drift gate must fail before diff/push touch anything.
        baseline = json.loads((config.STATE_DIR / "baseline.json").read_text())
        monkeypatch.setattr(config, "MIN_CAMPAIGN_COUNT", 0)
        (config.STATE_DIR / "baseline.json").write_text(
            json.dumps({**baseline, "campaigns": baseline["campaigns"] * 100})
        )
        with pytest.raises(verify.IntegrityError, match="drifted"):
            verify.run()
        assert (config.STATE_DIR / "manifest.json").read_bytes() == state_manifest_before


class FakeDocRef:
    def __init__(self, path: str, store: dict):
        self.path = path
        self._store = store

    def set(self, doc: dict) -> None:
        self._store[self.path] = doc


class FakeBulkWriter:
    def __init__(self, store: dict, fail_paths: set[str] | None = None):
        self._store = store
        self._fail_paths = fail_paths or set()
        self._on_error = None
        self.deletes: list[str] = []

    def on_write_error(self, callback) -> None:
        self._on_error = callback

    def set(self, ref: FakeDocRef, doc: dict) -> None:
        if ref.path in self._fail_paths:
            failure = SimpleNamespace(
                attempts=15,
                operation=SimpleNamespace(reference=ref),
                code=13,
                message="internal",
            )
            assert self._on_error is not None
            assert self._on_error(failure, self) is False
            return
        self._store[ref.path] = doc

    def delete(self, ref: FakeDocRef) -> None:
        self.deletes.append(ref.path)
        self._store.pop(ref.path, None)

    def close(self) -> None:
        pass


class FakeClient:
    def __init__(self, fail_paths: set[str] | None = None):
        self.store: dict[str, dict] = {}
        self.writer = FakeBulkWriter(self.store, fail_paths)

    def collection(self, name: str):
        client = self

        class _Collection:
            def document(self, doc_id: str) -> FakeDocRef:
                return FakeDocRef(f"{name}/{doc_id}", client.store)

        return _Collection()

    def bulk_writer(self) -> FakeBulkWriter:
        return self.writer


class TestPushFailureSurfacing:
    def test_successful_push_writes_meta_and_commits_checksums(self, built_pages):
        verify.run()
        diff.run()
        (config.RAW_DIR / "checksums.json").write_text('{"FLAT_RCL_POST_2010.zip": "aaaa"}')
        client = FakeClient()
        summary = push(client=client)
        assert summary["upserts"] > 0
        assert "meta/sync" in client.store
        assert client.store["meta/sync"]["upserts"] == summary["upserts"]
        assert (config.STATE_DIR / "checksums.json").exists()
        upserted = [p for p in client.store if p.startswith(("pages/", "campaignPages/"))]
        assert len(upserted) == summary["upserts"]

    def test_terminal_write_failure_raises_and_holds_baseline(self, built_pages):
        verify.run()
        diff.run()
        (config.RAW_DIR / "checksums.json").write_text('{"FLAT_RCL_POST_2010.zip": "aaaa"}')
        first_id, item = next(
            iter(json.loads((config.PAGES_DIR / "manifest.json").read_text()).items())
        )
        collection = item.get("collection", config.FIRESTORE_PAGES_COLLECTION)
        client = FakeClient(fail_paths={f"{collection}/{first_id}"})
        with pytest.raises(RuntimeError, match="1 writes failed"):
            push(client=client)
        # A partially-failed week never becomes the no-op baseline.
        assert "meta/sync" not in client.store
        assert not (config.STATE_DIR / "checksums.json").exists()


def spot_check_transport(missing: set[str], components: dict[str, str] | None = None):
    """MockTransport for the campaignNumber endpoint: campno -> canned results."""

    def handler(request: httpx.Request) -> httpx.Response:
        campno = parse_qs(request.url.query.decode())["campaignNumber"][0]
        if campno in missing:
            return httpx.Response(200, json={"Count": 0, "results": []})
        component = (components or {}).get(campno, "FUEL SYSTEM, GASOLINE")
        return httpx.Response(
            200,
            json={
                "Count": 1,
                "results": [
                    {"NHTSACampaignNumber": campno, "Component": component}
                ],
            },
        )

    return handler


@pytest.fixture()
def mock_api(monkeypatch):
    """Route verify's httpx.Client through a MockTransport set by the test."""
    state: dict = {"handler": None}

    real_client = httpx.Client

    def client_factory(**kwargs):
        return real_client(transport=httpx.MockTransport(state["handler"]))

    monkeypatch.setattr(verify.httpx, "Client", client_factory)
    return state


class TestSpotCheck:
    """The live-API gate catches systemic breakage, not per-campaign quirks:
    naming drift and multi-component campaigns must not fail the week."""

    def test_all_present_passes(self, built_pages, mock_api):
        mock_api["handler"] = spot_check_transport(missing=set())
        result = verify.run(spot_check=True)
        assert result["failures"] == []

    def test_single_missing_campaign_is_advisory(self, built_pages, mock_api):
        mock_api["handler"] = spot_check_transport(missing={"23V123000"})
        result = verify.run(spot_check=True)  # 1 of 3 missing: below majority
        assert result["failures"] == []

    def test_component_mismatch_is_advisory(self, built_pages, mock_api):
        mock_api["handler"] = spot_check_transport(
            missing=set(),
            components={
                "23V123000": "STRUCTURE:BODY:DOOR",
                "24V456000": "STRUCTURE:BODY:DOOR",
                "22V789000": "STRUCTURE:BODY:DOOR",
            },
        )
        result = verify.run(spot_check=True)
        assert result["failures"] == []

    def test_majority_missing_fails(self, built_pages, mock_api):
        mock_api["handler"] = spot_check_transport(
            missing={"23V123000", "24V456000", "22V789000"}
        )
        with pytest.raises(verify.IntegrityError, match="not in NHTSA API"):
            verify.run(spot_check=True)

    def test_api_unreachable_is_nonfatal(self, built_pages, mock_api):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down", request=request)

        mock_api["handler"] = handler
        result = verify.run(spot_check=True)
        assert result["failures"] == []


class TestSitemapValidity:
    def test_sitemaps_conform_to_protocol(self, built_pages):
        diff.run()
        sitemaps.run()

        index = ET.parse(config.SITEMAPS_DIR / "sitemap-index.xml").getroot()
        assert index.tag == f"{{{SITEMAP_NS}}}sitemapindex"
        shard_locs = [
            loc.text for loc in index.iter(f"{{{SITEMAP_NS}}}loc") if loc.text
        ]
        assert shard_locs, "sitemap index references no shards"

        indexable = {
            f"{config.SITE_URL}/{e['slug']}/"
            for e in json.loads((config.STATE_DIR / "manifest.json").read_text()).values()
            if e.get("indexable")
        }
        seen: set[str] = set()
        for shard_loc in shard_locs:
            assert shard_loc.startswith(f"{config.SITE_URL}/sitemaps/")
            shard_path = config.SITEMAPS_DIR / shard_loc.rsplit("/", 1)[1]
            assert shard_path.exists(), f"index references missing shard {shard_path.name}"
            root = ET.parse(shard_path).getroot()
            assert root.tag == f"{{{SITEMAP_NS}}}urlset"
            urls = root.findall(f"{{{SITEMAP_NS}}}url")
            assert 0 < len(urls) <= config.SITEMAP_SHARD_SIZE
            for url in urls:
                loc = url.find(f"{{{SITEMAP_NS}}}loc")
                assert loc is not None and loc.text
                assert loc.text.startswith(f"{config.SITE_URL}/")
                seen.add(loc.text)
                lastmod = url.find(f"{{{SITEMAP_NS}}}lastmod")
                if lastmod is not None:
                    assert lastmod.text
                    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", lastmod.text)
        assert seen == indexable  # every indexable URL, nothing more
