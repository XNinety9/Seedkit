from urllib.parse import unquote_plus

import pytest
from fastapi.testclient import TestClient

from seedkit.collector import store
from seedkit.config import Settings
from seedkit.db import Torrent
from seedkit.web.app import create_app
from tests.conftest import DAY, NOW, torrent
from tests.fakes import FakeClient

GB = 1024**3


def make_client(settings, fake=None):
    app = create_app(settings, start_jobs=False, client=fake or FakeClient())
    with app.state.sessions() as session:
        store(
            session,
            [torrent("a" * 40, tracker="acme.org", uploaded=5 * GB), torrent("b" * 40, tracker="tk.acme.net")],
            now=NOW - DAY,
        )
        store(
            session,
            [
                torrent("a" * 40, tracker="acme.org", uploaded=7 * GB),
                torrent("b" * 40, tracker="tk.acme.net", tracker_msg="Unregistered torrent"),
            ],
            now=NOW,
        )
    return TestClient(app)


PAGES = [
    "/",
    "/?window=all",
    "/torrents",
    "/torrents?q=torrent&tracker=acme.org&status=no_rule&sort=size&desc=false&page=1",
    "/watch",
    "/trackers",
    "/cleanup",
    "/cleanup/rules",
    "/files",
    "/journal",
    "/api/status",
    "/api/dashboard",
    "/api/torrents",
    "/api/watch",
    "/static/seedkit.js",
]


@pytest.mark.parametrize("url", PAGES)
def test_pages_render(settings, url):
    with make_client(settings) as client:
        assert client.get(url).status_code == 200


def test_htmx_partials(settings):
    with make_client(settings) as client:
        perf = client.get("/?window=7d", headers={"HX-Request": "true"})
        assert perf.status_code == 200 and "<html" not in perf.text and "Carte du seed" in perf.text
        table = client.get("/torrents?q=zzz", headers={"HX-Request": "true"})
        assert "Aucun torrent" in table.text


def test_merge_rule_and_cleanup_flow(settings):
    with make_client(settings) as client:
        assert "semblent être le même tracker" in client.get("/trackers").text
        client.post("/trackers/merge", data={"tracker": "tk.acme.net", "into": "acme.org", "name": "Acme"})
        client.post("/trackers/rule", data={"tracker": "Acme", "min_seed_hours": "72", "min_ratio": "1,5"})
        exported = client.get("/rules.yaml").text
        assert "Acme:" in exported and "min_ratio: 1.5" in exported
        page = client.get("/cleanup")
        assert "Suppression verrouillée" in page.text and "Retirés du tracker" in page.text


def test_delete_locked_sends_nothing(settings):
    fake = FakeClient()
    with make_client(settings, fake) as client:
        client.post("/trackers/rule", data={"tracker": "tk.acme.net", "min_seed_hours": "1"})
        r = client.post("/cleanup/delete", data={"hashes": ["b" * 40], "delete_files": "true"}, follow_redirects=False)
        assert r.status_code == 303
        assert "SEEDKIT_ALLOW_DELETE" in unquote_plus(r.headers["location"])
    assert fake.calls == []


def test_delete_unlocked(settings):
    fake = FakeClient()
    unlocked = Settings(**(settings.model_dump() | {"seedkit_allow_delete": True}))
    with make_client(unlocked, fake) as client:
        client.post("/trackers/rule", data={"tracker": "tk.acme.net", "min_seed_hours": "1"})
        client.post("/cleanup/delete", data={"hashes": ["b" * 40]})
        with client.app.state.sessions() as session:
            assert session.get(Torrent, "b" * 40).removed_at is not None
        assert "suppression" in client.get("/journal").text
    # selected by the default "unregistered" rule, which deletes the data
    assert fake.calls == [("torrents_delete", {"delete_files": True, "torrent_hashes": ["b" * 40]})]


def test_actions_locked(settings):
    fake = FakeClient()
    with make_client(settings, fake) as client:
        r = client.post("/watch/action", data={"action": "reannounce", "hashes": ["b" * 40]}, follow_redirects=False)
        assert r.status_code == 303
    assert fake.calls == []


def test_import_rejects_garbage(settings):
    with make_client(settings) as client:
        assert client.post("/rules/import", files={"file": ("r.yaml", "- a\n- b")}).status_code == 400


def test_rules_editor_check_and_save(settings):
    with make_client(settings) as client:
        page = client.get("/cleanup/rules").text
        assert "Retirés du tracker" in page  # default template created on first visit
        bad = client.post("/cleanup/rules/check", data={"text": "rules: [{name: x, when: {seed_time: 45d}}]"}).text
        assert "opérateur manquant" in bad and "rules[0].when.seed_time" in bad
        good = client.post("/cleanup/rules/check", data={"text": "rules: [{name: Gone, when: {unregistered: true}}]"})
        assert "Règles valides" in good.text and "Gone" in good.text

        # an invalid file is never written
        r = client.post("/cleanup/rules", data={"text": "rules: [{when: {}}]"})
        assert "chaque règle doit avoir un nom" in r.text
        assert "Retirés du tracker" in settings.seedkit_cleanup_rules.read_text()

        r = client.post("/cleanup/rules", data={"text": "rules: [{name: Gone, when: {unregistered: true}}]"})
        assert r.status_code == 200 and "Gone" in r.text
        assert settings.seedkit_cleanup_rules.read_text().startswith("rules: [{name: Gone")
        assert settings.seedkit_cleanup_rules.with_suffix(".yaml.bak").exists()


def test_cleanup_page_with_broken_rules_file(settings):
    settings.seedkit_cleanup_rules.write_text("rules: oops")
    with make_client(settings) as client:
        page = client.get("/cleanup").text
        assert "aucune règle n'est appliquée" in page
        r = client.post("/cleanup/delete", data={"hashes": ["b" * 40]}, follow_redirects=False)
        assert "erreurs" in unquote_plus(r.headers["location"])
