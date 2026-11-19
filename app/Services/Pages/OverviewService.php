<?php

declare(strict_types=1);

namespace App\Services\Pages;

use Hlidac\Scorer;
use App\Models\Alert;
use App\Models\Amendment;
use App\Models\Bill;
use App\Models\BillStep;
use App\Services\BillPresentationService;
use Hlidac\Analyzer;

final class OverviewService
{
    public function __construct(
        private BillPresentationService $presentation,
        private BillListingService $listing,
    ) {
    }

    public function data(int $findingsShown): array
    {
        $w = Scorer::weights(hlidacConfig());
        $s = $this->stats();

        $findingsShown = max(6, min(60, $findingsShown));
        $findings = $this->presentation->presentMany(
            Bill::query()->activeFindings((int)$w['level_major'])->ranked('score')->limit(60)->get(), true,
        );
        $alerts = Alert::query()->where('score', '>=', (int)$w['level_watch'])->orderByDesc('event_at')
            ->limit(8)->get()->map->getAttributes()->all();
        $suppressed = Alert::query()->where('score', '<', (int)$w['level_watch'])->count();
        $watch = $this->watchlist();
        $authors = Amendment::query()->select('osoba')->selectRaw('COUNT(*) AS n')
            ->where('cas', '>=', date('Y-m-d H:i:s', strtotime('-7 days')))
            ->groupBy('osoba')->orderByDesc('n')->orderBy('osoba')->limit(6)->get()->map->getAttributes()->all();
        $bills = $this->listing->bills('flagged', '', 12);
        $maxAuthor = $authors[0]['n'] ?? 1;

        $title = 'Přehled';
        $active = 'prehled';

        return compact('title', 'active', 'w', 's', 'findingsShown', 'findings', 'alerts', 'suppressed', 'watch', 'authors', 'bills', 'maxAuthor');
    }

    private function stats(): array
    {
        $since = date('Y-m-d H:i:s', strtotime('-7 days'));
        return [
            'pn_total' => Amendment::query()->count(),
            'bills_total' => Bill::query()->whereIn('druh', [1, 2])->count(),
            'bills_with_pn' => Bill::query()->where('pn_count', '>', 0)->count(),
            'pn_7d' => Amendment::query()->where('cas', '>=', $since)->count(),
            'pn_7d_top' => Amendment::query()
                ->leftJoin('tisky', 'tisky.ct', '=', 'pn.ct')
                ->select('pn.ct', 'tisky.nazev')->selectRaw('COUNT(*) AS n')
                ->where('pn.cas', '>=', $since)->groupBy('pn.ct', 'tisky.nazev')->orderByDesc('n')->first()?->getAttributes(),
            'p90' => Bill::query()->where('p90', 1)->count(),
            'p90ok' => Bill::query()->where('p90ok', 1)->count(),
            'zl' => Bill::query()->where('zl', 1)->count(),
            'zl_last' => BillStep::query()->select('ct', 'datum')
                ->whereIn('akce_id', Analyzer::AKCE_ZKRACENI)->orderByDesc('datum')->orderByDesc('id_hist')->first()?->getAttributes(),
        ];
    }

    private function watchlist(): array
    {
        $laws = hlidacConfig('watch_laws', []);
        $numbers = array_map('intval', hlidacConfig('watch_tisky', []));
        $bills = Bill::query()->orderByDesc('posledni')->get()->filter(function (Bill $bill) use ($laws, $numbers): bool {
            $changedLaws = array_filter(explode(',', (string)$bill->zakony));
            return array_intersect($changedLaws, $laws) !== [] || in_array($bill->ct, $numbers, true);
        })->values();
        return $this->presentation->presentMany($bills);
    }
}
