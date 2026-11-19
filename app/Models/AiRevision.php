<?php

declare(strict_types=1);

namespace App\Models;

final class AiRevision extends AppendOnlyRecord
{
    protected $table = 'ai_revize';
    protected $fillable = ['druh', 'obdobi', 'ref_id', 'verze', 'ct', 'ai_json', 'ai_score', 'ai_model', 'ai_prompt', 'ai_at', 'text_hash', 'hash', 'created_at', 'zdroj'];

    protected function casts(): array
    {
        return [
            'obdobi' => 'integer',
            'ref_id' => 'integer',
            'verze' => 'integer',
            'ct' => 'integer',
            'ai_json' => 'array',
            'ai_score' => 'integer',
            'ai_prompt' => 'integer',
        ];
    }
}
