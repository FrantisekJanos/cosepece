<?php

declare(strict_types=1);

namespace Tests\PHPStan;

use PHPStan\Reflection\ReflectionProvider;
use PHPStan\Rules\Rule;
use PHPStan\Testing\RuleTestCase;
use Project\PHPStan\Rules\RequestDTOValidationAttributesRule;

/** @extends RuleTestCase<RequestDTOValidationAttributesRule> */
final class RequestDTOValidationAttributesRuleTest extends RuleTestCase
{
    protected function getRule(): Rule
    {
        return new RequestDTOValidationAttributesRule(self::getContainer()->getByType(ReflectionProvider::class));
    }

    public function test_dtos_only_use_spatie_validation_attributes(): void
    {
        $this->analyse([__DIR__ . '/Fixtures/dto-validation.php'], [
            ['RequestDTO::$number must declare at least one built-in Spatie validation attribute.', 40],
            ['RequestDTO::$number must declare at least one built-in Spatie validation attribute.', 45],
            ['RequestDTO::$number must not use Spatie\\LaravelData\\Attributes\\Validation\\Rule.', 50],
            ['RequestDTO::$number must declare at least one built-in Spatie validation attribute.', 50],
            ['RequestDTO::$number must not use Spatie\\LaravelData\\Attributes\\WithoutValidation.', 55],
            ['RequestDTO::$number must declare at least one built-in Spatie validation attribute.', 55],
            ['DTOValidationFixtures\\ManualRulesDTO must use Spatie validation attributes instead of rules().', 59],
            ['DTOValidationFixtures\\TraitRulesDTO must use Spatie validation attributes instead of rules().', 69],
            ['RequestDTO validation must be declared with Spatie attributes, not validator calls.', 78],
            ['RequestDTO validation must be declared with Spatie attributes, not validator calls.', 79],
            ['RequestDTO validation must be declared with Spatie attributes, not validator calls.', 80],
            ['RequestDTO::$number must use built-in Spatie validation attributes instead of DTOValidationFixtures\\CustomMin.', 90],
            ['RequestDTO::$number must declare at least one built-in Spatie validation attribute.', 90],
        ]);
    }
}
