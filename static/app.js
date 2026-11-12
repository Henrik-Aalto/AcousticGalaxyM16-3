'use strict';

/* ================= Konstanten ================= */
const SCALE = 120;
const DIST_MIN = 16;
const DIST_MAX = 950;
const HOME_POS = new THREE.Vector3(0, 115, 385);
const FOCUS_MS = 16000;

/* Im Nextcloud-Betrieb liegt die App unter /index.php/apps/<id>/ */
const API_BASE = (() => {
  const m = window.location.pathname.match(/^(.*\/apps\/[^/]+)(?:\/.*)?$/);
  return m ? m[1] + '/' : '/';
})();
function api(path) { return API_BASE + String(path).replace(/^\//, ''); }

/* ================= Zustand ================= */
let renderer, scene, camera, controls;
let corona = null, coronaCore = null, coronaLayers = [];
let tiltGroup, galaxyGroup, groundGroup, starGroup;
let pointsObj = null, selectGlow = null;

let items = [], clusters = [], byId = new Map(), labelEls = [];
let selectedId = null, focus = null;
let selectedItem = null;
let similarResults = [];
let playlist = [], playlistIdx = -1;
let playedThisCycle = new Set();
let lyricManualUntil = 0;
let lyricEls = [], lyricTimes = [], lyricActive = -1;
const LS = 'agm163.';
(function migrateLS() {
  try {
    const old = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith('acousti.')) old.push(k);
    }
    for (const k of old) {
      const nk = LS + k.slice('acousti.'.length);
      if (localStorage.getItem(nk) === null) localStorage.setItem(nk, localStorage.getItem(k));
      localStorage.removeItem(k);
    }
  } catch (_) { /* egal */ }
})();

let favorites = loadFavorites();
let favServer = false;
let favObj = null, pathsGroup = null;
let starLabelSlots = [], starLabelNext = 0;
let playlistMode = 'similar', collection = null, collectionObj = null, holdTarget = null;
let autoSpin = true, showLabels = true;
let showRegions = (localStorage.getItem(LS + 'regions') ?? '1') !== '0';
let targetDist = HOME_POS.length(), desiredTarget = new THREE.Vector3(0, 0, 0);
let pollTimer = null, toastTimer = null, down = null;

let audioCtx = null, analyser = null, freqData = null, mediaSrc = null, playing = false;

/* DOM */
const $ = (id) => document.getElementById(id);
const sceneEl = $('scene');
const labelsEl = $('labels');
const starlabelsEl = $('starlabels');
const tooltip = $('tooltip');
const statusEl = $('status');
const legendEl = $('legend');
const drawer = $('drawer');
const scrim = $('scrim');
const pathList = $('path-list');
const detail = $('detail');
const toastEl = $('toast');
const audioEl = $('audio');
const leaderEl = $('leader');
const leaderLine = $('leader-line');
const leaderDot = $('leader-dot');
const startag = $('startag');
const stTitle = $('st-title');
const stSub = $('st-sub');
const stLyrics = $('st-lyrics');

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const tmpV = new THREE.Vector3();
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

/* ================= Texturen ================= */
function radialTexture(inner, outer) {
  const s = 64;
  const c = document.createElement('canvas');
  c.width = c.height = s;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
  grad.addColorStop(0, inner);
  grad.addColorStop(0.4, outer);
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, s, s);
  return new THREE.CanvasTexture(c);
}
function starTexture() {
  const s = 64;
  const c = document.createElement('canvas');
  c.width = c.height = s;
  const g = c.getContext('2d');
  const grad = g.createRadialGradient(s / 2, s / 2, 0, s / 2, s / 2, s / 2);
  grad.addColorStop(0, 'rgba(255,255,255,1)');
  grad.addColorStop(0.16, 'rgba(255,255,255,0.92)');
  grad.addColorStop(0.32, 'rgba(205,242,255,0.42)');
  grad.addColorStop(0.55, 'rgba(120,205,255,0.13)');
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = grad;
  g.fillRect(0, 0, s, s);
  return new THREE.CanvasTexture(c);
}
const pointTexture = starTexture();
const glowTexture = radialTexture('rgba(255,255,255,0.9)', 'rgba(90,220,255,0.3)');

function colorForItem(it) {
  if (it.color) return new THREE.Color(it.color);
  const nx = clamp((it.x + 1) / 2, 0, 1);
  const ny = clamp((it.y + 1) / 2, 0, 1);
  return new THREE.Color().setHSL(0.55 - ny * 0.42, 0.85, 0.42 + nx * 0.28);
}

/* ================= Favoriten (Fixsterne) ================= */
function loadFavorites() {
  try {
    const raw = JSON.parse(localStorage.getItem(LS + 'favorites') || '[]');
    return new Set(Array.isArray(raw) ? raw : []);
  } catch (_) { return new Set(); }
}

function saveFavorites() {
  try { localStorage.setItem(LS + 'favorites', JSON.stringify([...favorites])); } catch (_) { /* egal */ }
}

/* ================= Nextcloud-Anbindung ================= */
async function initServerFeatures() {
  try {
    const res = await fetch(api('/api/settings'));
    if (!res.ok) return;
    const cfg = await res.json();
    document.body.classList.add('nextcloud');
    const pathsBtn = $('btn-paths');
    if (pathsBtn) pathsBtn.style.display = 'none';
    const musicBtn = $('btn-music');
    if (musicBtn) musicBtn.style.display = '';
    favServer = true;
    try {
      const r = await fetch(api('/api/favorites'));
      if (r.ok) {
        const d = await r.json();
        if (Array.isArray(d.ids)) {
          favorites = new Set(d.ids);
          saveFavorites();
          refreshFavStars();
        }
      }
    } catch (_) { /* egal */ }
    if (!cfg.has_key) openNcSetup();
  } catch (_) { /* Standalone: kein /api/settings */ }
}

function openNcSetup() {
  const d = $('nc-drawer'), s = $('nc-scrim');
  if (d) d.classList.add('open');
  if (s) s.classList.add('show');
  const st = $('nc-status');
  if (st) st.textContent = '';
}

function closeNcSetup() {
  const d = $('nc-drawer'), s = $('nc-scrim');
  if (d) d.classList.remove('open');
  if (s) s.classList.remove('show');
}

async function saveNcSetup() {
  const keyInput = $('nc-key');
  const status = $('nc-status');
  const key = ((keyInput && keyInput.value) || '').trim();
  if (!key) {
    if (status) status.textContent = 'Bitte den API-Key aus der Music-App einfügen.';
    return;
  }
  if (status) status.textContent = 'Speichere …';
  try {
    const res = await fetch(api('/api/settings'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ apiKey: key })
    });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    if (keyInput) keyInput.value = '';
    closeNcSetup();
    toast('Music verbunden, Analyse startet');
    loadStatusAndPoll();
  } catch (_) {
    if (status) status.textContent = 'Speichern fehlgeschlagen.';
  }
}

function setFavBtn(btn, on, label) {
  btn.textContent = (on ? '★' : '☆') + (label ? ' ' + label : '');
  btn.classList.toggle('on', on);
  btn.title = on ? 'Fixstern entfernen' : 'Als Fixstern merken';
}

function toggleFav(id) {
  const on = !favorites.has(id);
  if (on) favorites.add(id); else favorites.delete(id);
  saveFavorites();
  if (favServer) {
    fetch(api('/api/favorites/' + encodeURIComponent(id)), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ on })
    }).catch(() => {});
  }
  refreshFavStars();
  document.querySelectorAll('.sim-fav').forEach((b) => {
    if (b.dataset.id === id) setFavBtn(b, favorites.has(id));
  });
  const dfav = $('d-fav');
  const sfav = $('st-fav');
  if (dfav && dfav.dataset.id === id) setFavBtn(dfav, favorites.has(id), 'Fixstern');
  if (sfav && sfav.dataset.id === id) setFavBtn(sfav, favorites.has(id));
  toast(favorites.has(id) ? '★ Fixstern gesetzt' : '☆ Fixstern entfernt');
}

function refreshFavStars() {
  if (favObj) {
    galaxyGroup.remove(favObj);
    favObj.geometry.dispose();
    favObj.material.dispose();
    favObj = null;
  }
  const list = items.filter((i) => favorites.has(i.id));
  if (!list.length) return;
  const pos = new Float32Array(list.length * 3);
  list.forEach((it, i) => {
    pos[i * 3] = it.x * SCALE;
    pos[i * 3 + 1] = it.y * SCALE;
    pos[i * 3 + 2] = it.z * SCALE;
  });
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.computeBoundingSphere();
  const m = new THREE.PointsMaterial({
    size: 10, map: pointTexture, color: 0xffd36b, transparent: true,
    opacity: 0.95, alphaTest: 0.01, depthWrite: false,
    blending: THREE.AdditiveBlending, sizeAttenuation: true
  });
  favObj = new THREE.Points(g, m);
  favObj.userData.list = list;
  galaxyGroup.add(favObj);
}

