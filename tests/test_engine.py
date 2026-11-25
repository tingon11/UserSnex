import json
import threading

import httpx

from usersnex.engine import DiskCache, Engine
from usersnex.models import Status
from usersnex.sites import SiteDef


def make_sites():
    return [
        SiteDef(name="Api", category="t", url="https://api.test/u/{username}",
                check_url="https://api.test/users/{username}", require="login",
                fields={"display_name": "name", "links": ["blog"]}, rate_per_sec=100),
        SiteDef(name="Page", category="t", url="https://page.test/{username}",
                check_url="https://page.test/{username}", type="html", present_text="profile-card",
                rate_per_sec=100),
        SiteDef(name="Blocked", category="t", url="https://blocked.test/{username}",
                check_url="https://blocked.test/{username}", rate_per_sec=100),
        SiteDef(name="Strict", category="t", url="https://strict.test/{username}",
                check_url="https://strict.test/{username}", regex="^[a-z]+$", rate_per_sec=100),
    ]


def handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host == "api.test":
        if path.endswith("/mario"):
            return httpx.Response(200, json={"login": "mario", "name": "Mario R", "blog": "https://m.dev"})
        return httpx.Response(404, json={"message": "Not Found"})
    if host == "page.test":
        body = "<div class='profile-card'><meta property='og:title' content='Mario'></div>" \
            if path == "/mario" else "<html>nothing</html>"
        return httpx.Response(200, text=body)
    if host == "blocked.test":
        return httpx.Response(403, text="no")
    return httpx.Response(500)


def test_engine_classifies_results():
    engine = Engine(make_sites(), transport=httpx.MockTransport(handler))
    seen = []
    res = {r.site: r for r in engine.run("mario", on_result=seen.append)}
    assert len(seen) == 4
    assert res["Api"].status == Status.FOUND
    assert res["Api"].profile.display_name == "Mario R"
    assert res["Api"].profile.links == ["https://m.dev"]
    assert res["Page"].status == Status.FOUND
    assert res["Blocked"].status == Status.ERROR and "403" in res["Blocked"].error
    assert res["Strict"].status == Status.ERROR  # 500


def test_engine_not_found_and_invalid_username():
    engine = Engine(make_sites(), transport=httpx.MockTransport(handler))
    res = {r.site: r for r in engine.run("luigi_9")}
    assert res["Api"].status == Status.NOT_FOUND
    assert res["Page"].status == Status.NOT_FOUND
    assert res["Strict"].status == Status.NOT_FOUND and "non valido" in res["Strict"].error


def test_engine_cancel_stops_early():
    cancel = threading.Event()
    cancel.set()
    engine = Engine(make_sites(), transport=httpx.MockTransport(handler))
    res = engine.run("mario", cancel=cancel)
    assert all(r.status != Status.FOUND for r in res)


def test_cache_avoids_second_request(tmp_path):
    calls = []

    def counting(request):
        calls.append(str(request.url))
        return handler(request)

    sites = make_sites()[:1]
    cache = DiskCache(tmp_path)
    Engine(sites, cache=cache, transport=httpx.MockTransport(counting)).run("mario")
    Engine(sites, cache=cache, transport=httpx.MockTransport(counting)).run("mario")
    assert len(calls) == 1


def test_retry_after_429():
    state = {"n": 0}

    def flaky(request):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, text=json.dumps({"login": "mario"}))

    res = Engine(make_sites()[:1], transport=httpx.MockTransport(flaky)).run("mario")
    assert res[0].status == Status.FOUND and state["n"] == 2


def test_antibot_challenge_is_an_error_not_a_hit():
    site = SiteDef(name="Pp", category="t", url="https://pp.test/{username}", check_url="https://pp.test/{username}",
                   type="html", rate_per_sec=100)
    t = httpx.MockTransport(lambda r: httpx.Response(200, text="<title>Client Challenge</title>"))
    res = Engine([site], transport=t).run("mario")
    assert res[0].status == Status.ERROR and "anti-bot" in res[0].error


