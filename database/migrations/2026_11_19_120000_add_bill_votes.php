<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        if (!Schema::hasTable('hlasovani')) {
            Schema::create('hlasovani', function (Blueprint $table): void {
                $table->integer('id_hlasovani')->primary();
                $table->integer('ct');
                $table->integer('schuze')->nullable();
                $table->integer('cislo')->nullable();
                $table->string('datum', 16)->nullable();
                foreach (['pro', 'proti', 'zdrzel', 'nehlasoval', 'prihlaseno', 'kvorum'] as $column) {
                    $table->integer($column)->nullable();
                }
                $table->string('vysledek', 1)->nullable();
                $table->string('nazev', 255)->nullable();
                $table->integer('krok')->default(0);
            });
        }
        if (!Schema::hasTable('hlasovani_kluby')) {
            Schema::create('hlasovani_kluby', function (Blueprint $table): void {
                $table->integer('id_hlasovani');
                $table->string('klub', 40);
                foreach (['pro', 'proti', 'zdrzel', 'nepritomen'] as $column) {
                    $table->integer($column)->nullable();
                }
            });
        }
        if (!Schema::hasColumn('kroky', 'id_hlasovani')) {
            Schema::table('kroky', function (Blueprint $table): void {
                $table->integer('id_hlasovani')->nullable();
            });
        }
        foreach (['hlasovani' => ['idx_hlasovani_ct', 'ct'], 'hlasovani_kluby' => ['idx_hlasovani_kluby', 'id_hlasovani']] as $name => [$index, $column]) {
            if (!Schema::hasIndex($name, $index)) {
                Schema::table($name, fn (Blueprint $table) => $table->index($column, $index));
            }
        }
    }

    public function down(): void
    {
        throw new RuntimeException('Migrace přebírá hlasování i z existujících databází. Obnovte databázi ze zálohy.');
    }
};
