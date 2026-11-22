'use strict';
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const view = $('#view'), drawer = $('#drawer');
let pollTimer = null;

async function api(path, opts) {
  const r = await fetch('/api' + path, opts);
  if (!r.ok) {
    let m = r.statusText;
    try { const j = await r.json(); m = j.detail || m; } catch (e) {}
    throw new Error(m);
  }
  return r.json();
}
function qs(o) {
  const p = new URLSearchParams();
  Object.entries(o).forEach(([k, v]) => { if (v !== '' && v != null && v !== false) p.set(k, v === true ? '1' : v); });
  return p.toString();
}
function toast(msg, ms = 2600) {
  const t = document.createElement('div'); t.className = 'toast'; t.textContent = msg; document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}
const debounce = (fn, ms = 220) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const fmtDate = r => r.date ? r.date.split('-').reverse().join('.') : (r.date_raw || '');
const fmtCoord = r => (r.lat != null && r.lon != null) ? r.lat.toFixed(4) + '°, ' + r.lon.toFixed(4) + '°' : '';
const PREC = {range: 'диапазон', decade: 'декада', month: 'месяц', season: 'сезон', year: 'год'};
function dateCell(r) {
  if (r.date && r.date_precision && r.date_precision !== 'day') {
    const per = r.date_end ? ', ' + r.date.split('-').reverse().join('.') + ' – ' + r.date_end.split('-').reverse().join('.') : '';
    return esc(r.date_raw || fmtDate(r)) + '<div class="muted" style="font-size:12px">' + (PREC[r.date_precision] || '') + per + '</div>';
  }
  return esc(fmtDate(r));
}
function fmtDT(s) { try { return new Date(s + 'Z').toLocaleString('ru-RU', {dateStyle: 'short', timeStyle: 'short'}); } catch (e) { return s; } }
function noteChip(r) {
  return r.user_note ? '<div class="usernote" title="' + esc(r.user_note) + '">Заметка: ' + esc(r.user_note.length > 90 ? r.user_note.slice(0, 90) + '…' : r.user_note) + '</div>' : '';
}
async function saveNote(r, text) {
  try {
    const res = await api('/records/' + r.id, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({user_note: text})});
    r.user_note = res.user_note; r.user_note_at = res.user_note_at;
    toast(text.trim() ? 'Заметка сохранена' : 'Заметка удалена');
    if ($('#results')) { S.keepScroll = true; await runSearch(); S.keepScroll = false; } else route();
    openRecord(r);
  } catch (e) { toast('Ошибка: ' + e.message, 4000); }
}
function confBadge(c) {
  const cls = c >= 0.8 ? 'b-hi' : c >= 0.5 ? 'b-mid' : 'b-lo';
  return '<span class="badge ' + cls + '">' + Math.round(c * 100) + '%</span>';
}

function attachAC(input, kind) {
  const wrap = document.createElement('div'); wrap.className = 'ac';
  input.parentNode.insertBefore(wrap, input); wrap.appendChild(input);
  const list = document.createElement('div'); list.className = 'ac-list'; list.hidden = true; wrap.appendChild(list);
  const load = debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) { list.hidden = true; return; }
    try {
      const items = await api('/suggest/' + kind + '?q=' + encodeURIComponent(q));
      list.innerHTML = items.map(i => '<div class="ac-item" data-v="' + esc(i.value) + '">' + esc(i.label) + (i.group ? '<small>' + esc(i.group) + '</small>' : '') + '</div>').join('');
      list.hidden = !items.length;
    } catch (e) { list.hidden = true; }
  });
  input.addEventListener('input', load);
  input.addEventListener('blur', () => setTimeout(() => list.hidden = true, 150));
  list.addEventListener('mousedown', e => {
    const it = e.target.closest('.ac-item'); if (!it) return;
    input.value = it.dataset.v; list.hidden = true; input.dispatchEvent(new Event('change'));
  });
}

