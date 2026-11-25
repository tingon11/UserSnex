# SPDX-License-Identifier: GPL-3.0-or-later
"""Aggiornamento delle liste: conversione, file separati con licenza, lista rotta che non sovrascrive."""
import json

import yaml

from usersnex import updater


def _wmn(n):
    return {"sites": [{"name": f"WSite{i}", "uri_check": f"https://w{i}.example/{{account}}", "e_code": 200,
                       "e_string": "profile", "m_code": 404, "m_string": "", "cat": "social",
                       "known": ["alice"]} for i in range(n)]}


def _sherlock(n):
    return {f"SSite{i}": {"url": f"https://s{i}.example/{{}}", "errorType": "status_code",
                          "username_claimed": "bob"} for i in range(n)}


def _maigret(n):
    return {"engines": {}, "sites": {f"MSite{i}": {"url": f"https://m{i}.example/{{username}}",
                                                     "checkType": "status_code", "alexaRank": 10 + i,
                                                     "usernameClaimed": "carol"} for i in range(n)}}


USX = "".join(f"- name: Nostro{i}\n  url: https://n{i}.example/{{username}}\n"
              f"  check_url: https://n{i}.example/{{username}}\n  type: html\n" for i in range(12))


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "LISTS_INFO", str(tmp_path / "packs" / "info.json"))
    monkeypatch.setattr(updater, "pack_dir", lambda: tmp_path / "packs")
    monkeypatch.setattr(updater, "MYTARGETS_FILE", str(tmp_path / "mytargets.yaml"))


def test_update_writes_separate_licensed_packs(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    data = {"usx": USX, updater.WMN_URL: _wmn(60), updater.SHERLOCK_URL: _sherlock(60), updater.MAIGRET_URL: _maigret(60)}
    fetch = lambda url: data[url].encode() if url == "usx" else json.dumps(data[url]).encode()  # noqa: E731
    res = updater.update(log=lambda *a: None, fetch=fetch, usersnex_url="usx")
    assert {k: r["esito"] for k, r in res.items()} == {"usersnex": "aggiornata", "wmn": "aggiornata",
                                                       "sherlock": "aggiornata", "maigret": "aggiornata"}
    assert (tmp_path / "packs" / "usersnex.yaml").read_text(encoding="utf-8") == USX
    wmn = (tmp_path / "packs" / "whatsmyname.yaml").read_text(encoding="utf-8")
    assert "CC BY-SA 4.0" in wmn and len(yaml.safe_load(wmn)) == 60
    assert "MIT" in (tmp_path / "packs" / "maigret.yaml").read_text(encoding="utf-8")
    # stessa lista scaricata di nuovo: niente da riscrivere
    res = updater.update(log=lambda *a: None, fetch=fetch, usersnex_url="usx")
    assert all(r["esito"] == "invariata" for r in res.values())


def test_broken_download_keeps_current_list(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    good = {updater.WMN_URL: _wmn(60), updater.SHERLOCK_URL: _sherlock(60), updater.MAIGRET_URL: _maigret(60)}
    updater.update(log=lambda *a: None, fetch=lambda url: json.dumps(good[url]).encode())
    before = (tmp_path / "packs" / "whatsmyname.yaml").read_text(encoding="utf-8")

    def bad(url):
        if url == updater.WMN_URL:
            return json.dumps(_wmn(3)).encode()        # troppo pochi siti: lista rotta
        if url == updater.SHERLOCK_URL:
            raise OSError("rete assente")
        return b"<html>non json</html>"

    res = updater.update(log=lambda *a: None, fetch=bad)
    assert {r["esito"] for r in res.values()} == {"errore"}
    assert (tmp_path / "packs" / "whatsmyname.yaml").read_text(encoding="utf-8") == before


def test_usersnex_list_wins_over_upstream(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    wmn = _wmn(60)
    wmn["sites"][0]["uri_check"] = "https://n3.example/{account}"     # stesso sito di usersnex.yaml
    data = {"usx": USX, updater.WMN_URL: wmn, updater.SHERLOCK_URL: _sherlock(60),
            updater.MAIGRET_URL: _maigret(60)}
    fetch = lambda url: data[url].encode() if url == "usx" else json.dumps(data[url]).encode()  # noqa: E731
    updater.update(log=lambda *a: None, fetch=fetch, usersnex_url="usx")
    got = yaml.safe_load((tmp_path / "packs" / "whatsmyname.yaml").read_text(encoding="utf-8"))
    assert len(got) == 59 and all("n3.example" not in s["check_url"] for s in got)


def test_broken_usersnex_list_is_rejected(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    data = {updater.WMN_URL: _wmn(60), updater.SHERLOCK_URL: _sherlock(60), updater.MAIGRET_URL: _maigret(60)}

    def fetch(url):
        return b"- name: Rotto\n  url: https://x/{username}\n" if url == "usx" else json.dumps(data[url]).encode()

    res = updater.update(log=lambda *a: None, fetch=fetch, usersnex_url="usx")
    assert res["usersnex"]["esito"] == "errore" and not (tmp_path / "packs" / "usersnex.yaml").exists()
