import pytest

from seedkit import cleanup, trackers
from seedkit.analytics import Catalog
from seedkit.collector import store
from seedkit.config import Settings
from seedkit.db import ActionLog, Torrent
from tests.conftest import DAY, NOW, torrent
from tests.fakes import FakeClient


@pytest.fixture
def stocked(session):
    store(
        session,
        [
            torrent("a" * 40, tracker="acme.org", seeding_time=30 * DAY),  # H&R met
            torrent("c" * 40, tracker="acme.org", seeding_time=DAY),  # H&R pending
            torrent("d" * 40, tracker="other.org"),  # no rule: untouchable
            torrent("e" * 40, tracker="acme.org", seeding_time=DAY, tracker_msg="Unregistered torrent"),
            torrent("f" * 40, tracker="acme.org", amount_left=10),  # downloading
        ],
        now=NOW,
    )
    trackers.set_rule(session, "acme.org", 72, None, "any", "")
    return session


def unlocked(settings):
    return Settings(**(settings.model_dump() | {"seedkit_allow_delete": True}))


def test_eligibility_gate(stocked):
    catalog = Catalog.load(stocked)
    verdicts = {t.hash[0]: cleanup.eligible(t, catalog, NOW) for t in stocked.query(Torrent)}
    assert verdicts == {
        "a": (True, ""),
        "c": (False, "H&R pas encore rempli"),
        "d": (False, "tracker sans règle (intouchable)"),
        "e": (True, ""),
        "f": (False, "en téléchargement"),
    }


def test_delete_is_locked_by_default(stocked, settings):
    client = FakeClient()
    with pytest.raises(cleanup.DeletionDisabled):
        cleanup.delete(client, stocked, settings, ["a" * 40], delete_files=True, now=NOW)
    assert client.calls == []


def test_delete_rechecks_every_torrent(stocked, settings):
    client = FakeClient()
    hashes = ["a" * 40, "c" * 40, "d" * 40, "f" * 40, "z" * 40]
    deleted = cleanup.delete(client, stocked, unlocked(settings), hashes, True, now=NOW, rule="Dormant")
    assert [t.hash[0] for t in deleted] == ["a"]
    assert client.calls == [("torrents_delete", {"delete_files": True, "torrent_hashes": ["a" * 40]})]
    assert stocked.get(Torrent, "a" * 40).removed_at == NOW
    assert [(log.action, log.detail) for log in stocked.query(ActionLog)] == [("delete", "torrent + données|Dormant")]


def test_shared_data_is_never_deleted(session, settings):
    # Cross-seed: two torrents on the same data; deleting one must keep the files.
    store(
        session,
        [
            torrent("a" * 40, tracker="acme.org", content_path="/data/Movie", seeding_time=30 * DAY),
            torrent("b" * 40, tracker="other.org", content_path="/data/Movie"),
        ],
        now=NOW,
    )
    trackers.set_rule(session, "acme.org", 72, None, "any", "")
    client = FakeClient()
    cleanup.delete(client, session, unlocked(settings), ["a" * 40], True, now=NOW)
    assert client.calls == [("torrents_delete", {"delete_files": False, "torrent_hashes": ["a" * 40]})]
    assert session.query(ActionLog).one().detail.startswith(cleanup.DETAIL_SHARED)


def test_deleting_every_copy_deletes_the_data(session, settings):
    store(
        session,
        [
            torrent("a" * 40, tracker="acme.org", content_path="/data/Movie", seeding_time=30 * DAY),
            torrent("b" * 40, tracker="acme.org", content_path="/data/Movie", seeding_time=30 * DAY),
        ],
        now=NOW,
    )
    trackers.set_rule(session, "acme.org", 72, None, "any", "")
    client = FakeClient()
    cleanup.delete(client, session, unlocked(settings), ["a" * 40, "b" * 40], True, now=NOW)
    assert client.calls == [("torrents_delete", {"delete_files": True, "torrent_hashes": ["a" * 40, "b" * 40]})]
