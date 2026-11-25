#!/usr/bin/env python3
# UserSnex — screenshot del manuale.  Copyright (C) 2026 Andrea Cumini <andrea@osintinfo.net>
# SPDX-License-Identifier: GPL-3.0-or-later  (+ attribuzione obbligatoria, GPL 7(b): vedi ADDITIONAL_TERMS.md)
"""Rifà gli screenshot del manuale (docs/manual/<lingua>/*.png) con numeri e frecce.

    python tools/make_manual.py                 # tutte le lingue
    python tools/make_manual.py --lang it,en    # solo alcune

Gira solo su Linux con Xvfb: si avvia da solo dentro uno schermo virtuale (xvfb-run), dove c'è
soltanto la finestra di UserSnex. Sullo schermo vero si rifiuta, perché la cattura di una zona dello
schermo copierebbe anche le finestre che ci stanno sopra.

Ogni lingua gira in un processo a parte, con una cartella di lavoro temporanea (USERSNEX_HOME): la
tua config.json, la cache e i risultati non vengono toccati. Nessuna richiesta esce dal PC: la ricerca
dimostrativa usa risposte inventate (utente "lunaverdi92", Luna Verdi di Torino, e un omonimo di
Buenos Aires). I numeri sulle immagini corrispondono agli elenchi numerati di MANUAL.md.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "manual"
LANGS = ("it", "en", "es", "de", "fr", "nl", "el", "sq", "pl")
USER = "lunaverdi92"
W, H = 1400, 860

MARK = (245, 158, 11)          # ambra: si vede sul tema scuro e non si confonde con i colori del programma
MARK_TEXT = (17, 17, 17)


# ============================================================================ disegno di numeri e frecce
def annotate(img, marks):
    """marks: [(numero, (x, y) bersaglio, (dx, dy) posizione del numero rispetto al bersaglio)]."""
    from PIL import ImageDraw, ImageFont
    import math
    img = img.convert("RGB")
    d = ImageDraw.Draw(img)
    font = None
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(name, 15)
            break
        except OSError:
            continue
    font = font or ImageFont.load_default()
    r = 13
    for num, (tx, ty), (dx, dy) in marks:
        bx, by = tx + dx, ty + dy
        bx = min(max(bx, r + 2), img.width - r - 3)
        by = min(max(by, r + 2), img.height - r - 3)
        dist = math.hypot(tx - bx, ty - by)
        if dist > r + 6:
            ux, uy = (tx - bx) / dist, (ty - by) / dist
            sx, sy = bx + ux * r, by + uy * r
            ex, ey = tx - ux * 2, ty - uy * 2
            for width, col in ((6, (0, 0, 0)), (3, MARK)):
                d.line((sx, sy, ex, ey), fill=col, width=width)
            ah, aw = 11, 6
            px, py = -uy, ux
            tip = (ex, ey)
            left = (ex - ux * ah + px * aw, ey - uy * ah + py * aw)
            right = (ex - ux * ah - px * aw, ey - uy * ah - py * aw)
            d.polygon([tip, left, right], fill=MARK, outline=(0, 0, 0))
        d.ellipse((bx - r - 1, by - r - 1, bx + r + 1, by + r + 1), fill=(0, 0, 0))
        d.ellipse((bx - r, by - r, bx + r, by + r), fill=MARK)
        text = str(num)
        tb = d.textbbox((0, 0), text, font=font)
        d.text((bx - (tb[2] - tb[0]) / 2 - tb[0], by - (tb[3] - tb[1]) / 2 - tb[1]), text, fill=MARK_TEXT, font=font)
    return img


def save_png(img, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").quantize(colors=256, method=2, dither=0).save(path, optimize=True)


# ============================================================================ dati inventati
def _avatar(seed: int, size: int = 400):
    """Ritratto stilizzato inventato (nessuna foto vera)."""
    from PIL import Image, ImageDraw
    bgs = [((46, 92, 138), (120, 170, 200)), ((120, 60, 40), (220, 150, 90))]
    top, bottom = bgs[seed % 2]
    im = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(im)
    for y in range(size):
        t = y / size
        d.line((0, y, size, y), fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    skin = (236, 196, 160) if seed == 0 else (200, 150, 110)
    hair = (70, 40, 30) if seed == 0 else (30, 30, 30)
    s = size / 400
    if seed == 0:
        d.ellipse((110 * s, 70 * s, 290 * s, 300 * s), fill=hair)                       # capelli lunghi
        d.rectangle((110 * s, 180 * s, 290 * s, 330 * s), fill=hair)
    d.ellipse((140 * s, 95 * s, 260 * s, 245 * s), fill=skin)                            # viso
    d.ellipse((70 * s, 280 * s, 330 * s, 520 * s), fill=(40, 150, 110) if seed == 0 else (60, 60, 160))
    if seed == 1:
        d.chord((135 * s, 80 * s, 265 * s, 170 * s), 180, 360, fill=hair)
    d.ellipse((170 * s, 155 * s, 182 * s, 167 * s), fill=(40, 40, 40))
    d.ellipse((218 * s, 155 * s, 230 * s, 167 * s), fill=(40, 40, 40))
    d.arc((180 * s, 180 * s, 220 * s, 210 * s), 20, 160, fill=(150, 60, 60), width=max(1, int(3 * s)))
    return im


def _img_bytes(im, size: int, fmt: str) -> bytes:
    import io
    b = io.BytesIO()
    im.resize((size, size)).save(b, fmt, **({"quality": 80} if fmt == "JPEG" else {}))
    return b.getvalue()


def demo_transport():
    import zlib
    import httpx
    luna, luis = _avatar(0), _avatar(1)
    images = {
        "avatars.githubusercontent.com": (_img_bytes(luna, 460, "PNG"), "image/png"),
        "files.mastodon.social": (_img_bytes(luna, 400, "JPEG"), "image/jpeg"),
        "media.dev.to": (_img_bytes(luna, 200, "JPEG"), "image/jpeg"),
        "codeberg.org/avatars": (_img_bytes(luna, 290, "PNG"), "image/png"),
        "images.chesscomfiles.com": (_img_bytes(luis, 200, "JPEG"), "image/jpeg"),
    }
    site = "https://lunaverdi.example"
    api = {
        "api.github.com": {"login": USER, "name": "Luna Verdi", "bio": "Web designer e fotografa. Open source nel tempo libero.",
                           "location": "Torino", "blog": site, "twitter_username": USER,
                           "avatar_url": "https://avatars.githubusercontent.com/u/9900001?v=4",
                           "created_at": "2016-04-12T09:00:00Z"},
        "keybase.io": {"them": [{"basics": {"username": USER, "ctime": 1488000000},
                                 "profile": {"full_name": "Luna Verdi", "bio": "design · foto · montagna",
                                             "location": "Torino, Italia"},
                                 "proofs_summary": {"all": [{"service_url": f"https://github.com/{USER}"},
                                                            {"service_url": site}]}}]},
        "dev.to": {"name": "Luna Verdi", "summary": "Web designer: CSS, accessibilità e fotografia di montagna",
                   "location": "Torino", "website_url": site, "github_username": USER,
                   "profile_image": "https://media.dev.to/uploads/user/profile_image/77001/luna.jpg",
                   "joined_at": "Mar 3, 2019"},
        "codeberg.org": {"full_name": "Luna V.", "description": "Temi CSS liberi", "location": "",
                         "website": site, "avatar_url": "https://codeberg.org/avatars/5f1c0de",
                         "created": "2021-01-20T10:00:00Z"},
        "mastodon.social": {"id": "110000001", "display_name": "Luna 🌿",
                            "note": "<p>Foto, CSS e montagne. Torino.</p>",
                            "avatar": "https://files.mastodon.social/accounts/avatars/110/000/001/original/luna.jpg",
                            "created_at": "2022-11-05T00:00:00Z", "fields": [{"name": "web", "value": site}]},
        "api.chess.com": {"player_id": 4400123, "name": "Luis Verdi", "location": "Buenos Aires",
                          "avatar": "https://images.chesscomfiles.com/uploads/v1/user/4400123.jpg",
                          "joined": 1420070400},
        "lichess.org": {"id": USER, "username": USER, "createdAt": 1430000000000,
                        "profile": {"realName": "Luis Verdi", "location": "Buenos Aires",
                                    "bio": "Ajedrez blitz", "links": f"https://www.chess.com/member/{USER}"}},
    }
    page = "<html><head></head><body><p>profile {t}</p></body></html>"   # senza nome: account "senza legami"

    def handler(req: httpx.Request) -> httpx.Response:
        h, path = req.url.host, req.url.path
        for key, (data, ct) in images.items():
            if (h + path).startswith(key) or h == key:
                return httpx.Response(200, content=data, headers={"content-type": ct})
        if h == "forum-montagna.example":          # il sito della "Nuova risorsa" d'esempio
            return httpx.Response(200 if path == f"/u/{USER}" else 404, text="profilo")
        if USER not in str(req.url):
            return httpx.Response(404)
        if h in api:
            return httpx.Response(200, json=api[h])
        if h == "www.reddit.com":
            return httpx.Response(429, headers={"Retry-After": "60"})
        if h == "hub.docker.com":
            return httpx.Response(403)
        if zlib.crc32(h.encode()) % 40 == 0:
            return httpx.Response(200, text=page.format(t=USER), headers={"content-type": "text/html"})
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


# ============================================================================ un processo per lingua
def shots(lang: str, out: Path) -> None:
    import usersnex.engine as eng
    from usersnex import avatars, mytargets
    from usersnex.i18n import tr
    transport = demo_transport()
    orig = eng.Engine.__init__

    def init(self, sites, **kw):
        kw["transport"] = transport
        kw["cache"] = None
        orig(self, sites, **kw)
    eng.Engine.__init__ = init
    real_fetch = avatars.fetch
    avatars.fetch = lambda results, **kw: real_fetch(results, transport=transport,
                                                     **{k: v for k, v in kw.items() if k != "transport"})
    mytargets.save([mytargets.build("Forum Fotografia (esempio)", "forum", "https://forum-foto.example/u/{username}",
                                    "", "absent", "Utente non trovato", "admin")])

    from PIL import ImageGrab
    import usersnex.gui.app as appmod
    app = appmod.App("")
    xdisplay = os.environ.get("DISPLAY") if sys.platform.startswith("linux") else None

    def grab(win=None, pad=0):
        win = win or app
        win.update()
        x, y = win.winfo_rootx() - pad, win.winfo_rooty() - pad
        w, h = win.winfo_width() + 2 * pad, win.winfo_height() + 2 * pad
        kw = {"xdisplay": xdisplay} if xdisplay else {"all_screens": True}
        return ImageGrab.grab(bbox=(x, y, x + w, y + h), **kw), (x, y)

    def box(w, origin):
        x, y = w.winfo_rootx() - origin[0], w.winfo_rooty() - origin[1]
        return x, y, x + w.winfo_width(), y + w.winfo_height()

    def at(b, where="c", inset=0):
        x0, y0, x1, y1 = b
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        return {"c": (cx, cy), "l": (x0 + inset, cy), "r": (x1 - inset, cy), "t": (cx, y0 + inset),
                "b": (cx, y1 - inset)}[where]

    def find(parent, cls, text=None):
        out = []

        def walk(x):
            for c in x.winfo_children():
                if c.__class__.__name__ == cls and (text is None or (hasattr(c, "cget") and c.cget("text") == text)):
                    out.append(c)
                walk(c)
        walk(parent)
        return out

    def save(name, img, marks):
        save_png(annotate(img, [m for m in marks if m]), out / lang / name)
        print(f"  {lang}/{name}")

    def pad(img, right=0, bottom=0):
        """Allarga l'immagine a destra e in basso: lì vanno i numeri, così non coprono niente."""
        from PIL import Image
        bg = img.convert("RGB").getpixel((img.width - 3, img.height - 3))
        new = Image.new("RGB", (img.width + right, img.height + bottom), bg)
        new.paste(img.convert("RGB"), (0, 0))
        return new

    def M(num, target, badge):
        """Numero `num` nel punto `badge`, con la freccia verso `target` (coordinate dell'immagine)."""
        return num, target, (badge[0] - target[0], badge[1] - target[1])

    steps = []

    def later(ms, fn):
        steps.append((ms, fn))

    def run_steps(i=0):
        if i >= len(steps):
            app.after(300, app.destroy)
            return
        ms, fn = steps[i]

        def go():
            try:
                fn()
            except Exception:  # noqa: BLE001
                import traceback
                traceback.print_exc()
            run_steps(i + 1)
        app.after(ms, go)

    # ---------------------------------------------------------------- 01-02: primo avvio
    def welcome():
        return next(w for w in app.winfo_children() if w.__class__.__name__ == "WelcomeWindow")

    def s01():
        w = welcome()
        img, o = grab(w)
        btn = [b for b in find(w, "CTkButton") if b.winfo_ismapped()]
        marks = [(1, at(box(btn[0], o), "l"), (-40, 0))] if btn else []
        save("01-language.png", img, marks)
        w._pick_language(lang)

    def s02():
        w = welcome()
        img, o = grab(w)
        rej = [b for b in w.btn_ok.master.winfo_children() if b is not w.btn_ok and b.__class__.__name__ == "CTkButton"]
        img = pad(img, right=46, bottom=44)
        tb = box(w.text, o)
        marks = [M(1, (tb[2], (tb[1] + tb[3]) / 2), (img.width - 23, (tb[1] + tb[3]) / 2))]
        for num, b in ((2, w.btn_ok), (3, rej[0] if rej else None)):
            if b is not None:
                bb = box(b, o)
                marks.append(M(num, at(bb, "b", 1), ((bb[0] + bb[2]) / 2, img.height - 22)))
        save("02-terms.png", img, marks)
        w.text.yview_moveto(1.0)

    def accept():
        w = welcome()
        w._accept_ready = True
        w._accept()

    later(1500, s01)
    later(700, s02)
    later(700, accept)

    # ---------------------------------------------------------------- ricerca dimostrativa
    def start():
        app.geometry(f"{W}x{H}+0+0")
        app.user_entry.delete(0, "end")
        app.user_entry.insert(0, USER)
        app._toggle_search()

    def wait_done():
        pass

    later(1500, start)
    for _ in range(14):                    # la ricerca finta dura pochi secondi, poi gli avatar
        later(1000, wait_done)

    # ---------------------------------------------------------------- posizioni comuni
    def gutter_x(o):
        """Centro dello spazio fra la barra laterale e la parte principale: lì i numeri non coprono testo."""
        return (box(app.btn_search, o)[2] + box(app.c_checked, o)[0]) / 2

    def side(num, widget, o, y=None):
        """Numero nello spazio a destra della barra laterale, accanto al controllo."""
        b = box(widget, o)
        yy = (b[1] + b[3]) / 2 if y is None else y
        return M(num, (b[2] - 2, yy), (gutter_x(o), yy))

    def into(num, widget, o, inset=12):
        """Numero nello spazio a sinistra della parte principale, con la freccia verso il controllo."""
        b = box(widget, o)
        yy = (b[1] + b[3]) / 2
        return M(num, (b[0] + inset, yy), (gutter_x(o), yy))

    def above_card(num, card, o):
        b = box(card, o)
        return M(num, ((b[0] + b[2]) / 2, b[1] + 2), ((b[0] + b[2]) / 2, b[1] - 15))

    def filter_mark(num, o):
        """Numero fra l'ultima scheda e il filtro, se c'è posto; altrimenti in fondo al filtro."""
        fb = box(app.filter, o)
        tabs_r = max(box(t["box"], o)[2] for t in app.tabbar._tabs.values())
        y = (fb[1] + fb[3]) / 2
        if fb[0] - tabs_r >= 32:
            return M(num, (fb[0] + 2, y), ((tabs_r + fb[0]) / 2, y))
        return M(num, (fb[2] - 16, y), (fb[2] - 16, y))

    def header_y(tree, o):
        tb = box(tree, o)
        first = tree.get_children()[0]
        return tb[1] + tree.bbox(first)[1] / 2

    def col_box(tree, iid, col, o):
        tb = box(tree, o)
        bx, by, bw, bh = tree.bbox(iid, col)
        return tb[0] + bx, tb[1] + by, tb[0] + bx + bw, tb[1] + by + bh

    def below_rows(num, tree, col, o, dx=None):
        """Numero nello spazio vuoto sotto l'ultima riga, con la freccia verso la cella dell'ultima riga."""
        last = tree.get_children()[-1]
        cb = col_box(tree, last, col, o)
        x = (cb[0] + cb[2]) / 2 if dx is None else cb[0] + dx
        return M(num, (x, (cb[1] + cb[3]) / 2 + 4), (x, cb[3] + 30))

    # ---------------------------------------------------------------- 03: finestra principale
    def s03():
        app._show_tab("results")
        app.t_results.toggle_sort("conf")
        app.t_results.toggle_sort("conf")          # Z-A: freccia ▼ sull'intestazione, stesso ordine
        app._select("GitHub")
        app.t_results.tree.selection_set("r|GitHub")
        app.update()
        img, o = grab()
        tree = app.t_results.tree
        hy = header_y(tree, o)
        rows = tree.get_children()
        with_img = [i for i in rows if tree.item(i, "image")]
        av = col_box(tree, with_img[-1], "#0", o) if with_img else None
        last = col_box(tree, rows[-1], "#0", o)
        av0 = col_box(tree, rows[0], "#0", o)
        conf = col_box(tree, rows[0], "conf", o)
        exp = find(app, "CTkButton", tr("Esporta JSON · CSV"))
        ub = box(app.user_entry, o)
        mb = box(app.btn_manual, o)
        cb = box(app.console, o)
        tabs_l = min(box(t["box"], o)[0] for t in app.tabbar._tabs.values())
        tab_y = (box(app.tabbar, o)[1] + box(app.tabbar, o)[3]) / 2
        found_b, ident_b = box(app.c_found, o), box(app.c_identity, o)
        cx8 = (found_b[2] + ident_b[0]) / 2
        marks = [
            M(1, (ub[0] + 200, ub[3] - 2), (ub[0] + 200, ub[3] + 24)),
            side(2, app.btn_search, o),
            side(3, app.btn_selftest, o),
            side(4, app.seed_entries["name"], o),
            side(5, exp[0], o) if exp else None,
            side(6, app.lang_menu, o, y=(box(app.sw_cache, o)[1] + box(app.sw_cache, o)[3]) / 2),
            side(7, app.lang_menu, o),
            M(8, (cx8, found_b[1] + 2), (cx8, found_b[1] - 15)),
            M(9, (tabs_l + 12, tab_y), (gutter_x(o) - 3, tab_y)),
            filter_mark(10, o),
            M(11, ((av[0] + av[2]) / 2, (av[1] + av[3]) / 2 + 8), ((av[0] + av[2]) / 2, last[3] + 30)) if av else None,
            M(12, at(box(app.det_avatar, o), "r", 0), (box(app.det_avatar, o)[2] + 40, at(box(app.det_avatar, o))[1])),
            M(13, (cb[0] + 64, cb[1] - 16), (cb[0] + 64, cb[1] - 16)),
            M(14, ((mb[0] + mb[2]) / 2, mb[3] - 1), ((mb[0] + mb[2]) / 2, mb[3] + 24)),
            M(15, (conf[0] + 6, hy), ((av0[0] + av0[2]) / 2, hy)),
        ]
        save("03-main.png", img, marks)

    later(500, s03)

    # ---------------------------------------------------------------- 04: identità
    def s04():
        app.seed_entries["name"].delete(0, "end")
        app.seed_entries["name"].insert(0, "Luna Verdi")
        app._recorrelate()
        app._show_tab("identity")
        app._select("GitHub")
        app.t_identity.tree.selection_set("i|GitHub")

    def s04b():
        img, o = grab()
        tree = app.t_identity.tree
        db = box(app.det_avatar, o)
        marks = [
            below_rows(1, tree, "grp", o),
            below_rows(2, tree, "conf", o),
            below_rows(3, tree, "signals", o, dx=70),
            side(4, app.seed_entries["name"], o),
            M(5, (db[2] + 50, db[1] - 20), (db[2] + 50, db[1] - 20)),
            above_card(6, app.c_identity, o),
        ]
        save("04-identity.png", img, marks)

    later(400, s04)
    later(1200, s04b)

    # ---------------------------------------------------------------- 05: grafo
    def s05():
        app.seed_entries["name"].delete(0, "end")
        app._recorrelate()
        app._show_tab("graph")

    def s05b():
        g = app.graph
        g.select(None)                            # niente evidenziazione: tutti i nodi con i loro colori
        g._fit_auto()
        app.update()

    def s05c():
        g = app.graph
        img, o = grab()
        cb = box(g.canvas, o)

        def node(nid):
            n = g.nodes.get(nid)
            if n is None or n.id in g.hidden:
                return None
            x, y = g._to_screen(n.x, n.y)
            return cb[0] + x, cb[1] + y

        def near(num, nid, dx, dy):
            p = node(nid)
            return M(num, p, (p[0] + dx, p[1] + dy)) if p else None
        names = sorted(n.id for n in g.nodes.values() if n.kind == "name")
        name_id = "name:luis verdi" if "name:luis verdi" in names else (names[0] if names else None)
        user_id = next((n.id for n in g.nodes.values() if n.kind == "user"), None)
        lb = box(g.layout_box, o)
        tb = box(g.tools, o)

        def lay(num, widget):
            b = box(widget, o) if widget is not None else lb
            y = (b[1] + b[3]) / 2
            return M(num, (lb[2] - 4, y), (lb[2] + 20, y))
        btns = find(g.layout_box, "CTkButton")
        hdr = find(g.layout_box, "CTkLabel")
        marks = [
            near(1, user_id, -44, -26),
            near(2, "group:0", -44, -26),
            near(3, "group:1", -36, -30),
            near(4, "group:-1", 40, -28),
            near(5, "acc:Codeberg", -44, 30),
            lay(6, hdr[0] if hdr else None),
            lay(7, g.btn_untangle),
            lay(8, btns[5] if len(btns) > 5 else None),
            lay(9, g.btn_loose),
            M(10, (tb[0] + 2, (tb[1] + tb[3]) / 2), (tb[0] - 22, (tb[1] + tb[3]) / 2)),
            near(11, name_id, 48, 12) if name_id else None,
        ]
        save("05-graph.png", img, marks)

    later(600, s05)
    later(2500, s05b)
    later(800, s05c)

    # ---------------------------------------------------------------- 06: siti
    def s06():
        app._show_tab("sites")
        app.filter.delete(0, "end")
        app.filter.insert(0, "mastodon")
        app._fill_sites()
        tree = app.t_sites.tree
        rows = tree.get_children()
        if len(rows) > 3:                         # un sito d'esempio al bando, selezionato
            tree.selection_set(rows[3])
            app._ban_sites(tree)
            app._fill_sites()
            iid = tree.get_children()[3]
            tree.selection_set(iid)
            tree.see(rows[0])

    def s06b():
        img, o = grab()
        tree = app.t_sites.tree
        hy = header_y(tree, o)
        first = tree.get_children()[0]

        def head(num, col):
            cb = col_box(tree, first, col, o)
            return M(num, (cb[2] - 18, hy), (cb[2] - 18, hy))

        def corner(num, widget):
            b = box(widget, o)
            return M(num, (b[2] - 3, b[3] + 2), (b[2] - 3, b[3] + 2))
        marks = [
            head(1, "state"),
            head(2, "src"),
            filter_mark(3, o),
            side(4, app.btn_selftest, o),
            corner(5, app.btn_ban),
            corner(6, app.btn_unban),
            corner(7, app.btn_edit_site),
        ]
        save("06-sites.png", img, marks)
        tree.selection_set(tree.get_children()[3])
        app._unban_sites(tree)
        app.filter.delete(0, "end")
        app._fill_sites()
        app._refill_tables()

    later(500, s06)
    later(900, s06b)

    # ---------------------------------------------------------------- 07: config
    def s07():
        app._show_tab("config")

    def s07b():
        img, o = grab()
        sw = box(app.sw_autotest, o)
        newb = find(app.pages["config"], "CTkButton", "+  " + tr("Nuova risorsa"))[0]
        tm = box(app.t_mine, o)
        col0 = box(app.list_rows["wmn"][0], o)[0]
        ub = box(app.ent_usx_url, o)
        url_y = (ub[1] + ub[3]) / 2
        marks = [
            into(1, app.sw_avatars, o, inset=4),
            into(2, app.list_rows["wmn"][0], o, inset=2),
            M(3, (col0 + 4, url_y), (gutter_x(o), url_y)),
            into(4, app.btn_update, o, inset=4),
            M(5, (sw[2] + 2, (sw[1] + sw[3]) / 2), (sw[2] + 30, (sw[1] + sw[3]) / 2)),
            into(6, newb, o, inset=4),
            M(7, (tm[0] + 12, tm[1] + 70), (gutter_x(o), tm[1] + 70)),
        ]
        save("07-config.png", img, marks)

    later(500, s07)
    later(900, s07b)

    # ---------------------------------------------------------------- 08: nuova risorsa
    state = {}

    def s08():
        state["win"] = app._new_target() or next(
            w for w in app.winfo_children() if w.__class__.__name__ == "CTkToplevel")

    def s08b():
        win = next(w for w in app.winfo_children() if w.__class__.__name__ == "CTkToplevel")
        state["win"] = win
        ents = find(win, "CTkEntry")
        ph = [e.cget("placeholder_text") or "" for e in ents]
        vals = {0: "Forum Montagna (esempio)"}
        for i, p in enumerate(ph):
            if "{username}" in p:
                vals[i] = "https://forum-montagna.example/u/{username}"
        last = len(ents) - 1
        vals[last] = USER
        for i, v in vals.items():
            ents[i].delete(0, "end")
            ents[i].insert(0, v)
        btn = [b for b in find(win, "CTkButton") if b.cget("text").endswith(tr("Prova"))]
        btn[0].invoke()

    def s08c():
        win = state["win"]
        img, o = grab(win)
        img = pad(img, right=46, bottom=44)
        ents = [e for e in find(win, "CTkEntry")]
        combo = find(win, "CTkComboBox")[0]
        opt = find(win, "CTkOptionMenu")[0]
        b = {k: [x for x in find(win, "CTkButton") if x.cget("text").endswith(tr(k))] for k in ("Prova", "Salva")}
        gx = img.width - 23

        def right(num, widget):
            bb = box(widget, o)
            y = (bb[1] + bb[3]) / 2
            return M(num, (bb[2] - 1, y), (gx, y))

        def under(num, widget):
            bb = box(widget, o)
            return M(num, at(bb, "b", 1), ((bb[0] + bb[2]) / 2, img.height - 22))
        marks = [
            right(1, ents[0]), right(2, combo), right(3, ents[1]), right(4, ents[2]), right(5, opt),
            right(6, ents[3]), right(7, ents[-1]),
            under(8, b["Prova"][0]), under(9, b["Salva"][0]),
        ]
        save("08-new-resource.png", img, marks)
        win.destroy()

    later(500, s08)
    later(900, s08b)
    later(2500, s08c)

    app.after(100, run_steps)
    app.mainloop()


