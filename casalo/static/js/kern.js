/* Casalo – Kern-Baustein: Meldungen, Warte-Hinweis, Dialog, Kontextmenü, Auswahl, Anbindung an htmx.
   Die Logik liegt auf dem Server; hier nur Bedienung. Keine Systemdialoge (confirm/alert/native Auswahllisten). */
(() => {
  const C = (window.Casalo = window.Casalo || {});
  const esc = (C.esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])));
  const csrf = () => document.querySelector('meta[name=csrf]')?.content || '';

  /* ---------------------------------------------------------------- Meldung unten rechts */
  C.meldung = (text, art = 'ok', knopf) => {
    let box = document.getElementById('meldungen');
    if (!box) { box = document.createElement('div'); box.id = 'meldungen'; box.setAttribute('aria-live', 'polite'); document.body.append(box); }
    const m = document.createElement('div');
    m.className = 'toast ' + art;
    m.innerHTML = `<span>${esc(text)}</span>`;
    if (knopf) {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'klein zweit'; b.textContent = knopf.text;
      b.onclick = () => { m.remove(); knopf.los(); }; m.append(b);
    }
    box.append(m);
    while (box.children.length > 3) box.firstElementChild.remove();   // höchstens drei Meldungen gleichzeitig
    setTimeout(() => m.classList.add('weg'), 5000);
    setTimeout(() => m.remove(), 5600);
  };

  /* ---------------------------------------------------------------- Warte-Hinweis ab 1,5 s */
  let laeuft = 0, uhr = null;
  const warteEl = () => document.getElementById('warte') || Object.assign(document.body.appendChild(document.createElement('div')), { id: 'warte', textContent: 'Einen Moment …' });
  C.warteStart = () => { if (laeuft++ === 0) uhr = setTimeout(() => warteEl().classList.add('an'), 1500); };
  C.warteEnde = () => { if (--laeuft <= 0) { laeuft = 0; clearTimeout(uhr); document.getElementById('warte')?.classList.remove('an'); } };
  document.addEventListener('htmx:beforeRequest', C.warteStart);
  document.addEventListener('htmx:afterRequest', C.warteEnde);
  // normale Formulare und Links: Hinweis, falls die nächste Seite auf sich warten lässt
  document.addEventListener('submit', (e) => { if (!e.defaultPrevented && !e.target.hasAttribute('hx-post')) C.warteStart(); });
  window.addEventListener('pageshow', () => { laeuft = 1; C.warteEnde(); });

  /* ---------------------------------------------------------------- Dialog (nur über Knöpfe zu schließen) */
  C.dialog = ({ titel, text, ok = 'OK', abbrechen = 'Abbrechen', gefahr = false, inhalt = '' }) => new Promise((fertig) => {
    const h = document.createElement('div');
    h.className = 'dlg-hg';
    h.innerHTML = `<div class="dlg" role="dialog" aria-modal="true" aria-labelledby="dlg-t">
      <h2 id="dlg-t">${esc(titel || 'Bitte bestätigen')}</h2>${text ? `<p>${esc(text)}</p>` : ''}${inhalt}
      <div class="dlg-knoepfe"><button type="button" class="zweit" data-a="0">${esc(abbrechen)}</button>
      <button type="button" class="${gefahr ? 'gefahr-voll' : ''}" data-a="1">${esc(ok)}</button></div></div>`;
    const zu = (wert) => { h.remove(); document.removeEventListener('keydown', taste, true); fertig(wert); };
    const taste = (e) => { if (e.key === 'Escape') { e.preventDefault(); zu(false); } };
    h.addEventListener('click', (e) => { const a = e.target.closest('[data-a]'); if (a) zu(a.dataset.a === '1' ? (h.querySelector('form') ? new FormData(h.querySelector('form')) : true) : false); });
    document.addEventListener('keydown', taste, true);
    document.body.append(h);
    (h.querySelector('input,textarea') || h.querySelector('[data-a="1"]')).focus();
  });

  // Formulare mit data-frage fragen vorher nach (statt confirm)
  document.addEventListener('submit', async (e) => {
    const f = e.target;
    const frage = (e.submitter && e.submitter.dataset.frage) || f.dataset.frage;
    if (!frage || f._bestaetigt) return;
    e.preventDefault();
    if (await C.dialog({ titel: frage, ok: (e.submitter && e.submitter.dataset.ok) || f.dataset.ok || 'Ja', gefahr: true })) {
      f._bestaetigt = true;
      if (e.submitter && e.submitter.name) { const i = document.createElement('input'); i.type = 'hidden'; i.name = e.submitter.name; i.value = e.submitter.value; f.append(i); }
      f.requestSubmit ? f.requestSubmit() : f.submit();
      f._bestaetigt = false;
    }
  }, true);
  // htmx: hx-confirm über den eigenen Dialog
  document.addEventListener('htmx:confirm', (e) => {
    if (!e.detail.question) return;
    e.preventDefault();
    C.dialog({ titel: e.detail.question, ok: 'Ja', gefahr: true }).then((ja) => ja && e.detail.issueRequest(true));
  });

  /* ---------------------------------------------------------------- Menü (Kontextmenü, Auswahl) – höchstens 6 Punkte */
  C.menue = (x, y, punkte, opts = {}) => {
    document.querySelectorAll('.menue').forEach((m) => m.remove());
    const m = document.createElement('div');
    m.className = 'menue' + (opts.breit ? ' breit' : '');
    m.setAttribute('role', 'menu');
    if (opts.suche) m.innerHTML = '<input class="menue-suche" placeholder="Suchen …" aria-label="Suchen">';
    const liste = document.createElement('div'); liste.className = 'menue-liste'; m.append(liste);
    const zeichne = (q = '') => {
      liste.innerHTML = '';
      punkte.filter((p) => !q || p.t.toLowerCase().includes(q.toLowerCase())).slice(0, opts.suche ? 50 : 6).forEach((p) => {
        const b = document.createElement(p.href ? 'a' : 'button');
        if (p.href) b.href = p.href; else b.type = 'button';
        b.className = 'menue-p' + (p.an ? ' an' : '') + (p.gefahr ? ' gefahr' : '');
        b.setAttribute('role', 'menuitem');
        b.innerHTML = `${p.ic ? `<span class="ic">${esc(p.ic)}</span>` : ''}${esc(p.t)}${p.zusatz ? `<span class="zusatz">${esc(p.zusatz)}</span>` : ''}`;
        if (!p.href) b.onclick = () => { m.remove(); p.los && p.los(); };
        liste.append(b);
      });
      if (!liste.children.length) liste.innerHTML = '<div class="leer">Nichts gefunden</div>';
    };
    zeichne();
    document.body.append(m);
    const r = m.getBoundingClientRect();
    m.style.left = Math.max(8, Math.min(x, innerWidth - r.width - 8)) + 'px';
    m.style.top = Math.max(8, Math.min(y, innerHeight - r.height - 8)) + 'px';
    const s = m.querySelector('.menue-suche');
    if (s) { s.oninput = () => zeichne(s.value); s.focus(); } else m.querySelector('.menue-p')?.focus();
    setTimeout(() => document.addEventListener('mousedown', function weg(e) { if (!m.contains(e.target)) { m.remove(); document.removeEventListener('mousedown', weg); } }), 0);
    m.addEventListener('keydown', (e) => {
      const ps = [...m.querySelectorAll('.menue-p')], i = ps.indexOf(document.activeElement);
      if (e.key === 'Escape') m.remove();
      else if (e.key === 'ArrowDown') { e.preventDefault(); (ps[i + 1] || ps[0])?.focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); (ps[i - 1] || ps[ps.length - 1])?.focus(); }
      else if (e.key === 'Enter' && s && document.activeElement === s) { e.preventDefault(); ps[0]?.click(); }
    });
    return m;
  };

  // Auswahl-Knöpfe statt nativer Auswahllisten: <button data-wahl='[{"t":"…","href":"…"}]'>
  document.addEventListener('click', (e) => {
    const b = e.target.closest('[data-wahl]');
    if (!b) return;
    e.preventDefault();
    const r = b.getBoundingClientRect();
    C.menue(r.left, r.bottom + 4, JSON.parse(b.dataset.wahl), { suche: b.hasAttribute('data-suche'), breit: true });
  });

  /* ---------------------------------------------------------------- htmx: Sitzungsschlüssel, Fehler */
  document.addEventListener('htmx:configRequest', (e) => { e.detail.headers['X-CSRF'] = csrf(); });
  document.addEventListener('htmx:responseError', (e) => {
    C.meldung(e.detail.xhr.status === 400 ? 'Sitzung abgelaufen – bitte die Seite neu laden.' : 'Das hat nicht geklappt (' + e.detail.xhr.status + ').', 'fehler');
  });
  document.addEventListener('htmx:sendError', () => C.meldung('Keine Verbindung zum Server.', 'fehler'));
  // Meldungen vom Server (HX-Trigger: {"casalo-meldung": {"text": …, "art": …}})
  document.addEventListener('casalo-meldung', (e) => C.meldung(e.detail.text, e.detail.art || 'ok'));

  // POST-Formulare ohne htmx bekommen den Sitzungsschlüssel
  document.addEventListener('submit', (e) => {
    const f = e.target;
    if ((f.method || '').toLowerCase() !== 'post' || f.querySelector('input[name=_csrf]')) return;
    const i = document.createElement('input'); i.type = 'hidden'; i.name = '_csrf'; i.value = csrf(); f.append(i);
  }, true);
})();
