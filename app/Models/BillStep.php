<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

final class BillStep extends Model
{
    protected $table = 'kroky';
    protected $primaryKey = 'id_hist';
    public $incrementing = false;
    public $timestamps = false;

    protected $fillable = [
        'id_hist', 'id_tisk', 'ct', 'datum', 'typ_stavu', 'stav',
        'akce_id', 'akce',
    ];

    protected function casts(): array
    {
        return [
            'id_hist' => 'integer',
            'id_tisk' => 'integer',
            'ct' => 'integer',
            'datum' => 'immutable_datetime',
            'typ_stavu' => 'integer',
            'akce_id' => 'integer',
        ];
    }

    public function bill(): BelongsTo
    {
        return $this->belongsTo(Bill::class, 'id_tisk', 'id_tisk');
    }
}
