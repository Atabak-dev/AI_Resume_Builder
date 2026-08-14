"""
LLM-callable tools for automated company research.

`ResearchToolbox` is dispatched from `LLM_Handeler.run_tool_loop()`
(see llm_client.py). It owns three tools - `web_search`, `fetch_page`,
`wikipedia_page` - and enforces two things on every call:

- `HostApprovalGate`: the user approves each new host once per run before any
  page on it is fetched (Wikipedia and the search endpoint itself are
  auto-allowed, plus anything listed in the repo-root `allowed_domains.txt`
  via `HostApprovalGate.load_domains_file()`). That file is gitignored and
  per-user; `load_domains_file()` creates it from `DEFAULT_DOMAINS_FILE_TEMPLATE`
  the first time it is missing. Every fetch is still traced to the console,
  even on an already-approved host, so nothing happens invisibly.
- the privacy contract (`src.utils.privacy.PersonalInfoScrubber`): outbound
  queries/URLs containing the candidate's personal info are hard-blocked;
  inbound page text is scrubbed before it re-enters the LLM.
"""

import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DEFAULT_DOMAINS_FILE_TEMPLATE = """\
# Hosts listed here are auto-approved for the company-research tool calls
# (web_search / fetch_page / wikipedia_page) - the assistant will never be
# asked to confirm them, on top of the built-in defaults (Wikipedia, the
# search endpoint). Everything else still prompts once per run.
#
# This file is local to your machine (gitignored) - one host per line.
# Lines starting with '#' and blank lines are ignored. A scheme, "www.",
# a port, or a path is tolerated and stripped, so
# "https://www.example.com/about" and "example.com" are equivalent.
#
# Example:
# wikipedia.org
"""


