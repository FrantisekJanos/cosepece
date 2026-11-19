<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

final class GovernmentPreparation extends Model
{
    protected $table = 'priprava';
    protected $primaryKey = 'ct';
    public $incrementing = false;
    public $timestamps = false;
    protected $fillable = ['ct', 'druh', 'pid', 'pripominky_od', 'pripominky_do', 'dni', 'mist', 'vlada', 'duvod', 'poznamka', 'mimo_plan', 'vyjimka', 'znovu'];

    protected function casts(): array
    {
        return ['ct' => 'integer', 'dni' => 'integer', 'mist' => 'integer', 'mimo_plan' => 'boolean',
            'vyjimka' => 'boolean', 'znovu' => 'boolean', 'pripominky_od' => 'immutable_date',
            'pripominky_do' => 'immutable_date', 'vlada' => 'immutable_date'];
    }

    public function bill(): BelongsTo
    {
        return $this->belongsTo(Bill::class, 'ct', 'ct');
    }
}
