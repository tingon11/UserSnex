# UserSnex.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Esportazione dei risultati: JSON completo, CSV e report HTML (tema scuro come l'interfaccia)."""
from __future__ import annotations

import csv
import html
import json
import os
import time
from dataclasses import asdict

from . import __version__
from .correlate import IdentityReport
from .models import SiteResult, Status


def _base(out_dir: str, username: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in username) or "username"
    return os.path.join(out_dir, f"usersnex-{safe}-{time.strftime('%Y%m%d-%H%M%S')}")


def to_dict(results: list[SiteResult], report: IdentityReport) -> dict:
    return {
        "tool": f"UserSnex {__version__}",
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "username": report.username,
        "username_uniqueness": round(report.username_uniqueness, 3),
        "clusters": report.clusters,
        "accounts": [asdict(a) for a in report.accounts],
        "edges": [asdict(e) for e in report.edges],
        "results": [r.to_dict() for r in results],
    }


def export_json_csv(results: list[SiteResult], report: IdentityReport, out_dir: str) -> list[str]:
    base = _base(out_dir, report.username)
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(to_dict(results, report), f, indent=1, ensure_ascii=False)
    scores = {a.site: a for a in report.accounts}
    with open(base + ".csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["sito", "categoria", "stato", "url", "confidenza", "gruppo", "nome", "località", "bio",
                    "link", "creato", "http", "errore"])
        for r in results:
            a = scores.get(r.site)
            w.writerow([r.site, r.category, r.status.value, r.url, a.confidence if a else "",
                        (a.cluster + 1 if a and a.cluster >= 0 else ""), r.profile.display_name,
                        r.profile.location, r.profile.bio, " ".join(r.profile.links), r.profile.created_at,
                        r.http_status or "", r.error])
    return [base + ".json", base + ".csv"]


CSS = """
body{background:#07090c;color:#d5dde6;font:13px 'Segoe UI',Inter,sans-serif;margin:0;padding:28px 36px}
h1{font-size:22px;margin:0 0 4px} .cap{font:bold 10px Consolas,monospace;color:#6e7b89;letter-spacing:.06em;
text-transform:uppercase} .accent{color:#7b95dd} .card{background:#0d1117;border:1px solid #1c2530;
border-radius:4px;padding:14px 16px;margin:12px 0} .cap:before{content:'';display:inline-block;width:2px;
height:10px;background:#7b95dd;margin-right:7px} table{border-collapse:collapse;width:100%}
th{font:bold 10px Consolas,monospace;color:#6e7b89;text-align:left;padding:7px 8px;background:#07090c}
td{padding:6px 8px;border-top:1px solid #1c2530;vertical-align:top} a{color:#7b95dd;text-decoration:none}
.ok{color:#34d399}.warn{color:#fbbf24}.err{color:#f87171}.dim{color:#6e7b89}.bar{height:6px;background:#161d27;
border-radius:3px;width:120px;display:inline-block;vertical-align:middle;margin-right:8px}
.bar i{display:block;height:6px;border-radius:3px;background:#7b95dd} .stats{display:flex;gap:8px}
.stats .card{flex:1;margin:0} .num{font:bold 22px Consolas,monospace} small{color:#6e7b89}
"""


def _conf_class(c: int) -> str:
    return "ok" if c >= 70 else "warn" if c >= 40 else "dim"


def export_html(results: list[SiteResult], report: IdentityReport, out_dir: str) -> str:
    e = html.escape
    by_site = {r.site: r for r in results}
    found = [r for r in results if r.status == Status.FOUND]
    errors = [r for r in results if r.status == Status.ERROR]
    main = report.clusters[0] if report.clusters else []
    rows = []
    for a in report.accounts:
        r = by_site[a.site]
        p = r.profile
        reasons = "<br>".join(f"{e(x.signal)} <small>{e(x.detail)}</small>" for x in a.evidence[:6])
        grp = f"#{a.cluster + 1}" if a.cluster >= 0 else "—"
        rows.append(
            f"<tr><td><b>{e(a.site)}</b><br><small>{e(r.category)}</small></td>"
            f"<td><span class='bar'><i style='width:{a.confidence}%'></i></span>"
            f"<span class='{_conf_class(a.confidence)}'>{a.confidence}%</span></td><td>{grp}</td>"
            f"<td><a href='{e(r.url)}'>{e(r.url)}</a><br>{e(p.display_name)} <small>{e(p.location)}</small>"
            f"<br><small>{e(p.bio[:200])}</small></td><td>{reasons}</td></tr>")
    err_rows = "".join(f"<tr><td>{e(r.site)}</td><td class='err'>{e(r.error)}</td></tr>" for r in errors)
    page = f"""<!DOCTYPE html><html lang="it"><head><meta charset="utf-8">
<title>UserSnex · {e(report.username)}</title><style>{CSS}</style></head><body>
<div class="cap">UserSnex / report</div><h1>User<span class="accent">Snex</span> · {e(report.username)}</h1>
<small>generato il {time.strftime('%Y-%m-%d %H:%M')} · UserSnex {__version__} · solo dati pubblici</small>
<div class="stats" style="margin-top:14px">
<div class="card"><div class="cap">Siti controllati</div><div class="num">{len(results)}</div></div>
<div class="card"><div class="cap">Trovati</div><div class="num">{len(found)}</div></div>
<div class="card"><div class="cap">Identità principale</div><div class="num ok">{len(main)}</div></div>
<div class="card"><div class="cap">Unicità username</div><div class="num">{int(report.username_uniqueness*100)}%</div></div>
<div class="card"><div class="cap">Errori</div><div class="num err">{len(errors)}</div></div></div>
<div class="card"><div class="cap">Account trovati e confidenza d'identità</div>
<table><tr><th>SITO</th><th>CONFIDENZA</th><th>GRUPPO</th><th>PROFILO</th><th>INDIZI</th></tr>
{''.join(rows) or '<tr><td colspan=5 class=dim>nessun account trovato</td></tr>'}</table></div>
{f'<div class="card"><div class="cap">Errori</div><table>{err_rows}</table></div>' if errors else ''}
</body></html>"""
    path = _base(out_dir, report.username) + "-report.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    return path
