"""Helpers for eCH-0160 historical time spans (<von>/<bis> with <datum>)."""

import re
from datetime import date

NOT_SPECIFIED = "keine Angabe"

_DATE_RE = re.compile(r"^\s*(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")


def parse_lower(value: str | None) -> date | None:
    """Earliest day covered by a date value ("2006" -> 2006-01-01)."""
    m = _DATE_RE.match(value or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1))
    except ValueError:
        return None


def parse_upper(value: str | None) -> date | None:
    """Latest day covered by a date value ("2006" -> 2006-12-31)."""
    m = _DATE_RE.match(value or "")
    if not m:
        return None
    year = int(m.group(1))
    if not m.group(2):
        return date(year, 12, 31)
    month = int(m.group(2))
    try:
        if not m.group(3):
            first_of_next = date(year + (month == 12), month % 12 + 1, 1)
            return date.fromordinal(first_of_next.toordinal() - 1)
        return date(year, month, int(m.group(3)))
    except ValueError:
        return None


def is_open(value: str | None) -> bool:
    return not value or not value.strip() or value.strip().lower() == NOT_SPECIFIED.lower()


def format_span(start: str, end: str) -> str:
    if not start and not end:
        return ""
    return f"{start or NOT_SPECIFIED} - {end or NOT_SPECIFIED}"


_SWISS_DATE = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_YEAR = re.compile(r"^\d{4}$")
_CA_PREFIX = re.compile(r"^ca\.?\s*", re.IGNORECASE)


class DateFormatError(ValueError):
    pass


def to_xml_date(text: str) -> str:
    """User input (JJJJ, TT.MM.JJJJ, JJJJ-MM-TT, keine Angabe) to the XSD representation."""
    text = text.strip()
    if not text or text.lower() == NOT_SPECIFIED.lower():
        return NOT_SPECIFIED
    if _YEAR.match(text) or _ISO_DATE.match(text):
        candidate = text
    else:
        m = _SWISS_DATE.match(text)
        if not m:
            raise DateFormatError(f"Ungültige Zeitangabe «{text}» (erlaubt: JJJJ, TT.MM.JJJJ oder keine Angabe).")
        candidate = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    if len(candidate) == 10:
        try:
            date.fromisoformat(candidate)
        except ValueError as exc:
            raise DateFormatError(f"Ungültiges Datum «{text}».") from exc
    return candidate


def to_display_date(value: str) -> str:
    m = _ISO_DATE.match(value.strip()) if value else None
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else (value or "")


def parse_point(text: str) -> tuple[bool, str]:
    """'ca. 31.12.2008' -> (True, '2008-12-31')."""
    text = text.strip()
    approximate = bool(_CA_PREFIX.match(text))
    return approximate, to_xml_date(_CA_PREFIX.sub("", text))


def format_point(approximate: bool, value: str) -> str:
    if not value:
        return ""
    shown = to_display_date(value)
    return f"ca. {shown}" if approximate and value != NOT_SPECIFIED else shown
