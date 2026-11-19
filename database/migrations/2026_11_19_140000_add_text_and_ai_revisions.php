<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        if (!Schema::hasTable('text_revize')) {
            Schema::create('text_revize', function (Blueprint $table): void {
                $table->string('druh', 10);
                $table->integer('obdobi');
                $table->integer('ref_id');
                $table->integer('verze');
                $table->integer('ct');
                $table->integer('cislo')->nullable();
                $table->string('docx_url', 255)->nullable();
                $table->mediumText('text')->nullable();
                $table->integer('chars')->default(0);
                $table->string('hash', 40);
                $table->string('fetched_at', 19)->nullable();
                $table->string('created_at', 19);
                $table->string('zdroj', 20);
                $table->primary(['druh', 'obdobi', 'ref_id', 'verze']);
                $table->index('ct', 'idx_text_revize_ct');
            });
        }
        if (!Schema::hasTable('ai_revize')) {
            Schema::create('ai_revize', function (Blueprint $table): void {
                $table->string('druh', 10);
                $table->integer('obdobi');
                $table->integer('ref_id');
                $table->integer('verze');
                $table->integer('ct');
                $table->mediumText('ai_json')->nullable();
                $table->integer('ai_score')->nullable();
                $table->string('ai_model', 60)->nullable();
                $table->integer('ai_prompt')->nullable();
                $table->string('ai_at', 19)->nullable();
                $table->string('text_hash', 40)->nullable();
                $table->string('hash', 40);
                $table->string('created_at', 19);
                $table->string('zdroj', 20);
                $table->primary(['druh', 'obdobi', 'ref_id', 'verze']);
                $table->index('ct', 'idx_ai_revize_ct');
            });
        }
        if (!Schema::hasTable('tisk_text_archiv')) {
            Schema::create('tisk_text_archiv', function (Blueprint $table): void {
                $table->integer('obdobi');
                $table->string('archived_at', 19);
                $table->integer('ct');
                $table->string('docx_url', 255)->nullable();
                $table->mediumText('text')->nullable();
                $table->integer('chars')->nullable();
                $table->mediumText('signals')->nullable();
                $table->mediumText('ai_json')->nullable();
                $table->integer('ai_score')->nullable();
                $table->string('fetched_at', 19)->nullable();
                $table->string('ai_at', 19)->nullable();
                $table->mediumText('error')->nullable();
                $table->string('checked_at', 19)->nullable();
                $table->string('ai_model', 60)->nullable();
                $table->integer('ai_prompt')->nullable();
                $table->mediumText('ai_prev_json')->nullable();
                $table->integer('ai_prev_score')->nullable();
                $table->string('ai_prev_model', 60)->nullable();
                $table->primary(['obdobi', 'ct']);
            });
        }
        if (!Schema::hasTable('pn_text_archiv')) {
            Schema::create('pn_text_archiv', function (Blueprint $table): void {
                $table->integer('obdobi');
                $table->string('archived_at', 19);
                $table->integer('id_dokument');
                $table->integer('ct');
                $table->integer('cislo')->nullable();
                $table->string('docx_url', 255)->nullable();
                $table->mediumText('text')->nullable();
                $table->integer('chars')->nullable();
                $table->mediumText('laws')->nullable();
                $table->mediumText('signals')->nullable();
                $table->integer('rule_score')->nullable();
                $table->mediumText('ai_json')->nullable();
                $table->integer('ai_score')->nullable();
                $table->string('fetched_at', 19)->nullable();
                $table->string('ai_at', 19)->nullable();
                $table->mediumText('error')->nullable();
                $table->string('ai_model', 60)->nullable();
                $table->integer('ai_prompt')->nullable();
                $table->mediumText('ai_prev_json')->nullable();
                $table->integer('ai_prev_score')->nullable();
                $table->string('ai_prev_model', 60)->nullable();
                $table->primary(['obdobi', 'id_dokument']);
            });
        }
    }

    public function down(): void
    {
        throw new RuntimeException('Historie a archiv textů se při rollbacku nemažou. Obnovte databázi ze zálohy.');
    }
};
