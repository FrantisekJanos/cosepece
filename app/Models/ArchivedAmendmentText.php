<?php

declare(strict_types=1);

namespace App\Models;

final class ArchivedAmendmentText extends AppendOnlyRecord
{
    protected $table = 'pn_text_archiv';
    protected $fillable = ['obdobi', 'archived_at', 'id_dokument', 'ct', 'cislo', 'docx_url', 'text', 'chars', 'laws', 'signals', 'rule_score', 'ai_json', 'ai_score', 'fetched_at', 'ai_at', 'error', 'ai_model', 'ai_prompt', 'ai_prev_json', 'ai_prev_score', 'ai_prev_model', 'ai_runs', 'ai_run_count'];

    protected function casts(): array
    {
        return [
            'obdobi' => 'integer',
            'id_dokument' => 'integer',
            'ct' => 'integer',
            'cislo' => 'integer',
            'chars' => 'integer',
            'signals' => 'array',
            'rule_score' => 'integer',
            'ai_json' => 'array',
            'ai_score' => 'integer',
            'ai_runs' => 'array', 'ai_run_count' => 'integer',
            'ai_prompt' => 'integer',
            'ai_prev_json' => 'array',
            'ai_prev_score' => 'integer',
        ];
    }
}