/* ================= Cover ================= */
function setCover(id) {
  const box = $('d-cover');
  const img = $('d-cover-img');
  const want = api('/api/cover/' + encodeURI(id));
  if (img.dataset.want === want) return;
  img.dataset.want = want;
  box.classList.remove('has');
  img.onload = () => box.classList.add('has');
  img.onerror = () => box.classList.remove('has');
  img.src = want;
}

function setTagCover(id) {
  const box = $('st-cover');
  const img = $('st-cover-img');
  const want = api('/api/cover/' + encodeURI(id));
  if (img.dataset.want === want) return;
  img.dataset.want = want;
  box.classList.remove('has');
  img.onload = () => box.classList.add('has');
  img.onerror = () => box.classList.remove('has');
  img.src = want;
}

/* ================= Flugbahnen ================= */
function posOf(id) {
  const it = byId.get(id);
  if (it) return it;
  return similarResults.find((r) => r.id === id) || null;
}

function drawPaths() {
  if (!pathsGroup) {
    pathsGroup = new THREE.Group();
    galaxyGroup.add(pathsGroup);
  }
  for (const o of [...pathsGroup.children]) {
    pathsGroup.remove(o);
    if (o.geometry) o.geometry.dispose();
    if (o.material) o.material.dispose();
  }
  if (!selectedItem || playlist.length < 2) return;
  const start = new THREE.Vector3(
    selectedItem.x * SCALE, selectedItem.y * SCALE, selectedItem.z * SCALE);
  let drawn = 0;
  for (const id of playlist) {
    if (drawn >= 32) break;
    if (!id || id === selectedItem.id) continue;
    const t = posOf(id);
    if (!t) continue;
    const end = new THREE.Vector3(t.x * SCALE, t.y * SCALE, t.z * SCALE);
    const dist = start.distanceTo(end);
    if (dist < 1) continue;
    const mid = start.clone().add(end).multiplyScalar(0.5);
    mid.y += Math.min(95, 18 + dist * 0.3);
    const curve = new THREE.QuadraticBezierCurve3(start, mid, end);
    const geo = new THREE.BufferGeometry().setFromPoints(curve.getPoints(30));
    const col = (t.color && String(t.color).startsWith('#')) ? t.color : '#38e8ff';
    const mat = new THREE.LineDashedMaterial({
      color: new THREE.Color(col), dashSize: 4, gapSize: 4,
      transparent: true, opacity: 0.45, depthWrite: false
    });
    const line = new THREE.Line(geo, mat);
    line.computeLineDistances();
    line.renderOrder = 2;
    pathsGroup.add(line);
    drawn++;
  }
}

/* ================= Künstler-/Albumkarte ================= */
function sameText(a, b) {
  return String(a || '').trim().toLowerCase() === String(b || '').trim().toLowerCase();
}

function isSampler(name) {
  return /various|verschiedene/i.test(String(name || ''));
}

function pickRegionTrack(c) {
  const members = items.filter((i) => i.cluster === c.id);
  if (!members.length) return null;
  const names = String(c.name || '').split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
  for (const n of names) {
    const hit = members.find((i) => {
      const a = String(i.artist || '').trim().toLowerCase();
      return a && (a === n || a.includes(n) || n.includes(a));
    });
    if (hit) return hit;
  }
  const counts = new Map();
  for (const i of members) {
    const a = String(i.artist || '').trim();
    if (!a || isSampler(a)) continue;
    counts.set(a, (counts.get(a) || 0) + 1);
  }
  let top = '', best = 0;
  for (const [a, n] of counts) if (n > best) { best = n; top = a; }
  if (top) {
    const hit = members.find((i) => sameText(i.artist, top));
    if (hit) return hit;
  }
  return members[0];
}

function fitToPoints(list) {
  if (!list.length) return;
  let cx = 0, cy = 0, cz = 0;
  for (const it of list) { cx += it.x; cy += it.y; cz += it.z; }
  const center = new THREE.Vector3(
    (cx / list.length) * SCALE, (cy / list.length) * SCALE, (cz / list.length) * SCALE);
  galaxyGroup.localToWorld(center);
  let maxD = 0;
  for (const it of list) {
    tmpV.set(it.x * SCALE, it.y * SCALE, it.z * SCALE);
    galaxyGroup.localToWorld(tmpV);
    maxD = Math.max(maxD, tmpV.distanceTo(center));
  }
  holdTarget = center;
  desiredTarget.copy(center);
  targetDist = clamp(maxD * 2.1 + 50, DIST_MIN, DIST_MAX);
}

function refreshCollectionStars() {
  if (collectionObj) {
    galaxyGroup.remove(collectionObj);
    collectionObj.geometry.dispose();
    collectionObj.material.dispose();
    collectionObj = null;
  }
  if (!collection) return;
  const list = collection.list;
  if (!list.length) return;
  const pos = new Float32Array(list.length * 3);
  list.forEach((it, i) => {
    pos[i * 3] = it.x * SCALE;
    pos[i * 3 + 1] = it.y * SCALE;
    pos[i * 3 + 2] = it.z * SCALE;
  });
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.computeBoundingSphere();
  const m = new THREE.PointsMaterial({
    size: 11, map: pointTexture, color: 0x8ff0ff, transparent: true,
    opacity: 0.95, alphaTest: 0.01, depthWrite: false,
    blending: THREE.AdditiveBlending, sizeAttenuation: true
  });
  collectionObj = new THREE.Points(g, m);
  galaxyGroup.add(collectionObj);
}

function folderOf(id) {
  const p = String(id || '');
  const i = p.lastIndexOf('/');
  return i > 0 ? p.slice(0, i) : '';
}

function loadCollection(kind, value, label, silent = false) {
  let list;
  if (kind === 'artist') {
    if (!value) return;
    label = label || value;
    list = items.filter((i) => sameText(i.artist, value));
  } else {
    // Album über den Ordner bestimmen – funktioniert auch bei Samplern.
    // value ist bereits das Album-Verzeichnis (siehe Click-Handler).
    const dir = String(value || '');
    if (!dir) return;
    label = label || dir;
    list = items.filter((i) => folderOf(i.id) === dir);
  }
  if (!list.length) { toast('Keine Titel gefunden'); return; }
  list.sort((a, b) =>
    String(a.album || '').localeCompare(String(b.album || ''), 'de')
    || String(a.title || a.name).localeCompare(String(b.title || b.name), 'de'));

  playlistMode = 'collection';
  collection = { kind, value, label, list };
  similarResults = list.map((i) => ({
    id: i.id, name: i.name, x: i.x, y: i.y, z: i.z,
    cluster: i.cluster, color: i.color, genre: i.genre,
    features: {}, similarity: 0
  }));
  playlist = list.map((i) => i.id);
  playlistIdx = 0;
  playedThisCycle = new Set();

  const first = byId.get(list[0].id) || list[0];
  selectItem(first, !silent, true);
  focus = null;
  fitToPoints(list);
  refreshCollectionStars();
  renderCollection();
  if (!silent) toast(kind === 'artist' ? 'Künstlerkarte: ' + label : 'Albumkarte: ' + label);
}

function renderCollection() {
  if (!collection) return;
  const list = collection.list;
  const head = (collection.kind === 'artist' ? 'Künstler: ' : 'Album: ')
    + collection.label + ' · ' + list.length;
  renderPlaylistPanel(list, head, 'collection');
}

/* ================= Suche ================= */
let searchSel = -1, searchItems = [], searchTimer = null;

function initSearch() {
  const input = $('search');
  const box = $('search-results');
  const clear = $('search-clear');
  const close = () => { box.style.display = 'none'; searchSel = -1; };
  input.addEventListener('input', () => {
    input.closest('.searchbox').classList.toggle('filled', !!input.value);
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => renderSearch(input.value.trim()), 120);
  });
  input.addEventListener('focus', () => { if (input.value.trim()) renderSearch(input.value.trim()); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { input.value = ''; close(); input.blur(); return; }
    if (!box.children.length || box.style.display === 'none') return;
    if (e.key === 'ArrowDown') { e.preventDefault(); moveSearch(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); moveSearch(-1); }
    else if (e.key === 'Enter') { e.preventDefault(); pickSearch(searchSel >= 0 ? searchSel : 0); }
  });
  clear.addEventListener('click', () => { input.value = ''; input.closest('.searchbox').classList.remove('filled'); close(); input.focus(); });
  document.addEventListener('pointerdown', (e) => {
    if (!e.target.closest('.searchbox')) close();
  });
}

