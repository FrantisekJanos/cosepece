<?php

declare(strict_types=1);

// Line comment.
# Hash comment.
/* Block comment. */
/** PHPDoc comment. */
function commentFixture(): string
{
    $url = 'https://example.test/path#fragment';
    $text = '// string, # string, /* string */';
    $heredoc = <<<'TEXT'
// This is string content.
/* This is string content too. */
TEXT;
    return $url . $text . $heredoc; // Trailing comment.
}

/* Comment at the end of the file. */
