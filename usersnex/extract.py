# UserSnex.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Estrazione dei segnali di profilo da risposte JSON o HTML."""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import html
import re
from typing import Any

from .models import Profile

URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.I)


def get_path(data: Any, path: str) -> Any:
    """Risolve un percorso puntato tipo 'them.0.basics.username'."""
    cur = data
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            if not part.isdigit() or int(part) >= len(cur):
                return None
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    return ""


def _date(value: Any) -> str:
    """Timestamp numerici (secondi o millisecondi) in data leggibile; il resto com'è."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        secs = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(secs, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return str(value)
    return _as_text(value)


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def profile_from_json(data: Any, fields: dict) -> Profile:
    p = Profile()
    for name in ("display_name", "bio", "location", "avatar", "created_at"):
        spec = fields.get(name)
        if not spec:
            continue
        for path in spec if isinstance(spec, list) else [spec]:
            raw = get_path(data, path)
            text = _date(raw) if name == "created_at" else _as_text(raw)
            if text:
                setattr(p, name, _strip_html(text) if name == "bio" else text)
                break
    links: list[str] = []
    for spec in fields.get("links", []) or []:
        # forme ammesse: "percorso" oppure {path, template: "https://x.com/{}", key: campo negli elementi}
        if isinstance(spec, str):
            path, template, key = spec, "{}", ""
        else:
            path, template, key = spec["path"], spec.get("template", "{}"), spec.get("key", "")
        value = get_path(data, path)
        values = value if isinstance(value, list) else [value]
        for v in values:
            if isinstance(v, dict):
                v = v.get(key) if key else (v.get("url") or v.get("value"))
            text = _as_text(v)
            if text:
                links.append(_strip_html(template.replace("{}", text)))
    p.links = _dedupe(links + URL_RE.findall(p.bio))
    return p


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = a["content"].strip()
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


# titoli che non sono il nome di nessuno: pagine di consenso, login, errori, verifiche
JUNK_TITLE_RE = re.compile(
    r"privacy|cookie|consent|\bgate\b|log ?in\b|sign ?in\b|sign ?up\b|accedi|registrati|access denied|"
    r"just a moment|attention required|are you a robot|captcha|\berror\b|not found|\b404\b|page not|"
    r"pagina non|forbidden|unavailable|redirect", re.I)
# separatori dopo i quali il titolo di una pagina contiene il nome del sito o una descrizione
_TITLE_CUT_RE = re.compile(r"\s+[|•·–—-]\s+|\s+\((?:@|&#64;)[^)]*\)|\s+on\s+[A-Z]|\s+su\s+[A-Z]|,\s+")


def clean_display_name(name: str, site: str = "", username: str = "") -> str:
    """Il nome di una persona dal titolo di una pagina: "Gianna Harner (@gianna) • Threads" -> "Gianna Harner".
    Restituisce "" per i titoli spazzatura (consenso privacy, login, errori) o uguali al nome del sito."""
    name = html.unescape(name or "").strip()
    if not name or JUNK_TITLE_RE.search(name):
        return ""
    name = _TITLE_CUT_RE.split(name, maxsplit=1)[0].strip()
    name = re.sub(r"^@|\s+@\S+$", "", name).strip(" .:|-")
    if site:
        base = re.sub(r"\s*\(.*\)$", "", site).strip().lower()
        if name.lower() in (site.lower(), base) or name.lower().startswith(base + " ") and len(base) > 3:
            return ""
    return name[:80]


def profile_from_html(html_text: str, site: str = "", username: str = "") -> Profile:
    parser = _MetaParser()
    try:
        parser.feed(html_text)
    except Exception:  # HTML malformato: teniamo quel che c'è
        pass
    m = parser.meta
    raw = m.get("og:title") or m.get("twitter:title") or parser.title.strip()
    if raw and JUNK_TITLE_RE.search(raw):
        return Profile()             # pagina di consenso / login / errore: niente di questa pagina è del profilo
    bio = m.get("og:description") or m.get("description") or ""
    return Profile(
        display_name=clean_display_name(raw, site, username),
        bio=bio,
        avatar=m.get("og:image") or m.get("twitter:image") or "",
        links=_dedupe(URL_RE.findall(bio)),
    )


def _dedupe(items: list[str]) -> list[str]:
    seen, out = set(), []
    for it in items:
        key = it.rstrip("/.,").lower()
        if key not in seen:
            seen.add(key)
            out.append(it.rstrip(".,"))
    return out
