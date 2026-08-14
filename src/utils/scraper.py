import requests
import time
import logging
import sys
from typing import Optional, Dict, List, Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
import wikipediaapi

# Configure logger
logger = logging.getLogger(__name__)

# Identifies this bot to robots.txt and to Wikipedia's API (which requires a
# descriptive UA, not a spoofed browser string, per its API etiquette policy).
ROBOTS_USER_AGENT = "JobApplicationBot"

# Used only for fetching regular HTML pages, where a browser UA avoids sites
# that block unrecognised clients outright.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/91.0.4472.124 Safari/537.36"
)

# Aggregators/listings that are never the company's own site.
_NON_OFFICIAL_HOSTS = (
    "linkedin.com", "glassdoor.", "indeed.", "crunchbase.com", "bloomberg.com",
    "wikipedia.org", "facebook.com", "x.com", "twitter.com", "kununu.com",
    "xing.com", "youtube.com", "instagram.com",
)


class WikipediaScraper:
    """
    Scraper for extracting company information from Wikipedia pages.
    """

    def __init__(self, user_agent: str = None, language: str = 'en'):  # type: ignore
        """
        Initialize the Wikipedia scraper with optional user agent and language.

        Args:
            user_agent: Custom user agent string
            language: Wikipedia language edition to use (e.g., 'en', 'de', 'fr')
        """
        logger.info(f"Initializing Wikipedia scraper for language: {language}")
        self.language = language
        self.user_agent = user_agent or ROBOTS_USER_AGENT
        self.wikipedia = wikipediaapi.Wikipedia(
            language=language,
            extract_format=wikipediaapi.ExtractFormat.WIKI,
            user_agent=self.user_agent
        )
        logger.info("Wikipedia scraper initialized successfully")

    def search_titles(self, query: str, limit: int = 5) -> List[str]:
        """Resolve candidate article titles via the MediaWiki opensearch API.

        ``wikipedia-api`` has no search endpoint of its own, so this hits the
        REST API directly with a small, keyless GET request.
        """
        url = f"https://{self.language}.wikipedia.org/w/api.php"
        params = {
            "action": "opensearch",
            "search": query,
            "limit": limit,
            "namespace": 0,
            "format": "json",
        }
        try:
            resp = requests.get(url, params=params, timeout=10,
                                headers={"User-Agent": ROBOTS_USER_AGENT})
            resp.raise_for_status()
            data = resp.json()
            titles = data[1] if len(data) > 1 else []
            logger.info(f"Wikipedia opensearch for '{query}' -> {titles}")
            return titles
        except Exception as exc:
            logger.warning(f"Wikipedia opensearch failed for '{query}': {exc}")
            return []

    def resolve_title(self, company_name: str) -> Optional[str]:
        """Return the best-guess article title for *company_name*.

        Tries an exact-title lookup first (cheap, no network call beyond the
        page fetch itself); falls back to the first opensearch hit that shares
        a token with the company name.
        """
        page = self.wikipedia.page(company_name)
        if page.exists():
            return company_name

        candidates = self.search_titles(company_name)
        if not candidates:
            return None

        name_tokens = {t.lower() for t in company_name.split()}
        for title in candidates:
            title_tokens = {t.lower() for t in title.split()}
            if name_tokens & title_tokens:
                return title
        return candidates[0]

    def extract_page_text(self, wikipedia_page_name: str, auto_resolve: bool = True) -> str:
        """
        Extract the text of a Wikipedia page.

        Args:
            wikipedia_page_name: Name of the Wikipedia page to fetch
            auto_resolve: If the exact title misses, try to resolve a close
                match via :meth:`resolve_title` before giving up.

        Returns:
            Raw text content of the Wikipedia page, or empty string if not found
        """
        logger.info(f"Extracting Wikipedia page: {wikipedia_page_name}")
        page = self.wikipedia.page(wikipedia_page_name)
        if not page.exists() and auto_resolve:
            resolved = self.resolve_title(wikipedia_page_name)
            if resolved and resolved != wikipedia_page_name:
                logger.info(f"Resolved '{wikipedia_page_name}' -> '{resolved}'")
                page = self.wikipedia.page(resolved)

        if not page.exists():
            logger.error(f"No Wikipedia page found for: {wikipedia_page_name}")
            return ""

        logger.debug(f"Wikipedia page found: {wikipedia_page_name}")

        try:
            page_text = page.text
            logger.info(f"Successfully extracted Wikipedia page text. Length: {len(page_text)} characters")
            return page_text
        except Exception as e:
            logger.error(f"Error extracting Wikipedia page text: {e}")
            return ""


