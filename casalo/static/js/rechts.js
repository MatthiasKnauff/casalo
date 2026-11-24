/* Casalo – Bereich rechts mit Detailkarten.
   Klick auf eine Zeile mit data-karte="/karte/…" lädt die Karte (htmx) in den Bereich rechts.
   Zuklappen lässt einen schmalen Reiter „Details“ stehen; der Zustand bleibt im Browser gemerkt. */
(() => {
  const C = window.Casalo;
  const bereich = () => document.getElementById('rechts');
  const offenMerk = (v) => { try { localStorage.setItem('casalo.rechts', v ? '1' : '0'); } catch { /* egal */ } };
  const offenGemerkt = () => { try { return localStorage.getItem('casalo.rechts') !== '0'; } catch { return true; } };

  C.rechtsAuf = (auf) => {
    const b = bereich(); if (!b) return;
    b.classList.toggle('zu', !auf);
    document.body.classList.toggle('rechts-offen', auf);
    offenMerk(auf);
  };
  C.karte = (url, zeile) => {
    const b = bereich(); if (!b) { location.href = url.replace('/karte/', '/seite/'); return; }
    document.querySelectorAll('tr.gewaehlt').forEach((r) => r.classList.remove('gewaehlt'));
    if (zeile) zeile.classList.add('gewaehlt');
    C.rechtsAuf(true);
    htmx.ajax('GET', url, { target: '#rechts-inhalt', swap: 'innerHTML' });
    try { sessionStorage.setItem('casalo.karte.' + location.pathname, url); } catch { /* egal */ }
  };

  document.addEventListener('click', (e) => {
    if (e.target.closest('#rechts-zu')) return C.rechtsAuf(false);
    if (e.target.closest('#rechts-reiter')) return C.rechtsAuf(true);
    const r = e.target.closest('tr[data-karte]');
    if (!r || e.target.closest('a,button,input,select,textarea,label,form,.edit,.edit-offen')) return;
    C.karte(r.dataset.karte, r);
  });
  document.addEventListener('keydown', (e) => {
    const r = e.target.closest && e.target.closest('tr[data-karte]');
    if (r && e.target === r && e.key === 'Enter') C.karte(r.dataset.karte, r);
  });
  document.addEventListener('DOMContentLoaded', () => {
    const b = bereich(); if (!b) return;
    document.querySelectorAll('tr[data-karte]').forEach((r) => { r.tabIndex = 0; });
    let zuletzt = null;
    try { zuletzt = sessionStorage.getItem('casalo.karte.' + location.pathname); } catch { /* egal */ }
    if (zuletzt && document.querySelector(`tr[data-karte="${CSS.escape(zuletzt)}"]`)) C.karte(zuletzt, document.querySelector(`tr[data-karte="${CSS.escape(zuletzt)}"]`));
    else C.rechtsAuf(offenGemerkt() && innerWidth > 1100);
  });
  document.addEventListener('htmx:load', (e) => { e.target.querySelectorAll?.('tr[data-karte]').forEach((r) => { r.tabIndex = 0; }); });
})();
