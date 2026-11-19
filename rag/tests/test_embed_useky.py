"""Dělení projevů a celků textů na úseky pro embeddingy (bez databáze)."""
from __future__ import annotations

from datetime import date

from hlidac_rag.embed.useky import hlavicka_celku, hlavicka_projevu, rozdel, text_hash


def _odstavec(n: int, slovo: str = "slovo") -> str:
    """Odstavec ze slov o délce přesně n znaků."""
    s = (slovo + " ") * (n // (len(slovo) + 1) + 1)
    return s[:n - 1].rstrip() + "." if n > 1 else "."


def _konzistentni(text: str, useky) -> None:
    for i, u in enumerate(useky):
        assert u.poradi == i
        assert text[u.od_znaku:u.do_znaku] == u.telo


def test_kratky_text_jeden_usek():
    useky = rozdel("a" * 500)
    assert len(useky) == 1
    u = useky[0]
    assert (u.poradi, u.od_znaku, u.do_znaku) == (0, 0, 500) and u.telo == "a" * 500


def test_prazdny_text_bez_useku():
    assert rozdel("") == [] and rozdel("   \n\n ") == []


def test_dlouhy_po_odstavcich():
    odstavce = [_odstavec(1500, w) for w in ("alfa", "beta", "gama")]
    text = "\n\n".join(odstavce)
    useky = rozdel(text)
    assert len(useky) >= 3
    _konzistentni(text, useky)
    for u in useky:
        assert len(u.telo) <= 2400
    for a, b in zip(useky, useky[1:]):
        prekryv = a.do_znaku - b.od_znaku
        assert 200 <= prekryv <= 400, prekryv
    # celý text je pokrytý
    assert useky[0].od_znaku == 0 and useky[-1].do_znaku == len(text)


def test_odstavec_delsi_nez_limit_po_vetach():
    vety = [f"Tohle je věta číslo {i} o rozpočtu a dotacích pro zemědělce." for i in range(120)]
    text = " ".join(vety)
    assert len(text) > 6000
    useky = rozdel(text)
    assert len(useky) >= 3
    _konzistentni(text, useky)
    for u in useky:
        assert len(u.telo) <= 2400
        assert u.telo.endswith("."), u.telo[-20:]
        assert text[u.do_znaku:u.do_znaku + 1] in ("", " ")


def test_bez_mezer_tvrdy_rez():
    text = "x" * 10000
    useky = rozdel(text)
    _konzistentni(text, useky)
    assert all(len(u.telo) <= 2400 for u in useky)
    assert useky[-1].do_znaku == 10000


def test_hlavicka_projevu():
    h = hlavicka_projevu(dict(recnik="Poslanec Martin Kupka", role="Poslanec", klub="ODS", datum=date(2026, 11, 13),
                              schuze=36, bod_nazev="Vládní návrh zákona o daních z příjmů", tisky=[311]))
    for kus in ("Martin Kupka", "ODS", "13. 11. 2026", "36. schůze", "tisk 311", "daních z příjmů"):
        assert kus in h, kus
    # role obsažená ve jméně řečníka se neopakuje, datum jako řetězec
    h2 = hlavicka_projevu(dict(recnik="Ministr financí Alena Schillerová", role="ministr financí", klub=None,
                               datum="2026-11-03", schuze=1, bod_nazev=None, tisky=[76, 77]))
    assert "3. 11. 2026" in h2 and "tisky 76, 77" in h2 and "None" not in h2
    assert h2.lower().count("ministr financí") == 1


def test_hlavicka_celku():
    h = hlavicka_celku(dict(druh="tisk", ct=221, pn=None, nazev_tisku="Novela zákona o střetu zájmů",
                            cesta="ČÁST TŘETÍ / Čl. VI", meni_zakon="256/2000"))
    for kus in ("Tisk 221", "ČÁST TŘETÍ / Čl. VI", "256/2000", "střetu zájmů"):
        assert kus in h, kus
    h2 = hlavicka_celku(dict(druh="pn", ct=221, pn=5012, nazev_tisku=None, cesta="bod 1", meni_zakon=None))
    assert "5012" in h2 and "tisku 221" in h2 and "None" not in h2


def test_hash_se_meni_s_hlavickou():
    a = text_hash("hlavička A", "tělo")
    assert a == text_hash("hlavička A", "tělo") and len(a) == 64
    assert a != text_hash("hlavička B", "tělo")
    assert a != text_hash("hlavička A", "tělo 2")
