/* Casalo – Bereich rechts mit Detailkarten.
   Klick auf eine Zeile mit data-karte="/karte/…" lädt die Karte (htmx) in den Bereich rechts.
   Der Bereich kann
     dock    – rechts neben der Seite stehen (Breite am Griff ziehen, Doppelklick: Standard), ⟩ klappt ihn an den Rand;
     frei    – als freies Fenster über der Seite schweben (an der Kopfzeile verschieben, an der Ecke ziehen), ⇲ dockt wieder an;
     fenster – in einem eigenen Browserfenster stehen (/details, z. B. auf dem zweiten Bildschirm); Karten landen dann dort.
   Modus, Breite, Lage und Größe bleiben im Browser gemerkt. */
(() => {
  const C = window.Casalo;
  const STANDARD_B = 380, MIN_B = 320;
  const lies = (k, d) => { try { const v = localStorage.getItem('casalo.rechts.' + k); return v == null ? d : JSON.parse(v); } catch { return d; } };
  const merk = (k, v) => { try { localStorage.setItem('casalo.rechts.' + k, JSON.stringify(v)); } catch { /* ohne Speicher: nur für diese Seite */ } };
  const bereich = () => document.getElementById('rechts');
  let fenster = null;               // eigenes Browserfenster (Modus „fenster“)

  const breiteSetzen = (b) => document.documentElement.style.setProperty('--rb', Math.max(MIN_B, Math.min(b, innerWidth - 480)) + 'px');

  C.rechtsAuf = (auf) => {
    const b = bereich(); if (!b) return;
    b.classList.toggle('zu', !auf);
    document.body.classList.toggle('rechts-offen', auf);
    merk('offen', auf);
  };

  function freiLage() {
    const b = bereich(), q = lies('frei', { x: innerWidth - 460, y: 90, w: 420, h: Math.min(640, innerHeight - 120) });
    const x = Math.max(0, Math.min(q.x, innerWidth - 120)), y = Math.max(0, Math.min(q.y, innerHeight - 60));
    Object.assign(b.style, { left: x + 'px', top: y + 'px', width: q.w + 'px', height: q.h + 'px' });
  }

  function fensterOeffnen() {
    const q = lies('fenster', { w: 480, h: 760 });
    fenster = window.open('/details', 'casalo_details', `popup=yes,width=${q.w},height=${q.h}${q.x != null ? `,left=${q.x},top=${q.y}` : ''}`);
    if (!fenster) { C.meldung('Der Browser hat das Fenster blockiert – Pop-ups für Casalo erlauben.', 'fehler'); return false; }
    const zuletzt = lies('karte', null);
    if (zuletzt) fenster.addEventListener('load', () => fenster.CasaloDetails && fenster.CasaloDetails.laden(zuletzt), { once: true });
    return true;
  }

  C.rechtsModus = (modus) => {
    const b = bereich(); if (!b) return;
    const vorher = b.classList.contains('weg') ? 'fenster' : '';
    if (modus === 'fenster' && !(fenster && !fenster.closed) && !fensterOeffnen()) modus = 'dock';
    if (modus !== 'fenster' && fenster && !fenster.closed) { const f = fenster; fenster = null; f.close(); }
    b.classList.remove('frei', 'weg');
    ['left', 'top', 'width', 'height'].forEach((k) => { b.style[k] = ''; });
    if (modus === 'frei') { b.classList.add('frei'); freiLage(); C.rechtsAuf(true); }
    if (modus === 'fenster') b.classList.add('weg');
    document.body.classList.toggle('rechts-dock', modus === 'dock');
    merk('modus', modus);
    if (vorher === 'fenster' && modus !== 'fenster' && lies('karte', null)) {   // Karte aus dem Fenster zurückholen
      C.rechtsAuf(true);
      htmx.ajax('GET', lies('karte', null), { target: '#rechts-inhalt', swap: 'innerHTML' });
    }
  };
  C.fensterZu = () => { setTimeout(() => { if (fenster && fenster.closed) { fenster = null; C.rechtsModus('dock'); } }, 300); };

  C.karte = (url, zeile) => {
    const b = bereich(); if (!b) return;
    document.querySelectorAll('tr.gewaehlt').forEach((r) => r.classList.remove('gewaehlt'));
    if (zeile) zeile.classList.add('gewaehlt');
    merk('karte', url);
    try { sessionStorage.setItem('casalo.karte.' + location.pathname, url); } catch { /* egal */ }
    if (lies('modus', 'dock') === 'fenster') {
      if (!fenster || fenster.closed) fensterOeffnen();           // nach Neuladen der Seite: Fenster wiederfinden
      if (fenster && fenster.CasaloDetails) { fenster.CasaloDetails.laden(url); return; }
      if (fenster) { fenster.addEventListener('load', () => fenster.CasaloDetails.laden(url), { once: true }); return; }
    }
    C.rechtsAuf(true);
    htmx.ajax('GET', url, { target: '#rechts-inhalt', swap: 'innerHTML' });
  };

  // freies Fenster: an der Kopfzeile verschieben; Größe (CSS resize) beim Loslassen merken
  document.addEventListener('pointerdown', (e) => {
    const b = bereich();
    const kopf = e.target.closest('#rechts.frei .rechts-kopf');
    if (kopf && !e.target.closest('button')) {
      const r = b.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
      const zieh = (m) => { b.style.left = Math.max(0, Math.min(m.clientX - dx, innerWidth - 80)) + 'px'; b.style.top = Math.max(0, Math.min(m.clientY - dy, innerHeight - 40)) + 'px'; };
      const los = () => { removeEventListener('pointermove', zieh); const q = b.getBoundingClientRect(); merk('frei', { x: q.left, y: q.top, w: q.width, h: q.height }); };
      addEventListener('pointermove', zieh); addEventListener('pointerup', los, { once: true });
      e.preventDefault();
      return;
    }
    if (e.target.id === 'rechts-griff') {
      const zieh = (m) => breiteSetzen(innerWidth - m.clientX);
      const los = () => { removeEventListener('pointermove', zieh); merk('breite', parseInt(getComputedStyle(document.documentElement).getPropertyValue('--rb'), 10)); };
      addEventListener('pointermove', zieh); addEventListener('pointerup', los, { once: true });
      e.preventDefault();
    }
  });
  document.addEventListener('pointerup', () => {
    const b = bereich();
    if (b && b.classList.contains('frei')) { const q = b.getBoundingClientRect(); merk('frei', { x: q.left, y: q.top, w: q.width, h: q.height }); }
  });
  document.addEventListener('dblclick', (e) => { if (e.target.id === 'rechts-griff') { breiteSetzen(STANDARD_B); merk('breite', STANDARD_B); } });

  document.addEventListener('click', (e) => {
    if (e.target.closest('#rechts-zu')) return C.rechtsAuf(false);
    if (e.target.closest('#rechts-reiter')) return C.rechtsAuf(true);
    const m = e.target.closest('[data-rechts]');
    if (m) return C.rechtsModus(m.dataset.rechts);
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
    breiteSetzen(lies('breite', STANDARD_B));
    document.querySelectorAll('tr[data-karte]').forEach((r) => { r.tabIndex = 0; });
    const modus = lies('modus', 'dock');
    if (modus === 'fenster') {                                   // Fenster ist evtl. noch offen: unter seinem Namen wiederfinden, ohne es neu zu laden
      fenster = window.open('', 'casalo_details');
      if (fenster && !fenster.CasaloDetails) { fenster.close(); fenster = null; }
      C.rechtsModus(fenster ? 'fenster' : 'dock');
    } else {
      C.rechtsModus(modus);
    }
    let zuletzt = null;
    try { zuletzt = sessionStorage.getItem('casalo.karte.' + location.pathname); } catch { /* egal */ }
    const zeile = zuletzt && document.querySelector(`tr[data-karte="${CSS.escape(zuletzt)}"]`);
    if (zeile) C.karte(zuletzt, zeile);
    else if (lies('modus', 'dock') === 'dock') C.rechtsAuf(lies('offen', true) && innerWidth > 1100);
  });
  document.addEventListener('htmx:load', (e) => { e.target.querySelectorAll?.('tr[data-karte]').forEach((r) => { r.tabIndex = 0; }); });
})();
