<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Database\Eloquent\Builder;
use LogicException;

final class PostLog extends Model
{
    protected $table = 'post_log';
    protected $primaryKey = null;
    public $incrementing = false;
    public $timestamps = false;

    protected $fillable = [
        'alert_key', 'network', 'status', 'at', 'error',
    ];

    protected function casts(): array
    {
        return [
            'at' => 'immutable_datetime',
        ];
    }

    public function alert(): BelongsTo
    {
        return $this->belongsTo(Alert::class, 'alert_key', 'alert_key');
    }

    protected function performUpdate(Builder $query)
    {
        throw new LogicException('Historie publikování je pouze pro přidávání záznamů.');
    }

    public function delete()
    {
        throw new LogicException('Záznam historie bez primárního klíče nelze jednotlivě smazat.');
    }
}
