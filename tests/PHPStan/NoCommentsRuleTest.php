<?php

declare(strict_types=1);

namespace Tests\PHPStan;

use PHPStan\Rules\Rule;
use PHPStan\Testing\RuleTestCase;
use Project\PHPStan\Rules\NoCommentsRule;

/** @extends RuleTestCase<NoCommentsRule> */
final class NoCommentsRuleTest extends RuleTestCase
{
    protected function getRule(): Rule
    {
        return new NoCommentsRule();
    }

    public function test_all_php_comment_tokens_are_detected_without_matching_string_content(): void
    {
        $this->analyse([__DIR__ . '/Fixtures/comments.php'], [
            ['Regular PHP comments are forbidden.', 5],
            ['Regular PHP comments are forbidden.', 6],
            ['Regular PHP comments are forbidden.', 7],
            ['Regular PHP comments are forbidden.', 17],
            ['Regular PHP comments are forbidden.', 20],
        ]);
    }

    public function test_phpdoc_is_allowed(): void
    {
        $this->analyse([__DIR__ . '/Fixtures/phpdoc-only.php'], []);
    }
}
