<?php

declare(strict_types=1);

namespace DTOValidationFixtures;

use App\Http\DTO\RequestDTO;
use Illuminate\Support\Facades\Validator as Validation;
use Spatie\LaravelData\Attributes\MapInputName;
use Spatie\LaravelData\Attributes\Validation\IntegerType;
use Spatie\LaravelData\Attributes\Validation\Min as LowerBound;
use Spatie\LaravelData\Attributes\Validation\Required;
use Spatie\LaravelData\Attributes\Validation\Rule as RawRule;
use Spatie\LaravelData\Attributes\WithoutValidation;
use function validator as checkInput;

abstract class FixtureDTO extends RequestDTO
{
    protected static function fromValidated(array $data): static { return new static(); }
}

final class ValidDTO extends FixtureDTO
{
    public function __construct(
        #[MapInputName('ct'), Required, IntegerType, LowerBound(1)]
        public readonly int $number,
    ) {}
}

final class PlainPropertyDTO extends FixtureDTO
{
    #[Required, IntegerType]
    public readonly int $number;
}

final class EmptyDTO extends FixtureDTO {}

final class MissingDTO extends FixtureDTO
{
    public function __construct(public readonly int $number) {}
}

final class MappingOnlyDTO extends FixtureDTO
{
    public function __construct(#[MapInputName('ct')] public readonly int $number) {}
}

final class RawRulesDTO extends FixtureDTO
{
    public function __construct(#[RawRule('integer')] public readonly int $number) {}
}

final class SkippedDTO extends FixtureDTO
{
    #[WithoutValidation]
    public readonly int $number;
}

final class ManualRulesDTO extends FixtureDTO
{
    public static function rules(): array { return ['number' => 'integer']; }
}

trait RulesTrait
{
    public static function rules(): array { return []; }
}

final class TraitRulesDTO extends FixtureDTO
{
    use RulesTrait;
}

final class ManualValidatorDTO extends FixtureDTO
{
    protected static function fromValidated(array $data): static
    {
        Validation::make($data, []);
        checkInput($data, []);
        request()->validate([]);
        return new static();
    }
}

#[\Attribute(\Attribute::TARGET_PARAMETER)]
final class CustomMin extends LowerBound {}

final class CustomAttributeDTO extends FixtureDTO
{
    public function __construct(#[CustomMin(1)] public readonly int $number) {}
}

final class NonDTOService
{
    public static function rules(): array { return []; }
    public function validate(): void {}
}
