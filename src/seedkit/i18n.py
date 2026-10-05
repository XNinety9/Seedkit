"""Minimal translation layer: French source strings are the keys, English is looked up in EN.

Templates use `_("…", name=value)`, which returns Markup: the source string is trusted (it may contain inline
HTML) while the keyword arguments are escaped. Python code uses `t()`, which returns a plain string.
"""

import os
from contextlib import contextmanager
from contextvars import ContextVar

from markupsafe import Markup

from seedkit.i18n_en import EN

LANGS = {"fr": "Français", "en": "English"}
DEFAULT = "en"

_lang: ContextVar[str] = ContextVar("lang")
_process_default = DEFAULT  # used where no request set a language (CLI, TUI, background threads)


def get_lang() -> str:
    return _lang.get(_process_default)


def set_default(lang: str) -> None:
    global _process_default
    _process_default = lang if lang in LANGS else DEFAULT


def set_lang(lang: str) -> None:
    _lang.set(lang if lang in LANGS else DEFAULT)


@contextmanager
def using(lang: str):
    token = _lang.set(lang if lang in LANGS else DEFAULT)
    try:
        yield
    finally:
        _lang.reset(token)


def _lookup(text: str) -> str:
    return EN.get(text, text) if get_lang() == "en" else text


def t(text: str, **kwargs) -> str:
    message = _lookup(text)
    return message.format(**kwargs) if kwargs else message


def markup(text: str, **kwargs) -> Markup:
    return Markup(_lookup(text)).format(**kwargs) if kwargs else Markup(_lookup(text))


def resolve(cookie: str | None, configured: str | None, accept_language: str | None) -> str:
    """Language of a request: explicit choice, then configuration, then the browser's preference."""
    if cookie in LANGS:
        return cookie
    if configured in LANGS:
        return configured
    for part in (accept_language or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LANGS:
            return code
    return DEFAULT


def from_env(configured: str | None = None) -> str:
    """Language for the CLI, the TUI and notifications: SEEDKIT_LANG, then the system locale."""
    if configured in LANGS:
        return configured
    for var in ("SEEDKIT_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        code = (os.environ.get(var) or "")[:2].lower()
        if code in LANGS:
            return code
    return DEFAULT
