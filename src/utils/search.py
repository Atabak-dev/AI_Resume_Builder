"""
Pluggable web-search backends for company research.

Only keyed, real-API backends are supported: Brave, Tavily, Serper. Set
SEARCH_PROVIDER (optional) + the matching <PROVIDER>_API_KEY in .env.

Two keyless HTML-scraping backends (DuckDuckGo, Bing) used to live here.
Both were removed: DuckDuckGo hard-blocks with a bot-challenge page on most
networks, and Bing was found to serve *structurally valid but content-wrong*
result pages (HTTP 200, correct title/searchbox, well-formed but unrelated
results, footnoted "Some results have been removed") that no parser can
distinguish from genuine results. Feeding that into the LLM research loop is
worse than no results at all. See CLAUDE.md "Known rough edges" for the
investigation. When no API key is configured, callers fall back to asking
the user for the company's website directly (see main.py's
`_prompt_manual_website`).
"""

import os
import sys
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

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

    #: Latched True once this backend is known unusable for the rest of the
    #: run (bad/expired key, exhausted quota), so later calls short-circuit
    #: instead of repeating a doomed request.
    blocked: bool = False

    #: Minimum seconds between requests, enforced by _throttle(). 0 = none.
    min_interval: float = 0.0

    _last_request: float = 0.0
    _cache: Optional[dict] = None

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.monotonic()

    def _cached(self, query: str) -> Optional[List[SearchResult]]:
        if self._cache is None:
            self._cache = {}
        cached = self._cache.get(query.strip().lower())
        if cached is not None:
            self.last_status = "ok" if cached else "empty"
        return cached

    def _store_cache(self, query: str, results: List[SearchResult]) -> None:
        if self._cache is None:
            self._cache = {}
        self._cache[query.strip().lower()] = results

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        ...


def _handle_http_error(provider: SearchProvider, exc: Exception, query: str, env_key: str) -> None:
    """Shared HTTP-error classification for the keyed providers."""
    status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if status_code in (401, 403):
        provider.blocked = True
        provider.last_status = "error"
        logger.warning(
            f"{provider.name} search failed for '{query}' with HTTP {status_code} "
            f"(invalid or expired key?). Disabling {provider.name} for the rest of this "
            f"run - check {env_key} in .env."
        )
    elif status_code == 429:
        provider.blocked = True
        provider.last_status = "error"
        logger.warning(
            f"{provider.name} search failed for '{query}' with HTTP 429 (rate limit / "
            f"free-tier quota exhausted). Disabling {provider.name} for the rest of this run."
        )
    else:
        provider.last_status = "error"
        logger.warning(f"{provider.name} search failed for '{query}': {exc}")


