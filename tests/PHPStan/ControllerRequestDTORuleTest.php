<?php

declare(strict_types=1);

namespace Tests\PHPStan;

use PHPStan\Reflection\ReflectionProvider;
use PHPStan\Rules\Rule;
use PHPStan\Testing\RuleTestCase;
use Project\PHPStan\Rules\ControllerRequestDTORule;

/** @extends RuleTestCase<ControllerRequestDTORule> */
final class ControllerRequestDTORuleTest extends RuleTestCase
{
    protected function getRule(): Rule
    {
        return new ControllerRequestDTORule(self::getContainer()->getByType(ReflectionProvider::class));
    }

    public function test_controller_actions_require_concrete_request_dto_types(): void
    {
        $errors = [];
        foreach ([
            'missing' => 29, 'rawRequest' => 30, 'untyped' => 31, 'nullable' => 32,
            'union' => 33, 'base' => 34, 'wrongPosition' => 35, 'fake' => 36,
            'abstractDTO' => 37, 'unknown' => 38, 'variadic' => 39, 'optional' => 40,
            'defaultValue' => 41, 'byReference' => 42, 'onlyPhpDoc' => 44,
        ] as $method => $line) {
            $errors[] = [
                'App\\Http\\Controllers\\RuleFixtures\\InvalidController::' . $method
                    . '() must declare a concrete RequestDTO subclass as its first required, non-nullable parameter.',
                $line,
            ];
        }
        $errors[] = [
            'App\\Http\\Controllers\\RuleFixtures\\Nested\\NestedController::missing() must declare a concrete RequestDTO subclass as its first required, non-nullable parameter.',
            51,
        ];
        $this->analyse([__DIR__ . '/Fixtures/controllers.php'], $errors);
    }
}
