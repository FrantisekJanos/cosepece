"""Hledání podle významu: embedding dotazu, husté (vektorové) hledání v rag.usek a slučování RRF.

Volání API pro dotazy mají denní strop EMBED_DOTAZY_DEN (výchozí 20 000, den = datum UTC, počítadlo v paměti procesu
MCP, dotazy z cache se nepočítají); po vyčerpání se hledá jen podle slov.

Embedding dotazu má tvrdý strop TIMEOUT (vlákno s future, ne jen timeout spojení) a jeden pokus; při chybě nebo bez
klíče vrátí None a důvod nechá v posledni_chyba(), nástroj pak hledá lexikálně a řekne to v odpovědi.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from .indexer import DIM
from .klient import VYCHOZI_MODEL, EmbedChyba, Klient

log = logging.getLogger("hlidac_rag.embed")

TIMEOUT = 3.0            # s, celkový strop embeddingu dotazu
CACHE = 256              # dotazů v LRU cache
RRF_K = 60
HUSTYCH = 50             # husté výsledky (úseky) do slučování
NA_ZDROJ = 2             # nejvýš úseků z jednoho projevu nebo celku
EF_SEARCH = 100          # hnsw.ef_search pro husté hledání (výchozí 40 je pro top-50 málo)

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="embed-dotaz")
_cache: OrderedDict[tuple, list[float]] = OrderedDict()
_zamek = threading.Lock()
_stav = threading.local()
_klienti: dict[tuple, Klient] = {}
DOTAZY_DEN = 20000       # výchozí denní strop embeddingů dotazů (EMBED_DOTAZY_DEN), napříč všemi tokeny MCP
_den = {"datum": None, "pocet": 0}   # počítadlo volání API za den (UTC) v paměti procesu MCP


def _dnes() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _denni_strop() -> int:
    """Denní strop embeddingů dotazů z EMBED_DOTAZY_DEN (výchozí DOTAZY_DEN; nečitelná hodnota = výchozí)."""
    try:
        return max(0, int((os.environ.get("EMBED_DOTAZY_DEN") or "").strip() or DOTAZY_DEN))
    except ValueError:
        return DOTAZY_DEN


def _zapocti_dotaz() -> bool:
    """Započte jedno volání API do denního stropu; False = strop pro dnešek (UTC) vyčerpán. Volá se pod zámkem."""
    dnes = _dnes()
    if _den["datum"] != dnes:
        _den.update(datum=dnes, pocet=0)
    if _den["pocet"] >= _denni_strop():
        return False
    _den["pocet"] += 1
    return True


def aktualni_model() -> str:
    """Model, kterým jsou úseky indexované a kterým se embeduje dotaz (EMBED_MODEL)."""
    return (os.environ.get("EMBED_MODEL") or "").strip() or VYCHOZI_MODEL


DUVOD_BEZ_KLICE = "hledání podle významu není zapnuté (chybí OPENAI_API_KEY)"


def _klient() -> Klient | None:
    """Klient pro dotazy (1 pokus, timeout TIMEOUT); jeden na kombinaci klíče, modelu a dimenze.
    Dimenze jiná než sloupec rag.usek.embedding (nebo nečitelná EMBED_DIM) = None, jinak by padal hustý dotaz."""
    klic = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not klic:
        return None
    k = (hash(klic), aktualni_model(), os.environ.get("EMBED_DIM"))
    with _zamek:
        if k not in _klienti:
            _klienti.clear()
            try:
                klient = Klient.z_prostredi(timeout=TIMEOUT, pokusu=1)
            except ValueError:
                klient = None
            if klient is not None and klient.dim != DIM:
                log.warning("embeddingy: EMBED_DIM=%s neodpovídá sloupci (%s), hledá se jen podle slov", klient.dim, DIM)
                klient = None
            _klienti[k] = klient
        return _klienti[k]


def je_zapnuto() -> bool:
    """Klíč je nastavený a konfigurace sedí (bez volání API)."""
    return _klient() is not None


def posledni_chyba() -> str | None:
    """Proč poslední vektor_dotazu v tomto vlákně vrátil None (bez klíče, timeout, chyba API)."""
    return getattr(_stav, "chyba", None)


def vycisti_cache() -> None:
    with _zamek:
        _cache.clear()
        _klienti.clear()
        _den.update(datum=None, pocet=0)
    _stav.chyba = None


def vektor_dotazu(dotaz: str) -> list[float] | None:
    """Embedding dotazu z LRU cache nebo z API do TIMEOUT s; jinak None a důvod v posledni_chyba()."""
    _stav.chyba = None
    klient = _klient()
    if klient is None:
        _stav.chyba = DUVOD_BEZ_KLICE
        return None
    klic = (klient.model, klient.dim, " ".join(dotaz.split()).lower())
    with _zamek:
        if klic in _cache:
            _cache.move_to_end(klic)
            return _cache[klic]
        if not _zapocti_dotaz():
            _stav.chyba = "denní limit sémantického hledání vyčerpán"
            return None
    fut = _pool.submit(klient.embed, [dotaz])
    try:
        vektory, _ = fut.result(timeout=TIMEOUT)
    except FutureTimeout:
        _stav.chyba = f"embedding dotazu nedorazil do {TIMEOUT:g} s"
        return None
    except EmbedChyba as e:
        _stav.chyba = f"embedding dotazu selhal ({e})"
        return None
    except Exception as e:   # pojistka: hledání nesmí spadnout kvůli embeddingu
        log.warning("embedding dotazu: %s", type(e).__name__)
        _stav.chyba = f"embedding dotazu selhal ({type(e).__name__})"
        return None
    v = vektory[0]
    with _zamek:
        _cache[klic] = v
        while len(_cache) > CACHE:
            _cache.popitem(last=False)
    return v


def rrf(seznamy: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: skóre = součet 1 / (k + pořadí) přes seznamy (pořadí od 1); shoda skóre = dřív viděný."""
    skore: dict = {}
    for seznam in seznamy:
        for i, klic in enumerate(seznam, start=1):
            skore[klic] = skore.get(klic, 0.0) + 1.0 / (k + i)
    return sorted(skore.items(), key=lambda kv: -kv[1])


