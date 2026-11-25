from usersnex.correlate import correlate, norm_avatar, norm_url, pair_evidence, username_uniqueness
from usersnex.models import Profile, SiteResult, Status


def found(site, url, **profile):
    return SiteResult(site, "t", "mario.rossi88", url, Status.FOUND, 200, Profile(**profile))


def test_norm_helpers():
    assert norm_url("https://www.GitHub.com/Mario/") == "github.com/mario"
    assert norm_url("mario.dev") == "mario.dev"
    assert norm_avatar("https://www.gravatar.com/avatar/" + "a" * 32 + "?s=80").startswith("gravatar:")


def test_uniqueness_orders_usernames():
    assert username_uniqueness("mario.rossi88") > username_uniqueness("mario")


def test_cross_links_and_avatar_build_main_identity():
    results = [
        found("GitHub", "https://github.com/mario.rossi88", display_name="Mario Rossi", location="Milano",
              links=["https://mario.dev", "https://x.com/mrossi"], avatar="https://cdn/a.png"),
        found("Keybase", "https://keybase.io/mario.rossi88", display_name="Mario Rossi",
              links=["https://github.com/mario.rossi88"], location="Milano, Italia"),
        found("DEV", "https://dev.to/mario.rossi88", display_name="M. Rossi", links=["https://mario.dev"]),
        found("Chess", "https://chess.example/mario.rossi88", display_name="Juan Pérez", location="Lima"),
        SiteResult("Nope", "t", "mario.rossi88", "https://nope/x", Status.NOT_FOUND),
    ]
    rep = correlate(results)
    main = set(rep.clusters[0])
    assert {"GitHub", "Keybase", "DEV"} <= main
    assert "Chess" not in main
    conf = {a.site: a.confidence for a in rep.accounts}
    assert conf["GitHub"] > 80 and conf["Keybase"] > 80
    assert conf["Chess"] < 40
    signals = {e.signal for a in rep.accounts if a.site == "Keybase" for e in a.evidence}
    assert "link incrociato" in signals
    assert len(rep.accounts) == 4  # i non trovati non hanno punteggio


def test_seed_selects_the_right_cluster():
    results = [
        found("A", "https://a/x", display_name="Anna Bianchi", location="Torino", links=["https://anna.it"]),
        found("B", "https://b/x", display_name="Anna Bianchi", links=["https://anna.it"]),
        found("C", "https://c/x", display_name="Carlo Verdi", links=["https://carlo.it"], location="Roma"),
        found("D", "https://d/x", display_name="Carlo Verdi", links=["https://carlo.it"], avatar="https://i/c.png"),
        found("E", "https://e/x", display_name="Carlo Verdi", avatar="https://i/c.png"),
    ]
    assert set(correlate(results).clusters[0]) == {"C", "D", "E"}   # senza indizi: il gruppo più forte
    rep = correlate(results, Profile(display_name="Anna Bianchi", links=["https://anna.it"]))
    assert set(rep.clusters[0]) == {"A", "B"}
    conf = {a.site: a.confidence for a in rep.accounts}
    assert conf["A"] > conf["C"]


def test_no_found_accounts():
    rep = correlate([SiteResult("X", "t", "u", "https://x", Status.NOT_FOUND)])
    assert rep.accounts == [] and rep.clusters == [] and rep.main_cluster == -1


def test_default_and_shared_avatars_are_ignored():
    from usersnex.correlate import is_default_avatar
    assert is_default_avatar("https://site.com/images/default-avatar.png")
    assert is_default_avatar("https://cdn.x.com/static/no_avatar.jpg?v=2")
    assert is_default_avatar("https://www.gravatar.com/avatar/d41d8cd98f00b204e9800998ecf8427e")
    assert not is_default_avatar("https://avatars.githubusercontent.com/u/12345?v=4")
    same = "https://cdn.example.com/u/pic.png"
    rs = [found(n, f"https://{n.lower()}/x", avatar=same) for n in ("A", "B", "C", "D")]
    rep = correlate(rs)
    assert not any(ev.signal == "stesso avatar" for e in rep.edges for ev in e.evidence)
    rep2 = correlate(rs[:2])
    assert any(ev.signal == "stesso avatar" for e in rep2.edges for ev in e.evidence)
    rep3 = correlate([found(n, f"https://{n.lower()}/x", avatar="https://s.com/img/default.png") for n in "AB"])
    assert not rep3.edges


def test_graph_model_from_report():
    from usersnex.gui.graphview import build_graph
    rs = [found("A", "https://a/x", display_name="Mario Rossi", links=["https://b/x"]),
          found("B", "https://b/x", display_name="Mario Rossi"),
          found("C", "https://c/x")]
    nodes, edges, collapse = build_graph(correlate(rs))
    ids = {n.id for n in nodes}
    assert {"user", "group:0", "group:-1", "acc:A", "acc:B", "acc:C"} <= ids
    assert any(not e.tree and {e.a, e.b} == {"acc:A", "acc:B"} for e in edges)
    assert not collapse


