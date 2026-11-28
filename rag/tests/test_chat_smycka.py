"""Agentní smyčka (smycka.tah) a rozhraní LLM (llm.AnthropicLLM). Skutečné API se nikdy nevolá."""
import asyncio
import copy
import itertools
from dataclasses import replace

import anthropic
import httpx2
import pytest

from hlidac_rag.chat import llm as llm_mod
from hlidac_rag.chat.config import nacti
from hlidac_rag.chat.historie import odhad_tokenu
from hlidac_rag.chat.llm import AnthropicLLM, Odpoved, Pretizeno, Usage
from hlidac_rag.chat.nastroje import SCHEMATA, Polozka, Vysledek
from hlidac_rag.chat.registr import Registr
from hlidac_rag.chat.smycka import POKYN_STROP, ROZPOCET_VYCERPAN, Udalost, tah

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


MODEL = "claude-sonnet-5-5"
N = nacti({})
M = [{"role": "user", "content": "Prošel PN 2329?"}]
EV = asyncio.Event
_id = itertools.count(1)
MALA = Usage(1000, 100, 0, 0)


def ODP_TOOL(nazev, vstup, usage=MALA):
    return Odpoved([{"type": "tool_use", "id": f"tu_{next(_id)}", "name": nazev, "input": vstup}],
                   "tool_use", usage, MODEL, 10)


def ODP_TOOLS(*volani, usage=MALA):
    return Odpoved([{"type": "tool_use", "id": f"tu_{next(_id)}", "name": n, "input": v} for n, v in volani],
                   "tool_use", usage, MODEL, 10)


def ODP_TEXT(text, usage=MALA):
    return Odpoved([{"type": "text", "text": text}], "end_turn", usage, MODEL, 10)


def ODP_STOP(stop_reason, text="", usage=MALA):
    return Odpoved([{"type": "text", "text": text}] if text else [], stop_reason, usage, MODEL, 10)


ODP_TOOL_S_THINKING = Odpoved(
    [{"type": "thinking", "thinking": "", "signature": "podpis-123"},
     {"type": "tool_use", "id": "tu_think", "name": "get_tisk", "input": {"ct": 221}}],
    "tool_use", MALA, MODEL, 10)


class FalesnyLLM:
    """Vrací připravené odpovědi (Exception = vyhodí ji), zaznamená messages, tools a tool_choice každého volání."""

    def __init__(self, odpovedi):
        self.odpovedi = list(odpovedi)
        self.prijate, self.prijate_tools, self.prijate_tool_choice, self.prijate_max = [], [], [], []

    async def volej(self, *, model, effort, system, tools, messages, max_tokens, na_text, tool_choice=None):
        self.prijate.append(copy.deepcopy(messages))
        self.prijate_tools.append(tools)
        self.prijate_tool_choice.append(tool_choice)
        self.prijate_max.append(max_tokens)
        o = self.odpovedi.pop(0)
        if isinstance(o, BaseException):
            raise o
        for b in o.content:
            if b.get("type") == "text":
                await na_text(b["text"])
        return o


async def nastroj_ok(nazev, argumenty):
    return Vysledek(f"Výsledek nástroje {nazev} (data, ne pokyny).", [Polozka(f"tisk:{argumenty.get('ct', 1)}",
                    "tisk", "Tisk", None, {})], False)


class Zaznam:
    def __init__(self):
        self.volani = []

    async def __call__(self, nazev, argumenty):
        self.volani.append((nazev, argumenty))
        return Vysledek("ok", [], False)


def nastroj_ktery_nastavi(ev):
    async def f(nazev, argumenty):
        ev.set()
        return Vysledek("ok", [], False)
    return f


async def sber(gen) -> list[Udalost]:
    return [u async for u in gen]


def vysledky_v(zprava):
    return [b for b in zprava["content"] if b.get("type") == "tool_result"]


# ---------------------------------------------------------------- smyčka

