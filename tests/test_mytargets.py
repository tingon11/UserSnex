# SPDX-License-Identifier: GPL-3.0-or-later
"""Maschera "Nuova risorsa": costruzione delle voci, salvataggio in mytargets.yaml, prova."""
import httpx
import pytest

from usersnex import mytargets


def test_build_validates_fields():
    with pytest.raises(ValueError):
        mytargets.build("", "varie", "https://x.example/{username}", "", "status", "", "")
    with pytest.raises(ValueError):
        mytargets.build("X", "varie", "https://x.example/utente", "", "status", "", "")
    with pytest.raises(ValueError):
        mytargets.build("X", "varie", "https://x.example/{username}", "", "absent", "", "")
    d = mytargets.build("Forum X", "forum", "https://x.example/u/{username}", "", "absent", "Utente non trovato",
                        "mario")
    assert d["check_url"] == d["url"] and d["absent_text"] == ["Utente non trovato"] and d["known"] == ["mario"]
    assert mytargets.mode_of(d) == ("absent", "Utente non trovato")
    j = mytargets.build("API", "", "https://x.example/{username}", "https://api.x.example/{username}", "json",
                        "data.id", "")
    assert j["type"] == "json" and j["require"] == "data.id" and mytargets.mode_of(j) == ("json", "data.id")


def test_upsert_rename_and_delete(tmp_path):
    f = str(tmp_path / "mytargets.yaml")
    a = mytargets.build("Alfa", "", "https://a.example/{username}", "", "status", "", "")
    mytargets.upsert(a, path=f)
    b = dict(a, name="Beta")
    mytargets.upsert(b, old_name="Alfa", path=f)       # modifica con cambio di nome
    assert [e["name"] for e in mytargets.load(f)] == ["Beta"]
    mytargets.delete("beta", path=f)
    assert mytargets.load(f) == []


def test_resource_test_uses_known_and_fake_account(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if request.url.path.endswith("/mario") else 404, text="<html></html>")

    import usersnex.mytargets as mt
    real_engine = mt.Engine
    monkeypatch.setattr(mt, "Engine", lambda sites, timeout=15.0: real_engine(
        sites, timeout=timeout, transport=httpx.MockTransport(handler)))
    d = mytargets.build("Prova", "", "https://p.example/{username}", "", "status", "", "mario")
    assert mytargets.test(d)["esito"] == "ok"
