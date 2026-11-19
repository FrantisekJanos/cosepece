<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        if (!Schema::hasTable('priprava')) {
            Schema::create('priprava', function (Blueprint $table): void {
                $table->integer('ct')->primary();
                $table->string('druh', 20);
                $table->string('pid', 20);
                $table->string('pripominky_od', 10)->nullable();
                $table->string('pripominky_do', 10)->nullable();
                $table->integer('dni')->nullable();
                $table->integer('mist')->default(0);
                $table->string('vlada', 10)->nullable();
                $table->mediumText('duvod')->nullable();
                $table->mediumText('poznamka')->nullable();
                $table->integer('mimo_plan')->default(0);
                $table->integer('vyjimka')->default(0);
                $table->integer('znovu')->default(0);
            });
        }
        if (!Schema::hasColumn('priprava', 'znovu')) {
            Schema::table('priprava', fn (Blueprint $table) => $table->integer('znovu')->default(0));
        }
    }

    public function down(): void
    {
        throw new RuntimeException('Převzatá data přípravy návrhů se nemažou. Obnovte databázi ze zálohy.');
    }
};
