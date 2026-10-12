"""DuckDuckGo HTML search, as a dependency-free Python client.

Uses the no-JavaScript endpoint rather than a browser or a paid API. DuckDuckGo
allows this, but it is rate limited: bursts get an HTTP 202 anomaly response
instead of results. That is handled here rather than reported as "no results",
because the two are very different things to a caller.
"""

import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

ENDPOINT = "https://html.duckduckgo.com/html/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)

# DuckDuckGo starts returning HTTP 202 (anomaly/challenge) under burst load.
# One request every few seconds stays well inside it; this was measured, not
# assumed - three queries one second apart triggered a block on the third.
MIN_INTERVAL_SECONDS = 3.0
RETRY_AFTER_BLOCK_SECONDS = 30.0

_RESULT_LINK = re.compile(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_RESULT_SNIPPET = re.compile(r'class="result__snippet"[^>]*>(.*?)</a>', re.S)
_TAGS = re.compile(r"<[^>]+>")


class SearchBlocked(Exception):
    """DuckDuckGo is rate limiting us; the query should be retried later."""


class SearchUnavailable(Exception):
    """The endpoint could not be reached."""


@dataclass(frozen=True, slots=True)
class Result:
    """One search result."""

    title: str
    url: str
    snippet: str


def _strip_tags(markup: str) -> str:
    return html.unescape(_TAGS.sub("", markup)).strip()


def _unwrap_redirect(href: str) -> str:
    """Recover the real destination from DuckDuckGo's /l/?uddg= wrapper."""
    if "uddg=" in href:
        query = urllib.parse.urlparse(href).query
        target = urllib.parse.parse_qs(query).get("uddg")
        if target:
            return target[0]
    return href


class DuckDuckGoClient:
    """Searches DuckDuckGo, pacing itself to avoid the rate limiter.

    Requests are serialised and spaced by `min_interval` because the pacing is
    what keeps this usable; a burst gets blocked rather than throttled.
    """

    def __init__(
        self,
        min_interval: float = MIN_INTERVAL_SECONDS,
        timeout: float = 30.0,
    ) -> None:
        self._min_interval = min_interval
        self._timeout = timeout
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._cooldown_until = 0.0

    def _wait_turn(self) -> None:
        """Block until this client is allowed to issue its next request."""
        now = time.monotonic()

        cooldown = self._cooldown_until - now
        if cooldown > 0:
            raise SearchBlocked(f"Rate limited by DuckDuckGo; retry in {cooldown:.0f}s.")

        since_last = now - self._last_request
        if since_last < self._min_interval:
            time.sleep(self._min_interval - since_last)

        self._last_request = time.monotonic()

    def _penalise(self, seconds: float) -> None:
        """Back off after a block so the next call reports the wait clearly."""
        self._cooldown_until = time.monotonic() + seconds

    @property
    def min_interval(self) -> float:
        """Minimum seconds between requests."""
        return self._min_interval

    @property
    def endpoint(self) -> str:
        """The endpoint being queried."""
        return ENDPOINT

    @property
    def cooldown_remaining(self) -> float:
        """Seconds left on the current backoff, or 0 if usable now."""
        return max(0.0, self._cooldown_until - time.monotonic())

    @property
    def is_cooling_down(self) -> bool:
        """Whether a recent rate-limit backoff is still active."""
        return self.cooldown_remaining > 0

    def search(self, query: str, limit: int = 10) -> list[Result]:
        """Run a search and return results.

        Raises SearchBlocked when rate limited, so the caller can tell that
        apart from a query that genuinely matched nothing.
        """
        if not query.strip():
            raise SearchUnavailable("Query must not be empty.")

        with self._lock:
            self._wait_turn()

            url = f"{ENDPOINT}?q={urllib.parse.quote(query)}"
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml",
                },
            )

            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    status = response.status
                    body = response.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as e:
                if e.code in (202, 429):
                    self._penalise(RETRY_AFTER_BLOCK_SECONDS)
                    raise SearchBlocked(
                        f"DuckDuckGo returned HTTP {e.code} (rate limited). "
                        f"Retry in about {RETRY_AFTER_BLOCK_SECONDS:.0f}s."
                    ) from e
                raise SearchUnavailable(f"HTTP {e.code} from DuckDuckGo.") from e
            except urllib.error.URLError as e:
                raise SearchUnavailable(f"Could not reach DuckDuckGo: {e.reason}") from e

        # A 202 with a body is their challenge page, not an empty result set.
        if status == 202 or "anomaly" in body.lower():
            self._penalise(RETRY_AFTER_BLOCK_SECONDS)
            raise SearchBlocked(
                "DuckDuckGo returned an anomaly page (rate limited). "
                f"Retry in about {RETRY_AFTER_BLOCK_SECONDS:.0f}s."
            )

        return _parse(body, limit)


def _parse(body: str, limit: int) -> list[Result]:
    """Extract result rows from the HTML response."""
    links = _RESULT_LINK.findall(body)
    snippets = [_strip_tags(m) for m in _RESULT_SNIPPET.findall(body)]

    results: list[Result] = []
    for index, (href, title) in enumerate(links):
        text = _strip_tags(title)
        if not text:
            continue
        results.append(
            Result(
                title=text,
                url=_unwrap_redirect(html.unescape(href)),
                snippet=snippets[index] if index < len(snippets) else "",
            )
        )
        if len(results) >= limit:
            break

    return results


@dataclass(frozen=True, slots=True)
class InstantAnswer:
    """An encyclopedic answer about a named thing."""

    heading: str
    abstract: str
    abstract_source: str
    abstract_url: str
    related: tuple[tuple[str, str], ...]

    @property
    def has_answer(self) -> bool:
        return bool(self.abstract)


def _flatten_topics(topics: list, out: list[tuple[str, str]]) -> None:
    """Walk DuckDuckGo's nested RelatedTopics into (text, url) pairs."""
    for topic in topics or []:
        if "Topics" in topic:
            _flatten_topics(topic["Topics"], out)
        elif topic.get("Text"):
            out.append((str(topic["Text"]), str(topic.get("FirstURL", ""))))


def instant(query: str, timeout: float = 20.0) -> InstantAnswer:
    """Fetch an encyclopedic answer from DuckDuckGo's instant-answer API.

    This is a different surface from the HTML search and is far less restricted:
    it kept answering while the HTML endpoint was returning block pages. The
    trade-off is that it only knows about named entities - libraries, tools,
    concepts - and cannot find arbitrary web pages.
    """
    url = (
        "https://api.duckduckgo.com/?q="
        f"{urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1"
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        raise SearchUnavailable(f"HTTP {e.code} from the instant-answer API.") from e
    except urllib.error.URLError as e:
        raise SearchUnavailable(f"Could not reach DuckDuckGo: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise SearchUnavailable("Instant-answer API returned malformed JSON.") from e

    related: list[tuple[str, str]] = []
    _flatten_topics(payload.get("RelatedTopics", []), related)

    return InstantAnswer(
        heading=str(payload.get("Heading", query)),
        abstract=str(payload.get("AbstractText", "") or ""),
        abstract_source=str(payload.get("AbstractSource", "") or ""),
        abstract_url=str(payload.get("AbstractURL", "") or ""),
        related=tuple(related),
    )