def test_wmn_style_rules_and_strip_chars():
    site = SiteDef(name="W", category="t", url="https://w.test/{username}", check_url="https://w.test/{username}",
                   type="html", found_status=[200], missing_status=[], present_text=["profile-ok"],
                   absent_text=["no such user"], strip_chars=".", rate_per_sec=100)

    def h(req):
        if req.url.path == "/mariorossi":
            return httpx.Response(200, text="<div>profile-ok</div>")
        return httpx.Response(200, text="no such user")

    t = httpx.MockTransport(h)
    assert Engine([site], transport=t).run("mario.rossi")[0].status == Status.FOUND
    assert Engine([site], transport=t).run("luigi")[0].status == Status.NOT_FOUND


def test_selftest_verdicts(tmp_path, monkeypatch):
    from usersnex import selftest
    monkeypatch.setattr(selftest, "HEALTH_FILE", str(tmp_path / "h.json"))
    good = SiteDef(name="Good", category="t", url="https://api.test/u/{username}",
                   check_url="https://api.test/users/{username}", require="login", known=["mario"], rate_per_sec=100)
    yes = SiteDef(name="AlwaysYes", category="t", url="https://page.test/{username}",
                  check_url="https://yes.test/{username}", type="html", known=["mario"], rate_per_sec=100)

    def h(req):
        if req.url.host == "yes.test":
            return httpx.Response(200, text="hi")
        return handler(req)

    rep = selftest.run_selftest([good, yes], Engine([good, yes], transport=httpx.MockTransport(h)))
    assert rep["Good"]["esito"] == "ok"
    assert rep["AlwaysYes"]["esito"] == "falso positivo"
    selftest.save_health(rep)
    sites = [good, yes]
    assert selftest.apply_health(sites) == 1 and not yes.enabled and good.enabled


def test_dns_missing_subdomain_is_not_found():
    site = SiteDef(name="Sub", category="t", url="https://{username}.sub.test", check_url="https://{username}.sub.test",
                   type="html", dns_missing=True, rate_per_sec=100)

    def h(req):
        if req.url.host == "mario.sub.test":
            return httpx.Response(200, text="blog")
        raise httpx.ConnectError("getaddrinfo failed", request=req)

    t = httpx.MockTransport(h)
    assert Engine([site], transport=t).run("mario")[0].status == Status.FOUND
    assert Engine([site], transport=t).run("nobody")[0].status == Status.NOT_FOUND


def test_accept_header_follows_site_type():
    seen = {}

    def h(req):
        seen[req.url.host] = req.headers["accept"]
        return httpx.Response(404)

    sites = [SiteDef(name="J", category="t", url="https://j.test/{username}", check_url="https://j.test/{username}",
                     rate_per_sec=100),
             SiteDef(name="H", category="t", url="https://h.test/{username}", check_url="https://h.test/{username}",
                     type="html", rate_per_sec=100)]
    Engine(sites, transport=httpx.MockTransport(h)).run("x")
    assert seen["j.test"] == "application/json" and seen["h.test"].startswith("text/html")


def test_present_any_and_extended_filter():
    a = SiteDef(name="A", category="t", url="https://a.test/{username}", check_url="https://a.test/{username}",
                type="html", present_any=["uno", "due"], rate_per_sec=100)
    b = SiteDef(name="B", category="t", url="https://b.test/{username}", check_url="https://b.test/{username}",
                type="html", extended=True, rate_per_sec=100)
    t = httpx.MockTransport(lambda r: httpx.Response(200, text="pagina con due"))
    res = Engine([a, b], transport=t).run("x")
    assert [r.site for r in res] == ["A"] and res[0].status == Status.FOUND
    assert len(Engine([a, b], transport=t, extended=True).run("x")) == 2
