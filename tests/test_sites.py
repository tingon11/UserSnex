import textwrap

import pytest

from usersnex.sites import BUILTIN_LIST, load_sites, upstream_missing


def test_builtin_sites_load_and_are_valid():
    sites = load_sites(files=[BUILTIN_LIST])
    assert len(sites) >= 40
    names = [s.name for s in sites]
    assert len(names) == len(set(names))
    for s in sites:
        assert "{username}" in s.check_url, s.name
        assert s.type in ("json", "html")
        assert s.rate_per_sec > 0


def test_plugin_dir_overrides_and_adds(tmp_path):
    (tmp_path / "github.yaml").write_text(textwrap.dedent("""
        name: GitHub
        url: https://example.org/{username}
        check_url: https://example.org/api/{username}
    """), encoding="utf-8")
    (tmp_path / "nuovo.yml").write_text("name: Nuovo\nurl: https://n.example/{username}\n"
                                        "check_url: https://n.example/{username}\ntype: html\n", encoding="utf-8")
    sites = {s.name: s for s in load_sites([tmp_path], files=[BUILTIN_LIST])}
    assert sites["GitHub"].url == "https://example.org/{username}"
    assert sites["Nuovo"].type == "html"


def test_invalid_plugin_is_rejected(tmp_path):
    (tmp_path / "rotto.yaml").write_text("name: Rotto\nurl: https://x/{username}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_sites([tmp_path], files=[BUILTIN_LIST])


def test_regex_filters_usernames():
    gh = next(s for s in load_sites(files=[BUILTIN_LIST]) if s.name == "GitHub")
    assert gh.accepts("mario-rossi")
    assert not gh.accepts("mario.rossi")


@pytest.mark.skipif(bool(upstream_missing()), reason="liste esterne non scaricate (run.py --update-lists)")
def test_packs_are_loaded_and_protected_sites_disabled():
    sites = load_sites()
    assert len(sites) > 500
    assert any(not s.enabled and s.protection for s in sites)
    assert all(not s.nsfw for s in sites)
    sources = {s.source.split(" (")[0] for s in sites}
    assert {"UserSnex", "WhatsMyName", "Sherlock", "Maigret"} <= sources
    assert any(s.extended for s in sites) and any("it" in s.tags for s in sites)
    gh = [s for s in sites if s.check_url.split("/")[2] in ("github.com", "www.github.com", "api.github.com")]
    assert len(gh) == 1 and gh[0].source == "UserSnex"   # i siti fatti a mano hanno la precedenza


def test_mytargets_override_everything(tmp_path, monkeypatch):
    import usersnex.sites as sites_mod
    mine = tmp_path / "mytargets.yaml"
    mine.write_text("- name: GitHub\n  url: https://mio.example/{username}\n"
                    "  check_url: https://mio.example/{username}\n  type: html\n", encoding="utf-8")
    monkeypatch.setattr(sites_mod, "MYTARGETS_FILE", str(mine))
    monkeypatch.setattr(sites_mod, "LISTS_DIR", str(tmp_path / "lists"))
    gh = next(s for s in load_sites() if s.name == "GitHub")
    assert gh.url == "https://mio.example/{username}" and gh.source == "mytargets"
