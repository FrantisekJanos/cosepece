<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Relations\HasMany;

final class Vote extends Model
{
    protected $table = 'hlasovani';
    protected $primaryKey = 'id_hlasovani';
    public $incrementing = false;
    public $timestamps = false;
    protected $fillable = ['id_hlasovani', 'ct', 'schuze', 'cislo', 'datum', 'pro', 'proti', 'zdrzel', 'nehlasoval', 'prihlaseno', 'kvorum', 'vysledek', 'nazev', 'krok'];

    protected function casts(): array
    {
        return array_fill_keys(['id_hlasovani', 'ct', 'schuze', 'cislo', 'pro', 'proti', 'zdrzel', 'nehlasoval', 'prihlaseno', 'kvorum', 'krok'], 'integer')
            + ['datum' => 'immutable_datetime'];
    }

    public function bill(): BelongsTo
    {
        return $this->belongsTo(Bill::class, 'ct', 'ct');
    }

    public function clubs(): HasMany
    {
        return $this->hasMany(VoteClub::class, 'id_hlasovani', 'id_hlasovani');
    }
}