function route() {
  clearInterval(pollTimer); pollTimer = null; closeDrawer();
  const h = location.hash.replace(/^#\/?/, '') || 'search';
  const [path, query] = h.split('?');
  const parts = path.split('/');
  $$('#nav a').forEach(a => a.classList.toggle('active', a.dataset.r === parts[0] || (parts[0] === 'source' && a.dataset.r === 'search') || (parts[0] === 'doc' && a.dataset.r === 'docs') || (parts[0] === 'table' && a.dataset.r === 'tables')));
  if (parts[0] === 'doc') return pageCoverage(+parts[1]);
  if (parts[0] === 'tables') return pageTables();
  if (parts[0] === 'table') return pageTable(+parts[1]);
  if (parts[0] === 'docs') return pageDocs();
  if (parts[0] === 'quality') return pageQuality();
  if (parts[0] === 'dict') return pageDict();
  if (parts[0] === 'source') return pageSource(+parts[1], +parts[2], new URLSearchParams(query || '').get('rec'));
  return pageSearch(new URLSearchParams(query || ''));
}
window.addEventListener('hashchange', route);

const S = { params: {}, page: 1, view: 'table', preset: 'species', total: 0, stats: null };
const PRESETS = {
  species: { title: 'Вид', hint: 'Введите русское или латинское название либо род. Синонимы находятся по справочнику.', examples: [['Берёза пушистая', {species: 'берёза пушистая'}], ['Betula', {species: 'Betula'}], ['Брусника', {species: 'брусника'}]], view: 'table' },
  author: { title: 'Автор', hint: 'Введите фамилию наблюдателя или автора раздела.', examples: [['Крюкова', {observer: 'Крюкова'}], ['Воробьёва', {observer: 'Воробьёва'}]], view: 'table' },
  phenology: { title: 'Фенология', hint: 'Выберите вид и (необязательно) фенофазу и годы: получите даты и график по годам.', examples: [['Берёза пушистая, зеленение', {species: 'берёза пушистая', phenophase: 'зеленение'}], ['Черника, цветение', {species: 'черника', phenophase: 'цветение'}], ['Прилёт лебедя', {species: 'лебедь', phenophase: 'прилёт'}]], view: 'chart' },
  place: { title: 'Место', hint: 'Введите топоним (при желании год и группу организмов): получите список видов с координатами из каталога.', examples: [['Остров Варлама', {toponym: 'остров Варлама'}], ['Янискоски', {toponym: 'Янискоски'}]], view: 'places' },
};
const FIELDS = [
  ['species', 'Вид (рус. / лат. / род)', 'text', 'species'], ['phenophase', 'Фенофаза / событие', 'text', 'phenophases'],
  ['toponym', 'Топоним', 'text', 'toponyms'], ['observer', 'Наблюдатель / автор', 'text', 'observers'],
  ['q', 'Текст (любое слово)', 'text', null],
];

const PH = {species: 'Вид: берёза пушистая, Betula, брусника', observer: 'Фамилия автора или наблюдателя, например Макарова', phenophase: 'Явление: зеленение, цветение, прилёт', toponym: 'Место: остров Варлама, Янискоски'};
const PRIMARY = {species: ['species'], author: ['observer'], phenology: ['species', 'phenophase'], place: ['toponym']};
const FIELD_ORDER = ['species', 'phenophase', 'toponym', 'observer', 'q', 'group', 'topic', 'unit', 'year_from', 'year_to', 'md_from', 'md_to', 'kind', 'min_conf', 'methodology', 'has_value', 'has_note', 'only_species', 'verified'];
const CHECKS = ['verified', 'has_value', 'has_note', 'only_species'];
const plural = (n, f) => { const a = Math.abs(n) % 100, b = a % 10; return f[(a > 10 && a < 20) ? 2 : (b > 1 && b < 5) ? 1 : (b === 1) ? 0 : 2]; };

async function pageSearch(hq) {
  if (hq && [...hq.keys()].length) {
    S.params = {}; hq.forEach((v, k) => { if (k === 'view') S.view = v; else S.params[k] = v; }); S.page = 1;
  }
  try { S.stats = await api('/stats'); } catch (e) { S.stats = S.stats || {years: [], groups: []}; }
  const years = (S.stats.years || []).filter(y => y >= 1900);
  const yopts = '<option value="">любой</option>' + years.map(y => '<option>' + y + '</option>').join('');
  const wrap = (k, label, inner) => '<div class="f-wrap" data-f="' + k + '"><label class="f">' + label + '</label>' + inner + '</div>';
  const text = ([k, l]) => wrap(k, l, '<input type="text" name="' + k + '" autocomplete="off">');
  const rec = S.stats.records || 0, docs = S.stats.documents_done || 0, spc = S.stats.species || 0;
  view.innerHTML = '' +
    '<section class="hero"><div class="hero-in">' +
    '<h1 class="display">Найдите наблюдение в архиве заповедника «Пасвик»</h1>' +
    '<div class="seg" id="presets" role="tablist" aria-label="Что искать">' + Object.entries(PRESETS).map(([k, p]) => '<button class="chip" role="tab" data-k="' + k + '">' + p.title + '</button>').join('') + '</div>' +
    '<div id="flt"><div class="searchbar" id="bar"><button class="btn primary big" id="go">Найти</button></div>' +
    '<details class="more" id="morewrap"><summary>Дополнительные фильтры</summary><div class="grid filters" id="more">' +
    FIELDS.map(text).join('') +
    wrap('group', 'Группа организмов', '<select name="group"><option value="">все</option>' + (S.stats.groups || []).map(g => '<option>' + esc(g) + '</option>').join('') + '</select>') +
    wrap('year_from', 'Год с', '<select name="year_from">' + yopts + '</select>') +
    wrap('year_to', 'Год по', '<select name="year_to">' + yopts + '</select>') +
    wrap('kind', 'Тип записи', '<select name="kind"><option value="">все</option><option value="phenology">фенология</option><option value="count">численность</option><option value="event">наблюдение</option></select>') +
    wrap('min_conf', 'Достоверность', '<select name="min_conf"><option value="">любая</option><option value="0.5">от 50%</option><option value="0.8">от 80%</option></select>') +
    wrap('topic', 'Раздел (тема)', '<select name="topic"><option value="">все</option>' + (S.stats.topics || []).map(t => '<option value="' + esc(t.value) + '">' + esc(t.value) + ' (' + t.count + ')</option>').join('') + '</select>') +
    wrap('unit', 'Единица измерения (показатель)', '<select name="unit"><option value="">любая</option>' + (S.stats.units || []).map(u => '<option value="' + esc(u.value) + '">' + esc(u.value) + ' (' + u.count + ')</option>').join('') + '</select>') +
    wrap('md_from', 'Дата в году с (дд.мм)', '<input type="text" name="md_from" placeholder="01.05" inputmode="numeric" autocomplete="off">') +
    wrap('md_to', 'Дата в году по (дд.мм)', '<input type="text" name="md_to" placeholder="31.05" inputmode="numeric" autocomplete="off">') +
    wrap('methodology', 'Методика, маршрут', '<input type="text" name="methodology" autocomplete="off">') +
    '<div class="f-wrap" data-f="has_value"><label class="check"><input type="checkbox" name="has_value"> Только с числовым значением</label></div>' +
    '<div class="f-wrap" data-f="only_species"><label class="check"><input type="checkbox" name="only_species"> Только записи о виде, без упоминаний</label></div>' +
    '<div class="f-wrap" data-f="has_note"><label class="check"><input type="checkbox" name="has_note"> Только с моими заметками</label></div>' +
    '<div class="f-wrap" data-f="verified"><label class="check"><input type="checkbox" name="verified"> Только проверенные</label></div>' +
    '</div></details></div>' +
    '<div class="examples" id="phint"></div>' +
    '<div class="tools"><select id="saved" aria-label="Сохранённые запросы"><option value="">Сохранённые запросы</option></select><button class="linkbtn" id="save">Сохранить запрос</button><button class="linkbtn" id="reset">Сбросить</button><button class="linkbtn danger" id="delsaved" hidden>Удалить запрос</button></div>' +
    '<p class="facts"><b>' + rec + '</b> ' + plural(rec, ['запись', 'записи', 'записей']) + ' в <b>' + docs + '</b> ' + plural(docs, ['документе', 'документах', 'документах']) + ', <b>' + spc + '</b> ' + plural(spc, ['вид', 'вида', 'видов']) + ' в справочнике</p>' +
    '</div></section><div id="results"></div>';
  FIELDS.forEach(([k, , , ac]) => { if (ac) attachAC($('[name=' + k + ']'), ac); });
  $$('#presets .chip').forEach(c => c.onclick = () => setPreset(c.dataset.k));
  $('#go').onclick = () => { S.page = 1; runSearch(); };
  $('#reset').onclick = () => { $$('#flt input[type=text],#flt select').forEach(i => i.value = ''); CHECKS.forEach(c => $('[name=' + c + ']').checked = false); S.page = 1; $('#results').innerHTML = ''; };
  $$('#flt input[type=text]').forEach(i => i.addEventListener('keydown', e => { if (e.key === 'Enter') { S.page = 1; runSearch(); } }));
  $('#save').onclick = saveQuery; $('#saved').onchange = onSavedPick; $('#delsaved').onclick = delSaved;
  setPreset(S.preset, true); fillForm(S.params); loadSaved();
  if (Object.keys(S.params).length) {
    if (!$$('#more [name]').every(i => !S.params[i.name])) $('#morewrap').open = true;
    runSearch();
  }
}
function readForm() {
  const p = {};
  $$('#flt input[type=text],#flt select').forEach(i => { if (i.value.trim() !== '') p[i.name] = i.value.trim(); });
  CHECKS.forEach(c => { if ($('[name=' + c + ']').checked) p[c] = '1'; });
  return p;
}
function fillForm(p) {
  $$('#flt input[type=text],#flt select').forEach(i => i.value = p[i.name] || '');
  CHECKS.forEach(c => $('[name=' + c + ']').checked = !!p[c]);
}
const HIST_KEY = 'chron_search_history';
function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HIST_KEY) || '[]'); } catch (e) { return []; }
}
function rememberSearch(p) {
  const q = {};
  ['species', 'toponym', 'phenophase', 'observer', 'q'].forEach(k => { if (p[k]) q[k] = p[k]; });
  if (!Object.keys(q).length) return;
  const h = loadHistory().filter(x => JSON.stringify(x) !== JSON.stringify(q));
  h.unshift(q);
  try { localStorage.setItem(HIST_KEY, JSON.stringify(h.slice(0, 12))); } catch (e) {}
}
function clearHistory() {
  try { localStorage.removeItem(HIST_KEY); } catch (e) {}
}
let hintToken = 0;
async function renderHint() {
  const el = $('#phint');
  if (!el) return;
  const token = ++hintToken, pr = PRESETS[S.preset], hist = loadHistory();
  const showStatic = () => {
    el.innerHTML = 'Например: ' + pr.examples.map((e, i) => '<button class="linkbtn" data-i="' + i + '">' + esc(e[0]) + '</button>').join('');
    $$('#phint button').forEach(b => b.onclick = () => { fillForm(pr.examples[b.dataset.i][1]); S.view = pr.view; S.page = 1; runSearch(); });
  };
  if (!hist.length) return showStatic();
  let rec = null;
  try { rec = await api('/recommendations', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({history: hist.slice(0, 3)})}); } catch (e) {}
  if (token !== hintToken || !$('#phint')) return;
  if (!rec || !rec.items.length) return showStatic();
  el.innerHTML = 'Может быть интересно: ' + rec.items.map((it, i) => '<button class="linkbtn" data-i="' + i + '" title="' + esc(it.reason) + '">' + esc(it.label) + '</button>').join('') +
    '<button class="linkbtn quiet" id="hint-reset" title="Забыть историю поиска в этом браузере">Сбросить</button>';
  $$('#phint button[data-i]').forEach(b => b.onclick = () => applySuggestion(rec.items[b.dataset.i].params));
  $('#hint-reset').onclick = () => { clearHistory(); renderHint(); };
}
function applySuggestion(params) {
  const preset = params.phenophase && params.species ? 'phenology' : params.toponym ? 'place' : params.observer ? 'author' : 'species';
  setPreset(preset, true);
  fillForm(params);
  S.view = preset === 'phenology' ? 'chart' : preset === 'place' ? 'places' : 'table';
  S.page = 1;
  runSearch();
}
function setPreset(k, silent) {
  S.preset = k; const p = PRIMARY[k], bar = $('#bar'), more = $('#more'), go = $('#go');
  $$('#presets .chip').forEach(c => { const on = c.dataset.k === k; c.classList.toggle('active', on); c.setAttribute('aria-selected', on); });
  p.forEach(n => { const w = $('.f-wrap[data-f=' + n + ']'); bar.insertBefore(w, go); const i = $('input', w); if (i) i.placeholder = PH[n] || ''; });
  FIELD_ORDER.filter(n => !p.includes(n)).forEach(n => { const w = $('.f-wrap[data-f=' + n + ']'); more.appendChild(w); const i = $('input[type=text]', w); if (i) i.placeholder = ''; });
  const pr = PRESETS[k];
  renderHint();
  if (!silent) { S.view = pr.view; const f = $('input', bar); f && f.focus(); }
}
async function runSearch() {
  S.params = readForm();
  if (!S.keepScroll) rememberSearch(S.params);
  const res = $('#results');
  res.innerHTML = '<div class="empty">Ищем…</div>';
  try {
    if (S.view === 'chart') await renderChart(res);
    else if (S.view === 'places') await renderPlaces(res);
    else await renderTable(res);
  } catch (e) { res.innerHTML = '<div class="empty err">Ошибка: ' + esc(e.message) + '</div>'; }
  if (!S.keepScroll) renderHint();
  const hero = $('.hero'); if (hero && !S.keepScroll) window.scrollTo({top: Math.max(0, res.getBoundingClientRect().top + window.scrollY - 76), behavior: 'smooth'});
}
function viewTabs() {
  return '<div class="tabs">' + [['table', 'Таблица записей'], ['chart', 'Фенология по годам'], ['places', 'Виды в месте']].map(([k, l]) =>
    '<div class="tab' + (S.view === k ? ' active' : '') + '" data-v="' + k + '">' + l + '</div>').join('') + '</div>';
}
function bindTabs(res) { $$('.tab', res).forEach(t => t.onclick = () => { S.view = t.dataset.v; runSearch(); }); }
function exportBar(total, mentions) {
  const q = qs(S.params);
  return '<div class="results-head"><b>' + total + '</b><span class="muted">' + plural(total, ['запись найдена', 'записи найдено', 'записей найдено']) + (mentions ? ', из них упоминаний вида в других записях: ' + mentions : '') + '</span><span class="spacer"></span>' +
    '<a class="btn small" href="/api/records/export?' + q + '&format=xlsx">Скачать Excel</a><a class="btn small" href="/api/records/export?' + q + '&format=csv">Скачать CSV</a></div>';
}
async function renderTable(res) {
  const p = Object.assign({}, S.params, {sort: S.sort || 'date', order: S.order || 'asc'});
  const d = await api('/records?' + qs(Object.assign({}, p, {page: S.page, page_size: 50})));
  S.total = d.total; S.items = d.items;
  let html = viewTabs() + exportBar(d.total, d.mentions_total);
  if (d.sections && d.sections.length) {
    html += '<div class="card sectionlist" style="margin-bottom:12px"><h2>Разделы, где указан «' + esc(S.params.observer) + '»</h2>' +
      d.sections.map(s => '<a href="#/source/' + s.document_id + '/' + s.page_start + '">' + esc(s.document) + ', ' + esc(s.section) + ' <span class="muted">(стр. ' + s.page_start + '–' + s.page_end + '; ' + esc(s.authors.join(', ')) + ')</span></a>').join('') + '</div>';
  }
  if (!d.items.length) html += '<div class="empty">Ничего не найдено. Попробуйте убрать часть фильтров или проверьте написание.</div>';
  else {
    const th = (k, l) => '<th class="sortable" data-s="' + k + '">' + l + (p.sort === k ? (p.order === 'asc' ? ' ▲' : ' ▼') : '') + '</th>';
    html += '<div class="card" style="padding:0;overflow:auto"><table class="recs"><thead><tr>' + th('species', 'Вид') + th('event', 'Событие / фенофаза') + th('date', 'Дата') +
      '<th>Место</th><th>Значение</th><th>Наблюдатель</th>' + th('page', 'Источник') + th('conf', 'Достов.') + '</tr></thead><tbody>' +
      d.items.map((r, i) => (r.mention && !(i > 0 && d.items[i - 1].mention) ? '<tr class="divider"><td colspan="8">Упоминания вида в записях о других видах, для контекста</td></tr>' : '') + rowHtml(r, i)).join('') + '</tbody></table></div>';
    const pages = Math.ceil(d.total / d.page_size);
    html += '<div class="pager"><button class="btn small" id="prev" ' + (S.page <= 1 ? 'disabled' : '') + '>← Назад</button><span class="muted">стр. ' + S.page + ' из ' + pages + '</span><button class="btn small" id="next" ' + (S.page >= pages ? 'disabled' : '') + '>Вперёд →</button></div>';
  }
  res.innerHTML = html; bindTabs(res);
  $$('th.sortable', res).forEach(t => t.onclick = () => { const k = t.dataset.s; S.order = (S.sort === k && S.order === 'asc') ? 'desc' : 'asc'; S.sort = k; S.page = 1; runSearch(); });
  $$('tbody tr', res).forEach(tr => tr.onclick = e => { if (tr.dataset.i === undefined || e.target.closest('a')) return; openRecord(S.items[+tr.dataset.i]); });
  const pv = $('#prev', res), nx = $('#next', res);
  if (pv) pv.onclick = () => { S.page--; runSearch(); window.scrollTo(0, 0); };
  if (nx) nx.onclick = () => { S.page++; runSearch(); window.scrollTo(0, 0); };
}
function rowHtml(r, i) {
  const sp = r.species_ref || r.species_text;
  const lat = r.species_lat || r.species_lat_text;
  return '<tr' + (r.mention ? ' class="mention"' : '') + ' data-i="' + i + '"><td><b>' + esc(sp || '—') + '</b>' + (r.mention ? ' <span class="tag" title="Запись о другом виде: искомый вид упоминается в примечании или событии">упоминание</span>' : '') + (lat ? '<div class="lat">' + esc(lat) + '</div>' : '') + (r.group ? '<span class="tag">' + esc(r.group) + '</span>' : '') + '</td>' +
    '<td>' + esc(r.event) + (r.habitat ? '<div class="muted">' + esc(r.habitat) + '</div>' : '') + noteChip(r) + '</td>' +
    '<td style="white-space:nowrap">' + dateCell(r) + '</td>' +
    '<td>' + esc(r.toponym) + (fmtCoord(r) ? '<div class="muted">' + fmtCoord(r) + '</div>' : '') + '</td>' +
    '<td style="white-space:nowrap">' + (r.value != null ? esc(r.value) + ' ' + esc(r.unit) : '') + '</td>' +
    '<td>' + esc(r.observer) + (r.observer_source === 'section' ? ' <span class="tag" title="Автор раздела, в тексте наблюдатель не назван">автор раздела</span>' : '') + '</td>' +
    '<td style="white-space:nowrap"><a href="#/source/' + r.document_id + '/' + r.page + '?rec=' + r.id + '">' + esc(r.doc_year || r.doc_title) + ', стр. ' + r.page + '</a></td>' +
    '<td style="white-space:nowrap">' + confBadge(r.confidence) + (r.verified ? ' <span class="tag ver">проверено</span>' : '') + (r.flags.length ? ' <span title="' + esc(r.flags_text.join('; ')) + '">⚠</span>' : '') + '</td></tr>';
}

