<?php

declare(strict_types=1);

use App\Http\Controllers\AiController;
use App\Http\Controllers\FeedController;
use App\Http\Controllers\OgController;
use App\Http\Controllers\HomeController;
use App\Http\Controllers\BillController;
use App\Http\Controllers\AlertController;
use App\Http\Controllers\MethodologyController;
use App\Http\Controllers\SyncController;
use App\Http\Middleware\VerifyServiceToken;
use App\Http\Middleware\EnsureAiEnabled;
use App\Http\Middleware\ValidateAiTransport;
use Illuminate\Support\Facades\Route;

Route::get('/', [HomeController::class, 'index'])->name('home');
Route::get('/tisky', [BillController::class, 'index'])->name('bills');
Route::get('/tisky/{ct}', [BillController::class, 'show'])->whereNumber('ct')->name('bill');
Route::get('/upozorneni', [AlertController::class, 'index'])->name('alerts');
Route::get('/metodika', [MethodologyController::class, 'index'])->name('methodology');
Route::get('/zmeny', [\App\Http\Controllers\ChangesController::class, 'index'])->name('changes');
Route::get('/revize/{druh}/{id}', [\App\Http\Controllers\RevisionController::class, 'show'])->where('druh', 'tisk|pn')->whereNumber('id')->name('revisions');
Route::get('/api', [\App\Http\Controllers\PublicApiController::class, 'index'])->name('public-api');
Route::get('/rss', [FeedController::class, 'index'])->name('rss');
Route::get('/og', [OgController::class, 'show'])->name('og');

foreach ([
    '/zmeny.php' => [\App\Http\Controllers\ChangesController::class, 'index'],
    '/revize.php' => [\App\Http\Controllers\RevisionController::class, 'show'],
    '/api.php' => [\App\Http\Controllers\PublicApiController::class, 'index'],
    '/index.php' => [HomeController::class, 'index'],
    '/tisky.php' => [BillController::class, 'index'],
    '/tisk.php' => [BillController::class, 'show'],
    '/upozorneni.php' => [AlertController::class, 'index'],
    '/metodika.php' => [MethodologyController::class, 'index'],
    '/rss.php' => [FeedController::class, 'index'],
    '/og.php' => [OgController::class, 'show'],
] as $path => $action) {
    Route::get($path, $action)->middleware(\App\Http\Middleware\RedirectLegacyUrl::class);
}

Route::match(['get', 'post'], '/ai_api.php', [AiController::class, 'handle'])
    ->middleware([VerifyServiceToken::class . ':ai', EnsureAiEnabled::class, ValidateAiTransport::class])->name('ai');
Route::get('/cron.php', [SyncController::class, 'run'])
    ->middleware(VerifyServiceToken::class . ':cron')->name('webcron');
