"""Diagnosi TikTok: confronta oEmbed e pagina profilo per alcuni username (solo richieste pubbliche)."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx  # noqa: E402

from usersnex.engine import USER_AGENT  # noqa: E402

NAMES = ["giannamjoyce", "tiktok", "khaby.lame", "zq9x7k2m4p1v8"]
H = {"User-Agent": USER_AGENT, "Accept-Language": "it,en;q=0.8"}
with httpx.Client(timeout=20, follow_redirects=True, headers=H) as c:
    for n in NAMES:
        r = c.get(f"https://www.tiktok.com/oembed?url=https://www.tiktok.com/@{n}", headers={"Accept": "application/json"})
        print(f"OEMBED {n}: {r.status_code} len={len(r.text)} {r.text[:160]!r}")
        r = c.get(f"https://www.tiktok.com/@{n}", headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
        t = r.text
        m = re.search(r'"statusCode":(\d+)', t)
        u = re.search(r'"uniqueId":"([^"]+)"', t)
        print(f"PAGE   {n}: {r.status_code} url={r.url} len={len(t)} statusCode={m.group(1) if m else None} "
              f"uniqueId={u.group(1) if u else None} rehydration={'__UNIVERSAL_DATA_FOR_REHYDRATION__' in t} "
              f"captcha={'captcha' in t.lower()} title={re.search(r'<title[^>]*>([^<]*)', t).group(1)[:80] if '<title' in t else None!r}")
