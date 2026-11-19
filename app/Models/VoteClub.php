<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Builder;
use LogicException;

final class VoteClub extends Model
{
    protected $table = 'hlasovani_kluby';
    protected $primaryKey = null;
    public $incrementing = false;
    public $timestamps = false;

    protected $fillable = ['id_hlasovani', 'klub', 'pro', 'proti', 'zdrzel', 'nepritomen'];

    protected function casts(): array
    {
        return array_fill_keys(['id_hlasovani', 'pro', 'proti', 'zdrzel', 'nepritomen'], 'integer');
    }

    public function vote(): BelongsTo
    {
        return $this->belongsTo(Vote::class, 'id_hlasovani', 'id_hlasovani');
    }

    protected function performUpdate(Builder $query)
    {
        throw new LogicException('Souhrn klubu bez primárního klíče nelze jednotlivě aktualizovat.');
    }

    public function delete()
    {
        throw new LogicException('Souhrn klubu bez primárního klíče nelze jednotlivě smazat.');
    }
}
