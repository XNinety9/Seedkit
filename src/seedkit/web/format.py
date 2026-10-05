"""Formatting filters, French or US English depending on the current language."""

import time
from datetime import datetime

from seedkit.i18n import get_lang, t

NBSP = " "  # narrow no-break space, the French thousands separator
UNITS = {
    "fr": ("o", "Ko", "Mo", "Go", "To", "Po"),
    "en": ("B", "KiB", "MiB", "GiB", "TiB", "PiB"),
}


def _num(value: float, digits: int) -> str:
    text = f"{value:,.{digits}f}"
    if get_lang() == "fr":
        return text.replace(",", NBSP).replace(".", ",")
    return text


def human_size(n: int | float | None) -> str:
    if n is None:
        return "–"
    n = float(n)
    units = UNITS[get_lang()]
    for unit in units:
        if abs(n) < 1024 or unit == units[-1]:
            digits = 0 if unit == units[0] or abs(n) >= 100 else 1 if abs(n) >= 10 else 2
            return f"{_num(n, digits)} {unit}"
        n /= 1024
    return ""


def size_parts(n: int | float | None) -> tuple[str, str]:
    """('3,9', 'To') for hero numbers that style the unit separately."""
    value, _, unit = human_size(n).partition(" ")
    return value, unit


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "–"
    fr = get_lang() == "fr"
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    if days >= 365:
        years, months = days // 365, (days % 365) // 30
        if fr:
            return f"{years} an{'s' if years > 1 else ''} {months} mois"
        return f"{years} y {months} mo"
    day = "j" if fr else "d"
    if days:
        return f"{days} {day} {hours} h" if days < 10 else f"{days} {day}"
    if hours:
        return f"{hours} h {rem // 60:02d}" if fr else f"{hours} h {rem // 60:02d} min"
    return f"{rem // 60} min"


def ago(ts: int | None) -> str:
    if not ts:
        return t("jamais")
    delta = time.time() - ts
    if delta < 60:
        return t("à l'instant")
    return t("il y a {duration}", duration=human_duration(delta))


def date(ts: int | None) -> str:
    if not ts:
        return "–"
    fmt = "%d/%m/%Y %H:%M" if get_lang() == "fr" else "%m/%d/%Y %I:%M %p"
    return datetime.fromtimestamp(ts).strftime(fmt)


def short_date(ts: int | None) -> str:
    if not ts:
        return "–"
    return datetime.fromtimestamp(ts).strftime("%d/%m/%y" if get_lang() == "fr" else "%m/%d/%y")


def ratio(r: float | None) -> str:
    return "∞" if r is None else _num(r, 2)


def number(n: float | None, digits: int = 0) -> str:
    return "–" if n is None else _num(n, digits)


def pct(part: float, whole: float) -> str:
    if not whole:
        return "0"
    value = 100 * part / whole
    return _num(value, 1 if value < 10 else 0)


FILTERS = {
    "size": human_size,
    "size_parts": size_parts,
    "duration": human_duration,
    "ago": ago,
    "date": date,
    "short_date": short_date,
    "ratio": ratio,
    "number": number,
    "pct": pct,
}
