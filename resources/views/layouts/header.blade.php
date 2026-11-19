<?php use Hlidac\OgCard; ?>
<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ $title }} – Co se peče</title>
<meta name="description" content="{{ $desc }}">
<link rel="canonical" href="{{ $ogUrl }}">
<meta property="og:type" content="{!! isset($og['url']) ? 'article' : 'website' !!}">
<meta property="og:site_name" content="Co se peče">
<meta property="og:locale" content="cs_CZ">
<meta property="og:url" content="{{ $ogUrl }}">
<meta property="og:title" content="{{ $ogTitle }}">
<meta property="og:description" content="{{ $desc }}">
<meta property="og:image" content="{{ $ogImage }}">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="{{ OgCard::WIDTH }}">
<meta property="og:image:height" content="{{ OgCard::HEIGHT }}">
<meta property="og:image:alt" content="{{ $ogAlt }}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{{ $ogTitle }}">
<meta name="twitter:description" content="{{ $desc }}">
<meta name="twitter:image" content="{{ $ogImage }}">
<meta name="twitter:image:alt" content="{{ $ogAlt }}">
<link rel="alternate" type="application/rss+xml" title="Co se peče – upozornění" href="{{ route('rss', [], false) }}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&amp;family=Space+Grotesk:wght@500;600;700&amp;display=swap" rel="stylesheet">
<link rel="stylesheet" href="{{ asset('assets/style.css') . '?v=' . filemtime(public_path('assets/style.css')) }}">
<script>document.documentElement.classList.add('js');</script>
<script defer src="{{ asset('assets/nav.js') }}"></script>
<?php $umami = (array)hlidacConfig('umami', []); if (($umami['src'] ?? '') !== '' && ($umami['website_id'] ?? '') !== ''): ?>
<script defer src="{{ $umami['src'] }}" data-website-id="{{ $umami['website_id'] }}"<?php if (($umami['domains'] ?? '') !== ''): ?> data-domains="{{ $umami['domains'] }}"<?php endif; ?> data-do-not-track="true"></script>
<?php endif; ?>
</head>
<body>
<a class="skip-link" href="#obsah">Přeskočit na obsah</a>
<header class="top">
  <div class="top-in">
    <a class="brand" href="{{ route('home', [], false) }}">

      <span class="brand-text">
        <span class="brand-name">Co se peče</span>
        <span class="brand-sub">Sněmovna · {{ $obdobi }}. volební období</span>
      </span>
    </a>
    <button type="button" class="nav-toggle" id="nav-toggle" aria-expanded="false" aria-controls="site-menu" aria-label="Menu">
      <span class="nav-toggle-bars" aria-hidden="true"></span>
      <span class="nav-toggle-label">Menu</span>
    </button>
    <div class="top-menu" id="site-menu">
      <nav class="top-nav" aria-label="Hlavní menu">
        <?php foreach ($nav as $key => [$href, $label]): ?>
          <a href="{{ $href }}"{!! $key === $active ? ' class="active" aria-current="page"' : '' !!}>{{ $label }}</a>
        <?php endforeach; ?>
      </nav>
      <div class="top-extra">
        <a class="rss-link" href="{{ route('rss', [], false) }}" title="Odebírat upozornění přes RSS">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 11a9 9 0 0 1 9 9"/><path d="M4 4a16 16 0 0 1 16 16"/><circle cx="5" cy="19" r="1"/></svg>
          <span>RSS</span>
        </a>
        <div class="sync">{!! $sync ? 'Data aktualizována ' . e(formatDate($sync, true)) : 'Data zatím nebyla načtena' !!}</div>
      </div>
    </div>
  </div>
</header>
<main class="wrap" id="obsah" tabindex="-1">
