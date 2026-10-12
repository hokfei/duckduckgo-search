"""Tests for the DuckDuckGo client.

Parsing is tested against real captured HTML; the network tests are marked and
skipped by default so the suite runs offline.
"""

import json
import time

import pytest

from ddg_search.client import (
    DuckDuckGoClient,
    Result,
    SearchBlocked,
    SearchUnavailable,
    _parse,
    _strip_tags,
    _unwrap_redirect,
    instant,
)

# Trimmed but structurally faithful result page.
RESULT_PAGE = """
<html><body>
<div class="result">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fone&amp;rut=abc">
     First <b>result</b> title
  </a>
  <a class="result__snippet">A snippet with &amp; entities.</a>
</div>
<div class="result">
  <a rel="nofollow" class="result__a"
     href="https://direct.example.org/two">Direct link</a>
  <a class="result__snippet">Second snippet.</a>
</div>
</body></html>
"""

CHALLENGE_PAGE = """
<html><body>
<script src="/js/anomaly.js"></script>
<p>Our systems have detected unusual traffic from your computer network.</p>
</body></html>
"""

EMPTY_PAGE = "<html><body><p>No results found for that query.</p></body></html>"


class TestParsing:
    def test_extracts_title_url_and_snippet(self):
        results = _parse(RESULT_PAGE, limit=10)

        assert len(results) == 2
        assert results[0].title == "First result title"
        assert results[0].url == "https://example.com/one"
        assert results[0].snippet == "A snippet with & entities."

    def test_leaves_direct_links_alone(self):
        results = _parse(RESULT_PAGE, limit=10)

        assert results[1].url == "https://direct.example.org/two"

    def test_respects_limit(self):
        assert len(_parse(RESULT_PAGE, limit=1)) == 1

    def test_challenge_page_yields_no_results(self):
        assert _parse(CHALLENGE_PAGE, limit=10) == []

    def test_empty_page_yields_no_results(self):
        assert _parse(EMPTY_PAGE, limit=10) == []

    def test_strips_tags_and_unescapes_entities(self):
        assert _strip_tags("a <b>bold</b> &amp; plain") == "a bold & plain"

    def test_unwraps_redirect(self):
        wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fx.test%2Fy&rut=z"

        assert _unwrap_redirect(wrapped) == "https://x.test/y"

    def test_unwrap_leaves_plain_url_unchanged(self):
        assert _unwrap_redirect("https://plain.test/a") == "https://plain.test/a"


class TestPacing:
    def test_reports_usable_initially(self):
        client = DuckDuckGoClient()

        assert not client.is_cooling_down
        assert client.cooldown_remaining == 0
        assert client.min_interval == 3.0
        assert "duckduckgo" in client.endpoint

    def test_health_changes_after_a_block(self):
        client = DuckDuckGoClient()
        client._penalise(30)

        assert client.is_cooling_down
        assert client.cooldown_remaining > 25

    def test_block_raises_rather_than_returning_empty(self, monkeypatch):
        """A rate limit must not be mistaken for 'no results'."""
        client = DuckDuckGoClient()
        client._penalise(30)

        with pytest.raises(SearchBlocked) as caught:
            client.search("anything")

        assert "Rate limited" in str(caught.value)

    def test_empty_query_is_rejected(self):
        client = DuckDuckGoClient()

        with pytest.raises(SearchUnavailable):
            client.search("   ")

    def test_spaces_requests(self, monkeypatch):
        """Two calls in a row must be at least min_interval apart."""
        client = DuckDuckGoClient(min_interval=0.5)
        stamps: list[float] = []

        def fake_open(request, timeout=0):
            stamps.append(time.monotonic())

            class Response:
                status = 200

                def read(self):
                    return RESULT_PAGE.encode()

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            return Response()

        monkeypatch.setattr("urllib.request.urlopen", fake_open)

        client.search("first")
        client.search("second")

        assert stamps[1] - stamps[0] >= 0.5


class TestBlocking:
    def test_challenge_body_raises_blocked(self, monkeypatch):
        client = DuckDuckGoClient()

        def fake_open(request, timeout=0):
            class Response:
                status = 200

                def read(self):
                    return CHALLENGE_PAGE.encode()

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            return Response()

        monkeypatch.setattr("urllib.request.urlopen", fake_open)

        with pytest.raises(SearchBlocked):
            client.search("test")

        assert client.is_cooling_down

    def test_202_status_raises_blocked(self, monkeypatch):
        client = DuckDuckGoClient()

        def fake_open(request, timeout=0):
            class Response:
                status = 202

                def read(self):
                    return b""

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            return Response()

        monkeypatch.setattr("urllib.request.urlopen", fake_open)

        with pytest.raises(SearchBlocked):
            client.search("test")


class TestInstantAnswers:
    def test_flattens_nested_topics(self):
        from ddg_search.client import _flatten_topics

        collected: list[tuple[str, str]] = []
        _flatten_topics(
            [
                {"Text": "A", "FirstURL": "https://a.test"},
                {"Topics": [{"Text": "B", "FirstURL": "https://b.test"}]},
                {"Topics": [{"Text": "C", "FirstURL": "https://c.test"}]},
            ],
            collected,
        )

        assert collected == [
            ("A", "https://a.test"),
            ("B", "https://b.test"),
            ("C", "https://c.test"),
        ]

    def test_flattens_empty_topics(self):
        from ddg_search.client import _flatten_topics

        collected: list[tuple[str, str]] = []
        _flatten_topics(None, collected)

        assert collected == []

    def test_reads_a_real_instant_answer(self, monkeypatch):
        payload = {
            "Heading": "SQLite",
            "AbstractText": "SQLite is an embedded database engine.",
            "AbstractSource": "Wikipedia",
            "AbstractURL": "https://en.wikipedia.org/wiki/SQLite",
            "RelatedTopics": [{"Text": "SQL", "FirstURL": "https://sql.test"}],
        }

        def fake_open(request, timeout=0):
            class Response:
                status = 200

                def read(self):
                    return json.dumps(payload).encode()

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            return Response()

        monkeypatch.setattr("urllib.request.urlopen", fake_open)

        answer = instant("sqlite")

        assert answer.heading == "SQLite"
        assert answer.has_answer
        assert answer.abstract_source == "Wikipedia"
        assert answer.related == (("SQL", "https://sql.test"),)

    def test_missing_entry_reports_no_answer(self, monkeypatch):
        def fake_open(request, timeout=0):
            class Response:
                status = 200

                def read(self):
                    return json.dumps({"Heading": "", "AbstractText": ""}).encode()

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            return Response()

        monkeypatch.setattr("urllib.request.urlopen", fake_open)

        assert not instant("zzzz nonsense").has_answer


@pytest.mark.integration
class TestLiveSearch:
    """Hits the real endpoint. Skipped unless explicitly enabled."""

    def test_returns_results(self):
        client = DuckDuckGoClient()

        results = client.search("sqlite vec0 extension windows", limit=5)

        assert results, "expected live results"
        assert all(isinstance(r, Result) for r in results)
        assert all(r.url.startswith("http") for r in results)
