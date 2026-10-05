"""Tracker names (aliases over announce domains) and their H&R rules: editing, export and import."""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from seedkit import analytics
from seedkit.analytics import Catalog
from seedkit.db import TrackerAlias, TrackerRule


def domains_of(session: Session, name: str) -> set[str]:
    catalog = Catalog.load(session)
    domains = {d for d, n in catalog.aliases.items() if n == name}
    domains |= {t.tracker for t in analytics.active_torrents(session) if catalog.name(t.tracker) == name}
    return domains


def _move_rule(session: Session, old: str, new: str) -> None:
    rule = session.get(TrackerRule, old)
    if rule is None or old == new:
        return
    if session.get(TrackerRule, new) is None:
        session.add(
            TrackerRule(
                tracker=new,
                min_seed_hours=rule.min_seed_hours,
                min_ratio=rule.min_ratio,
                satisfy=rule.satisfy,
                notes=rule.notes,
            )
        )
    session.delete(rule)


def _assign(session: Session, domains: set[str], name: str) -> None:
    for domain in domains:
        if domain == name:
            session.execute(delete(TrackerAlias).where(TrackerAlias.domain == domain))
        else:
            session.merge(TrackerAlias(domain=domain, name=name))


def rename(session: Session, name: str, new_name: str) -> None:
    new_name = new_name.strip()
    if not new_name or new_name == name:
        return
    _assign(session, domains_of(session, name), new_name)
    _move_rule(session, name, new_name)
    session.commit()


def merge(session: Session, name: str, into: str) -> None:
    """Put every domain of `name` under `into`. The rule of `into` wins; otherwise `name`'s rule moves."""
    if name == into:
        return
    _assign(session, domains_of(session, name) | domains_of(session, into), into)
    _move_rule(session, name, into)
    session.commit()


def split(session: Session, name: str) -> None:
    """Remove the alias: each domain becomes its own tracker again and inherits the group's rule."""
    domains = domains_of(session, name)
    rule = session.get(TrackerRule, name)
    session.execute(delete(TrackerAlias).where(TrackerAlias.name == name))
    if rule is not None:
        for domain in domains - {name}:
            if session.get(TrackerRule, domain) is None:
                session.add(
                    TrackerRule(
                        tracker=domain,
                        min_seed_hours=rule.min_seed_hours,
                        min_ratio=rule.min_ratio,
                        satisfy=rule.satisfy,
                        notes=rule.notes,
                    )
                )
        if name not in domains:
            session.delete(rule)
    session.commit()


def set_rule(
    session: Session, name: str, min_seed_hours: float | None, min_ratio: float | None, satisfy: str, notes: str
) -> None:
    if satisfy not in ("any", "all"):
        raise ValueError("satisfy doit valoir 'any' ou 'all'")
    rule = session.get(TrackerRule, name) or TrackerRule(tracker=name)
    rule.min_seed_hours = min_seed_hours
    rule.min_ratio = min_ratio
    rule.satisfy = satisfy
    rule.notes = notes.strip()
    session.add(rule)
    session.commit()


def delete_rule(session: Session, name: str) -> None:
    if rule := session.get(TrackerRule, name):
        session.delete(rule)
        session.commit()


def export(session: Session) -> dict:
    catalog = Catalog.load(session)
    names = sorted(set(catalog.rules) | set(catalog.aliases.values()))
    trackers = {}
    for name in names:
        entry: dict = {}
        if domains := sorted(d for d, n in catalog.aliases.items() if n == name):
            entry["domains"] = domains
        if rule := catalog.rules.get(name):
            entry |= {
                "min_seed_hours": rule.min_seed_hours,
                "min_ratio": rule.min_ratio,
                "satisfy": rule.satisfy,
                "notes": rule.notes,
            }
        trackers[name] = entry
    return {"trackers": trackers}


def _optional_float(value) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    return float(str(value).replace(",", "."))


def import_(session: Session, data) -> int:
    """Import an export (or the older flat `tracker: rule` format). Returns the number of trackers."""
    if not isinstance(data, dict):
        raise ValueError("le fichier doit contenir un dictionnaire")
    trackers = data.get("trackers", data)
    if not isinstance(trackers, dict):
        raise ValueError("la clé 'trackers' doit contenir un dictionnaire")
    for name, spec in trackers.items():
        name = str(name)
        spec = spec or {}
        if not isinstance(spec, dict):
            raise ValueError(f"entrée invalide pour {name!r}")
        for domain in spec.get("domains") or []:
            session.merge(TrackerAlias(domain=str(domain), name=name))
        if any(k in spec for k in ("min_seed_hours", "min_ratio", "satisfy")):
            satisfy = spec.get("satisfy") if spec.get("satisfy") in ("any", "all") else "any"
            session.merge(
                TrackerRule(
                    tracker=name,
                    min_seed_hours=_optional_float(spec.get("min_seed_hours")),
                    min_ratio=_optional_float(spec.get("min_ratio")),
                    satisfy=satisfy,
                    notes=str(spec.get("notes") or ""),
                )
            )
    session.commit()
    return len(trackers)


def known_names(session: Session) -> list[str]:
    catalog = Catalog.load(session)
    names = {catalog.name(t.tracker) for t in analytics.active_torrents(session)}
    return sorted(names | set(catalog.rules))


def rules_without_torrents(session: Session) -> list[TrackerRule]:
    catalog = Catalog.load(session)
    active = {catalog.name(t.tracker) for t in analytics.active_torrents(session)}
    return [r for r in session.scalars(select(TrackerRule)) if r.tracker not in active]


def _family(domain: str) -> str | None:
    """'tk.acme.net' and 'acme.org' both give 'acme'."""
    labels = domain.split(".")
    return labels[-2] if len(labels) >= 2 and len(labels[-2]) >= 3 else None


def suggest_merges(stats: list[analytics.TrackerStats], aliases: dict[str, str]) -> list[tuple[str, str]]:
    """Pairs of not-yet-grouped trackers whose domains look like the same site."""
    raw = [s for s in stats if s.name not in aliases.values() and len(s.domains) == 1]
    by_family: dict[str, list[str]] = {}
    for s in raw:
        if family := _family(next(iter(s.domains))):
            by_family.setdefault(family, []).append(s.name)
    return [(names[0], other) for names in by_family.values() for other in names[1:]]