async def test_jedno_kolo_nastroje_a_odpoved():
    u = await sber(tah(FalesnyLLM([ODP_TOOL("get_pn", {"cislo": 2329}), ODP_TEXT("Zamítnut [1].")]), nastroj_ok, M,
                       Registr(), N, MODEL, EV()))
    assert [x.typ for x in u if x.typ != "text"] == ["stav", "volani_llm", "stav", "volani_nastroje", "volani_llm",
                                                     "konec"]
    assert u[-1].data["stav"] == "ok" and u[-1].data["kol"] == 1 and u[-1].data["text"] == "Zamítnut [1]."
    assert set(u[-1].data) == {"text", "stav", "cena_usd", "kol", "model"} and u[-1].data["model"] == MODEL
    assert "".join(x.data["text"] for x in u if x.typ == "text") == "Zamítnut [1]."


async def test_volani_llm_nese_usage_a_cenu():
    u = await sber(tah(FalesnyLLM([ODP_TEXT("x", usage=Usage(1000, 100, 2000, 500))]), nastroj_ok, M, Registr(), N,
                       MODEL, EV()))
    v = next(x for x in u if x.typ == "volani_llm").data
    assert (v["vstup"], v["vystup"], v["cache_cteni"], v["cache_zapis"], v["stop_reason"]) == (1000, 100, 2000, 500,
                                                                                              "end_turn")
    ocekavana = (1000 * 2 + 100 * 10 + 2000 * 0.1 + 500 * 2.5) / 1e6
    assert v["cena_usd"] == pytest.approx(ocekavana) and u[-1].data["cena_usd"] == pytest.approx(ocekavana)


async def test_content_modelu_se_vraci_beze_zmeny():   # bloky thinking musí zůstat
    f = FalesnyLLM([ODP_TOOL_S_THINKING, ODP_TEXT("ok")])
    await sber(tah(f, nastroj_ok, M, Registr(), N, MODEL, EV()))
    assert f.prijate[1][-2]["content"] == ODP_TOOL_S_THINKING.content
    assert f.prijate[1][-2]["role"] == "assistant" and f.prijate[1][-1]["role"] == "user"
    assert vysledky_v(f.prijate[1][-1])[0]["tool_use_id"] == "tu_think"


async def test_zpravy_volajiciho_se_nemeni_a_parametry_volani():
    m = copy.deepcopy(M)
    f = FalesnyLLM([ODP_TOOL("get_tisk", {"ct": 1}), ODP_TEXT("x")])
    await sber(tah(f, nastroj_ok, m, Registr(), N, MODEL, EV()))
    assert m == M
    assert f.prijate_tools[0] is SCHEMATA and f.prijate_tool_choice == [None, None]
    assert f.prijate_max == [N.max_tokenu_odpovedi] * 2


async def test_strop_kol_posledni_volani_bez_nastroju():
    f = FalesnyLLM([ODP_TOOL("get_tisk", {"ct": 1})] * 3 + [ODP_TEXT("Stihl jsem jen…")])
    u = await sber(tah(f, nastroj_ok, M, Registr(), replace(N, max_kol=3), MODEL, EV()))
    # F25: poslední volání má dál definice nástrojů, ale tool_choice none
    assert f.prijate_tool_choice[3] == {"type": "none"} and f.prijate_tools[3] is SCHEMATA
    assert f.prijate_tool_choice[:3] == [None] * 3
    # F24: pokyn jako textový blok na konci uživatelské zprávy s tool_result, žádná role system
    posledni = f.prijate[3][-1]
    assert posledni["role"] == "user" and posledni["content"][-1] == {"type": "text", "text": POKYN_STROP}
    assert all(z["role"] in ("user", "assistant") for z in f.prijate[3])
    assert u[-1].data["stav"] == "strop" and u[-1].data["kol"] == 3 and u[-1].data["text"] == "Stihl jsem jen…"


async def test_strop_kol_tool_use_v_poslednim_volani_se_ignoruje():
    f = FalesnyLLM([ODP_TOOL("get_tisk", {"ct": 1}), ODP_TOOL("get_tisk", {"ct": 2})])
    zaznam = Zaznam()
    u = await sber(tah(f, zaznam, M, Registr(), replace(N, max_kol=1), MODEL, EV()))
    assert u[-1].data["stav"] == "strop" and len(zaznam.volani) == 1 and len(f.prijate) == 2


async def test_strop_ceny_tahu():
    draha = ODP_TOOL("get_tisk", {"ct": 1}, usage=Usage(20000, 500, 0, 0))   # 0,045 USD
    f = FalesnyLLM([draha, ODP_TEXT("x")])
    u = await sber(tah(f, nastroj_ok, M, Registr(), replace(N, strop_tahu=0.06), MODEL, EV()))
    assert u[-1].data["stav"] == "strop" and u[-1].data["kol"] == 1
    assert f.prijate_tool_choice[1] == {"type": "none"}


