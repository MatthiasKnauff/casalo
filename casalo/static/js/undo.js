/* Casalo – Rückgängig / Wiederholen.
   Der Server meldet jede Änderung, die sich zurücknehmen lässt, per HX-Trigger:
     {"casalo-schritt": {"text": "MEA W 01: 62 → 63", "url": "/feld/einheit/5/mea", "alt": "62", "neu": "63", "ziel": "#z-einheit-5-mea"}}
   Zurück schickt den alten Wert an dieselbe Adresse (mit rueck=1, damit kein neuer Schritt entsteht), Wieder den neuen.
   Bis zu 50 Schritte je geöffneter Seite; Strg+Z / Strg+Y außerhalb von Eingabefeldern. */
(() => {
  const C = window.Casalo;
  const S = { zurueck: [], wieder: [] };
  const MAX = 50;

  function knoepfe() {
    const z = document.getElementById('undo-z'), w = document.getElementById('undo-w');
    if (!z) return;
    const oben = S.zurueck[S.zurueck.length - 1], wo = S.wieder[S.wieder.length - 1];
    z.disabled = !oben; w.disabled = !wo;
    z.title = oben ? 'Rückgängig: ' + oben.text + ' (Strg+Z)' : 'Nichts zum Zurücknehmen';
    w.title = wo ? 'Wiederholen: ' + wo.text + ' (Strg+Y)' : 'Nichts zum Wiederholen';
    const n = document.getElementById('undo-n');
    if (n) n.textContent = S.zurueck.length ? String(S.zurueck.length) : '';
  }

  function senden(s, wert) {
    return htmx.ajax('POST', s.url, { target: s.ziel, swap: 'outerHTML', values: { wert, rueck: '1' } });
  }

  C.zurueck = async () => {
    const s = S.zurueck.pop(); if (!s) return;
    await senden(s, s.alt);
    S.wieder.push(s); knoepfe();
    C.meldung('Zurückgenommen: ' + s.text);
  };
  C.wieder = async () => {
    const s = S.wieder.pop(); if (!s) return;
    await senden(s, s.neu);
    S.zurueck.push(s); knoepfe();
    C.meldung('Wiederholt: ' + s.text);
  };
  C.verlauf = (x, y) => C.menue(x, y, S.zurueck.slice(-6).reverse().map((s, i) => ({
    t: s.text, ic: '↶', zusatz: i ? `${i + 1} Schritte` : 'letzter',
    los: async () => { for (let k = 0; k <= i; k++) await C.zurueck(); },
  })), { breit: true });

  document.addEventListener('casalo-schritt', (e) => {
    S.zurueck.push(e.detail); if (S.zurueck.length > MAX) S.zurueck.shift();
    S.wieder = [];
    knoepfe();
    C.meldung('Gespeichert: ' + e.detail.text, 'ok', { text: 'Rückgängig', los: C.zurueck });
  });

  document.addEventListener('keydown', (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.target.closest('input,textarea,[contenteditable]')) return;
    if (e.key === 'z' && !e.shiftKey) { e.preventDefault(); C.zurueck(); }
    else if (e.key === 'y' || (e.key === 'z' && e.shiftKey)) { e.preventDefault(); C.wieder(); }
  });
  document.addEventListener('click', (e) => {
    if (e.target.closest('#undo-z')) C.zurueck();
    else if (e.target.closest('#undo-w')) C.wieder();
    else if (e.target.closest('#undo-n')) { const r = e.target.getBoundingClientRect(); C.verlauf(r.left, r.bottom + 4); }
  });
  document.addEventListener('DOMContentLoaded', knoepfe);
})();
