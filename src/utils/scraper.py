import re
import requests
import time
import logging
import sys
import unicodedata
from typing import Optional, Dict, List, Any, NamedTuple, Tuple
from urllib.parse import urljoin, urlparse, parse_qs, quote, unquote
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

# Aggregators/listings that are never the company's own site. Matched by
# exact host or dotted suffix (see _is_non_official), not raw substring.
_NON_OFFICIAL_HOSTS = (
    "linkedin.com", "glassdoor.com", "glassdoor.de", "indeed.com", "indeed.de",
    "crunchbase.com", "bloomberg.com", "wikipedia.org", "facebook.com", "x.com",
    "twitter.com", "kununu.com", "xing.com", "youtube.com", "instagram.com",
)

# Legal forms and generic corporate nouns/articles that dilute name matching
# ("VNR Group" vs "VR Group" must NOT match on the word "Group"). Deliberately
# excludes words that carry real identity, e.g. "verlag", "bank", "media",
# "systems", "works", "labs", "industries", "energy".
_LEGAL_FORM_STOPWORDS = {
    "ag", "gmbh", "mbh", "kg", "kgaa", "se", "ohg", "gbr", "eg", "ev",
    "plc", "ltd", "limited", "llc", "llp", "lp", "inc", "incorporated",
    "corp", "corporation", "co", "nv", "bv", "sa", "sas", "sarl", "srl",
    "spa", "oy", "oyj", "ab", "asa", "aps", "pty", "pte", "kk", "zrt",
    "group", "gruppe", "groupe", "groep", "holding", "holdings",
    "company", "companies", "international", "global", "worldwide",
    "the", "der", "die", "das", "den", "dem", "des", "und", "and",
    "of", "for", "et", "la", "le", "les",
}

# Wikidata "instance of" (P31) values that mean "this article is about an
# organisation" - the anchor of the entity check that stops a same-named but
# unrelated article (a Go opening, a person, a village, ...) from being
# accepted as a company's Wikipedia source. Deliberately broad across sectors
# rather than an exhaustive taxonomy; a P279 ("subclass of") hop from a more
# specific P31 value is also accepted (see classify_article()).
_WIKIDATA_ORG_TYPES = {
    "Q4830453",   # business
    "Q6881511",   # enterprise
    "Q783794",    # company
    "Q891723",    # public company
    "Q167037",    # corporation
    "Q43229",     # organization
    "Q163740",    # nonprofit organization
    "Q3918",      # university
    "Q31855",     # research institute
    "Q327333",    # government agency
    "Q18388277",  # technology company
    "Q1058914",   # software company
    "Q4830453",   # (dup kept for readability of the list above)
    "Q431289",    # brand
    "Q2401749",   # public institution
    "Q1616075",   # startup company
    "Q15911314",  # corporate group
    "Q1057304",   # holding company
}

# Category-title fallback when Wikidata is unreachable or the article has no
# wikibase item. Deliberately looser than _WIKIDATA_ORG_TYPES since a title
# alone can't distinguish sectors.
_ORG_CATEGORY_RE = re.compile(
    r"compan|corporat|business|enterprise|manufactur|employers|brands|"
    r"subsidiar|unternehmen|hersteller|firmen|arbeitgeber|dienstleist|"
    r"bank|verlag|organi[sz]ation",
    re.IGNORECASE,
)

# Two-part public suffixes where the registrable label sits one segment
# further left than a naive host.split(".")[-2] would find.
_MULTI_PART_SUFFIXES = {
    "co.uk", "com.au", "co.nz", "co.jp", "ne.jp", "or.jp", "com.br",
    "co.za", "com.tr", "co.in", "com.mx", "com.cn", "com.sg", "co.kr",
    "com.hk", "co.il", "com.ar", "com.pl", "co.at", "ac.uk", "org.uk",
    "com.ua", "com.my", "co.th", "com.tw", "co.id",
}


def _registrable_label(host: str) -> str:
    """The brand label of *host*, honouring two-part public suffixes."""
    parts = host.lower().lstrip(".").split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTI_PART_SUFFIXES:
        return parts[-3]
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {
        "co", "com", "net", "org", "ac", "gov", "edu", "or", "ne", "gr"
    }:
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else parts[-1]


def _is_non_official(host: str) -> bool:
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == bad or host.endswith("." + bad) for bad in _NON_OFFICIAL_HOSTS)


