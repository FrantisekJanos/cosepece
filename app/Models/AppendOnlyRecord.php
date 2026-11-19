<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Builder;
use LogicException;

abstract class AppendOnlyRecord extends Model
{
    protected $primaryKey = null;
    public $incrementing = false;
    public $timestamps = false;

    protected function performUpdate(Builder $query)
    {
        throw new LogicException('Historický záznam se složeným klíčem nelze jednotlivě aktualizovat.');
    }

    public function delete()
    {
        throw new LogicException('Historický záznam se složeným klíčem nelze jednotlivě smazat.');
    }
}
