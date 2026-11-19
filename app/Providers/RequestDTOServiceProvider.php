<?php

declare(strict_types=1);

namespace App\Providers;

use App\Http\DTO\AiRequestDTO;
use App\Http\DTO\AlertListRequestDTO;
use App\Http\DTO\BillDetailRequestDTO;
use App\Http\DTO\BillListRequestDTO;
use App\Http\DTO\FeedRequestDTO;
use App\Http\DTO\MethodologyRequestDTO;
use App\Http\DTO\OgImageRequestDTO;
use App\Http\DTO\OverviewRequestDTO;
use App\Http\DTO\SyncRequestDTO;
use Illuminate\Http\Request;
use Illuminate\Support\ServiceProvider;

final class RequestDTOServiceProvider extends ServiceProvider
{
    public function register(): void
    {
        foreach ([
            \App\Http\DTO\UpcomingRequestDTO::class, \App\Http\DTO\ChangesRequestDTO::class, \App\Http\DTO\RevisionRequestDTO::class, \App\Http\DTO\PublicApiRequestDTO::class, \App\Http\DTO\DocumentRequestDTO::class, AiRequestDTO::class, AlertListRequestDTO::class, BillDetailRequestDTO::class,
            BillListRequestDTO::class, FeedRequestDTO::class, MethodologyRequestDTO::class,
            OgImageRequestDTO::class, OverviewRequestDTO::class, SyncRequestDTO::class,
        ] as $dtoClass) {

            $this->app->bind($dtoClass, fn ($app) => $dtoClass::fromRequest($app->make(Request::class)));
        }
    }
}
