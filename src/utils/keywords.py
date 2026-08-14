"""
Local, deterministic keyword matching between a job description and a CV.

This is intentionally not an LLM call: it complements the LLM-produced
``compatibility_score`` with a reproducible, auditable figure (an approximation
of how a literal ATS keyword filter would see the CV) - see the "Keyword Match"
section of ``missing_skills.txt`` written by
``src/utils/file_handler.py:save_missing_skills()``.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import List


TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.]*")

# Generic HR/job-ad filler that is never itself a skill or requirement.
BOILERPLATE = {
    "team", "teams", "work", "working", "experience", "experienced", "role",
    "roles", "candidate", "candidates", "company", "companies", "opportunity",
    "opportunities", "applicant", "applicants", "benefits", "salary",
    "position", "positions", "job", "jobs", "we", "you", "our", "your",
    "the", "and", "or", "for", "with", "will", "are", "is", "as", "to",
    "of", "in", "on", "a", "an", "be", "have", "has", "this", "that",
    "about", "who", "what", "all", "new", "us", "including", "etc",
    "years", "year", "strong", "good", "excellent", "ability", "skills",
    "skill", "knowledge", "understanding", "looking", "join",
}

STOPWORDS_EN = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "so",
    "of", "in", "on", "at", "by", "for", "with", "about", "as", "to",
    "from", "into", "over", "after", "before", "between", "through",
    "we", "you", "your", "our", "us", "they", "their", "it", "its",
    "is", "are", "was", "were", "be", "been", "being", "have", "has",
    "had", "do", "does", "did", "will", "would", "should", "could",
    "can", "may", "might", "must", "this", "that", "these", "those",
    "not", "no", "yes", "all", "any", "each", "other", "some", "such",
}

STOPWORDS_DE = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen",
    "einem", "einer", "eines", "und", "oder", "aber", "wenn", "als",
    "wir", "sie", "ihr", "ihre", "ihren", "ihrem", "uns", "du", "es",
    "ist", "sind", "war", "waren", "sein", "haben", "hat", "hatte",
    "werden", "wird", "wurde", "kann", "koennen", "muss", "muessen",
    "mit", "bei", "von", "zu", "zur", "zum", "auf", "fuer", "im", "in",
    "am", "an", "aus", "nach", "ueber", "unter", "durch", "auch",
    "nicht", "kein", "keine", "alle", "jede", "jeder", "jedes",
}

_SUFFIXES = ("es", "en", "e", "s")

# Mirrors Generator_Handler._humanize_text()'s translation table, so CV text
# (already humanized) and raw job-ad text (never humanized) normalize the same way.
_TRANSLATION_TABLE = {
    0x2014: ",",    # em dash
    0x2013: "-",    # en dash
    0x2011: "-",    # non-breaking hyphen
    0x2026: "...",  # ellipsis
    0x201C: '"',    # left double quote
    0x201D: '"',    # right double quote
    0x2018: "'",    # left single quote
    0x2019: "'",    # right single quote
    0x00A0: " ",    # NBSP
}


@dataclass
class KeywordMatchResult:
    keywords: List[str] = field(default_factory=list)
    matched: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    percentage: int = 0


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return text.lower().translate(_TRANSLATION_TABLE)


class KeywordMatcher:
    """Extracts candidate keywords from a job description and checks which of
    them literally appear in a CV.
    """

    def __init__(self, language: str = "en", max_keywords: int = 40, min_length: int = 3) -> None:
        self.language = language
        self.max_keywords = max_keywords
        self.min_length = min_length
        self._stopwords = STOPWORDS_EN | (STOPWORDS_DE if language == "de" else set())

    def _tokenize(self, text: str) -> List[str]:
        # rstrip trailing "." - TOKEN_RE allows "." mid-token (for "node.js", "3.12",
        # "C++"-style tokens) but a trailing "." is sentence punctuation, not part
        # of the word (e.g. "...with Python." must yield "python", not "python.").
        tokens = (t.rstrip(".") for t in TOKEN_RE.findall(_normalize(text)))
        return [t for t in tokens if t]

    def _is_candidate(self, token: str) -> bool:
        if len(token) < self.min_length:
            return False
        if token in self._stopwords or token in BOILERPLATE:
            return False
        if token.isdigit():
            return False
        return True

    def extract_keywords(self, job_description: str) -> List[str]:
        """Rank job-description tokens/phrases by frequency, filtering out
        stopwords and boilerplate. Unigrams and adjacent-word bigrams (when
        both halves survive filtering) are both considered, so multi-word
        terms like "machine learning" count as a single keyword.
        """
        tokens = self._tokenize(job_description)

        counts: dict = {}
        first_seen: dict = {}
        for i, token in enumerate(tokens):
            if not self._is_candidate(token):
                continue
            counts[token] = counts.get(token, 0) + 1
            first_seen.setdefault(token, i)

            if i + 1 < len(tokens) and self._is_candidate(tokens[i + 1]):
                bigram = f"{token} {tokens[i + 1]}"
                counts[bigram] = counts.get(bigram, 0) + 1
                first_seen.setdefault(bigram, i)

        ranked = sorted(counts, key=lambda k: (-counts[k], first_seen[k]))
        return ranked[: self.max_keywords]

    def _keyword_pattern(self, keyword: str) -> re.Pattern:
        parts = [re.escape(word) for word in keyword.split(" ")]
        # Allow trailing plural/German suffixes on each word of the keyword.
        parts = [rf"{part}(?:{'|'.join(_SUFFIXES)})?" for part in parts]
        # \b relies on a \w/\W transition, which never fires when the keyword's
        # own edge is a symbol (e.g. "c++", "c#") since both sides are then \W.
        # Check alnum-adjacency explicitly instead so those keywords still match.
        boundary_start = r"(?<![a-z0-9])"
        boundary_end = r"(?![a-z0-9])"
        return re.compile(boundary_start + r"\s+".join(parts) + boundary_end)

    def analyse(self, job_description: str, cv_text: str) -> KeywordMatchResult:
        keywords = self.extract_keywords(job_description)
        if not keywords:
            return KeywordMatchResult()

        normalized_cv = _normalize(cv_text)
        matched, missing = [], []
        for keyword in keywords:
            pattern = self._keyword_pattern(keyword)
            (matched if pattern.search(normalized_cv) else missing).append(keyword)

        percentage = round(100 * len(matched) / len(keywords))
        return KeywordMatchResult(
            keywords=keywords, matched=matched, missing=missing, percentage=percentage
        )
