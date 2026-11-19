<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        if (!Schema::hasTable('tisky')) {
            Schema::create('tisky', function (Blueprint $table): void {
                $table->integer('id_tisk')->primary();
                $table->integer('ct');
                $table->integer('druh');
                $table->string('typ_navrhu', 20);
                $table->string('navrhovatel', 255)->nullable();
                $table->string('nazev', 255)->nullable();
                $table->text('uplny_nazev')->nullable();
                $table->string('predlozeno', 19)->nullable();
                $table->string('faze', 60)->nullable();
                $table->string('posledni', 19)->nullable();
                $table->integer('p90')->default(0);
                $table->integer('p90ok')->default(0);
                $table->integer('zl')->default(0);
                $table->integer('pn_count')->default(0);
                $table->string('pn_last', 19)->nullable();
                $table->text('zakony')->nullable();
                $table->string('sbirka', 20)->nullable();
                $table->integer('score')->default(0);
                $table->text('reasons')->nullable();
            });
        }
        if (!Schema::hasTable('kroky')) {
            Schema::create('kroky', function (Blueprint $table): void {
                $table->integer('id_hist')->primary();
                $table->integer('id_tisk');
                $table->integer('ct');
                $table->string('datum', 19)->nullable();
                $table->integer('typ_stavu')->nullable();
                $table->string('stav', 120)->nullable();
                $table->integer('akce_id')->nullable();
                $table->string('akce', 120)->nullable();
            });
        }
        if (!Schema::hasTable('pn')) {
            Schema::create('pn', function (Blueprint $table): void {
                $table->integer('id_dokument')->primary();
                $table->integer('cislo');
                $table->integer('ct');
                $table->integer('id_osoba')->nullable();
                $table->string('osoba', 120)->nullable();
                $table->string('cas', 19)->nullable();
            });
        }
        if (!Schema::hasTable('pn_seen')) {
            Schema::create('pn_seen', function (Blueprint $table): void {
                $table->integer('id_dokument')->primary();
            });
        }
        if (!Schema::hasTable('alerts')) {
            Schema::create('alerts', function (Blueprint $table): void {
                $table->string('alert_key', 190)->primary();
                $table->string('kind', 20);
                $table->integer('ct');
                $table->string('event_at', 19);
                $table->string('created_at', 19);
                $table->string('title', 255);
                $table->text('body');
                $table->integer('watched')->default(0);
                $table->integer('posted')->default(0);
                $table->integer('score')->default(0);
            });
        }
        if (!Schema::hasTable('pn_text')) {
            Schema::create('pn_text', function (Blueprint $table): void {
                $table->integer('id_dokument')->primary();
                $table->integer('ct');
                $table->integer('cislo');
                $table->string('docx_url', 255)->nullable();
                $table->text('text')->nullable();
                $table->integer('chars')->default(0);
                $table->text('laws')->nullable();
                $table->text('signals')->nullable();
                $table->integer('rule_score')->default(0);
                $table->text('ai_json')->nullable();
                $table->integer('ai_score')->nullable();
                $table->string('fetched_at', 19)->nullable();
                $table->string('ai_at', 19)->nullable();
                $table->text('error')->nullable();
            });
        }
        if (!Schema::hasTable('tisk_text')) {
            Schema::create('tisk_text', function (Blueprint $table): void {
                $table->integer('ct')->primary();
                $table->string('docx_url', 255)->nullable();
                $table->text('text')->nullable();
                $table->integer('chars')->default(0);
                $table->text('signals')->nullable();
                $table->text('ai_json')->nullable();
                $table->integer('ai_score')->nullable();
                $table->string('fetched_at', 19)->nullable();
                $table->string('ai_at', 19)->nullable();
                $table->text('error')->nullable();
            });
        }
        if (!Schema::hasTable('post_log')) {
            Schema::create('post_log', function (Blueprint $table): void {
                $table->string('alert_key', 190);
                $table->string('network', 20);
                $table->string('status', 10);
                $table->string('at', 19);
                $table->text('error')->nullable();
            });
        }
        if (!Schema::hasTable('meta')) {
            Schema::create('meta', function (Blueprint $table): void {
                $table->string('k', 190)->primary();
                $table->text('v')->nullable();
            });
        }
        if (!Schema::hasColumn('tisky', 'score')) {
            Schema::table('tisky', function (Blueprint $table): void {
                $table->integer('score')->default(0);
            });
        }
        if (!Schema::hasColumn('tisky', 'vyhlaseno')) {
            Schema::table('tisky', function (Blueprint $table): void {
                $table->string('vyhlaseno', 10)->nullable();
            });
        }
        if (!Schema::hasColumn('tisky', 'reasons')) {
            Schema::table('tisky', function (Blueprint $table): void {
                $table->text('reasons')->nullable();
            });
        }
        if (!Schema::hasColumn('alerts', 'score')) {
            Schema::table('alerts', function (Blueprint $table): void {
                $table->integer('score')->default(0);
            });
        }
        if (!Schema::hasColumn('tisk_text', 'ai_model')) {
            Schema::table('tisk_text', function (Blueprint $table): void {
                $table->string('ai_model', 60)->nullable();
            });
        }
        if (!Schema::hasColumn('pn_text', 'ai_model')) {
            Schema::table('pn_text', function (Blueprint $table): void {
                $table->string('ai_model', 60)->nullable();
            });
        }
        if (!Schema::hasColumn('tisk_text', 'ai_prompt')) {
            Schema::table('tisk_text', function (Blueprint $table): void {
                $table->integer('ai_prompt')->nullable();
            });
        }
        if (!Schema::hasColumn('pn_text', 'ai_prompt')) {
            Schema::table('pn_text', function (Blueprint $table): void {
                $table->integer('ai_prompt')->nullable();
            });
        }
        if (!Schema::hasIndex('kroky', 'idx_kroky_tisk')) {
            Schema::table('kroky', function (Blueprint $table): void {
                $table->index('id_tisk', 'idx_kroky_tisk');
            });
        }
        if (!Schema::hasIndex('pn_text', 'idx_pn_text_ct')) {
            Schema::table('pn_text', function (Blueprint $table): void {
                $table->index('ct', 'idx_pn_text_ct');
            });
        }
        if (!Schema::hasIndex('pn', 'idx_pn_ct')) {
            Schema::table('pn', function (Blueprint $table): void {
                $table->index('ct', 'idx_pn_ct');
            });
        }
        if (!Schema::hasIndex('alerts', 'idx_alerts_event')) {
            Schema::table('alerts', function (Blueprint $table): void {
                $table->index('event_at', 'idx_alerts_event');
            });
        }
        if (!Schema::hasIndex('tisky', 'idx_tisky_ct')) {
            Schema::table('tisky', function (Blueprint $table): void {
                $table->index('ct', 'idx_tisky_ct');
            });
        }
    }

    public function down(): void
    {

        throw new RuntimeException('Základní schéma je nevratná migrace. Obnovte databázi ze zálohy.');
    }
};
