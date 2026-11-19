<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

final class BillText extends Model
{
    protected $table = 'tisk_text';
    protected $primaryKey = 'ct';
    public $incrementing = false;
    public $timestamps = false;

    protected $fillable = [
        'ct', 'docx_url', 'text', 'chars', 'signals', 'ai_json',
        'ai_score', 'fetched_at', 'ai_at', 'error', 'ai_model', 'ai_prompt', 'ai_prev_json', 'ai_prev_score', 'ai_prev_model', 'checked_at',
    ];

    protected function casts(): array
    {
        return [
            'ct' => 'integer',
            'chars' => 'integer',
            'signals' => 'array',
            'ai_json' => 'array',
            'ai_score' => 'integer',
            'fetched_at' => 'immutable_datetime',
            'ai_at' => 'immutable_datetime',
            'checked_at' => 'immutable_datetime',
            'ai_prompt' => 'integer',
            'ai_prev_json' => 'array',
            'ai_prev_score' => 'integer',
        ];
    }

    public function bill(): BelongsTo
    {
        return $this->belongsTo(Bill::class, 'ct', 'ct');
    }
}
