(() => {
  const nav = document.querySelector('.reader-sidebar nav');
  const current = nav && nav.querySelector('.is-selected');
  if (current && nav.scrollWidth > nav.clientWidth) {
    nav.scrollLeft = Math.max(0, current.offsetLeft - nav.offsetLeft - 16);
  }

  const viewer = document.querySelector('.word-viewer');
  if (!viewer) return;
  const status = viewer.querySelector('.word-status');
  const viewport = viewer.querySelector('.word-viewport');
  const scaler = viewer.querySelector('.word-scaler');
  const pages = viewer.querySelector('.word-pages');
  const controls = viewer.querySelector('.word-controls');
  const zoomOutput = viewer.querySelector('.word-zoom');
  const pageLabel = viewer.querySelectorAll('.word-page b');
  const MIN = 0.4;
  const MAX = 2.5;
  const STEP = 0.1;
  let naturalWidth = 0;
  let scale = 1;
  let fitMode = true;
  let sheets = [];

  const clamp = (value) => Math.min(MAX, Math.max(MIN, value));
  const fitScale = () => clamp(Math.min(1.25, (viewport.clientWidth - 2) / naturalWidth));

  function apply() {
    pages.style.width = naturalWidth + 'px';
    pages.style.transform = `scale(${scale})`;
    scaler.style.width = Math.round(naturalWidth * scale) + 'px';
    scaler.style.height = Math.round(pages.offsetHeight * scale) + 'px';
    zoomOutput.textContent = Math.round(scale * 100) + ' %';
    updatePage();
  }

  function setScale(value, fit) {
    const probe = viewer.querySelector('.word-toolbar').getBoundingClientRect().bottom + 24;
    const inside = scaler.getBoundingClientRect().top < probe;
    const anchor = inside ? (probe - scaler.getBoundingClientRect().top) / scale : 0;
    fitMode = fit;
    scale = clamp(value);
    apply();
    if (inside) window.scrollBy(0, scaler.getBoundingClientRect().top + anchor * scale - probe);
  }

  function measure() {
    pages.style.transform = '';
    pages.style.width = 'max-content';
    naturalWidth = Math.ceil(pages.getBoundingClientRect().width);
  }

  function sheetTop(index) {
    return sheets[index].getBoundingClientRect().top - scaler.getBoundingClientRect().top;
  }

  function currentIndex() {
    const probe = viewer.querySelector('.word-toolbar').getBoundingClientRect().bottom + 24;
    let found = 0;
    sheets.forEach((sheet, index) => {
      if (sheet.getBoundingClientRect().top <= probe) found = index;
    });
    return found;
  }

  function scrollToPage(index, smooth = true) {
    const toolbarHeight = viewer.querySelector('.word-toolbar').offsetHeight;
    const target = scaler.getBoundingClientRect().top + window.scrollY + sheetTop(index) - toolbarHeight - 8;
    window.scrollTo({ top: Math.max(0, target), behavior: smooth ? 'smooth' : 'auto' });
  }

  function updatePage() {
    if (!sheets.length) return;
    pageLabel[0].textContent = String(currentIndex() + 1);
  }

  function findQuote(quote) {
    const normalize = (text) => text.replace(/\s+/g, ' ').trim().toLowerCase();
    const needle = normalize(quote);
    const head = needle.slice(0, 60);
    let best = null;
    pages.querySelectorAll('article p, article li, article td').forEach((node) => {
      if (best) return;
      const text = normalize(node.textContent || '');
      if (text.length < 20) return;
      if (text.includes(head) || (text.length > 40 && needle.includes(text))) best = node;
    });
    return best;
  }

  function revealQuote(quote) {
    pages.querySelectorAll('.quote-hit').forEach((node) => node.classList.remove('quote-hit'));
    const hit = findQuote(quote);
    if (!hit) return false;
    hit.classList.add('quote-hit');
    const toolbarHeight = viewer.querySelector('.word-toolbar').offsetHeight;
    window.scrollTo({ top: Math.max(0, hit.getBoundingClientRect().top + window.scrollY - toolbarHeight - 120), behavior: 'smooth' });
    return true;
  }

  function revealLinkedQuote() {
    const quote = new URLSearchParams(location.hash.slice(1)).get('citace');
    if (!quote) return;
    if (!revealQuote(quote)) {
      status.hidden = false;
      status.textContent = 'Citaci se v náhledu nepodařilo najít. Dokument je otevřený; můžete použít hledání v prohlížeči.';
    }
  }

  function bindControls() {
    controls.hidden = false;
    viewer.querySelector('[data-zoom="out"]').addEventListener('click', () => setScale(scale - STEP, false));
    viewer.querySelector('[data-zoom="in"]').addEventListener('click', () => setScale(scale + STEP, false));
    viewer.querySelector('[data-zoom="fit"]').addEventListener('click', () => setScale(fitScale(), true));
    viewer.querySelector('[data-page="prev"]').addEventListener('click', () => {
      const index = currentIndex();
      const top = sheets[index].getBoundingClientRect().top;
      scrollToPage(top < viewer.querySelector('.word-toolbar').getBoundingClientRect().bottom - 4 ? index : Math.max(0, index - 1));
    });
    viewer.querySelector('[data-page="next"]').addEventListener('click', () => scrollToPage(Math.min(sheets.length - 1, currentIndex() + 1)));
    viewport.addEventListener('keydown', (event) => {
      if (event.target !== viewport || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === '+' || event.key === '=') { event.preventDefault(); setScale(scale + STEP, false); }
      if (event.key === '-') { event.preventDefault(); setScale(scale - STEP, false); }
      if (event.key === '0') { event.preventDefault(); setScale(fitScale(), true); }
    });
    let ticking = false;
    window.addEventListener('scroll', () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => { ticking = false; updatePage(); });
    }, { passive: true });
    new ResizeObserver(() => { if (fitMode) setScale(fitScale(), true); }).observe(viewport);

    document.querySelectorAll('.reader-find').forEach((button) => {
      button.hidden = false;
      button.addEventListener('click', () => {
        if (!revealQuote(button.dataset.quote || '')) {
          button.textContent = 'Pasáž se v zobrazení nepodařilo najít';
          button.disabled = true;
        }
      });
    });
  }

  (async () => {
    try {
      if (typeof docx === 'undefined') throw new Error('Čtečka není načtena');
      const response = await fetch(viewer.dataset.source);
      if (!response.ok) throw new Error('Word není dostupný');
      await docx.renderAsync(await response.blob(), pages, null, {
        inWrapper: true,
        breakPages: true,
        ignoreLastRenderedPageBreak: false,
        renderHeaders: true,
        renderFooters: true,
        renderFootnotes: true,
        renderEndnotes: true,
        renderAltChunks: false,
      });
      status.hidden = true;
      measure();
      sheets = Array.from(pages.querySelectorAll('section.docx'));
      pageLabel[1].textContent = String(sheets.length);
      bindControls();
      setScale(fitScale(), true);
      await document.fonts.ready;
      measure();
      if (fitMode) setScale(fitScale(), true);
      requestAnimationFrame(revealLinkedQuote);
      window.addEventListener('hashchange', revealLinkedQuote);
    } catch (error) {
      status.textContent = 'Word se nepodařilo zobrazit. Otevřete originální soubor odkazem „Stáhnout Word“.';
    }
  })();
})();
