import pytest

from seedkit import autoclean, engine, trackers
from seedkit.collector import store, store_free_space, store_signatures
from seedkit.config import Settings
from seedkit.db import AutoPending, Torrent
from seedkit.ruleset import parse
from tests.conftest import DAY, NOW, torrent
from tests.fakes import FakeClient

GB = 1024**3
OLD = {"added_on": NOW - 90 * DAY, "completion_on": NOW - 90 * DAY, "seeding_time": 90 * DAY}


def library(session, extra=()):
    items = [
        torrent("a" * 40, name="Dormant.Movie.2010.1080p", size=40 * GB, num_complete=9, **OLD),
        torrent("b" * 40, name="Popular.Movie.2011.1080p", uploaded=400 * GB, ratio=10.0, **OLD),
        torrent("c" * 40, name="Gone.Movie.2012.1080p", tracker_msg="Unregistered torrent", **OLD),
        torrent("k" * 40, name="Kept.Movie.2013.1080p", tags="keep", size=30 * GB, **OLD),
        torrent("n" * 40, name="Fresh.Movie.2014.1080p", seeding_time=DAY),  # H&R not met yet
        *extra,
    ]
    before = [torrent(**(vars(t) | {"uploaded": t.uploaded - 10 * GB})) if t.hash == "b" * 40 else t for t in items]
    store(session, before, now=NOW - DAY)  # "b" uploaded 10 GiB during the last day
    store(session, items, now=NOW)
    trackers.set_rule(session, "tracker.example.org", 72, None, "any", "")
    return session


RULES = """
protect:
  - name: Keep
    when: { tag: keep }
rules:
  - name: Unregistered
    when: { unregistered: true }
  - name: Dormant
    when: { seed_time: ">= 45d", upload_30d: "< 1 GiB", seeders: ">= 3" }
"""


def test_rules_protect_and_gate(session):
    ev = engine.evaluate(library(session), parse(RULES), now=NOW)
    assert [(m.rule.name, m.torrent.hash[0]) for m in ev.candidates] == [("Unregistered", "c"), ("Dormant", "a")]
    dormant = ev.result("Dormant")
    assert dormant.blocked == {"protégé par « Keep »": 1}
    reasons = dormant.matches[0].reasons
    assert any(r.startswith("temps de seed 90 j ≥ 45 j") for r in reasons), reasons
    assert any("seeders 9 ≥ 3" in r for r in reasons)
    assert ev.protected == {"k" * 40: "Keep"}


def test_first_rule_wins_and_any_not(session):
    rules = """
rules:
  - name: Big
    when: { size: ">= 30 GiB" }
  - name: Not popular
    when:
      not: { ratio: ">= 5" }
      any: [ { unregistered: true }, { name: "~ dormant" } ]
"""
    ev = engine.evaluate(library(session), parse(rules), now=NOW)
    assert [(m.rule.name, m.torrent.hash[0]) for m in ev.candidates] == [
        ("Big", "a"),
        ("Big", "k"),
        ("Not popular", "c"),
    ]
    assert {m.torrent.hash[0] for m in ev.result("Not popular").matches} == {"a", "c"}


def test_until_free_select(session):
    library(session)
    rules = (
        "rules: [{name: Low, when: {disk_free: '< 100 GiB'}, select: {order_by: efficiency_30d, until_free: 125 GiB}}]"
    )
    store_free_space(session, 90 * GB)
    ev = engine.evaluate(session, parse(rules), now=NOW)
    # needs 35 GiB: the least efficient torrents first until the target is reached
    chosen = ev.result("Low").matches
    assert sum(m.torrent.size for m in chosen) >= 35 * GB
    assert "b" * 40 not in {m.torrent.hash for m in chosen}  # the efficient one is spared
    store_free_space(session, 500 * GB)
    ev = engine.evaluate(session, parse(rules), now=NOW)
    assert ev.result("Low").matches == [] and ev.result("Low").inactive