const PALETTE = ['#0a6b5e', '#2f5d8a', '#c2791c', '#b4413a', '#6b4fa3', '#13808f', '#a63c78', '#4b5ec8'];
const MONTHS = [['янв', 1], ['фев', 32], ['мар', 60], ['апр', 91], ['май', 121], ['июн', 152], ['июл', 182], ['авг', 213], ['сен', 244], ['окт', 274], ['ноя', 305], ['дек', 335]];
async function renderChart(res) {
  const d = await api('/phenology?' + qs(S.params));
  let html = viewTabs();
  const series = d.series || [];
  if (!series.length) { res.innerHTML = html + '<div class="empty">Нет фенологических дат по этому запросу. Укажите вид (например, «берёза пушистая») и при необходимости фенофазу.</div>'; return bindTabs(res); }
  const pts = series.flatMap(s => s.points);
  let y0 = Math.min(...pts.map(p => p.year)), y1 = Math.max(...pts.map(p => p.year));
  if (y0 === y1) { y0 -= 1; y1 += 1; }
  const dmin = Math.max(1, Math.min(...pts.map(p => p.doy)) - 10), dmax = Math.min(366, Math.max(...pts.map(p => p.doy)) + 10);
  const W = 980, H = 380, L = 56, R = 20, T = 14, B = 34;
  const X = y => L + (y - y0) / (y1 - y0) * (W - L - R), Y = dd => T + (dd - dmin) / (dmax - dmin) * (H - T - B);
  let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;background:var(--panel);border:1px solid var(--line);border-radius:12px">';
  MONTHS.forEach(([n, dd]) => { if (dd >= dmin && dd <= dmax) svg += '<line x1="' + L + '" x2="' + (W - R) + '" y1="' + Y(dd) + '" y2="' + Y(dd) + '" stroke="#e6ebea"/><text x="' + (L - 8) + '" y="' + (Y(dd) + 4) + '" fill="#5b6467" font-size="11" text-anchor="end">' + n + '</text>'; });
  const step = Math.max(1, Math.ceil((y1 - y0) / 14));
  for (let y = y0; y <= y1; y += step) svg += '<text x="' + X(y) + '" y="' + (H - 12) + '" fill="#5b6467" font-size="11" text-anchor="middle">' + y + '</text>';
  series.forEach((s, i) => {
    const c = PALETTE[i % PALETTE.length], sp = s.points;
    if (sp.length > 1) svg += '<polyline fill="none" stroke="' + c + '" stroke-opacity=".5" points="' + sp.map(p => X(p.year) + ',' + Y(p.doy)).join(' ') + '"/>';
    sp.forEach(p => svg += '<a href="#/source/' + p.doc + '/' + p.page + '?rec=' + p.id + '"><circle cx="' + X(p.year) + '" cy="' + Y(p.doy) + '" r="5.5" fill="' + c + '" stroke="#fff" stroke-width="1.5"><title>' + esc(s.event) + ': ' + p.date.split('-').reverse().join('.') + '</title></circle></a>');
  });
  series.forEach((s, i) => { const tl = s.stats && s.stats.trend_line; if (tl) svg += '<line x1="' + X(tl.y0) + '" y1="' + Y(tl.v0) + '" x2="' + X(tl.y1) + '" y2="' + Y(tl.v1) + '" stroke="' + PALETTE[i % PALETTE.length] + '" stroke-width="2" stroke-dasharray="6 5" opacity=".8"><title>Линейный тренд: ' + esc(s.event) + '</title></line>'; });
  svg += '</svg>';
  html += '<div class="legend">' + series.map((s, i) => '<span style="--c:' + PALETTE[i % PALETTE.length] + '">' + esc(s.event) + ' (' + s.points.length + ')</span>').join('') + '</div>' + svg;
  html += '<div class="card" style="margin-top:12px;padding:0;overflow:auto"><table><thead><tr><th>Вид / фенофаза</th><th>Даты по годам</th></tr></thead><tbody>' + series.map(s =>
    '<tr style="cursor:default"><td>' + esc(s.event) + '</td><td>' + s.points.map(p => '<a href="#/source/' + p.doc + '/' + p.page + '?rec=' + p.id + '"><b>' + p.year + '</b>: ' + p.date.split('-').reverse().slice(0, 2).join('.') + '</a>').join(' &nbsp; ') + '</td></tr>').join('') + '</tbody></table></div>';
  const sd = x => x.date + ' (' + x.year + ')';
  html += '<h2 style="margin-top:36px">Статистика ряда</h2><div class="card" style="padding:0;overflow:auto"><table class="plain"><thead><tr><th>Ряд</th><th>Лет в ряду</th><th>Средняя дата</th><th>Медиана</th><th>Самая ранняя</th><th>Самая поздняя</th><th>Разброс, дней</th><th>Тренд, дней за 10 лет</th></tr></thead><tbody>' + series.map(s => { const st = s.stats;
    const tr = st.trend_per_decade == null ? '<span class="muted">мало данных</span>' : (st.trend_per_decade > 0 ? '+' : '') + st.trend_per_decade + (st.trend_per_decade < 0 ? ' (раньше)' : st.trend_per_decade > 0 ? ' (позже)' : '') + ' <span class="muted">R² ' + st.r2 + '</span>';
    return '<tr style="cursor:default"><td>' + esc(s.event) + '</td><td>' + st.n + '</td><td>' + st.mean + '</td><td>' + st.median + '</td><td>' + sd(st.earliest) + '</td><td>' + sd(st.latest) + '</td><td>' + st.sd + '</td><td>' + tr + '</td></tr>'; }).join('') + '</tbody></table></div>' +
    '<p class="hint">В расчёт входят только записи с точной датой. Несколько наблюдений за год сводятся к среднему. Тренд (штриховая линия на графике) строится только при восьми и более годах наблюдений в периоде не короче восьми лет. Это описательная оценка по доступным годам, а не доказательство изменения климата.</p>';
  res.innerHTML = html; bindTabs(res);
}

