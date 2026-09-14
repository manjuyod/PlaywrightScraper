"""Validated display metadata shared by collectors, storage and the UI."""

from __future__ import annotations

from decimal import Decimal
import re
from typing import Literal


AssignmentCategory = Literal["formative", "summative"]
_NUMBER = r"[0-9]+(?:\.[0-9]+)?"
_SCORE_LABEL = re.compile(r"^score\b\s*:?\s*", re.IGNORECASE)
_FRACTION = re.compile(
    rf"(?P<earned>{_NUMBER})\s*/\s*(?P<possible>{_NUMBER})"
    rf"(?:\s*\(\s*{_NUMBER}\s*%\s*\))?"
)
_PERCENTAGE = re.compile(rf"(?P<percent>{_NUMBER})\s*%")


def _decimal_display(raw: str) -> str:
    value = format(Decimal(raw), "f")
    return value.rstrip("0").rstrip(".") if "." in value else value


def normalize_assignment_score(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 128:
        return None
    text = _SCORE_LABEL.sub("", " ".join(value.split()), count=1)
    fraction = _FRACTION.fullmatch(text)
    if fraction is not None:
        if Decimal(fraction["possible"]) <= 0:
            return None
        score = f"{_decimal_display(fraction['earned'])}/{_decimal_display(fraction['possible'])}"
    else:
        percentage = _PERCENTAGE.fullmatch(text)
        if percentage is None:
            return None
        score = f"{_decimal_display(percentage['percent'])}%"
    return score if len(score) <= 32 else None


def normalize_assignment_category(value: object) -> AssignmentCategory | None:
    if not isinstance(value, str) or len(value) > 128:
        return None
    text = " ".join(value.split()).casefold()
    if text == "formative":
        return "formative"
    if text == "summative":
        return "summative"
    return None
