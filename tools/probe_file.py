# UserSnex.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Come probe_candidates.py ma su un file a scelta; scrive <file>_report.json.

    python tools/probe_file.py tools/candidates_regioni.yaml
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from usersnex.engine import Engine  # noqa: E402
from usersnex.models import Status  # noqa: E402
from usersnex.selftest import fake_username  # noqa: E402
from usersnex.sites import _parse  # noqa: E402


async def main(path: Path) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    sites = [_parse(x, path.name) for x in raw]
    eng = Engine(sites, concurrency=16, timeout=20, cache=None, extended=True)
    fake = fake_username()
    report = {}
    for s in sites:
        hit, tried = None, []
        for u in s.known:
            r = (await eng.check_pairs([(s, u)]))[0]
            tried.append(f"{u}={r.status.value}/{r.http_status}" + (f" ({r.error})" if r.error else ""))
            if r.status == Status.FOUND:
                hit = r
                break
            if r.status == Status.ERROR and r.http_status in (401, 403, 429, None):
                break
        f = (await eng.check_pairs([(s, fake)]))[0]
        tried.append(f"{fake}={f.status.value}/{f.http_status}" + (f" ({f.error})" if f.error else ""))
        if hit and f.status == Status.NOT_FOUND:
            verdict = "ok"
        elif hit and f.status == Status.FOUND:
            verdict = "falso positivo"
        elif hit:
            verdict = "errore sul falso"
        elif any("=error" in t for t in tried):
            verdict = "errore"
        else:
            verdict = "nessuno trovato"
        prof = hit.profile if hit else None
        report[s.name] = {"esito": verdict, "known": hit.username if hit else None, "prove": tried,
                          "nome": prof.display_name if prof else ""}
        print(f"{verdict:16} {s.name:40} {' | '.join(tried)}")
    out = path.with_name(path.stem + "_report.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nok:", sum(v["esito"] == "ok" for v in report.values()), "su", len(report), "->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(Path(sys.argv[1]))))
