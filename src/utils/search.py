"""
Pluggable web-search backends for company research.

DuckDuckGo's HTML endpoint is the default because it needs no signup or API
key, with a keyless Bing scrape as a fallback when DuckDuckGo serves its
bot-challenge page or otherwise comes back empty. Brave / Tavily / Serper are
available as drop-in alternatives (and are tried first) once an API key is
set in .env (SEARCH_PROVIDER + <PROVIDER>_API_KEY).
"""

import base64
import os
import sys
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlparse, urljoin, parse_qs, unquote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/91.0.4472.124 Safari/537.36"
)

_INVISIBLE = str.maketrans({"­": "", "​": "", "‎": "", "‏": "", "﻿": ""})


def _clean_text(s: str) -> str:
    """Strip soft hyphens / zero-width chars and collapse whitespace."""
    return " ".join(s.translate(_INVISIBLE).split())


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str

    def to_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


class SearchProvider(ABC):
    name: str = "base"

    #: Set by every search() call: "ok" | "empty" | "blocked" | "error".
    last_status: str = "ok"

    #: Latched True once this backend has served a bot-challenge/captcha, so
    #: later calls in the same run short-circuit instead of retrying.
    blocked: bool = False

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        ...


class DuckDuckGoProvider(SearchProvider):
    """Keyless HTML-scraping backend. No signup required; rate-limits easily."""

    name = "duckduckgo"
    ENDPOINT = "https://html.duckduckgo.com/html/"

    _CHALLENGE_MARKERS = ("anomaly-modal", "bots use DuckDuckGo", "Select all squares containing")

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

    @classmethod
    def _is_challenge(cls, resp: requests.Response, html: str) -> bool:
        return resp.status_code == 202 or any(m in html for m in cls._CHALLENGE_MARKERS)

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        if self.blocked:
            self.last_status = "blocked"
            return []

        cache_key = query.strip().lower()
        if cache_key in self._cache:
            self.last_status = "ok" if self._cache[cache_key] else "empty"
            return self._cache[cache_key][:max_results]

        self._wait()
        try:
            resp = self.session.post(self.ENDPOINT, data={"q": query}, timeout=self.timeout)
            resp.raise_for_status()
        except requests.exceptions.RequestException as exc:
            logger.warning(f"DuckDuckGo search failed for '{query}': {exc}")
            self.last_status = "error"
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
            snippet = _clean_text(" ".join(snippet_el.stripped_strings)) if snippet_el else ""
            title = _clean_text(" ".join(link.stripped_strings))
            results.append(SearchResult(title=title, url=url, snippet=snippet))

        if not results:
            if self._is_challenge(resp, resp.text):
                self.blocked = True
                self.last_status = "blocked"
                msg = (
                    f"DuckDuckGo served its bot-challenge page (HTTP {resp.status_code}); "
                    "the endpoint is blocking this network. Disabling DuckDuckGo for the rest "
                    "of this run - set SEARCH_PROVIDER=brave|tavily|serper with an API key for "
                    "reliable search."
                )
                logger.warning(msg)
                print(f"  ! {msg}")
                return []
            self.last_status = "empty"
            logger.warning(f"DuckDuckGo returned no parsable results for '{query}'.")

        self.last_status = "ok" if results else "empty"
        self._cache[cache_key] = results
        return results[:max_results]


