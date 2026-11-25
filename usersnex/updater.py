# UserSnex — aggiornamento delle liste pubbliche di siti.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Scarica le liste di siti dai loro repository e le converte nel formato YAML di UserSnex.

Ogni lista resta in un file a parte con la sua licenza (vedi THIRD_PARTY_NOTICES.md), in lists/
(fuori da git):
  - WhatsMyName, Micah Hoffman, CC BY-SA 4.0  ->  whatsmyname.yaml (stessa licenza)
  - Sherlock Project, MIT                     ->  sherlock.yaml
  - Maigret, Soxoj, MIT                       ->  maigret.yaml
    (i siti più noti e quelli italiani nella lista normale, gli altri forum nella lista estesa)
  - UserSnex                                  ->  usersnex.yaml (già nel formato giusto, dal repository
                                                  di UserSnex se l'indirizzo è impostato)

Regole: niente siti per adulti, niente siti che vogliono una richiesta POST, niente siti segnati
come non funzionanti. I siti con protezioni (Cloudflare, captcha, ...) restano ma disattivati.
Dalle intestazioni si tolgono User-Agent e cookie: UserSnex si presenta sempre con il suo nome.
usersnex.yaml e mytargets.yaml hanno la precedenza; tra le liste esterne vince WhatsMyName, che ha
regole di verifica più precise.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import yaml

from .i18n import tr
from .paths import LISTS_DIR, LISTS_INFO, MYTARGETS_FILE
from .sites import _parse, usersnex_list

WMN_URL = "https://raw.githubusercontent.com/WebBreacher/WhatsMyName/main/wmn-data.json"
MAIGRET_URL = "https://raw.githubusercontent.com/soxoj/maigret/main/maigret/resources/data.json"
SHERLOCK_URL = "https://raw.githubusercontent.com/sherlock-project/sherlock/master/sherlock_project/resources/data.json"
DEFAULT_USERSNEX_URL = "https://raw.githubusercontent.com/tingon11/UserSnex/main/usersnex/sites/usersnex.yaml"
USER_AGENT = "UserSnex list updater (+https://osintinfo.net)"
MIN_SITES = 50          # sotto questa soglia la lista scaricata è considerata rotta e si tiene la vecchia

DROP_HEADERS = {"user-agent", "cookie"}

CATEGORIES = {   # categorie WhatsMyName -> italiano
    "social": "social", "gaming": "giochi", "tech": "tecnologia", "hobby": "hobby", "coding": "sviluppo",
    "misc": "varie", "finance": "finanza", "business": "lavoro", "music": "musica", "shopping": "shopping",
    "images": "immagini", "blog": "blog", "art": "arte", "dating": "incontri", "video": "video",
    "political": "politica", "news": "notizie", "archived": "archivio", "health": "salute",
}


def host(url: str) -> str:
    h = (urlsplit(url.replace("{username}", "x")).hostname or "").lower()
    return h.removeprefix("www.")


def _headers(h: dict | None) -> dict:
    return {k: v for k, v in (h or {}).items() if k.lower() not in DROP_HEADERS}


def from_wmn(data: dict) -> list[dict]:
    out = []
    for s in data["sites"]:
        if s.get("valid") is False or "post_body" in s or "NSFW" in s.get("cat", ""):
            continue
        check = s["uri_check"].replace("{account}", "{username}")
        pretty = (s.get("uri_pretty") or s["uri_check"]).replace("{account}", "{username}")
        e_code, m_code = int(s["e_code"]), int(s["m_code"])
        d = {"name": s["name"], "category": CATEGORIES.get(s.get("cat", ""), s.get("cat", "varie")),
             "url": pretty, "check_url": check, "type": "html",
             "found_status": [e_code], "missing_status": [m_code] if m_code != e_code else [],
             "present_text": s["e_string"]}
        if m_code == e_code and s.get("m_string"):
            d["absent_text"] = s["m_string"]
        if 300 <= e_code < 400 or 300 <= m_code < 400:
            d["follow_redirects"] = False
        if s.get("strip_bad_char"):
            d["strip_chars"] = s["strip_bad_char"]
        if _headers(s.get("headers")):
            d["headers"] = _headers(s.get("headers"))
        if s.get("protection"):
            d["protection"] = s["protection"]
            d["enabled"] = False
        d["known"] = s.get("known", [])[:2]
        d["source"] = "WhatsMyName (CC BY-SA 4.0)"
        out.append(d)
    return out


def from_sherlock(data: dict) -> list[dict]:
    out = []
    for name, s in data.items():
        if not isinstance(s, dict) or s.get("isNSFW") or s.get("request_method", "GET") != "GET" \
                or s.get("request_payload"):
            continue
        kind = s.get("errorType")
        kind = kind[0] if isinstance(kind, list) else kind
        url = s["url"].replace("{}", "{username}")
        check = (s.get("urlProbe") or s["url"]).replace("{}", "{username}")
        d = {"name": name, "category": "varie", "url": url, "check_url": check, "type": "html"}
        if kind == "status_code":
            d["found_status"] = [200]
            codes = s.get("errorCode")
            if codes:
                d["missing_status"] = sorted({404, 410, *(codes if isinstance(codes, list) else [codes])})
        elif kind == "message":
            d["absent_text"] = s["errorMsg"]
        elif kind == "response_url":
            d["follow_redirects"] = False
            d["found_status"] = [200]
            d["missing_status"] = [301, 302, 303, 307, 308, 404, 410]
        else:
            continue
        if s.get("regexCheck"):
            d["regex"] = s["regexCheck"]
        if _headers(s.get("headers")):
            d["headers"] = _headers(s.get("headers"))
        if s.get("username_claimed"):
            d["known"] = [s["username_claimed"]]
        d["source"] = "Sherlock (MIT)"
        out.append(d)
    return out


MAIGRET_SKIP_TAGS = {"porn", "erotic", "webcam", "dating"}
MAIGRET_BASE_RANK = 100_000      # sotto questa posizione Alexa il sito va nella lista normale
COUNTRIES = {"it", "de", "fr", "es", "ru", "ua", "pl", "us", "gb", "cn", "jp", "br", "in", "tr", "nl", "ch",
             "kr", "ir", "vn", "id", "cz", "by", "kz", "ca", "au", "ar", "mx", "pt", "gr", "ro", "hu", "il"}
MAIGRET_CATEGORIES = {"forum": "forum", "discussion": "forum", "tech": "tecnologia", "coding": "sviluppo",
                      "wiki": "wiki", "social": "social", "gaming": "giochi", "hobby": "hobby",
                      "education": "istruzione", "business": "lavoro", "music": "musica", "blog": "blog",
                      "sport": "sport", "news": "notizie", "video": "video", "art": "arte", "crypto": "finanza",
                      "finance": "finanza", "photo": "immagini", "travel": "viaggi", "auto": "motori",
                      "science": "scienza", "streaming": "video", "design": "arte", "shopping": "shopping"}


def from_maigret(data: dict) -> list[dict]:
    engines = data.get("engines", {})
    out = []
    for name, raw in data["sites"].items():
        if raw.get("disabled") or raw.get("requestMethod", "GET") != "GET" or raw.get("requestPayload"):
            continue
        if raw.get("type") not in (None, "username") or MAIGRET_SKIP_TAGS & set(raw.get("tags", [])):
            continue
        s = dict(engines.get(raw.get("engine"), {}).get("site", {}))
        s.update(raw)
        if not s.get("url") or not s.get("urlMain") and "{urlMain}" in s.get("url", ""):
            continue
        main = (s.get("urlMain") or "").rstrip("/")
        sub = s.get("urlSubpath", "")

        def fill(t: str) -> str:
            return t.replace("{urlMain}", main).replace("{urlSubpath}", sub)

        url, check = fill(s["url"]), fill(s.get("urlProbe") or s["url"])
        if "{username}" not in check or not check.startswith(("http://", "https://")):
            continue
        tags = s.get("tags", [])
        cat = next((MAIGRET_CATEGORIES[t] for t in tags if t in MAIGRET_CATEGORIES), "varie")
        d = {"name": name, "category": cat, "url": url, "check_url": check, "type": "html"}
        kind = s.get("checkType")
        if kind == "status_code":
            d["found_status"] = [200]
        elif kind == "message":
            if s.get("presenseStrs"):
                d["present_any"] = s["presenseStrs"]
            if s.get("absenceStrs"):
                d["absent_text"] = s["absenceStrs"]
            if not s.get("presenseStrs") and not s.get("absenceStrs"):
                continue
        elif kind == "response_url":
            d["follow_redirects"] = False
            d["found_status"] = [200]
            d["missing_status"] = [301, 302, 303, 307, 308, 404, 410]
        else:
            continue
        if s.get("ignore403"):
            d["missing_status"] = sorted(set(d.get("missing_status", [404, 410])) | {403})
        if s.get("regexCheck"):
            d["regex"] = s["regexCheck"]
        if _headers(s.get("headers")):
            d["headers"] = _headers(s.get("headers"))
        if s.get("protection"):
            d["protection"] = s["protection"] if isinstance(s["protection"], list) else [s["protection"]]
            d["enabled"] = False
        country = [t for t in tags if t in COUNTRIES]
        if country:
            d["tags"] = country
        rank = s.get("alexaRank") or 10**9
        if rank > MAIGRET_BASE_RANK and "it" not in country:
            d["extended"] = True
        if s.get("usernameClaimed"):
            d["known"] = [s["usernameClaimed"]]
        d["source"] = "Maigret (MIT)"
        out.append(d)
    return out



def handmade(usersnex_file: Path | None = None) -> tuple[set[str], set[str]]:
    """Nomi e host di usersnex.yaml e mytargets.yaml: hanno la precedenza sulle liste esterne."""
    names, hosts = set(), set()
    for f in (usersnex_file or usersnex_list(), Path(MYTARGETS_FILE)):
        try:
            data = yaml.safe_load(Path(f).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        for s in data if isinstance(data, list) else [data]:
            if isinstance(s, dict) and s.get("name") and s.get("check_url"):
                names.add(str(s["name"]).lower())
                hosts.add(host(s["check_url"]))
                hosts.add(host(s.get("url") or s["check_url"]))
    return names, hosts


def dedupe(sites: list[dict], names: set[str], hosts: set[str]) -> list[dict]:
    kept = []
    for s in sites:
        n, h, hu = s["name"].lower(), host(s["check_url"]), host(s["url"])
        if n in names or h in hosts or hu in hosts:
            continue
        names.add(n)
        hosts.update({h, hu})
        kept.append(s)
    return kept


def _valid(sites: list[dict]) -> list[dict]:
    """Tiene solo le voci che UserSnex sa leggere (stesso controllo del caricamento)."""
    ok = []
    for s in sites:
        try:
            _parse(s, "update")
        except Exception:  # noqa: BLE001 - una voce strana non deve far fallire tutta la lista
            continue
        ok.append(s)
    return ok


SOURCES = [   # chiave, nome, file, convertitore, intestazione con la licenza
    ("wmn", "WhatsMyName", "whatsmyname.yaml", from_wmn,
     "# Derivato da WhatsMyName (https://github.com/WebBreacher/WhatsMyName)\n"
     "# Copyright (C) 2015-2026 Micah Hoffman. Licenza CC BY-SA 4.0:\n"
     "# https://creativecommons.org/licenses/by-sa/4.0/  -- convertito nel formato UserSnex;\n"
     "# questo file resta sotto CC BY-SA 4.0.\n"),
    ("sherlock", "Sherlock", "sherlock.yaml", from_sherlock,
     "# Derivato da Sherlock (https://github.com/sherlock-project/sherlock)\n"
     "# Copyright (c) 2019 Sherlock Project. Licenza MIT -- convertito nel formato UserSnex.\n"),
    ("maigret", "Maigret", "maigret.yaml", from_maigret,
     "# Derivato da Maigret (https://github.com/soxoj/maigret)\n"
     "# Copyright (c) 2020-2026 Soxoj. Licenza MIT -- convertito nel formato UserSnex.\n"),
]
URLS = {"wmn": WMN_URL, "sherlock": SHERLOCK_URL, "maigret": MAIGRET_URL}
LICENSES = {"usersnex": "GPL-3.0 + 7(b)", "wmn": "CC BY-SA 4.0", "sherlock": "MIT", "maigret": "MIT"}
MIN_USERSNEX = 10


def pack_dir() -> Path:
    return Path(LISTS_DIR)


def _count(p: Path) -> int | None:
    try:
        return sum(1 for line in p.open(encoding="utf-8") if line.startswith(("- name:", "name:")))
    except OSError:
        return None


def info(usersnex_url: str = "") -> dict:
    """Stato di ogni lista: siti, data dell'ultimo aggiornamento, impronta del file scaricato."""
    try:
        with open(LISTS_INFO, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        saved = {}
    out = {}
    rows = [("usersnex", "UserSnex", "usersnex.yaml")] + [(k, n, f) for k, n, f, _c, _h in SOURCES]
    for key, name, file in rows:
        d = dict(saved.get(key, {}))
        p = usersnex_list() if key == "usersnex" else pack_dir() / file
        if p.is_file():
            d["sites"] = _count(p)
            if key == "usersnex" and p.parent != pack_dir():
                d["bundled"] = True
                d.pop("updated", None)
            d.setdefault("updated", time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime)))
        else:
            d = {"missing": True}
        d.update(name=name, file=file, license=LICENSES[key],
                 url=usersnex_url if key == "usersnex" else URLS[key])
        out[key] = d
    return out


def _save_info(data: dict) -> None:
    os.makedirs(os.path.dirname(LISTS_INFO) or ".", exist_ok=True)
    tmp = LISTS_INFO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, LISTS_INFO)


