"""
Pluggable web-search backends for company research.

DuckDuckGo's HTML endpoint is the default because it needs no signup or API
key. It is also the most brittle option (markup changes, rate limiting), so
Brave / Tavily / Serper are available as drop-in alternatives once an API key
is set in .env (SEARCH_PROVIDER + <PROVIDER>_API_KEY).
"""

import os
import sys
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/91.0.4472.124 Safari/537.36"
)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str

    def to_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


class SearchProvider(ABC):
    name: str = "base"

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        ...


class DuckDuckGoProvider(SearchProvider):
    """Keyless HTML-scraping backend. No signup required; rate-limits easily."""

    name = "duckduckgo"
    ENDPOINT = "https://html.duckduckgo.com/html/"

    def __init__(self, timeout: int = 15, min_interval: float = 2.0):
        self.timeout = timeout
        self.min_interval = min_interval
        self._last_request = 0.0
        self._cache: dict = {}
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _BROWSER_UA})

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.monotonic()

    @staticmethod
    def _unwrap_redirect(href: str) -> Optional[str]:
        if href.startswith("//duckduckgo.com/l/") or href.startswith("/l/"):
            qs = parse_qs(urlparse(href).query)
            target = qs.get("uddg", [None])[0]
            return unquote(target) if target else None
        return href

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        cache_key = query.strip().lower()
        if cache_key in self._cache:
            return self._cache[cache_key][:max_results]

        self._wait()
        try:
            resp = self.session.post(self.ENDPOINT, data={"q": query}, timeout=self.timeout)
            resp.raise_for_status()
        except requests.exceptions.RequestException as exc:
            logger.warning(f"DuckDuckGo search failed for '{query}': {exc}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        results: List[SearchResult] = []
        for div in soup.select("div.result"):
            link = div.select_one("a.result__a")
            if not link:
                continue
            href = link.get("href", "")
            url = self._unwrap_redirect(href)
            if not url or not url.startswith(("http://", "https://")):
                continue
            if urlparse(url).netloc.endswith("duckduckgo.com"):
                continue
            snippet_el = div.select_one("a.result__snippet") or div.select_one(".result__snippet")
            snippet = " ".join(snippet_el.stripped_strings) if snippet_el else ""
            title = " ".join(link.stripped_strings)
            results.append(SearchResult(title=title, url=url, snippet=snippet))

        if not results:
            logger.warning(
                f"DuckDuckGo returned no parsable results for '{query}' "
                "(possible markup change or rate limiting)."
            )

        self._cache[cache_key] = results
        return results[:max_results]


class BraveProvider(SearchProvider):
    name = "brave"
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, timeout: int = 15):
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        try:
            resp = requests.get(
                self.ENDPOINT,
                params={"q": query, "count": max_results},
                headers={"Accept": "application/json", "X-Subscription-Token": self.api_key},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning(f"Brave search failed for '{query}': {exc}")
            return []

        results = []
        for item in data.get("web", {}).get("results", [])[:max_results]:
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("description", ""),
            ))
        return results


class TavilyProvider(SearchProvider):
    name = "tavily"
    ENDPOINT = "https://api.tavily.com/search"

    def __init__(self, api_key: str, timeout: int = 15):
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        try:
            resp = requests.post(
                self.ENDPOINT,
                json={"api_key": self.api_key, "query": query, "max_results": max_results},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning(f"Tavily search failed for '{query}': {exc}")
            return []

        results = []
        for item in data.get("results", [])[:max_results]:
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
            ))
        return results


class SerperProvider(SearchProvider):
    name = "serper"
    ENDPOINT = "https://google.serper.dev/search"

    def __init__(self, api_key: str, timeout: int = 15):
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        try:
            resp = requests.post(
                self.ENDPOINT,
                json={"q": query, "num": max_results},
                headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning(f"Serper search failed for '{query}': {exc}")
            return []

        results = []
        for item in data.get("organic", [])[:max_results]:
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
            ))
        return results


_KEYED_PROVIDERS = {
    "brave": (BraveProvider, "BRAVE_API_KEY"),
    "tavily": (TavilyProvider, "TAVILY_API_KEY"),
    "serper": (SerperProvider, "SERPER_API_KEY"),
}


def get_search_provider(name: Optional[str] = None) -> SearchProvider:
    """Resolve a SearchProvider by name, env var, or default.

    Resolution order: explicit *name* -> ``SEARCH_PROVIDER`` env var ->
    ``'duckduckgo'``. A keyed provider with a missing API key falls back to
    DuckDuckGo with a warning rather than failing.
    """
    provider_name = (name or os.getenv("SEARCH_PROVIDER") or "duckduckgo").lower()

    if provider_name in _KEYED_PROVIDERS:
        provider_cls, env_key = _KEYED_PROVIDERS[provider_name]
        api_key = os.getenv(env_key)
        if api_key:
            return provider_cls(api_key)
        logger.warning(f"{env_key} not set; falling back to DuckDuckGo for search.")
        return DuckDuckGoProvider()

    if provider_name != "duckduckgo":
        logger.warning(f"Unknown SEARCH_PROVIDER '{provider_name}'; falling back to DuckDuckGo.")

    return DuckDuckGoProvider()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    q = " ".join(sys.argv[1:]) or "Siemens AG official website"
    encoding = sys.stdout.encoding or "utf-8"
    for r in get_search_provider().search(q):
        line = f"{r.title}\n  {r.url}\n  {r.snippet[:120]}\n"
        print(line.encode(encoding, errors="replace").decode(encoding))
