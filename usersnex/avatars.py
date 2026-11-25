# UserSnex — avatar: download, impronta visiva, miniature.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Scarica le immagini del profilo pubbliche degli account trovati e ne calcola un'impronta visiva.

Confrontare l'indirizzo dell'avatar trova solo la stessa immagine sullo stesso server. L'impronta
(immagine ridotta 12x12 + colore medio) riconosce la stessa foto caricata su siti diversi, anche
ridimensionata, ricompressa o schiarita. Le immagini quasi tinta unita (avatar vuoti) vengono marcate "-" e non contano.

Una richiesta per avatar, con lo User-Agent di UserSnex, limite per host e cache su disco: nessun
login, nessun aggiramento di protezioni. Le immagini che non si scaricano vengono semplicemente saltate.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
import threading
import time
from pathlib import Path

import httpx
from PIL import Image, ImageOps, ImageStat

from .correlate import is_default_avatar
from .engine import USER_AGENT, HostLimiter
from .models import SiteResult, Status
from .paths import CACHE_DIR

AVATAR_DIR = Path(CACHE_DIR) / "avatars"
MAX_BYTES = 3 * 1024 * 1024
MAX_PIXELS = 4096 * 4096
THUMB = 96                    # lato della miniatura salvata (la GUI la riduce)
CACHE_DAYS = 7
FLAT_STDDEV = 6.0             # sotto questa variazione l'immagine è quasi tinta unita


def _key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def thumb_path(url: str) -> Path:
    return AVATAR_DIR / (_key(url) + ".png")


GRID = 12                     # l'impronta è l'immagine ridotta a 12x12 in scala di grigi + il colore medio


def fingerprint(img: Image.Image) -> str:
    """Impronta visiva: "2" + 144 valori di grigio (12x12) + colore medio, oppure "-" se è un'immagine vuota.

    Il confronto (correlate.avatar_distance) normalizza luminosità e contrasto, quindi la stessa foto
    ridimensionata, ricompressa o schiarita resta vicina; foto diverse sono lontane."""
    rgb = _flatten(img)
    if max(ImageStat.Stat(rgb.convert("L")).stddev) < FLAT_STDDEV:
        return "-"
    gray = rgb.convert("L").resize((GRID, GRID), Image.Resampling.BOX).tobytes()
    r, g, b = (int(v) for v in ImageStat.Stat(rgb.resize((16, 16))).mean[:3])
    return "2" + gray.hex() + f"{r:02x}{g:02x}{b:02x}"


def _flatten(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def _process(url: str, data: bytes) -> str:
    """Apre l'immagine, salva la miniatura quadrata e ritorna l'impronta."""
    with Image.open(io.BytesIO(data)) as im:
        if im.width * im.height > MAX_PIXELS:
            raise ValueError("immagine troppo grande")
        im.seek(0)                                  # GIF animate: primo fotogramma
        img = _flatten(im)
    fp = fingerprint(img)
    thumb = ImageOps.fit(img, (THUMB, THUMB), Image.Resampling.LANCZOS)
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    tmp = thumb_path(url).with_suffix(".tmp")
    thumb.save(tmp, "PNG")
    os.replace(tmp, thumb_path(url))
    (AVATAR_DIR / (_key(url) + ".fp2")).write_text(fp, encoding="ascii")
    return fp


def _cached(url: str) -> str | None:
    fpf = AVATAR_DIR / (_key(url) + ".fp2")
    try:
        if time.time() - fpf.stat().st_mtime < CACHE_DAYS * 86400 and thumb_path(url).is_file():
            return fpf.read_text(encoding="ascii").strip()
    except OSError:
        pass
    return None


async def _fetch_all(urls: list[str], timeout: float, cancel, transport, on_progress) -> dict:
    """Scarica ogni indirizzo una volta sola. Ritorna {url: (impronta, miniatura)}."""
    out: dict[str, tuple[str, str]] = {}
    limiter = HostLimiter()
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT, "Accept": "image/*"}, timeout=timeout,
                                 follow_redirects=True, transport=transport) as client:
        async def one(url: str) -> None:
            if cancel is not None and cancel.is_set():
                return
            fp = _cached(url)
            if fp is None:
                async with sem:
                    host = httpx.URL(url).host
                    await limiter.wait(host, 4.0)
                    try:
                        async with client.stream("GET", url) as r:
                            if r.status_code != 200 or not r.headers.get("content-type", "image/").startswith("image/"):
                                return
                            if "svg" in r.headers.get("content-type", ""):
                                return
                            buf = bytearray()
                            async for chunk in r.aiter_bytes():
                                buf += chunk
                                if len(buf) > MAX_BYTES:
                                    return
                        fp = await asyncio.to_thread(_process, url, bytes(buf))
                    except Exception:  # noqa: BLE001 - un avatar che non si apre si salta
                        return
            out[url] = (fp, str(thumb_path(url)))
            if on_progress:
                on_progress(url)

        await asyncio.gather(*(one(u) for u in urls))
    return out


def fetch(results: list[SiteResult], timeout: float = 15.0, cancel: threading.Event | None = None,
          transport: httpx.AsyncBaseTransport | None = None, on_progress=None) -> dict[str, tuple[str, str]]:
    """Scarica gli avatar degli account trovati. Imposta profile.avatar_hash e ritorna
    {sito: (impronta, percorso della miniatura)}."""
    items = []
    for r in results:
        url = (r.profile.avatar or "").strip()
        if r.status != Status.FOUND or not url.startswith(("http://", "https://")) or is_default_avatar(url):
            continue
        items.append((r.site, url))
    urls = list(dict.fromkeys(u for _s, u in items))
    by_url = asyncio.run(_fetch_all(urls, timeout, cancel, transport, on_progress)) if urls else {}
    by_site = {r.site: r for r in results}
    got = {}
    for site, url in items:
        if url in by_url:
            got[site] = by_url[url]
            by_site[site].profile.avatar_hash = by_url[url][0]
    return got
