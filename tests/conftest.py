import pytest

from seedkit import i18n
from seedkit.config import Settings
from seedkit.db import make_engine, make_sessionmaker
from seedkit.qbit import TorrentData

DAY = 86400
NOW = 1_800_000_000


def torrent(hash="a" * 40, **overrides) -> TorrentData:
    fields = dict(
        hash=hash,
        name=f"Torrent {hash[:4]}",
        size=10 * 1024**3,
        uploaded=0,
        downloaded=10 * 1024**3,
        ratio=0.0,
        state="uploading",
        category="",
        tags="",
        added_on=NOW - 30 * DAY,
        completion_on=NOW - 30 * DAY,
        seeding_time=30 * DAY,
        last_activity=NOW,
        num_complete=10,
        num_incomplete=1,
        amount_left=0,
        save_path="/data",
        content_path=f"/data/{hash}",
        tracker="tracker.example.org",
        tracker_status=2,
        tracker_msg="",
    )
    return TorrentData(**(fields | overrides))


@pytest.fixture(autouse=True)
def french():
    """Tests run in French (the source language) unless they switch explicitly."""
    i18n.set_default("fr")
    with i18n.using("fr"):
        yield
    i18n.set_default(i18n.DEFAULT)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        qbit_url="http://qbit.invalid",
        seedkit_db=tmp_path / "seedkit.db",
        seedkit_cleanup_rules=tmp_path / "cleanup-rules.yaml",
        seedkit_lang="fr",
    )


@pytest.fixture
def session(settings):
    sessions = make_sessionmaker(make_engine(settings.seedkit_db))
    with sessions() as s:
        yield s
