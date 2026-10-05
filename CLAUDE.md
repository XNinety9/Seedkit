# seedkit

Boîte à outils pour seedbox qBittorrent, orientée trackers privés. Outil autonome : il ne parle qu'à l'API Web
de qBittorrent (URL + clé d'API ou identifiants), sans dépendance à Prowlarr ou à une stack *arr.

## Commandes

```sh
uv run pytest                                  # tests
uv run ruff check . && uv run ruff format .    # lint + format (lignes de 120)
uv run seedkit check | serve | collect | tui | backup DIR
```

## Architecture (`src/seedkit/`)

- `config.py` : réglages pydantic-settings (`QBIT_*`, `SEEDKIT_*`, `SMB_*`), dont les verrous d'écriture.
- `qbit.py` : wrapper `qbittorrent-api` → dataclasses `TorrentData`. Tracker principal = premier tracker *working*,
  identifié par son nom d'hôte. Détection « unregistered » par regex sur le message du tracker.
- `db.py` : SQLAlchemy/SQLite. `Torrent` (état courant, `removed_at` quand il disparaît de qBit), `Snapshot`
  (compteurs cumulés), `TrackerAlias` (domaine → nom), `TrackerRule` (règles H&R par nom), `ActionLog`, `KeyValue`.
  Pas de migrations : on ne fait qu'ajouter des tables (`create_all`).
- `collector.py` : upsert des torrents + un snapshot par torrent et par collecte.
- `rules.py` : statut et progression H&R.
- `analytics.py` : `Catalog` (alias + règles), upload par fenêtre (deltas entre snapshots), efficacité, classements,
  stats par tracker, liste « à surveiller ».
- `trackers.py` : renommer / fusionner / séparer les trackers, règles, export/import YAML, suggestions de fusion.
- `cleanup.py` : candidats (aperçu) et suppression, avec la porte de sécurité `eligible()`.
- `actions.py` : reannounce, recheck, tags (verrou `SEEDKIT_ALLOW_ACTIONS`).
- `retention.py`, `notify.py` (ntfy/Discord), `smb.py` (orphelins/manquants, lecture seule), `backup.py`.
- `service.py` : tâches de fond (collecte, alertes, résumé, tags, compactage, scan SMB).
- `views.py` : données des pages et de l'API (palette par tracker stable).
- `web/` : FastAPI + Jinja + HTMX + Chart.js, assets embarqués dans `web/static` (pas de CDN, pas de build).
  `web/api.py` : API JSON en lecture seule, consommée par la TUI.
- `tui.py` : TUI Textual, client de l'API JSON.
- `i18n.py` / `i18n_en.py` : traduction FR → EN US. Le texte français sert de clé : `_("…")` dans les templates
  (Markup, arguments échappés), `tr("…")` en Python (toujours importé `t as tr` pour éviter qu'une variable
  locale `t` ne masque la fonction). Tout nouveau texte doit être ajouté à `EN` (test de complétude).

## Principes à respecter

- qBittorrent ne donne que des compteurs cumulés : toute stat « sur une période » passe par les snapshots.
- **Efficacité** = Go uploadés / Go occupés / jour : c'est la métrique des classements, pas le ratio brut.
- Un tracker **sans règle est intouchable** : jamais proposé à la suppression.
- Toute écriture vers qBittorrent est verrouillée par défaut ; une suppression passe par un **aperçu puis une
  confirmation**, et `cleanup.delete` revérifie chaque torrent. Le module SMB ne supprime jamais rien.
- Les écritures se testent avec `tests/fakes.FakeClient`, jamais contre une vraie seedbox.
- Graphiques : la couleur suit le tracker (`views.Palette`), palette validée (dataviz) dans `seedkit.css`.
- L'interface est en français et en anglais US, le code et les commentaires en anglais.
- Repo public (`XNinety9/Seedkit`, MIT) : aucune donnée personnelle (vrais noms de trackers, contenus) dans le code,
  les tests ou la doc ; utiliser des exemples génériques (`acme.org`).
- CI : `.github/workflows/ci.yml` (lint, tests, build Docker) ; CD : `release.yml` (image GHCR multi-arch,
  release GitHub sur tag `v*`).