async function renderPlaces(res) {
  const d = await api('/places/species?' + qs(S.params));
  let html = viewTabs();
  if (d.toponym) html += '<div class="card" style="margin-bottom:12px"><b>' + esc(d.toponym.name) + '</b>, ' + (d.toponym.lat != null ? 'координаты каталога: ' + d.toponym.lat + '°, ' + d.toponym.lon + '°' : '<span class="muted">координаты в каталоге не заданы (добавьте в «Справочниках»)</span>') + '</div>';
  else if (S.params.toponym) html += '<div class="card" style="margin-bottom:12px"><span class="muted">Топоним «' + esc(S.params.toponym) + '» не найден в каталоге, поиск идёт по тексту записей.</span></div>';
  if (!d.species.length) html += '<div class="empty">Видов для этого места не найдено. Введите топоним (например, «остров Варлама») и при желании год и группу.</div>';
  else {
    html += '<div class="row" style="margin:10px 0"><b>' + d.species.length + '</b> <span class="muted">видов</span></div><div class="card" style="padding:0;overflow:auto"><table><thead><tr><th>Вид</th><th>Латинское название</th><th>Группа</th><th>Записей</th><th>Годы</th><th>Координаты</th></tr></thead><tbody>' +
      d.species.map(s => '<tr class="sp" data-n="' + esc(s.name_ru) + '"><td><b>' + esc(s.name_ru) + '</b></td><td class="lat">' + esc(s.name_lat) + '</td><td>' + esc(s.group) + '</td><td>' + s.records + '</td><td>' + (s.year_from || '') + (s.year_to && s.year_to !== s.year_from ? '–' + s.year_to : '') + '</td><td class="muted">' + (s.lat != null ? s.lat + '°, ' + s.lon + '°' : (d.toponym && d.toponym.lat != null ? d.toponym.lat + '°, ' + d.toponym.lon + '° (каталог)' : '')) + '</td></tr>').join('') + '</tbody></table></div>';
  }
  res.innerHTML = html; bindTabs(res);
  $$('tr.sp', res).forEach(tr => tr.onclick = () => { fillForm({species: tr.dataset.n}); S.view = 'table'; S.page = 1; runSearch(); });
}

async function loadSaved() {
  try {
    S.saved = await api('/saved-queries');
    $('#saved').innerHTML = '<option value="">Сохранённые запросы…</option>' + S.saved.map(s => '<option value="' + s.id + '">' + esc(s.name) + '</option>').join('');
  } catch (e) {}
}
async function saveQuery() {
  const p = readForm();
  if (!Object.keys(p).length) return toast('Сначала задайте фильтры');
  const name = prompt('Название запроса', Object.values(p).slice(0, 3).join(', '));
  if (!name) return;
  await api('/saved-queries', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name, params: Object.assign({_view: S.view}, p)})});
  toast('Запрос сохранён'); loadSaved();
}
function onSavedPick() {
  const id = $('#saved').value; $('#delsaved').hidden = !id; if (!id) return;
  const s = S.saved.find(x => x.id == id); if (!s) return;
  const p = Object.assign({}, s.params); S.view = p._view || 'table'; delete p._view;
  fillForm(p); S.page = 1; runSearch();
}
async function delSaved() { const id = $('#saved').value; if (id && confirm('Удалить сохранённый запрос?')) { await api('/saved-queries/' + id, {method: 'DELETE'}); $('#delsaved').hidden = true; loadSaved(); } }

function closeDrawer() { if (drawer) drawer.classList.remove('open'); }
function openRecord(r) {
  const flagList = r.flags_text.length ? '<ul class="warnlist">' + r.flags_text.map(f => '<li>' + esc(f) + '</li>').join('') + '</ul>' : '<div class="muted">Предупреждений нет</div>';
  const ex = r.extra || {};
  const extra = (ex.mean_date || ex.earliest) ? '<dt>Многолетние данные</dt><dd>средняя дата: ' + esc(ex.mean_date || '—') + '; самая ранняя: ' + esc(ex.earliest ? ex.earliest.split('-').reverse().join('.') : '—') + '; самая поздняя: ' + esc(ex.latest ? ex.latest.split('-').reverse().join('.') : '—') + '; лет наблюдений: ' + esc(ex.years_observed == null ? '—' : ex.years_observed) + '</dd>' : '';
  drawer.innerHTML = '<span class="close" id="dclose">×</span><h2>' + esc(r.species_ref || r.species_text || r.event) + '</h2>' +
    '<div class="row">' + confBadge(r.confidence) + ' <span class="tag">' + ({rule: 'правила', llm: 'YandexGPT', manual: 'вручную'}[r.method] || r.method) + '</span>' + (r.ai_check === 'ok' ? '<span class="tag ver">проверено второй моделью</span>' : '') + (r.verified ? '<span class="tag ver">проверено</span>' : '') + '</div>' +
    '<dl class="kv"><dt>Вид (как в тексте)</dt><dd>' + esc(r.species_text || '—') + '</dd><dt>Вид по справочнику</dt><dd>' + esc(r.species_ref || '—') + (r.species_lat ? ' <span class="lat">' + esc(r.species_lat) + '</span>' : '') + '</dd>' +
    '<dt>Род / группа</dt><dd>' + esc(r.genus || '—') + ' / ' + esc(r.group || '—') + '</dd><dt>Событие, фенофаза</dt><dd>' + esc(r.event || '—') + '</dd>' +
    '<dt>Дата</dt><dd>' + dateCell(r) + (r.date_raw && r.date && (!r.date_precision || r.date_precision === 'day') ? ' <span class="muted">(в тексте: ' + esc(r.date_raw) + ')</span>' : '') + '</dd>' +
    '<dt>Место</dt><dd>' + esc(r.toponym || '—') + (fmtCoord(r) ? ' <span class="muted">' + fmtCoord(r) + '</span>' : '') + '</dd><dt>Местообитание</dt><dd>' + esc(r.habitat || '—') + '</dd>' +
    '<dt>Значение</dt><dd>' + (r.value != null ? esc(r.value) + ' ' + esc(r.unit) + (r.unit_raw && r.unit_raw !== r.unit ? ' <span class="muted">(в тексте: ' + esc(r.unit_raw) + ')</span>' : '') : '—') + '</dd><dt>Методика, маршрут</dt><dd>' + esc(r.methodology || '—') + '</dd><dt>Наблюдатель / автор</dt><dd>' + esc(r.observer || '—') + (r.observer_source === 'section' ? ' <span class="muted">(автор раздела)</span>' : '') + '</dd>' +
    '<dt>Примечание</dt><dd>' + esc(r.note || '—') + '</dd>' + extra + '<dt>Раздел</dt><dd>' + esc(r.section || '—') + (r.topic ? ' <span class="tag">' + esc(r.topic) + '</span>' : '') + '</dd><dt>Источник</dt><dd>' + esc(r.doc_title) + ', стр. PDF ' + r.page + '</dd></dl>' +
    '<div class="muted">Исходный фрагмент</div><div class="frag">' + esc(r.fragment || '—') + '</div>' +
    '<div class="muted">Предупреждения</div>' + flagList +
    '<div class="muted" style="margin-top:20px">Моя заметка</div><textarea id="unote" rows="4" placeholder="Комментарий к записи: уточнение, чем проверено, вопрос коллегам" style="height:auto;padding:12px 14px;line-height:1.5;resize:vertical">' + esc(r.user_note || '') + '</textarea>' +
    '<div class="row" style="margin-top:8px"><button class="btn small primary" id="unote-save">Сохранить заметку</button>' + (r.user_note ? '<button class="btn small" id="unote-del">Удалить заметку</button>' : '') + (r.user_note_at ? '<span class="muted" style="font-size:13px">сохранена ' + esc(fmtDT(r.user_note_at)) + '</span>' : '') + '</div>' +
    '<div class="row" style="margin-top:16px"><a class="btn primary" href="#/source/' + r.document_id + '/' + r.page + '?rec=' + r.id + '">Открыть источник</a>' +
    '<button class="btn" id="dver">' + (r.verified ? 'Снять отметку' : '✓ Подтвердить') + '</button><button class="btn" id="dedit">Править</button></div>';
  drawer.classList.add('open');
  $('#dclose').onclick = closeDrawer;
  $('#dver').onclick = async () => { await api('/records/' + r.id, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({verified: !r.verified})}); toast('Сохранено'); closeDrawer(); if ($('#results')) runSearch(); };
  $('#dedit').onclick = () => editRecord(r);
  $('#unote-save').onclick = () => saveNote(r, $('#unote').value);
  const delNote = $('#unote-del'); if (delNote) delNote.onclick = () => saveNote(r, '');
}
function editRecord(r, after) {
  const f = (k, l, v, type) => '<div><label class="f">' + l + '</label><input type="' + (type || 'text') + '" name="' + k + '" value="' + esc(v == null ? '' : v) + '"></div>';
  drawer.innerHTML = '<span class="close" id="dclose">×</span><h2>Правка записи #' + r.id + '</h2><div class="grid" id="ef">' +
    f('species_text', 'Вид (рус.)', r.species_text) + f('species_lat_text', 'Вид (лат.)', r.species_lat_text) + f('event', 'Событие / фенофаза', r.event) +
    f('date', 'Дата (дд.мм.гггг)', r.date ? r.date.split('-').reverse().join('.') : r.date_raw) + f('toponym', 'Топоним', r.toponym) + f('habitat', 'Местообитание', r.habitat) +
    f('value', 'Значение', r.value, 'number') + f('unit', 'Единица', r.unit) + f('observer', 'Наблюдатель', r.observer_source === 'section' ? '' : r.observer) + f('note', 'Примечание', r.note) +
    '</div><div class="frag">' + esc(r.fragment) + '</div><div class="row"><button class="btn primary" id="esave">Сохранить и подтвердить</button><button class="btn" id="ecancel">Отмена</button></div>';
  $('#dclose').onclick = $('#ecancel').onclick = () => { if (after) after(); closeDrawer(); };
  $('#esave').onclick = async () => {
    const body = {};
    $$('#ef input').forEach(i => { body[i.name] = i.type === 'number' ? (i.value === '' ? null : +i.value) : i.value; });
    try { await api('/records/' + r.id, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}); toast('Запись сохранена'); closeDrawer(); if (after) after(); else if ($('#results')) runSearch(); }
    catch (e) { toast('Ошибка: ' + e.message, 4000); }
  };
}