def _normalize_host(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host.split(":")[0]


class HostApprovalGate:
    """Per-host (not per-URL) user approval, cached for the run."""

    AUTO_ALLOW = {"wikipedia.org"}

    def __init__(self, auto_allow: Optional[set] = None, interactive: bool = True):
        self._auto_allow = auto_allow if auto_allow is not None else set(self.AUTO_ALLOW)
        self._interactive = interactive
        self._approved: set = set()
        self._denied: set = set()

    @staticmethod
    def _is_wikipedia(host: str) -> bool:
        return host == "wikipedia.org" or host.endswith(".wikipedia.org")

    def is_allowed(self, url: str) -> bool:
        host = _normalize_host(url)
        return self._is_wikipedia(host) or host in self._auto_allow or host in self._approved

    def request(self, url: str, reason: str = "") -> bool:
        host = _normalize_host(url)
        if self._is_wikipedia(host) or host in self._auto_allow or host in self._approved:
            return True
        if host in self._denied:
            return False
        if not self._interactive:
            logger.warning(f"Non-interactive gate: denying unapproved host {host}")
            self._denied.add(host)
            return False

        print("\nThe assistant wants to access a new website:")
        print(f"  Host  : {host}")
        print(f"  URL   : {url}")
        if reason:
            print(f"  Reason: {reason}")
        answer = input("Allow all pages on this host for this run? [y/N]: ").strip().lower()

        if answer == "y":
            self._approved.add(host)
            return True
        self._denied.add(host)
        return False

    def approve(self, url: str) -> None:
        """Mark *url*'s host approved without prompting.

        For a URL the user typed in themselves (the manual-website
        fallback) - supplying it *is* the approval, so asking again would
        just be noise.
        """
        self._approved.add(_normalize_host(url))

    @property
    def approved_hosts(self) -> List[str]:
        return sorted(self._approved | self._auto_allow)

    @classmethod
    def load_domains_file(cls, path: str, create_if_missing: bool = True) -> set:
        """Read a user-maintained list of pre-approved hosts.

        One host per line (e.g. `example.com`); `#` starts a comment, blank
        lines are ignored, and a scheme/`www.`/port/path on a line is
        tolerated and stripped. The file is per-user (gitignored); if it
        doesn't exist yet, it is created from `DEFAULT_DOMAINS_FILE_TEMPLATE`
        so each user gets their own empty, documented copy on first run.
        """
        if create_if_missing and not os.path.exists(path):
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(DEFAULT_DOMAINS_FILE_TEMPLATE)
                logger.info(f"Created default allowed-domains file at {path}")
            except OSError as e:
                logger.warning(f"Could not create allowed-domains file at {path}: {e}")

        domains = set()
        try:
            with open(path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.split("#", 1)[0].strip()
                    if not line:
                        continue
                    if "://" not in line:
                        line = f"https://{line}"
                    domains.add(_normalize_host(line))
        except FileNotFoundError:
            logger.debug(f"No allowed-domains file found at {path}; skipping.")
        return domains


class ResearchToolbox:
    def __init__(
        self,
        scrubber: Any,
        gate: HostApprovalGate,
        provider: Any,
        wiki: Any,
        site: Any,
        max_page_chars: int = 12000,
        max_fetches: int = 8,
        max_searches: int = 6,
        default_search_results: int = 5,
    ):
        self.scrubber = scrubber
        self.gate = gate
        self.provider = provider
        self.wiki = wiki
        self.site = site
        self.max_page_chars = max_page_chars
        self.max_fetches = max_fetches
        self.max_searches = max_searches
        self.default_search_results = default_search_results

        self._fetch_count = 0
        self._search_count = 0
        self._empty_searches = 0
        self._sources: List[str] = []
        self._dossier_parts: List[str] = []
        self._page_cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #

    def web_search(self, query: str, max_results: Optional[int] = None) -> dict:
        hits = self.scrubber.find_personal_info(query)
        if hits:
            self._block("web_search", query, hits)
            return {"status": "blocked",
                    "reason": "The query contained the candidate's personal information and was not "
                              "executed. Search only for the company; never for a person."}

        if self.provider is None:
            return {"status": "unavailable",
                    "reason": "no search backend configured",
                    "hint": "No search API key is set up. Do not retry web_search - call "
                            "wikipedia_page with the company name, use any pages already "
                            "fetched, and summarise what you could not verify."}

        if self._search_count >= self.max_searches:
            return {"status": "budget_exceeded",
                    "message": f"Search budget of {self.max_searches} queries used up. "
                              "Use wikipedia_page or summarise what you already have."}

        max_results = max_results or self.default_search_results
        self._search_count += 1
        results = self.provider.search(query, max_results=max_results)
        provider_name = getattr(self.provider, "name", "?")
        print(f"  -> web_search '{query}'  (provider {provider_name}) -> {len(results)} result(s)")
        logger.info(f"web_search '{query}' -> {[r.url for r in results]}")

        if not results:
            status = getattr(self.provider, "last_status", "empty")
            self._empty_searches += 1
            if status == "blocked" or self._empty_searches >= 2:
                hint = ("Every configured search back-end is blocked or returning nothing. "
                        "Rewording the query will NOT help - do not retry web_search. "
                        "Call wikipedia_page with the company name instead, or summarise "
                        "what you already have and state what you could not verify.")
            else:
                hint = ("No results for this exact query. Try at most one differently-worded "
                        "query, then move on - do not keep rephrasing.")
            return {"status": "empty", "query": query, "provider": provider_name, "reason": status,
                    "hint": hint, "searches_remaining": max(0, self.max_searches - self._search_count)}
        return {"status": "ok", "query": query, "results": [r.to_dict() for r in results]}

    def fetch_page(self, url: str, reason: str = "") -> dict:
        hits = self.scrubber.find_personal_info(url)
        if hits:
            self._block("fetch_page", url, hits)
            return {"status": "blocked",
                    "reason": "The URL contained the candidate's personal information and was not "
                              "fetched."}

        if not url.startswith(("http://", "https://")):
            return {"status": "error", "url": url, "error": "URL must start with http:// or https://"}

        if url in self._page_cache:
            return self._page_cache[url]

        if self._fetch_count >= self.max_fetches:
            return {"status": "budget_exceeded",
                    "message": f"Fetch budget of {self.max_fetches} pages used up. Summarise what you have."}

        host = _normalize_host(url)
        already_approved = self.gate.is_allowed(url)
        if not self.gate.request(url, reason):
            print(f"  x  denied: {url}  (host {host})")
            return {"status": "denied", "url": url, "host": host,
                    "message": "The user declined access to this host. Try a different source."}

        note = "already approved" if already_approved else "approved"
        print(f"  -> fetching {url}  (host {host} {note})")

        page = self.site.extract_text_and_links(url)
        text = page.get("text", "")
        if not text:
            print("     error: no content retrieved")
            result = {"status": "error", "url": url, "error": "failed to fetch or empty page"}
            self._page_cache[url] = result
            return result

        scrubbed = self.scrubber.scrub(text, min_length=3)
        truncated = len(scrubbed) > self.max_page_chars
        scrubbed = scrubbed[:self.max_page_chars]

        self._fetch_count += 1
        self._sources.append(url)
        self._dossier_parts.append(f"=== PAGE: {url} ===\n{scrubbed}")
        print(f"     ok, {len(scrubbed)} chars" + (" (truncated)" if truncated else ""))

        result = {
            "status": "ok",
            "url": url,
            "text": scrubbed,
            "links": page.get("links", [])[:40],
            "truncated": truncated,
        }
        self._page_cache[url] = result
        return result

    def wikipedia_page(self, title: str, language: Optional[str] = None) -> dict:
        hits = self.scrubber.find_personal_info(title)
        if hits:
            self._block("wikipedia_page", title, hits)
            return {"status": "blocked",
                    "reason": "The title contained the candidate's personal information and was not "
                              "looked up."}

        if self._fetch_count >= self.max_fetches:
            return {"status": "budget_exceeded",
                    "message": f"Fetch budget of {self.max_fetches} pages used up. Summarise what you have."}

        article = self.wiki.resolve_article(title, provider=self.provider, language=language)
        if not article:
            suggestions = self.wiki.search_titles(title)
            return {"status": "not_found", "requested_title": title, "suggestions": suggestions,
                    "message": "No Wikipedia article could be confidently matched to this company. "
                              "Do NOT accept a similarly-named article - a different company with a "
                              "similar name is worse than no article. Continue with the official website."}

        if not self.gate.request(article.url, reason=f"Wikipedia article for {title}"):
            print(f"  x  denied: {article.url}  (host {_normalize_host(article.url)})")
            return {"status": "denied", "url": article.url, "host": _normalize_host(article.url),
                    "message": "The user declined access to this host. Try a different source."}

        print(f"  -> fetching {article.url}  (host {_normalize_host(article.url)} already approved)")

        text = self.wiki.fetch_article(article)
        if not text:
            print("     error: no content retrieved")
            return {"status": "not_found", "requested_title": title, "suggestions": []}

        scrubbed = self.scrubber.scrub(text, min_length=3)
        truncated = len(scrubbed) > self.max_page_chars
        scrubbed = scrubbed[:self.max_page_chars]

        self._fetch_count += 1
        self._sources.append(article.url)
        self._dossier_parts.append(f"=== PAGE: {article.url} ===\n{scrubbed}")
        print(f"     ok, {len(scrubbed)} chars" + (" (truncated)" if truncated else ""))

        result = {"status": "ok", "requested_title": title, "title": article.title,
                  "language": article.language, "url": article.url,
                  "text": scrubbed, "truncated": truncated}
        if article.title != title or article.language != self.wiki.language:
            result["note"] = (f"Resolved '{title}' to the {article.language}-edition article "
                              f"'{article.title}'.")
        return result

    # ------------------------------------------------------------------ #
    # Dispatch / schemas / results
    # ------------------------------------------------------------------ #

    def dispatch(self, name: str, arguments: Any) -> dict:
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
        except json.JSONDecodeError as e:
            logger.warning(f"Tool call '{name}' had invalid arguments JSON: {e}")
            return {"status": "error", "error": "invalid arguments JSON"}

        handler = {
            "web_search": self.web_search,
            "fetch_page": self.fetch_page,
            "wikipedia_page": self.wikipedia_page,
        }.get(name)

        if handler is None:
            return {"status": "error", "error": f"unknown tool '{name}'"}

        try:
            return handler(**args)
        except TypeError as e:
            logger.warning(f"Tool call '{name}' had bad arguments: {e}")
            return {"status": "error", "error": f"bad arguments: {e}"}
        except Exception as e:
            logger.error(f"Tool '{name}' raised: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    def _block(self, tool_name: str, value: str, hits: List[str]) -> None:
        logger.critical(f"PRIVACY: blocked a {tool_name} call containing personal information {hits} — value: {value!r}")
        print(f"\n  !! BLOCKED: the assistant tried to use your personal information ({', '.join(hits)}) "
              f"in a {tool_name} call. It was not sent. !!\n")

    @property
    def schemas(self) -> List[dict]:
        return [
            {"type": "function", "function": {
                "name": "web_search",
                "description": ("Search the public web. Use it to find a company's official website or "
                                "its Wikipedia article. Only search for the company; never for a person. "
                                "If a result comes back with status 'empty', do not keep rewording the "
                                "query - read the 'hint' field and follow it."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query, e.g. 'Acme GmbH official website'"},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            }},
            {"type": "function", "function": {
                "name": "fetch_page",
                "description": ("Fetch the readable text and internal links of one page. The user must "
                                "approve each new host; if the result status is 'denied', do not retry "
                                "that host - use another source."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "reason": {"type": "string", "description": "One sentence shown to the user when asking for approval"},
                    },
                    "required": ["url", "reason"],
                    "additionalProperties": False,
                },
            }},
            {"type": "function", "function": {
                "name": "wikipedia_page",
                "description": ("Fetch the plain text of a Wikipedia article. Prefer passing the exact "
                                "title from a https://<lang>.wikipedia.org/wiki/<Title> URL you found "
                                "with web_search (URL-decode it and turn '_' into spaces), together with "
                                "its language. A title that does not verifiably name the same company is "
                                "rejected rather than guessed at - status will be 'not_found'."),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "language": {"type": "string",
                                    "description": "Wikipedia edition code from the article URL host, "
                                                    "e.g. 'de' for de.wikipedia.org. Optional."},
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                },
            }},
        ]

    @property
    def dossier(self) -> str:
        return "\n\n".join(self._dossier_parts)

    @property
    def sources(self) -> List[str]:
        return list(dict.fromkeys(self._sources))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    from src.utils.privacy import PersonalInfoScrubber
    from src.utils.search import get_search_provider
    from src.utils.scraper import WikipediaScraper, CompanyWebsiteScraper

    company = " ".join(sys.argv[1:]) or "Siemens AG"
    provider = get_search_provider()
    toolbox = ResearchToolbox(
        scrubber=PersonalInfoScrubber({}),
        gate=HostApprovalGate(),
        provider=provider,
        wiki=WikipediaScraper(provider=provider),
        site=CompanyWebsiteScraper(),
    )

    print(json.dumps(toolbox.dispatch("web_search", {"query": f"{company} official website"}), indent=2)[:2000])
    print(json.dumps(toolbox.dispatch("wikipedia_page", {"title": company}), indent=2)[:2000])
    print("\nSources:", toolbox.sources)
