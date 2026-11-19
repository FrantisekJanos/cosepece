<?php

declare(strict_types=1);

namespace App\Http\DTO;

final class UpcomingRequestDTO extends RequestDTO
{

    protected static function fromValidated(array $data): static
    {
        return new static();
    }
}
