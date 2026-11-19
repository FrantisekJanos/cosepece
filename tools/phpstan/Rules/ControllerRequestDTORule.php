<?php

declare(strict_types=1);

namespace Project\PHPStan\Rules;

use App\Http\DTO\RequestDTO;
use PhpParser\Node;
use PhpParser\Node\Name;
use PhpParser\Node\Stmt\ClassMethod;
use PHPStan\Analyser\Scope;
use PHPStan\Reflection\ReflectionProvider;
use PHPStan\Rules\Rule;
use PHPStan\Rules\RuleErrorBuilder;

/** @implements Rule<ClassMethod> */
final class ControllerRequestDTORule implements Rule
{
    public function __construct(private ReflectionProvider $reflectionProvider)
    {
    }

    public function getNodeType(): string
    {
        return ClassMethod::class;
    }

    public function processNode(Node $node, Scope $scope): array
    {
        $class = $scope->getClassReflection();
        if ($class === null || !str_starts_with($class->getName(), 'App\\Http\\Controllers\\')
            || $class->isInterface() || $class->isTrait() || !$node->isPublic()
            || in_array(strtolower($node->name->toString()), ['__construct', '__destruct'], true)) {
            return [];
        }

        $parameter = $node->params[0] ?? null;

        if ($parameter !== null && $parameter->type instanceof Name
            && $parameter->default === null && !$parameter->variadic && !$parameter->byRef) {
            $name = $scope->resolveName($parameter->type);
            if ($this->reflectionProvider->hasClass($name)) {
                $dto = $this->reflectionProvider->getClass($name);
                if (!$dto->isAbstract() && $dto->isSubclassOf(RequestDTO::class)) {
                    return [];
                }
            }
        }

        return [RuleErrorBuilder::message(sprintf(
            '%s::%s() must declare a concrete RequestDTO subclass as its first required, non-nullable parameter.',
            $class->getName(), $node->name->toString(),
        ))->identifier('controller.requestDto')->line($node->getStartLine())->build()];
    }
}
