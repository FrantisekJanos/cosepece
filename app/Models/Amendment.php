<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\HasOne;

final class Amendment extends Model
{
    protected $table = 'pn';
    protected $primaryKey = 'id_dokument';
    public $incrementing = false;
    public $timestamps = false;

    protected $fillable = [
        'id_dokument', 'cislo', 'ct', 'id_osoba', 'osoba', 'cas',
    ];

    protected function casts(): array
    {
        return [
            'id_dokument' => 'integer',
            'cislo' => 'integer',
            'ct' => 'integer',
            'id_osoba' => 'integer',
            'cas' => 'immutable_datetime',
        ];
    }

    public function bill(): BelongsTo
    {
        return $this->belongsTo(Bill::class, 'ct', 'ct');
    }

    public function text(): HasOne
    {
        return $this->hasOne(AmendmentText::class, 'id_dokument', 'id_dokument');
    }

    public function seen(): HasOne
    {
        return $this->hasOne(SeenAmendment::class, 'id_dokument', 'id_dokument');
    }
}
