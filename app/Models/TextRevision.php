<?php

declare(strict_types=1);

namespace App\Models;

final class TextRevision extends AppendOnlyRecord
{
    protected $table = 'text_revize';
    protected $fillable = ['druh', 'obdobi', 'ref_id', 'verze', 'ct', 'cislo', 'docx_url', 'text', 'chars', 'hash', 'fetched_at', 'created_at', 'zdroj'];

    protected function casts(): array
    {
        return [
            'obdobi' => 'integer',
            'ref_id' => 'integer',
            'verze' => 'integer',
            'ct' => 'integer',
            'cislo' => 'integer',
            'chars' => 'integer',
        ];
    }
}