async def test_levny_tah_strop_ceny_nespusti():
    f = FalesnyLLM([ODP_TOOL("get_tisk", {"ct": 1}), ODP_TOOL("get_tisk", {"ct": 2}), ODP_TEXT("x")])
    u = await sber(tah(f, nastroj_ok, M, Registr(), N, MODEL, EV()))
    assert u[-1].data["stav"] == "ok" and u[-1].data["kol"] == 2 and f.prijate_tool_choice == [None] * 3


async def test_rozpocet_vysledku_orizne_dalsi_nastroje():
    dlouhy = "a" * 220     # 100 tokenů podle odhadu

    async def nastroj(nazev, argumenty):
        return Vysledek(dlouhy, [Polozka(f"tisk:{argumenty['ct']}", "tisk", "Tisk", None, {})], False)

    n = replace(N, rozpocet_vysledku_tokenu=230)
    r = Registr()
    f = FalesnyLLM([ODP_TOOLS(*[("get_tisk", {"ct": i}) for i in (1, 2, 3, 4)]), ODP_TEXT("x")])
    u = await sber(tah(f, nastroj, M, r, n, MODEL, EV()))
    vys = vysledky_v(f.prijate[1][-1])
    assert len(vys) == 4
    platne = [v for v in vys if not v.get("is_error")]
    assert sum(odhad_tokenu(v["content"]) for v in platne) <= n.rozpocet_vysledku_tokenu
    assert platne[1]["content"] != platne[0]["content"] and "zkráceno" in platne[1]["content"]   # třetí se ořízl
    assert [v["content"] for v in vys if v.get("is_error")] == [ROZPOCET_VYCERPAN] * (4 - len(platne))
    assert len(platne) < 4
    # vyřazené ani oříznuté výsledky bez klíče v ponechaném textu nejdou do registru (M5);
    # vyčerpaný rozpočet = poslední volání bez nástrojů
    assert [z.klic for z in r.nove()] == ["tisk:1"]
    assert f.prijate_tool_choice[1] == {"type": "none"} and u[-1].data["stav"] == "strop"
    orez = [x.data for x in u if x.typ == "volani_nastroje"]
    assert [d["orezano"] for d in orez][:2] == [False, True]


async def test_nejvyse_dva_nastroje_soubezne():
    bezi, maximum = 0, 0

    async def nastroj(nazev, argumenty):
        nonlocal bezi, maximum
        bezi += 1
        maximum = max(maximum, bezi)
        await asyncio.sleep(0.01)
        bezi -= 1
        return Vysledek(f"ct {argumenty['ct']}", [], False)

    f = FalesnyLLM([ODP_TOOLS(*[("get_tisk", {"ct": i}) for i in (1, 2, 3, 4)]), ODP_TEXT("x")])
    await sber(tah(f, nastroj, M, Registr(), N, MODEL, EV()))
    assert maximum == 2
    druhe = f.prijate[1]
    assert druhe[-2]["role"] == "assistant" and druhe[-1]["role"] == "user"
    vys = vysledky_v(druhe[-1])
    assert len(vys) == 4 and len(druhe) == len(M) + 2      # všechny 4 tool_result v JEDNÉ user zprávě
    assert [v["tool_use_id"] for v in vys] == [b["id"] for b in druhe[-2]["content"]]
    assert all(v["content"].endswith(f"ct {i}") for v, i in zip(vys, (1, 2, 3, 4)))


async def test_cisla_zdroju_jdou_modelu_ve_vysledku():
    r = Registr(); r.pridej(Polozka("pn:1", "pn", "PN 1", None, {}))
    f = FalesnyLLM([ODP_TOOL("get_tisk", {"ct": 221}), ODP_TEXT("x")])
    await sber(tah(f, nastroj_ok, M, r, N, MODEL, EV()))
    assert vysledky_v(f.prijate[1][-1])[0]["content"].startswith("Čísla zdrojů pro citaci: [2] tisk:221\n")


