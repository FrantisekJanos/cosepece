<?php
// Stažení a čtení ZIP balíků otevřených dat psp.cz (soubory .unl, oddělovač „|“, windows-1250).

declare(strict_types=1);

namespace Hlidac;

use RuntimeException;
use ZipArchive;

final class Source
{
    /** pauzy v sekundách před jednotlivými pokusy o stažení balíku (první hned) */
    private const RETRY_DELAYS = [0, 15, 60];

    /** @var array<string, ZipArchive> */
    private array $zips = [];

    public function __construct(
        private string $baseUrl,
        private string $cacheDir,
        private ?string $localDir = null
    ) {
        if (!is_dir($this->cacheDir)) {
            mkdir($this->cacheDir, 0775, true);
        }
    }

    public function fetch(string $package): string
    {
        // vrátí cestu k ZIPu; z lokálního adresáře (testy) nebo stažením z psp.cz
        if ($this->localDir !== null) {
            $path = rtrim($this->localDir, '/') . '/' . $package . '.zip';
            if (!is_file($path)) {
                throw new RuntimeException("Chybí lokální soubor $path");
            }
            return $path;
        }

        $url = rtrim($this->baseUrl, '/') . '/' . $package . '.zip';
        $target = $this->cacheDir . '/' . $package . '.zip';
        $tmp = $target . '.part';
        // psp.cz občas krátce neodpovídá (timeout spojení); dva další pokusy po pauze, teprve pak chyba
        $error = '';
        foreach (self::RETRY_DELAYS as $delay) {
            if ($delay > 0) {
                sleep($delay);
            }
            $fp = fopen($tmp, 'wb');
            if ($fp === false) {
                throw new RuntimeException("Nelze zapisovat do $tmp");
            }
            $ch = curl_init($url);
            curl_setopt_array($ch, [
                CURLOPT_FILE => $fp,
                CURLOPT_FOLLOWLOCATION => true,
                CURLOPT_TIMEOUT => 300,
                CURLOPT_CONNECTTIMEOUT => 20,
                CURLOPT_USERAGENT => 'HlidacZakonu/1.0 (+' . siteUrl() . ')',
                CURLOPT_FAILONERROR => true,
            ]);
            $ok = curl_exec($ch);
            $error = curl_error($ch);
            fclose($fp);
            if ($ok !== false) {
                rename($tmp, $target);
                return $target;
            }
            @unlink($tmp);
        }
        throw new RuntimeException("Stažení $url selhalo (" . count(self::RETRY_DELAYS) . " pokusy): $error");
    }

    public function rows(string $package, string $file): \Generator
    {
        // postupně vrací řádky souboru jako pole sloupců (bez načtení celého souboru do paměti)
        $zip = $this->open($package);
        $stream = $zip->getStream($file);
        if ($stream === false) {
            throw new RuntimeException("V balíku $package chybí soubor $file");
        }
        while (($line = fgets($stream)) !== false) {
            $line = rtrim($line, "\r\n");
            if ($line === '') {
                continue;
            }
            $line = iconv('CP1250', 'UTF-8//IGNORE', $line);
            if ($line === false) {
                continue;
            }
            yield explode('|', $line);
        }
        fclose($stream);
    }

    /** Je soubor v balíku? */
    public function has(string $package, string $file): bool
    {
        return $this->open($package)->locateName($file) !== false;
    }

    public function map(string $package, string $file, int $keyCol, int $valueCol): array
    {
        // číselník jako pole klíč => hodnota
        $out = [];
        foreach ($this->rows($package, $file) as $cols) {
            $out[$cols[$keyCol]] = $cols[$valueCol] ?? '';
        }
        return $out;
    }

    private function open(string $package): ZipArchive
    {
        if (!isset($this->zips[$package])) {
            $zip = new ZipArchive();
            $path = $this->fetch($package);
            if ($zip->open($path) !== true) {
                throw new RuntimeException("Nelze otevřít $path");
            }
            $this->zips[$package] = $zip;
        }
        return $this->zips[$package];
    }
}
