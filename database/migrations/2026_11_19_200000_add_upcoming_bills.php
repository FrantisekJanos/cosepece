<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        if (!Schema::hasTable('pripravovane')) {
            Schema::create('pripravovane', function (Blueprint $table): void {
                $table->string('pid', 20)->primary();
                $table->mediumText('nazev')->nullable();
                $table->string('resort', 255)->nullable();
                $table->string('faze', 20);
                $table->string('stav', 80)->nullable();
                foreach (['zalozeno', 'zmeneno', 'pripominky_od', 'pripominky_do', 'vlada'] as $column) {
                    $table->string($column, 10)->nullable();
                }
                $table->integer('dni')->nullable();
                $table->integer('mist')->default(0);
                $table->mediumText('duvod')->nullable();
                $table->mediumText('popis')->nullable();
                $table->integer('mimo_plan')->default(0);
                $table->integer('vyjimka')->default(0);
                $table->integer('verzi')->default(1);
            });
        }
    }

    public function down(): void
    {
        throw new RuntimeException('Převzaté připravované návrhy se nemažou. Obnovte databázi ze zálohy.');
    }
};
