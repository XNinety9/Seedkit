# seedkit 🌱

[![CI](https://github.com/XNinety9/Seedkit/actions/workflows/ci.yml/badge.svg)](https://github.com/XNinety9/Seedkit/actions/workflows/ci.yml)
[![Release](https://github.com/XNinety9/Seedkit/actions/workflows/release.yml/badge.svg)](https://github.com/XNinety9/Seedkit/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> **English:** seedkit is a toolkit for qBittorrent seedboxes on private trackers — upload statistics over time,
> per-tracker Hit & Run rules, problem detection, safe cleanup and orphaned-file analysis, with a web dashboard and a
> terminal UI. The interface is available in **English** and French (`SEEDKIT_LANG=en`, or the FR/EN switch).

Boîte à outils pour seedbox qBittorrent, pensée pour les trackers privés : statistiques d'upload dans le temps,
suivi des règles Hit & Run, détection des torrents à problème, nettoyage sûr, analyse des fichiers orphelins.
Dashboard web et interface terminal.

seedkit ne parle qu'à l'API Web de qBittorrent : il suffit d'une URL et d'une clé d'API (ou d'identifiants).

## Démarrage

```sh
cp .env.example .env   # renseigner au minimum QBIT_URL et QBIT_API_KEY
uv run seedkit check   # vérifie la connexion et liste les trackers détectés
uv run seedkit serve   # dashboard sur http://localhost:8337 + collecte périodique
uv run seedkit tui     # interface terminal (se connecte au serveur ci-dessus)
```

Avec Docker (image multi-architecture amd64/arm64 publiée sur GHCR) :

```sh
docker run -d --name seedkit -p 8337:8337 -v seedkit-data:/data --env-file .env ghcr.io/xninety9/seedkit:edge
```

`edge` suit la branche `main` ; les versions publiées sont aussi taguées (`0.1.0`, `0.1`, `latest`).
Pour construire l'image soi-même : `docker build -t seedkit .`

Autres commandes : `seedkit collect` (une collecte puis quitte), `seedkit backup <dossier>` (exporte les `.torrent`).

## Ce que ça fait

| Écran | Contenu |
|---|---|
| **Tableau de bord** | upload total, 24 h, 7 j ; upload par jour et par tracker ; espace par tracker ; santé H&R ; âge du stock ; meilleurs et moins rentables seeds ; « carte du seed » (taille × upload/jour de chaque torrent) |
| **Torrents** | recherche instantanée, filtres, tri, progression H&R de chaque torrent |
| **À surveiller** | non enregistrés, erreurs, trackers en panne, H&R en danger, métadonnées bloquées, téléchargements morts |
| **Trackers & règles** | regroupement des domaines (ex. `acme.org` + `tk.acme.net` → « Acme »), règles H&R, export/import YAML |
| **Nettoyage** | moteur de règles (YAML) : candidats expliqués règle par règle, doublons, suppressions automatiques avec délai de grâce ; éditeur avec validation et aperçu en direct |
| **Fichiers (SMB)** | fichiers orphelins et manquants, en lecture seule |
| **Journal** | toutes les actions envoyées à qBittorrent |

### Notions clés

- **Snapshots** : qBittorrent ne fournit que des compteurs cumulés. seedkit les enregistre à chaque collecte
  (toutes les 10 min par défaut) pour calculer l'upload par période. Les anciens snapshots sont compactés
  chaque nuit (1/heure après 7 jours, 1/jour après 90 jours).
- **Efficacité** = Go uploadés / Go occupés / jour. C'est la métrique des classements : elle favorise les
  torrents qui rapportent beaucoup pour peu de place.
- **Règles H&R** par tracker : temps de seed minimum, ratio minimum, l'un ou les deux. Un tracker **sans
  règle est intouchable** : aucun de ses torrents n'est jamais proposé au nettoyage.

| Statut | Signification |
|---|---|
| H&R OK | exigences remplies |
| H&R en cours | pas encore remplies, le torrent seed normalement |
| H&R en danger | pas remplies **et** le torrent ne seed pas (pause, erreur, non enregistré, tracker en panne) |
| sans règle | aucune règle définie pour ce tracker |

## Règles de nettoyage

Le nettoyage est piloté par un fichier YAML (`SEEDKIT_CLEANUP_RULES`, par défaut `data/cleanup-rules.yaml`, créé
avec des exemples commentés à la première visite). Il se modifie depuis l'écran *Nettoyage → Modifier les règles*,
qui le valide pendant la frappe et montre ce que chaque règle sélectionnerait.

```yaml
version: 1
protect:                      # passent avant tout
  - name: Favoris
    when: { tag: keep }
rules:
  - name: Dormants Acme
    when:
      tracker: Acme
      seed_time: ">= 45d"
      upload_30d: "< 500 MiB"
      seeders: ">= 5"
  - name: Épisodes couverts par un pack
    when: { duplicate: episode_in_pack }
  - name: Même titre, meilleure qualité
    when: { duplicate: same_title }
    keep: highest_quality
  - name: Disque presque plein
    when: { disk_free: "< 200 GiB" }
    select: { order_by: efficiency_30d, until_free: 350 GiB }
    auto: true                # suppression automatique…
    grace: 3d                 # …après 3 jours de correspondance continue
```

- **Conditions** : toutes celles d'un `when` doivent être vraies ; `any: [...]`, `all: [...]`, `not: {...}` pour
  combiner. Champs : `tracker`, `category`, `tag`, `name` (`"~ regex"`), `state`, `hnr`, `unregistered`,
  `duplicate`, `size`, `uploaded`, `upload_24h|7d|30d`, `ratio`, `efficiency_24h|7d|30d|all`, `seed_time`, `age`,
  `inactive`, `seeders`, `leechers`, et les conditions globales `disk_free`, `torrent_count`.
- **Valeurs** : `>=`, `<=`, `>`, `<`, `==`, `!=`, intervalles `1..5 GiB` ; tailles en MiB/GiB/TiB (ou Mo/Go/To),
  durées en h/d/w/mo/y ; une liste vaut « l'un de ».
- **Doublons** : `same_files` (mêmes fichiers stockés deux fois), `same_title` (même film / épisode / saison dans
  plusieurs releases), `episode_in_pack` (épisodes contenus dans un pack de qualité égale ou supérieure). `keep`
  choisit la copie gardée : `highest_quality`, `best_ratio`, `best_efficiency`, `most_seeders`, `oldest`,
  `newest`, `largest`, `smallest`. Le cross-seed (mêmes données sur plusieurs torrents) n'est jamais un doublon.
- **Garde-fous** : une règle ne peut jamais sélectionner un torrent dont le tracker n'a pas de règle H&R, dont le
  H&R n'est pas rempli, ou qui est protégé. Des données partagées avec un torrent conservé ne sont jamais
  supprimées. Une règle `auto` ne supprime que si `SEEDKIT_ALLOW_DELETE=true`, après un délai de grâce pendant
  lequel le torrent doit correspondre à chaque collecte (une notification part au début du décompte).

## Sécurité

Tout ce qui écrit dans qBittorrent est **désactivé par défaut** :

| Variable | Débloque |
|---|---|
| `SEEDKIT_ALLOW_ACTIONS=true` | reannounce, recheck, tags |
| `SEEDKIT_ALLOW_DELETE=true` | suppression depuis l'écran Nettoyage |
| `SEEDKIT_AUTO_TAGS=true` | tags automatiques `sk:hnr-ok`, `sk:hnr-en-cours`… (avec `ALLOW_ACTIONS`) |

Même déverrouillée, une suppression revérifie côté serveur chaque torrent (règle définie, H&R rempli ou
torrent non enregistré) et est consignée dans le journal. Le module SMB ne fait que lire.

Le dashboard n'a pas d'authentification : ne l'expose pas sur Internet sans reverse proxy authentifié.

## Langue

L'interface (web, TUI, CLI, notifications) existe en français et en anglais (US). Par défaut, le dashboard suit la
langue du navigateur et propose un sélecteur FR/EN ; `SEEDKIT_LANG=fr` ou `en` force une langue. La TUI et la CLI
suivent `SEEDKIT_LANG`, puis la locale du système (`seedkit tui --lang en` pour forcer).

## Notifications

Avec `SEEDKIT_NTFY_URL` et/ou `SEEDKIT_DISCORD_WEBHOOK` : alerte dès qu'un torrent passe « à surveiller »
(une seule fois par problème), et résumé quotidien à `SEEDKIT_SUMMARY_HOUR`.

## Développement

```sh
uv run pytest
uv run ruff check . && uv run ruff format .
```

Les textes sont écrits en français dans le code et traduits via `src/seedkit/i18n_en.py` ; un test vérifie que
chaque texte a sa traduction et que les pages anglaises ne contiennent plus de français.

La CI (lint, tests, build Docker) tourne sur chaque push et pull request. Pousser un tag `vX.Y.Z` publie l'image
Docker versionnée et crée une release GitHub.

## Licence

[MIT](LICENSE)
