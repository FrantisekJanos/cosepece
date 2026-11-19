<?php

declare(strict_types=1);

namespace Tests\PHPStan;

use PHPStan\Rules\Rule;
use PHPStan\Testing\RuleTestCase;
use Project\PHPStan\Rules\NoInvokableControllerRule;

/** @extends RuleTestCase<NoInvokableControllerRule> */
final class NoInvokableControllerRuleTest extends RuleTestCase
{
    protected function getRule(): Rule
    {
        return new NoInvokableControllerRule();
    }

    public function test_controllers_use_named_actions_even_when_they_have_a_valid_dto(): void
    {
        $this->analyse([__DIR__ . '/Fixtures/invokable-controllers.php'], [
            ['App\\Http\\Controllers\\InvokableRuleFixtures\\InvokableController must use named controller actions instead of __invoke().', 16],
            ['App\\Http\\Controllers\\InvokableRuleFixtures\\MixedCaseController must use named controller actions instead of __invoke().', 21],
            ['App\\Http\\Controllers\\InvokableRuleFixtures\\Nested\\NestedController must use named controller actions instead of __invoke().', 28],
        ]);
    }
}
