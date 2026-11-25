# UserSnex — i siti aggiunti dall'utente.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""mytargets.yaml: i siti che l'utente aggiunge dalla maschera "Nuova risorsa" della scheda Config.

Si caricano per ultimi, quindi sostituiscono un sito con lo stesso nome delle altre liste.
Il file è personale: non va su git e gli aggiornamenti delle liste non lo toccano.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import yaml

from .engine import Engine
from .paths import MYTARGETS_FILE
from .selftest import run_selftest
from .sites import SiteDef, _parse

HEADER = ("# I miei siti per UserSnex: si gestiscono dalla scheda Config > Nuova risorsa.\n"
          "# Stesso formato di usersnex.yaml. Hanno la precedenza su tutte le altre liste.\n")
MODES = ("status", "present", "absent", "json")     # come si capisce se l'account esiste


def load(path: str = MYTARGETS_FILE) -> list[dict]:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return []
    if data is None:
        return []
    return data if isinstance(data, list) else [data]


def save(entries: list[dict], path: str = MYTARGETS_FILE) -> None:
    body = yaml.safe_dump(entries, allow_unicode=True, sort_keys=False, width=200) if entries else "[]\n"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(HEADER + body, encoding="utf-8")
    os.replace(tmp, p)


def mode_of(entry: dict) -> tuple[str, str]:
    """Ricava dalla voce il modo di verifica e il testo/campo, per riaprirla nella maschera."""
    if entry.get("type", "json") == "json":
        return "json", entry.get("require", "")
    if entry.get("present_text"):
        t = entry["present_text"]
        return "present", t[0] if isinstance(t, list) else t
    if entry.get("absent_text"):
        t = entry["absent_text"]
        return "absent", t[0] if isinstance(t, list) else t
    return "status", ""


def build(name: str, category: str, url: str, check_url: str, mode: str, text: str, known: str) -> dict:
    """Costruisce la voce YAML dai campi della maschera. Solleva ValueError con un messaggio chiaro."""
    name, url, check_url, text, known = (x.strip() for x in (name, url, check_url, text, known))
    if not name:
        raise ValueError("manca il nome")
    for label, u in (("URL del profilo", url), ("URL di verifica", check_url or url)):
        if not re.match(r"^https?://", u):
            raise ValueError(f"{label}: deve iniziare con http:// o https://")
        if "{username}" not in u:
            raise ValueError(f"{label}: manca {{username}} al posto del nome utente")
    if mode not in MODES:
        raise ValueError("modo di verifica sconosciuto")
    if mode in ("present", "absent", "json") and not text:
        raise ValueError("indica il testo (o il campo JSON) da controllare")
    d: dict = {"name": name, "category": category.strip() or "varie", "url": url,
               "check_url": check_url or url, "type": "json" if mode == "json" else "html"}
    if mode == "status":
        d["found_status"] = [200]
    elif mode == "present":
        d["present_text"] = [text]
    elif mode == "absent":
        d["absent_text"] = [text]
    else:
        d["require"] = text
    if known:
        d["known"] = [known]
    d["source"] = "mytargets"
    _parse(d, "mytargets.yaml")
    return d


CHECK_KEYS = ("type", "require", "absent_text", "present_text", "present_any", "missing_status", "found_status")
KEEP_KEYS = ("regex", "strip_chars", "rate_per_sec", "dns_missing", "follow_redirects", "headers", "fields", "tags",
             "nsfw")


def merge(base: dict, new: dict, keep_check: bool) -> dict:
    """Modifica di un sito di un'altra lista: si parte dalla sua definizione e si applicano i campi della
    maschera. Le regole di verifica originali restano se l'utente non ha cambiato il modo di verifica.
    Il sito modificato va in mytargets.yaml e torna attivo (niente enabled: false)."""
    d = dict(new)
    for k in KEEP_KEYS:
        if k in base and k not in d:
            d[k] = base[k]
    if keep_check:
        for k in CHECK_KEYS:
            d.pop(k, None)
            if k in base:
                d[k] = base[k]
        d.setdefault("type", "json")
    if not d.get("known") and base.get("known"):
        d["known"] = list(base["known"])
    origin = str(base.get("source") or "").split(" (")[0]
    d["source"] = "mytargets" + (f" ({origin})" if origin and not origin.startswith("mytargets") else "")
    order = ("name", "category", "url", "check_url", "type")
    d = {**{k: d[k] for k in order if k in d}, **{k: v for k, v in d.items() if k not in order}}
    _parse(d, "mytargets.yaml")
    return d


def upsert(entry: dict, old_name: str | None = None, path: str = MYTARGETS_FILE) -> None:
    keys = {entry["name"].lower(), (old_name or entry["name"]).lower()}
    entries = [e for e in load(path) if str(e.get("name", "")).lower() not in keys]
    entries.append(entry)
    entries.sort(key=lambda e: str(e.get("name", "")).lower())
    save(entries, path)


def delete(name: str, path: str = MYTARGETS_FILE) -> None:
    save([e for e in load(path) if str(e.get("name", "")).lower() != name.lower()], path)


def test(entry: dict, timeout: float = 15.0) -> dict:
    """Prova la voce come l'autotest: l'account noto va trovato, uno inventato no."""
    site: SiteDef = _parse(entry, "test")
    if not site.known:
        return {"esito": "senza account noto", "nota": "", "http": []}
    report = run_selftest([site], Engine([site], timeout=timeout))
    return report.get(site.name, {"esito": "errore", "nota": "nessuna risposta", "http": []})
