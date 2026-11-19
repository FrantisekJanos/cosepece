<?php

declare(strict_types=1);

namespace App\Http\DTO;

use Spatie\LaravelData\Attributes\Validation\IntegerType;
use Spatie\LaravelData\Attributes\Validation\StringType;
use Spatie\LaravelData\Attributes\Validation\In;
use Spatie\LaravelData\Attributes\MapInputName;
use Spatie\LaravelData\Attributes\Validation\Max;
use Spatie\LaravelData\Attributes\Validation\Min;
use Spatie\LaravelData\Attributes\Validation\Sometimes;

final class OverviewRequestDTO extends RequestDTO
{
    public function __construct(
        #[MapInputName('n'), Sometimes, IntegerType, Min(9), Max(60)]
        public readonly int $findingsShown,
        #[MapInputName('razeni'), Sometimes, StringType, In('skore', 'ai', 'nove', 'hlasovani')]
        public readonly string $sort = 'skore',
        #[MapInputName('druh'), Sometimes, StringType, In('', 'prilepky', 'zmeny_pn', 'lhuty', 'par90', 'pripominky', 'zpetne')]
        public readonly string $group = '',
    ) {
    }

    protected static function fromValidated(array $data): static
    {
        return new static((int)($data['n'] ?? 9), $data['razeni'] ?? 'skore', $data['druh'] ?? '');
    }
}
