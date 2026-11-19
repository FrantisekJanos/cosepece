<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

final class Meta extends Model
{
    protected $table = 'meta';
    protected $primaryKey = 'k';
    protected $keyType = 'string';
    public $incrementing = false;
    public $timestamps = false;

    protected $fillable = [
        'k', 'v',
    ];
}
