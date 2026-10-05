import pytest

from seedkit import cleanup, trackers
from seedkit.collector import store
from seedkit.config import Settings
from seedkit.db import ActionLog, Torrent
from tests.conftest import DAY, NOW, torrent
from tests.fakes import FakeClient

GB = 1024**3


@pytest.fixture
def stocked(session):
    store(
        session,
        [
            torrent("a" * 40, tracker="acme.org", uploaded=0, seeding_time=30 * DAY),  # dormant, H&R ok
            torrent("b" * 40, tracker="acme.org", uploaded=500 * GB, ratio=50.0),  # very profitable
            torrent("c" * 40, tracker="acme.org", seeding_time=DAY),  # H&R pending
            torrent("d" * 40, tracker="other.org", uploaded=0),  # no rule: untouchable
            torrent("e" * 40, tracker="acme.org", seeding_time=DAY, tracker_msg="Unregistered torrent"),
        ],
        now=NOW,
    )
    trackers.set_rule(session, "acme.org", 72, None, "any", "")
    return session


def test_preview_only_proposes_safe_or_unregistered(stocked):
    preview = cleanup.preview(stocked, cleanup.Criteria(window="all", max_efficiency=0.01), now=NOW)
    assert [c.torrent.hash[0] for c in preview.candidates] == ["e", "a"]  # unregistered first
    assert preview.excluded == {
        "assez rentable": 1,
        "H&R pas encore rempli": 1,
        "tracker sans règle (intouchable)": 1,
    }


def test_preview_tracker_filter_and_unregistered_toggle(stocked):
    criteria = cleanup.Criteria(trackers=["acme.org"], window="all", max_efficiency=0.01, unregistered=False)
    preview = cleanup.preview(stocked, criteria, now=NOW)
    assert [c.torrent.hash[0] for c in preview.candidates] == ["a"]


def test_delete_is_locked_by_default(stocked, settings):
    client = FakeClient()
    with pytest.raises(cleanup.DeletionDisabled):
        cleanup.delete(client, stocked, settings, ["a" * 40], delete_files=True, now=NOW)
    assert client.calls == []


def test_delete_rechecks_every_torrent(stocked, settings):
    unlocked = Settings(**(settings.model_dump() | {"seedkit_allow_delete": True}))
    client = FakeClient()
    # b is profitable but safe (allowed when explicitly selected), c is pending, d has no rule, z is unknown.
    deleted = cleanup.delete(client, stocked, unlocked, ["a" * 40, "c" * 40, "d" * 40, "z" * 40], True, now=NOW)
    assert [t.hash[0] for t in deleted] == ["a"]
    assert client.calls == [("torrents_delete", {"delete_files": True, "torrent_hashes": ["a" * 40]})]
    assert stocked.get(Torrent, "a" * 40).removed_at == NOW
    assert [log.action for log in stocked.query(ActionLog)] == ["delete"]


def test_delete_nothing_eligible_sends_nothing(stocked, settings):
    unlocked = Settings(**(settings.model_dump() | {"seedkit_allow_delete": True}))
    client = FakeClient()
    assert cleanup.delete(client, stocked, unlocked, ["d" * 40], True, now=NOW) == []
    assert client.calls == []
