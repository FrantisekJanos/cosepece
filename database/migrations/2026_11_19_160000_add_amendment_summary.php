<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        if (!Schema::hasColumn('tisky', 'souhrn_pn')) {
            Schema::table('tisky', fn (Blueprint $table) => $table->integer('souhrn_pn')->nullable());
        }
        if (!Schema::hasColumn('tisky', 'souhrn_pn_at')) {
            Schema::table('tisky', fn (Blueprint $table) => $table->string('souhrn_pn_at', 16)->nullable());
        }
    }

    public function down(): void
    {
        throw new RuntimeException('Převzaté údaje souhrnu PN se nemažou. Obnovte databázi ze zálohy.');
    }
};
