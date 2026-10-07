"""Extract reverse-split ratio and effective date from filing text."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "twenty-five": 25, "thirty": 30, "thirty-five": 35, "forty": 40,
    "forty-five": 45, "fifty": 50, "sixty": 60, "seventy": 70, "seventy-five": 75,
    "eighty": 80, "ninety": 90, "one hundred": 100, "one hundred fifty": 150,
    "two hundred": 200, "two hundred fifty": 250, "three hundred": 300,
    "five hundred": 500, "one thousand": 1000,
}
_WORD_ALT = "|".join(sorted((re.escape(w) for w in _WORDS), key=len, reverse=True))

_DIGIT_RATIO = re.compile(
    r"\b(\d{1,4}(?:,\d{3})?)\s*-?\s*for\s*-?\s*(\d{1,4}(?:,\d{3})?)\b"
    r"|\b(1)\s*:\s*(\d{1,4}(?:,\d{3})?)\b(?!\s*[ap]\.?m)",  # "1:20", not "1:30 p.m."
    re.I,
)
_WORD_RATIO = re.compile(rf"\b({_WORD_ALT})\s*-?\s*for\s*-?\s*({_WORD_ALT})\b", re.I)
_REVERSE = re.compile(r"reverse\s+(?:stock\s+|share\s+)?split|share\s+consolidation", re.I)

_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)
_DATE = rf"({_MONTHS})\s+(\d{{1,2}}),\s*(\d{{4}})"
_SPLIT_ADJUSTED = re.compile(rf"split[- ]adjusted basis.{{0,150}}?{_DATE}", re.I | re.S)
_EFFECTIVE = re.compile(rf"effective.{{0,150}}?{_DATE}", re.I | re.S)

WINDOW = 400


@dataclass(frozen=True)
class SplitText:
    ratio: int | None
    ratio_text: str
    effective_date: date | None


def _to_int(token: str) -> int | None:
    token = token.lower().replace(",", "").strip()
    if token.isdigit():
        return int(token)
    return _WORDS.get(token)


def _ratios_in(text: str) -> list[int]:
    found: list[int] = []
    for pattern in (_DIGIT_RATIO, _WORD_RATIO):
        for match in pattern.finditer(text):
            groups = [g for g in match.groups() if g is not None]
            new, old = _to_int(groups[0]), _to_int(groups[1])
            if new and old and old > new:  # reverse: fewer new shares than old
                ratio = round(old / new)
                if 2 <= ratio <= 5000:
                    found.append(ratio)
    return found


def _parse_date(match: re.Match) -> date | None:
    try:
        return datetime.strptime(f"{match.group(1)} {match.group(2)} {match.group(3)}", "%B %d %Y").date()
    except ValueError:
        return None


def parse_split_text(text: str) -> SplitText:
    """Find ratios near 'reverse split' mentions. Ranges return the max ratio."""
    windows = [
        text[max(0, m.start() - WINDOW): m.end() + WINDOW] for m in _REVERSE.finditer(text)
    ]
    ratios = [r for w in windows for r in _ratios_in(w)]

    unique = sorted(set(ratios))
    if not unique:
        ratio, ratio_text = None, ""
    elif len(unique) == 1:
        ratio, ratio_text = unique[0], f"1:{unique[0]}"
    else:
        ratio, ratio_text = unique[-1], f"rango 1:{unique[0]} - 1:{unique[-1]}"

    # "split-adjusted basis" is specific enough to search the whole text;
    # a generic "effective ... <date>" only counts near a reverse split mention.
    return SplitText(ratio, ratio_text, _first_date(_SPLIT_ADJUSTED, [text]) or _first_date(_EFFECTIVE, windows))


def _first_date(pattern: re.Pattern, texts: list[str]) -> date | None:
    for chunk in texts:
        for match in pattern.finditer(chunk):
            found = _parse_date(match)
            if found:
                return found
    return None