function renderSearch(q) {
  const box = $('search-results');
  if (!q || !items.length) { box.style.display = 'none'; return; }
  const needle = q.toLowerCase();
  const words = needle.split(/\s+/).filter(Boolean);
  const score = (it) => {
    const a = (it.artist || '').toLowerCase();
    const t = (it.title || it.name || '').toLowerCase();
    const al = (it.album || '').toLowerCase();
    let s = 0, rest = 0;
    for (const w of words) {
      if (t.startsWith(w)) s += 8; else if (t.includes(w)) s += 5;
      else if (a.startsWith(w)) s += 6; else if (a.includes(w)) s += 4;
      else if (al.includes(w)) s += 3;
      else rest++;
    }
    return rest === 0 ? s : 0;
  };
  const hits = [];
  for (const it of items) {
    const s = score(it);
    if (s > 0) hits.push([s, it]);
  }
  hits.sort((a, b) => b[0] - a[0] || String(a[1].artist).localeCompare(String(b[1].artist), 'de'));
  searchItems = hits.slice(0, 12).map((h) => h[1]);
  searchSel = -1;
  if (!searchItems.length) {
    box.innerHTML = '<div class="sr-empty">Nichts gefunden für „' + escapeHtml(q) + '“</div>';
    box.style.display = 'block';
    return;
  }
  box.innerHTML = '';
  searchItems.forEach((it, i) => {
    const row = document.createElement('div');
    row.className = 'sr-item';
    const img = document.createElement('img');
    img.className = 'sr-cover';
    img.alt = '';
    img.src = api('/api/cover/' + encodeURI(it.id));
    img.addEventListener('error', () => img.remove());
    const tx = document.createElement('div');
    tx.className = 'sr-text';
    const t = document.createElement('div');
    t.className = 'sr-title';
    t.textContent = it.title || it.name;
    const a = document.createElement('div');
    a.className = 'sr-artist';
    a.textContent = [it.artist, it.album].filter(Boolean).join(' · ');
    tx.appendChild(t); tx.appendChild(a);
    row.appendChild(img); row.appendChild(tx);
    row.addEventListener('click', () => pickSearch(i));
    box.appendChild(row);
  });
  box.style.display = 'block';
}

function moveSearch(d) {
  const box = $('search-results');
  const rows = [...box.querySelectorAll('.sr-item')];
  if (!rows.length) return;
  searchSel = (searchSel + d + rows.length) % rows.length;
  rows.forEach((r, i) => r.classList.toggle('sel', i === searchSel));
  rows[searchSel].scrollIntoView({ block: 'nearest' });
}

function pickSearch(i) {
  const it = searchItems[i];
  if (!it) return;
  const input = $('search');
  input.value = '';
  $('search-results').style.display = 'none';
  input.blur();
  selectItem(it, true);
  toast((it.title || it.name) + ' – ' + (it.artist || ''));
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

/* ================= Detail-Panel: Größenverstellung ================= */
function initPanelResize() {
  const handle = $('detail-resize');
  if (!handle) return;
  let st = null;
  try { st = JSON.parse(localStorage.getItem(LS + 'panel') || 'null'); } catch (_) { /* egal */ }
  if (st && st.w && st.h) applyPanelSize(st.w, st.h);

  handle.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const rect = detail.getBoundingClientRect();
    const startX = e.clientX, startY = e.clientY;
    const w0 = rect.width, h0 = rect.height;
    const move = (ev) => {
      const w = clamp(w0 + (ev.clientX - startX), 300, Math.min(900, window.innerWidth - 40));
      const h = clamp(h0 + (startY - ev.clientY), 240, window.innerHeight - 40);
      applyPanelSize(w, h);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      const r = detail.getBoundingClientRect();
      try { localStorage.setItem(LS + 'panel', JSON.stringify({ w: Math.round(r.width), h: Math.round(r.height) })); } catch (_) { /* egal */ }
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  });
  handle.addEventListener('dblclick', () => {
    detail.classList.remove('resized');
    detail.style.width = '';
    detail.style.height = '';
    try { localStorage.removeItem(LS + 'panel'); } catch (_) { /* egal */ }
    toast('Panelgröße zurückgesetzt');
  });
}

function applyPanelSize(w, h) {
  detail.classList.add('resized');
  detail.style.width = w + 'px';
  detail.style.height = h + 'px';
}

/* ================= Szene ================= */
function init() {
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(window.innerWidth, window.innerHeight);
  sceneEl.appendChild(renderer.domElement);

  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x03050a);
  scene.fog = new THREE.FogExp2(0x03050a, 0.0011);

  camera = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 1, 6000);
  camera.position.copy(HOME_POS);

  controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.07;
  controls.rotateSpeed = 0.62;
  controls.panSpeed = 0.8;
  controls.enableZoom = false;
  controls.target.set(0, 0, 0);
  controls.addEventListener('end', () => {
    targetDist = camera.position.distanceTo(controls.target);
  });

  raycaster.params.Points.threshold = 3.2;

  // Scheibe liegt in der Y-Z-Ebene -> um 90° drehen, damit sie vor dem Betrachter liegt
  tiltGroup = new THREE.Group();
  tiltGroup.rotation.x = -0.55;
  tiltGroup.rotation.z = 0.10;
  scene.add(tiltGroup);

  galaxyGroup = new THREE.Group();
  galaxyGroup.rotation.order = 'YXZ';
  galaxyGroup.rotation.y = -Math.PI / 2;
  tiltGroup.add(galaxyGroup);

  groundGroup = new THREE.Group();
  scene.add(groundGroup);

  starGroup = new THREE.Group();
  scene.add(starGroup);

  buildStarfield();
  buildGround();
  buildSpectrum();
  buildSelectGlow();
  bindEvents();
  initSearch();
  initPanelResize();
  $('btn-regions').classList.toggle('on', showRegions);
  $('btn-labels').classList.toggle('on', showLabels);
  makeDraggable(legendEl, '.lg-title');
  makeDraggable(detail, 'h2, .detail-toggle');
  animate();
}

function buildStarfield() {
  const N = 2000;
  const g = new THREE.BufferGeometry();
  const pos = new Float32Array(N * 3);
  const col = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) {
    const r = 900 + Math.random() * 1500;
    const th = Math.random() * Math.PI * 2;
    const ph = Math.acos(2 * Math.random() - 1);
    pos[i * 3] = r * Math.sin(ph) * Math.cos(th);
    pos[i * 3 + 1] = r * Math.cos(ph);
    pos[i * 3 + 2] = r * Math.sin(ph) * Math.sin(th);
    const t = Math.random();
    col[i * 3] = 0.55 + t * 0.45;
    col[i * 3 + 1] = 0.65 + t * 0.35;
    col[i * 3 + 2] = 1.0;
  }
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  const m = new THREE.PointsMaterial({
    size: 2.4, map: pointTexture, vertexColors: true, transparent: true,
    opacity: 0.7, depthWrite: false, blending: THREE.AdditiveBlending, fog: false
  });
  starGroup.add(new THREE.Points(g, m));
}

function buildGround() {
  const grid = new THREE.GridHelper(SCALE * 3.4, 30, 0x0e3a55, 0x0a2233);
  grid.position.y = -SCALE * 1.35;
  grid.material.transparent = true;
  grid.material.opacity = 0.28;
  groundGroup.add(grid);
}

/* ---------------- Spektrum (pulsierende Korona) ---------------- */
function coronaTexture() {
  const size = 512, cx = size / 2;
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d');
  for (let i = 0; i < 240; i++) {
    const a = Math.random() * Math.PI * 2;
    const len = size * 0.5 * (0.30 + Math.random() * 0.66);
    const w = 0.5 + Math.random() * 2.2;
    const grd = g.createLinearGradient(cx, cx, cx + Math.cos(a) * len, cx + Math.sin(a) * len);
    grd.addColorStop(0, 'rgba(255,255,255,0.5)');
    grd.addColorStop(0.4, 'rgba(150,225,255,0.14)');
    grd.addColorStop(1, 'rgba(0,0,0,0)');
    g.strokeStyle = grd;
    g.lineWidth = w;
    g.beginPath();
    g.moveTo(cx + Math.cos(a) * size * 0.05, cx + Math.sin(a) * size * 0.05);
    g.lineTo(cx + Math.cos(a) * len, cx + Math.sin(a) * len);
    g.stroke();
  }
  const rg = g.createRadialGradient(cx, cx, 0, cx, cx, size * 0.34);
  rg.addColorStop(0, 'rgba(255,255,255,0.85)');
  rg.addColorStop(0.4, 'rgba(140,220,255,0.22)');
  rg.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = rg;
  g.beginPath();
  g.arc(cx, cx, size * 0.34, 0, Math.PI * 2);
  g.fill();
  return new THREE.CanvasTexture(c);
}

