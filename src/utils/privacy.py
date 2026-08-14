"""
Privacy contract for the pipeline: the candidate's personal information must
never reach the LLM. This module is the single choke point for that check,
used both on the job description (existing behaviour) and on the web-research
tool paths (outbound query/URL guard, inbound page-text scrub).
"""

import re
import logging
from typing import Any, List

logger = logging.getLogger(__name__)


class PersonalInfoScrubber:
    """Collects every leaf string out of ``personal_info.json`` and removes it
    from arbitrary text, case-insensitively, including ``word_with_underscores``
    variants of multi-word values.
    """

    def __init__(self, personal_info: dict) -> None:
        strings = sorted(set(self._collect_strings(personal_info)), key=len, reverse=True)
        self._values: List[str] = [v for v in strings if v]
        self._patterns = [
            (value, self._build_pattern(value))
            for value in self._values
        ]
        logger.debug(f"PersonalInfoScrubber compiled {len(self._patterns)} pattern(s)")

    @staticmethod
    def _collect_strings(obj: Any) -> List[str]:
        strings: List[str] = []
        if isinstance(obj, dict):
            for v in obj.values():
                strings.extend(PersonalInfoScrubber._collect_strings(v))
        elif isinstance(obj, list):
            for item in obj:
                strings.extend(PersonalInfoScrubber._collect_strings(item))
        elif isinstance(obj, (str, int, float)):
            strings.append(str(obj))
        return strings

    @staticmethod
    def _build_pattern(value: str) -> List[re.Pattern]:
        patterns = [re.compile(re.escape(value), re.IGNORECASE)]
        if " " in value:
            parts = [re.escape(part) for part in value.split()]
            patterns.append(re.compile(r"[_\s-]+".join(parts), re.IGNORECASE))
        return patterns

    def scrub(self, text: str, min_length: int = 0) -> str:
        """Remove every matching personal-info value from *text*.

        ``min_length`` filters out short leaves (a house number, a 2-letter
        country code) that would otherwise shred unrelated prose. The default
        of 0 preserves the original, broader job-description behaviour.
        """
        cleaned = text
        replaced = 0
        for value, patterns in self._patterns:
            if len(value) < min_length:
                continue
            for pattern in patterns:
                cleaned, n = pattern.subn("", cleaned)
                replaced += n
        if replaced:
            logger.debug(f"Scrubbed {replaced} personal-info occurrence(s) (min_length={min_length})")
        return cleaned

    def find_personal_info(self, text: str, min_length: int = 3) -> List[str]:
        """Return the distinct personal-info values found in *text*."""
        hits = []
        for value, patterns in self._patterns:
            if len(value) < min_length:
                continue
            if any(p.search(text) for p in patterns):
                hits.append(value)
        return hits
