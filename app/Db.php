<?php
// Tenká vrstva nad PDO. Schéma funguje v SQLite, MySQL/MariaDB i PostgreSQL.

declare(strict_types=1);

namespace Hlidac;

use PDO;
use PDOStatement;

final class Db
{
    public PDO $pdo;
    public string $driver;

    public function __construct(string $dsn, ?string $user = null, ?string $pass = null)
    {
        if (str_starts_with($dsn, 'sqlite:')) {
            // u SQLite zajistíme existenci adresáře pro soubor databáze
            $file = substr($dsn, 7);
            $dir = dirname($file);
            if ($dir !== '' && !is_dir($dir)) {
                mkdir($dir, 0775, true);
            }
        }
        $this->pdo = new PDO($dsn, $user, $pass, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]);
        $this->driver = (string)$this->pdo->getAttribute(PDO::ATTR_DRIVER_NAME);
        if ($this->driver === 'sqlite') {
            $this->pdo->exec('PRAGMA journal_mode = WAL');
            $this->pdo->exec('PRAGMA foreign_keys = OFF');
        } elseif ($this->driver === 'mysql') {
            $this->pdo->exec("SET NAMES utf8mb4");
        } elseif ($this->driver === 'pgsql') {
            $this->pdo->exec("SET client_encoding TO 'UTF8'");
        }
    }

    /**
     * Otisk schématu: mění se s každou úpravou tohoto souboru (nová tabulka, sloupec, index).
     * Všechny změny struktury databáze proto patří jen do migrate() v tomto souboru.
     */
    public static function schemaStamp(): string
    {
        return md5_file(__FILE__) ?: '0';
    }

    /**
     * Spustí migrate() jen tehdy, když je databáze nová nebo se od poslední migrace změnilo schéma
     * (jiný otisk tohoto souboru než ten uložený v meta schema_stamp). Kontrola struktury při každém
     * požadavku stála v PostgreSQL desítky dotazů (kolem 0,2 s); takhle je to jeden dotaz.
     * Vrací true, když migrace proběhla.
     */
    public function migrateIfNeeded(): bool
    {
        $stamp = self::schemaStamp();
        try {
            if ($this->value("SELECT v FROM meta WHERE k = 'schema_stamp'") === $stamp) {
                return false;
            }
        } catch (\Throwable) {
            // nová databáze: tabulka meta ještě neexistuje
        }
        $this->migrate();
        // otisk až po úspěšné migraci: když selže, zkusí se při dalším požadavku znovu
        $this->setMeta('schema_stamp', $stamp);
        return true;
    }

    public function migrate(): void
    {
        // tabulky se vytvoří jen pokud neexistují; data z psp.cz se při každém importu přepíší
        // MySQL TEXT pojme jen 64 kB; texty tisků bývají delší (#2 bod 12)
        $text = $this->driver === 'mysql' ? 'MEDIUMTEXT' : 'TEXT';
        $key = $this->driver === 'sqlite' ? 'TEXT' : 'VARCHAR(190)';
        $suffix = $this->driver !== 'mysql' ? '' : ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_czech_ci';

        $this->pdo->exec("CREATE TABLE IF NOT EXISTS tisky (
            id_tisk INTEGER PRIMARY KEY,
            ct INTEGER NOT NULL,
            druh INTEGER NOT NULL,
            typ_navrhu VARCHAR(20) NOT NULL,
            navrhovatel VARCHAR(255),
            nazev VARCHAR(255),
            uplny_nazev $text,
            predlozeno VARCHAR(19),
            faze VARCHAR(60),
            posledni VARCHAR(19),
            p90 INTEGER NOT NULL DEFAULT 0,
            p90ok INTEGER NOT NULL DEFAULT 0,
            zl INTEGER NOT NULL DEFAULT 0,
            pn_count INTEGER NOT NULL DEFAULT 0,
            pn_last VARCHAR(19),
            zakony $text,
            sbirka VARCHAR(20),
            score INTEGER NOT NULL DEFAULT 0,
            reasons $text
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS kroky (
            id_hist INTEGER PRIMARY KEY,
            id_tisk INTEGER NOT NULL,
            ct INTEGER NOT NULL,
            datum VARCHAR(19),
            typ_stavu INTEGER,
            stav VARCHAR(120),
            akce_id INTEGER,
            akce VARCHAR(120)
        )$suffix");
        // hlasování Sněmovny k tiskům (app/Votes.php) a jejich rozdělení podle poslaneckých klubů
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS hlasovani (
            id_hlasovani INTEGER PRIMARY KEY,
            ct INTEGER NOT NULL,
            schuze INTEGER,
            cislo INTEGER,
            datum VARCHAR(16),
            pro INTEGER, proti INTEGER, zdrzel INTEGER, nehlasoval INTEGER, prihlaseno INTEGER, kvorum INTEGER,
            vysledek VARCHAR(1),
            nazev VARCHAR(255),
            krok INTEGER NOT NULL DEFAULT 0
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS hlasovani_kluby (
            id_hlasovani INTEGER NOT NULL,
            klub VARCHAR(40) NOT NULL,
            pro INTEGER, proti INTEGER, zdrzel INTEGER, nepritomen INTEGER
        )$suffix");
        // hlasy jednotlivých poslanců (app/Votes.php): kód hlasu psp.cz (A pro, B/N proti, C zdržel, F nehlasoval,
        // K zdržel/nehlasoval, @ nepřihlášen, M omluven, W před slibem), klub ke dni hlasování
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS hlasovani_poslanci (
            id_hlasovani INTEGER NOT NULL,
            id_osoba INTEGER NOT NULL,
            klub VARCHAR(40),
            hlas VARCHAR(1) NOT NULL,
            PRIMARY KEY (id_hlasovani, id_osoba)
        )$suffix");
        // příprava návrhu u vlády (otevřená data ODok, app/GovPrep.php)
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS priprava (
            ct INTEGER PRIMARY KEY,
            druh VARCHAR(20) NOT NULL,
            pid VARCHAR(20) NOT NULL,
            pripominky_od VARCHAR(10),
            pripominky_do VARCHAR(10),
            dni INTEGER,
            mist INTEGER NOT NULL DEFAULT 0,
            vlada VARCHAR(10),
            duvod $text,
            poznamka $text,
            mimo_plan INTEGER NOT NULL DEFAULT 0,
            vyjimka INTEGER NOT NULL DEFAULT 0,
            znovu INTEGER NOT NULL DEFAULT 0
        )$suffix");
        // připravované zákony u vlády, které ještě nejsou ve Sněmovně, a připravovaná nařízení vlády
        // (otevřená data ODok, app/GovPrep.php); obě tabulky mají stejné sloupce
        foreach (['pripravovane', 'pripravovana_narizeni'] as $prepTable) {
            $this->pdo->exec("CREATE TABLE IF NOT EXISTS $prepTable (
            pid VARCHAR(20) PRIMARY KEY,
            nazev $text,
            resort VARCHAR(255),
            faze VARCHAR(20) NOT NULL,
            stav VARCHAR(80),
            zalozeno VARCHAR(10),
            zmeneno VARCHAR(10),
            pripominky_od VARCHAR(10),
            pripominky_do VARCHAR(10),
            dni INTEGER,
            mist INTEGER NOT NULL DEFAULT 0,
            vlada VARCHAR(10),
            duvod $text,
            popis $text,
            mimo_plan INTEGER NOT NULL DEFAULT 0,
            vyjimka INTEGER NOT NULL DEFAULT 0,
            verzi INTEGER NOT NULL DEFAULT 1
        )$suffix");
        }
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS pn (
            id_dokument INTEGER PRIMARY KEY,
            cislo INTEGER NOT NULL,
            ct INTEGER NOT NULL,
            id_osoba INTEGER,
            osoba VARCHAR(120),
            cas VARCHAR(19)
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS pn_seen (
            id_dokument INTEGER PRIMARY KEY
        )$suffix");
        // které pozměňovací návrhy prošly (app/PnOutcome.php): souhrn PN po 2. čtení (písmena → čísla návrhů)
        // a výsledek každého písemného návrhu ve 3. čtení
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS pn_souhrn (
            ct INTEGER PRIMARY KEY,
            cislo_za INTEGER NOT NULL,
            docx_url VARCHAR(255),
            pismena $text,
            chars INTEGER NOT NULL DEFAULT 0,
            fetched_at VARCHAR(19),
            error VARCHAR(255)
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS pn_vysledek (
            ct INTEGER NOT NULL,
            cislo INTEGER NOT NULL,
            pismena VARCHAR(120),
            stav VARCHAR(20) NOT NULL,
            hlasovani $text,
            stanovisko VARCHAR(40),
            updated_at VARCHAR(19),
            PRIMARY KEY (ct, cislo)
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS alerts (
            alert_key $key PRIMARY KEY,
            kind VARCHAR(20) NOT NULL,
            ct INTEGER NOT NULL,
            event_at VARCHAR(19) NOT NULL,
            created_at VARCHAR(19) NOT NULL,
            title VARCHAR(255) NOT NULL,
            body $text NOT NULL,
            watched INTEGER NOT NULL DEFAULT 0,
            posted INTEGER NOT NULL DEFAULT 0,
            score INTEGER NOT NULL DEFAULT 0
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS pn_text (
            id_dokument INTEGER PRIMARY KEY,
            ct INTEGER NOT NULL,
            cislo INTEGER NOT NULL,
            docx_url VARCHAR(255),
            text $text,
            chars INTEGER NOT NULL DEFAULT 0,
            laws $text,
            signals $text,
            rule_score INTEGER NOT NULL DEFAULT 0,
            ai_json $text,
            ai_score INTEGER,
            fetched_at VARCHAR(19),
            ai_at VARCHAR(19),
            error $text
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS tisk_text (
            ct INTEGER PRIMARY KEY,
            docx_url VARCHAR(255),
            text $text,
            chars INTEGER NOT NULL DEFAULT 0,
            signals $text,
            ai_json $text,
            ai_score INTEGER,
            fetched_at VARCHAR(19),
            ai_at VARCHAR(19),
            error $text
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS post_log (
            alert_key $key NOT NULL,
            network VARCHAR(20) NOT NULL,
            status VARCHAR(10) NOT NULL,
            at VARCHAR(19) NOT NULL,
            error $text
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS meta (
            k $key PRIMARY KEY,
            v $text
        )$suffix");

        // doplnění sloupců do databází založených starší verzí
        $this->addColumn('tisky', 'score', 'INTEGER NOT NULL DEFAULT 0');
        $this->addColumn('tisky', 'vyhlaseno', 'VARCHAR(10)');
        $this->addColumn('tisky', 'lhuta', 'INTEGER');
        $this->addColumn('kroky', 'id_hlasovani', 'INTEGER');
        // souhrn pozměňovacích návrhů po 2. čtení: číslo podtisku (X/n) a čas rozeslání
        $this->addColumn('tisky', 'souhrn_pn', 'INTEGER');
        $this->addColumn('tisky', 'souhrn_pn_at', 'VARCHAR(16)');
        $this->addColumn('priprava', 'znovu', 'INTEGER NOT NULL DEFAULT 0');
        // PID všech verzí připravovaného materiálu (čárkami): upozornění se nesmí opakovat, když přibude nová verze
        $this->addColumn('pripravovane', 'pidy', $text);
        $this->addColumn('pripravovana_narizeni', 'pidy', $text);
        // opakované běhy rychlého modelu u hraničních hodnocení (#7 bod 7): závažnosti jednotlivých běhů (JSON) a jejich počet
        foreach (['tisk_text', 'pn_text'] as $aiTable) {
            $this->addColumn($aiTable, 'ai_runs', 'VARCHAR(60)');
            $this->addColumn($aiTable, 'ai_run_count', 'INTEGER');
        }
        // kdy se naposledy ověřilo, že psp.cz má pořád stejný dokument tisku
        $this->addColumn('tisk_text', 'checked_at', 'VARCHAR(19)');
        $this->addColumn('tisky', 'reasons', $text);
        $this->addColumn('alerts', 'score', 'INTEGER NOT NULL DEFAULT 0');
        // volební období upozornění (čísla tisků se v každém období počítají znovu od 1)
        $this->addColumn('alerts', 'obdobi', 'INTEGER');
        // model, který rozbor naposledy udělal (druhé kolo silnějším modelem se pozná podle něj)
        $this->addColumn('tisk_text', 'ai_model', 'VARCHAR(60)');
        $this->addColumn('pn_text', 'ai_model', 'VARCHAR(60)');
        // verze promptu, podle které rozbor vznikl (NULL = starší než verze 2)
        $this->addColumn('tisk_text', 'ai_prompt', 'INTEGER');
        $this->addColumn('pn_text', 'ai_prompt', 'INTEGER');
        // výsledek 1. kola zůstane uložený i po ověření silnějším modelem (#2 bod 4)
        foreach (['tisk_text', 'pn_text'] as $table) {
            $this->addColumn($table, 'ai_prev_json', $text);
            $this->addColumn($table, 'ai_prev_score', 'INTEGER');
            $this->addColumn($table, 'ai_prev_model', 'VARCHAR(60)');
        }
        if ($this->driver === 'mysql') {
            // starší MySQL databáze mají u dlouhých textů TEXT (64 kB) → MEDIUMTEXT
            foreach (['tisk_text' => ['text', 'ai_json'], 'pn_text' => ['text', 'ai_json']] as $table => $cols) {
                foreach ($this->all("SHOW COLUMNS FROM $table") as $c) {
                    if (in_array($c['Field'], $cols, true) && strtolower((string)$c['Type']) === 'text') {
                        $this->pdo->exec("ALTER TABLE $table MODIFY {$c['Field']} MEDIUMTEXT");
                    }
                }
            }
        }

        // historie verzí textů a rozborů AI (#2 bod 2): záznam přibude jen při skutečné změně (hash),
        // nic se nemaže; obdobi odděluje stejná čísla tisků různých volebních období
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS text_revize (
            druh VARCHAR(10) NOT NULL,
            obdobi INTEGER NOT NULL,
            ref_id INTEGER NOT NULL,
            verze INTEGER NOT NULL,
            ct INTEGER NOT NULL,
            cislo INTEGER,
            docx_url VARCHAR(255),
            text $text,
            chars INTEGER NOT NULL DEFAULT 0,
            hash VARCHAR(40) NOT NULL,
            fetched_at VARCHAR(19),
            created_at VARCHAR(19) NOT NULL,
            zdroj VARCHAR(20) NOT NULL,
            PRIMARY KEY (druh, obdobi, ref_id, verze)
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS ai_revize (
            druh VARCHAR(10) NOT NULL,
            obdobi INTEGER NOT NULL,
            ref_id INTEGER NOT NULL,
            verze INTEGER NOT NULL,
            ct INTEGER NOT NULL,
            ai_json $text,
            ai_score INTEGER,
            ai_model VARCHAR(60),
            ai_prompt INTEGER,
            ai_at VARCHAR(19),
            text_hash VARCHAR(40),
            hash VARCHAR(40) NOT NULL,
            created_at VARCHAR(19) NOT NULL,
            zdroj VARCHAR(20) NOT NULL,
            PRIMARY KEY (druh, obdobi, ref_id, verze)
        )$suffix");
        // archiv pracovních tabulek textů po skončení volebního období (celé řádky včetně signálů a rozborů)
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS tisk_text_archiv (
            obdobi INTEGER NOT NULL,
            archived_at VARCHAR(19) NOT NULL,
            ct INTEGER NOT NULL,
            docx_url VARCHAR(255),
            text $text,
            chars INTEGER,
            signals $text,
            ai_json $text,
            ai_score INTEGER,
            fetched_at VARCHAR(19),
            ai_at VARCHAR(19),
            error $text,
            checked_at VARCHAR(19),
            ai_model VARCHAR(60),
            ai_prompt INTEGER,
            ai_prev_json $text,
            ai_prev_score INTEGER,
            ai_prev_model VARCHAR(60),
            PRIMARY KEY (obdobi, ct)
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS pn_text_archiv (
            obdobi INTEGER NOT NULL,
            archived_at VARCHAR(19) NOT NULL,
            id_dokument INTEGER NOT NULL,
            ct INTEGER NOT NULL,
            cislo INTEGER,
            docx_url VARCHAR(255),
            text $text,
            chars INTEGER,
            laws $text,
            signals $text,
            rule_score INTEGER,
            ai_json $text,
            ai_score INTEGER,
            fetched_at VARCHAR(19),
            ai_at VARCHAR(19),
            error $text,
            ai_model VARCHAR(60),
            ai_prompt INTEGER,
            ai_prev_json $text,
            ai_prev_score INTEGER,
            ai_prev_model VARCHAR(60),
            PRIMARY KEY (obdobi, id_dokument)
        )$suffix");

        // kroky a PN ze stránek historie tisku, které noční otevřená data ještě nemají (app/HistoryHtml.php)
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS html_kroky (
            id_hist INTEGER NOT NULL PRIMARY KEY,
            id_tisk INTEGER NOT NULL,
            ct INTEGER NOT NULL,
            id_prechod INTEGER NOT NULL,
            datum VARCHAR(19),
            seen_at VARCHAR(19) NOT NULL
        )$suffix");
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS html_pn (
            id_dokument INTEGER NOT NULL PRIMARY KEY,
            cislo INTEGER NOT NULL,
            ct INTEGER NOT NULL,
            id_osoba INTEGER,
            osoba VARCHAR(120),
            cas VARCHAR(19),
            seen_at VARCHAR(19) NOT NULL
        )$suffix");
        // historie běhů synchronizace (app/SyncRuns.php): ok NULL = běží, 0 = chyba nebo nedokončeno, 1 = hotovo
        $this->pdo->exec("CREATE TABLE IF NOT EXISTS sync_beh (
            id INTEGER NOT NULL PRIMARY KEY,
            zacatek VARCHAR(19) NOT NULL,
            konec VARCHAR(19),
            ok INTEGER,
            vysledek $text,
            chyba $text
        )$suffix");

        $this->createIndex('kroky', 'idx_kroky_tisk', 'id_tisk');
        $this->createIndex('sync_beh', 'idx_sync_beh_zacatek', 'zacatek');
        $this->createIndex('text_revize', 'idx_text_revize_ct', 'ct');
        $this->createIndex('ai_revize', 'idx_ai_revize_ct', 'ct');
        $this->createIndex('pn_text', 'idx_pn_text_ct', 'ct');
        $this->createIndex('pn', 'idx_pn_ct', 'ct');
        $this->createIndex('alerts', 'idx_alerts_event', 'event_at');
        $this->createIndex('tisky', 'idx_tisky_ct', 'ct');
        $this->createIndex('hlasovani', 'idx_hlasovani_ct', 'ct');
        $this->createIndex('hlasovani_kluby', 'idx_hlasovani_kluby', 'id_hlasovani');
    }

    private function addColumn(string $table, string $column, string $definition): void
    {
        // ALTER TABLE jen pokud sloupec chybí (funguje v SQLite i MySQL)
        $columns = match ($this->driver) {
            'sqlite' => array_column($this->all("PRAGMA table_info($table)"), 'name'),
            'pgsql' => array_column($this->all(
                'SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = ?',
                [$table]
            ), 'column_name'),
            default => array_column($this->all("SHOW COLUMNS FROM $table"), 'Field'),
        };
        if (!in_array($column, $columns, true)) {
            $this->pdo->exec("ALTER TABLE $table ADD COLUMN $column $definition");
        }
    }

    private function createIndex(string $table, string $name, string $column): void
    {
        // MySQL nezná CREATE INDEX IF NOT EXISTS, proto chybu „už existuje“ ignorujeme
        try {
            $sql = $this->driver !== 'mysql'
                ? "CREATE INDEX IF NOT EXISTS $name ON $table ($column)"
                : "CREATE INDEX $name ON $table ($column)";
            $this->pdo->exec($sql);
        } catch (\PDOException) {
        }
    }

    /** Operátor pro hledání bez ohledu na velikost písmen (PostgreSQL má LIKE citlivé). */
    public function ilike(): string
    {
        return $this->driver === 'pgsql' ? 'ILIKE' : 'LIKE';
    }

    /** Převod čísla na text pro porovnání s řetězcem (CAST AS CHAR je v PostgreSQL char(1)). */
    public function castText(string $expr): string
    {
        return $this->driver === 'mysql' ? "CAST($expr AS CHAR)" : "CAST($expr AS VARCHAR)";
    }

    public function run(string $sql, array $params = []): PDOStatement
    {
        $stmt = $this->pdo->prepare($sql);
        $stmt->execute($params);
        return $stmt;
    }

    public function all(string $sql, array $params = []): array
    {
        return $this->run($sql, $params)->fetchAll();
    }

    public function one(string $sql, array $params = []): ?array
    {
        $row = $this->run($sql, $params)->fetch();
        return $row === false ? null : $row;
    }

    public function value(string $sql, array $params = []): mixed
    {
        $value = $this->run($sql, $params)->fetchColumn();
        return $value === false ? null : $value;
    }

    public function insertMany(string $table, array $columns, array $rows): void
    {
        // dávkové vkládání v transakci volajícího, po 200 řádcích kvůli limitům parametrů
        if ($rows === []) {
            return;
        }
        $placeholders = '(' . implode(',', array_fill(0, count($columns), '?')) . ')';
        foreach (array_chunk($rows, 200) as $chunk) {
            $sql = "INSERT INTO $table (" . implode(',', $columns) . ') VALUES '
                . implode(',', array_fill(0, count($chunk), $placeholders));
            $params = [];
            foreach ($chunk as $row) {
                foreach ($columns as $column) {
                    $params[] = $row[$column] ?? null;
                }
            }
            $this->run($sql, $params);
        }
    }

    public function getMeta(string $key, ?string $default = null): ?string
    {
        $value = $this->value('SELECT v FROM meta WHERE k = ?', [$key]);
        return $value === null ? $default : (string)$value;
    }

    public function setMeta(string $key, string $value): void
    {
        $this->run('DELETE FROM meta WHERE k = ?', [$key]);
        $this->run('INSERT INTO meta (k, v) VALUES (?, ?)', [$key, $value]);
    }
}
