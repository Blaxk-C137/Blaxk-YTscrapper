const state = {
  route: 'overview',
  config: null,
  stats: null,
  creators: { items: [], total: 0, page: 1, pages: 1, page_size: 25 },
  filters: { q: '', region: '', niche: '', contact: '', stage: '', sort: 'found_at', order: 'desc' },
  selected: new Set(),
  jobs: [],
  jobTimer: null,
  creatorRequest: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const icon = (name, cls = '') => `<svg class="${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`;

function escapeHtml(value = '') {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

function formatCompact(value) {
  const number = Number(value || 0);
  if (!number) return '0';
  return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(number);
}

function formatDate(value, relative = false) {
  if (!value) return 'Unknown';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Unknown';
  if (relative) {
    const days = Math.floor((Date.now() - date.getTime()) / 86400000);
    if (days <= 0) return 'Today';
    if (days === 1) return 'Yesterday';
    if (days < 30) return `${days} days ago`;
  }
  return new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', year: 'numeric' }).format(date);
}

function initials(name = '') {
  const clean = name.trim() || 'Unknown';
  return clean.split(/\s+/).slice(0, 2).map(word => word[0]).join('').toUpperCase();
}

function stageLabel(stage = 'new') {
  return stage.replaceAll('_', ' ').replace(/\b\w/g, char => char.toUpperCase());
}

function jobMarketLabel(job) {
  const names = (job.regions || []).map(region => region.name).filter(Boolean);
  return names.length ? names.join(', ') : (job.region_name || 'Unknown market');
}

function preferenceLabel(preferences = {}) {
  const bits = [];
  const min = Number(preferences.min_subscribers || 0);
  const max = preferences.max_subscribers == null ? null : Number(preferences.max_subscribers);
  if (min || max !== null) bits.push(`${min ? formatCompact(min) : '0'}–${max ? formatCompact(max) : '∞'} subs`);
  if (preferences.min_videos) bits.push(`${preferences.min_videos}+ videos`);
  if (preferences.min_channel_age) bits.push(`${preferences.min_channel_age}+ years`);
  const contactLabels = { email: 'email required', business_email: 'business email required', social: 'social required' };
  if (contactLabels[preferences.contact_requirement]) bits.push(contactLabels[preferences.contact_requirement]);
  return bits.length ? bits.join(' · ') : 'Any creator with public contact details';
}

function socialCount(creator) {
  const socials = creator.socials || {};
  return Object.values(socials).reduce((total, entries) => total + (entries?.length || 0), 0);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { detail = (await response.json()).detail || detail; } catch (_) { /* no-op */ }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

function toast(message, type = 'success') {
  const region = $('#toastRegion');
  const node = document.createElement('div');
  node.className = `toast ${type}`;
  node.innerHTML = `<span>${icon(type === 'error' ? 'x' : 'check')}</span><p>${escapeHtml(message)}</p><button aria-label="Dismiss">×</button>`;
  node.querySelector('button').addEventListener('click', () => node.remove());
  region.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

function routeTo(route, updateHash = true) {
  const allowed = ['overview', 'creators', 'discovery', 'settings'];
  route = allowed.includes(route) ? route : 'overview';
  state.route = route;
  $$('.page').forEach(page => page.classList.toggle('active', page.dataset.page === route));
  $$('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.route === route));
  if (updateHash && location.hash !== `#${route}`) history.pushState(null, '', `#${route}`);
  document.title = `${route[0].toUpperCase() + route.slice(1)} · CreatorScope`;
  window.scrollTo({ top: 0, behavior: 'smooth' });
  closeMobileMenu();
  if (route === 'creators') loadCreators();
  if (route === 'discovery') loadJobs();
}

function setupNavigation() {
  document.addEventListener('click', event => {
    const routeButton = event.target.closest('[data-route]');
    if (routeButton) {
      event.preventDefault();
      routeTo(routeButton.dataset.route);
      return;
    }
    const action = event.target.closest('[data-action]')?.dataset.action;
    if (!action) return;
    if (action === 'new-discovery') routeTo('discovery');
    if (action === 'export-all') window.location.assign('/api/export.csv');
    if (action === 'export-selected') exportSelected();
    if (action === 'copy-command') copyText('python scripts/connect_youtube.py', 'Setup command copied');
  });
  window.addEventListener('popstate', () => routeTo(location.hash.slice(1), false));
  $('#menuBtn').addEventListener('click', openMobileMenu);
  $('#mobileScrim').addEventListener('click', closeMobileMenu);
}

function openMobileMenu() {
  $('#sidebar').classList.add('open');
  $('#mobileScrim').hidden = false;
}

function closeMobileMenu() {
  $('#sidebar').classList.remove('open');
  $('#mobileScrim').hidden = true;
}

async function loadConfig() {
  state.config = await api('/api/config');
  const { regions, niches, facets, session } = state.config;
  $('#regionGrid').innerHTML = regions.map(item => `
    <label class="market-option">
      <input type="checkbox" name="region" value="${escapeHtml(item.code)}" data-name="${escapeHtml(item.name)}">
      <span>${escapeHtml(item.name)}</span>
    </label>`).join('');
  $('#regionFilter').innerHTML = '<option value="">All regions</option>' + facets.regions.map(item => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join('');
  $('#nicheFilter').innerHTML = '<option value="">All niches</option>' + facets.niches.map(item => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join('');
  $('#nicheGrid').innerHTML = niches.map((item, index) => `
    <label class="niche-chip">
      <input type="checkbox" name="niche" value="${escapeHtml(item.query)}" ${index < 2 ? 'checked' : ''}>
      <span>${escapeHtml(item.label)}</span>
    </label>`).join('');
  renderSession(session);
}

function renderSession(session) {
  const card = $('#sessionCard');
  card.classList.toggle('connected', session.configured);
  card.innerHTML = `
    <div class="session-icon">${icon('lock')}</div>
    <div><strong>${session.configured ? 'YouTube connected' : 'Session not connected'}</strong><span>${session.configured ? 'Local profile ready' : 'Setup required'}</span></div><i></i>`;
  const settings = $('#settingsSession');
  settings.classList.toggle('connected', session.configured);
  settings.innerHTML = `<i></i><span>${session.configured ? `Connected${session.updated_at ? ` · ${formatDate(session.updated_at, true)}` : ''}` : 'Not connected · run the command below'}</span>`;
}

async function loadStats() {
  state.stats = await api('/api/stats');
  renderStats();
}

function renderStats() {
  const stats = state.stats;
  $$('[data-stat]').forEach(element => {
    const key = element.dataset.stat;
    const value = stats[key] || 0;
    element.textContent = ['audience'].includes(key) ? formatCompact(value) : Number(value).toLocaleString();
  });
  $$('[data-stat-suffix]').forEach(element => {
    element.textContent = `${stats[element.dataset.statSuffix] || 0}% reachable`;
  });
  $('#navCreatorCount').textContent = formatCompact(stats.total);
  renderRegionChart(stats.top_regions);
  renderPipeline(stats.pipeline);
}

function renderRegionChart(items) {
  const chart = $('#regionChart');
  if (!items?.length) {
    chart.innerHTML = '<p class="run-message">Discover creators to see regional signals.</p>';
    return;
  }
  const max = Math.max(...items.map(item => item.value), 1);
  chart.innerHTML = items.map(item => `
    <div class="bar-item">
      <div class="bar-column" style="height:${Math.max(8, item.value / max * 130)}px"><strong>${item.value}</strong></div>
      <span>${escapeHtml(item.label)}</span>
    </div>`).join('');
}

function renderPipeline(pipeline = {}) {
  const keys = ['new', 'shortlisted', 'contacted', 'partnered'];
  const values = keys.map(key => Number(pipeline[key] || 0));
  const total = values.reduce((sum, value) => sum + value, 0);
  $('#pipelineRing').querySelector('strong').textContent = total;
  const colors = ['var(--blue)', 'var(--coral)', 'var(--lime)', 'var(--amber)'];
  let cursor = 0;
  const stops = values.map((value, index) => {
    const start = cursor;
    cursor += total ? value / total * 100 : 0;
    return `${colors[index]} ${start}% ${cursor}%`;
  });
  if (cursor < 100) stops.push(`#e6e4dc ${cursor}% 100%`);
  $('#pipelineRing').style.background = `conic-gradient(${stops.join(',')})`;
  $('#pipelineLegend').innerHTML = keys.map((key, index) => `<div class="legend-item"><i></i><span>${stageLabel(key)}</span><b>${values[index]}</b></div>`).join('');
}

async function loadRecent() {
  const data = await api('/api/creators?page=1&page_size=4&sort=found_at&order=desc');
  const container = $('#recentCreators');
  if (!data.items.length) {
    container.innerHTML = '<div class="active-empty"><h2>No creators yet</h2><p>Launch a discovery run to build your directory.</p></div>';
    return;
  }
  container.innerHTML = data.items.map(creator => `
    <div class="mini-row">
      <div class="creator-cell"><div class="avatar">${escapeHtml(initials(creator.title))}</div><div><strong>${escapeHtml(creator.title)}</strong><span>${escapeHtml(creator.handle || creator.channel_id)}</span></div></div>
      <div class="mini-stat"><span>Audience</span><strong>${escapeHtml(creator.subscriber_count || 'Unknown')}</strong></div>
      <div class="mini-stat"><span>Market</span><strong>${escapeHtml(creator.country || creator.region_searched || 'Unknown')}</strong></div>
      <div class="mini-stat"><span>Contact</span><strong>${creator.emails?.length ? `${creator.emails.length} email${creator.emails.length > 1 ? 's' : ''}` : `${socialCount(creator)} social${socialCount(creator) === 1 ? '' : 's'}`}</strong></div>
      <button class="arrow-circle" data-open-creator="${escapeHtml(creator.channel_id)}" aria-label="View ${escapeHtml(creator.title)}">${icon('arrow')}</button>
    </div>`).join('');
}

function setupCreatorFilters() {
  let debounce;
  $('#creatorSearch').addEventListener('input', event => {
    clearTimeout(debounce);
    debounce = setTimeout(() => { state.filters.q = event.target.value.trim(); state.creators.page = 1; loadCreators(); }, 250);
  });
  const mappings = { regionFilter: 'region', nicheFilter: 'niche', contactFilter: 'contact', stageFilter: 'stage' };
  Object.entries(mappings).forEach(([id, key]) => {
    $(`#${id}`).addEventListener('change', event => { state.filters[key] = event.target.value; state.creators.page = 1; loadCreators(); });
  });
  $('#sortCreators').addEventListener('change', event => {
    [state.filters.sort, state.filters.order] = event.target.value.split(':');
    loadCreators();
  });
  $('#clearFilters').addEventListener('click', () => {
    state.filters = { q: '', region: '', niche: '', contact: '', stage: '', sort: 'found_at', order: 'desc' };
    $('#creatorSearch').value = '';
    ['regionFilter', 'nicheFilter', 'contactFilter', 'stageFilter'].forEach(id => $(`#${id}`).value = '');
    $('#sortCreators').value = 'found_at:desc';
    loadCreators();
  });
  $('#prevPage').addEventListener('click', () => { if (state.creators.page > 1) { state.creators.page--; loadCreators(); } });
  $('#nextPage').addEventListener('click', () => { if (state.creators.page < state.creators.pages) { state.creators.page++; loadCreators(); } });
  $('#selectAll').addEventListener('change', event => {
    state.creators.items.forEach(item => event.target.checked ? state.selected.add(item.channel_id) : state.selected.delete(item.channel_id));
    renderCreatorTable();
  });
  $('#clearSelection').addEventListener('click', () => { state.selected.clear(); renderCreatorTable(); });
  $$('[data-bulk]').forEach(button => button.addEventListener('click', () => bulkAction(button.dataset.bulk, button.dataset.stage)));
  document.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault(); routeTo('creators'); setTimeout(() => $('#creatorSearch').focus(), 80);
    }
  });
}

async function loadCreators() {
  if (state.creatorRequest) state.creatorRequest.abort();
  state.creatorRequest = new AbortController();
  const params = new URLSearchParams({ page: state.creators.page, page_size: 25, ...state.filters });
  try {
    const data = await api(`/api/creators?${params}`, { signal: state.creatorRequest.signal });
    state.creators = data;
    renderCreatorTable();
  } catch (error) {
    if (error.name !== 'AbortError') toast(error.message, 'error');
  }
}

function creatorContact(creator) {
  if (creator.emails?.length) return `<span class="contact-pill" title="${escapeHtml(creator.emails[0])}">${icon('mail')}<span>${escapeHtml(creator.emails[0])}</span></span>`;
  if (socialCount(creator)) return `<span class="contact-pill social">${icon('globe')}<span>${socialCount(creator)} social link${socialCount(creator) === 1 ? '' : 's'}</span></span>`;
  return '<span class="mini-stat"><strong>None found</strong></span>';
}

function renderCreatorTable() {
  const { items, total, page, pages, page_size } = state.creators;
  $('#creatorResultCount').textContent = total.toLocaleString();
  $('#creatorTableBody').innerHTML = items.map(creator => {
    const selected = state.selected.has(creator.channel_id);
    return `<tr data-creator-row="${escapeHtml(creator.channel_id)}" class="${selected ? 'selected' : ''}">
      <td class="check-col"><input type="checkbox" data-select="${escapeHtml(creator.channel_id)}" ${selected ? 'checked' : ''} aria-label="Select ${escapeHtml(creator.title)}"></td>
      <td><div class="creator-cell"><div class="avatar table-avatar">${escapeHtml(initials(creator.title))}</div><div><strong>${escapeHtml(creator.title)}</strong><span>${escapeHtml(creator.handle || creator.channel_id)}</span></div></div></td>
      <td><div class="audience"><strong>${escapeHtml(creator.subscriber_count || 'Unknown')}</strong><span>${creator.video_count ? `${escapeHtml(creator.video_count)} videos` : 'Subscribers'}</span></div></td>
      <td><div class="market-cell"><span>${escapeHtml(creator.country || 'Not listed')}</span><small>${escapeHtml(creator.region_searched || 'Unknown region')}</small></div></td>
      <td>${creatorContact(creator)}</td>
      <td><select class="stage-select" data-stage-change="${escapeHtml(creator.channel_id)}" data-stage="${escapeHtml(creator.pipeline_stage)}" aria-label="Partnership stage">${stageOptions(creator.pipeline_stage)}</select></td>
      <td><div class="row-actions"><button class="favorite-btn ${creator.favorite ? 'active' : ''}" data-favorite="${escapeHtml(creator.channel_id)}" data-value="${creator.favorite ? 'false' : 'true'}" aria-label="${creator.favorite ? 'Remove favorite' : 'Add favorite'}">${icon('heart')}</button><button data-open-creator="${escapeHtml(creator.channel_id)}" aria-label="View details">${icon('chevron')}</button></div></td>
    </tr>`;
  }).join('');
  $('#tableEmpty').hidden = total !== 0;
  $('.table-scroll').hidden = total === 0;
  $('.pagination').hidden = total === 0;
  $('#paginationLabel').textContent = total ? `Showing ${(page - 1) * page_size + 1}–${Math.min(page * page_size, total)} of ${total}` : 'No results';
  $('#pageIndicator').textContent = `${page} / ${pages}`;
  $('#prevPage').disabled = page <= 1;
  $('#nextPage').disabled = page >= pages;
  const allVisible = items.length && items.every(item => state.selected.has(item.channel_id));
  $('#selectAll').checked = Boolean(allVisible);
  $('#selectAll').indeterminate = !allVisible && items.some(item => state.selected.has(item.channel_id));
  $('#bulkBar').hidden = state.selected.size === 0;
  $('#selectedCount').textContent = state.selected.size;
}

function stageOptions(selected) {
  return ['new', 'shortlisted', 'contacted', 'replied', 'partnered', 'not_a_fit']
    .map(stage => `<option value="${stage}" ${stage === selected ? 'selected' : ''}>${stageLabel(stage)}</option>`).join('');
}

function setupCreatorActions() {
  document.addEventListener('change', async event => {
    const id = event.target.dataset.stageChange;
    if (!id) return;
    try {
      await updateCreator(id, { pipeline_stage: event.target.value });
      event.target.dataset.stage = event.target.value;
      toast(`Moved to ${stageLabel(event.target.value)}`);
    } catch (error) { toast(error.message, 'error'); }
  });
  document.addEventListener('click', async event => {
    const select = event.target.closest('[data-select]');
    if (select) return;
    const favorite = event.target.closest('[data-favorite]');
    if (favorite) {
      event.stopPropagation();
      try { await updateCreator(favorite.dataset.favorite, { favorite: favorite.dataset.value === 'true' }); toast(favorite.dataset.value === 'true' ? 'Added to favorites' : 'Removed from favorites'); }
      catch (error) { toast(error.message, 'error'); }
      return;
    }
    const opener = event.target.closest('[data-open-creator]');
    if (opener) { event.stopPropagation(); openCreator(opener.dataset.openCreator); return; }
    const row = event.target.closest('[data-creator-row]');
    if (row && !event.target.closest('select, button, input, a')) openCreator(row.dataset.creatorRow);
  });
  document.addEventListener('change', event => {
    const id = event.target.dataset.select;
    if (!id) return;
    event.target.checked ? state.selected.add(id) : state.selected.delete(id);
    renderCreatorTable();
  });
}

async function updateCreator(id, payload) {
  const updated = await api(`/api/creators/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(payload) });
  const local = state.creators.items.find(item => item.channel_id === id);
  if (local) Object.assign(local, updated);
  renderCreatorTable();
  loadStats();
  loadRecent();
  return updated;
}

async function bulkAction(action, stage = null) {
  if (!state.selected.size) return;
  try {
    const result = await api('/api/creators/bulk', { method: 'POST', body: JSON.stringify({ channel_ids: [...state.selected], action, stage }) });
    toast(`${result.updated} creator${result.updated === 1 ? '' : 's'} updated`);
    state.selected.clear();
    await Promise.all([loadCreators(), loadStats(), loadRecent()]);
  } catch (error) { toast(error.message, 'error'); }
}

function exportSelected() {
  if (!state.selected.size) return toast('Select at least one creator', 'error');
  window.location.assign(`/api/export.csv?ids=${encodeURIComponent([...state.selected].join(','))}`);
}

async function openCreator(id) {
  $('#drawerBackdrop').hidden = false;
  $('#creatorDrawer').classList.add('open');
  $('#creatorDrawer').setAttribute('aria-hidden', 'false');
  $('#drawerBody').innerHTML = '<div class="loading-block tall"></div><div class="loading-row"></div><div class="loading-row"></div>';
  try {
    const creator = await api(`/api/creators/${encodeURIComponent(id)}`);
    renderDrawer(creator);
  } catch (error) { closeDrawer(); toast(error.message, 'error'); }
}

function renderDrawer(creator) {
  const contacts = [
    ...(creator.emails || []).map(value => ({ icon: 'mail', value })),
    ...Object.entries(creator.socials || {}).flatMap(([platform, values]) => (values || []).map(value => ({ icon: 'globe', value: `${platform}: @${value}` }))),
  ];
  $('#drawerBody').innerHTML = `
    <div class="drawer-profile"><div class="avatar">${escapeHtml(initials(creator.title))}</div><div><h2>${escapeHtml(creator.title)}</h2><p>${escapeHtml(creator.handle || creator.channel_id)}</p></div></div>
    <div class="drawer-buttons"><a class="btn btn-primary" href="${escapeHtml(creator.youtube_url)}" target="_blank" rel="noopener">${icon('play')} Open channel</a><button class="btn btn-quiet favorite-btn ${creator.favorite ? 'active' : ''}" data-favorite="${escapeHtml(creator.channel_id)}" data-value="${creator.favorite ? 'false' : 'true'}">${icon('heart')}</button></div>
    <div class="drawer-stats"><div class="drawer-stat"><strong>${escapeHtml(creator.subscriber_count || '—')}</strong><span>Subscribers</span></div><div class="drawer-stat"><strong>${escapeHtml(creator.video_count || '—')}</strong><span>Videos</span></div><div class="drawer-stat"><strong>${escapeHtml(creator.total_views || '—')}</strong><span>Views</span></div></div>
    <section class="drawer-section"><label>Partnership stage</label><select id="drawerStage" data-drawer-id="${escapeHtml(creator.channel_id)}">${stageOptions(creator.pipeline_stage)}</select></section>
    <section class="drawer-section"><h3>Contact points</h3><div class="contact-list">${contacts.length ? contacts.map(contact => `<div class="contact-item">${icon(contact.icon)}<span>${escapeHtml(contact.value)}</span><button data-copy="${escapeHtml(contact.value)}" aria-label="Copy">${icon('copy')}</button></div>`).join('') : '<p>No contact points were found.</p>'}</div></section>
    <section class="drawer-section"><h3>Market & niche</h3><div class="drawer-tags"><span>${escapeHtml(creator.country || creator.region_searched || 'Unknown market')}</span>${(creator.niches || []).map(niche => `<span>${escapeHtml(niche)}</span>`).join('')}</div></section>
    <section class="drawer-section"><h3>About</h3><p>${escapeHtml(creator.description || 'No description available.')}</p></section>
    <section class="drawer-section"><label for="creatorNotes">Private notes</label><textarea id="creatorNotes" placeholder="Add context for your next conversation…">${escapeHtml(creator.notes || '')}</textarea><div class="note-actions"><button class="btn btn-dark" id="saveNotes" data-drawer-id="${escapeHtml(creator.channel_id)}">Save notes</button></div></section>
    <section class="drawer-section"><p>Discovered ${formatDate(creator.found_at)} · ${escapeHtml(creator.region_searched || 'Unknown region')}</p></section>`;
  $('#drawerStage').addEventListener('change', async event => {
    try { await updateCreator(event.target.dataset.drawerId, { pipeline_stage: event.target.value }); toast('Pipeline stage updated'); }
    catch (error) { toast(error.message, 'error'); }
  });
  $('#saveNotes').addEventListener('click', async event => {
    try { await updateCreator(event.currentTarget.dataset.drawerId, { notes: $('#creatorNotes').value }); toast('Private notes saved'); }
    catch (error) { toast(error.message, 'error'); }
  });
}

function closeDrawer() {
  $('#creatorDrawer').classList.remove('open');
  $('#creatorDrawer').setAttribute('aria-hidden', 'true');
  setTimeout(() => { $('#drawerBackdrop').hidden = true; }, 220);
}

function setupDrawer() {
  $('#closeDrawer').addEventListener('click', closeDrawer);
  $('#drawerBackdrop').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeDrawer(); });
  document.addEventListener('click', event => {
    const copy = event.target.closest('[data-copy]');
    if (copy) copyText(copy.dataset.copy, 'Copied to clipboard');
  });
}

async function copyText(text, message) {
  try { await navigator.clipboard.writeText(text); toast(message); }
  catch (_) { toast('Could not access the clipboard', 'error'); }
}

function updateMarketPicker() {
  const inputs = $$('input[name="region"]', $('#regionGrid'));
  const selected = inputs.filter(input => input.checked);
  inputs.forEach(input => { input.disabled = selected.length >= 10 && !input.checked; });
  const names = selected.map(input => input.dataset.name);
  $('#marketPickerLabel').textContent = !names.length
    ? 'Choose one or more markets'
    : names.length <= 2 ? names.join(' + ') : `${names.length} markets selected`;
  $('#marketPickerCount').hidden = !selected.length;
  $('#marketPickerCount').textContent = selected.length;
}

function setupDiscovery() {
  const picker = $('#marketPicker');
  const pickerButton = $('#marketPickerButton');
  const marketMenu = $('#marketMenu');
  pickerButton.addEventListener('click', () => {
    const opening = marketMenu.hidden;
    marketMenu.hidden = !opening;
    picker.classList.toggle('open', opening);
    pickerButton.setAttribute('aria-expanded', String(opening));
  });
  $('#regionGrid').addEventListener('change', updateMarketPicker);
  $('#clearMarkets').addEventListener('click', () => {
    $$('input[name="region"]', $('#regionGrid')).forEach(input => { input.checked = false; });
    updateMarketPicker();
  });
  document.addEventListener('click', event => {
    if (!picker.contains(event.target)) {
      marketMenu.hidden = true;
      picker.classList.remove('open');
      pickerButton.setAttribute('aria-expanded', 'false');
    }
  });

  const range = $('#discoveryLimit');
  const updateRange = () => {
    $('#limitOutput').value = range.value;
    const progress = (range.value - range.min) / (range.max - range.min) * 100;
    range.style.background = `linear-gradient(90deg, var(--blue) 0 ${progress}%, #dedcd5 ${progress}%)`;
  };
  range.addEventListener('input', updateRange);
  updateRange();
  $('#discoveryForm').addEventListener('submit', startDiscovery);
  $('#refreshJobs').addEventListener('click', loadJobs);
  document.addEventListener('click', async event => {
    const cancel = event.target.closest('[data-cancel-job]');
    if (!cancel) return;
    try { await api(`/api/jobs/${cancel.dataset.cancelJob}/cancel`, { method: 'POST' }); toast('Stopping safely…'); await loadJobs(); }
    catch (error) { toast(error.message, 'error'); }
  });
}

async function startDiscovery(event) {
  event.preventDefault();
  if (!state.config?.session?.configured) {
    toast('Connect a local YouTube session before starting discovery', 'error');
    routeTo('settings');
    return;
  }
  const regions = $$('input[name="region"]:checked').map(input => input.value);
  const niches = $$('input[name="niche"]:checked').map(input => input.value);
  if (!regions.length) return toast('Choose at least one target market', 'error');
  if (!niches.length) return toast('Choose at least one niche', 'error');
  const [minimumAudience, maximumAudience] = $('#audiencePreference').value.split(':');
  const preferences = {
    min_subscribers: Number(minimumAudience || 0),
    max_subscribers: maximumAudience ? Number(maximumAudience) : null,
    min_videos: Number($('#videoPreference').value || 0),
    min_channel_age: Number($('#agePreference').value || 0),
    contact_requirement: $('#contactPreference').value,
  };
  const button = $('#startDiscovery');
  button.disabled = true;
  button.innerHTML = `${icon('clock')} Starting…`;
  try {
    await api('/api/jobs', {
      method: 'POST',
      body: JSON.stringify({
        regions,
        niches,
        limit: Number($('#discoveryLimit').value),
        preferences,
      }),
    });
    toast('Discovery started');
    await loadJobs();
    scheduleJobPoll();
  } catch (error) { toast(error.message, 'error'); }
  finally { button.disabled = false; button.innerHTML = `${icon('radar')} Start discovery`; }
}

async function loadJobs() {
  try {
    const data = await api('/api/jobs?limit=8');
    state.jobs = data.items;
    renderJobs();
    const active = state.jobs.some(job => ['queued', 'running'].includes(job.status));
    $('#jobPulse').hidden = !active;
    if (active) {
      scheduleJobPoll();
    } else if (state.jobTimer) {
      clearInterval(state.jobTimer);
      state.jobTimer = null;
      await Promise.all([loadStats(), loadRecent()]);
    }
  } catch (error) { toast(error.message, 'error'); }
}

function scheduleJobPoll() {
  if (state.jobTimer) return;
  let ticks = 0;
  state.jobTimer = setInterval(async () => {
    ticks += 1;
    await loadJobs();
    // Creator cards do not need to refresh as often as the live job counters.
    if (ticks % 5 === 0) await Promise.all([loadStats(), loadRecent()]);
  }, 1200);
}

function renderJobs() {
  const active = state.jobs.find(job => ['queued', 'running'].includes(job.status)) || state.jobs[0];
  $('#activeRunEmpty').hidden = Boolean(active);
  $('#activeRunContent').hidden = !active;
  if (active) renderActiveJob(active);
  const history = $('#jobHistory');
  history.innerHTML = state.jobs.length ? state.jobs.slice(0, 5).map(job => `
    <div class="job-row ${escapeHtml(job.status)}">
      <span>${icon(job.status === 'completed' ? 'check' : job.status === 'running' ? 'radar' : job.status === 'failed' ? 'x' : 'clock')}</span>
      <div><strong>${escapeHtml(jobMarketLabel(job))} · ${job.niches.map(item => item.label).join(', ')}</strong><small>${escapeHtml(preferenceLabel(job.preferences))} · ${stageLabel(job.status)} · ${formatDate(job.created_at, true)}</small></div>
      <b>${job.found}/${job.target}</b>
    </div>`).join('') : '<p class="run-message">No discovery runs yet.</p>';
}

function renderActiveJob(job) {
  const active = ['queued', 'running'].includes(job.status);
  const percent = Math.max(0, Math.min(100, job.progress || 0));
  $('#activeRunContent').innerHTML = `
    <div class="run-top"><span class="run-status ${escapeHtml(job.status)}"><i></i>${escapeHtml(stageLabel(job.status))}</span><small>${formatDate(job.created_at, true)}</small></div>
    <h2 class="run-title">${job.regions?.length > 1 ? `${job.regions.length}-market` : escapeHtml(jobMarketLabel(job))} creator search</h2>
    <p class="run-subtitle">${escapeHtml(jobMarketLabel(job))} · ${job.niches.map(item => escapeHtml(item.label)).join(' · ')}</p>
    <p class="run-preferences">${escapeHtml(preferenceLabel(job.preferences))}</p>
    <div class="progress-track"><i style="width:${percent}%"></i></div>
    <div class="progress-meta"><span>${percent}% of save target</span><span>${job.found} / ${job.target} saved</span></div>
    <div class="run-counters"><div class="run-counter"><strong>${job.discovered}</strong><span>Fresh pool</span></div><div class="run-counter"><strong>${job.processed}</strong><span>Reviewed</span></div><div class="run-counter"><strong>${job.filtered || 0}</strong><span>Outside fit</span></div><div class="run-counter"><strong>${job.skipped}</strong><span>No contact / failed</span></div></div>
    <p class="run-message">${escapeHtml(job.message)}</p>
    ${job.error ? `<div class="run-error">${escapeHtml(job.error)}</div>` : ''}
    ${active ? `<div class="run-actions"><button class="btn btn-quiet" data-cancel-job="${escapeHtml(job.id)}">${icon('stop')} Stop safely</button></div>` : ''}`;
}

function setupExports() {
  $$('[data-export]').forEach(button => button.addEventListener('click', () => window.location.assign(`/api/export.${button.dataset.export}`)));
  $('#clearWorkspace').addEventListener('click', async () => {
    const confirmed = window.confirm(
      'Clear every creator, processed channel, and discovery run? This cannot be undone.'
    );
    if (!confirmed) return;
    try {
      const result = await api('/api/creators', { method: 'DELETE' });
      state.selected.clear();
      state.creators.page = 1;
      await Promise.all([loadConfig(), loadStats(), loadRecent(), loadCreators(), loadJobs()]);
      const count = result.cleared.creators || 0;
      toast(`Workspace cleared — ${count} creator${count === 1 ? '' : 's'} removed`);
      routeTo('overview');
    } catch (error) {
      toast(error.message, 'error');
    }
  });
}

async function initialize() {
  setupNavigation();
  setupCreatorFilters();
  setupCreatorActions();
  setupDrawer();
  setupDiscovery();
  setupExports();
  try {
    await loadConfig();
    await Promise.all([loadStats(), loadRecent(), loadCreators(), loadJobs()]);
  } catch (error) {
    console.error(error);
    toast(`Could not load the workspace: ${error.message}`, 'error');
  }
  routeTo(location.hash.slice(1) || 'overview', false);
}

document.addEventListener('DOMContentLoaded', initialize);
