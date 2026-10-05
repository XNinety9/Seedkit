import pytest

from seedkit.ruleset import DEFAULT_GRACE, All, Any, Leaf, Not, RuleSetError, parse, parse_duration, parse_size


def test_units():
    assert parse_size("500 MiB") == 500 * 1024**2
    assert parse_size("1.5 To") == int(1.5 * 1024**4)
    assert parse_size("2GB") == 2 * 1000**3
    assert parse_duration("45d") == 45 * 86400
    assert parse_duration("2 w") == 14 * 86400
    assert parse_duration("12h") == 12 * 3600
    with pytest.raises(ValueError):
        parse_size("lots")
    with pytest.raises(ValueError):
        parse_duration("3 fortnights")


def test_full_rule():
    rs = parse(
        """
version: 1
protect:
  - name: Keep
    when: { tag: [keep, perso] }
rules:
  - name: Dormant
    when:
      tracker: Acme
      seed_time: ">= 45d"
      upload_30d: "< 500 MiB"
      ratio: 0.5..2
      any:
        - { seeders: ">= 5" }
        - not: { name: "~ 2160p" }
    delete_files: false
    auto: true
    grace: 2d
  - name: Low disk
    when: { disk_free: "< 100 GiB" }
    select: { order_by: -size, until_free: 1 TiB, limit: 10 }
"""
    )
    assert rs.protect[0].when == All((Leaf("tag", "in", ("keep", "perso"), ["keep", "perso"]),))
    dormant, low = rs.rules
    leaves = {leaf.field: leaf for leaf in dormant.leaves()}
    assert leaves["seed_time"].op == ">=" and leaves["seed_time"].value == 45 * 86400
    assert leaves["upload_30d"].value == 500 * 1024**2
    assert leaves["ratio"].op == "range" and leaves["ratio"].value == (0.5, 2.0)
    assert leaves["name"].op == "regex"
    assert isinstance(dormant.when.items[-1], Any) and isinstance(dormant.when.items[-1].items[1].items[0], Not)
    assert (dormant.delete_files, dormant.auto, dormant.grace) == (False, True, 2 * 86400)
    assert low.select.order_by == "size" and low.select.descending and low.select.until_free == 1024**4
    assert low.leaves()[0].is_global
    assert low.grace == DEFAULT_GRACE and not low.auto


def test_errors_are_collected_with_their_location():
    with pytest.raises(RuleSetError) as exc:
        parse(
            """
rules:
  - name: A
    when: { seed_time: "45d", colour: blue, duplicate: twins }
    keep: best_ratio
  - name: A
    when: { size: "> 10 parsecs" }
    auto: yes please
  - when: {}
"""
        )
    where = [w for w, _ in exc.value.errors]
    assert "rules[0].when.seed_time" in where  # missing operator
    assert "rules[0].when.colour" in where  # unknown field
    assert "rules[0].when.duplicate" in where  # unknown duplicate kind
    assert "rules[0].keep" in where  # keep without a valid duplicate condition
    assert "rules[1].when.size" in where
    assert "rules[1].auto" in where
    assert "rules[2].name" in where
    assert "rules" in where  # duplicated name


def test_keep_validation_per_kind():
    parse("rules: [{name: a, when: {duplicate: episode_in_pack}, keep: pack}]")
    with pytest.raises(RuleSetError):
        parse("rules: [{name: a, when: {duplicate: episode_in_pack}, keep: best_ratio}]")
    with pytest.raises(RuleSetError):
        parse("rules: [{name: a, when: {duplicate: same_title}, keep: pack}]")


def test_invalid_yaml_reports_line():
    with pytest.raises(RuleSetError) as exc:
        parse("rules:\n  - name: a\n   when: x")
    assert exc.value.errors[0][0].startswith("ligne")
