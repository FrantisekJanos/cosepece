<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

final class UpcomingBill extends Model
{
    protected $table = 'pripravovane';
    protected $primaryKey = 'pid';
    protected $keyType = 'string';
    public $incrementing = false;
    public $timestamps = false;
    protected $fillable = ['pid', 'nazev', 'resort', 'faze', 'stav', 'zalozeno', 'zmeneno', 'pripominky_od', 'pripominky_do', 'dni', 'mist', 'vlada', 'duvod', 'popis', 'mimo_plan', 'vyjimka', 'verzi'];

    protected function casts(): array
    {
        return ['dni' => 'integer', 'mist' => 'integer', 'verzi' => 'integer', 'mimo_plan' => 'boolean', 'vyjimka' => 'boolean'];
    }
}
