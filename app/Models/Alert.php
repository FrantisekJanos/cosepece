<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\HasMany;

final class Alert extends Model
{
    protected $table = 'alerts';
    protected $primaryKey = 'alert_key';
    protected $keyType = 'string';
    public $incrementing = false;
    public $timestamps = false;

    protected $fillable = [
        'alert_key', 'kind', 'ct', 'event_at', 'created_at', 'title',
        'body', 'watched', 'posted', 'score', 'obdobi',
    ];

    protected function casts(): array
    {
        return [
            'ct' => 'integer',
            'obdobi' => 'integer',
            'event_at' => 'immutable_datetime',
            'created_at' => 'immutable_datetime',
            'watched' => 'boolean',
            'posted' => 'integer',
            'score' => 'integer',
        ];
    }

    public function scopeCurrentPeriod(\Illuminate\Database\Eloquent\Builder $query): \Illuminate\Database\Eloquent\Builder
    {
        $period = (int)(Meta::query()->whereKey('obdobi_cislo')->value('v') ?? 0);
        return $query->where(fn ($periodQuery) => $periodQuery->whereNull('obdobi')->orWhere('obdobi', $period));
    }

    public function bill(): BelongsTo
    {
        return $this->belongsTo(Bill::class, 'ct', 'ct');
    }

    public function postLogs(): HasMany
    {
        return $this->hasMany(PostLog::class, 'alert_key', 'alert_key');
    }
}