async function pageSource(doc, page, recId) {
  view.innerHTML = '<div class="empty">Загрузка…</div>';
  let d;
  try { d = await api('/documents/' + doc + '/pages/' + page); } catch (e) { view.innerHTML = '<div class="empty err">' + esc(e.message) + '</div>'; return; }
  const sel = d.records.find(r => r.id == recId) || d.records[0];
  const hl = sel ? sel.fragment : '';
  const nonPdf = d.document.kind && d.document.kind !== 'pdf';
  const textView = S.srcText || nonPdf;
  view.innerHTML = '<div class="row" style="margin-bottom:12px"><a href="javascript:history.back()">← Назад</a><h1 style="margin:0">' + esc(d.document.title) + ', стр. PDF ' + page + '</h1>' +
    (d.printed_no ? '<span class="pill">напечатано: ' + esc(d.printed_no) + '</span>' : '') + '<span class="pill">' + (d.source === 'vision' ? 'прочитано ИИ' : d.source === 'ocr' ? 'текст: OCR' : 'текстовый слой') + (d.ai_vision && d.source !== 'vision' ? ', таблицы и рисунки: ИИ' : '') + (d.spread ? ', разворот' : '') + '</span><span class="spacer"></span>' +
    '<button class="btn small" id="p-prev" ' + (page <= 1 ? 'disabled' : '') + '>← стр.</button><button class="btn small" id="p-next" ' + (page >= d.pages_total ? 'disabled' : '') + '>стр. →</button>' +
    (nonPdf ? '' : '<button class="btn small" id="p-toggle">' + (textView ? 'Показать скан' : 'Показать текст') + '</button>') + '<a class="btn small" ' + (nonPdf ? '' : 'target="_blank" ') + 'href="/api/documents/' + doc + '/file' + (nonPdf ? '' : '#page=' + page) + '">' + (nonPdf ? 'Скачать файл' : 'PDF') + '</a></div>' +
    (d.section ? '<p class="sub">Раздел: ' + esc(d.section) + '</p>' : '') +
    '<div class="src"><div id="left"></div><div id="right"></div></div>';
  const left = $('#left');
  if (textView) {
    let t = esc(d.rich || d.text);
    if (hl) { const first = esc(hl.split(/\s+/).slice(0, 4).join(' ')); if (first && t.includes(first)) t = t.replace(first, '<mark>' + first + '</mark>'); }
    left.innerHTML = '<div class="ocrtext">' + t + '</div>';
  } else left.innerHTML = '<div class="pageimg"><img id="pimg" src="/api/documents/' + doc + '/pages/' + page + '/image?hl=' + encodeURIComponent(hl) + '" alt="страница"></div>' +
    (d.source === 'ocr' ? '<p class="hint">Скан: подсветка недоступна (нет текстового слоя). Фрагмент смотрите в «Показать текст».</p>' : '');
  const picked = d.records.find(r => r.id == recId);
  const recBox = r =>
    '<div class="recbox' + (sel && r.id === sel.id ? ' sel' : '') + '" data-id="' + r.id + '"><div class="row"><b>' + esc(r.species_ref || r.species_text || '—') + '</b>' + confBadge(r.confidence) + (r.verified ? '<span class="tag ver">проверено</span>' : '') + '</div>' +
    '<div>' + [r.event, fmtDate(r), r.value != null ? (r.value + ' ' + r.unit).trim() : ''].filter(Boolean).map(esc).join(', ') + '</div><div class="frag">' + esc(r.fragment) + '</div>' +
    (r.flags_text.length ? '<div class="muted">⚠ ' + esc(r.flags_text.join('; ')) + '</div>' : '') + noteChip(r) +
    '<div class="row" style="margin-top:6px"><button class="btn small ok" data-a="ver">' + (r.verified ? 'Снять отметку' : '✓ Подтвердить') + '</button><button class="btn small" data-a="note">Заметка</button><button class="btn small" data-a="edit">Править</button><button class="btn small danger" data-a="del">Удалить</button></div></div>';
  const rest = d.records.filter(r => !picked || r.id !== picked.id);
  $('#right').innerHTML = !d.records.length
    ? '<h2>Записи на странице (0)</h2><div class="empty">' + emptyReason(d) + '</div>'
    : picked
      ? '<h2>Выбранная запись</h2>' + recBox(picked) + (rest.length ? '<h2 style="margin-top:28px">Остальные записи на странице (' + rest.length + ')</h2>' + rest.map(recBox).join('') : '')
      : '<h2>Записи на странице (' + d.records.length + ')</h2>' + d.records.map(recBox).join('');
  $('#right').insertAdjacentHTML('beforeend', pageExtras(d, doc, page));
  const aiBtn = $('#ai-page'); if (aiBtn) aiBtn.onclick = () => runAi(doc, page, () => pageSource(doc, page, recId), aiBtn);
  const go = (p, rid) => { location.hash = '#/source/' + doc + '/' + p + (rid ? '?rec=' + rid : ''); };
  $('#p-prev').onclick = () => go(page - 1); $('#p-next').onclick = () => go(page + 1);
  const tg = $('#p-toggle'); if (tg) tg.onclick = () => { S.srcText = !S.srcText; pageSource(doc, page, recId); };
  $$('.recbox').forEach(b => {
    const r = d.records.find(x => x.id == b.dataset.id);
    b.addEventListener('click', e => { if (e.target.closest('button')) return; go(page, r.id); });
    $$('button', b).forEach(btn => btn.onclick = async () => {
      if (btn.dataset.a === 'ver') { await api('/records/' + r.id, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({verified: !r.verified})}); pageSource(doc, page, r.id); }
      else if (btn.dataset.a === 'note') openRecord(r);
      else if (btn.dataset.a === 'edit') editRecord(r, () => pageSource(doc, page, r.id));
      else if (confirm('Удалить запись?')) { await api('/records/' + r.id, {method: 'DELETE'}); pageSource(doc, page); }
    });
  });
}

async function pageDocs() {
  view.innerHTML = '<h1>Документы</h1><p class="sub">Загрузите свои «Летописи природы»: PDF, Word, Excel или CSV. Текст сканов распознаёт Yandex Vision OCR, таблицы и графики читает ИИ, записи извлекаются автоматически.</p>' +
    '<div class="grid docs-top"><div class="drop" id="drop"><b>Перетащите файлы сюда</b> или нажмите для выбора<div class="hint">PDF, Word (.docx), Excel (.xlsx), CSV</div><input type="file" id="file" accept=".pdf,.docx,.xlsx,.csv" multiple hidden></div>' +
    '<div class="card"><label class="f">Год летописи (если не определился из названия)</label><input type="number" id="yr" placeholder="например, 2005" min="1900" max="2100"><div class="hint">Применяется при загрузке одного файла.</div></div></div>' +
    '<h2 style="margin-top:40px">Загруженные документы</h2><div id="docs" class="card" style="padding:0;overflow:auto"></div><div id="lib"></div>';
  const drop = $('#drop'), file = $('#file');
  drop.onclick = () => file.click();
  drop.ondragover = e => { e.preventDefault(); drop.classList.add('over'); }; drop.ondragleave = () => drop.classList.remove('over');
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove('over'); upload(e.dataTransfer.files); };
  file.onchange = () => upload(file.files);
  await loadDocs(); loadLib();
  pollTimer = setInterval(loadDocs, 2500);
}
async function upload(files) {
  if (!files.length) return;
  const fd = new FormData(); [...files].forEach(f => fd.append('files', f)); if ($('#yr').value) fd.append('year', $('#yr').value);
  toast('Загрузка…');
  try { const r = await api('/documents', {method: 'POST', body: fd}); const bad = r.filter(x => x.status === 'error'); toast(bad.length ? 'Не принят: ' + bad[0].error : 'Файлы приняты, обработка началась', 4000); } catch (e) { toast('Ошибка: ' + e.message, 4000); }
  loadDocs();
}
async function loadLib() {
  let items = []; try { items = await api('/library'); } catch (e) {}
  const box = $('#lib'); if (!box) return;
  if (!items.length) { box.innerHTML = ''; return; }
  box.innerHTML = '<h2 style="margin-top:40px">Готовые тома из папки</h2><div class="card" style="padding:0;overflow:auto"><table><thead><tr><th></th><th>Файл</th><th>Год</th><th>МБ</th><th>Статус</th></tr></thead><tbody>' +
    items.map(i => '<tr style="cursor:default"><td><input type="checkbox" class="lchk" value="' + esc(i.filename) + '" ' + (i.imported || i.broken ? 'disabled' : '') + '></td><td>' + esc(i.filename) + '</td><td>' + (i.year || '') + '</td><td>' + i.size_mb + '</td><td>' +
      (i.broken ? '<span class="err">файл повреждён (0 байт)</span>' : i.imported ? '<span class="pill ok">загружен</span>' : '<span class="muted">не загружен</span>') + '</td></tr>').join('') +
    '</tbody></table></div><div class="row" style="margin-top:10px"><button class="btn primary" id="limp">Загрузить выбранные</button><span class="hint">Сканы идут в платный OCR: начните с одного-двух томов.</span></div>';
  $('#limp').onclick = async () => {
    const files = $$('.lchk:checked').map(c => c.value); if (!files.length) return toast('Отметьте тома');
    await api('/library/import', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({files})});
    toast('Тома поставлены в очередь'); loadLib(); loadDocs();
  };
}
async function loadDocs() {
  const box = $('#docs'); if (!box) return;
  let docs = []; try { docs = await api('/documents'); } catch (e) { return; }
  if (!docs.length) { box.innerHTML = '<div class="empty">Документов пока нет</div>'; return; }
  const st = {queued: ['в очереди', ''], processing: ['обработка', 'ok'], done: ['готов', 'ok'], error: ['ошибка', 'bad']};
  box.innerHTML = '<table class="docs"><thead><tr><th>Документ</th><th>Год</th><th>Статус</th><th>Прогресс</th><th>Страниц</th><th>OCR / ИИ</th><th>Записей</th><th></th></tr></thead><tbody>' + docs.map(d => {
    const pct = d.pages_total ? Math.round(d.pages_done / d.pages_total * 100) : 0;
    return '<tr style="cursor:default"><td><b>' + esc(d.title || d.filename) + '</b>' + (d.kind && d.kind !== 'pdf' ? ' <span class="tag">' + esc(d.kind) + '</span>' : '') + '<div class="muted">' + esc(d.filename) + '</div>' + (d.error ? '<div class="' + (d.status === 'error' ? 'err' : 'muted') + '">' + esc(d.error) + '</div>' : '') + '</td><td>' + (d.year || '') + '</td>' +
      '<td><span class="pill ' + st[d.status][1] + '">' + st[d.status][0] + '</span>' + (d.stage ? '<div class="muted">' + esc(d.stage) + '</div>' : '') + '</td>' +
      '<td>' + (d.status === 'processing' ? '<div class="bar"><i style="width:' + pct + '%"></i></div>' + pct + '%' : '') + '</td><td>' + (d.pages_total || '') + '</td><td>' + (d.pages_ocr || '') + '</td><td>' + (d.records_count || '') + '</td>' +
      '<td><div class="row-actions">' + (d.status === 'done' ? '<a class="btn small" href="#/doc/' + d.id + '">Покрытие</a> <a class="btn small" href="#/source/' + d.id + '/1">Открыть</a> <button class="btn small" data-a="chk" data-id="' + d.id + '" title="Вторая модель проверит, подтверждаются ли записи их цитатами">Проверить ИИ</button> ' : '') + (d.status !== 'processing' ? '<button class="btn small" data-a="re" data-id="' + d.id + '">Обработать заново</button> <button class="btn small danger" data-a="del" data-id="' + d.id + '">Удалить</button>' : '') + '</div></td></tr>';
  }).join('') + '</tbody></table>';
  $$('button[data-a]', box).forEach(b => b.onclick = async () => {
    try {
      if (b.dataset.a === 'del') { if (!confirm('Удалить документ и все его записи?')) return; await api('/documents/' + b.dataset.id, {method: 'DELETE'}); S.stats = null; }
      else if (b.dataset.a === 'chk') { await api('/documents/' + b.dataset.id + '/ai-check', {method: 'POST'}); toast('Проверка записей запущена в фоне, результат появится в «Качестве»', 5000); }
      else await api('/documents/' + b.dataset.id + '/reprocess', {method: 'POST'});
      loadDocs(); loadLib();
    } catch (e) { toast('Ошибка: ' + e.message, 4000); }
  });
}

