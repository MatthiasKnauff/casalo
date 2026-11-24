/* Casalo – Tabellen: sortieren, filtern, Spalten ein-/ausblenden und ordnen, Kontextmenü je Zeile,
   Tastatur beim Bearbeiten in der Zelle (Enter speichert, Esc verwirft, Tab springt weiter).
   Gilt für jede <table class="tab">. Spalten-Einstellungen merkt sich der Browser je Tabelle (data-tab="schlüssel").
   Zeilen mit class="summe" bleiben unten und werden weder sortiert noch gefiltert.
   Zeile mit data-karte="/karte/…" öffnet die Detailkarte rechts; data-aktionen='[{"t":"…","url":"…","frage":"…"}]'
   ergänzt das Kontextmenü (höchstens 6 Punkte). */
(() => {
  const C = window.Casalo;
  const lies = (k) => { try { return JSON.parse(localStorage.getItem('casalo.sp.' + k) || '{}'); } catch { return {}; } };
  const merk = (k, v) => { try { localStorage.setItem('casalo.sp.' + k, JSON.stringify(v)); } catch { /* egal */ } };

  const zahl = (t) => {
    if (/^-?\d+(\.\d+)?$/.test(t)) return parseFloat(t);   // data-sort vom Server
    const s = t.replace(/[€\s m²%]/g, '').replace('−', '-');
    let m = s.match(/^(\d{1,2})\.(\d{1,2})\.(\d{4})(?:(\d{1,2}):(\d{2}))?$/);
    if (m) return Date.UTC(+m[3], +m[2] - 1, +m[1], +(m[4] || 0), +(m[5] || 0));
    if (/^-?[\d.]*,?\d+$/.test(s) && /\d/.test(s)) return parseFloat(s.replace(/\./g, '').replace(',', '.'));
    return null;
  };
  const text = (td) => (td?.dataset.sort ?? td?.textContent ?? '').trim();

  function kopf(t) {
    if (!t.tHead) {
      const r = [...t.rows].find((row) => row.querySelector('th'));
      if (!r) return null;
      const th = t.createTHead(); th.append(r);
    }
    return t.tHead.rows[0];
  }
  const koerper = (t) => [...t.tBodies].flatMap((b) => [...b.rows]);
  const schluessel = (t) => t.dataset.tab || (location.pathname.replace(/\d+/g, '#') + '|' + [...kopf(t).cells].map((c) => c.textContent.trim()).join('|'));

  /* ---------------------------------------------------------------- Spalten */
  // Jede Zelle trägt ihre ursprüngliche Spalte in data-sp – auch nach Umordnen und nach dem Austausch durch htmx.
  const zellen = (row) => { [...row.cells].forEach((c, i) => { if (c.dataset.sp === undefined) c.dataset.sp = i; }); return [...row.cells]; };
  const zelle = (row, i) => [...row.cells].find((c) => +c.dataset.sp === i);
  function spaltenAnwenden(t) {
    const k = kopf(t); if (!k) return;
    const n = k.cells.length, st = lies(schluessel(t));
    const ord = (st.ord && st.ord.length === n) ? st.ord : [...Array(n).keys()];
    const aus = new Set(st.aus || (t.dataset.aus ? t.dataset.aus.split(',').map(Number) : []));
    for (const row of [k, ...koerper(t)]) {
      if (row.cells.length !== n || [...row.cells].some((c) => c.colSpan > 1)) continue;
      const cs = zellen(row);
      ord.forEach((i) => { const c = cs.find((x) => +x.dataset.sp === i); if (c) row.append(c); });
      cs.forEach((c) => { const i = +c.dataset.sp; c.hidden = aus.has(i) && i !== 0; });
    }
  }
  const merkSp = {};
  document.addEventListener('htmx:beforeSwap', (e) => {
    const td = e.detail.target;
    if (td && td.matches && td.matches('table.tab td[id]')) merkSp[td.id] = { sp: td.dataset.sp, hidden: td.hidden };
  });
  document.addEventListener('htmx:load', (e) => {
    const td = e.target, m = td && td.id && merkSp[td.id];
    if (m && td.matches('td')) { if (m.sp !== undefined) td.dataset.sp = m.sp; td.hidden = m.hidden; delete merkSp[td.id]; }
  });
  function spaltenMenue(t, x, y) {
    const k = kopf(t), key = schluessel(t);
    const namen = zellen(k).sort((a, b) => a.dataset.sp - b.dataset.sp).map((c) => c.dataset.name || c.textContent.trim() || '(ohne Namen)');
    const st = lies(key);
    let ord = (st.ord && st.ord.length === namen.length) ? [...st.ord] : namen.map((_, i) => i);
    const aus = new Set(st.aus || (t.dataset.aus ? t.dataset.aus.split(',').map(Number) : []));
    document.querySelectorAll('.menue').forEach((m) => m.remove());
    const m = document.createElement('div');
    m.className = 'menue breit spalten'; m.setAttribute('role', 'dialog'); m.setAttribute('aria-label', 'Spalten');
    const zeichne = () => {
      m.innerHTML = '<div class="menue-titel">Spalten</div>' + ord.map((i, pos) => `
        <div class="sp-z" draggable="${i ? 'true' : 'false'}" data-i="${i}" data-pos="${pos}">
          <span class="sp-griff" aria-hidden="true">${i ? '⋮⋮' : ''}</span>
          <label><input type="checkbox" ${aus.has(i) ? '' : 'checked'} ${i ? '' : 'disabled'}> ${C.esc(namen[i])}</label>
          ${i ? `<button type="button" class="sp-hoch" aria-label="nach oben" ${pos > 1 ? '' : 'disabled'}>↑</button>` : ''}
        </div>`).join('') + '<div class="sp-fuss"><button type="button" class="klein zweit sp-std">Standard</button><button type="button" class="klein sp-ok">Fertig</button></div>';
    };
    const speichern = () => { merk(key, { ord, aus: [...aus] }); spaltenAnwenden(t); };
    zeichne();
    m.addEventListener('change', (e) => {
      const i = +e.target.closest('.sp-z').dataset.i;
      e.target.checked ? aus.delete(i) : aus.add(i); speichern();
    });
    m.addEventListener('click', (e) => {
      if (e.target.closest('.sp-ok')) return m.remove();
      if (e.target.closest('.sp-std')) { try { localStorage.removeItem('casalo.sp.' + key); } catch { /* egal */ }
        ord = namen.map((_, i) => i); aus.clear(); (t.dataset.aus || '').split(',').filter(Boolean).forEach((x) => aus.add(+x)); spaltenAnwenden(t); return zeichne(); }
      const h = e.target.closest('.sp-hoch');
      if (h) { const p = +h.closest('.sp-z').dataset.pos; [ord[p - 1], ord[p]] = [ord[p], ord[p - 1]]; speichern(); zeichne(); }
    });
    let ziehe = null;
    m.addEventListener('dragstart', (e) => { ziehe = +e.target.closest('.sp-z').dataset.pos; });
    m.addEventListener('dragover', (e) => e.preventDefault());
    m.addEventListener('drop', (e) => {
      const z = e.target.closest('.sp-z'); if (!z || ziehe === null) return;
      const nach = Math.max(1, +z.dataset.pos), [w] = ord.splice(ziehe, 1); ord.splice(nach, 0, w);
      ziehe = null; speichern(); zeichne();
    });
    document.body.append(m);
    m.style.left = Math.min(x, innerWidth - m.offsetWidth - 8) + 'px';
    m.style.top = Math.min(y, innerHeight - m.offsetHeight - 8) + 'px';
    setTimeout(() => document.addEventListener('mousedown', function weg(e) { if (!m.contains(e.target)) { m.remove(); document.removeEventListener('mousedown', weg); } }), 0);
  }

  /* ---------------------------------------------------------------- Sortieren und Filtern */
  function sortieren(t, th) {
    const k = kopf(t); zellen(k); const i = +th.dataset.sp;
    const richtung = th.dataset.richtung === 'auf' ? 'ab' : 'auf';
    [...k.cells].forEach((c) => { delete c.dataset.richtung; c.removeAttribute('aria-sort'); });
    th.dataset.richtung = richtung; th.setAttribute('aria-sort', richtung === 'auf' ? 'ascending' : 'descending');
    for (const body of t.tBodies) {
      const zeilen = [...body.rows], summe = zeilen.filter((r) => r.classList.contains('summe') || r.querySelector('td[colspan]'));
      const rest = zeilen.filter((r) => !summe.includes(r));
      const wert = (r) => { zellen(r); return text(zelle(r, i)); };
      rest.sort((a, b) => {
        const x = wert(a), y = wert(b), nx = zahl(x), ny = zahl(y);
        const v = nx !== null && ny !== null ? nx - ny : x.localeCompare(y, 'de', { numeric: true, sensitivity: 'base' });
        return richtung === 'auf' ? v : -v;
      });
      body.append(...rest, ...summe);
    }
  }
  function filterFeld(t) {
    if (t._filter || koerper(t).length < 8 || t.dataset.filter === 'aus') return;
    const f = document.createElement('input');
    f.type = 'search'; f.className = 'tab-filter'; f.placeholder = 'In der Tabelle suchen …'; f.setAttribute('aria-label', 'In der Tabelle suchen');
    f.oninput = () => {
      const q = f.value.trim().toLowerCase();
      koerper(t).forEach((r) => { if (!r.classList.contains('summe')) r.hidden = !!q && !r.textContent.toLowerCase().includes(q); });
    };
    (t.closest('.scroll') || t).before(f);
    t._filter = f;
  }

  /* ---------------------------------------------------------------- Kontextmenü je Zeile */
  function zeilenMenue(t, r, x, y) {
    const p = [];
    if (r.dataset.karte) p.push({ t: 'Details rechts zeigen', ic: '▸', los: () => C.karte(r.dataset.karte, r) });
    const ed = r.querySelector('.edit');
    if (ed) p.push({ t: 'Bearbeiten', ic: '✎', los: () => ed.click() });
    for (const a of JSON.parse(r.dataset.aktionen || '[]')) {
      p.push({ t: a.t, ic: a.ic || '', gefahr: !!a.frage, href: a.methode ? undefined : a.url,
        los: a.methode ? async () => { if (a.frage && !(await C.dialog({ titel: a.frage, ok: 'Ja', gefahr: true }))) return;
          htmx.ajax(a.methode, a.url, { target: a.ziel || 'body', swap: a.swap || 'none' }); } : undefined });
    }
    p.push({ t: 'Zeile kopieren', ic: '⧉', los: () => { navigator.clipboard?.writeText([...r.cells].filter((c) => !c.hidden).map((c) => c.textContent.trim()).join('\t')); C.meldung('Zeile kopiert'); } });
    p.push({ t: 'Spalten …', ic: '☰', los: () => spaltenMenue(t, x, y) });
    C.menue(x, y, p.slice(0, 6));
  }

  /* ---------------------------------------------------------------- Einrichten */
  function einrichten(wurzel) {
    for (const t of (wurzel.querySelectorAll ? wurzel : document).querySelectorAll('table.tab')) {
      if (t._fertig) { spaltenAnwenden(t); continue; }
      const k = kopf(t); if (!k) continue;
      t._fertig = true;
      [...k.cells].forEach((th) => { if (!th.dataset.nosort) { th.tabIndex = 0; th.classList.add('sortierbar'); } });
      k.addEventListener('click', (e) => { const th = e.target.closest('th.sortierbar'); if (th) sortieren(t, th); });
      k.addEventListener('keydown', (e) => { const th = e.target.closest('th.sortierbar'); if (th && e.key === 'Enter') sortieren(t, th); });
      k.addEventListener('contextmenu', (e) => { e.preventDefault(); spaltenMenue(t, e.clientX, e.clientY); });
      t.addEventListener('contextmenu', (e) => {
        const r = e.target.closest('tbody tr'); if (!r || e.target.closest('input,textarea')) return;
        e.preventDefault(); zeilenMenue(t, r, e.clientX, e.clientY);
      });
      const knopf = document.createElement('button');
      knopf.type = 'button'; knopf.className = 'tab-spalten'; knopf.title = 'Spalten wählen'; knopf.setAttribute('aria-label', 'Spalten wählen'); knopf.textContent = '☰';
      knopf.onclick = (e) => { const b = e.currentTarget.getBoundingClientRect(); spaltenMenue(t, b.left - 200, b.bottom + 4); };
      const huelle = t.closest('.scroll') || t;
      huelle.classList.add('tab-huelle'); huelle.before(knopf);
      spaltenAnwenden(t); filterFeld(t);
    }
  }
  document.addEventListener('DOMContentLoaded', () => einrichten(document));
  document.addEventListener('htmx:load', (e) => { const t = e.target.closest && e.target.closest('table.tab'); einrichten(t ? t.parentNode : e.target); });

  /* ---------------------------------------------------------------- Bearbeiten in der Zelle: Tastatur */
  let weiter = null;   // nach dem Speichern zur nächsten Zelle springen
  document.addEventListener('keydown', (e) => {
    const f = e.target.closest('form.zelle-form'); if (!f) return;
    if (e.key === 'Escape') { e.preventDefault(); verwerfen(f); }
    else if (e.key === 'Tab') {
      e.preventDefault();
      const td = f.closest('td'), alle = [...td.closest('table').querySelectorAll('td.edit, td.edit-offen')].filter((c) => !c.hidden && !c.closest('tr').hidden);
      weiter = alle[alle.indexOf(td) + (e.shiftKey ? -1 : 1)]?.id || null;
      htmx.trigger(f, 'submit');
    }
  });
  const verwerfen = (f) => htmx.ajax('GET', f.dataset.anzeige, { target: f.closest('td'), swap: 'outerHTML' });
  // Klick daneben: geändert → speichern, sonst schließen (wie in einer Tabellenkalkulation)
  document.addEventListener('focusout', (e) => {
    const f = e.target.closest && e.target.closest('form.zelle-form');
    if (!f || (e.relatedTarget && f.contains(e.relatedTarget))) return;
    setTimeout(() => {
      if (!f.isConnected || f.contains(document.activeElement)) return;
      const i = f.querySelector('input[name=wert],textarea[name=wert]');
      if (i && i.value !== (f.dataset.alt || '')) htmx.trigger(f, 'submit'); else verwerfen(f);
    }, 120);
  });
  document.addEventListener('htmx:load', (e) => {   // neue Zelle ist da (outerHTML): Eingabe fokussieren bzw. weiterspringen
    if (e.target.classList?.contains('edit-offen')) { const i = e.target.querySelector('input[name=wert],textarea[name=wert],button[aria-checked=true]'); i?.focus(); i?.select?.(); }
    if (weiter && e.target.classList?.contains('edit')) { const z = document.getElementById(weiter); weiter = null; z?.click(); }
  });
  document.addEventListener('keydown', (e) => {   // Enter auf einer markierten Zelle öffnet sie
    if (e.key === 'Enter' && e.target.matches?.('td.edit')) { e.preventDefault(); e.target.click(); }
  });
})();