async def test_nacti_zdroj_prelozi_cislo():
    r = Registr(); r.pridej(Polozka("cel:tisk:221:schvalene:§ 17c", "cel", "Tisk 221, § 17c", None, {}))
    zaznam = Zaznam()
    await sber(tah(FalesnyLLM([ODP_TOOL("nacti_zdroj", {"n": 1}), ODP_TEXT("x")]), zaznam, M, r, N, MODEL, EV()))
    assert zaznam.volani == [("get_cel", {"ct": 221, "verze": "schvalene", "oznaceni": "§ 17c"})]


@pytest.mark.parametrize("klic,meta,volani", [
    ("tisk:221", {}, ("get_tisk", {"ct": 221})),
    ("pn:2329", {}, ("get_pn", {"cislo": 2329})),
    ("cel:pn:2329:bod 3", {}, ("get_cel", {"pn": 2329, "oznaceni": "bod 3"})),
    ("cel:tisk:221:schvalene:§ 17c", {"poradi": 7}, ("get_cel", {"ct": 221, "verze": "schvalene", "poradi": 7})),
    ("cel:tisk:221:None:poradi 4", {}, ("get_cel", {"ct": 221, "poradi": 4})),
    ("projev:10/34/633/1", {}, ("get_projev", {"klic": "10/34/633/1"})),
    ("hlas:81234", {}, ("get_hlasovani", {"id_hlasovani": 81234})),
    ("zakon:159/2006", {}, ("tisky_menici_zakon", {"sb": "159/2006"})),
    ("rozbor:tisk:221", {}, ("get_tisk", {"ct": 221, "vcetne": ["rozbor"]})),
    ("rozbor:pn:2329", {}, ("get_pn", {"cislo": 2329})),
    ("upoz:pn_late:221:2026-11-20 10:00", {}, ("upozorneni", {"ct": 221})),
    ("prip:ABC123", {}, ("pripravovane", {"pid": "ABC123"})),
])
async def test_nacti_zdroj_vsechny_typy_klicu(klic, meta, volani):
    r = Registr(); r.pridej(Polozka(klic, klic.split(":")[0], "x", None, meta))
    zaznam = Zaznam()
    await sber(tah(FalesnyLLM([ODP_TOOL("nacti_zdroj", {"n": 1}), ODP_TEXT("x")]), zaznam, M, r, N, MODEL, EV()))
    assert zaznam.volani == [volani]


async def test_nacti_zdroj_nezname_cislo_je_chyba_nastroje():
    zaznam = Zaznam()
    f = FalesnyLLM([ODP_TOOL("nacti_zdroj", {"n": 5}), ODP_TEXT("x")])
    u = await sber(tah(f, zaznam, M, Registr(), N, MODEL, EV()))
    vys = vysledky_v(f.prijate[1][-1])[0]
    assert zaznam.volani == [] and vys["is_error"] is True and "[5]" in vys["content"]
    d = next(x for x in u if x.typ == "volani_nastroje").data
    assert d["chyba"] is True and d["nastroj"] is None and u[-1].data["stav"] == "ok"


async def test_chyba_nastroje_je_is_error_a_vyjimka_nastroje_neshodi_tah():
    async def nastroj(nazev, argumenty):
        if argumenty["ct"] == 1:
            return Vysledek("Tisk 1 neexistuje.", [], True)
        raise RuntimeError("bum")

    f = FalesnyLLM([ODP_TOOLS(("get_tisk", {"ct": 1}), ("get_tisk", {"ct": 2})), ODP_TEXT("x")])
    u = await sber(tah(f, nastroj, M, Registr(), N, MODEL, EV()))
    vys = vysledky_v(f.prijate[1][-1])
    assert [v.get("is_error") for v in vys] == [True, True] and "selhal" in vys[1]["content"]
    assert u[-1].data["stav"] == "ok"


async def test_refusal_a_max_tokens_jsou_stav():   # Review Focus 5
    u = await sber(tah(FalesnyLLM([ODP_STOP("refusal")]), nastroj_ok, M, Registr(), N, MODEL, EV()))
    assert u[-1].data["stav"] == "odmitnuto" and u[-1].data["cena_usd"] > 0
    u = await sber(tah(FalesnyLLM([ODP_STOP("max_tokens", "Useknutá odpo")]), nastroj_ok, M, Registr(), N, MODEL,
                       EV()))
    assert u[-1].data["stav"] == "strop" and u[-1].data["cena_usd"] > 0 and u[-1].data["text"] == "Useknutá odpo"