function buildSpectrum() {
  const tex = coronaTexture();
  corona = new THREE.Group();
  corona.visible = false;
  galaxyGroup.add(corona);

  const layer = (base, opacity, spin, k) => {
    const mat = new THREE.SpriteMaterial({
      map: tex, color: 0xbfe9ff, transparent: true, opacity: opacity,
      depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending, fog: false
    });
    const sp = new THREE.Sprite(mat);
    sp.scale.set(base, base, 1);
    corona.add(sp);
    coronaLayers.push({ sp: sp, base: base, opacity: opacity, spin: spin, k: k, rot: Math.random() * 6.28 });
    return sp;
  };
  layer(24, 0.55, 0.16, 1.0);
  layer(42, 0.26, -0.09, 0.62);

  coronaCore = new THREE.Sprite(new THREE.SpriteMaterial({
    map: glowTexture, color: 0xdaf6ff, transparent: true, opacity: 0.55,
    depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending, fog: false
  }));
  coronaCore.scale.set(15, 15, 1);
  corona.add(coronaCore);
}

function updateSpectrum(now, dt) {
  if (!corona) return;
  if (!selectedItem) { corona.visible = false; return; }

  corona.visible = true;
  corona.position.set(selectedItem.x * SCALE, selectedItem.y * SCALE, selectedItem.z * SCALE);

  let level;
  if (analyser && playing) {
    analyser.getByteFrequencyData(freqData);
    let sum = 0;
    const hi = Math.max(1, Math.floor(freqData.length * 0.35));
    for (let i = 0; i < hi; i++) sum += freqData[i];
    level = sum / (hi * 255);
  } else {
    level = 0.10 + 0.05 * Math.sin(now / 780) + 0.02 * Math.sin(now / 313);
  }
  level = clamp(level, 0, 1);

  const col = (selectedItem.color && String(selectedItem.color).startsWith('#'))
    ? selectedItem.color : '#8fe6ff';
  for (const L of coronaLayers) {
    const s = L.base * (0.82 + level * 0.9 + 0.06 * Math.sin(now / 240 + L.spin * 40));
    L.sp.scale.set(s, s, 1);
    L.sp.material.rotation += L.spin * dt * (0.6 + level * 2.2);
    L.sp.material.color.set(col);
    L.sp.material.opacity = clamp(L.opacity * (0.5 + level * 1.5), 0, 0.95);
  }
  coronaCore.material.color.set(col);
  coronaCore.scale.setScalar(13 + level * 26);
  coronaCore.material.opacity = clamp(0.35 + level * 0.55, 0, 0.95);
}

/* ---------------- Marker ---------------- */
function buildSelectGlow() {
  const mat = new THREE.SpriteMaterial({
    map: glowTexture, transparent: true, depthWrite: false,
    blending: THREE.AdditiveBlending, color: 0x38e8ff, opacity: 0.9, fog: false
  });
  selectGlow = new THREE.Sprite(mat);
  selectGlow.scale.set(15, 15, 1);
  selectGlow.visible = false;
  galaxyGroup.add(selectGlow);
}

/* ================= Punkte ================= */
function buildPoints(list) {
  if (pointsObj) {
    galaxyGroup.remove(pointsObj);
    pointsObj.geometry.dispose();
    pointsObj.material.dispose();
  }
  const n = list.length;
  const pos = new Float32Array(n * 3);
  const col = new Float32Array(n * 3);
  const c = new THREE.Color();
  for (let i = 0; i < n; i++) {
    const it = list[i];
    pos[i * 3] = it.x * SCALE;
    pos[i * 3 + 1] = it.y * SCALE;
    pos[i * 3 + 2] = it.z * SCALE;
    c.copy(colorForItem(it));
    col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  g.computeBoundingSphere();
  const m = new THREE.PointsMaterial({
    size: 2.7, map: pointTexture, vertexColors: true, transparent: true,
    opacity: 0.95, alphaTest: 0.01, depthWrite: false,
    blending: THREE.AdditiveBlending, sizeAttenuation: true
  });
  pointsObj = new THREE.Points(g, m);
  pointsObj.userData.list = list;
  galaxyGroup.add(pointsObj);
}

/* ---------------- Labels (HTML-Overlay) ---------------- */
function buildLabels() {
  labelsEl.innerHTML = '';
  labelEls = [];
  // Zoom-Schwelle je Region: große Cluster sind immer sichtbar,
  // kleine erscheinen erst beim Hineinzoomen
  const order = [...clusters].sort((a, b) => b.size - a.size);
  const m = Math.max(1, order.length - 1);
  order.forEach((c, k) => { c._thr = DIST_MAX - 10 - (k / m) * (DIST_MAX - 180); });
  for (const c of clusters) {
    if (!c.name) continue;
    const el = document.createElement('button');
    el.className = 'cluster-label';
    const color = c.color || '#38e8ff';
    el.innerHTML =
      `<span class="dot" style="background:${color};color:${color}"></span>` +
      `<span class="n">${escapeHtml(c.name)}</span>` +
      `<span class="c">${c.size}</span>`;
    el.title = c.subtitle || '';
    el.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const it = pickRegionTrack(c);
      if (it) selectItem(it, true);
    });
    labelsEl.appendChild(el);
    labelEls.push({ el, c });
  }
  labelsEl.style.display = showLabels ? '' : 'none';
}

function updateLyricsScroll() {
  if (!stLyrics.textContent) return;
  // Bei synchronisierten Lyrics übernimmt updateLyricSync das Scrollen
  if (lyricTimes.some((t) => t != null)) return;
  const dur = audioEl.duration;
  if (!dur || !isFinite(dur)) return;
  if (performance.now() < lyricManualUntil) return;
  const p = clamp(audioEl.currentTime / dur, 0, 1);
  const max = stLyrics.scrollHeight - stLyrics.clientHeight;
  if (max > 0) stLyrics.scrollTop = p * max;
}

/* ---------------- Synchronisierte Lyrics ---------------- */
function stripTimestamps(s) {
  return String(s || '').replace(/\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]/g, '').trim();
}

function renderLyrics(trackId, plain, synced) {
  if (selectedId !== trackId) return;
  stLyrics.innerHTML = '';
  lyricEls = [];
  lyricTimes = [];
  lyricActive = -1;
  lyricManualUntil = 0;

  const add = (text) => {
    const d = document.createElement('div');
    d.className = 'lyr-line';
    d.textContent = stripTimestamps(text);
    stLyrics.appendChild(d);
    return d;
  };

  if (Array.isArray(synced) && synced.length) {
    for (const s of synced) {
      lyricEls.push(add(s.text));
      lyricTimes.push(Number(s.t) || 0);
    }
  } else {
    const txt = stripTimestamps(plain).trim();
    if (txt) {
      for (const line of txt.split(/\r?\n/)) {
        lyricEls.push(add(line));
        lyricTimes.push(null);
      }
    }
  }
  stLyrics.scrollTop = 0;
}

function updateLyricSync() {
  if (!lyricEls.length) return;
  if (!lyricTimes.some((t) => t != null)) return;
  const t = audioEl.currentTime + 0.12;
  let idx = -1;
  for (let i = 0; i < lyricTimes.length; i++) {
    if (lyricTimes[i] == null) continue;
    if (lyricTimes[i] <= t) idx = i; else break;
  }
  if (idx === lyricActive) return;
  if (lyricActive >= 0 && lyricEls[lyricActive]) lyricEls[lyricActive].classList.remove('active');
  lyricActive = idx;
  const el = idx >= 0 ? lyricEls[idx] : null;
  if (!el) return;
  el.classList.add('active');
  if (performance.now() < lyricManualUntil) return;
  const top = el.offsetTop - stLyrics.clientHeight / 2 + el.offsetHeight / 2;
  stLyrics.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
}

/* ---------------- Stern-Namen beim Hineinzoomen ---------------- */
const STAR_GRACE_MS = 900;   // Nachlauf, bevor ein Platz neu belegt wird
const STAR_SPACING = 96;     // Mindestabstand neuer Labels in px

function ensureStarSlots(n) {
  while (starLabelSlots.length < n) {
    const el = document.createElement('button');
    el.className = 'star-label hidden';
    el.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const it = byId.get(el.dataset.id);
      if (it) selectItem(it, true);
    });
    starlabelsEl.appendChild(el);
    starLabelSlots.push({ el: el, id: null, lost: 0 });
  }
}

function resetStarSlots() {
  for (const s of starLabelSlots) {
    s.el.classList.add('hidden');
    s.id = null;
    s.lost = 0;
  }
}

function setStarLabelText(slot, it) {
  const art = it.artist && !/various|verschiedene/i.test(it.artist) ? it.artist : '';
  slot.el.textContent = art
    ? `${art} – ${it.title || it.name}`
    : (it.title || it.name);
}

function setStarLabelPos(slot, sx, sy, animate) {
  const el = slot.el;
  const left = sx.toFixed(1) + 'px';
  const top = sy.toFixed(1) + 'px';
  if (animate) {
    el.style.left = left;
    el.style.top = top;
    return;
  }
  // Neuer Stern im selben Label: Sprung ohne Übergang, sonst gleitet es quer
  el.classList.add('nofx');
  el.style.left = left;
  el.style.top = top;
  void el.offsetWidth;
  el.classList.remove('nofx');
}

