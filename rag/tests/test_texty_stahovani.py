"""Testy stahování textů z psp.cz: odkazy z uložených stránek a převod DOCX (bez sítě)."""
import io
import zipfile
from pathlib import Path

from hlidac_rag.psp.parser import page_text
from hlidac_rag.texty.stahovani import (
    docx_to_text,
    vycisti_kody_poli,
    odkaz_docx_pn,
    odkaz_docx_tisku,
    odkaz_originalu,
    odkaz_schvalene_stranky,
    stahni_text,
)

HTML = Path(__file__).parent / "fixtures" / "texty" / "html"


def stranka(name: str) -> str:
    return page_text((HTML / name).read_bytes())


def vzor_docx(document_xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def test_schvalene_znění_76():
    url = odkaz_schvalene_stranky(stranka("historie-76.html"))
    assert url and url.endswith("ct=76&ct1=0&v=PZ&pn=4&pt=1") and "&amp;" not in url
    assert url.startswith("https://www.psp.cz/")


def test_schvalene_znění_311_chybí():
    assert odkaz_schvalene_stranky(stranka("historie-311.html")) is None


def test_original_pz():
    url = odkaz_originalu(stranka("tiskt-76-pz.html"))
    assert url and url.endswith("idd=278472") and url.startswith("https://www.psp.cz/")


def test_docx_tisku():
    url = odkaz_docx_tisku(stranka("tiskt-76-0.html"))
    assert url and url.endswith("idd=266516")


def test_docx_pn():
    url = odkaz_docx_pn(stranka("sd-894.html"))
    assert url and url.endswith("idd=269467")


def test_bez_odkazu():
    assert odkaz_docx_tisku("<html></html>") is None
    assert odkaz_docx_pn("<html></html>") is None
    assert odkaz_originalu("<html></html>") is None


def test_docx_to_text_vzor():
    xml = (
        '<w:document><w:body><w:p><w:r><w:t>První odstavec</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Druhý</w:t></w:r><w:r><w:tab/><w:t>odstavec</w:t></w:r></w:p></w:body></w:document>'
    )
    assert docx_to_text(vzor_docx(xml)) == "První odstavec\nDruhý\todstavec"


def test_docx_to_text_entity_a_prazdne_radky():
    xml = "<w:p><w:t>A &amp; B &lt;x&gt;</w:t></w:p><w:p></w:p><w:p></w:p><w:p><w:t>C<w:br/>D</w:t></w:p>"
    assert docx_to_text(vzor_docx(xml)) == "A & B <x>\n\n\nC\nD".replace("\n\n\n", "\n\n")


class FalesnyFetcher:
    def __init__(self, data):
        self.data, self.urls = data, []

    def get(self, url):
        self.urls.append(url)
        return self.data


def test_stahni_text():
    docx = vzor_docx("<w:p><w:t>Ahoj</w:t></w:p>")
    f = FalesnyFetcher(docx)
    assert stahni_text(f, "/sqw/text/orig2.sqw?idd=1") == "Ahoj"
    assert f.urls == ["https://www.psp.cz/sqw/text/orig2.sqw?idd=1"]
    assert stahni_text(FalesnyFetcher(None), "https://www.psp.cz/x") is None


def test_docx_bez_kodu_poli_a_smazaneho_textu():
    xml = (
        '<w:document><w:body><w:p>'
        '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText xml:space="preserve"> HYPERLINK "https://www.e-sbirka.cz/" </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
        '<w:r><w:t>Zákon č. 2/1969 Sb.</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
        '<w:del w:id="1"><w:r><w:delText xml:space="preserve">smazáno</w:delText></w:r></w:del>'
        '</w:p></w:body></w:document>'
    )
    text = docx_to_text(vzor_docx(xml))
    assert "Zákon č. 2/1969 Sb." in text
    assert "HYPERLINK" not in text and "smazáno" not in text


def test_vycisti_hyperlink_pageref_mergeformat():
    assert vycisti_kody_poli(
        'HYPERLINK "https://www.e-sbirka.cz/"Zákon č. 2/1969 Sb., o zřízení ministerstev'
    ) == "Zákon č. 2/1969 Sb., o zřízení ministerstev"
    assert vycisti_kody_poli(r'Čl. III HYPERLINK "https://x.cz/a?b=1" \l "Odkaz"Text') == "Čl. III Text"
    assert vycisti_kody_poli(r"Úvod PAGEREF _Toc123456 \h 5") == "Úvod 5"
    assert vycisti_kody_poli(r"Text \* MERGEFORMAT další") == "Text další"


def test_vycisti_nesahá_na_normalni_text():
    t = 'Podle „zákona“ a "§ 17c" odst. 2 se slova "a více" nahrazují.\nDruhý odstavec.'
    assert vycisti_kody_poli(t) == t