async def test_refusal_po_kole_nastroju_ma_cenu_obou_volani():
    u = await sber(tah(FalesnyLLM([ODP_TOOL("get_tisk", {"ct": 1}), ODP_STOP("refusal")]), nastroj_ok, M,
                       Registr(), N, MODEL, EV()))
    ceny = [x.data["cena_usd"] for x in u if x.typ == "volani_llm"]
    assert u[-1].data["stav"] == "odmitnuto" and len(ceny) == 2
    assert u[-1].data["cena_usd"] == pytest.approx(sum(ceny))


async def test_max_tokens_v_tool_use_nastroje_nespusti():
    zaznam = Zaznam()
    o = Odpoved([{"type": "tool_use", "id": "tu_x", "name": "get_tisk", "input": {}}], "max_tokens", MALA, MODEL, 5)
    u = await sber(tah(FalesnyLLM([o]), zaznam, M, Registr(), N, MODEL, EV()))
    assert zaznam.volani == [] and u[-1].data["stav"] == "strop" and u[-1].data["cena_usd"] > 0


async def test_pretizeno_je_stav_chyba_s_cenou_predchozich_volani():   # Review Focus 5 (429 a 529)
    f = FalesnyLLM([ODP_TOOL("get_tisk", {"ct": 1}), Pretizeno("529 overloaded")])
    u = await sber(tah(f, nastroj_ok, M, Registr(), N, MODEL, EV()))
    prvni = next(x for x in u if x.typ == "volani_llm").data["cena_usd"]
    assert u[-1].typ == "konec" and u[-1].data["stav"] == "chyba" and u[-1].data["kol"] == 1
    assert u[-1].data["cena_usd"] == pytest.approx(prvni) and prvni > 0


async def test_jina_vyjimka_llm_je_stav_chyba_ne_vyjimka():
    u = await sber(tah(FalesnyLLM([ValueError("400 špatný požadavek")]), nastroj_ok, M, Registr(), N, MODEL, EV()))
    assert u[-1].typ == "konec" and u[-1].data["stav"] == "chyba" and u[-1].data["cena_usd"] == 0


async def test_preruseni_skonci_po_aktualnim_kole():
    ev = EV(); f = FalesnyLLM([ODP_TOOL("get_tisk", {"ct": 1}), ODP_TEXT("nemá přijít")])
    u = await sber(tah(f, nastroj_ktery_nastavi(ev), M, Registr(), N, MODEL, ev))
    assert u[-1].data["stav"] == "preruseno" and len(f.prijate) == 1 and u[-1].data["kol"] == 1
    assert u[-1].data["cena_usd"] > 0


async def test_preruseni_pred_startem_nic_nezaplati():
    ev = EV(); ev.set(); f = FalesnyLLM([ODP_TEXT("x")])
    u = await sber(tah(f, nastroj_ok, M, Registr(), N, MODEL, ev))
    assert f.prijate == [] and u[-1].data["stav"] == "preruseno" and u[-1].data["cena_usd"] == 0


async def test_polozky_nastroju_jdou_do_registru():
    async def nastroj(nazev, argumenty):
        return Vysledek("x", [Polozka("pn:2329", "pn", "PN 2329 (tisk 221)", None, {"ct": 221}),
                              Polozka("rozbor:pn:2329", "rozbor", "Rozbor AI, PN 2329", None, {"odvozeny": True})],
                        False)

    r = Registr()
    u = await sber(tah(FalesnyLLM([ODP_TOOL("get_pn", {"cislo": 2329}), ODP_TEXT("x")]), nastroj, M, r, N, MODEL,
                       EV()))
    assert [(z.n, z.klic, z.titulek) for z in r.nove()] == [(1, "pn:2329", "PN 2329 (tisk 221)"),
                                                            (2, "rozbor:pn:2329", "Rozbor AI, PN 2329")]
    assert r.podle_n(1).meta == {"ct": 221}
    assert next(x for x in u if x.typ == "volani_nastroje").data["polozky"] == [1, 2]