function updateStarLabels(now) {
  if (now - starLabelNext < 160) return;
  starLabelNext = now;
  if (!showLabels || !items.length) { resetStarSlots(); return; }
  const camD = camera.position.distanceTo(controls.target);
  const near = DIST_MAX * 0.58;
  const zt = clamp((near - camD) / (near - 120), 0, 1);
  if (zt < 0.03) { resetStarSlots(); return; }
  const want = Math.max(2, Math.round(2 + zt * 6));
  ensureStarSlots(want);

  const w = window.innerWidth, h = window.innerHeight;
  const shown = new Map();
  const cands = [];
  for (const it of items) {
    if (!it.name) continue;
    tmpV.set(it.x * SCALE, it.y * SCALE, it.z * SCALE);
    galaxyGroup.localToWorld(tmpV);
    const dist = tmpV.distanceTo(camera.position);
    tmpV.project(camera);
    if (tmpV.z > 1 || tmpV.z < -1) continue;
    if (Math.abs(tmpV.x) > 1.06 || Math.abs(tmpV.y) > 1.06) continue;
    const sx = (tmpV.x * 0.5 + 0.5) * w;
    const sy = (-tmpV.y * 0.5 + 0.5) * h;
    const center = Math.hypot(sx - w / 2, sy - h / 2) / Math.hypot(w / 2, h / 2);
    const score = (1 - center) * 1.6 - dist / (SCALE * 5);
    const rec = { it, sx, sy, score };
    shown.set(it.id, rec);
    if (Math.abs(tmpV.x) < 0.92 && Math.abs(tmpV.y) < 0.92) cands.push(rec);
  }

  // 1) Bestehende Labels halten – sie bleiben stehen, solange ihr Stern
  //    sichtbar ist. Erst nach einer Gnadenfrist wird der Platz frei.
  const used = new Set();
  const placed = [];
  for (let i = 0; i < starLabelSlots.length; i++) {
    const slot = starLabelSlots[i];
    const el = slot.el;
    if (i >= want) {
      if (slot.id) {
        el.classList.add('hidden');
        if (!slot.lost) slot.lost = now;
        if (now - slot.lost >= STAR_GRACE_MS) { slot.id = null; slot.lost = 0; }
        else used.add(slot.id);
      } else {
        el.classList.add('hidden');
      }
      continue;
    }
    if (!slot.id) { el.classList.add('hidden'); continue; }
    const c = shown.get(slot.id);
    if (!c) {
      el.classList.add('hidden');
      if (!slot.lost) slot.lost = now;
      if (now - slot.lost >= STAR_GRACE_MS) { slot.id = null; slot.lost = 0; }
      else used.add(slot.id);
      continue;
    }
    const fresh = slot.lost > 0;
    slot.lost = 0;
    used.add(slot.id);
    setStarLabelText(slot, c.it);
    setStarLabelPos(slot, c.sx, c.sy, !fresh);
    el.dataset.id = c.it.id;
    el.classList.remove('hidden');
    el.classList.toggle('fav', favorites.has(c.it.id));
    placed.push({ x: c.sx, y: c.sy });
  }

  // 2) Freie Plätze nur mit gut sichtbaren, ausreichend entfernten
  //    Kandidaten füllen. Reicht es nicht, bleiben 1–2 Labels weg.
  cands.sort((a, b) => b.score - a.score);
  let fill = 0;
  for (let i = 0; i < want; i++) if (!starLabelSlots[i].id) fill++;
  for (const c of cands) {
    if (fill <= 0) break;
    if (used.has(c.it.id)) continue;
    if (c.score < 0.12) continue;
    if (placed.some((p) => Math.hypot(p.x - c.sx, p.y - c.sy) < STAR_SPACING)) continue;
    let slot = null;
    for (let i = 0; i < want; i++) {
      if (!starLabelSlots[i].id) { slot = starLabelSlots[i]; break; }
    }
    if (!slot) break;
    slot.id = c.it.id;
    slot.lost = 0;
    used.add(c.it.id);
    placed.push({ x: c.sx, y: c.sy });
    fill--;
    setStarLabelText(slot, c.it);
    setStarLabelPos(slot, c.sx, c.sy, false);
    slot.el.dataset.id = c.it.id;
    slot.el.classList.remove('hidden');
    slot.el.classList.toggle('fav', favorites.has(c.it.id));
  }
}

function updateStartag() {
  if (!selectedItem) {
    startag.classList.remove('show');
    leaderEl.style.display = 'none';
    return;
  }
  tmpV.set(selectedItem.x * SCALE, selectedItem.y * SCALE, selectedItem.z * SCALE);
  galaxyGroup.localToWorld(tmpV);
  const p = tmpV.project(camera);
  if (p.z > 1 || p.x < -1.4 || p.x > 1.4 || p.y < -1.4 || p.y > 1.4) {
    startag.classList.remove('show');
    leaderEl.style.display = 'none';
    return;
  }
  const w = window.innerWidth, h = window.innerHeight;
  const px = (p.x * 0.5 + 0.5) * w;
  const py = (-p.y * 0.5 + 0.5) * h;
  const tw = startag.offsetWidth || 268;
  const th = startag.offsetHeight || 84;
  const gap = 74;
  let left = px + gap;
  if (left + tw > w - 14) left = px - gap - tw;
  left = clamp(left, 14, Math.max(14, w - tw - 14));
  let top = clamp(py - th * 0.55, 14, Math.max(14, h - th - 14));
  startag.style.left = left + 'px';
  startag.style.top = top + 'px';
  startag.classList.add('show');
  leaderEl.style.display = '';
  const ax = px < left ? left : (px > left + tw ? left + tw : px);
  const ay = clamp(py, top, top + th);
  leaderLine.setAttribute('x1', px.toFixed(1));
  leaderLine.setAttribute('y1', py.toFixed(1));
  leaderLine.setAttribute('x2', ax.toFixed(1));
  leaderLine.setAttribute('y2', ay.toFixed(1));
  leaderDot.setAttribute('cx', px.toFixed(1));
  leaderDot.setAttribute('cy', py.toFixed(1));
}

function updateLabels() {
  if (!showLabels) return;
  const w = window.innerWidth, h = window.innerHeight;
  const camD = camera.position.distanceTo(controls.target);
  for (const L of labelEls) {
    const thr = L.c._thr != null ? L.c._thr : DIST_MAX;
    if (camD > thr) {
      L.el.classList.add('hidden');
      continue;
    }
    tmpV.set(L.c.x * SCALE, L.c.y * SCALE + 9, L.c.z * SCALE);
    galaxyGroup.localToWorld(tmpV);
    tmpV.project(camera);
    if (tmpV.z > 1 || tmpV.x < -1.15 || tmpV.x > 1.15 || tmpV.y < -1.15 || tmpV.y > 1.15) {
      L.el.classList.add('hidden');
      continue;
    }
    L.el.classList.remove('hidden');
    L.el.style.left = ((tmpV.x * 0.5 + 0.5) * w) + 'px';
    L.el.style.top = ((-tmpV.y * 0.5 + 0.5) * h) + 'px';
    L.el.classList.toggle('soft', camD > thr * 0.72);
  }
}

