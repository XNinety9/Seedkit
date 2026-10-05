from seedkit import analytics, trackers
from seedkit.analytics import Catalog
from seedkit.collector import store
from seedkit.db import TrackerRule
from tests.conftest import NOW, torrent


def setup(session):
    store(
        session,
        [
            torrent("a" * 40, tracker="acme.org"),
            torrent("b" * 40, tracker="tk.acme.net"),
            torrent("c" * 40, tracker="x.org"),
        ],
        now=NOW,
    )


def test_merge_with_name_groups_stats_and_moves_rule(session):
    setup(session)
    trackers.set_rule(session, "tk.acme.net", 72, 1.0, "any", "")
    trackers.merge(session, "tk.acme.net", "acme.org")
    trackers.rename(session, "acme.org", "Acme")
    catalog = Catalog.load(session)
    assert catalog.name("acme.org") == catalog.name("tk.acme.net") == "Acme"
    assert set(catalog.rules) == {"Acme"}
    stats = {s.name: s for s in analytics.tracker_stats(session, NOW)}
    assert stats["Acme"].count == 2 and stats["Acme"].domains == {"acme.org", "tk.acme.net"}


def test_split_gives_each_domain_the_group_rule(session):
    setup(session)
    trackers.merge(session, "tk.acme.net", "acme.org")
    trackers.rename(session, "acme.org", "Acme")
    trackers.set_rule(session, "Acme", 72, None, "any", "")
    trackers.split(session, "Acme")
    catalog = Catalog.load(session)
    assert catalog.aliases == {}
    assert set(catalog.rules) == {"acme.org", "tk.acme.net"}


def test_export_import_roundtrip(session):
    setup(session)
    trackers.merge(session, "tk.acme.net", "acme.org")
    trackers.rename(session, "acme.org", "Acme")
    trackers.set_rule(session, "Acme", 72, 1.0, "all", "note")
    exported = trackers.export(session)
    assert exported == {
        "trackers": {
            "Acme": {
                "domains": ["acme.org", "tk.acme.net"],
                "min_seed_hours": 72,
                "min_ratio": 1.0,
                "satisfy": "all",
                "notes": "note",
            }
        }
    }
    trackers.split(session, "Acme")
    for rule in session.query(TrackerRule):
        session.delete(rule)
    session.commit()
    assert trackers.import_(session, exported) == 1
    assert trackers.export(session) == exported


def test_import_legacy_flat_format(session):
    trackers.import_(session, {"x.org": {"min_seed_hours": "48", "min_ratio": "0,5"}})
    rule = session.get(TrackerRule, "x.org")
    assert (rule.min_seed_hours, rule.min_ratio, rule.satisfy) == (48.0, 0.5, "any")


def test_suggest_merges(session):
    setup(session)
    catalog = Catalog.load(session)
    stats = analytics.tracker_stats(session, NOW, catalog)
    assert trackers.suggest_merges(stats, catalog.aliases) in (
        [("acme.org", "tk.acme.net")],
        [("tk.acme.net", "acme.org")],
    )
    trackers.merge(session, "tk.acme.net", "acme.org")
    catalog = Catalog.load(session)
    assert trackers.suggest_merges(analytics.tracker_stats(session, NOW, catalog), catalog.aliases) == []