def test_duplicates(session):
    extra = [
        # same title, two qualities
        torrent("d1" * 20, name="Show.S01E01.1080p.WEB", content_path="/d/a", **OLD),
        torrent("d2" * 20, name="Show.S01E01.2160p.WEB", content_path="/d/b", **OLD),
        # season pack + an episode of lower quality + an episode of higher quality
        torrent("p1" * 20, name="Series.S02.1080p.WEB", content_path="/d/p", **OLD),
        torrent("e1" * 20, name="Series.S02E03.720p.WEB", content_path="/d/e1", **OLD),
        torrent("e2" * 20, name="Series.S02E04.2160p.WEB", content_path="/d/e2", **OLD),
        # a pack of unknown quality covers no episode of known quality
        torrent("p2" * 20, name="Other.S01.WEB", content_path="/d/p2", **OLD),
        torrent("e3" * 20, name="Other.S01E01.1080p.WEB", content_path="/d/e3", **OLD),
        # same files twice, and a cross-seed copy of one of them (same data path)
        torrent("f1" * 20, name="Album", content_path="/d/x/Album", ratio=3.0, **OLD),
        torrent("f2" * 20, name="Album (copy)", content_path="/d/y/Album", ratio=0.1, **OLD),
        torrent("f3" * 20, name="Album", content_path="/d/x/Album", tracker="other.org", **OLD),
    ]
    library(session, extra)
    store_signatures(session, {"f1" * 20: ("sig", 3), "f2" * 20: ("sig", 3), "f3" * 20: ("sig", 3)})
    rules = """
rules:
  - name: Packs
    when: { duplicate: episode_in_pack }
  - name: Files
    when: { duplicate: same_files }
    keep: best_ratio
  - name: Titles
    when: { duplicate: same_title }
    keep: highest_quality
"""
    ev = engine.evaluate(session, parse(rules), now=NOW)
    by_rule = {r.rule.name: {m.torrent.hash for m in r.matches} for r in ev.results}
    assert by_rule["Packs"] == {"e1" * 20}  # the 2160p episode is better than the 1080p pack
    assert by_rule["Files"] == {"f2" * 20}  # the cross-seeded pair (f1 + f3) is kept as one unit
    assert by_rule["Titles"] == {"d1" * 20}  # the 2160p release wins
    assert any("on garde « Show.S01E01.2160p.WEB »" in r for r in ev.result("Titles").matches[0].reasons)


@pytest.fixture
def auto_rules():
    return parse("rules: [{name: Gone, when: {unregistered: true}, auto: true, grace: 2d}]")


def test_autoclean_grace_period(session, settings, auto_rules):
    library(session)
    client = FakeClient()
    unlocked = Settings(**(settings.model_dump() | {"seedkit_allow_delete": True}))

    def run(now, s=unlocked):
        return autoclean.run(client, session, s, engine.evaluate(session, auto_rules, now=now), now=now)

    assert run(NOW) == []  # countdown starts
    assert session.query(AutoPending).one().first_seen == NOW
    assert run(NOW + DAY) == []  # still in grace
    assert run(NOW + 2 * DAY, settings) == []  # due, but deletion is locked
    assert client.calls == []
    deleted = run(NOW + 2 * DAY)
    assert [t.hash[0] for t in deleted] == ["c"]
    assert client.calls == [("torrents_delete", {"delete_files": True, "torrent_hashes": ["c" * 40]})]
    assert session.query(AutoPending).count() == 0


def test_autoclean_countdown_resets(session, settings, auto_rules):
    library(session)
    client = FakeClient()
    autoclean.run(client, session, settings, engine.evaluate(session, auto_rules, now=NOW), now=NOW)
    # the tracker knows the torrent again: it stops matching, the countdown is dropped
    session.get(Torrent, "c" * 40).unregistered = False
    session.commit()
    autoclean.run(client, session, settings, engine.evaluate(session, auto_rules, now=NOW + DAY), now=NOW + DAY)
    assert session.query(AutoPending).count() == 0


def test_background_load_never_creates_the_rules_file(settings):
    from seedkit import rulesfile

    text, ruleset, errors = rulesfile.load(settings, create=False)
    assert (text, ruleset.rules, errors) == ("", (), [])
    assert not settings.seedkit_cleanup_rules.exists()