class BraveProvider(SearchProvider):
    name = "brave"
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    #: Brave's free tier is rate-limited to 1 request/second.
    min_interval = 1.1

    def __init__(self, api_key: str, timeout: int = 15, language: Optional[str] = None):
        self.api_key = api_key
        self.timeout = timeout
        self.language = language

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        if self.blocked:
            self.last_status = "blocked"
            return []
        cached = self._cached(query)
        if cached is not None:
            return cached[:max_results]

        self._throttle()
        params = {"q": query, "count": max_results}
        if self.language:
            params["search_lang"] = self.language
            params["country"] = self.language.upper()
        try:
            resp = requests.get(
                self.ENDPOINT,
                params=params,
                headers={"Accept": "application/json", "X-Subscription-Token": self.api_key},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            _handle_http_error(self, exc, query, "BRAVE_API_KEY")
            return []

        results = []
        for item in data.get("web", {}).get("results", [])[:max_results]:
            results.append(SearchResult(
                title=_clean_text(item.get("title", "")),
                url=item.get("url", ""),
                snippet=_clean_text(item.get("description", "")),
            ))
        self.last_status = "ok" if results else "empty"
        self._store_cache(query, results)
        return results


class TavilyProvider(SearchProvider):
    name = "tavily"
    ENDPOINT = "https://api.tavily.com/search"

    def __init__(self, api_key: str, timeout: int = 15, language: Optional[str] = None):
        self.api_key = api_key
        self.timeout = timeout
        self.language = language  # Tavily has no locale parameter; accepted for interface symmetry.

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        if self.blocked:
            self.last_status = "blocked"
            return []
        cached = self._cached(query)
        if cached is not None:
            return cached[:max_results]

        self._throttle()
        try:
            resp = requests.post(
                self.ENDPOINT,
                json={"api_key": self.api_key, "query": query, "max_results": max_results},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            _handle_http_error(self, exc, query, "TAVILY_API_KEY")
            return []

        results = []
        for item in data.get("results", [])[:max_results]:
            results.append(SearchResult(
                title=_clean_text(item.get("title", "")),
                url=item.get("url", ""),
                snippet=_clean_text(item.get("content", "")),
            ))
        self.last_status = "ok" if results else "empty"
        self._store_cache(query, results)
        return results


class SerperProvider(SearchProvider):
    name = "serper"
    ENDPOINT = "https://google.serper.dev/search"

    def __init__(self, api_key: str, timeout: int = 15, language: Optional[str] = None):
        self.api_key = api_key
        self.timeout = timeout
        self.language = language

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        if self.blocked:
            self.last_status = "blocked"
            return []
        cached = self._cached(query)
        if cached is not None:
            return cached[:max_results]

        self._throttle()
        payload = {"q": query, "num": max_results}
        if self.language:
            payload["hl"] = self.language
            payload["gl"] = self.language
        try:
            resp = requests.post(
                self.ENDPOINT,
                json=payload,
                headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            _handle_http_error(self, exc, query, "SERPER_API_KEY")
            return []

        results = []
        for item in data.get("organic", [])[:max_results]:
            results.append(SearchResult(
                title=_clean_text(item.get("title", "")),
                url=item.get("link", ""),
                snippet=_clean_text(item.get("snippet", "")),
            ))
        self.last_status = "ok" if results else "empty"
        self._store_cache(query, results)
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


def _instantiate(provider_name: str, language: Optional[str]) -> Optional[SearchProvider]:
    """Build one keyed provider by name, or None when unknown / its API key is missing."""
    if provider_name in _KEYED_PROVIDERS:
        provider_cls, env_key = _KEYED_PROVIDERS[provider_name]
        api_key = os.getenv(env_key)
        if not api_key:
            logger.warning(f"{env_key} not set; skipping the {provider_name} search backend.")
            return None
        return provider_cls(api_key, language=language)
    logger.warning(f"Unknown search provider '{provider_name}'; skipping.")
    return None


def get_search_provider(name: Optional[str] = None, language: Optional[str] = None) -> Optional[SearchProvider]:
    """Resolve a SearchProvider by name, env var, or the set of configured keys.

    Resolution order: explicit *name* (or the ``SEARCH_PROVIDER`` env var) ->
    every remaining keyed provider whose API key is set.

    *name* / ``SEARCH_PROVIDER`` accepts:
      - empty / ``"auto"``: try every keyed provider with a key set, brave
        first, then tavily, then serper.
      - a single provider name: prioritised ahead of the others.
      - a comma-separated list: an exact order, then any remaining keyed
        providers with a key set.
      - an ``"only:<name>"`` prefix: pins that one provider, no fallback.

    Returns ``None`` when no API key is configured for any requested
    provider - callers must handle this (see main.py's manual website
    fallback) since there is no keyless backend left to fall back to.
    """
    raw = (name or os.getenv("SEARCH_PROVIDER") or "").strip().lower()

    pin = raw.startswith("only:")
    if pin:
        raw = raw[len("only:"):]

    requested = [n.strip() for n in raw.split(",") if n.strip() and n.strip() != "auto"]

    ordered_names: List[str] = []
    for n in requested:
        if n in _KEYED_PROVIDERS:
            if n not in ordered_names:
                ordered_names.append(n)
        else:
            logger.warning(f"Unknown SEARCH_PROVIDER entry '{n}'; ignoring.")

    if not pin:
        for n in _KEYED_PROVIDERS:
            if n not in ordered_names:
                ordered_names.append(n)

    providers = []
    for n in ordered_names:
        if n not in requested and not os.getenv(_KEYED_PROVIDERS[n][1]):
            continue
        provider = _instantiate(n, language)
        if provider is not None:
            providers.append(provider)
        if pin:
            break

    if not providers:
        logger.warning(
            "No search API key configured (checked BRAVE_API_KEY, TAVILY_API_KEY, "
            "SERPER_API_KEY). Automatic company research is unavailable this run."
        )
        return None

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
    if provider is None:
        print("No search backend configured - set BRAVE_API_KEY, TAVILY_API_KEY or SERPER_API_KEY in .env.")
        raise SystemExit(1)

    encoding = sys.stdout.encoding or "utf-8"
    results = provider.search(q)
    print(f"[provider={provider.name} status={provider.last_status}]")
    for r in results:
        line = f"{r.title}\n  {r.url}\n  {r.snippet[:120]}\n"
        print(line.encode(encoding, errors="replace").decode(encoding))
