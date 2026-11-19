<?php

declare(strict_types=1);

namespace Project\PHPStan\Rules;

use App\Http\DTO\RequestDTO;
use PhpParser\Node;
use PhpParser\Node\Expr\FuncCall;
use PhpParser\Node\Expr\MethodCall;
use PhpParser\Node\Expr\New_;
use PhpParser\Node\Expr\StaticCall;
use PhpParser\Node\Expr\Variable;
use PhpParser\Node\Identifier;
use PhpParser\Node\Name;
use PhpParser\Node\Stmt\ClassMethod;
use PhpParser\Node\Stmt\Property;
use PhpParser\NodeFinder;
use PHPStan\Analyser\Scope;
use PHPStan\Node\InClassNode;
use PHPStan\Reflection\ReflectionProvider;
use PHPStan\Rules\Rule;
use PHPStan\Rules\RuleErrorBuilder;
use Spatie\LaravelData\Attributes\Validation\Rule as RawRule;
use Spatie\LaravelData\Attributes\WithoutValidation;
use Spatie\LaravelData\Support\Validation\ValidationRule;

/** @implements Rule<InClassNode> */
final class RequestDTOValidationAttributesRule implements Rule
{
    public function __construct(private ReflectionProvider $reflectionProvider)
    {
    }

    public function getNodeType(): string
    {
        return InClassNode::class;
    }

    public function processNode(Node $node, Scope $scope): array
    {
        $class = $node->getClassReflection();
        if (!$class->isSubclassOf(RequestDTO::class)) {
            return [];
        }
        $errors = [];
        foreach (['rules', 'withValidator', 'getValidationRules', 'validate', 'validateAndCreate'] as $hook) {
            if (!$class->hasNativeMethod($hook)) {
                continue;
            }
            $owner = $class->getNativeMethod($hook)->getDeclaringClass()->getName();
            if (!str_starts_with($owner, 'Spatie\\LaravelData\\')) {
                $errors[] = RuleErrorBuilder::message(sprintf(
                    '%s must use Spatie validation attributes instead of %s().', $class->getName(), $hook,
                ))->identifier('dto.manualValidation')->build();
            }
        }

        $original = $node->getOriginalNode();
        foreach ($original->stmts as $statement) {
            if ($statement instanceof Property && $statement->isPublic() && !$statement->isStatic()) {
                foreach ($statement->props as $property) {
                    $errors = array_merge($errors, $this->checkAttributes($property->name->toString(), $statement->attrGroups, $scope, $statement->getStartLine()));
                }
            }
            if ($statement instanceof ClassMethod && strtolower($statement->name->toString()) === '__construct') {
                foreach ($statement->params as $parameter) {
                    if (($parameter->flags & \PhpParser\Node\Stmt\Class_::MODIFIER_PUBLIC) !== 0
                        && $parameter->var instanceof Variable && is_string($parameter->var->name)) {
                        $errors = array_merge($errors, $this->checkAttributes($parameter->var->name, $parameter->attrGroups, $scope, $parameter->getStartLine()));
                    }
                }
            }
        }

        foreach ((new NodeFinder())->find($original->stmts, fn (Node $child) =>
            $child instanceof StaticCall || $child instanceof MethodCall || $child instanceof FuncCall || $child instanceof New_
        ) as $call) {
            $manual = false;
            if ($call instanceof StaticCall || $call instanceof New_) {
                if ($call->class instanceof Name) {
                    $owner = $scope->resolveName($call->class);
                    $manual = $owner === 'Illuminate\\Support\\Facades\\Validator'
                        || $owner === 'Illuminate\\Validation\\Validator';
                }
            }
            if (($call instanceof StaticCall || $call instanceof MethodCall) && $call->name instanceof Identifier) {
                $manual = $manual || in_array(strtolower($call->name->toString()), ['validate', 'validatewithbag', 'validateandcreate', 'getvalidationrules', 'withoutvalidation'], true);
            }
            if ($call instanceof FuncCall && $call->name instanceof Name) {
                $manual = $this->reflectionProvider->resolveFunctionName($call->name, $scope) === 'validator';
            }
            if ($manual) {
                $errors[] = RuleErrorBuilder::message('RequestDTO validation must be declared with Spatie attributes, not validator calls.')
                    ->identifier('dto.manualValidation')->line($call->getStartLine())->build();
            }
        }
        return $errors;
    }

    /** @param array<Node\AttributeGroup> $groups
     *  @return list<\PHPStan\Rules\IdentifierRuleError>
     */
    private function checkAttributes(string $property, array $groups, Scope $scope, int $line): array
    {
        $errors = [];
        $hasValidation = false;
        foreach ($groups as $group) {
            foreach ($group->attrs as $attribute) {
                $name = $scope->resolveName($attribute->name);
                if ($name === RawRule::class || $name === WithoutValidation::class) {
                    $errors[] = RuleErrorBuilder::message(sprintf('RequestDTO::$%s must not use %s.', $property, $name))
                        ->identifier('dto.validationBypass')->line($attribute->getStartLine())->build();
                    continue;
                }
                if (!$this->reflectionProvider->hasClass($name)) {
                    continue;
                }
                $reflection = $this->reflectionProvider->getClass($name);
                if ($reflection->isSubclassOf(ValidationRule::class)) {
                    if (str_starts_with($name, 'Spatie\\LaravelData\\Attributes\\Validation\\')) {
                        $hasValidation = true;
                    } else {
                        $errors[] = RuleErrorBuilder::message(sprintf('RequestDTO::$%s must use built-in Spatie validation attributes instead of %s.', $property, $name))
                            ->identifier('dto.validationAttribute')->line($attribute->getStartLine())->build();
                    }
                }
            }
        }
        if (!$hasValidation) {
            $errors[] = RuleErrorBuilder::message(sprintf('RequestDTO::$%s must declare at least one built-in Spatie validation attribute.', $property))
                ->identifier('dto.missingValidationAttribute')->line($line)->build();
        }
        return $errors;
    }
}
