<?php use Hlidac\OgCard; ?>
<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ $title }} – Hlídač zákonů</title>
<meta name="description" content="{{ $desc }}">
<link rel="canonical" href="{{ $ogUrl }}">
<meta property="og:type" content="{!! isset($og['url']) ? 'article' : 'website' !!}">
<meta property="og:site_name" content="Hlídač zákonů">
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
<link rel="alternate" type="application/rss+xml" title="Hlídač zákonů – upozornění" href="{{ route('rss', [], false) }}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&amp;family=Space+Grotesk:wght@500;600;700&amp;display=swap" rel="stylesheet">
<link rel="stylesheet" href="{{ asset('assets/style.css') }}">
<?php $umami = (array)hlidacConfig('umami', []); if (($umami['src'] ?? '') !== '' && ($umami['website_id'] ?? '') !== ''): ?>
<script defer src="{{ $umami['src'] }}" data-website-id="{{ $umami['website_id'] }}"<?php if (($umami['domains'] ?? '') !== ''): ?> data-domains="{{ $umami['domains'] }}"<?php endif; ?> data-do-not-track="true"></script>
<?php endif; ?>
</head>
<body>
<header class="top">
  <div class="top-in">
    <a class="brand" href="{{ route('home', [], false) }}">
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 21h18"/><path d="M5 21V10"/><path d="M19 21V10"/><path d="M9 21V10"/><path d="M15 21V10"/><path d="M2 10 12 3l10 7z"/></svg>
      <span class="brand-name">Hlídač zákonů</span>
      <span class="brand-sub">Poslanecká sněmovna · {{ $obdobi }}. volební období</span>
    </a>
    <nav aria-label="Hlavní menu">
      <?php foreach ($nav as $key => [$href, $label]): ?>
        <a href="{{ $href }}"{!! $key === $active ? ' class="active" aria-current="page"' : '' !!}>{{ $label }}</a>
      <?php endforeach; ?>
      <a href="{{ route('rss', [], false) }}">RSS</a>
    </nav>
    <div class="sync">{!! $sync ? 'Data aktualizována ' . e(formatDate($sync, true)) : 'Data zatím nebyla načtena' !!}</div>
  </div>
</header>
<main class="wrap">
