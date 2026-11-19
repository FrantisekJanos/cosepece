<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

final class AmendmentText extends Model
{
    protected $table = 'pn_text';
    protected $primaryKey = 'id_dokument';
    public $incrementing = false;
    public $timestamps = false;

    protected $fillable = [
        'id_dokument', 'ct', 'cislo', 'docx_url', 'text', 'chars',
        'laws', 'signals', 'rule_score', 'ai_json', 'ai_score', 'fetched_at',
        'ai_at', 'error', 'ai_model', 'ai_prompt', 'ai_prev_json', 'ai_prev_score', 'ai_prev_model', 'ai_runs', 'ai_run_count',
    ];

    protected function casts(): array
    {
        return [
            'id_dokument' => 'integer',
            'ct' => 'integer',
            'cislo' => 'integer',
            'chars' => 'integer',
            'signals' => 'array',
            'rule_score' => 'integer',
            'ai_json' => 'array',
            'ai_score' => 'integer',
            'ai_runs' => 'array', 'ai_run_count' => 'integer',
            'fetched_at' => 'immutable_datetime',
            'ai_at' => 'immutable_datetime',
            'ai_prompt' => 'integer',
            'ai_prev_json' => 'array',
            'ai_prev_score' => 'integer',
        ];
    }

    public function amendment(): BelongsTo
    {
        return $this->belongsTo(Amendment::class, 'id_dokument', 'id_dokument');
    }

    public function bill(): BelongsTo
    {
        return $this->belongsTo(Bill::class, 'ct', 'ct');
    }
}
