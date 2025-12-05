// theme.js – shared across all pages

(function () {
  const FONT_KEY = 'gg-font-size';
  const THEME_KEY = 'gg-theme';

  function applyFontSize(value) {
    document.body.classList.remove('font-small', 'font-large');
    if (value === 'small') {
      document.body.classList.add('font-small');
    } else if (value === 'large') {
      document.body.classList.add('font-large');
    }
  }

  function applyTheme(value) {
    document.body.classList.remove('theme-dark');
    if (value === 'dark') {
      document.body.classList.add('theme-dark');
    }
  }

  function loadPreferences() {
    const storedFont = localStorage.getItem(FONT_KEY) || 'medium';
    const storedTheme = localStorage.getItem(THEME_KEY) || 'light';

    applyFontSize(storedFont);
    applyTheme(storedTheme);

    // If we are on settings page, sync the radio buttons
    const fontRadios = document.querySelectorAll('input[name="font-size"]');
    if (fontRadios.length) {
      fontRadios.forEach(r => {
        r.checked = (r.value === storedFont);
      });
    }

    const themeRadios = document.querySelectorAll('input[name="theme"]');
    if (themeRadios.length) {
      themeRadios.forEach(r => {
        r.checked = (r.value === storedTheme);
      });
    }
  }

  function wireSettingsPage() {
    const fontRadios = document.querySelectorAll('input[name="font-size"]');
    fontRadios.forEach(radio => {
      radio.addEventListener('change', () => {
        const val = radio.value;
        localStorage.setItem(FONT_KEY, val);
        applyFontSize(val);
      });
    });

    const themeRadios = document.querySelectorAll('input[name="theme"]');
    themeRadios.forEach(radio => {
      radio.addEventListener('change', () => {
        const val = radio.value;
        localStorage.setItem(THEME_KEY, val);
        applyTheme(val);
      });
    });
  }

  document.addEventListener('DOMContentLoaded', () => {
    loadPreferences();
    wireSettingsPage();
  });
})();