async function pageQuality() {
  view.innerHTML = '<h1>Качество извлечения</h1><p class="sub">Насколько можно доверять записям: достоверность, предупреждения и сравнение с эталоном, размеченным вручную по страницам оригинала.</p><div id="q" class="empty">Загрузка…</div>';
  let d; try { d = await api('/quality'); } catch (e) { $('#q').textContent = e.message; return; }
  const total = d.records_total || 0, pct = v => total ? Math.round(v / total * 100) + '%' : '—';
  const hi = d.confidence['высокая (>=0.8)'] || 0, mid = d.confidence['средняя (0.5-0.8)'] || 0, lo = d.confidence['низкая (<0.5)'] || 0;
  const maxFlag = Math.max(1, ...d.flags.map(f => f.count));
  const docTitle = {};
  try { (await api('/documents')).forEach(x => docTitle[x.id] = x.title); } catch (e) {}
  const pcn = v => v == null ? '—' : Math.round(v * 100) + '%';
  const goldBlock = d.gold_checked
    ? '<div class="metric"><b>' + pcn(d.recall) + '</b><span>полнота на эталоне: найдено ' + d.matched + ' из ' + d.gold_checked + '</span></div><div class="metric"><b>' + pcn(d.precision) + '</b><span>точность на размеченных страницах</span></div>'
    : '';
  $('#q').className = '';
  $('#q').innerHTML = '<div class="metrics"><div class="metric"><b>' + total + '</b><span>всего записей</span></div>' +
    '<div class="metric"><b>' + pct(hi) + '</b><span>высокая достоверность (от 80%), ' + hi + '</span></div>' +
    '<div class="metric"><b>' + pct(mid) + '</b><span>средняя (50–80%), ' + mid + '</span></div>' +
    '<div class="metric"><b>' + pct(lo) + '</b><span>низкая (ниже 50%), ' + lo + '</span></div>' + goldBlock +
    '<div class="metric"><b>' + (d.ai_checked || 0) + '</b><span>записей проверено второй моделью, сомнения: ' + (d.ai_doubts || 0) + '</span></div>' +
    '<div class="metric"><b>' + (d.learning_examples || 0) + '</b><span>подтверждённых и исправленных вами записей служат примерами для модели</span></div></div>' +
    (d.gold_checked ? '' : '<p class="hint" style="margin:-12px 0 28px;max-width:70ch">Полноту и точность посчитать пока не с чем: эталон (файл eval/gold.csv, ' + d.gold_total + ' размеченных записей) относится к томам, которые ещё не загружены. Разметьте страницы загруженных томов, и метрики появятся здесь.</p>') +
    '<div class="two"><section><h2>Предупреждения</h2>' + (d.flags.length ? '<div class="flaglist">' + d.flags.map(f =>
      '<div class="flag-row"><span>' + esc(f.text) + '</span><span class="n">' + f.count + '</span><span class="meter"><i style="width:' + Math.max(2, Math.round(f.count / maxFlag * 100)) + '%"></i></span></div>').join('') + '</div>' : '<p class="muted">Предупреждений нет.</p>') +
    '</section><section><h2>Что требует внимания</h2>' +
    ((d.unsupported_documents.length || d.problem_pages.length) ? '<div class="flaglist">' +
      d.unsupported_documents.map(u => '<div><b>' + esc(u.filename) + '</b><div class="err">' + esc(u.error) + '</div></div>').join('') +
      d.problem_pages.map(p => '<div><a href="#/source/' + p.document_id + '/' + p.page + '"><b>' + esc(docTitle[p.document_id] || 'Документ ' + p.document_id) + ', стр. PDF ' + p.page + '</b></a><div class="muted">' + esc(/bad json/.test(p.note || '') ? 'Не удалось разобрать ответ модели при извлечении записей. На странице есть кнопка «Распознать страницу ИИ».' : (p.note || p.source)) + '</div></div>').join('') + '</div>' : '<p class="muted">Неподдерживаемых документов и проблемных страниц нет.</p>') +
    '</section></div>' +
    '<h2 style="margin-top:44px">Примеры ошибок по эталону</h2>' + ((d.missed.length + d.wrong.length) ? '<div class="card" style="padding:0;overflow:auto"><table class="plain"><thead><tr><th>Эталон</th><th>Что извлечено</th></tr></thead><tbody>' +
      d.wrong.map(w => '<tr style="cursor:default"><td>стр. ' + w.gold.page + ': ' + esc(w.gold.event || w.gold.species) + ', ' + esc(w.gold.date) + '</td><td>' + esc(w.found.event) + ', ' + esc(w.found.date || 'без даты') + ' <span class="tag">неверная дата или поле</span></td></tr>').join('') +
      d.missed.map(w => '<tr style="cursor:default"><td>стр. ' + w.gold.page + ': ' + esc(w.gold.event || w.gold.species) + ', ' + esc(w.gold.date) + '</td><td><span class="tag">не найдено</span></td></tr>').join('') + '</tbody></table></div>' : '<p class="muted">Примеров нет: на эталоне ошибок не найдено или эталон не относится к загруженным томам.</p>');
}