def download(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _write_bytes(path: Path, data: bytes) -> None:
    """Scrive in un file temporaneo e poi lo sostituisce: una lista a metà non resta mai su disco."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write(path: Path, header: str, sites: list[dict]) -> None:
    body = yaml.safe_dump(sites, allow_unicode=True, sort_keys=False, width=200)
    _write_bytes(path, (header + body).encode("utf-8"))


def _check_usersnex(data: bytes) -> int:
    """Controlla una usersnex.yaml scaricata: deve essere tutta valida. Ritorna quanti siti ha."""
    entries = yaml.safe_load(data.decode("utf-8"))
    if not isinstance(entries, list):
        raise ValueError("non è una lista di siti")
    for e in entries:
        _parse(e, "usersnex.yaml")
    if len(entries) < MIN_USERSNEX:
        raise ValueError(f"solo {len(entries)} siti")
    return len(entries)


def update(log: Callable[[str, str], None] = lambda m, k="info": print(m),
           cancel: threading.Event | None = None, local: dict[str, str] | None = None,
           force: bool = False, fetch: Callable[[str], bytes] = download, usersnex_url: str = "") -> dict:
    """Scarica e riconverte le liste. Ritorna {chiave: {"esito", "sites", ...}}.

    Una lista che non si scarica o che sembra rotta non tocca il file esistente. Se il file scaricato
    è identico all'ultimo (stessa impronta) non viene riscritto, a meno di force=True.
    """
    local = local or {}
    saved = info()
    target = pack_dir()
    results: dict[str, dict] = {}
    plan = ([("usersnex", "UserSnex", usersnex_url)] if usersnex_url or "usersnex" in local else []) \
        + [(k, n, URLS[k]) for k, n, _f, _c, _h in SOURCES]
    # si scarica tutto prima di convertire: la deduplica dipende dall'ordine
    raw: dict[str, bytes] = {}
    for key, name, url in plan:
        if cancel is not None and cancel.is_set():
            log(tr("aggiornamento interrotto"), "warn")
            return results
        log(tr("scarico {name}…", name=name), "info")
        try:
            raw[key] = Path(local[key]).read_bytes() if key in local else fetch(url)
            log(f"{name}: {len(raw[key]) // 1024} KB", "info")
        except Exception as e:  # noqa: BLE001
            results[key] = {"esito": "errore", "errore": str(e)}
            log(tr("{name}: download non riuscito ({e}), resta la lista attuale", name=name, e=e), "err")
    keep = ("sites", "updated", "sha256", "extended", "disabled")
    out_info = {k: {kk: vv for kk, vv in v.items() if kk in keep} for k, v in saved.items()
                if not v.get("missing") and not v.get("bundled")}

    def changed(key: str, sha: str, path: Path) -> bool:
        return force or sha != saved.get(key, {}).get("sha256") or not path.is_file()

    if "usersnex" in raw:
        sha = hashlib.sha256(raw["usersnex"]).hexdigest()
        before = saved.get("usersnex", {}).get("sites")
        try:
            n = _check_usersnex(raw["usersnex"])
        except Exception as e:  # noqa: BLE001
            results["usersnex"] = {"esito": "errore", "errore": str(e)}
            log(tr("{name}: formato non riconosciuto ({e}), resta la lista attuale", name="UserSnex", e=e), "err")
        else:
            if changed("usersnex", sha, target / "usersnex.yaml"):
                _write_bytes(target / "usersnex.yaml", raw["usersnex"])
                out_info["usersnex"] = {"sites": n, "updated": time.strftime("%Y-%m-%d %H:%M"), "sha256": sha}
                results["usersnex"] = {"esito": "aggiornata", "sites": n, "prima": before}
                log(tr("{name}: {n} siti", name="UserSnex", n=n), "ok")
            else:
                results["usersnex"] = {"esito": "invariata", "sites": n, "prima": before}
                log(tr("{name}: già aggiornata ({n} siti)", name="UserSnex", n=n), "ok")
    fresh = results.get("usersnex", {}).get("esito") in ("aggiornata", "invariata")
    names, hosts = handmade(target / "usersnex.yaml" if fresh else None)
    for key, name, file, conv, header in SOURCES:
        if key not in raw:
            # anche senza download i nomi della lista attuale vanno riservati, se no le altre li duplicano
            try:
                for s in yaml.safe_load((target / file).read_text(encoding="utf-8")) or []:
                    names.add(s["name"].lower())
                    hosts.update({host(s["check_url"]), host(s["url"])})
            except Exception:  # noqa: BLE001
                pass
            continue
        sha = hashlib.sha256(raw[key]).hexdigest()
        try:
            sites = _valid(dedupe(conv(json.loads(raw[key].decode("utf-8"))), names, hosts))
        except Exception as e:  # noqa: BLE001
            results[key] = {"esito": "errore", "errore": f"formato non riconosciuto: {e}"}
            log(tr("{name}: formato non riconosciuto ({e}), resta la lista attuale", name=name, e=e), "err")
            continue
        if len(sites) < MIN_SITES:
            results[key] = {"esito": "errore", "errore": f"solo {len(sites)} siti"}
            log(tr("{name}: solo {n} siti, sembra rotta: resta la lista attuale", name=name, n=len(sites)), "err")
            continue
        before = saved.get(key, {}).get("sites")
        if not changed(key, sha, target / file):
            results[key] = {"esito": "invariata", "sites": len(sites), "prima": before}
            log(tr("{name}: già aggiornata ({n} siti)", name=name, n=len(sites)), "ok")
            continue
        write(target / file, header + f"# Aggiornato il {time.strftime('%Y-%m-%d')}.\n", sites)
        ext = sum(bool(s.get("extended")) for s in sites)
        off = sum(not s.get("enabled", True) for s in sites)
        out_info[key] = {"sites": len(sites), "updated": time.strftime("%Y-%m-%d %H:%M"), "sha256": sha,
                         "extended": ext, "disabled": off}
        results[key] = {"esito": "aggiornata", "sites": len(sites), "prima": before, "extended": ext,
                        "disabled": off}
        diff = "" if before is None else f" ({len(sites) - before:+d})"
        log(tr("{name}: {n} siti", name=name, n=len(sites)) + diff
            + (tr(", {n} nella lista estesa", n=ext) if ext else "")
            + (tr(", {n} protetti e disattivati", n=off) if off else ""), "ok")
    _save_info(out_info)
    return results