def _virtual_display_or_exit() -> None:
    """Le catture si fanno SOLO su uno schermo virtuale (Xvfb), dove esiste soltanto la finestra di UserSnex.

    Su uno schermo vero ImageGrab copia quello che c'è in quella zona: se un'altra finestra è sopra,
    nell'immagine finiscono i suoi contenuti (dati personali). Per questo il programma si rifiuta.
    """
    if os.environ.get("USERSNEX_MANUAL_XVFB") == "1":
        return
    if not sys.platform.startswith("linux") or not shutil.which("xvfb-run"):
        sys.exit("make_manual.py: serve Linux con Xvfb (xvfb-run). Le catture non si fanno sullo schermo vero, "
                 "per non copiare nelle immagini altre finestre o dati personali.")
    env = dict(os.environ, USERSNEX_MANUAL_XVFB="1")
    cmd = ["xvfb-run", "-a", "-s", "-screen 0 1600x1000x24", sys.executable, __file__, *sys.argv[1:]]
    sys.exit(subprocess.run(cmd, env=env, check=False).returncode)


def main() -> int:
    _virtual_display_or_exit()
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default=",".join(LANGS))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--one", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.one:
        sys.path.insert(0, str(ROOT))
        shots(a.one, Path(a.out))
        return 0
    for lang in a.lang.split(","):
        home = Path(tempfile.mkdtemp(prefix="usersnex-manual-"))
        try:
            src = ROOT / "lists"
            if src.is_dir():
                shutil.copytree(src, home / "lists")
            (home / "config.json").write_text(json.dumps({"use_cache": False, "avatars": True}), encoding="utf-8")
            env = dict(os.environ, USERSNEX_HOME=str(home))
            print(f"[{lang}]")
            subprocess.run([sys.executable, __file__, "--one", lang, "--out", a.out], env=env, check=False)
        finally:
            shutil.rmtree(home, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
