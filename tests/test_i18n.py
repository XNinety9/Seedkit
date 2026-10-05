import ast
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seedkit import analytics, cleanup, engine, i18n, rules, tui, views
from seedkit.collector import store
from seedkit.config import Settings
from seedkit.i18n_en import EN
from seedkit.web.app import create_app
from seedkit.web.format import human_size, number
from tests.conftest import DAY, NOW, torrent
from tests.fakes import FakeClient

SRC = Path(__file__).parent.parent / "src" / "seedkit"
TEMPLATE_KEY = re.compile(r"""\b_\(\s*(["'])((?:\\.|(?!\1).)*)\1""", re.S)


def used_keys() -> set[str]:
    keys = set()
    for f in (SRC / "web" / "templates").rglob("*.html"):
        keys |= {m.group(2) for m in TEMPLATE_KEY.finditer(f.read_text())}
    for f in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("tr", "t")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                keys.add(node.args[0].value)
    # Keys built from data
    keys |= set(rules.HNR_LABELS.values())
    keys |= {label for _, label in analytics.AGE_BUCKETS}
    keys |= set(views.REASON_ICONS) | {views.OTHERS}
    keys |= set(tui.SeedkitTUI.WINDOW_LABELS.values()) | set(tui.SeedkitTUI.SORT_KEYS)
    keys |= set(engine.FIELD_LABELS.values()) | set(engine.DUPLICATE_LABELS.values())
    keys |= {cleanup.DETAIL_WITH_DATA, cleanup.DETAIL_TORRENT_ONLY, cleanup.DETAIL_SHARED}
    return keys


def test_every_key_is_translated():
    missing = sorted(used_keys() - EN.keys())
    assert missing == []


def test_translations_keep_placeholders():
    for key, value in EN.items():
        assert set(re.findall(r"\{(\w+)\}", key)) == set(re.findall(r"\{(\w+)\}", value)), key


def test_resolve_language():
    assert i18n.resolve("fr", "en", "en-US") == "fr"
    assert i18n.resolve(None, "fr", "en-US") == "fr"
    assert i18n.resolve(None, None, "de-DE,fr;q=0.8,en;q=0.5") == "fr"
    assert i18n.resolve(None, None, "de-DE") == "en"
    assert i18n.resolve("xx", None, None) == "en"


def test_formats_follow_language():
    with i18n.using("en"):
        assert human_size(5.2 * 1024**4) == "5.20 TiB"
        assert number(1234.5, 1) == "1,234.5"
    with i18n.using("fr"):
        assert human_size(5.2 * 1024**4) == "5,20 To"
        assert number(1234.5, 1) == "1 234,5"


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        self._skip += tag in ("script", "style", "svg")
        self.parts += [v for k, v in attrs if k in ("title", "placeholder", "aria-label") and v]

    def handle_endtag(self, tag):
        self._skip -= tag in ("script", "style", "svg")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


FRENCH = re.compile(r"[éèêàùâîôûçœ]|\b(les|des|une|pour|avec|sans|dans|torrents? sans)\b", re.I)


@pytest.mark.parametrize("url", ["/", "/torrents", "/watch", "/trackers", "/cleanup", "/files", "/journal"])
def test_pages_have_no_french_in_english(tmp_path, url):
    settings = Settings(
        qbit_url="http://qbit.invalid",
        seedkit_db=tmp_path / "db.sqlite",
        seedkit_cleanup_rules=tmp_path / "rules.yaml",
        seedkit_lang="en",
    )
    app = create_app(settings, start_jobs=False, client=FakeClient())
    with app.state.sessions() as session:
        store(
            session, [torrent("a" * 40, uploaded=2 * 1024**3), torrent("b" * 40, tracker="tk.acme.net")], now=NOW - DAY
        )
        store(
            session,
            [
                torrent("a" * 40, uploaded=5 * 1024**3),
                torrent("b" * 40, tracker="tk.acme.net", tracker_msg="Unregistered torrent"),
            ],
            now=NOW,
        )
    with TestClient(app) as client:
        client.post("/trackers/rule", data={"tracker": "tk.acme.net", "min_seed_hours": "1"})
        html = client.get(url).text
    parser = TextOnly()
    parser.feed(html)
    text = " ".join(" ".join(parser.parts).split())
    text = text.replace("Torrent a", "").replace("Torrent b", "")  # test torrent names
    assert not FRENCH.findall(text), FRENCH.findall(text)


def test_language_switch_cookie(settings):
    app = create_app(settings.model_copy(update={"seedkit_lang": None}), start_jobs=False, client=FakeClient())
    with TestClient(app) as client:
        assert "Tableau de bord" in client.get("/", headers={"accept-language": "fr-FR"}).text
        assert "Dashboard" in client.get("/", headers={"accept-language": "en-US"}).text
        client.get("/lang/fr", follow_redirects=False)
        assert "Tableau de bord" in client.get("/", headers={"accept-language": "en-US"}).text
        assert client.get("/api/status?lang=en").json()["lang"] == "en"
