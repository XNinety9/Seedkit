import argparse
import logging
import os
import sys


def main() -> None:
    from seedkit import i18n
    from seedkit.i18n import t as tr

    i18n.set_default(i18n.from_env())
    parser = argparse.ArgumentParser(prog="seedkit", description=tr("Boîte à outils pour seedbox qBittorrent"))
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help=tr("lance le dashboard et le collecteur (par défaut)"))
    sub.add_parser("check", help=tr("vérifie la connexion à qBittorrent"))
    sub.add_parser("collect", help=tr("effectue une collecte unique puis quitte"))
    tui = sub.add_parser("tui", help=tr("interface terminal (se connecte à un serveur seedkit)"))
    tui.add_argument("--url", help=tr("URL du serveur seedkit (défaut : $SEEDKIT_URL ou http://127.0.0.1:8337)"))
    tui.add_argument("--lang", choices=list(i18n.LANGS), help=tr("langue de l'interface"))
    backup = sub.add_parser("backup", help=tr("exporte les fichiers .torrent dans un dossier (lecture seule)"))
    backup.add_argument("directory", help=tr("dossier de destination"))
    args = parser.parse_args()

    if args.command == "tui":
        if args.lang:
            i18n.set_default(args.lang)
        from seedkit.tui import run

        run(args.url or os.environ.get("SEEDKIT_URL") or "http://127.0.0.1:8337")
        return

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from seedkit.config import get_settings

    settings = get_settings()
    i18n.set_default(i18n.from_env(settings.seedkit_lang))

    if args.command == "check":
        import qbittorrentapi

        from seedkit.qbit import fetch_torrents, make_client

        client = make_client(settings)
        try:
            print(f"qBittorrent {client.app.version} (Web API {client.app.web_api_version})")
        except qbittorrentapi.LoginFailed:
            sys.exit(tr("Authentification refusée : vérifie QBIT_API_KEY ou QBIT_USERNAME / QBIT_PASSWORD."))
        except qbittorrentapi.APIConnectionError as exc:
            sys.exit(
                tr(
                    "Impossible de joindre qBittorrent à {url} : {error}",
                    url=settings.qbit_url,
                    error=type(exc).__name__,
                )
            )
        torrents = fetch_torrents(client)
        trackers = sorted({tor.tracker or tr("(aucun)") for tor in torrents})
        print(tr("{n} torrents, trackers : {trackers}", n=len(torrents), trackers=", ".join(trackers)))
    elif args.command == "collect":
        from seedkit.collector import store
        from seedkit.db import make_engine, make_sessionmaker
        from seedkit.qbit import fetch_torrents, make_client

        sessions = make_sessionmaker(make_engine(settings.seedkit_db))
        with sessions() as session:
            store(session, fetch_torrents(make_client(settings)))
    elif args.command == "backup":
        from seedkit.backup import export_torrents
        from seedkit.qbit import make_client

        written, skipped = export_torrents(make_client(settings), args.directory)
        print(
            tr(
                "{written} fichier(s) .torrent exporté(s), {skipped} déjà présent(s) dans {directory}",
                written=written,
                skipped=skipped,
                directory=args.directory,
            )
        )
    else:
        import uvicorn

        from seedkit.web.app import create_app

        uvicorn.run(create_app(settings), host=settings.seedkit_host, port=settings.seedkit_port)
