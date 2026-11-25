# UserSnex.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Caricamento delle definizioni dei siti (liste YAML).

Ordine di caricamento (un sito con lo stesso nome in una lista successiva sostituisce il precedente):
  1. lists/whatsmyname.yaml, lists/sherlock.yaml, lists/maigret.yaml  -- scaricate dai loro repository
  2. usersnex.yaml  -- i siti curati da UserSnex (l'unica lista nel repository git)
  3. mytargets.yaml -- i siti aggiunti dall'utente dalla scheda Config
Un file può contenere un solo sito (mappa) o una lista di siti.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import shutil
from urllib.parse import quote

import yaml

try:                                   # parser C: molto più veloce sui pacchetti grandi
    from yaml import CSafeLoader as _Loader
except ImportError:  # pragma: no cover
    from yaml import SafeLoader as _Loader

from .paths import FROZEN, HOME_DIR, LISTS_DIR, LISTS_INFO, MYTARGETS_FILE, data_path

# da sorgente usersnex/sites/; nell'exe la cartella sites/ accanto a UserSnex.exe (fuori dal bundle)
BUILTIN_DIR = Path(data_path("sites")) if FROZEN else Path(__file__).parent / "sites"
BUILTIN_LIST = BUILTIN_DIR / "usersnex.yaml"
UPSTREAM_FILES = ("whatsmyname.yaml", "sherlock.yaml", "maigret.yaml")
_LEGACY = {"_pack_wmn.yaml": "whatsmyname.yaml", "_pack_sherlock.yaml": "sherlock.yaml",
           "_pack_maigret.yaml": "maigret.yaml"}


@dataclass
class SiteDef:
    name: str
    category: str
    url: str                      # profilo leggibile da un umano
    check_url: str                # URL interrogato (API pubblica o pagina)
    type: str = "json"            # json | html
    require: str = ""             # percorso JSON che deve esistere e non essere vuoto
    absent_text: list = field(default_factory=list)   # testi che indicano profilo inesistente
    present_text: list = field(default_factory=list)  # testi che devono esserci (tutti)
    present_any: list = field(default_factory=list)   # almeno uno di questi testi deve esserci
    missing_status: list = field(default_factory=lambda: [404, 410])  # codici HTTP = "non esiste"
    found_status: list = field(default_factory=list)  # se indicato, solo questi codici = "esiste"
    regex: str = ""               # username validi per il sito
    strip_chars: str = ""         # caratteri da togliere dall'username per questo sito
    rate_per_sec: float = 1.0     # richieste massime al secondo verso l'host
    dns_missing: bool = False     # sottodominio per utente: errore di connessione = non esiste
    follow_redirects: bool = True  # False quando un 3xx fa parte della risposta attesa
    headers: dict = field(default_factory=dict)
    fields: dict = field(default_factory=dict)  # campo Profile -> percorso JSON (o lista di percorsi)
    protection: list = field(default_factory=list)    # cloudflare, captcha, ...: spesso danno errore
    enabled: bool = True          # i siti protetti partono disattivati
    nsfw: bool = False
    extended: bool = False        # lista estesa (migliaia di forum): solo se richiesta
    tags: list = field(default_factory=list)   # es. paese: it, de, ru
    known: list = field(default_factory=list)  # username esistenti, per l'autotest
    source: str = "UserSnex"      # da dove viene la definizione (attribuzione)
    banned: bool = False          # messo al bando dall'utente (banlist.json), non salvato nelle liste

    def accepts(self, username: str) -> bool:
        return not self.regex or re.fullmatch(self.regex, username) is not None

    def account(self, username: str) -> str:
        return "".join(c for c in username if c not in self.strip_chars) if self.strip_chars else username

    def format(self, template: str, username: str) -> str:
        return template.replace("{username}", quote(self.account(username), safe="@._-+~"))


REQUIRED = ("name", "url", "check_url")


def _list(value) -> list:
    if value in (None, ""):
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _parse(data: dict, origin: str) -> SiteDef:
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise ValueError(f"{origin}: {data.get('name', '?')}: campi mancanti {missing}")
    if data.get("type", "json") not in ("json", "html"):
        raise ValueError(f"{origin}: type deve essere json o html")
    return SiteDef(
        name=str(data["name"]),
        category=data.get("category", "other"),
        url=data["url"],
        check_url=data["check_url"],
        type=data.get("type", "json"),
        require=data.get("require", ""),
        absent_text=[str(x) for x in _list(data.get("absent_text"))],
        present_text=[str(x) for x in _list(data.get("present_text"))],
        present_any=[str(x) for x in _list(data.get("present_any"))],
        missing_status=[int(x) for x in _list(data.get("missing_status", [404, 410]))],
        found_status=[int(x) for x in _list(data.get("found_status"))],
        regex=data.get("regex", ""),
        strip_chars=data.get("strip_chars", ""),
        rate_per_sec=float(data.get("rate_per_sec", 1.0)),
        follow_redirects=bool(data.get("follow_redirects", True)),
        dns_missing=bool(data.get("dns_missing", False)),
        headers=data.get("headers", {}) or {},
        fields=data.get("fields", {}) or {},
        protection=[str(x) for x in _list(data.get("protection"))],
        enabled=bool(data.get("enabled", True)),
        nsfw=bool(data.get("nsfw", False)),
        extended=bool(data.get("extended", False)),
        tags=[str(x) for x in _list(data.get("tags"))],
        known=[str(x) for x in _list(data.get("known"))],
        source=data.get("source", "UserSnex"),
    )


_DEFAULTS = SiteDef("", "", "", "")


def to_entry(site: SiteDef) -> dict:
    """La voce YAML di un sito caricato (solo i campi diversi dal predefinito), per modificarlo."""
    d: dict = {"name": site.name, "category": site.category, "url": site.url, "check_url": site.check_url,
               "type": site.type}
    for k in ("require", "absent_text", "present_text", "present_any", "missing_status", "found_status", "regex",
              "strip_chars", "rate_per_sec", "dns_missing", "follow_redirects", "headers", "fields", "protection",
              "enabled", "nsfw", "extended", "tags", "known", "source"):
        v = getattr(site, k)
        if v != getattr(_DEFAULTS, k) or k == "source":
            d[k] = v
    return d


def migrate_legacy() -> None:
    """Le versioni precedenti tenevano le liste in usersnex/sites/_pack_*.yaml: si spostano in lists/."""
    for old, new in _LEGACY.items():
        dst = Path(LISTS_DIR) / new
        for src in (BUILTIN_DIR / old, Path(HOME_DIR) / "packs" / old):
            if src.is_file() and not dst.is_file():
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if FROZEN:
                        shutil.copyfile(src, dst)
                    else:
                        shutil.move(str(src), str(dst))
                except OSError:
                    pass
    old_info = Path(HOME_DIR) / "packs_info.json"
    if old_info.is_file() and not os.path.isfile(LISTS_INFO):
        try:
            info = old_info.read_text(encoding="utf-8")
            Path(LISTS_INFO).parent.mkdir(parents=True, exist_ok=True)
            Path(LISTS_INFO).write_text(info, encoding="utf-8")
            old_info.unlink()
        except OSError:
            pass


def usersnex_list() -> Path:
    """La lista di UserSnex in uso: quella scaricata se c'è (e, da sorgente, se è più recente)."""
    dl = Path(LISTS_DIR) / "usersnex.yaml"
    if not dl.is_file():
        return BUILTIN_LIST
    if FROZEN or not BUILTIN_LIST.is_file() or dl.stat().st_mtime >= BUILTIN_LIST.stat().st_mtime:
        return dl
    return BUILTIN_LIST


def list_files() -> list[Path]:
    files = [Path(LISTS_DIR) / f for f in UPSTREAM_FILES if (Path(LISTS_DIR) / f).is_file()]
    files.append(usersnex_list())
    if os.path.isfile(MYTARGETS_FILE):
        files.append(Path(MYTARGETS_FILE))
    return files


def upstream_missing() -> list[str]:
    """Le liste esterne non ancora scaricate (prima esecuzione)."""
    return [f for f in UPSTREAM_FILES if not (Path(LISTS_DIR) / f).is_file()]


def load_file(path: Path) -> list[SiteDef]:
    raw = yaml.load(Path(path).read_text(encoding="utf-8"), Loader=_Loader)
    if raw is None:
        return []
    mine = os.path.abspath(path) == os.path.abspath(MYTARGETS_FILE)
    out = []
    for entry in raw if isinstance(raw, list) else [raw]:
        if mine and isinstance(entry, dict):
            entry = {**entry, "source": entry.get("source") or "mytargets"}
        out.append(_parse(entry, Path(path).name))
    return out


def load_sites(extra: list[Path] | None = None, include_disabled: bool = True,
               files: list[Path] | None = None) -> list[SiteDef]:
    """Carica le liste nell'ordine di list_files(), più file o cartelle aggiuntive (--sites)."""
    migrate_legacy()
    paths = list(files) if files is not None else list_files()
    for e in extra or []:
        e = Path(e)
        paths += sorted(e.glob("*.y*ml")) if e.is_dir() else [e]
    sites: dict[str, SiteDef] = {}
    for path in paths:
        for site in load_file(path):
            sites[site.name.lower()] = site
    out = sorted(sites.values(), key=lambda s: s.name.lower())
    return out if include_disabled else [s for s in out if s.enabled]