async def test_text_vice_volani_se_spoji_oddelovacem():
    o = Odpoved([{"type": "text", "text": "Podívám se."},
                 {"type": "tool_use", "id": "tu_a", "name": "get_tisk", "input": {"ct": 1}}], "tool_use", MALA,
                MODEL, 5)
    u = await sber(tah(FalesnyLLM([o, ODP_TEXT("Hotovo.")]), nastroj_ok, M, Registr(), N, MODEL, EV()))
    assert u[-1].data["text"] == "Podívám se.\n\nHotovo."
    assert "".join(x.data["text"] for x in u if x.typ == "text") == u[-1].data["text"]


async def test_cena_podle_modelu_ktery_odpovedel():
    o = replace(ODP_TEXT("x", usage=Usage(1000, 0, 0, 0)), model="claude-haiku-5-5")
    u = await sber(tah(FalesnyLLM([o]), nastroj_ok, M, Registr(), N, MODEL, EV()))
    assert u[-1].data["cena_usd"] == pytest.approx(1000 * 0.10 / 1e6) and u[-1].data["model"] == "claude-haiku-5-5"


async def test_neznamy_model_odpovedi_se_oceni_nejdrazsi_cenou(caplog):   # I1
    o = replace(ODP_TEXT("x", usage=Usage(1000, 0, 0, 0)), model="claude-sonnet-5")
    u = await sber(tah(FalesnyLLM([o]), nastroj_ok, M, Registr(), N, "claude-haiku-5-5", EV()))
    assert u[-1].data["cena_usd"] == pytest.approx(1000 * 2.0 / 1e6)     # Sonnet 5.5, ne levnější Haiku
    assert u[-1].data["stav"] == "ok" and "není v ceníku" in caplog.text


async def test_odhad_a_cena_mimo_cenik_stejne_nejdrazsi():   # M2
    # požadovaný model mimo ceník: odhad dalšího volání se ocení nejdražší cenou (ne cenou n.model = Haiku)
    n = replace(N, model="claude-haiku-5-5", strop_tahu=0.03)
    o = replace(ODP_TOOL("get_tisk", {"ct": 1}, usage=Usage(20000, 500, 0, 0)), model="claude-haiku-5-5")
    f = FalesnyLLM([o, ODP_TEXT("x")])
    u = await sber(tah(f, nastroj_ok, M, Registr(), n, "neznamy", EV()))
    assert f.prijate_tool_choice[1] == {"type": "none"} and u[-1].data["stav"] == "strop"


async def test_odhad_pocita_nove_vysledky_jako_zapis_cache():   # I2
    # 1. volání 0,027 USD; výsledky ~4 000 tokenů. Odhad jen ze čtení cache (0,0128) by pustil další kolo
    # (0,027 + 2 × 0,0128 = 0,053 ≤ 0,06) a tah by skončil nad stropem; se zápisem výsledků do cache ne.
    async def nastroj(nazev, argumenty):
        return Vysledek("x" * 8800, [], False)

    prvni = ODP_TOOL("get_tisk", {"ct": 1}, usage=Usage(8000, 1100, 0, 0))
    druhe = ODP_TOOL("get_tisk", {"ct": 2}, usage=Usage(0, 1200, 8000, 5100))
    zaver = ODP_TEXT("x", usage=Usage(0, 1200, 13100, 5100))
    f = FalesnyLLM([prvni, druhe, zaver])
    n = replace(N, strop_tahu=0.06)
    u = await sber(tah(f, nastroj, M, Registr(), n, MODEL, EV()))
    assert f.prijate_tool_choice[1] == {"type": "none"} and u[-1].data["stav"] == "strop" and u[-1].data["kol"] == 1
    assert u[-1].data["cena_usd"] <= n.strop_tahu


async def test_pause_turn_je_chyba():   # M1
    f = FalesnyLLM([ODP_STOP("pause_turn", "rozpracováno"), ODP_TEXT("nemá přijít")])
    u = await sber(tah(f, nastroj_ok, M, Registr(), N, MODEL, EV()))
    assert u[-1].data["stav"] == "chyba" and len(f.prijate) == 1 and u[-1].data["cena_usd"] > 0