def _claim_qids(claims: dict, prop: str) -> List[str]:
    """Q-ids of a wikibase-item claim, e.g. claims["P31"] -> ["Q4830453", ...]."""
    ids = []
    for statement in claims.get(prop, []) or []:
        value = (statement.get("mainsnak", {}) or {}).get("datavalue", {}).get("value")
        if isinstance(value, dict) and value.get("id"):
            ids.append(value["id"])
    return ids


def _claim_url(claims: dict, prop: str) -> Optional[str]:
    """First plain-string value of a url-typed claim, e.g. claims["P856"]."""
    for statement in claims.get(prop, []) or []:
        value = (statement.get("mainsnak", {}) or {}).get("datavalue", {}).get("value")
        if isinstance(value, str) and value:
            return value
    return None


_WIKI_HOST_RE = re.compile(r"^(?P<lang>[a-z][a-z0-9-]{1,11})\.(?:m\.)?wikipedia\.org$")
_NON_ARTICLE_NAMESPACES = {
    "special", "file", "image", "category", "help", "talk", "portal",
    "template", "wikipedia", "user", "media", "draft",
    "datei", "kategorie", "hilfe", "diskussion", "spezial", "vorlage", "benutzer",
}


def _fold(text: str) -> str:
    """Lowercase and normalise *text* for loose comparison (accents, ß, &)."""
    text = text.replace("ß", "ss").replace("&", " and ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def tokenize_name(text: str) -> List[str]:
    """Fold and split *text* into alphanumeric tokens, dropping 1-char tokens."""
    return [t for t in re.split(r"[^a-z0-9]+", _fold(text)) if len(t) > 1]


def significant_tokens(name: str) -> List[str]:
    """tokenize_name() with legal-form/generic-corporate stopwords removed.

    Falls back to the unfiltered token list when stopwording would remove
    everything (e.g. a company literally named "The Group").
    """
    tokens = tokenize_name(name)
    filtered = [t for t in tokens if t not in _LEGAL_FORM_STOPWORDS]
    return filtered or tokens


def title_matches_company(company_name: str, title: str, *, min_coverage: float = 1.0) -> bool:
    """True when *title* plausibly names the same entity as *company_name*.

    Requires the company's anchor token (its first significant token, usually
    the brand word) to appear among the title's tokens, plus at least
    *min_coverage* of the remaining significant tokens. Legal-form and generic
    corporate words are ignored, so "Siemens AG" matches "Siemens" but
    "VNR Group" does NOT match "VR Group" (their only shared token is the
    stopword "group").
    """
    comp = significant_tokens(company_name)
    if not comp:
        return False
    title_tokens = set(tokenize_name(title))
    anchor = comp[0]
    if anchor not in title_tokens:
        # Cheap fallback: a single-token title that is exactly the initials
        # of the company's significant tokens (e.g. "BMW" for "Bayerische
        # Motoren Werke").
        if len(title_tokens) == 1:
            initials = "".join(t[0] for t in comp)
            if next(iter(title_tokens)) == initials:
                return True
        return False
    if len(comp) == 1:
        return True
    hits = sum(1 for t in comp[1:] if t in title_tokens)
    return hits / (len(comp) - 1) >= min_coverage - 1e-9


class WikiArticle(NamedTuple):
    title: str      # human-readable, percent-decoded, spaces not underscores
    language: str   # edition code: 'en', 'de', 'simple', ...
    url: str
    official_website: Optional[str] = None  # from Wikidata P856, or an extlinks fallback
    verified: bool = False  # True once classify_article() confirmed an organisation


class WikipediaScraper:
    """
    Scraper for resolving and extracting company information from Wikipedia
    pages, across language editions.
    """

    def __init__(self, user_agent: str = None, language: str = 'en',  # type: ignore
                 provider: Any = None, scrubber: Any = None):
        """
        Initialize the Wikipedia scraper.

        Args:
            user_agent: Custom user agent string.
            language: Preferred Wikipedia language edition (e.g., 'en', 'de').
            provider: Optional SearchProvider (see src.utils.search) used to
                locate the article's URL (and hence its edition) before
                falling back to the MediaWiki opensearch API.
            scrubber: Optional PersonalInfoScrubber guarding outbound queries.
        """
        logger.info(f"Initializing Wikipedia scraper for language: {language}")
        self.language = language
        self.user_agent = user_agent or ROBOTS_USER_AGENT
        self.provider = provider
        self.scrubber = scrubber
        self._clients: Dict[str, "wikipediaapi.Wikipedia"] = {}
        self._meta_cache: Dict[Tuple[str, str], Optional[dict]] = {}
        self._wd_cache: Dict[str, Optional[dict]] = {}
        self._last_api_call: Dict[str, float] = {}
        self.wikipedia = self._client(self.language)
        logger.info("Wikipedia scraper initialized successfully")

    def _api_get(self, host: str, params: dict, *, min_interval: float = 0.2) -> Optional[dict]:
        """GET on *host*'s api.php, throttled per host and retried once on a
        429 - the entity check (_page_meta + up to 6 _wikidata_claims calls
        per candidate article) is bursty enough to get rate-limited without
        this, which would otherwise surface as a false "unverifiable"."""
        elapsed = time.monotonic() - self._last_api_call.get(host, 0.0)
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        url = f"https://{host}/w/api.php"
        for attempt in range(2):
            self._last_api_call[host] = time.monotonic()
            try:
                resp = requests.get(url, params=params, timeout=10,
                                    headers={"User-Agent": self.user_agent})
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", 1.0))
                    logger.debug(f"{host} rate-limited us; retrying after {retry_after}s")
                    time.sleep(min(retry_after, 5.0))
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                logger.warning(f"Request to {host} failed: {exc}")
                return None
        return None

    def _client(self, language: Optional[str] = None) -> "wikipediaapi.Wikipedia":
        language = language or self.language
        if language not in self._clients:
            self._clients[language] = wikipediaapi.Wikipedia(
                language=language,
                extract_format=wikipediaapi.ExtractFormat.WIKI,
                user_agent=self.user_agent,
            )
        return self._clients[language]

    def _guard(self, value: str, what: str) -> bool:
        """False (and a logger.critical) when *value* carries personal info."""
        if self.scrubber is None:
            return True
        hits = self.scrubber.find_personal_info(value)
        if hits:
            logger.critical(f"PRIVACY: blocked outbound {what} containing personal information: {hits}")
            return False
        return True

    @staticmethod
    def _article_url(language: str, title: str) -> str:
        return f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='_():')}"

    @staticmethod
    def parse_article_url(url: str) -> Optional[Tuple[str, str]]:
        """Return (language, title) from a Wikipedia article URL, else None."""
        try:
            parsed = urlparse(url)
        except ValueError:
            return None
        match = _WIKI_HOST_RE.match(parsed.netloc.lower())
        if not match:
            return None
        language = match.group("lang")
        if language == "www":
            return None

        title = None
        if parsed.path.startswith("/wiki/"):
            title = parsed.path[len("/wiki/"):]
        elif parsed.path in ("/w/index.php", "/w/index.php/"):
            title = parse_qs(parsed.query).get("title", [None])[0]
        if not title:
            return None

        title = unquote(title).replace("_", " ").strip()
        if not title:
            return None
        namespace = title.split(":", 1)[0].strip().lower()
        if namespace in _NON_ARTICLE_NAMESPACES:
            return None
        return language, title

    def search_titles(self, query: str, limit: int = 5, language: Optional[str] = None) -> List[str]:
        """Resolve candidate article titles via the MediaWiki opensearch API."""
        if not self._guard(query, "opensearch query"):
            return []
        language = language or self.language
        params = {
            "action": "opensearch",
            "search": query,
            "limit": limit,
            "namespace": 0,
            "format": "json",
        }
        data = self._api_get(f"{language}.wikipedia.org", params)
        titles = data[1] if data and len(data) > 1 else []
        logger.info(f"Wikipedia opensearch for '{query}' -> {titles}")
        return titles

    def _is_disambiguation(self, title: str, text: str) -> bool:
        lowered = title.lower()
        if lowered.endswith("(disambiguation)") or "(begriffsklärung)" in lowered:
            return True
        if len(text) < 500 and re.search(
            r"\bmay refer to\b|\bsteht für\b|\bkann sich beziehen auf\b", text, re.IGNORECASE
        ):
            return True
        return False

    def _page_meta(self, language: str, title: str) -> Optional[dict]:
        """One MediaWiki call: redirect-resolved title, Wikidata Q-id, the
        disambiguation pageprop, and the article's categories. None on any
        failure or a missing page - callers must treat that as "unknown",
        never as "yes"."""
        if not self._guard(title, "page metadata query"):
            return None
        cache_key = (language, title)
        if cache_key in self._meta_cache:
            return self._meta_cache[cache_key]
        params = {
            "action": "query",
            "titles": title,
            "prop": "pageprops|categories",
            "redirects": 1,
            "cllimit": "max",
            "format": "json",
        }
        meta: Optional[dict] = None
        data = self._api_get(f"{language}.wikipedia.org", params)
        if data is not None:
            pages = (data.get("query", {}) or {}).get("pages", {}) or {}
            page = next(iter(pages.values()), {})
            if "missing" not in page:
                pageprops = page.get("pageprops", {}) or {}
                meta = {
                    "title": page.get("title", title),
                    "qid": pageprops.get("wikibase_item"),
                    "disambiguation": "disambiguation" in pageprops,
                    "categories": [c.get("title", "") for c in (page.get("categories", []) or [])],
                }
        self._meta_cache[cache_key] = meta
        return meta

    def _wikidata_claims(self, qid: Optional[str]) -> Optional[dict]:
        """Claims for a Wikidata Q-id. The outbound payload is a bare Q-id,
        so it carries no personal-info risk; still guarded for consistency."""
        if not qid or not self._guard(qid, "wikidata claims query"):
            return None
        if qid in self._wd_cache:
            return self._wd_cache[qid]
        params = {"action": "wbgetentities", "ids": qid, "props": "claims", "format": "json"}
        claims: Optional[dict] = None
        data = self._api_get("www.wikidata.org", params)
        if data is not None:
            entity = (data.get("entities", {}) or {}).get(qid, {}) or {}
            if "missing" not in entity:
                claims = entity.get("claims", {}) or {}
        self._wd_cache[qid] = claims
        return claims

    def _extlink_website(self, language: str, title: str, company_name: str) -> Optional[str]:
        """Fallback official website when Wikidata has no P856: the
        article's first external link that isn't a known aggregator and
        plausibly belongs to *company_name*, reusing the same host-scoring
        helpers CompanyWebsiteScraper._search_official_website() uses for
        search results."""
        if not self._guard(title, "extlinks query"):
            return None
        params = {"action": "query", "titles": title, "prop": "extlinks", "ellimit": "max", "format": "json"}
        data = self._api_get(f"{language}.wikipedia.org", params)
        if data is None:
            return None
        pages = (data.get("query", {}) or {}).get("pages", {}) or {}
        page = next(iter(pages.values()), {})
        links = [l.get("*", "") for l in (page.get("extlinks", []) or [])]

        tokens = significant_tokens(company_name) if company_name else []
        for link in links:
            if not link:
                continue
            if not link.startswith(("http://", "https://")):
                link = f"https://{link}"
            host = urlparse(link).netloc.lower()
            if not host or _is_non_official(host):
                continue
            if tokens and not any(t in _registrable_label(host) for t in tokens):
                continue
            parsed = urlparse(link)
            return f"{parsed.scheme}://{parsed.netloc}/"
        return None

    def classify_article(self, language: str, title: str,
                         company_name: str = "") -> Tuple[Optional[bool], Optional[str]]:
        """Tri-state check of whether *title* is about an organisation, plus
        its official website when one can be determined.

        Wikidata's P31 taxonomy for companies is deep and sector-specific
        (a railway operator's P31 is "railway company", a state-owned one is
        also "state-owned enterprise", neither of which is literally
        "business" or "company", and a one-hop P279 climb does not always
        reach a type on _WIKIDATA_ORG_TYPES either - Deutsche Bahn's actual
        Wikidata item needed exactly this). So a Wikidata miss does NOT
        override a category match - the two signals are OR'd, not chained
        as strict/then/fallback.

        Returns (is_organisation, official_website):
          - (False, None) - a disambiguation page, or both Wikidata and the
            categories agree it is not an organisation.
          - (True, url_or_None) - Wikidata or the categories say it is an
            organisation.
          - (None, None) - neither signal could be reached with confidence;
            callers must treat this as unverifiable, not as a match.
        """
        meta = self._page_meta(language, title)
        if meta is None:
            return None, None
        if meta["disambiguation"]:
            return False, None

        wikidata_says_org: Optional[bool] = None
        claims: Optional[dict] = None
        qid = meta.get("qid")
        if qid:
            claims = self._wikidata_claims(qid)
            if claims is not None:
                instance_of = _claim_qids(claims, "P31")
                if instance_of:
                    if any(q in _WIKIDATA_ORG_TYPES for q in instance_of):
                        wikidata_says_org = True
                    else:
                        wikidata_says_org = False
                        for parent_qid in instance_of[:5]:
                            parent_claims = self._wikidata_claims(parent_qid)
                            if parent_claims and any(
                                p in _WIKIDATA_ORG_TYPES for p in _claim_qids(parent_claims, "P279")
                            ):
                                wikidata_says_org = True
                                break
                # else: a real Wikidata item with no P31 at all - stays None, categories decide.

        if wikidata_says_org is True:
            website = (_claim_url(claims, "P856") if claims else None) \
                or self._extlink_website(language, title, company_name)
            return True, website

        categories = meta.get("categories", [])
        if any(_ORG_CATEGORY_RE.search(c) for c in categories):
            return True, self._extlink_website(language, title, company_name)

        if wikidata_says_org is False or len(categories) >= 3:
            return False, None
        return None, None

    def _accept(self, language: str, title: str, company_name: str, *,
                min_coverage: float = 1.0) -> Optional[WikiArticle]:
        """The single gate every candidate article passes through: the title
        must name the same company AND Wikidata/categories must confirm it
        is an organisation. Never guesses on an unverifiable entity type."""
        if not title_matches_company(company_name, title, min_coverage=min_coverage):
            return None
        if self._is_disambiguation(title, ""):
            return None
        is_org, website = self.classify_article(language, title, company_name)
        if is_org is not True:
            reason = "not an organisation" if is_org is False else "entity type could not be verified"
            logger.warning(f"Rejecting Wikipedia article {language}:{title} for '{company_name}': {reason}")
            return None
        return WikiArticle(title, language, self._article_url(language, title), website, True)

    def find_article_via_search(self, company_name: str, provider: Any = None) -> Optional[WikiArticle]:
        """Locate the company's Wikipedia article by searching the web for it."""
        provider = provider or self.provider
        if provider is None or not self._guard(company_name, "wikipedia search"):
            return None
        for query in (f"{company_name} wikipedia", f"{company_name} site:wikipedia.org"):
            for result in provider.search(query, max_results=8):
                parsed = self.parse_article_url(result.url)
                if not parsed:
                    continue
                lang, title = parsed
                article = self._accept(lang, title, company_name, min_coverage=0.5)
                if article:
                    logger.info(f"Wikipedia article for '{company_name}' found via search: {lang}:{title}")
                    return article
            if getattr(provider, "last_status", "ok") == "blocked":
                break
        return None

    def resolve_article(self, company_name: str, provider: Any = None,
                        language: Optional[str] = None) -> Optional[WikiArticle]:
        """Return the best-verified WikiArticle for *company_name*, or None.

        Never guesses: a candidate is only accepted by _accept(), which
        requires both title_matches_company() and classify_article() to
        confirm the entity is an organisation. Order: exact title in the
        preferred edition -> search-engine lookup (preferring the configured
        language via langlinks, but keeping a foreign-edition hit when there
        is no counterpart) -> opensearch fallback.
        """
        if not self._guard(company_name, "wikipedia lookup"):
            return None

        target_language = language or self.language
        client = self._client(target_language)
        page = client.page(company_name)
        if page.exists():
            article = self._accept(target_language, page.title, company_name, min_coverage=1.0)
            if article:
                return article

        found = self.find_article_via_search(company_name, provider)
        if found:
            if found.language != target_language:
                try:
                    origin_page = self._client(found.language).page(found.title)
                    links = origin_page.langlinks
                    hop = links.get(target_language)
                    if hop is not None:
                        hop_article = self._accept(target_language, hop.title, company_name, min_coverage=0.5)
                        if hop_article:
                            return hop_article
                        logger.warning(
                            f"Langlink hop {found.language}:{found.title} -> {target_language}:{hop.title} "
                            "did not pass verification; keeping the source-language article instead."
                        )
                except Exception as exc:
                    logger.debug(f"langlinks lookup failed for {found.language}:{found.title}: {exc}")
            return found

        candidates = self.search_titles(company_name, language=target_language)
        for title in candidates:
            article = self._accept(target_language, title, company_name, min_coverage=1.0)
            if article:
                return article

        return None

    def resolve_title(self, company_name: str) -> Optional[str]:
        """Deprecated: use resolve_article(). Returns a title only when the
        article lives in this scraper's own language edition."""
        article = self.resolve_article(company_name)
        if article is None:
            return None
        if article.language != self.language:
            logger.info(
                f"'{company_name}' resolved to the {article.language} edition; "
                "resolve_title() cannot express that - use resolve_article()."
            )
            return None
        return article.title

    def fetch_article(self, article: WikiArticle) -> str:
        """Fetch the plain text of a resolved WikiArticle."""
        page = self._client(article.language).page(article.title)
        if not page.exists():
            logger.error(f"No Wikipedia page found for: {article.language}:{article.title}")
            return ""
        try:
            page_text = page.text
            logger.info(f"Successfully extracted Wikipedia page text. Length: {len(page_text)} characters")
            return page_text
        except Exception as e:
            logger.error(f"Error extracting Wikipedia page text: {e}")
            return ""

    def extract_page_text(self, wikipedia_page_name: str, auto_resolve: bool = True,
                          language: Optional[str] = None) -> str:
        """
        Extract the text of a Wikipedia page.

        Args:
            wikipedia_page_name: Name of the Wikipedia page to fetch.
            auto_resolve: If the exact title misses, try to resolve a
                confidently-matching article via :meth:`resolve_article`.
            language: Edition to look in; defaults to self.language.

        Returns:
            Raw text content of the Wikipedia page, or empty string if not found.
        """
        logger.info(f"Extracting Wikipedia page: {wikipedia_page_name}")
        language = language or self.language
        page = self._client(language).page(wikipedia_page_name)
        if page.exists():
            article = self._accept(language, page.title, wikipedia_page_name, min_coverage=1.0)
            if article:
                return self.fetch_article(article)
            logger.warning(f"'{wikipedia_page_name}' exists but did not pass entity verification.")

        if auto_resolve:
            article = self.resolve_article(wikipedia_page_name, language=language)
            if article:
                logger.info(f"Resolved '{wikipedia_page_name}' -> '{article.language}:{article.title}'")
                return self.fetch_article(article)

        logger.error(f"No Wikipedia page found for: {wikipedia_page_name}")
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
        tokens = significant_tokens(company_name)

        best_url = None
        best_score = -1.0
        for rank, result in enumerate(results):
            host = urlparse(result.url).netloc.lower()
            if not host or _is_non_official(host):
                continue
            label = _registrable_label(host)
            host_hits = sum(1 for t in tokens if t in label)
            if host_hits == 0:
                continue
            title_hits = sum(1 for t in tokens if t in _fold(result.title))
            path_depth = urlparse(result.url).path.rstrip("/").count("/")
            score = (host_hits * 10) + (min(title_hits, 2) * 2) - rank - (path_depth * 0.5)
            if score > best_score:
                best_score = score
                best_url = result.url

        if not best_url:
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
        print(
            "Usage:\n"
            "  python -m src.utils.scraper --robots <url>\n"
            "  python -m src.utils.scraper --wiki <company name>\n"
            "  python -m src.utils.scraper --wiki-search <company name>\n"
            "  python -m src.utils.scraper --match \"<company>\" \"<title>\""
        )
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "--robots":
        arg = " ".join(sys.argv[2:])
        scraper = CompanyWebsiteScraper()
        print(f"Allowed: {scraper._is_allowed_by_robots(arg)}")
    elif mode == "--wiki":
        arg = " ".join(sys.argv[2:])
        wiki = WikipediaScraper()
        article = wiki.resolve_article(arg)
        print(f"Resolved article: {article}")
        if article:
            text = wiki.fetch_article(article)
            print(f"Fetched {len(text)} characters")
    elif mode == "--wiki-search":
        from src.utils.search import get_search_provider
        arg = " ".join(sys.argv[2:])
        provider = get_search_provider()
        wiki = WikipediaScraper(provider=provider)
        article = wiki.resolve_article(arg)
        print(f"Resolved article: {article}")
        if article:
            text = wiki.fetch_article(article)
            print(f"Fetched {len(text)} characters")
    elif mode == "--match":
        company, title = sys.argv[2], sys.argv[3]
        print(f"title_matches_company({company!r}, {title!r}) = {title_matches_company(company, title)}")
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)
