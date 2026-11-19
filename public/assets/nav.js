(function () {
  var toggle = document.getElementById('nav-toggle');
  var menu = document.getElementById('site-menu');
  if (!toggle || !menu) {
    return;
  }
  var mobile = window.matchMedia('(max-width: 760px)');

  function setOpen(open) {
    menu.classList.toggle('open', open);
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  }

  toggle.addEventListener('click', function () {
    setOpen(toggle.getAttribute('aria-expanded') !== 'true');
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && toggle.getAttribute('aria-expanded') === 'true') {
      setOpen(false);
      toggle.focus();
    }
  });

  document.addEventListener('click', function (event) {
    if (toggle.getAttribute('aria-expanded') === 'true' && !menu.contains(event.target) && !toggle.contains(event.target)) {
      setOpen(false);
    }
  });

  var onChange = function () {
    if (!mobile.matches) {
      setOpen(false);
    }
  };
  if (mobile.addEventListener) {
    mobile.addEventListener('change', onChange);
  } else {
    mobile.addListener(onChange);
  }
})();
