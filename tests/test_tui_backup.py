import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient
from textual.widgets import DataTable, Digits

from seedkit import backup, tui
from seedkit.collector import store
from seedkit.web.app import create_app
from tests.conftest import DAY, NOW, torrent
from tests.fakes import FakeClient


def test_tui_renders_from_api(settings, monkeypatch):
    app = create_app(settings, start_jobs=False, client=FakeClient())
    with app.state.sessions() as session:
        store(session, [torrent("a" * 40, uploaded=3 * 1024**3)], now=NOW - DAY)
        store(
            session, [torrent("a" * 40, uploaded=5 * 1024**3), torrent("b" * 40, tracker_msg="unregistered")], now=NOW
        )
    http = TestClient(app)

    def get(self, path, **params):
        r = http.get(f"/api/{path}", params=params)
        r.raise_for_status()
        return r.json()

    monkeypatch.setattr(tui.Api, "get", get)

    async def run():
        ui = tui.SeedkitTUI("http://test")
        async with ui.run_test(size=(140, 50)) as pilot:
            await pilot.pause(1)
            assert str(ui.query_one("#total", Digits).value) == "5,00"
            await pilot.press("2")
            await pilot.pause(0.5)
            assert ui.query_one("#torrents", DataTable).row_count == 2
            await pilot.press("w")
            await pilot.pause(0.5)
            await pilot.press("3", "4", "1")

    asyncio.run(run())


def test_tui_offline(monkeypatch):
    async def run():
        ui = tui.SeedkitTUI("http://127.0.0.1:1")
        async with ui.run_test() as pilot:
            await pilot.pause(1)
            assert "injoignable" in str(ui.query_one("#status").render())

    asyncio.run(run())


def test_backup_exports_missing_files(tmp_path):
    class Client:
        def __init__(self):
            self.exported = []

        def torrents_info(self):
            return [SimpleNamespace(name="A/B: C", hash="ab" * 20), SimpleNamespace(name="D", hash="cd" * 20)]

        def torrents_export(self, torrent_hash):
            self.exported.append(torrent_hash)
            return b"d8:announce0:e"

    client = Client()
    (tmp_path / backup.filename("D", "cd" * 20)).write_bytes(b"x")
    assert backup.export_torrents(client, tmp_path) == (1, 1)
    assert (tmp_path / "A_B_ C [abababab].torrent").exists()
    assert client.exported == ["ab" * 20]
