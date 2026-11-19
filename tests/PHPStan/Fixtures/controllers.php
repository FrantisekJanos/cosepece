<?php

declare(strict_types=1);

namespace App\Http\Controllers\RuleFixtures;

use App\Http\DTO\OverviewRequestDTO;
use App\Http\DTO\OverviewRequestDTO as Input;
use App\Http\DTO\BillListRequestDTO;
use App\Http\DTO\RequestDTO;
use Illuminate\Http\Request;

final class NotARequestDTO {}
abstract class AbstractInput extends RequestDTO {}

final class ValidController
{
    public function __construct() {}
    public function __destruct() {}
    public function index(OverviewRequestDTO $dto): void {}
    public function alias(Input $dto, \stdClass $service): void {}
    public function fullyQualified(\App\Http\DTO\FeedRequestDTO $dto): void {}
    protected function helper(): void {}
    private function internal(): void {}
}

final class InvalidController
{
    public function missing(): void {}
    public function rawRequest(Request $request): void {}
    public function untyped($dto): void {}
    public function nullable(?OverviewRequestDTO $dto): void {}
    public function union(OverviewRequestDTO|BillListRequestDTO $dto): void {}
    public function base(RequestDTO $dto): void {}
    public function wrongPosition(\stdClass $service, OverviewRequestDTO $dto): void {}
    public function fake(NotARequestDTO $dto): void {}
    public function abstractDTO(AbstractInput $dto): void {}
    public function unknown(UnknownRequestDTO $dto): void {}
    public function variadic(OverviewRequestDTO ...$dto): void {}
    public function optional(?OverviewRequestDTO $dto = null): void {}
    public function defaultValue(OverviewRequestDTO $dto = new OverviewRequestDTO(6)): void {}
    public function byReference(OverviewRequestDTO &$dto): void {}
    /** @param OverviewRequestDTO $dto */
    public function onlyPhpDoc($dto): void {}
}

namespace App\Http\Controllers\RuleFixtures\Nested;

final class NestedController
{
    public function missing(): void {}
}

namespace App\Services\RuleFixtures;

final class RegularService
{
    public function withoutDTO(): void {}
}
