// Hlášení chyby ve vysvětlení pozměňovacího návrhu: praporek u vysvětlení otevře formulář jako vrstvu
// nad stránkou (prvek dialog) a odešle ho na pozadí. Bez tohoto skriptu vede praporek na stránku hlaseni.php.
(function () {
  'use strict';
  var dialog = document.getElementById('report-dialog');
  if (!dialog || typeof dialog.showModal !== 'function' || !window.fetch || !window.FormData) {
    return;
  }
  var form = dialog.querySelector('form');
  var body = dialog.querySelector('[data-report-body]');
  var done = dialog.querySelector('[data-report-done]');
  var error = dialog.querySelector('.report-error');
  var context = dialog.querySelector('[data-report-context]');
  var submit = form.querySelector('button[type="submit"]');
  var opener = null;

  function showError(text) {
    error.textContent = text;
    error.hidden = text === '';
  }

  document.addEventListener('click', function (ev) {
    var link = ev.target.closest ? ev.target.closest('a.report-link') : null;
    if (!link || ev.ctrlKey || ev.metaKey || ev.shiftKey) {
      return;
    }
    ev.preventDefault();
    opener = link;
    form.reset();
    form.elements.d.value = link.getAttribute('data-report');
    context.textContent = link.getAttribute('data-report-label');
    showError('');
    submit.disabled = false;
    body.hidden = false;
    done.hidden = true;
    dialog.showModal();
  });

  dialog.addEventListener('click', function (ev) {
    // klik na tlačítko zavření nebo mimo okno vrstvy (na ztmavené pozadí)
    if (ev.target === dialog || (ev.target.closest && ev.target.closest('[data-report-close]'))) {
      dialog.close();
    }
  });
  dialog.addEventListener('close', function () {
    if (opener) {
      opener.focus();
    }
  });

  form.addEventListener('submit', function (ev) {
    ev.preventDefault();
    if (!form.querySelector('input[name="cast"]:checked')) {
      showError('Vyberte, která část vysvětlení nesedí.');
      return;
    }
    showError('');
    submit.disabled = true;
    fetch(form.action, { method: 'POST', body: new FormData(form), headers: { Accept: 'application/json' }, credentials: 'same-origin' })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) { return { ok: res.ok, data: data }; });
      })
      .then(function (res) {
        submit.disabled = false;
        if (res.ok && res.data.ok) {
          body.hidden = true;
          done.hidden = false;
          var close = done.querySelector('[data-report-close]');
          if (close) {
            close.focus();
          }
        } else {
          showError(res.data.error || 'Hlášení se nepodařilo odeslat. Zkuste to znovu.');
        }
      })
      .catch(function () {
        submit.disabled = false;
        showError('Hlášení se nepodařilo odeslat. Zkontrolujte připojení a zkuste to znovu.');
      });
  });
  form.addEventListener('change', function () { showError(''); });
})();
