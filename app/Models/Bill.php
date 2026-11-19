<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Builder;
use Illuminate\Database\Eloquent\Relations\HasMany;
use Illuminate\Database\Eloquent\Relations\HasOne;

final class Bill extends Model
{
    protected $table = 'tisky';
    protected $primaryKey = 'id_tisk';
    public $incrementing = false;
    public $timestamps = false;

    protected $fillable = [
        'id_tisk', 'ct', 'druh', 'typ_navrhu', 'navrhovatel', 'nazev',
        'uplny_nazev', 'predlozeno', 'faze', 'posledni', 'p90', 'p90ok',
        'zl', 'pn_count', 'pn_last', 'zakony', 'sbirka', 'score',
        'reasons', 'vyhlaseno', 'lhuta',
    ];

    protected function casts(): array
    {
        return [
            'id_tisk' => 'integer',
            'ct' => 'integer',
            'lhuta' => 'integer',
            'druh' => 'integer',
            'p90' => 'boolean',
            'p90ok' => 'boolean',
            'zl' => 'boolean',
            'pn_count' => 'integer',
            'score' => 'integer',
            'reasons' => 'array',
            'predlozeno' => 'immutable_datetime',
            'posledni' => 'immutable_datetime',
            'pn_last' => 'immutable_datetime',
            'vyhlaseno' => 'immutable_date',
        ];
    }

    public function votes(): HasMany
    {
        return $this->hasMany(Vote::class, 'ct', 'ct');
    }

    public function steps(): HasMany
    {
        return $this->hasMany(BillStep::class, 'id_tisk', 'id_tisk');
    }

    public function amendments(): HasMany
    {
        return $this->hasMany(Amendment::class, 'ct', 'ct');
    }

    public function text(): HasOne
    {
        return $this->hasOne(BillText::class, 'ct', 'ct');
    }

    public function amendmentTexts(): HasMany
    {
        return $this->hasMany(AmendmentText::class, 'ct', 'ct');
    }

    public function alerts(): HasMany
    {
        return $this->hasMany(Alert::class, 'ct', 'ct')->currentPeriod();
    }
    public function scopeProcedureFilter(Builder $query, string $filter): Builder
    {
        return match ($filter) {
            'mp' => $query->where('typ_navrhu', '<>', 'vládní'),
            'p90' => $query->where('p90', 1),
            'cut' => $query->where('zl', 1),
            'surge' => $query->whereIn('ct', Alert::query()->select('ct')->where('kind', 'surge')->currentPeriod()),
            'flagged' => $query->where(fn (Builder $flags) => $flags->where('typ_navrhu', '<>', 'vládní')
                ->orWhere('p90', 1)->orWhere('zl', 1)->orWhere('pn_count', '>', 0)),
            default => $query,
        };
    }

    public function scopeRanked(Builder $query, string $sort): Builder
    {
        return $sort === 'recent'
            ? $query->orderByDesc('posledni')->orderByDesc('ct')
            : $query->orderByDesc('score')->orderByDesc('posledni')->orderByDesc('ct');
    }

    public function scopeActiveFindings(Builder $query, int $minimumScore, array $strongAiNumbers = []): Builder
    {
        $since = date('Y-m-d', strtotime('-120 days'));
        return $query->where(fn (Builder $eligible) => $eligible->where('score', '>=', $minimumScore)->orWhereIn('ct', $strongAiNumbers))
            ->where(fn (Builder $active) => $active
                ->whereNotIn('faze', ['Vyhlášeno', 'Vzat zpět', 'Zamítnuto', 'Vráceno předkladateli'])->orWhere('posledni', '>=', $since));
    }
}
