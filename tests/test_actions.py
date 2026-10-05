import pytest

from seedkit import actions, trackers
from seedkit.collector import store
from seedkit.config import Settings
from tests.conftest import DAY, NOW, torrent
from tests.fakes import FakeClient


def unlocked(settings):
    return Settings(**(settings.model_dump() | {"seedkit_allow_actions": True}))


def test_actions_are_locked_by_default(session, settings):
    client = FakeClient()
    store(session, [torrent()], now=NOW)
    for fn in (actions.reannounce, actions.recheck):
        with pytest.raises(actions.ActionsDisabled):
            fn(client, session, settings, ["a" * 40])
    with pytest.raises(actions.ActionsDisabled):
        actions.sync_tags(client, session, settings)
    assert client.calls == []


def test_reannounce_known_torrents_only(session, settings):
    client = FakeClient()
    store(session, [torrent()], now=NOW)
    assert actions.reannounce(client, session, unlocked(settings), ["a" * 40, "f" * 40]) == 1
    assert client.calls == [("torrents_reannounce", {"torrent_hashes": ["a" * 40]})]


def test_sync_tags_adds_and_removes_prefixed_tags_only(session, settings):
    store(
        session,
        [
            torrent("a" * 40, tags="perso, sk:hnr-en-cours", seeding_time=10 * DAY),
            torrent("b" * 40, seeding_time=DAY),
        ],
        now=NOW,
    )
    trackers.set_rule(session, "tracker.example.org", 72, None, "any", "")
    client = FakeClient()
    added, removed = actions.sync_tags(client, session, unlocked(settings), now=NOW)
    calls = {(name, kw["tags"], tuple(kw["torrent_hashes"])) for name, kw in client.calls}
    assert ("torrents_remove_tags", "sk:hnr-en-cours", ("a" * 40,)) in calls
    assert ("torrents_add_tags", "sk:hnr-ok", ("a" * 40,)) in calls
    assert ("torrents_add_tags", "sk:hnr-en-cours", ("b" * 40,)) in calls
    assert ("torrents_add_tags", "sk:tracker.example.org", ("a" * 40, "b" * 40)) in calls
    assert not any("perso" in tags for _, tags, _ in calls)
    assert (added, removed) == (4, 1)