class BingProvider(SearchProvider):
    """Keyless HTML-scraping backend for www.bing.com/search. Used as a
    fallback when DuckDuckGo is blocked or comes back empty."""

    name = "bing"
    ENDPOINT = "https://www.bing.com/search"

    def __init__(self, timeout: int = 15, min_interval: float = 1.5, language: Optional[str] = None):
        self.timeout = timeout
        self.min_interval = min_interval
        self.language = language
        self._last_request = 0.0
        self._cache: dict = {}
        self.session = requests.Session()
        accept_language = f"{language}, en;q=0.8" if language and language != "en" else "en-US,en;q=0.9"
        self.session.headers.update({
            "User-Agent": _BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": accept_language,
        })

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.monotonic()

    @staticmethod
    def _unwrap_bing_url(href: str) -> Optional[str]:
        if not href:
            return None
        if href.startswith("/"):
            href = urljoin("https://www.bing.com", href)
        parsed = urlparse(href)
        if not parsed.netloc.lower().endswith("bing.com"):
            return href
        if not parsed.path.startswith("/ck/a"):
            return None
        encoded = parse_qs(parsed.query).get("u", [""])[0]
        if not encoded:
            return None
        if encoded.startswith(("http://", "https://")):
            return encoded
        candidates = (encoded[2:], encoded[1:], encoded) if encoded.startswith("a1") else (encoded[1:], encoded)
        for cand in candidates:
            padded = cand + "=" * (-len(cand) % 4)
            try:
                decoded = base64.urlsafe_b64decode(padded).decode("utf-8", "replace")
            except (ValueError, UnicodeDecodeError):
                continue
            if decoded.startswith(("http://", "https://")):
                return decoded
        return None

    @staticmethod
    def _is_challenge(resp: requests.Response) -> bool:
        if resp.status_code in (403, 429):
            return True
        if "b_captcha" in resp.text or "Verifying you are human" in resp.text:
            return True
        if urlparse(resp.url).path != "/search":
            return True
        return False

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        if self.blocked:
            self.last_status = "blocked"
            return []

        cache_key = query.strip().lower()
        if cache_key in self._cache:
            self.last_status = "ok" if self._cache[cache_key] else "empty"
            return self._cache[cache_key][:max_results]

        self._wait()
        try:
            resp = self.session.get(
                self.ENDPOINT,
                params={"q": query, "count": max(max_results, 10)},
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as exc:
            logger.warning(f"Bing search failed for '{query}': {exc}")
            self.last_status = "error"
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select("li.b_algo")

        if not items:
            if self._is_challenge(resp):
                self.blocked = True
                self.last_status = "blocked"
                logger.warning(
                    f"Bing served a challenge/redirect page (HTTP {resp.status_code}, "
                    f"final URL {resp.url}); disabling Bing for the rest of this run."
                )
                return []
            self.last_status = "empty"
            logger.warning(f"Bing returned no parsable results for '{query}' (markup may have changed).")
            logger.debug(f"Bing response excerpt: {resp.text[:400]!r}")
            return []

        results: List[SearchResult] = []
        seen_urls = set()
        for item in items:
            link = item.select_one("h2 a")
            if not link:
                continue
            url = self._unwrap_bing_url(link.get("href", ""))
            if not url:
                cite = item.select_one("cite")
                if cite:
                    host_text = cite.get_text(" ", strip=True).split()[0] if cite.get_text(strip=True) else ""
                    if host_text and not host_text.startswith("http"):
                        url = f"https://{host_text}"
                    elif host_text.startswith("http"):
                        url = host_text
            if not url or not url.startswith(("http://", "https://")):
                continue
            host = urlparse(url).netloc.lower()
            if host.endswith("bing.com") or host.endswith("microsoft.com"):
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            snippet_el = item.select_one("div.b_caption p") or item.select_one("p")
            snippet = _clean_text(snippet_el.get_text(" ", strip=True)) if snippet_el else ""
            title = _clean_text(link.get_text(" ", strip=True))
            results.append(SearchResult(title=title, url=url, snippet=snippet))

        self.last_status = "ok" if results else "empty"
        if results:
            self._cache[cache_key] = results
        else:
            logger.warning(f"Bing results parsed but yielded no usable URLs for '{query}'.")
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
            self.last_status = "error"
            return []

        results = []
        for item in data.get("web", {}).get("results", [])[:max_results]:
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("description", ""),
            ))
        self.last_status = "ok" if results else "empty"
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
            self.last_status = "error"
            return []

        results = []
        for item in data.get("results", [])[:max_results]:
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
            ))
        self.last_status = "ok" if results else "empty"
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
            self.last_status = "error"
            return []

        results = []
        for item in data.get("organic", [])[:max_results]:
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
            ))
        self.last_status = "ok" if results else "empty"
        return results


class ChainedProvider(SearchProvider):
    """Tries each backend in order; the first non-empty result set wins."""

    def __init__(self, providers: List[SearchProvider]):
        self.providers = [p for p in providers if p is not None]
        self.name = "+".join(p.name for p in self.providers) or "none"

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        statuses = []
        for index, provider in enumerate(self.providers):
            try:
                results = provider.search(query, max_results=max_results)
            except Exception as exc:
                logger.warning(f"Search provider '{provider.name}' raised for '{query}': {exc}")
                statuses.append("error")
                continue
            statuses.append(getattr(provider, "last_status", "ok"))
            if results:
                if index:
                    logger.info(f"Search for '{query}' served by fallback provider '{provider.name}'.")
                self.last_status = "ok"
                return results

        self.last_status = "blocked" if "blocked" in statuses else ("error" if "error" in statuses else "empty")
        logger.warning(
            f"No search provider returned results for '{query}' "
            f"(tried {[p.name for p in self.providers]}, status={self.last_status})."
        )
        return []

    @property
    def blocked(self) -> bool:
        return bool(self.providers) and all(getattr(p, "blocked", False) for p in self.providers)


