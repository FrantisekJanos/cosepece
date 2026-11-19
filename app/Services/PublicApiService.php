<?php


declare(strict_types=1);

namespace App\Services;

use App\Http\DTO\PublicApiRequestDTO;
use App\Models\Alert;
use App\Models\Bill;
use App\Models\Meta;
use App\Services\Pages\BillListingService;
use App\Services\Pages\UpcomingService;
use Hlidac\Scorer;
use Hlidac\View;

final class PublicApiService
{
    public const VERSION = 1;
    private ?int $period = null;

    public function __construct(
        private BillListingService $listing,
        private UpcomingService $upcoming,
        private BillPresentationService $presentation,
        private ReviewStatusService $reviews,
        private AlertPresentationService $alertPresentation,
    ) {
    }

    public function handle(PublicApiRequestDTO $dto): array
    {
        if ($dto->detailBill !== null) {
            $bill = $this->bill($dto->detailBill);
            return $bill === null ? [404, ['error' => 'Tisk nenalezen']] : [200, $this->wrap(['tisk' => $bill])];
        }
        if ($dto->includeUpcoming) {
            return [200, $this->wrap(['pripravovane' => $this->upcoming()])];
        }
        $filters = $dto->filters();
        return $dto->includeAlerts
            ? [200, $this->wrap(['upozorneni' => $this->alerts($filters)])]
            : [200, $this->wrap(['tisky' => $this->bills($filters)])];
    }

    public function upcoming(): array
    {
        return array_map(fn(array $u) => [
            'pid' => $u['pid'], 'nazev' => $u['nazev'], 'resort' => $u['resort'], 'faze' => $u['faze'], 'stav_odok' => $u['stav'],
            'zalozeno' => $u['zalozeno'], 'zmeneno' => $u['zmeneno'],
            'pripominky' => $u['dni'] !== null ? ['od' => $u['pripominky_od'], 'do' => $u['pripominky_do'], 'dni' => (int)$u['dni'], 'mist' => (int)$u['mist']] : null,
            'vlada_projednala' => $u['vlada'], 'mimo_plan' => (bool)$u['mimo_plan'], 'vyjimka' => (bool)$u['vyjimka'],
            'popis' => $u['popis'], 'duvod' => $u['duvod'], 'veklep_url' => \Hlidac\GovPrep::url((string)$u['pid']),
        ], $this->upcoming->items());
    }

    public function bills(array $f): array
    {

        $out = [];
        foreach ($this->listing->bills('', '', 1000, 'recent', (int)($f['min_score'] ?? 0)) as $t) {
            if ($f['since'] !== '' && (string)$t['posledni'] < $f['since']) {
                continue;
            }
            if ($f['zakon'] !== '' && !in_array($f['zakon'], explode(',', (string)$t['zakony']), true)) {
                continue;
            }
            $out[] = $this->summary($t);
            if (count($out) >= $f['limit']) {
                break;
            }
        }
        return $out;
    }

    public function bill(int $ct): ?array
    {
        $bill = Bill::query()->where('ct', $ct)->with(['votes.clubs'])->first();
        $t = $bill === null ? null : $this->presentation->present($bill);
        if ($t === null) {
            return null;
        }
        $out = $this->summary($t);
        $out['hlasovani'] = array_map(fn(array $v) => [
            'id' => (int)$v['id_hlasovani'], 'datum' => $v['datum'], 'vysledek' => $v['vysledek'] === 'A' ? 'přijato' : 'zamítnuto',
            'pro' => (int)$v['pro'], 'proti' => (int)$v['proti'], 'zdrzel' => (int)$v['zdrzel'], 'kvorum' => (int)$v['kvorum'],
            'kluby' => array_map(fn(array $c) => ['klub' => $c['klub'], 'pro' => (int)$c['pro'], 'proti' => (int)$c['proti'],
                'zdrzel_nehlasoval' => (int)$c['zdrzel'], 'nepritomen' => (int)$c['nepritomen']], $v['kluby']),
            'psp_url' => 'https://www.psp.cz/sqw/hlasy.sqw?g=' . (int)$v['id_hlasovani'],
        ], $bill->votes->sortByDesc('id_hlasovani')->sortByDesc('datum')->map(fn ($vote): array => $vote->getAttributes() + ['kluby' => $vote->clubs->sortBy('klub')->map->getAttributes()->all()])->all());
        $out['upozorneni'] = array_map([$this, 'alert'], $this->alertPresentation->presentMany($bill->alerts()->orderByDesc('event_at')->orderByDesc('created_at')->orderByDesc('alert_key')->limit(100)->get()));
        $status = $this->reviews->statuses($ct)[$ct] ?? ['total' => 0, 'done' => 0];
        $out['rozbor_textu'] = ['posouzeno' => (int)$status['done'], 'celkem' => (int)$status['total']];
        return $out;
    }

