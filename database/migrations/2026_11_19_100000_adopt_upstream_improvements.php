<?php

declare(strict_types=1);

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        $columns = [
            'tisky' => ['lhuta' => 'integer'],
            'alerts' => ['obdobi' => 'integer'],
            'tisk_text' => ['checked_at' => 'date', 'ai_prev_json' => 'text', 'ai_prev_score' => 'integer', 'ai_prev_model' => 'model'],
            'pn_text' => ['ai_prev_json' => 'text', 'ai_prev_score' => 'integer', 'ai_prev_model' => 'model'],
        ];
        foreach ($columns as $name => $fields) {
            foreach ($fields as $column => $type) {
                if (!Schema::hasColumn($name, $column)) {
                    Schema::table($name, function (Blueprint $table) use ($column, $type): void {
                        match ($type) {
                            'integer' => $table->integer($column)->nullable(),
                            'text' => $table->mediumText($column)->nullable(),
                            'model' => $table->string($column, 60)->nullable(),
                            'date' => $table->string($column, 19)->nullable(),
                        };
                    });
                }
            }
        }
        if (Schema::getConnection()->getDriverName() === 'mysql') {
            foreach (['tisk_text', 'pn_text'] as $name) {
                Schema::table($name, function (Blueprint $table): void {
                    $table->mediumText('text')->nullable()->change();
                    $table->mediumText('ai_json')->nullable()->change();
                });
            }
        }
    }

    public function down(): void
    {
        throw new RuntimeException('Migrace uchovává historii AI a volebních období. Obnovte databázi ze zálohy.');
    }
};