class CompanyWebsiteScraper:
    """Scraper for extracting text from a company's official website.

    This scraper:
    - Extracts visible text and internal links from provided URLs.
    - Works only with company name and URLs (no job or personal data).
    - Respects robots.txt and paces requests per host.
    """

    def __init__(
        self,
        user_agent: Optional[str] = None,
        timeout: int = 15,
        request_delay: float = 1.0,
        max_retries: int = 3,
    ):
        self.user_agent = user_agent or BROWSER_USER_AGENT
        self.timeout = timeout
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})
        self._robots_cache: Dict[str, Optional[RobotFileParser]] = {}
        self._last_request: Dict[str, float] = {}

    def _wait_for_host(self, host: str) -> None:
        elapsed = time.monotonic() - self._last_request.get(host, 0.0)
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last_request[host] = time.monotonic()

    def _get(self, url: str) -> Optional[str]:
        """Perform an HTTP GET request with retries, backoff and per-host pacing.

        Args:
            url: The URL to fetch.

        Returns:
            HTML text if successful, None otherwise.
        """
        if not self._is_allowed_by_robots(url):
            logger.warning(f"Skipping {url} due to robots.txt restrictions.")
            return None

        host = urlparse(url).netloc

        for attempt in range(self.max_retries):
            try:
                self._wait_for_host(host)
                resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
                if resp.status_code == 200:
                    content_type = resp.headers.get("Content-Type", "")
                    if "text/html" not in content_type and "text/plain" not in content_type:
                        logger.warning(f"Skipping non-HTML response from {url} (Content-Type: {content_type})")
                        return None
                    final_host = urlparse(resp.url).netloc
                    if final_host and final_host != host:
                        logger.warning(f"{url} redirected to a different host ({final_host}); discarding.")
                        return None
                    return resp.text
                logger.warning(f"GET {url} -> {resp.status_code} (Attempt {attempt + 1}/{self.max_retries})")
            except requests.exceptions.RequestException as exc:
                logger.warning(f"Error fetching {url}: {exc} (Attempt {attempt + 1}/{self.max_retries})")
            except Exception as exc:
                logger.error(f"Unexpected error fetching {url}: {exc}")
                break
            if attempt + 1 < self.max_retries:
                time.sleep(self.request_delay * (2 ** attempt))
        return None

    def _is_allowed_by_robots(self, url: str) -> bool:
        """Check if the URL is allowed by robots.txt for ROBOTS_USER_AGENT.

        Explicit refusal (401/403 on robots.txt itself) fails closed; a missing
        robots.txt (404) or a network error stays lenient.
        """
        parsed = urlparse(url)
        host = parsed.netloc.lower()

        if host in self._robots_cache:
            rp = self._robots_cache[host]
            return True if rp is None else rp.can_fetch(ROBOTS_USER_AGENT, url)

        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            logger.info(f"Checking robots.txt at {robots_url}")
            resp = requests.get(robots_url, timeout=10, headers={"User-Agent": ROBOTS_USER_AGENT})

            if resp.status_code in (401, 403):
                logger.warning(f"robots.txt at {robots_url} is protected ({resp.status_code}); treating site as disallowed.")
                self._robots_cache[host] = None
                return False

            if resp.status_code != 200:
                logger.info(f"No robots.txt found at {robots_url}, proceeding.")
                self._robots_cache[host] = None
                return True

            rp = RobotFileParser()
            rp.set_url(robots_url)
            rp.parse(resp.text.splitlines())
            self._robots_cache[host] = rp

            allowed = rp.can_fetch(ROBOTS_USER_AGENT, url)
            if not allowed:
                logger.warning(f"URL {url} is disallowed by robots.txt for user agent '{ROBOTS_USER_AGENT}'.")
            return allowed
        except requests.exceptions.RequestException as exc:
            logger.warning(f"Could not fetch {robots_url}: {exc}; proceeding.")
            self._robots_cache[host] = None
            return True

    def find_official_website(self, company_name: str, provider: Any = None) -> Optional[str]:
        """
        Get the official website URL for a company.

        Args:
            company_name: Name of the company to search for.
            provider: Optional SearchProvider (see src.utils.search). When given,
                search results are ranked and the best same-company match is
                returned automatically. Falls back to a manual prompt when no
                provider is given or the search yields nothing plausible.

        Returns:
            URL of the official website if found/provided, None otherwise.
        """
        if provider is not None:
            homepage = self._search_official_website(company_name, provider)
            if homepage:
                logger.info(f"Found likely official website via search: {homepage}")
                return homepage
            logger.warning(f"Search did not find a confident official website for: {company_name}")

        official_website = input(f"Please enter the official website URL for '{company_name}' (or press Enter to skip): ").strip()

        if not official_website:
            logger.warning(f"No official website provided for company: {company_name}")
            return None

        if not (official_website.startswith("http://") or official_website.startswith("https://")):
            official_website = f"https://{official_website}"

        logger.info(f"Using provided official website: {official_website}")
        return official_website

    def _search_official_website(self, company_name: str, provider: Any) -> Optional[str]:
        results = provider.search(f"{company_name} official website", max_results=8)
        name_tokens = {t.lower().strip(".,") for t in company_name.split() if len(t) > 2}

        best_url = None
        best_score = -1.0
        for rank, result in enumerate(results):
            host = urlparse(result.url).netloc.lower()
            if not host or any(bad in host for bad in _NON_OFFICIAL_HOSTS):
                continue
            registrable = host.split(".")[-2] if "." in host else host
            token_hits = sum(1 for t in name_tokens if t in registrable)
            path_depth = urlparse(result.url).path.count("/")
            score = (token_hits * 10) - rank - (path_depth * 0.5)
            if score > best_score:
                best_score = score
                best_url = result.url

        if not best_url or best_score < 0:
            return None
        parsed = urlparse(best_url)
        return f"{parsed.scheme}://{parsed.netloc}/"

    def extract_text_and_links(self, url: str) -> Dict[str, Any]:
        """Extract visible text and internal links from a provided URL.

        Args:
            url: The URL to extract text from.

        Returns:
            A dictionary with:
            - text: Extracted visible text.
            - links: List of dictionaries containing URLs and their anchor text.
        """
        if not url:
            logger.warning("URL is empty or None.")
            return {"text": "", "links": []}

        html = self._get(url)
        if not html:
            logger.warning(f"Failed to fetch HTML content from {url}")
            return {"text": "", "links": []}

        try:
            soup = BeautifulSoup(html, "html.parser")

            # Remove script/style/noscript
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()

            # Extract visible text
            text = " ".join(soup.stripped_strings)
            logger.info(f"Extracted {len(text)} characters of text from {url}")

            # Collect internal links
            base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
            links: List[Dict[str, str]] = []
            for a in soup.find_all("a"):
                href = a.get("href")
                if not href:
                    continue
                absolute = urljoin(base, href)
                parsed = urlparse(absolute)
                # Only keep same-domain HTTP(S) links
                if parsed.scheme not in ("http", "https"):
                    continue
                if parsed.netloc != urlparse(base).netloc:
                    continue
                anchor = " ".join(a.stripped_strings)
                links.append({"url": absolute, "anchor": anchor})

            logger.info(f"Extracted {len(links)} internal links from {url}")
            return {"text": text, "links": links}
        except Exception as exc:
            logger.error(f"Error extracting text and links from {url}: {exc}")
            return {"text": "", "links": []}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print("Usage:\n  python -m src.utils.scraper --robots <url>\n  python -m src.utils.scraper --wiki <company name>")
        sys.exit(1)

    mode = sys.argv[1]
    arg = " ".join(sys.argv[2:])

    if mode == "--robots":
        scraper = CompanyWebsiteScraper()
        print(f"Allowed: {scraper._is_allowed_by_robots(arg)}")
    elif mode == "--wiki":
        wiki = WikipediaScraper()
        title = wiki.resolve_title(arg)
        print(f"Resolved title: {title}")
        if title:
            text = wiki.extract_page_text(title)
            print(f"Fetched {len(text)} characters")
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)