async function pageDict() {
  view.innerHTML = '<h1>Справочники</h1><p class="sub">Виды (русские и латинские названия, синонимы) и каталог топонимов с координатами. Новые виды добавляются при обработке автоматически.</p>' +
    '<div class="tabs"><div class="tab active" data-t="sp">Виды</div><div class="tab" data-t="tp">Топонимы</div></div><div id="dict"></div>';
  $$('.tab').forEach(t => t.onclick = () => { $$('.tab').forEach(x => x.classList.toggle('active', x === t)); t.dataset.t === 'sp' ? dictSpecies() : dictTop(); });
  dictSpecies();
}
async function dictSpecies() {
  const box = $('#dict'); box.innerHTML = '<div class="row" style="margin-bottom:10px"><input type="text" id="dq" placeholder="Поиск по названию, роду, синониму" style="max-width:360px"><button class="btn" id="dadd">Добавить вид</button><button class="btn" id="dgbif" title="Проверка латинских названий, рода и группы по GBIF">Уточнить по GBIF</button></div><div id="dl" class="card" style="padding:0;overflow:auto"></div>';
  const load = async () => {
    const list = await api('/species?q=' + encodeURIComponent($('#dq').value));
    $('#dl').innerHTML = '<table><thead><tr><th>Название</th><th>Латынь</th><th>Род</th><th>Группа</th><th>Синонимы</th><th>Источник</th></tr></thead><tbody>' + list.map(s => '<tr style="cursor:default"><td><b>' + esc(s.name_ru) + '</b></td><td class="lat">' + esc(s.name_lat) + '</td><td>' + esc(s.genus) + '</td><td>' + esc(s.group) + '</td><td class="muted">' + esc(s.synonyms.join(', ')) + '</td><td><span class="tag">' + esc(s.source) + '</span></td></tr>').join('') + '</tbody></table>';
  };
  $('#dq').oninput = debounce(load, 250);
  $('#dadd').onclick = async () => {
    const ru = prompt('Русское название'); if (!ru) return;
    const lat = prompt('Латинское название', ''), grp = prompt('Группа (например, птицы)', ''), syn = prompt('Синонимы через запятую', '');
    await api('/species', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name_ru: ru, name_lat: lat || '', group: grp || '', synonyms: (syn || '').split(',').map(s => s.trim()).filter(Boolean)})});
    toast('Вид добавлен'); load();
  };
  $('#dgbif').onclick = async () => {
    if (!confirm('Латинские названия видов будут отправлены в открытый сервис GBIF, чтобы уточнить название, род и группу организмов. Больше никакие данные не передаются. Продолжить?')) return;
    const b = $('#dgbif'); b.disabled = true; b.textContent = 'Проверяем по GBIF…'; toast('Идёт проверка, это может занять около минуты', 5000);
    try {
      const r = await api('/species/gbif', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({accept_external: true, limit: 60})});
      toast('Уточнено видов: ' + r.matched + ', названий исправлено: ' + r.renamed + ', групп обновлено: ' + r.groups_updated + ', не найдено: ' + r.not_found.length + (r.remaining ? '. Осталось: ' + r.remaining + ', нажмите ещё раз' : ''), 8000);
    } catch (er) { toast('Ошибка: ' + er.message, 5000); }
    b.disabled = false; b.textContent = 'Уточнить по GBIF'; load();
  };
  load();
}
async function dictTop() {
  const box = $('#dict');
  box.innerHTML = '<div class="row" style="margin-bottom:14px"><button class="btn small" id="tadd">Добавить место</button><label class="btn small">Импорт из CSV<input type="file" id="tcsv" accept=".csv,text/csv" hidden></label><button class="btn small" id="tdoc">Взять координаты из текстов</button><button class="btn small primary" id="tgeo">Найти координаты онлайн</button><span class="spacer"></span><label class="check" style="height:auto"><input type="checkbox" id="tnocoord"> Только без координат</label></div><p class="hint" id="tinfo" style="margin:0 0 14px"></p><div id="tl" class="card" style="padding:0;overflow:auto"></div>';
  const SRCN = {seed: 'справочник', case: 'пример из кейса', doc: 'из текста документа', osm: 'OpenStreetMap', user: 'вручную', auto: 'найдено в записях'};
  let all = [];
  const render = () => {
    const list = $('#tnocoord').checked ? all.filter(t => t.lat == null) : all;
    const withC = all.filter(t => t.lat != null).length;
    $('#tinfo').textContent = 'Мест: ' + all.length + ', с координатами: ' + withC + '. Нажмите строку, чтобы изменить координаты. Координаты из OpenStreetMap приблизительные, проверьте их.';
    $('#tl').innerHTML = '<table class="plain"><thead><tr><th>Место</th><th>Записей</th><th>Широта</th><th>Долгота</th><th>Источник</th><th>Варианты написания</th></tr></thead><tbody>' + list.map(t =>
      '<tr data-id="' + t.id + '"><td><b>' + esc(t.name) + '</b>' + (t.osm_not_found ? ' <span class="tag" title="В OpenStreetMap не найдено">не найдено онлайн</span>' : '') + '</td><td>' + t.records + '</td><td>' + (t.lat == null ? '<span class="muted">нет</span>' : t.lat.toFixed(5)) + '</td><td>' + (t.lon == null ? '<span class="muted">нет</span>' : t.lon.toFixed(5)) + '</td><td><span class="tag">' + esc(SRCN[t.source] || t.source) + '</span></td><td class="muted">' + esc((t.aliases || []).join(', ')) + '</td></tr>').join('') + '</tbody></table>';
    $$('#tl tbody tr').forEach(tr => tr.onclick = async () => {
      const t = all.find(x => x.id == tr.dataset.id);
      const lat = prompt('Широта для «' + t.name + '» (десятичные градусы)', t.lat == null ? '' : t.lat); if (lat === null) return;
      const lon = prompt('Долгота', t.lon == null ? '' : t.lon); if (lon === null) return;
      await api('/toponyms', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: t.name, lat: lat === '' ? null : +lat.replace(',', '.'), lon: lon === '' ? null : +lon.replace(',', '.'), aliases: t.aliases || []})});
      toast('Координаты сохранены'); load();
    });
  };
  const load = async () => { all = await api('/toponyms'); render(); };
  $('#tnocoord').onchange = render;
  $('#tadd').onclick = async () => { const n = prompt('Название места'); if (!n) return; const lat = prompt('Широта (необязательно)', ''), lon = prompt('Долгота (необязательно)', ''); await api('/toponyms', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: n, lat: lat ? +lat.replace(',', '.') : null, lon: lon ? +lon.replace(',', '.') : null, aliases: []})}); load(); };
  $('#tcsv').onchange = async e => { const fd = new FormData(); fd.append('file', e.target.files[0]); try { const r = await api('/toponyms/import', {method: 'POST', body: fd}); toast('Импортировано мест: ' + r.imported); load(); } catch (er) { toast('Ошибка: ' + er.message, 4000); } };
  $('#tdoc').onclick = async () => { const r = await api('/toponyms/from-documents', {method: 'POST'}); toast(r.updated ? 'Координаты из текстов добавлены для мест: ' + r.updated : 'В текстах документов не найдено новых координат', 4500); load(); };
  $('#tgeo').onclick = async () => {
    if (!confirm('Названия мест без координат (например, «остров Варлама») будут отправлены в сервис OpenStreetMap Nominatim, чтобы найти их положение. Больше никакие данные не передаются. Продолжить?')) return;
    const b = $('#tgeo'); b.disabled = true; b.textContent = 'Ищем координаты…'; toast('Поиск идёт, примерно секунда на место', 5000);
    try {
      const r = await api('/toponyms/geocode', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({accept_external: true, limit: 20})});
      toast('Найдено: ' + r.found.length + ', не найдено: ' + r.not_found.length + (r.remaining ? '. Осталось проверить: ' + r.remaining + ', нажмите кнопку ещё раз' : ''), 7000);
    } catch (er) { toast('Ошибка: ' + er.message, 5000); }
    b.disabled = false; b.textContent = 'Найти координаты онлайн'; load();
  };
  load();
}

const COV = {
  records: ['есть записи', '#0a6b5e'], tables: ['только таблицы', '#2f5d8a'], figures: ['только рисунки', '#6b4fa3'],
  meteo: ['погода (таблицы сохранены)', '#13808f'], text: ['текст без наблюдений', '#b4bdbb'], blank: ['пустая страница', '#e6ebea'],
  failed: ['сбой: нужна проверка', '#b4413a'],
};
function tableHtml(rows, maxRows) {
  if (!rows || !rows.length) return '<div class="muted">пусто</div>';
  const cut = maxRows && rows.length > maxRows + 1;
  const shown = cut ? rows.slice(0, maxRows + 1) : rows;
  return '<div class="tblwrap"><table class="tbl"><thead><tr>' + shown[0].map(c => '<th>' + esc(c) + '</th>').join('') + '</tr></thead><tbody>' +
    shown.slice(1).map(r => '<tr style="cursor:default">' + r.map(c => '<td>' + esc(c) + '</td>').join('') + '</tr>').join('') + '</tbody></table></div>' +
    (cut ? '<div class="hint">Показаны первые ' + maxRows + ' строк из ' + (rows.length - 1) + '. Полностью: «Открыть таблицу» или Excel.</div>' : '');
}
const KIND = {observations: 'наблюдения', weather: 'погода', other: 'прочее'};
const SRC = {vision: 'прочитано ИИ', geometry: 'по ячейкам PDF', ocr: 'OCR', caption: 'только подпись', file: 'из файла'};
function tableCard(t) {
  return '<div class="recbox"><div class="row"><b>' + esc(t.title || 'Таблица без заголовка') + '</b><span class="tag">' + esc(KIND[t.kind] || t.kind) + '</span><span class="tag">' + esc(SRC[t.source] || t.source) + '</span></div>' +
    '<div class="muted">' + esc(t.doc_title) + ', стр. PDF ' + t.page + ', ' + t.n_rows + ' строк × ' + t.n_cols + ' столбцов</div>' +
    '<div class="row" style="margin-top:8px"><a class="btn small" href="#/table/' + t.id + '">Открыть таблицу</a><a class="btn small" href="#/source/' + t.document_id + '/' + t.page + '">Страница источника</a><a class="btn small" href="/api/tables/' + t.id + '/export">⬇ Excel</a></div></div>';
}
function figureCard(f) {
  return '<div class="recbox"><div class="row"><b>' + esc(f.caption || 'Рисунок без подписи') + '</b><span class="tag">' + esc(SRC[f.source] || f.source) + '</span></div>' +
    (f.description ? '<div class="frag">' + esc(f.description.slice(0, 700)) + (f.description.length > 700 ? '…' : '') + '</div>' : '') +
    '<div class="muted">' + esc(f.doc_title) + ', стр. PDF ' + f.page + '</div><div class="row" style="margin-top:6px"><a class="btn small" href="#/source/' + f.document_id + '/' + f.page + '">Страница источника</a></div></div>';
}

