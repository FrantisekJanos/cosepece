<?php

declare(strict_types=1);

namespace Project\PHPStan\Rules;

use PhpParser\Node;
use PHPStan\Analyser\Scope;
use PHPStan\Node\FileNode;
use PHPStan\Rules\Rule;
use PHPStan\Rules\RuleErrorBuilder;

/** @implements Rule<FileNode> */
final class NoCommentsRule implements Rule
{
    public function getNodeType(): string
    {
        return FileNode::class;
    }

    public function processNode(Node $node, Scope $scope): array
    {
        $errors = [];
        $source = file_get_contents($scope->getFile());
        if ($source === false) {
            throw new \RuntimeException('Cannot read PHP source for comment validation.');
        }
        foreach (token_get_all($source) as $token) {
            if (!is_array($token)) {
                continue;
            }
            if ($token[0] !== T_COMMENT) {
                continue;
            }
            $errors[] = RuleErrorBuilder::message('Regular PHP comments are forbidden.')
                ->identifier('code.comments')->line($token[2])->build();
        }
        return $errors;
    }
}
