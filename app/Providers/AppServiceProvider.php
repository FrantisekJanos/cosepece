<?php

declare(strict_types=1);

namespace App\Providers;

use App\View\Composers\HeaderComposer;
use Hlidac\Db;
use Hlidac\AiReviewer;
use Hlidac\Scorer;
use Illuminate\Support\Facades\DB as Database;
use Illuminate\Support\Facades\View;
use Illuminate\Support\ServiceProvider;

final class AppServiceProvider extends ServiceProvider
{
    public function register(): void
    {

        $this->app->singleton(Db::class, fn () => new Db(Database::connection()->getPdo()));
        $this->app->bind(AiReviewer::class, fn ($app) => new AiReviewer($app->make(Db::class), hlidacConfig()));
        $this->app->bind(Scorer::class, fn ($app) => new Scorer($app->make(Db::class), hlidacConfig()));
    }

    public function boot(): void
    {
        View::composer('layouts.header', HeaderComposer::class);
        View::composer('partials.search', function ($view): void {
            $view->with('searchIndexVersion', substr(md5((string)(\App\Models\Meta::query()->whereKey('last_sync')->value('v') ?? '')), 0, 10));
        });
        mb_internal_encoding('UTF-8');
        $path = hlidacDataPath();
        if (!is_dir($path) && !mkdir($path, 0775, true) && !is_dir($path)) {
            throw new \RuntimeException('Nelze vytvořit adresář dat.');
        }
    }
}
