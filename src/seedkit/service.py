"""Background jobs: collection, alerts, summary, tags, snapshot compaction and SMB scans."""

import json
import logging
import threading
import time

import qbittorrentapi
from sqlalchemy.orm import sessionmaker

from seedkit import actions, autoclean, engine, i18n, notify, retention, rulesfile, smb
from seedkit.collector import missing_signatures, store, store_free_space, store_signatures
from seedkit.config import Settings
from seedkit.db import KeyValue
from seedkit.qbit import fetch_free_space, fetch_signatures, fetch_torrents, make_client

log = logging.getLogger(__name__)


class Service:
    def __init__(self, settings: Settings, sessions: sessionmaker, client=None):
        self.settings = settings
        self.sessions = sessions
        self.client = client or make_client(settings)
        self.last_run: int | None = None
        self._error: tuple[str, dict] | None = None  # message key + arguments, translated when displayed
        self.smb_running = False
        self._lock = threading.Lock()
        self._smb_lock = threading.Lock()

    # Collection --------------------------------------------------------------

    def collect(self) -> None:
        if not self._lock.acquire(blocking=False):
            return  # a collection is already in progress
        try:
            torrents = fetch_torrents(self.client)
            with self.sessions() as session:
                store(session, torrents)
                store_free_space(session, fetch_free_space(self.client))
                store_signatures(session, fetch_signatures(self.client, missing_signatures(session)))
            self.last_run, self._error = int(time.time()), None
        except qbittorrentapi.LoginFailed:
            self._fail("authentification refusée par qBittorrent (vérifie la clé d'API ou les identifiants)")
        except qbittorrentapi.Forbidden403Error:
            self._fail("accès refusé par qBittorrent (403) : clé d'API invalide ou IP bannie ?")
        except qbittorrentapi.APIConnectionError:
            self._fail("qBittorrent injoignable à {url}", url=self.settings.qbit_url)
        except Exception as exc:
            log.exception("Collection failed")
            self._error = ("{error}", {"error": f"{type(exc).__name__}: {exc}"})
        finally:
            self._lock.release()
        if self._error is None:
            self._after_collect()

    @property
    def last_error(self) -> str | None:
        return i18n.t(self._error[0], **self._error[1]) if self._error else None

    def _fail(self, message: str, **kwargs) -> None:
        self._error = (message, kwargs)
        log.warning(message.format(**kwargs))

    def _after_collect(self) -> None:
        jobs = [
            ("alertes", notify.check_alerts),
            ("résumé", notify.maybe_send_summary),
            ("nettoyage auto", self._autoclean),
        ]
        if self.settings.seedkit_auto_tags and self.settings.seedkit_allow_actions:
            jobs.append(("tags", lambda s, st: actions.sync_tags(self.client, s, st)))
        for name, job in jobs:
            try:
                with self.sessions() as session, i18n.using(i18n.from_env(self.settings.seedkit_lang)):
                    job(session, self.settings)
            except Exception:
                log.exception("Job %s failed", name)

    def _autoclean(self, session, settings) -> None:
        _, ruleset, errors = rulesfile.load(settings)
        if errors or ruleset is None or not any(r.auto and r.enabled for r in ruleset.rules):
            return
        autoclean.run(self.client, session, settings, engine.evaluate(session, ruleset))

    def compact(self) -> None:
        with self.sessions() as session:
            retention.compact(session, self.settings)

    # SMB -----------------------------------------------------------------------

    def _smb(self) -> tuple[smb.SmbFileSystem, list[smb.Mapping]]:
        return smb.SmbFileSystem(self.settings), smb.parse_path_map(self.settings.seedkit_path_map)

    def smb_check(self) -> dict:
        fs, mappings = self._smb()
        return smb.check_mapping(fs, mappings, smb.torrent_files(self.client))

    def smb_scan(self) -> None:
        if not self._smb_lock.acquire(blocking=False):
            return
        self.smb_running = True
        try:
            fs, mappings = self._smb()
            ignore = [p.strip() for p in self.settings.seedkit_smb_ignore.split(",") if p.strip()]
            result = smb.scan(fs, mappings, smb.torrent_files(self.client), ignore)
        except Exception as exc:
            log.exception("SMB scan failed")
            result = smb.ScanResult(ts=int(time.time()), roots=[], errors=[f"{type(exc).__name__}: {exc}"])
        finally:
            self.smb_running = False
            self._smb_lock.release()
        with self.sessions() as session:
            session.merge(KeyValue(key="smb.scan", value=json.dumps(result.to_json()), ts=result.ts))
            session.commit()

    def smb_scan_async(self) -> None:
        threading.Thread(target=self.smb_scan, daemon=True, name="smb-scan").start()


def last_smb_scan(session) -> smb.ScanResult | None:
    row = session.get(KeyValue, "smb.scan")
    return smb.ScanResult.from_json(json.loads(row.value)) if row else None
