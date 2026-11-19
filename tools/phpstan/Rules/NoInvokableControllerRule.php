<?php

declare(strict_types=1);

namespace Project\PHPStan\Rules;

use PhpParser\Node;
use PhpParser\Node\Stmt\ClassMethod;
use PHPStan\Analyser\Scope;
use PHPStan\Rules\Rule;
use PHPStan\Rules\RuleErrorBuilder;

/** @implements Rule<ClassMethod> */
final class NoInvokableControllerRule implements Rule
{
    public function getNodeType(): string
    {
        return ClassMethod::class;
    }

    public function processNode(Node $node, Scope $scope): array
    {
        $class = $scope->getClassReflection();
        if ($class === null || !str_starts_with($class->getName(), 'App\\Http\\Controllers\\')
            || $class->isInterface() || $class->isTrait()
            || strtolower($node->name->toString()) !== '__invoke') {
            return [];
        }

        return [RuleErrorBuilder::message(sprintf(
            '%s must use named controller actions instead of __invoke().', $class->getName(),
        ))->identifier('controller.invokable')->line($node->getStartLine())->build()];
    }
}
