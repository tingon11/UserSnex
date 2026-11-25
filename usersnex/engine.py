# UserSnex.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Motore asincrono: interroga i siti in parallelo, con limite di velocità per host e cache su disco.

Usa solo pagine e API pubbliche. Non aggira protezioni: un 401/403/429 persistente diventa un
errore visibile nei risultati, non un tentativo di passare comunque.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import httpx

from . import __version__
from .extract import get_path, profile_from_html, profile_from_json
from .models import Profile, SiteResult, Status
from .sites import SiteDef

# User-Agent descrittivo con un recapito, come chiedono le policy di alcuni siti (es. Wikimedia)
USER_AGENT = f"UserSnex/{__version__} (+https://www.osintinfo.net; username OSINT, public data only)"
MAX_RETRY_AFTER = 20.0     # secondi massimi di attesa su un 429 prima di arrendersi
# pagine di verifica anti-bot: il sito non risponde a un programma, e UserSnex non prova ad aggirarlo
CHALLENGE_MARKERS = ("Client Challenge", "Just a moment...", "Attention Required!", "cf-chl-",
                     "captcha-delivery.com", "Checking your browser")


class DiskCache:
    """Cache delle risposte: un file JSON per URL, valido per `ttl` secondi."""

    def __init__(self, folder: Path, ttl: int = 3600) -> None:
        self.folder = Path(folder)
        self.ttl = ttl
        self.folder.mkdir(parents=True, exist_ok=True)

    def _path(self, url: str) -> Path:
        return self.folder / (hashlib.sha1(url.encode()).hexdigest() + ".json")

    def get(self, url: str) -> tuple[int, str] | None:
        p = self._path(url)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if time.time() - data.get("t", 0) > self.ttl:
            return None
        return data["status"], data["text"]

    def put(self, url: str, status: int, text: str) -> None:
        if status >= 500 or status == 429:
            return                      # errori temporanei: non si memorizzano
        try:
            self._path(url).write_text(json.dumps({"t": time.time(), "status": status, "text": text}),
                                       encoding="utf-8")
        except OSError:
            pass

    def clear(self) -> int:
        n = 0
        for p in self.folder.glob("*.json"):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
        return n


