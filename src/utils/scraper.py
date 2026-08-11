import requests
import time
import logging
from typing import Optional, Dict, List, Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import wikipediaapi

# Configure logger
logger = logging.getLogger(__name__)


class WikipediaScraper:
    """
    Scraper for extracting company information from Wikipedia pages using LLM.
    
    Note: This scraper now requires manual input of Wikipedia page names.
    """
    
    def __init__(self, user_agent: str = None, language: str = 'en'):  # type: ignore
        """
        Initialize the Wikipedia scraper with optional user agent and language.
        
        Args:
            user_agent: Custom user agent string
            language: Wikipedia language edition to use (e.g., 'en', 'de', 'fr')
        """
        logger.info(f"Initializing Wikipedia scraper for language: {language}")
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.124 Safari/537.36"
        )
        self.wikipedia = wikipediaapi.Wikipedia(
            language=language,
            extract_format=wikipediaapi.ExtractFormat.WIKI,
            user_agent=self.user_agent
        )
        logger.info("Wikipedia scraper initialized successfully")
    
    def extract_page_text(self, wikipedia_page_name: str) -> str:
        """
        Extract company information from Wikipedia page using LLM.
        
        Args:
            wikipedia_page_name: Name of the Wikipedia page to fetch
            
        Returns:
            Raw text content of the Wikipedia page, or empty string if not found
        """
        logger.info(f"Extracting Wikipedia page: {wikipedia_page_name}")
        # Fetch Wikipedia page using the provided name
        page = self.wikipedia.page(wikipedia_page_name)
        if not page.exists():
            error_msg = f"No Wikipedia page found for: {wikipedia_page_name}"
            logger.error(error_msg)
            print(error_msg)
            return ""
            
        logger.debug(f"Wikipedia page found: {wikipedia_page_name}")
        
        try:
            page_text = page.text
            logger.info(f"Successfully extracted Wikipedia page text. Length: {len(page_text)} characters")
            return page_text
        except Exception as e:
            error_msg = f"Error extracting Wikipedia page text: {e}"
            logger.error(error_msg)
            return ""


class CompanyWebsiteScraper:
    """Scraper for extracting text from a company's official website.

    This scraper:
    - Extracts visible text and internal links from provided URLs.
    - Works only with company name and URLs (no job or personal data).
    
    Note: This scraper now requires manual input of the official website URL.
    """

    def __init__(
        self,
        user_agent: Optional[str] = None,
        timeout: int = 15,
        request_delay: float = 1.0,
        max_retries: int = 3,
    ):
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.124 Safari/537.36"
        )
        self.timeout = timeout
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.user_agent})

    def _get(self, url: str) -> Optional[str]:
        """Perform an HTTP GET request with retries and delay between requests.

        Args:
            url: The URL to fetch.

        Returns:
            HTML text if successful, None otherwise.
        """
        # Check robots.txt before fetching
        if not self._is_allowed_by_robots(url):
            logger.warning(f"Skipping {url} due to robots.txt restrictions.")
            return None

        for attempt in range(self.max_retries):
            try:
                time.sleep(self.request_delay)
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.text
                logger.warning(f"GET {url} -> {resp.status_code} (Attempt {attempt + 1}/{self.max_retries})")
            except requests.exceptions.RequestException as exc:
                logger.warning(f"Error fetching {url}: {exc} (Attempt {attempt + 1}/{self.max_retries})")
            except Exception as exc:
                logger.error(f"Unexpected error fetching {url}: {exc}")
                break
        return None

    def _is_allowed_by_robots(self, url: str) -> bool:
        """Check if the URL is allowed by robots.txt for the scraper's user agent.

        Args:
            url: The URL to check.

        Returns:
            True if allowed, False otherwise.
        """
        try:
            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            robots_url = f"{base_url}/robots.txt"

            logger.info(f"Checking robots.txt at {robots_url}")
            resp = requests.get(robots_url, timeout=10, headers={"User-Agent": ROBOTS_USER_AGENT}) #TODO
            if resp.status_code != 200:
                logger.info(f"No robots.txt found at {robots_url}, proceeding with caution.")
                return True

            # Parse robots.txt content
            from urllib.robotparser import RobotFileParser
            rp = RobotFileParser()
            rp.parse(resp.text.splitlines())

            # Check if the scraper's user agent is allowed
            allowed = rp.can_fetch(self.user_agent, url)
            if not allowed:
                logger.warning(f"URL {url} is disallowed by robots.txt for user agent '{ROBOTS_USER_AGENT}'.") #TODO
            return allowed
        except Exception as exc:
            logger.error(f"Error checking robots.txt for {url}: {exc}")
            return True  # Proceed with caution if robots.txt cannot be fetched

    def find_official_website(self, company_name: str) -> Optional[str]:
        """
        Get the official website URL for a company.
        
        Args:
            company_name: Name of the company to search for.
            
        Returns:
            URL of the official website if provided, None otherwise.
        """
        # Prompt user to manually enter the official website URL
        official_website = input(f"Please enter the official website URL for '{company_name}' (or press Enter to skip): ").strip()
        
        if not official_website:
            logger.warning(f"No official website provided for company: {company_name}")
            return None
        
        # Validate the URL format
        if not (official_website.startswith("http://") or official_website.startswith("https://")):
            official_website = f"https://{official_website}"
            
        logger.info(f"Using provided official website: {official_website}")
        return official_website

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
    