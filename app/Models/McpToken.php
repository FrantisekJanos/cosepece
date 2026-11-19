<?php

declare(strict_types=1);

namespace App\Models;

use Illuminate\Database\Eloquent\Model;

final class McpToken extends Model
{
    protected $table = 'mcp.token';
    public $timestamps = false;
    protected $fillable = ['jmeno', 'hash', 'vytvoren', 'naposledy', 'zrusen'];
    protected $hidden = ['hash'];

    protected function casts(): array
    {
        return ['vytvoren' => 'immutable_datetime', 'naposledy' => 'immutable_datetime', 'zrusen' => 'immutable_datetime'];
    }
}