_KEYED_PROVIDERS = {
    "brave": (BraveProvider, "BRAVE_API_KEY"),
    "tavily": (TavilyProvider, "TAVILY_API_KEY"),
    "serper": (SerperProvider, "SERPER_API_KEY"),
}
_KEYLESS_PROVIDERS = {
    "duckduckgo": DuckDuckGoProvider,
    "bing": BingProvider,
}
_DEFAULT_KEYLESS_ORDER = ("duckduckgo", "bing")


def _instantiate(provider_name: str, language: Optional[str]) -> Optional[SearchProvider]:
    """Build one provider by name, or None when unknown / its API key is missing."""
    if provider_name in _KEYED_PROVIDERS:
        provider_cls, env_key = _KEYED_PROVIDERS[provider_name]
        api_key = os.getenv(env_key)
        if not api_key:
            logger.warning(f"{env_key} not set; skipping the {provider_name} search backend.")
            return None
        return provider_cls(api_key)
    if provider_name == "bing":
        return BingProvider(language=language)
    if provider_name in _KEYLESS_PROVIDERS:
        return _KEYLESS_PROVIDERS[provider_name]()
    logger.warning(f"Unknown search provider '{provider_name}'; skipping.")
    return None


def get_search_provider(name: Optional[str] = None, language: Optional[str] = None) -> SearchProvider:
    """Resolve a SearchProvider by name, env var, or default.

    Resolution order: explicit *name* (or the ``SEARCH_PROVIDER`` env var) ->
    any keyed provider whose API key is set -> ``duckduckgo`` -> ``bing``.

    *name* / ``SEARCH_PROVIDER`` accepts:
      - empty / ``"auto"``: the default chain described above.
      - a single provider name: prioritised ahead of the default chain.
      - a comma-separated list: an exact order, then the remaining defaults.
      - an ``"only:<name>"`` prefix: pins that one provider, no fallback.

    A keyed provider with a missing API key is skipped (with a warning)
    rather than special-cased; the chain's remaining backends cover for it.
    """
    raw = (name or os.getenv("SEARCH_PROVIDER") or "").strip().lower()

    pin = raw.startswith("only:")
    if pin:
        raw = raw[len("only:"):]

    requested = [n.strip() for n in raw.split(",") if n.strip() and n.strip() != "auto"]

    ordered_names: List[str] = []
    for n in requested:
        if n in _KEYED_PROVIDERS or n in _KEYLESS_PROVIDERS:
            if n not in ordered_names:
                ordered_names.append(n)
        else:
            logger.warning(f"Unknown SEARCH_PROVIDER entry '{n}'; ignoring.")

    if not pin:
        for n in list(_KEYED_PROVIDERS) + list(_DEFAULT_KEYLESS_ORDER):
            if n not in ordered_names:
                ordered_names.append(n)

    providers = []
    for n in ordered_names:
        # Only build a keyed provider if it's requested or its key is set;
        # keyless providers are always available.
        if n in _KEYED_PROVIDERS and n not in requested and not os.getenv(_KEYED_PROVIDERS[n][1]):
            continue
        provider = _instantiate(n, language)
        if provider is not None:
            providers.append(provider)
        if pin:
            break

    if not providers:
        providers = [DuckDuckGoProvider()]

    return providers[0] if len(providers) == 1 else ChainedProvider(providers)


if __name__ == "__main__":
    import argparse

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default=None)
    parser.add_argument("query", nargs="*")
    args = parser.parse_args()

    q = " ".join(args.query) or "Siemens AG official website"
    provider = get_search_provider(args.provider)
    encoding = sys.stdout.encoding or "utf-8"
    results = provider.search(q)
    print(f"[provider={provider.name} status={provider.last_status}]")
    for r in results:
        line = f"{r.title}\n  {r.url}\n  {r.snippet[:120]}\n"
        print(line.encode(encoding, errors="replace").decode(encoding))
