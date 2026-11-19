<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        foreach (['tisk_text', 'pn_text', 'tisk_text_archiv', 'pn_text_archiv'] as $name) {
            if (!Schema::hasColumn($name, 'ai_runs')) {
                Schema::table($name, fn (Blueprint $table) => $table->string('ai_runs', 60)->nullable());
            }
            if (!Schema::hasColumn($name, 'ai_run_count')) {
                Schema::table($name, fn (Blueprint $table) => $table->integer('ai_run_count')->nullable());
            }
        }
    }

    public function down(): void
    {
        throw new RuntimeException('Historie opakovaných rozborů se nemaže. Obnovte databázi ze zálohy.');
    }
};
