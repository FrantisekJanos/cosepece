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
        private \App\Services\FindingsService $findingService,
        private BillListingService $listing,
        private \App\Services\AlertPresentationService $alertPresentation,
    ) {
    }

    public function data(int $findingsShown, string $sort = 'skore', string $group = ''): array
    {
        $w = Scorer::weights(hlidacConfig());
        $s = $this->stats();

        $findingsShown = max(9, min(60, $findingsShown));
        $found = $this->findingService->findings((int)$w['finding_min'], $sort, $group);
        $findings = array_slice($found['rows'], 0, 60);
        $findingsUrl = fn(array $set): string => route('home', array_filter(array_merge(['razeni' => $sort === 'skore' ? null : $sort, 'druh' => $group ?: null], $set)), false) . '#nalezy';
        $alerts = $this->alertPresentation->presentMany(Alert::query()->where('score', '>=', (int)$w['level_watch'])->orderByDesc('event_at')->orderByDesc('created_at')->orderByDesc('alert_key')
            ->limit(8)->get());
        $suppressed = Alert::query()->where('score', '<', (int)$w['level_watch'])->count();
        $watch = $this->watchlist();
        $authors = Amendment::query()->select('osoba')->selectRaw('COUNT(*) AS n')
            ->where('cas', '>=', date('Y-m-d H:i:s', strtotime('-7 days')))
            ->groupBy('osoba')->orderByDesc('n')->orderBy('osoba')->limit(6)->get()->map->getAttributes()->all();
        $bills = $this->listing->bills('flagged', '', 12);
        $maxAuthor = $authors[0]['n'] ?? 1;

        $title = 'Přehled';
        $active = 'prehled';

        return compact('found', 'sort', 'group', 'findingsUrl', 'title', 'active', 'w', 's', 'findingsShown', 'findings', 'alerts', 'suppressed', 'watch', 'authors', 'bills', 'maxAuthor');
    }

    public function topFindings(int $minimumScore, int $limit): array
    {
        return array_slice($this->findingService->findings($minimumScore)['rows'], 0, $limit);
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