def _vektor_sql(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6g}" for x in v) + "]"


_SPOJENI = {
    "projev": "JOIN steno.projev p ON p.id = u.projev_id",
    "cel": "JOIN texty.cel c ON c.id = u.cel_id JOIN texty.dokument d ON d.id = c.dokument_id",
}


def husty(conn, vektor: list[float], zdroj: str, filtry_sql: str, params: list, limit: int = HUSTYCH,
          model: str | None = None) -> list[dict]:
    """Nejbližší úseky k vektoru dotazu (kosinová vzdálenost, HNSW) s filtry nástroje, nejvýš NA_ZDROJ ze zdroje.

    filtry_sql je podmínka nad aliasy p (projev) nebo c, d (celek, dokument) s parametry params. Úzký filtr by
    s obyčejným HNSW vrátil méně než limit, proto iterativní průchod indexu (relaxed_order, pořadí se srovná
    vnějším ORDER BY)."""
    if zdroj not in _SPOJENI:
        raise ValueError(f"neznámý zdroj {zdroj}")
    v = _vektor_sql(vektor)
    with conn.cursor() as cur:
        cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        cur.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}")
    sql = f"""SELECT projev_id, cel_id, od_znaku, do_znaku, vzdalenost FROM (
                SELECT x.*, row_number() OVER (PARTITION BY coalesce(x.projev_id, x.cel_id) ORDER BY x.vzdalenost) AS n
                FROM (SELECT u.projev_id, u.cel_id, u.od_znaku, u.do_znaku, u.embedding <=> %s::halfvec AS vzdalenost
                      FROM rag.usek u {_SPOJENI[zdroj]}
                      WHERE u.zdroj = %s AND u.model = %s AND ({filtry_sql or 'TRUE'})
                      ORDER BY u.embedding <=> %s::halfvec LIMIT %s) x) y
              WHERE n <= {NA_ZDROJ} ORDER BY vzdalenost LIMIT %s"""
    with conn.cursor(row_factory=_dict_row()) as cur:
        cur.execute(sql, (v, zdroj, model or aktualni_model(), *params, v, limit * 3, limit))
        return list(cur.fetchall())


def _dict_row():
    from psycopg.rows import dict_row
    return dict_row


def je_index(conn, zdroj: str, model: str | None = None) -> bool:
    """rag.usek existuje a má úseky zdroje pro aktuální model."""
    with conn.cursor(row_factory=_dict_row()) as cur:
        cur.execute("SELECT to_regclass('rag.usek') IS NOT NULL AS ok")
        if not cur.fetchall()[0]["ok"]:
            return False
        cur.execute("SELECT EXISTS (SELECT 1 FROM rag.usek WHERE zdroj = %s AND model = %s) AS ok",
                    (zdroj, model or aktualni_model()))
        return bool(cur.fetchall()[0]["ok"])