    public function alerts(array $f): array
    {
        return array_map([$this, 'alert'], $this->alertRows($f));
    }

    public function alertRows(array $f): array
    {

        $min = $f['min_score'] ?? (int)Scorer::weights(hlidacConfig())['level_watch'];
        $query = Alert::query()->where('score', '>=', $min)->orderByDesc('event_at')->orderByDesc('created_at')->orderByDesc('alert_key');
        if ($f['tisk'] !== null) {
            $query->currentPeriod()->where('ct', $f['tisk']);
        }
        if ($f['since'] !== '') {
            $query->where('event_at', '>=', $f['since']);
        }
        if ($f['zakon'] !== '') {
            $ids = Bill::query()->get(['ct', 'zakony'])->filter(
                fn (Bill $bill): bool => in_array($f['zakon'], explode(',', (string)$bill->zakony), true),
            )->pluck('ct')->all();
            $query->currentPeriod()->whereIn('ct', $ids);
        }
        return $this->alertPresentation->presentMany($query->limit($f['limit'])->get());
    }

    private function summary(array $t): array
    {
        $obdobi = $this->period ??= (int)(Meta::query()->whereKey('obdobi_cislo')->value('v') ?? 0);
        return [
            'ct' => (int)$t['ct'],
            'obdobi' => $obdobi,
            'nazev' => $t['nazev'],
            'uplny_nazev' => $t['uplny_nazev'],
            'typ_navrhu' => $t['typ_navrhu'],
            'navrhovatel' => $t['navrhovatel'],
            'faze' => $t['faze'],
            'predlozeno' => $t['predlozeno'],
            'posledni_zmena' => $t['posledni'],
            'zakony' => array_values(array_filter(explode(',', (string)$t['zakony']))),
            'skore' => (int)$t['score'],
            'uroven' => Scorer::level((int)$t['score'], hlidacConfig()),
            'duvody' => array_map(fn(array $r) => ['klic' => $r['key'], 'body' => (int)$r['points'], 'popis' => $r['label']],
                $t['reasons_list'] ?? (json_decode((string)$t['reasons'], true) ?: [])),
            'priznaky' => array_map(fn(array $f) => $f[1], $t['flags'] ?? []),
            'url' => siteUrl(route('bill', ['ct' => (int)$t['ct']], false)),
            'psp_url' => View::pspTiskUrl((int)$t['ct'], $obdobi),
        ];
    }

    private function alert(array $a): array
    {
        return [
            'id' => $a['alert_key'], 'typ' => $a['kind'], 'ct' => (int)$a['ct'] > 0 ? (int)$a['ct'] : null, 'obdobi' => $a['obdobi'] !== null ? (int)$a['obdobi'] : null,
            'datum' => $a['event_at'], 'titulek' => $a['title'], 'text' => $a['body'], 'skore' => (int)$a['score'],
            'url' => View::alertUrl($a, true),
        ];
    }

    private function wrap(array $data): array
    {
        return ['verze' => self::VERSION, 'zdroj' => siteUrl(), 'aktualizovano' => Meta::query()->whereKey('last_sync')->value('v')] + $data;
    }
}
