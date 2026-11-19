"""Stažení DOCX tisků, PN a schváleného znění z psp.cz a převod na prostý text.

Funkce pro hledání odkazů jsou čisté (dostanou HTML dekódované z windows-1250,
vrací absolutní URL nebo None). Převod DOCX je port PHP `PnText::docxToText`.
"""
from __future__ import annotations

import html
import io
import re
import zipfile

from hlidac_rag.psp.http import BASE, Fetcher

RE_TAG = re.compile(r"<[^>]*>")
RE_INSTR_TEXT = re.compile(r"<w:instrText\b[^>]*>.*?</w:instrText>", re.S)
RE_DEL_TEXT = re.compile(r"<w:delText\b[^>]*>.*?</w:delText>", re.S)
# kódy polí Wordu, které PHP převodník nechal v textu (každý vzor je omezený, běžný text nezasáhne)
RE_KODY_POLI = [
    re.compile(r'HYPERLINK\s+"[^"]*"(?:\s*\\[a-z]\s+"[^"]*")*'),
    re.compile(r"PAGEREF\s+\S+(?:\s+\\h)?\s*"),
    re.compile(r"(?:\\\*\s*)?MERGEFORMAT\s*"),
    re.compile(r'\bTOC\s+\\[a-z]\s+"[^"]*"(?:\s+\\[a-z])*\s*'),
    re.compile(r"\bSEQ\s+\w+(?:\s+\\[*a-z])+(?:\s+[A-Za-z]+)?\s*"),
    re.compile(r"\bFORMTEXT\b\s*"),
]
RE_VICE_RADKU = re.compile(r"\n{3,}")
RE_A = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S | re.I)
RE_HREF = re.compile(r'href\s*=\s*"([^"]*)"', re.I)
RE_ODSTAVEC_SCHVALENE = re.compile(
    r"<p\b[^>]*>(?:(?!</p>).)*?Text\s+návrhu\s+zákona\s+(?:(?!</p>).)*?Poslaneckou\s+sněmovnou(?:(?!</p>).)*?</p>",
    re.S | re.I,
)


def docx_to_text(data: bytes) -> str:
    """Text z word/document.xml: odstavce na řádky, tabulátory, značky pryč."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    xml = RE_INSTR_TEXT.sub("", xml)
    xml = RE_DEL_TEXT.sub("", xml)
    xml = xml.replace("</w:p>", "\n").replace("<w:tab/>", "\t").replace("<w:br/>", "\n")
    text = html.unescape(RE_TAG.sub("", xml))
    return RE_VICE_RADKU.sub("\n\n", text).strip()


def vycisti_kody_poli(text: str) -> str:
    """Odstraní z textu kódy polí Wordu (HYPERLINK, PAGEREF, MERGEFORMAT, TOC, SEQ, FORMTEXT)."""
    for rx in RE_KODY_POLI:
        text = rx.sub("", text)
    return text


def _absolutni(href: str) -> str:
    href = html.unescape(href).strip()
    if href.startswith("/"):
        return BASE + href
    if href.startswith("http"):
        return href
    return BASE + "/sqw/text/" + href


def _href(atributy: str) -> str | None:
    m = RE_HREF.search(atributy)
    return m.group(1) if m else None


def odkaz_docx_tisku(html_tisku: str) -> str | None:
    """Stránka tiskt.sqw?…&ct1=0: odkaz s title="Dokument DOCX"."""
    for m in RE_A.finditer(html_tisku):
        if re.search(r'title\s*=\s*"Dokument DOCX"', m.group(1), re.I):
            href = _href(m.group(1))
            if href:
                return _absolutni(href)
    return None


def odkaz_docx_pn(html_pn: str) -> str | None:
    """Stránka sd.sqw?cd=…: odkaz uvnitř class="file docx"."""
    m = re.search(r'class\s*=\s*"file docx"\s*>\s*<a\b([^>]*)>', html_pn, re.I)
    href = _href(m.group(1)) if m else None
    return _absolutni(href) if href else None


def odkaz_schvalene_stranky(html_historie: str) -> str | None:
    """Historie tisku: odkaz „schváleného“ v odstavci o textu návrhu schváleném Sněmovnou."""
    for odst in RE_ODSTAVEC_SCHVALENE.finditer(html_historie):
        for m in RE_A.finditer(odst.group(0)):
            href = _href(m.group(1))
            if href and "schv" in RE_TAG.sub("", m.group(2)).lower():
                return _absolutni(href)
    return None


def odkaz_originalu(html_pz: str) -> str | None:
    """Stránka schváleného znění (v=PZ): odkaz „Originál dokumentu“ na orig2.sqw (DOCX)."""
    for m in RE_A.finditer(html_pz):
        href = _href(m.group(1))
        if href and "orig2.sqw?idd=" in href and "Originál dokumentu" in RE_TAG.sub("", m.group(2)):
            return _absolutni(href)
    return None


def stahni_text(fetcher: Fetcher, url: str) -> str | None:
    """Stáhne DOCX z psp.cz a vrátí jeho text; None, když soubor neexistuje."""
    data = fetcher.get(_absolutni(url))
    if data is None:
        return None
    return docx_to_text(data)
