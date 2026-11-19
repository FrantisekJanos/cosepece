<form class="search search-{{ $searchVariant ?? 'hero' }}" action="{{ route('search', [], false) }}" method="get" role="search" data-search="{{ route('search', ['index' => $searchIndexVersion], false) }}">
  <div class="search-box">
    <label for="{{ $searchId ?? 'search-main' }}" class="sr-only">Hledat tisky, zákony, pozměňovací návrhy a témata</label>
    <input id="{{ $searchId ?? 'search-main' }}" name="q" type="search" value="{{ $searchQuery ?? '' }}" placeholder="Tisk, zákon, téma nebo poslanec" autocomplete="off" spellcheck="false"
      role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="{{ $searchId ?? 'search-main' }}-list">
    <ul class="suggest" id="{{ $searchId ?? 'search-main' }}-list" role="listbox" aria-label="Návrhy" hidden></ul>
  </div>
  <button type="submit">Hledat</button>
</form>