/* ================= Events ================= */
function bindEvents() {
  window.addEventListener('resize', onResize);
  const dom = renderer.domElement;

  // Zoom am Fenster (nicht am Canvas), damit kein Overlay das Rad abfangen kann
  window.addEventListener('wheel', (e) => {
    const t = e.target;
    if (t && t.closest && t.closest('.legend, .detail, .drawer, .scrim, .topbar, .st-lyrics')) return;
    e.preventDefault();
    const k = Math.exp((e.deltaY > 0 ? 1 : -1) * 0.16);
    targetDist = clamp(targetDist * k, DIST_MIN, DIST_MAX);
  }, { passive: false });

  let pinchPrev = null;
  const pinchDist = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
  dom.addEventListener('touchstart', (e) => {
    if (e.touches.length === 2) pinchPrev = pinchDist(e.touches);
  }, { passive: true });
  dom.addEventListener('touchmove', (e) => {
    if (e.touches.length === 2 && pinchPrev) {
      const d = pinchDist(e.touches);
      if (d > 0) targetDist = clamp(targetDist * (pinchPrev / d), DIST_MIN, DIST_MAX);
      pinchPrev = d;
    }
  }, { passive: true });
  dom.addEventListener('touchend', () => { pinchPrev = null; }, { passive: true });

  dom.addEventListener('pointermove', onPointerMove);
  dom.addEventListener('pointerleave', () => { tooltip.style.display = 'none'; });
  dom.addEventListener('pointerdown', (e) => {
    down = { x: e.clientX, y: e.clientY, t: Date.now() };
    if (e.button === 2) focus = null;
  });
  dom.addEventListener('pointerup', onPointerUp);
  dom.addEventListener('contextmenu', (e) => e.preventDefault());

  $('btn-zoom-in').addEventListener('click', () => {
    targetDist = clamp(targetDist / 1.28, DIST_MIN, DIST_MAX);
  });
  $('btn-zoom-out').addEventListener('click', () => {
    targetDist = clamp(targetDist * 1.28, DIST_MIN, DIST_MAX);
  });
  $('btn-reset').addEventListener('click', () => {
    focus = null;
    holdTarget = null;
    desiredTarget.set(0, 0, 0);
    camera.position.copy(HOME_POS);
    controls.target.set(0, 0, 0);
    targetDist = HOME_POS.length();
  });
  $('btn-spin').addEventListener('click', (e) => {
    autoSpin = !autoSpin;
    e.currentTarget.classList.toggle('on', autoSpin);
  });
  $('btn-regions').addEventListener('click', (e) => {
    showRegions = !showRegions;
    e.currentTarget.classList.toggle('on', showRegions);
    try { localStorage.setItem(LS + 'regions', showRegions ? '1' : '0'); } catch (_) { /* egal */ }
    legendEl.style.display = (showRegions && clusters.length) ? 'flex' : 'none';
  });
  $('btn-labels').addEventListener('click', (e) => {
    showLabels = !showLabels;
    e.currentTarget.classList.toggle('on', showLabels);
    labelsEl.style.display = showLabels ? '' : 'none';
  });
  const ncClose = $('nc-close');
  if (ncClose) ncClose.addEventListener('click', closeNcSetup);
  const ncScrim = $('nc-scrim');
  if (ncScrim) ncScrim.addEventListener('click', closeNcSetup);
  const ncSave = $('nc-save');
  if (ncSave) ncSave.addEventListener('click', saveNcSetup);
  const btnMusic = $('btn-music');
  if (btnMusic) btnMusic.addEventListener('click', openNcSetup);
  $('btn-scan').addEventListener('click', async (e) => {
    e.currentTarget.disabled = true;
    toast('Scan gestartet …');
    await fetch(api('/api/scan'), { method: 'POST' });
    loadStatusAndPoll();
  });
  $('btn-landscape').addEventListener('click', async (e) => {
    e.currentTarget.disabled = true;
    toast('Berechne Landschaft …');
    await fetch(api('/api/landscape'), { method: 'POST' });
    pollWhile(1600, async (s) => {
      if (!s.landscape_running) {
        await loadMap();
        toast('Landschaft aktualisiert');
        $('btn-landscape').disabled = false;
        return true;
      }
      return false;
    });
  });

  $('btn-paths').addEventListener('click', openPaths);
  $('btn-root').addEventListener('click', applyRoot);
  $('drawer-close').addEventListener('click', closePaths);
  scrim.addEventListener('click', closePaths);
  $('btn-all').addEventListener('click', () => setAllPaths(true));
  $('btn-none').addEventListener('click', () => setAllPaths(false));
  $('btn-apply').addEventListener('click', applyPaths);
  $('detail-toggle').addEventListener('click', () => detail.classList.toggle('detail-collapsed'));
  $('d-fav').addEventListener('click', (e) => {
    e.stopPropagation();
    const id = e.currentTarget.dataset.id;
    if (id) toggleFav(id);
  });
  $('st-fav').addEventListener('click', (e) => {
    e.stopPropagation();
    const id = e.currentTarget.dataset.id;
    if (id) toggleFav(id);
  });
  $('d-artist').addEventListener('click', () => {
    const a = $('d-artist').dataset.value;
    if (a && !isSampler(a)) loadCollection('artist', a, a);
  });
  $('d-album').addEventListener('click', () => {
    const al = $('d-album').dataset.value;
    if (!al || !selectedItem) return;
    const dir = folderOf(selectedItem.id);
    if (dir) loadCollection('album', dir, al);
  });

  stLyrics.addEventListener('wheel', (e) => {
    e.preventDefault();
    e.stopPropagation();
    stLyrics.scrollTop += e.deltaY;
    lyricManualUntil = performance.now() + 5000;
  }, { passive: false });

  audioEl.addEventListener('play', () => { ensureAudio(); playing = true; });
  audioEl.addEventListener('pause', () => { playing = false; });
  audioEl.addEventListener('timeupdate', updateLyricSync);
  audioEl.addEventListener('ended', () => {
    playing = false;
    playNext();
  });
}

function setPointer(ev) {
  pointer.x = (ev.clientX / window.innerWidth) * 2 - 1;
  pointer.y = -(ev.clientY / window.innerHeight) * 2 + 1;
}

function onResize() {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}

function onPointerMove(ev) {
  if (!pointsObj) return;
  setPointer(ev);
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObject(pointsObj, false);
  if (hits.length) {
    const it = pointsObj.userData.list[hits[0].index];
    if (it) {
      tooltip.style.display = 'block';
      tooltip.style.left = (ev.clientX + 16) + 'px';
      tooltip.style.top = (ev.clientY + 14) + 'px';
      const bits = [];
      if (it.artist && !/various|verschiedene/i.test(it.artist)) bits.push(it.artist);
      if (it.genre) bits.push(it.genre);
      if (it.tempo) bits.push(Math.round(it.tempo) + ' BPM');
      tooltip.innerHTML =
        `<div class="tt-name">${escapeHtml(it.title || it.name)}</div>` +
        (bits.length ? `<div class="tt-meta">${escapeHtml(bits.join(' · '))}</div>` : '');
      renderer.domElement.style.cursor = 'pointer';
      return;
    }
  }
  tooltip.style.display = 'none';
  renderer.domElement.style.cursor = '';
}

function onPointerUp(ev) {
  if (!down) return;
  const moved = Math.hypot(ev.clientX - down.x, ev.clientY - down.y);
  const dt = Date.now() - down.t;
  down = null;
  if (moved > 7 || dt > 600 || !pointsObj) return;
  setPointer(ev);
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObject(pointsObj, false);
  if (hits.length) {
    const it = pointsObj.userData.list[hits[0].index];
    if (it) selectItem(it, true);
  }
}

/* ================= Audio ================= */
function ensureAudio() {
  if (audioCtx) {
    if (audioCtx.state === 'suspended') audioCtx.resume();
    return;
  }
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    audioCtx = new Ctx();
    mediaSrc = audioCtx.createMediaElementSource(audioEl);
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.82;
    freqData = new Uint8Array(analyser.frequencyBinCount);
    const gain = audioCtx.createGain();
    gain.gain.value = 0.95;
    mediaSrc.connect(analyser);
    analyser.connect(gain);
    gain.connect(audioCtx.destination);
  } catch (e) {
    console.warn('Audio-Analyse nicht verfügbar', e);
  }
}

/* ================= Auswahl ================= */
function selectItem(it, autoplay, auto = false) {
  selectedId = it.id;
  selectedItem = it;
  if (auto) {
    playedThisCycle.add(it.id);
  } else {
    playlistMode = 'similar';
    collection = null;
    holdTarget = null;
    refreshCollectionStars();
    playedThisCycle = new Set([it.id]);
    playlist = [it.id];
    playlistIdx = 0;
    similarResults = [];
  }
  focus = { id: it.id, t0: Date.now() };
  selectGlow.position.set(it.x * SCALE, it.y * SCALE, it.z * SCALE);
  selectGlow.visible = true;

  stTitle.textContent = it.title || it.name || '';
  const subBits = [it.artist, it.album].filter(Boolean);
  stSub.textContent = subBits.length ? subBits.join(' · ') : (it.genre || '');
  stLyrics.textContent = '';
  stLyrics.scrollTop = 0;
  lyricEls = [];
  lyricTimes = [];
  lyricActive = -1;
  lyricManualUntil = 0;
  startag.classList.add('show');

  fetch(api('/api/lyrics/' + encodeURI(it.id)))
    .then((r) => r.json())
    .then((d) => renderLyrics(it.id, d.lyrics || '', d.synced || []))
    .catch(() => {});

  detail.classList.remove('detail-collapsed');
  $('d-name').textContent = it.title || it.name;
  setDetailLinks(it.artist, it.album);
  setCover(it.id);
  setTagCover(it.id);
  const dfav = $('d-fav');
  const sfav = $('st-fav');
  if (dfav) {
    dfav.dataset.id = it.id;
    setFavBtn(dfav, favorites.has(it.id), 'Fixstern');
  }
  if (sfav) {
    sfav.dataset.id = it.id;
    setFavBtn(sfav, favorites.has(it.id));
  }
  $('d-tempo').textContent = it.tempo ? Math.round(it.tempo) + ' BPM' : '– BPM';
  const g = $('d-genre');
  if (it.genre) { g.style.display = ''; g.textContent = it.genre; } else { g.style.display = 'none'; }

  audioEl.src = api('/api/audio/' + encodeURI(it.id));
  if (autoplay) {
    ensureAudio();
    const p = audioEl.play();
    if (p && p.catch) p.catch(() => {});
  }

  fetch(api('/api/track/' + encodeURI(it.id)))
    .then((r) => r.json())
    .then((tr) => {
      if (tr.path !== it.id) return;
      if (selectedId === it.id) {
        const bits = [tr.artist, tr.album].filter(Boolean);
        if (bits.length) stSub.textContent = bits.join(' · ');
        setDetailLinks(tr.artist || it.artist, tr.album || it.album);
      }
      const f = tr.features || {};
      $('d-key').textContent = 'Key ' + (f.key != null ? f.key : '–');
      $('d-dur').textContent = f.duration ? fmtDur(f.duration) : '–';
      if (!it.genre && tr.genre) { g.style.display = ''; g.textContent = tr.genre; }
    })
    .catch(() => {});

  if (auto && playlistMode === 'collection') {
    renderCollection();
  } else {
    loadSimilar(it.id, auto);
  }
  drawPaths();
}

