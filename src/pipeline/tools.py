"""
LLM-callable tools for automated company research.

`ResearchToolbox` is dispatched from `LLM_Handeler.run_tool_loop()`
(see llm_client.py). It owns three tools - `web_search`, `fetch_page`,
`wikipedia_page` - and enforces two things on every call:

- `HostApprovalGate`: the user approves each new host once per run before any
  page on it is fetched (Wikipedia and the search endpoint itself are
  auto-allowed). Every fetch is still traced to the console, even on an
  already-approved host, so nothing happens invisibly.
- the privacy contract (`src.utils.privacy.PersonalInfoScrubber`): outbound
  queries/URLs containing the candidate's personal info are hard-blocked;
  inbound page text is scrubbed before it re-enters the LLM.
"""

import json
import logging
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _normalize_host(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host.split(":")[0]


class HostApprovalGate:
    """Per-host (not per-URL) user approval, cached for the run."""

    AUTO_ALLOW = {"en.wikipedia.org", "de.wikipedia.org", "wikipedia.org", "html.duckduckgo.com"}

    def __init__(self, auto_allow: Optional[set] = None, interactive: bool = True):
        self._auto_allow = auto_allow if auto_allow is not None else set(self.AUTO_ALLOW)
        self._interactive = interactive
        self._approved: set = set()
        self._denied: set = set()

    def is_allowed(self, url: str) -> bool:
        host = _normalize_host(url)
        return host in self._auto_allow or host in self._approved

    def request(self, url: str, reason: str = "") -> bool:
        host = _normalize_host(url)
        if host in self._auto_allow or host in self._approved:
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

    @property
    def approved_hosts(self) -> List[str]:
        return sorted(self._approved | self._auto_allow)


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
    ):
        self.scrubber = scrubber
        self.gate = gate
        self.provider = provider
        self.wiki = wiki
        self.site = site
        self.max_page_chars = max_page_chars
        self.max_fetches = max_fetches

        self._fetch_count = 0
        self._sources: List[str] = []
        self._dossier_parts: List[str] = []
        self._page_cache: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #

    def web_search(self, query: str, max_results: int = 5) -> dict:
        hits = self.scrubber.find_personal_info(query)
        if hits:
            self._block("web_search", query, hits)
            return {"status": "blocked",
                    "reason": "The query contained the candidate's personal information and was not "
                              "executed. Search only for the company; never for a person."}

        results = self.provider.search(query, max_results=max_results)
        logger.info(f"web_search '{query}' -> {[r.url for r in results]}")

        if not results:
            return {"status": "empty", "query": query}
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

    def wikipedia_page(self, title: str) -> dict:
        hits = self.scrubber.find_personal_info(title)
        if hits:
            self._block("wikipedia_page", title, hits)
            return {"status": "blocked",
                    "reason": "The title contained the candidate's personal information and was not "
                              "looked up."}

        if self._fetch_count >= self.max_fetches:
            return {"status": "budget_exceeded",
                    "message": f"Fetch budget of {self.max_fetches} pages used up. Summarise what you have."}

        resolved = self.wiki.resolve_title(title)
        if not resolved:
            suggestions = self.wiki.search_titles(title)
            return {"status": "not_found", "title": title, "suggestions": suggestions}

        url = f"https://{self.wiki.language}.wikipedia.org/wiki/{resolved.replace(' ', '_')}"
        print(f"  -> fetching {url}  (host {_normalize_host(url)} already approved)")

        text = self.wiki.extract_page_text(resolved, auto_resolve=False)
        if not text:
            print("     error: no content retrieved")
            return {"status": "not_found", "title": title, "suggestions": []}

        scrubbed = self.scrubber.scrub(text, min_length=3)
        truncated = len(scrubbed) > self.max_page_chars
        scrubbed = scrubbed[:self.max_page_chars]

        self._fetch_count += 1
        self._sources.append(url)
        self._dossier_parts.append(f"=== PAGE: {url} ===\n{scrubbed}")
        print(f"     ok, {len(scrubbed)} chars" + (" (truncated)" if truncated else ""))

        return {"status": "ok", "title": resolved, "text": scrubbed, "truncated": truncated}

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
                                "its Wikipedia article. Only search for the company; never for a person."),
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
                "description": "Fetch the plain text of a Wikipedia article by title. Resolves near-misses via Wikipedia search.",
                "parameters": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
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
    toolbox = ResearchToolbox(
        scrubber=PersonalInfoScrubber({}),
        gate=HostApprovalGate(),
        provider=get_search_provider(),
        wiki=WikipediaScraper(),
        site=CompanyWebsiteScraper(),
    )

    print(json.dumps(toolbox.dispatch("web_search", {"query": f"{company} official website"}), indent=2)[:2000])
    print(json.dumps(toolbox.dispatch("wikipedia_page", {"title": company}), indent=2)[:2000])
    print("\nSources:", toolbox.sources)
