<?php

declare(strict_types=1);

namespace Tests\Feature;

use App\Http\DTO\BillListRequestDTO;
use App\Http\DTO\OverviewRequestDTO;
use App\Services\Pages\OverviewService;
use Illuminate\Http\Request;
use Tests\TestCase;

final class RequestDTOTest extends TestCase
{
    public function test_invalid_page_parameters_return_validation_errors_instead_of_casting_them(): void
    {
        foreach ([
            ['/?n[]=6', 'n'], ['/?n=61', 'n'],
            ['/tisky.php?f=unknown', 'f'], ['/tisky.php?q[]=text', 'q'],
            ['/tisky.php?sort=unknown', 'sort'], ['/tisky.php?min=101', 'min'],
            ['/tisky.php?q=' . str_repeat('x', 101), 'q'],
            ['/tisk.php?ct=abc', 'ct'], ['/tisk.php?ct[]=221', 'ct'], ['/tisk.php', 'ct'],
            ['/upozorneni.php?p=0', 'p'], ['/upozorneni.php?vse[]=1', 'vse'],
            ['/rss.php?vse=unknown', 'vse'],
            ['/og.php?ct=-1', 'ct'], ['/og.php?p=unknown', 'p'], ['/og.php?v=bad', 'v'],
        ] as [$url, $field]) {
            $this->getJson($url)->assertUnprocessable()->assertJsonValidationErrors($field);
        }
    }

    public function test_request_dto_is_validated_before_the_service_is_resolved(): void
    {
        $this->app->bind(OverviewService::class, function () {
            throw new \RuntimeException('Služba se při neplatném vstupu nesmí vytvořit.');
        });
        $this->getJson('/?n=wrong')->assertUnprocessable()->assertJsonValidationErrors('n');
    }

    public function test_dtos_contain_typed_validated_values_and_do_not_leak_between_requests(): void
    {
        $this->app->instance('request', Request::create('/tisky.php', 'GET', [
            'f' => 'mp', 'q' => '  dotace  ', 'sort' => 'recent', 'min' => '25', 'untrusted' => 'ignored',
        ]));
        $dto = $this->app->make(BillListRequestDTO::class);
        $this->assertSame('mp', $dto->filter);
        $this->assertSame('dotace', $dto->search);
        $this->assertSame('recent', $dto->sort);
        $this->assertSame(25, $dto->minimumScore);
        $this->assertFalse(property_exists($dto, 'untrusted'));
        foreach (['filter', 'search', 'sort', 'minimumScore'] as $property) {
            $this->assertTrue((new \ReflectionProperty($dto, $property))->isReadOnly());
        }

        $this->app->instance('request', Request::create('/tisky.php'));
        $defaults = $this->app->make(BillListRequestDTO::class);
        $this->assertNotSame($dto, $defaults);
        $this->assertSame(['', '', 'score', 0], [$defaults->filter, $defaults->search, $defaults->sort, $defaults->minimumScore]);
        $this->assertSame(6, OverviewRequestDTO::fromRequest(Request::create('/'))->findingsShown);
    }
}
