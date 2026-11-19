<?php

declare(strict_types=1);

namespace App\Models;

final class ArchivedBillText extends AppendOnlyRecord
{
    protected $table = 'tisk_text_archiv';
    protected $fillable = ['obdobi', 'archived_at', 'ct', 'docx_url', 'text', 'chars', 'signals', 'ai_json', 'ai_score', 'fetched_at', 'ai_at', 'error', 'checked_at', 'ai_model', 'ai_prompt', 'ai_prev_json', 'ai_prev_score', 'ai_prev_model'];

    protected function casts(): array
    {
        return [
            'obdobi' => 'integer',
            'ct' => 'integer',
            'chars' => 'integer',
            'signals' => 'array',
            'ai_json' => 'array',
            'ai_score' => 'integer',
            'ai_prompt' => 'integer',
            'ai_prev_json' => 'array',
            'ai_prev_score' => 'integer',
        ];
    }
}
