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
Route::get('/index.php', [HomeController::class, 'index']);
Route::get('/tisky.php', [BillController::class, 'index'])->name('bills');
Route::get('/tisk.php', [BillController::class, 'show'])->name('bill');
Route::get('/upozorneni.php', [AlertController::class, 'index'])->name('alerts');
Route::get('/metodika.php', [MethodologyController::class, 'index'])->name('methodology');
Route::get('/rss.php', [FeedController::class, 'index'])->name('rss');
Route::get('/og.php', [OgController::class, 'show'])->name('og');
Route::match(['get', 'post'], '/ai_api.php', [AiController::class, 'handle'])
    ->middleware([VerifyServiceToken::class . ':ai', EnsureAiEnabled::class, ValidateAiTransport::class])->name('ai');
Route::get('/cron.php', [SyncController::class, 'run'])
    ->middleware(VerifyServiceToken::class . ':cron')->name('webcron');