function setDetailLinks(artist, album) {
  artist = artist || '';
  album = album || '';
  const da = $('d-artist');
  const dal = $('d-album');
  da.textContent = artist;
  da.dataset.value = artist;
  da.classList.toggle('linkable', !!artist && !isSampler(artist));
  da.title = artist && !isSampler(artist) ? 'Alle Titel von ' + artist : '';
  dal.textContent = album;
  dal.dataset.value = album;
  dal.classList.toggle('linkable', !!album);
  dal.title = album ? 'Alle Titel des Albums ' + album : '';
}

function similarToItem(r) {
  return {
    id: r.id, name: r.name, x: r.x, y: r.y, z: r.z,
    cluster: r.cluster, color: r.color, genre: r.genre,
    tempo: (r.features || {}).tempo,
  };
}

function playNext() {
  if (!playlist.length) return;
  let start = playlistIdx >= 0 ? playlistIdx : playlist.indexOf(selectedId);
  if (start < 0) start = 0;

  let nextId = null, nextIdx = -1;
  for (let step = 1; step <= playlist.length; step++) {
    const idx = (start + step) % playlist.length;
    const cand = playlist[idx];
    if (!cand || cand === selectedId || playedThisCycle.has(cand)) continue;
    nextId = cand; nextIdx = idx; break;
  }

  // Runde durchgehört: mit den Nachbarn des aktuellen Titels weiterziehen
  if (!nextId) {
    const neighbors = similarResults.map((r) => r.id).filter((id) => id !== selectedId);
    if (neighbors.length) {
      playlist = [selectedId, ...neighbors];
      playlistIdx = 0;
      playedThisCycle = new Set([selectedId]);
      return playNext();
    }
    return;
  }

  playlistIdx = nextIdx;
  playedThisCycle.add(nextId);
  const sim = similarResults.find((r) => r.id === nextId);
  const it = byId.get(nextId) || (sim ? similarToItem(sim) : null);
  if (!it) return;
  selectItem(it, true, true);
  toast('▶ ' + it.name);
}

function fmtDur(s) {
  s = Math.round(s);
  return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}

async function loadSimilar(id, keepPlaylist = false) {
  const box = $('similar');
  const titleEl = $('sim-title');
  if (titleEl) titleEl.textContent = 'Ähnlich';
  box.innerHTML = '<div class="empty">Suche …</div>';
  try {
    const data = await fetch(api('/api/similar/' + encodeURI(id) + '?limit=8')).then((r) => r.json());
    const list = data.similar || [];
    similarResults = list;
    if (!keepPlaylist) {
      playlist = [id, ...list.map((r) => r.id)];
      playlistIdx = 0;
    }
    drawPaths();
    renderPlaylistPanel(list, 'Ähnlich', 'similar');
  } catch (e) {
    box.innerHTML = '<div class="empty">Fehler</div>';
  }
}

function renderPlaylistPanel(entries, heading, mode) {
  const box = $('similar');
  const titleEl = $('sim-title');
  if (titleEl) titleEl.textContent = heading;
  box.innerHTML = '';
  if (!entries.length) { box.innerHTML = '<div class="empty">Keine Treffer</div>'; return; }
  for (const s of entries) {
    const el = document.createElement('div');
    el.className = 'sim-item' + (s.id === selectedId ? ' current' : '');
    const color = s.color && s.color.startsWith('#') ? s.color : '#38e8ff';

    const dot = document.createElement('span');
    dot.className = 'sim-dot';
    dot.style.background = color;
    dot.style.color = color;

    const img = document.createElement('img');
    img.className = 'sim-cover';
    img.alt = '';
    img.onload = () => { img.classList.add('has'); el.classList.add('hascov'); };
    img.onerror = () => img.remove();
    img.src = api('/api/cover/' + encodeURI(s.id));

    const isColl = mode === 'collection';
    const main = isColl ? (s.title || s.name) : s.name;
    const sub = isColl
      ? [s.artist && !isSampler(s.artist) ? s.artist : '', s.album, s.genre]
        .filter(Boolean).join(' · ')
      : `${((s.similarity || 0) * 100).toFixed(0)} % · ${s.genre || ''}`;

    const info = document.createElement('div');
    info.className = 'sim-info';
    info.innerHTML =
      `<div class="t">${escapeHtml(main)}</div>` +
      `<div class="s">${escapeHtml(sub)}</div>`;

    const fav = document.createElement('button');
    fav.className = 'sim-fav';
    fav.dataset.id = s.id;
    setFavBtn(fav, favorites.has(s.id));
    fav.addEventListener('click', (ev) => { ev.stopPropagation(); toggleFav(s.id); });

    el.appendChild(dot);
    el.appendChild(img);
    el.appendChild(info);
    el.appendChild(fav);
    el.addEventListener('click', () => {
      const idx = playlist.indexOf(s.id);
      if (idx >= 0) playlistIdx = idx;
      if (isColl) {
        selectItem(byId.get(s.id) || s, true, true);
      } else {
        selectItem(byId.get(s.id) || similarToItem(s), true);
      }
    });
    box.appendChild(el);
  }
}

/* ================= Legende ================= */
function renderLegend() {
  legendEl.innerHTML = '';
  if (!clusters.length || !showRegions) { legendEl.style.display = 'none'; return; }
  legendEl.style.display = 'flex';
  const head = document.createElement('div');
  head.className = 'lg-title';
  head.textContent = `${clusters.length} Regionen · ${items.length} Stücke`;
  legendEl.appendChild(head);
  for (const c of clusters) {
    const row = document.createElement('div');
    row.className = 'lg-item';
    row.innerHTML =
      `<span class="sw" style="background:${c.color};color:${c.color}"></span>` +
      `<div style="min-width:0"><div class="lg-name">${escapeHtml(c.name)}</div>` +
      `<div class="lg-meta">${c.size} St.${c.subtitle ? ' · ' + escapeHtml(c.subtitle) : ''}</div></div>`;
    row.addEventListener('click', (e) => {
      e.stopPropagation();
      const it = pickRegionTrack(c);
      if (it) selectItem(it, true);
    });
    legendEl.appendChild(row);
  }
}

/* ================= Daten ================= */
async function loadMap() {
  try {
    const data = await fetch(api('/api/map')).then((r) => r.json());
    items = data.items || [];
    clusters = data.clusters || [];
    byId = new Map(items.map((i) => [i.id, i]));
    buildPoints(items);
    buildLabels();
    renderLegend();
    refreshFavStars();
    if (selectedId) {
      const it = byId.get(selectedId);
      if (it) {
        selectGlow.position.set(it.x * SCALE, it.y * SCALE, it.z * SCALE);
        selectGlow.visible = true;
      }
    }
    drawPaths();
    if (collection) loadCollection(collection.kind, collection.value, collection.label, true);
    refreshStatus();
  } catch (e) {
    toast('Karte konnte nicht geladen werden');
  }
}

async function refreshStatus() {
  try {
    const s = await fetch(api('/api/status')).then((r) => r.json());
    if (s.landscape_running) {
      statusEl.textContent = 'Berechne Landschaft …';
      statusEl.style.color = '#c9a4ff';
    } else if (s.running) {
      statusEl.textContent = `Analysiere ${s.progress ?? 0}/${s.total ?? '?'}`;
      statusEl.style.color = '#ffd36b';
    } else {
      statusEl.textContent = `${items.length} Sterne · ${clusters.length} Regionen`;
      statusEl.style.color = '';
    }
    return s;
  } catch (e) {
    return { running: false, landscape_running: false };
  }
}

function pollWhile(everyMs, done) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    const s = await refreshStatus();
    if (await done(s)) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }, everyMs);
}

