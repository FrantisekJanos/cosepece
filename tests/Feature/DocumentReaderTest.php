<?php

declare(strict_types=1);

namespace Tests\Feature;

use Tests\TestCase;

final class DocumentReaderTest extends TestCase
{
    public function test_reader_displays_bill_and_selected_amendment_safely(): void
    {
        $this->importFixtures();
        db()->run('INSERT INTO tisk_text (ct,text,chars,docx_url) VALUES (311,?,50,?)', ["Původní zákon\n<script>alert(1)</script>", 'https://www.psp.cz/sqw/text/orig2.sqw?idd=280196']);
        $pn = db()->one('SELECT * FROM pn WHERE ct=311 LIMIT 1');
        db()->run('INSERT INTO pn_text (id_dokument,ct,cislo,text,chars) VALUES (?,311,?,?,30)', [$pn['id_dokument'], $pn['cislo'], 'Obsah pozměňovacího návrhu']);
        $this->get('/tisky/311')->assertOk()->assertSee('Otevřít zákon a návrhy');
        $this->get('/tisky/311/dokumenty')->assertOk()->assertSee('word-viewer')->assertSee('/tisky/311/dokumenty/word', false)->assertDontSee('<script>alert(1)</script>', false);
        $this->get('/tisky/311/dokumenty?pn=' . $pn['id_dokument'])->assertOk()->assertSee('Návrh č. ' . $pn['cislo'])->assertSee('Pozměňovací návrh')->assertDontSee('Původní zákon');
        $this->get('/tisky/90/dokumenty?pn=' . $pn['id_dokument'])->assertNotFound();
    }

    public function test_reader_handles_missing_documents_and_invalid_input(): void
    {
        $this->importFixtures();
        $this->get('/tisky/311/dokumenty')->assertOk()->assertSee('Word zatím není dostupný')
            ->assertSee('/tisky/311#hlasovani', false)->assertSee('0 načtených hlasování');
        $this->get('/tisky/78/dokumenty')->assertOk()->assertSee('4 načtených hlasování');
        $this->get('/tisky/311')->assertOk()->assertSee('id="hlasovani"', false)
            ->assertSee('Neznamená to, že se o něm nehlasovalo.');
        $this->get('/tisky/999999/dokumenty')->assertNotFound();
        $this->get('/tisky/311/dokumenty?pn=invalid')->assertUnprocessable();
    }
    public function test_reader_shows_bill_context_and_escaped_ai_analysis(): void
    {
        $this->importFixtures();
        $ai = json_encode(['shrnuti' => 'Shrnutí <b>návrhu</b>', 'zavaznost' => 6, 'zpetna_ucinnost' => true, 'citace' => ['Doslovná citace']], JSON_UNESCAPED_UNICODE);
        db()->run('INSERT INTO tisk_text (ct,docx_url,ai_json,ai_score) VALUES (311,?,?,6)', ['https://www.psp.cz/sqw/text/orig2.sqw?idd=280196', $ai]);
        $this->get('/tisky/311/dokumenty')->assertOk()
            ->assertSee('Rozbor dokumentu')->assertSee('AI 6/10')->assertSee('Zpětné použití')
            ->assertSee('Doslovná citace')->assertSee('Shrnutí &lt;b&gt;návrhu&lt;/b&gt;', false)
            ->assertSee('data-zoom="fit"', false)->assertSee('Pozměňovací návrhy');
    }

    public function test_word_endpoint_returns_original_bytes_and_rejects_external_hosts(): void
    {
        $this->importFixtures();
        $url = 'https://www.psp.cz/sqw/text/orig2.sqw?idd=280196';
        db()->run('INSERT INTO tisk_text (ct,docx_url) VALUES (311,?)', [$url]);
        $binary = "PK\x03\x04original-document";
        \Illuminate\Support\Facades\Cache::put('word-document:' . hash('sha256', $url), $binary, 60);
        $this->get('/tisky/311/dokumenty/word')->assertOk()
            ->assertHeader('Content-Type', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
            ->assertContent($binary);
        db()->run('UPDATE tisk_text SET docx_url=? WHERE ct=311', ['https://example.com/file.docx']);
        $this->get('/tisky/311/dokumenty/word')->assertNotFound();
    }

}
