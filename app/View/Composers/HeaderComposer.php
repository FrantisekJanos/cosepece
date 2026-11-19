<?php

declare(strict_types=1);

namespace App\View\Composers;

use App\Services\Pages\LayoutService;
use Illuminate\View\View;

final class HeaderComposer
{
    public function __construct(private LayoutService $layout)
    {
    }

    public function compose(View $view): void
    {
        $data = $view->getData();
        $page = request()->path() === '/' ? 'index.php' : basename(request()->path());
        $view->with($this->layout->header(
            $data['title'],
            $data['active'],
            $data['description'] ?? '',
            $data['og'] ?? [],
            $page,
        ));
    }
}
