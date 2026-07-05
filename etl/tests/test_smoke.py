"""check_url is the only assertion logic in the post-deploy smoke job —
pin down what it accepts and what it flags."""

from __future__ import annotations

from etl.smoke import check_url

GOOD_PAGE = """<!doctype html><html><head>
<script async src="https://www.googletagmanager.com/gtag/js?id=G-X"></script>
<script type="application/ld+json">{"@type":"BreadcrumbList"}</script>
</head><body><article class="recall-card">Recall 16V061000</article></body></html>"""


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


class FakeClient:
    def __init__(self, response: FakeResponse):
        self._response = response

    def get(self, url: str) -> FakeResponse:
        return self._response


def problems_for(html: str, status: int = 200) -> list[str]:
    return check_url(FakeClient(FakeResponse(html, status)), "https://x.test/p/")


def test_good_page_passes() -> None:
    assert problems_for(GOOD_PAGE) == []


def test_empty_state_is_valid_content() -> None:
    html = GOOD_PAGE.replace(
        '<article class="recall-card">Recall 16V061000</article>',
        "<p>No NHTSA recalls on record for the 2021 Mazda CX-30.</p>",
    )
    assert problems_for(html) == []


def test_non_200_flagged() -> None:
    assert problems_for(GOOD_PAGE, status=500) == ["https://x.test/p/: HTTP 500"]


def test_missing_gtag_flagged_when_ga_configured(monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_GA_ID", "G-TEST")
    html = GOOD_PAGE.replace("googletagmanager.com/gtag", "example.com")
    assert any("gtag" in p for p in problems_for(html))


def test_missing_gtag_tolerated_without_ga(monkeypatch) -> None:
    # Pre-Phase-4 deploys have no GA4 configured; gtag is only required
    # when PUBLIC_GA_ID is set.
    monkeypatch.delenv("PUBLIC_GA_ID", raising=False)
    html = GOOD_PAGE.replace("googletagmanager.com/gtag", "example.com")
    assert problems_for(html) == []


def test_broken_jsonld_flagged() -> None:
    html = GOOD_PAGE.replace('{"@type":"BreadcrumbList"}', "{not json")
    assert any("JSON-LD" in p for p in problems_for(html))


def test_noindex_on_sitemap_url_flagged() -> None:
    # Sampled URLs are drawn from the indexable set; noindex means the
    # indexable flag and the rendered template disagree.
    html = GOOD_PAGE.replace(
        "<head>", '<head><meta name="robots" content="noindex,follow">'
    )
    assert any("noindex" in p for p in problems_for(html))