def test_same_display_name_groups_accounts():
    def f(site, name):
        return SiteResult(site, "t", "giannamjoyce", f"https://{site.lower()}/x", Status.FOUND, 200,
                          Profile(display_name=name))
    rep = correlate([f("hoo.be", "Gianna Harner"), f("Threads", "Gianna Harner"), f("Venmo", "Gianna Harner"),
                     f("TikTok", "gianna"), f("Linktree", "giannamjoyce"), f("X", "")])
    main = set(rep.clusters[0])
    assert {"hoo.be", "Threads", "Venmo", "TikTok"} <= main
    assert "X" not in main


def test_page_titles_are_cleaned():
    from usersnex.extract import clean_display_name, profile_from_html
    assert clean_display_name("Gianna Harner (@giannamjoyce) • Threads, Say more", "Threads") == "Gianna Harner"
    assert clean_display_name("giannamjoyce | Linktree", "Linktree") == "giannamjoyce"
    assert profile_from_html('<title>DPG Media Privacy Gate</title><meta property="og:description" content="x y z">').bio == ""


def test_avatar_fingerprint_matches_same_photo_on_different_servers(tmp_path, monkeypatch):
    """La stessa foto, ridimensionata e ricompressa su due siti, risulta 'stesso avatar'."""
    import io
    import httpx
    from PIL import Image, ImageDraw
    from usersnex import avatars

    monkeypatch.setattr(avatars, "AVATAR_DIR", tmp_path)
    photo = Image.new("RGB", (400, 400), (30, 90, 160))
    d = ImageDraw.Draw(photo)
    for y in range(400):                               # sfondo sfumato: il caso difficile per le impronte
        d.line((0, y, 400, y), fill=(30 + y // 8, 90 + y // 10, 160))
    d.ellipse((80, 60, 320, 300), fill=(230, 190, 150))
    d.rectangle((0, 300, 400, 400), fill=(200, 40, 40))
    other = Image.new("RGB", (400, 400), (30, 90, 160))
    ImageDraw.Draw(other).rectangle((50, 50, 200, 380), fill=(240, 240, 20))

    def png(img, size, fmt="PNG"):
        b = io.BytesIO()
        img.resize((size, size)).save(b, fmt, quality=70) if fmt == "JPEG" else img.resize((size, size)).save(b, fmt)
        return b.getvalue()

    files = {"/a.png": png(photo, 460), "/b.jpg": png(photo, 120, "JPEG"), "/c.png": png(other, 200),
             "/flat.png": png(Image.new("RGB", (50, 50), (200, 200, 200)), 50),
             "/crop.png": png(photo.crop((20, 20, 380, 380)), 300)}

    def handler(req):
        ct = "image/jpeg" if req.url.path.endswith(".jpg") else "image/png"
        return httpx.Response(200, content=files[req.url.path], headers={"content-type": ct})

    def res(site, url):
        return SiteResult(site=site, category="x", username="mario", url=f"https://{site}/mario",
                          status=Status.FOUND, profile=Profile(avatar=url))

    rs = [res("one.example", "https://cdn1.example/a.png"), res("two.example", "https://cdn2.example/b.jpg"),
          res("three.example", "https://cdn3.example/c.png"), res("four.example", "https://cdn4.example/flat.png"),
          res("five.example", "https://cdn5.example/crop.png")]
    got = avatars.fetch(rs, transport=httpx.MockTransport(handler))
    assert set(got) == {"one.example", "two.example", "three.example", "four.example", "five.example"}
    assert rs[3].profile.avatar_hash == "-"
    ev = pair_evidence("one", rs[0].profile, set(), "two", rs[1].profile, set(), "mario")
    assert any(e.signal == "stesso avatar" for e in ev)
    ev = pair_evidence("one", rs[0].profile, set(), "three", rs[2].profile, set(), "mario")
    assert not any("avatar" in e.signal for e in ev)
    ev = pair_evidence("one", rs[0].profile, set(), "five", rs[4].profile, set(), "mario")
    assert any(e.signal in ("avatar simile", "stesso avatar") for e in ev)


def test_same_photo_on_many_linked_accounts_counts():
    """La stessa foto su 4+ account: generica se gli account non hanno altri legami, valida se sono linkati."""
    fp = "2" + "".join(f"{(i * 37) % 256:02x}" for i in range(144)) + "6080a0"
    def acc(n, links=()):
        r = found(n, f"https://{n.lower()}/x", links=list(links))
        r.profile.avatar_hash = fp
        return r
    loose = correlate([acc(n) for n in "ABCD"])
    assert not any(ev.signal == "stesso avatar" for e in loose.edges for ev in e.evidence)
    linked = correlate([acc(n, ["https://luna.example"]) for n in "ABCD"])
    assert any(ev.signal == "stesso avatar" for e in linked.edges for ev in e.evidence)
