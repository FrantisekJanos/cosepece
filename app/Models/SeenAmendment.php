<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

final class SeenAmendment extends Model
{
    protected $table = 'pn_seen';
    protected $primaryKey = 'id_dokument';
    public $incrementing = false;
    public $timestamps = false;

    protected $fillable = [
        'id_dokument',
    ];

    protected function casts(): array
    {
        return [
            'id_dokument' => 'integer',
        ];
    }

    public function amendment(): BelongsTo
    {
        return $this->belongsTo(Amendment::class, 'id_dokument', 'id_dokument');
    }
}