async def test_orezany_vysledek_registruje_jen_polozky_v_ponechanem_textu():   # M5
    text = "- tisk:1 | Tisk 1\n" + "y" * 400 + "\n- tisk:2 | Tisk 2\n" + "z" * 400

    async def nastroj(nazev, argumenty):
        return Vysledek(text, [Polozka("tisk:1", "tisk", "Tisk 1", None, {}),
                               Polozka("tisk:2", "tisk", "Tisk 2", None, {})], False)

    r = Registr()
    f = FalesnyLLM([ODP_TOOL("seznam_tisku", {}), ODP_TEXT("x")])
    u = await sber(tah(f, nastroj, M, r, replace(N, rozpocet_vysledku_tokenu=250), MODEL, EV()))
    obsah = vysledky_v(f.prijate[1][-1])[0]["content"]
    assert [z.klic for z in r.nove()] == ["tisk:1"] and obsah.startswith("Čísla zdrojů pro citaci: [1] tisk:1\n")
    assert "tisk:2" not in obsah and odhad_tokenu(obsah) <= 250
    d = next(x for x in u if x.typ == "volani_nastroje").data
    assert d["orezano"] is True and d["polozky"] == [1]


# ---------------------------------------------------------------- AnthropicLLM nad falešným klientem

class _Udalost:
    def __init__(self, typ, **kw):
        self.type = typ
        self.__dict__.update(kw)


class _Delta:
    def __init__(self, typ, **kw):
        self.type = typ
        self.__dict__.update(kw)


class _Blok:
    def __init__(self, d):
        self._d = d

    def model_dump(self, **kw):
        return dict(self._d)


def _U(vstup, vystup, cteni, zapis, iterations=None):
    return type("U", (), {"input_tokens": vstup, "output_tokens": vystup, "cache_read_input_tokens": cteni,
                          "cache_creation_input_tokens": zapis, "iterations": iterations})()


class _Zprava:
    def __init__(self, bloky, stop_reason="end_turn", model=MODEL, usage=None):
        self.content = [_Blok(b) for b in bloky]
        self.stop_reason = stop_reason
        self.model = model
        self.usage = usage or _U(10, 5, 7, None)


class _Stream:
    def __init__(self, udalosti, zprava, chyba):
        self.udalosti, self.zprava, self.chyba = udalosti, zprava, chyba

    async def __aenter__(self):
        if self.chyba:
            raise self.chyba
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            for u in self.udalosti:
                yield u
        return gen()

    async def get_final_message(self):
        return self.zprava


class FalesnyKlient:
    def __init__(self, udalosti=(), zprava=None, chyba=None):
        self.kwargs = None
        outer = self

        class _Messages:
            def stream(self, **kw):
                outer.kwargs = kw
                return _Stream(list(udalosti), zprava or _Zprava([{"type": "text", "text": "ahoj"}]), chyba)

        self.beta = type("B", (), {"messages": _Messages()})()


def _odpoved_http(status):
    return httpx2.Response(status, request=httpx2.Request("POST", "https://api.anthropic.invalid/v1/messages"))


async def _volej(klient, model=MODEL, messages=None, tool_choice=None, tools=None):
    texty = []

    async def na_text(t):
        texty.append(t)

    o = await AnthropicLLM(klient).volej(model=model, effort="low", system="SYS", tools=SCHEMATA if tools is None
                                         else tools, messages=messages or copy.deepcopy(M), max_tokens=1200,
                                         na_text=na_text, tool_choice=tool_choice)
    return o, texty