function loadStatusAndPoll() {
  refreshStatus().then((s) => {
    if (s.running || s.landscape_running) {
      pollWhile(2500, async () => {
        const st = await refreshStatus();
        if (!st.running && !st.landscape_running) {
          await loadMap();
          $('btn-scan').disabled = false;
          $('btn-landscape').disabled = false;
          toast('Galaxie aktualisiert');
          return true;
        }
        return false;
      });
    } else {
      $('btn-scan').disabled = false;
    }
  });
}

/* ================= Pfade ================= */
async function openPaths() {
  drawer.classList.add('open');
  scrim.classList.add('show');
  pathList.innerHTML = '<div class="empty">Lade Pfade …</div>';
  try {
    const data = await fetch(api('/api/paths')).then((r) => r.json());
    $('root-input').value = data.root || '';
    const selected = new Set(data.selected || []);
    const all = selected.size === 0;
    pathList.innerHTML = '';
    for (const p of data.paths) {
      const row = document.createElement('label');
      row.className = 'path-item';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = p.name;
      cb.checked = all || selected.has(p.name);
      row.appendChild(cb);
      const n = document.createElement('span');
      n.className = 'pn';
      n.textContent = p.name;
      row.appendChild(n);
      const c = document.createElement('span');
      c.className = 'pc';
      c.textContent = p.count.toLocaleString('de-DE');
      row.appendChild(c);
      pathList.appendChild(row);
    }
    if (!data.paths.length) pathList.innerHTML = '<div class="empty">Keine Unterordner gefunden</div>';
  } catch (e) {
    pathList.innerHTML = '<div class="empty">Fehler beim Laden</div>';
  }
}

function closePaths() {
  drawer.classList.remove('open');
  scrim.classList.remove('show');
}

function setAllPaths(checked) {
  pathList.querySelectorAll('input[type=checkbox]').forEach((cb) => { cb.checked = checked; });
}

async function applyRoot(e) {
  const root = $('root-input').value.trim();
  if (!root) { toast('Bitte einen Pfad angeben'); return; }
  const btn = e.currentTarget;
  btn.disabled = true;
  try {
    const res = await fetch(api('/api/root'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ root })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      toast(err.detail || 'Pfad ungültig');
      return;
    }
    const data = await res.json();
    toast(data.note || 'Wurzel geändert');
    closePaths();
    setTimeout(loadStatusAndPoll, 1500);
  } catch (err) {
    toast('Fehler beim Ändern der Wurzel');
  } finally {
    btn.disabled = false;
  }
}

async function applyPaths(e) {
  const boxes = Array.from(pathList.querySelectorAll('input[type=checkbox]'));
  const names = boxes.filter((b) => b.checked).map((b) => b.value);
  if (!names.length) { toast('Bitte mindestens einen Pfad wählen'); return; }
  e.currentTarget.disabled = true;
  const all = names.length === boxes.length;
  try {
    const res = await fetch(api('/api/selection'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paths: all ? [] : names })
    }).then((r) => r.json());
    closePaths();
    toast(res.note && res.note.length ? res.note : 'Neue Galaxie wird gebaut …');
    loadStatusAndPoll();
  } catch (err) {
    toast('Fehler beim Speichern');
  }
  e.currentTarget.disabled = false;
}

/* ================= Verschiebbare Fenster ================= */
function makeDraggable(el, handleSel) {
  const key = LS + 'pos.' + el.id;
  try {
    const p = JSON.parse(localStorage.getItem(key) || 'null');
    if (p && Number.isFinite(p.l) && Number.isFinite(p.t)) {
      el.style.left = p.l + 'px';
      el.style.top = p.t + 'px';
      el.style.right = 'auto';
      el.style.bottom = 'auto';
    }
  } catch (_) { /* egal */ }

  let sx = 0, sy = 0, ox = 0, oy = 0, active = false, moved = false;

  el.addEventListener('pointerdown', (e) => {
    if (e.button !== 0 || !e.target.closest(handleSel)) return;
    const r = el.getBoundingClientRect();
    sx = e.clientX; sy = e.clientY; ox = r.left; oy = r.top;
    active = true; moved = false;
    try { el.setPointerCapture(e.pointerId); } catch (_) { /* egal */ }
    el.classList.add('dragging');
  });

  el.addEventListener('pointermove', (e) => {
    if (!active) return;
    const dx = e.clientX - sx, dy = e.clientY - sy;
    if (!moved && Math.abs(dx) + Math.abs(dy) < 4) return;
    moved = true;
    el.style.left = clamp(ox + dx, 4, window.innerWidth - el.offsetWidth - 4) + 'px';
    el.style.top = clamp(oy + dy, 4, window.innerHeight - el.offsetHeight - 4) + 'px';
    el.style.right = 'auto';
    el.style.bottom = 'auto';
  });

  const end = () => {
    if (!active) return;
    active = false;
    el.classList.remove('dragging');
    if (!moved) return;
    const r = el.getBoundingClientRect();
    try {
      localStorage.setItem(key, JSON.stringify({ l: Math.round(r.left), t: Math.round(r.top) }));
    } catch (_) { /* egal */ }
    const stop = (ev) => { ev.stopPropagation(); ev.preventDefault(); };
    el.addEventListener('click', stop, { capture: true, once: true });
  };
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);

  window.addEventListener('resize', () => {
    if (!el.style.left) return;
    const r = el.getBoundingClientRect();
    if (r.left > window.innerWidth - 40 || r.top > window.innerHeight - 40) {
      el.style.left = clamp(r.left, 4, window.innerWidth - el.offsetWidth - 4) + 'px';
      el.style.top = clamp(r.top, 4, window.innerHeight - el.offsetHeight - 4) + 'px';
    }
  });
}

/* ================= Helfer ================= */
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c]));
}

function toast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove('show'), 2800);
}

/* ================= Loop ================= */
let lastT = performance.now();
function animate() {
  requestAnimationFrame(animate);
  try {
    frame();
  } catch (err) {
    console.error('Frame-Fehler', err);
    if (!window.__frameErr) {
      window.__frameErr = true;
      toast('Frame-Fehler: ' + (err && err.message ? err.message : err));
    }
  }
}

function frame() {
  const now = performance.now();
  const dt = Math.min(0.05, (now - lastT) / 1000);
  lastT = now;

  if (autoSpin) galaxyGroup.rotation.x += dt * 0.021;
  groundGroup.rotation.y += dt * 0.006;
  starGroup.rotation.y += dt * 0.0015;

  if (focus) {
    const it = byId.get(focus.id);
    if (!it) {
      focus = null;
    } else {
      tmpV.set(it.x * SCALE, it.y * SCALE, it.z * SCALE);
      galaxyGroup.localToWorld(tmpV);
      desiredTarget.copy(tmpV);
      if (now - focus.t0 > FOCUS_MS) focus = null;
    }
  } else if (holdTarget) {
    desiredTarget.copy(holdTarget);
  } else {
    desiredTarget.multiplyScalar(1 - Math.min(1, dt * 0.35));
  }
  controls.target.lerp(desiredTarget, 0.055);

  const cur = camera.position.distanceTo(controls.target);
  if (Math.abs(cur - targetDist) > 0.05) {
    tmpV.copy(camera.position).sub(controls.target);
    tmpV.setLength(clamp(targetDist, DIST_MIN, DIST_MAX));
    camera.position.copy(controls.target).add(tmpV);
  }

  controls.update();

  // Stern-LOD: scharfer Kern, leichtes Wachsen beim Heranfahren
  if (pointsObj) {
    const d = camera.position.distanceTo(controls.target);
    pointsObj.material.size = clamp(3.0 * Math.pow(clamp(d / 385, 0.03, 3.4), 0.85), 0.06, 7.0);
    raycaster.params.Points.threshold = clamp(d * 0.008, 0.25, 3.2);
  }

  updateSpectrum(now, dt);
  if (selectGlow && selectGlow.visible) {
    const p = 1 + Math.sin(now / 260) * 0.14;
    selectGlow.scale.set(15 * p, 15 * p, 1);
  }
  if (favObj) favObj.material.size = 9.5 + Math.sin(now / 380) * 1.8;
  if (collectionObj) collectionObj.material.size = 10 + Math.sin(now / 460) * 2.2;
  if (pathsGroup) {
    const k = 0.30 + 0.16 * Math.sin(now / 700);
    for (const o of pathsGroup.children) o.material.opacity = k;
  }
  updateStartag();
  updateLyricsScroll();
  updateLyricSync();
  updateStarLabels(now);
  updateLabels();
  renderer.render(scene, camera);
}

/* ================= Start ================= */
init();
initServerFeatures();
loadMap();
refreshStatus().then((s) => {
  if (s && (s.running || s.landscape_running)) {
    pollWhile(3000, async () => {
      const st = await refreshStatus();
      if (!st.running && !st.landscape_running) {
        await loadMap();
        toast('Galaxie aktualisiert');
        return true;
      }
      return false;
    });
  }
});