class HostLimiter:
    """Distanzia le richieste verso lo stesso host (rispetto dei limiti dei siti)."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def wait(self, host: str, rate_per_sec: float) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            gap = 1.0 / max(rate_per_sec, 0.01)
            delay = self._last.get(host, 0.0) + gap - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last[host] = time.monotonic()


class Engine:
    def __init__(self, sites: list[SiteDef], concurrency: int = 32, timeout: float = 15.0,
                 cache: DiskCache | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 extended: bool = False) -> None:
        self.sites = sites
        self.concurrency = concurrency
        self.timeout = timeout
        self.cache = cache
        self.transport = transport
        self.extended = extended        # anche la lista estesa (migliaia di forum)

    def active(self, site: SiteDef) -> bool:
        return site.enabled and (self.extended or not site.extended)

    # ------------------------------------------------------------------ API pubblica
    def run(self, username: str, on_result: Callable[[SiteResult], None] | None = None,
            cancel: threading.Event | None = None) -> list[SiteResult]:
        """Versione sincrona (per GUI e CLI): crea il proprio event loop."""
        return asyncio.run(self.search(username, on_result, cancel))

    async def search(self, username: str, on_result: Callable[[SiteResult], None] | None = None,
                     cancel: threading.Event | None = None) -> list[SiteResult]:
        username = username.strip().lstrip("@")
        pairs = [(s, username) for s in self.sites if self.active(s)]
        results = await self.check_pairs(pairs, on_result, cancel)
        order = {s.name: i for i, s in enumerate(self.sites)}
        return sorted(results, key=lambda r: order.get(r.site, 0))

    def run_pairs(self, pairs: list[tuple[SiteDef, str]], on_result=None, cancel=None) -> list[SiteResult]:
        return asyncio.run(self.check_pairs(pairs, on_result, cancel))

    async def check_pairs(self, pairs: list[tuple[SiteDef, str]],
                          on_result: Callable[[SiteResult], None] | None = None,
                          cancel: threading.Event | None = None) -> list[SiteResult]:
        """Controlla coppie (sito, username): la ricerca normale e l'autotest dei siti."""
        sem = asyncio.Semaphore(self.concurrency)
        limiter = HostLimiter()
        results: list[SiteResult] = []
        headers = {"User-Agent": USER_AGENT, "Accept-Language": "it,en;q=0.8"}
        limits = httpx.Limits(max_connections=self.concurrency, max_keepalive_connections=self.concurrency)

        def emit(res: SiteResult) -> None:
            results.append(res)
            if on_result:
                on_result(res)

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, headers=headers, limits=limits,
                                     transport=self.transport) as client:

            async def one(site: SiteDef, username: str) -> None:
                if cancel is not None and cancel.is_set():
                    return
                async with sem:
                    if cancel is not None and cancel.is_set():
                        return
                    emit(await self._check(client, limiter, site, username))

            todo = []
            for site, username in pairs:
                if site.accepts(site.account(username)):
                    todo.append(one(site, username))
                else:
                    emit(SiteResult(site.name, site.category, username, site.format(site.url, username),
                                    Status.NOT_FOUND, error="username non valido per il sito"))
            await asyncio.gather(*todo)
        return results

    # ------------------------------------------------------------------ singolo sito
    async def _fetch(self, client, limiter, site: SiteDef, url: str) -> tuple[int, str, bool]:
        if self.cache:
            hit = self.cache.get(url)
            if hit:
                return hit[0], hit[1], True
        host = urlsplit(url).hostname or ""
        for attempt in range(2):
            await limiter.wait(host, site.rate_per_sec)
            # Accept in base al tipo: molti siti (Discourse, Mastodon, ...) a un Accept JSON rispondono
            # in JSON invece che con la pagina, e i testi da cercare non ci sono più
            accept = "application/json" if site.type == "json" else "text/html,application/xhtml+xml,*/*;q=0.8"
            resp = await client.get(url, headers={"Accept": accept, **site.headers},
                                    follow_redirects=site.follow_redirects)
            if resp.status_code == 429 and attempt == 0:
                try:
                    wait = float(resp.headers.get("Retry-After", "5"))
                except ValueError:
                    wait = 5.0
                if wait <= MAX_RETRY_AFTER:
                    await asyncio.sleep(wait)
                    continue
            break
        text = resp.text
        # in cache solo risposte "pulite": un 400/403/429/5xx può essere un blocco temporaneo
        # (es. TikTok risponde 400 anche quando limita le richieste) e non va ripetuto per un'ora
        if self.cache and (resp.status_code < 400 or resp.status_code in (404, 410)):
            self.cache.put(url, resp.status_code, text)
        return resp.status_code, text, False

    async def _check(self, client, limiter, site: SiteDef, username: str) -> SiteResult:
        url = site.format(site.url, username)
        res = SiteResult(site.name, site.category, username, url, Status.ERROR)
        t0 = time.monotonic()
        try:
            status, text, _cached = await self._fetch(client, limiter, site, site.format(site.check_url, username))
        except httpx.ConnectError as e:
            if site.dns_missing:               # sottodominio per utente: se non esiste, non esiste l'utente
                res.status = Status.NOT_FOUND
                return res
            res.error = f"rete: {type(e).__name__}"
            return res
        except httpx.HTTPError as e:
            res.error = f"rete: {type(e).__name__}"
            return res
        except Exception as e:  # noqa: BLE001 - un sito malfatto non deve fermare gli altri
            res.error = f"errore: {type(e).__name__}"
            return res
        finally:
            res.elapsed_ms = int((time.monotonic() - t0) * 1000)
        res.http_status = status
        classify(site, status, text, res)
        return res


def classify(site: SiteDef, status: int, text: str, res: SiteResult) -> None:
    """Decide trovato / non trovato / errore dalla risposta e ne estrae il profilo."""
    if status in site.missing_status or (status in (404, 410) and status not in site.found_status):
        res.status = Status.NOT_FOUND
        return
    if site.found_status and status not in site.found_status and status < 400:
        res.status = Status.NOT_FOUND          # es. redirect alla home al posto del profilo
        return
    if status in (401, 403):
        res.status, res.error = Status.ERROR, f"accesso negato dal sito ({status})"
        return
    if status == 429:
        res.status, res.error = Status.ERROR, "limite di richieste del sito (429)"
        return
    if status >= 400:
        res.status, res.error = Status.ERROR, f"HTTP {status}"
        return
    if site.type == "html" and any(m in text[:20000] for m in CHALLENGE_MARKERS):
        res.status, res.error = Status.ERROR, "pagina di verifica anti-bot: controlla a mano"
        return
    if any(t in text for t in site.absent_text):
        res.status = Status.NOT_FOUND
        return
    if site.present_text and not all(t in text for t in site.present_text):
        res.status = Status.NOT_FOUND
        return
    if site.present_any and not any(t in text for t in site.present_any):
        res.status = Status.NOT_FOUND
        return
    if site.type == "json":
        try:
            data = json.loads(text)
        except ValueError:
            res.status, res.error = Status.ERROR, "risposta non JSON"
            return
        if site.require:
            value = get_path(data, site.require)
            if value in (None, "", [], {}):
                res.status = Status.NOT_FOUND
                return
        res.profile = profile_from_json(data, site.fields)
    else:
        res.profile = profile_from_html(text, site.name, res.username) if text else Profile()
    res.status = Status.FOUND
