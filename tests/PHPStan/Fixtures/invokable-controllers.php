<?php

declare(strict_types=1);

namespace App\Http\Controllers\InvokableRuleFixtures;

use App\Http\DTO\OverviewRequestDTO;

final class NamedController
{
    public function index(OverviewRequestDTO $dto): void {}
}

final class InvokableController
{
    public function __invoke(OverviewRequestDTO $dto): void {}
}

final class MixedCaseController
{
    public function __INVOKE(OverviewRequestDTO $dto): void {}
}

namespace App\Http\Controllers\InvokableRuleFixtures\Nested;

final class NestedController
{
    public function __invoke(\App\Http\DTO\OverviewRequestDTO $dto): void {}
}

namespace App\Services\InvokableRuleFixtures;

final class InvokableService
{
    public function __invoke(): void {}
}