async function pageCoverage(id) {
  view.innerHTML = '<div class="empty">Загрузка…</div>';
  let c; try { c = await api('/documents/' + id + '/coverage'); } catch (e) { view.innerHTML = '<div class="empty err">' + esc(e.message) + '</div>'; return; }
  const d = c.document, tabs = S.covTab || 'tables';
  const bad = c.pages.filter(p => p.coverage === 'failed' || /лимит страниц|ИИ-зрение:|OCR:/.test(p.note || ''));
  view.innerHTML = '<div class="row" style="margin-bottom:6px"><a href="#/docs">← Документы</a></div><h1>' + esc(d.title) + ': покрытие</h1>' +
    '<p class="sub">Что сделано с каждой страницей: ничего не теряется молча. Нажмите страницу, чтобы открыть её с записями, таблицами и рисунками.</p>' +
    '<div class="metrics"><div class="metric"><b>' + c.pages.length + '</b><span>страниц</span></div><div class="metric"><b>' + c.records + '</b><span>записей</span></div><div class="metric"><b>' + c.tables + '</b><span>таблиц сохранено</span></div>' +
    '<div class="metric"><b>' + c.figures + '</b><span>рисунков и графиков</span></div><div class="metric"><b>' + c.ocr_pages + '</b><span>страниц через OCR</span></div><div class="metric"><b>' + c.ai_pages + '</b><span>страниц через ИИ-зрение</span></div></div>' +
    '<div class="card"><div class="legend">' + Object.entries(COV).map(([k, v]) => '<span style="--c:' + v[1] + '">' + v[0] + ' (' + (c.totals[k] || 0) + ')</span>').join('') + '</div>' +
    '<div class="covgrid">' + c.pages.map(p => '<a class="cov" href="#/source/' + id + '/' + p.page + '" style="background:' + (COV[p.coverage] || COV.text)[1] + (p.coverage === 'blank' ? ';color:#5b6467' : '') + '" title="стр. ' + p.page + (p.printed ? ' (напечатано ' + esc(p.printed) + ')' : '') + ': ' + (COV[p.coverage] || COV.text)[0] + '; записей ' + p.records + ', таблиц ' + p.tables + ', рисунков ' + p.figures + (p.ai ? '; ИИ' : '') + (p.spread ? '; разворот' : '') + '">' + p.page + '</a>').join('') + '</div></div>' +
    '<div class="tabs"><div class="tab' + (tabs === 'tables' ? ' active' : '') + '" data-t="tables">Таблицы (' + c.tables + ')</div><div class="tab' + (tabs === 'figures' ? ' active' : '') + '" data-t="figures">Рисунки и графики (' + c.figures + ')</div><div class="tab' + (tabs === 'bad' ? ' active' : '') + '" data-t="bad">Требуют внимания (' + bad.length + ')</div></div><div id="covbody"></div>';
  $$('.tab').forEach(t => t.onclick = () => { S.covTab = t.dataset.t; pageCoverage(id); });
  const body = $('#covbody');
  if (tabs === 'tables') { const ts = await api('/tables?doc_id=' + id); body.innerHTML = ts.length ? ts.map(tableCard).join('') : '<div class="empty">Таблиц нет</div>'; }
  else if (tabs === 'figures') { const fs = await api('/figures?doc_id=' + id); body.innerHTML = fs.length ? fs.map(figureCard).join('') : '<div class="empty">Рисунков нет</div>'; }
  else {
    body.innerHTML = bad.length ? bad.map(p => '<div class="recbox"><div class="row"><b>Стр. PDF ' + p.page + '</b><span class="tag">' + esc((COV[p.coverage] || COV.text)[0]) + '</span><span class="spacer"></span>' +
      (c.ai_available ? '<button class="btn small primary" data-p="' + p.page + '">Распознать ИИ</button>' : '') + '<a class="btn small" href="#/source/' + id + '/' + p.page + '">Открыть</a></div><div class="muted">' + esc(p.note || 'ошибка распознавания') + '</div></div>').join('') : '<div class="empty">Проблемных страниц нет</div>';
    $$('button[data-p]', body).forEach(b => b.onclick = () => runAi(id, +b.dataset.p, () => pageCoverage(id), b));
  }
}
async function runAi(doc, page, after, btn) {
  if (btn) { btn.disabled = true; btn.textContent = 'ИИ читает страницу…'; }
  toast('Страница отправлена в ИИ, это занимает до минуты', 5000);
  try {
    const r = await api('/documents/' + doc + '/pages/' + page + '/ai', {method: 'POST'});
    toast('Готово: записей ' + r.records + ', таблиц ' + r.tables + ', рисунков ' + r.figures + (r.truncated ? ' (ответ ИИ обрезан)' : ''), 5000);
  } catch (e) { toast('Ошибка: ' + e.message, 6000); }
  if (after) after();
}
async function pageTables() {
  let docYears = [];
  try { docYears = [...new Set((await api('/documents')).filter(x => x.status === 'done' && x.year).map(x => x.year))].sort((a, b) => a - b); } catch (e) {}
  view.innerHTML = '<h1>Таблицы и рисунки</h1><p class="sub">Все таблицы и графики из загруженных документов. Таблицы и рисунки читает ИИ-зрение, данные сохраняются целиком: их можно открыть, найти по тексту и выгрузить в Excel.</p>' +
    '<div class="card"><div class="grid filters"><div><label class="f">Поиск по заголовку и содержимому</label><input type="text" id="tq" placeholder="например: снегомерная, брусника, температура"></div>' +
    '<div><label class="f">Тип таблицы</label><select id="tk"><option value="">все</option><option value="observations">наблюдения</option><option value="weather">погода</option><option value="other">прочее</option></select></div>' +
    '<div><label class="f">Год документа</label><select id="ty"><option value="">любой</option>' + docYears.map(y => '<option>' + y + '</option>').join('') + '</select></div>' +
    '<div><label class="f">&nbsp;</label><button class="btn primary" id="tgo">Найти</button></div></div></div><div class="tabs"><div class="tab active" data-t="t">Таблицы</div><div class="tab" data-t="f">Рисунки и графики</div></div><div id="tbody"></div>';
  let mode = 't';
  const load = async () => {
    const q = $('#tq').value.trim();
    if (mode === 't') { const ts = await api('/tables?' + qs({q, kind: $('#tk').value, year: $('#ty').value})); $('#tbody').innerHTML = '<div class="muted" style="margin-bottom:8px">найдено: ' + ts.length + '</div>' + (ts.length ? ts.map(tableCard).join('') : '<div class="empty">Ничего не найдено</div>'); }
    else { const fs = await api('/figures?' + qs({q})); $('#tbody').innerHTML = '<div class="muted" style="margin-bottom:8px">найдено: ' + fs.length + '</div>' + (fs.length ? fs.map(figureCard).join('') : '<div class="empty">Ничего не найдено</div>'); }
  };
  $$('.tab').forEach(t => t.onclick = () => { mode = t.dataset.t; $$('.tab').forEach(x => x.classList.toggle('active', x === t)); load(); });
  $('#tgo').onclick = load; $('#tq').addEventListener('keydown', e => { if (e.key === 'Enter') load(); });
  load();
}
async function pageTable(id) {
  view.innerHTML = '<div class="empty">Загрузка…</div>';
  let t; try { t = await api('/tables/' + id); } catch (e) { view.innerHTML = '<div class="empty err">' + esc(e.message) + '</div>'; return; }
  view.innerHTML = '<div class="row" style="margin-bottom:8px"><a href="javascript:history.back()">← Назад</a></div><h1>' + esc(t.title || 'Таблица без заголовка') + '</h1>' +
    '<p class="sub">' + esc(t.doc_title) + ', стр. PDF ' + t.page + ', ' + t.n_rows + ' строк × ' + t.n_cols + ' столбцов · ' + esc(SRC[t.source] || t.source) + ', ' + esc(KIND[t.kind] || t.kind) + '</p>' +
    '<div class="row" style="margin-bottom:12px"><a class="btn primary" href="/api/tables/' + id + '/export">⬇ Excel</a><a class="btn" href="#/source/' + t.document_id + '/' + t.page + '">Страница источника</a></div>' + tableHtml(t.rows, 0);
}
function emptyReason(d) {
  const why = {
    text: '<b>Наблюдений на странице нет.</b><br>Это текст без привязки к видам, датам и местам: титул, выходные данные, оглавление, введение, описание территории и т.п. Текст распознан и доступен кнопкой «Показать текст».',
    tables: '<b>Записей нет, но таблицы сохранены.</b><br>В таблицах ниже данные не про наблюдения видов (например, метеорология, площади, лесной фонд). Их можно открыть и выгрузить в Excel.',
    meteo: '<b>Погода и климат.</b><br>Метеоданные не входят в записи наблюдений. Таблицы страницы сохранены ниже.',
    figures: '<b>Только рисунки.</b><br>Подписи и описания рисунков сохранены ниже.',
    blank: '<b>Пустая или почти пустая страница.</b>',
    failed: '<b>Страница не обработана до конца.</b><br>Нажмите «Распознать страницу ИИ» ниже: ИИ перечитает её как картинку.',
  };
  return (why[d.coverage] || why.text) + (d.note ? '<div class="hint">' + esc(d.note) + '</div>' : '');
}
function pageExtras(d, doc, page) {
  let h = '';
  if (d.tables && d.tables.length) h += '<h2 style="margin-top:18px">Таблицы на странице (' + d.tables.length + ')</h2>' + d.tables.map(t => '<div class="recbox"><div class="row"><b>' + esc(t.title || 'Таблица без заголовка') + '</b><span class="tag">' + esc(KIND[t.kind] || t.kind) + '</span><span class="tag">' + esc(SRC[t.source] || t.source) + '</span></div>' + tableHtml(t.rows, 8) +
    '<div class="row" style="margin-top:8px"><a class="btn small" href="#/table/' + t.id + '">Открыть таблицу</a><a class="btn small" href="/api/tables/' + t.id + '/export">⬇ Excel</a></div></div>').join('');
  if (d.figures && d.figures.length) h += '<h2 style="margin-top:18px">Рисунки и графики (' + d.figures.length + ')</h2>' + d.figures.map(figureCard).join('');
  if (d.ai_available) h += '<div class="card" style="margin-top:16px"><b>Распознано плохо или нужна проверка?</b><div class="hint">Страница целиком уйдёт в ИИ как картинка: он перепишет текст, таблицы и графики, а записи страницы будут пересозданы.</div><div style="margin-top:10px"><button class="btn primary" id="ai-page">' + (d.ai_vision ? 'Перечитать страницу ИИ' : 'Распознать страницу ИИ') + '</button></div></div>';
  return h;
}

async function initStatus() {
  const el = $('#status');
  try { const c = await api('/config'); el.innerHTML = '<span>' + (c.yandex_configured ? 'Yandex Cloud подключён' : 'Yandex Cloud не настроен') + '</span>'; el.className = 'status ' + (c.yandex_configured ? 'ok' : 'bad'); } catch (e) { el.innerHTML = '<span>нет связи с сервером</span>'; el.className = 'status bad'; }
}
window.addEventListener('error', e => { if (view && !view.innerHTML.trim()) view.innerHTML = '<div class="empty err">Ошибка интерфейса: ' + esc(e.message) + '</div>'; });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });
initStatus(); route();
