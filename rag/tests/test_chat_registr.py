from hlidac_rag.chat.nastroje import Polozka
from hlidac_rag.chat.registr import Registr, Zdroj


def P(klic: str) -> Polozka:
    return Polozka(klic, klic.split(":")[0], klic.replace(":", " "), None, {})


def test_stejny_klic_ma_stejne_cislo():
    r = Registr(); a = r.pridej(P("tisk:221")); b = r.pridej(P("pn:2329")); c = r.pridej(P("tisk:221"))
    assert (a, b, c) == (1, 2, 1)


def test_cislovani_pokracuje_z_ulozenych():
    r = Registr([Zdroj(7, "tisk:1", "tisk", "Tisk 1", {}, True)])
    assert r.pridej(P("pn:5")) == 8


def test_nezname_n_se_odstrani_a_spocita():
    r = Registr(); r.pridej(P("tisk:221"))
    assert r.odstran_nezname("Ano [1], ne [9] a [12].") == ("Ano [1], ne a.", 2)


def test_odstraneni_nezere_konec_radku():
    r = Registr(); r.pridej(P("tisk:221"))
    assert r.odstran_nezname("Ano [1]\n[9]Dál.") == ("Ano [1]\nDál.", 1)


def test_radky_citovane_napred_a_strop():
    r = Registr(); [r.pridej(P(f"pn:{i}")) for i in range(50)]; r.oznac_citovane("viz [50]")
    assert r.radky(40).splitlines()[0].startswith("[50]") and len(r.radky(40).splitlines()) == 40


def test_radky_bez_citovanych_drzi_nejnovejsi_a_format():
    r = Registr(); [r.pridej(P(f"pn:{i}")) for i in range(3)]
    assert r.radky(2).splitlines() == ["[3] pn, pn:2, pn 2", "[2] pn, pn:1, pn 1"]
    assert Registr().radky() == ""


def test_oznac_citovane_vraci_jen_zname_a_oznaci():
    r = Registr(); r.pridej(P("tisk:221")); r.pridej(P("pn:2329"))
    assert r.oznac_citovane("Viz [2], [7] a znovu [2] a [1].") == [2, 1]
    assert r.podle_n(1).citovan and r.podle_n(2).citovan and r.podle_n(7) is None


def test_nove_vraci_jen_pridane_v_tomto_tahu():
    r = Registr([Zdroj(1, "tisk:1", "tisk", "Tisk 1", {}, False)])
    r.pridej(P("tisk:1")); r.pridej(P("pn:5"))
    assert [z.klic for z in r.nove()] == ["pn:5"]