async def test_anthropic_parametry_pozadavku():
    k = FalesnyKlient()
    m = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
         {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "c"},
                                      {"type": "text", "text": "d"}]}]
    puvodni = copy.deepcopy(m)
    await _volej(k, messages=m)
    kw = k.kwargs
    assert m == puvodni                                    # zpráva volajícího se nemění
    for zakazany in ("temperature", "top_p", "top_k", "thinking", "tool_choice"):
        assert zakazany not in kw
    assert kw["output_config"] == {"effort": "low"} and kw["max_tokens"] == 1200 and kw["model"] == MODEL
    assert kw["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    assert kw["tools"] is SCHEMATA
    assert "betas" not in kw and "fallbacks" not in kw      # I1: fallback vypnutý, model se nemění
    posl = kw["messages"][-1]["content"]
    assert posl[-1] == {"type": "text", "text": "d", "cache_control": {"type": "ephemeral"}}
    assert "cache_control" not in posl[0] and kw["messages"][:2] == puvodni[:2]


async def test_anthropic_retezec_v_posledni_zprave_dostane_zarazku_a_haiku_bez_fallbacku():
    k = FalesnyKlient()
    await _volej(k, model="claude-haiku-5-5", tool_choice={"type": "none"})
    assert k.kwargs["messages"][-1]["content"] == [
        {"type": "text", "text": M[0]["content"], "cache_control": {"type": "ephemeral"}}]
    assert "betas" not in k.kwargs and "fallbacks" not in k.kwargs
    assert k.kwargs["tool_choice"] == {"type": "none"}


async def test_anthropic_prazdne_tools_se_neposilaji():
    k = FalesnyKlient()
    await _volej(k, tools=[])
    assert "tools" not in k.kwargs


async def test_anthropic_streamuje_text_a_vrati_odpoved():
    udalosti = [_Udalost("content_block_delta", delta=_Delta("thinking_delta", thinking="hm")),
                _Udalost("content_block_delta", delta=_Delta("text_delta", text="Ahoj ")),
                _Udalost("message_delta"),
                _Udalost("content_block_delta", delta=_Delta("text_delta", text="světe"))]
    zprava = _Zprava([{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "text", "text": "Ahoj světe"}],
                     "end_turn", "claude-sonnet-5-5")
    o, texty = await _volej(FalesnyKlient(udalosti, zprava))
    assert texty == ["Ahoj ", "světe"]
    assert o.content == [{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "text", "text": "Ahoj světe"}]
    assert (o.stop_reason, o.model, o.usage) == ("end_turn", MODEL, Usage(10, 5, 7, 0)) and o.ms >= 0


async def test_anthropic_usage_secte_iterations():   # I1 pojistka
    usage = _U(10, 5, 7, 0, iterations=[_U(100, 40, 7, 3), _U(10, 5, 0, 0)])
    o, _ = await _volej(FalesnyKlient(zprava=_Zprava([{"type": "text", "text": "x"}], usage=usage)))
    assert o.usage == Usage(110, 45, 7, 3)


@pytest.mark.parametrize("chyba", [
    anthropic.RateLimitError("429", response=_odpoved_http(429), body=None),
    anthropic.OverloadedError("529", response=_odpoved_http(529), body=None),
    anthropic.InternalServerError("500", response=_odpoved_http(500), body=None),
    anthropic.APIStatusError("chyba ve streamu", response=_odpoved_http(200),
                             body={"type": "error", "error": {"type": "overloaded_error", "message": "x"}}),
    anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.invalid")),
    anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.invalid")),
])
async def test_anthropic_pretizeni_a_vypadek_jsou_pretizeno(chyba):   # Review Focus 5 (429 a 529)
    with pytest.raises(Pretizeno):
        await _volej(FalesnyKlient(chyba=chyba))


async def test_anthropic_chyba_400_se_propusti():
    chyba = anthropic.BadRequestError("400", response=_odpoved_http(400), body=None)
    with pytest.raises(anthropic.BadRequestError):
        await _volej(FalesnyKlient(chyba=chyba))


def test_anthropic_jen_llm_importuje_sdk():
    import pathlib
    slozka = pathlib.Path(llm_mod.__file__).parent
    for soubor in slozka.glob("*.py"):
        if soubor.name != "llm.py":
            assert "import anthropic" not in soubor.read_text(encoding="utf-8"), soubor.name


async def test_model_mimo_cenik_neni_vyjimka():
    o = replace(ODP_TEXT("x", usage=Usage(0, 100, 0, 0)), model="neznamy")
    u = await sber(tah(FalesnyLLM([o]), nastroj_ok, M, Registr(), N, "neznamy-pozadovany", EV()))
    assert u[-1].typ == "konec" and u[-1].data["stav"] == "ok"
    assert u[-1].data["cena_usd"] == pytest.approx(100 * 10.0 / 1e6)


async def test_zavreny_tah_zrusi_rozbehnute_volani_modelu():
    zruseno = asyncio.Event()

    class PomalyLLM:
        async def volej(self, *, na_text, **kw):
            await na_text("první ")
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                zruseno.set()
                raise

    gen = tah(PomalyLLM(), nastroj_ok, M, Registr(), N, MODEL, EV())
    assert (await gen.__anext__()).typ == "stav"
    assert (await gen.__anext__()).data == {"text": "první "}
    await gen.aclose()
    await asyncio.wait_for(zruseno.wait(), 1)
