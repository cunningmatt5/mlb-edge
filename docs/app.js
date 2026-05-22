'use strict';

const PICKS_URL = './picks.json';

let allGames = [];
let activeFilter = 'ALL';

// ── Service Worker ────────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('./sw.js').catch(console.error);
}

// ── Data fetching ─────────────────────────────────────────────────────────────
async function loadPicks() {
  try {
    const res = await fetch(`${PICKS_URL}?t=${Date.now()}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (networkErr) {
    // Fallback to SW cache for offline support
    try {
      const cached = await caches.match(PICKS_URL);
      if (cached) return await cached.json();
    } catch (_) {}
    throw networkErr;
  }
}

// ── Rendering helpers ─────────────────────────────────────────────────────────
const BET_LABELS = {
  K_PROP:   'Strikeouts',
  HR_PROP:  'Home Run',
  HIT_PROP: 'Hit Prop',
  TOTAL:    'Total',
  ML_F5:    'ML / F5',
};

function signalColor(signal) {
  if (signal >= 9)  return '#22c55e';
  if (signal >= 8)  return '#f59e0b';
  return '#60a5fa';
}

function renderSignalBar(signal) {
  // Map [7, 10] → [0%, 100%]
  const pct = Math.round(((signal - 7) / 3) * 100);
  const color = signalColor(signal);
  return `<div class="signal-row">
    <div class="signal-track">
      <div class="signal-fill" style="width:${pct}%;background:${color}"></div>
    </div>
    <span class="signal-label" style="color:${color}">${signal.toFixed(1)}</span>
  </div>`;
}

function renderBadge(betType) {
  const label = BET_LABELS[betType] || betType;
  return `<span class="badge badge-${betType.toLowerCase().replace('_', '')}">${label}</span>`;
}

function renderDirectionPill(direction) {
  const cls = `dir-${direction.toLowerCase()}`;
  return `<span class="direction-pill ${cls}">${direction}</span>`;
}

function renderPick(pick) {
  const reasons = pick.reasons.map(r => `<li>${r}</li>`).join('');
  return `<div class="pick-card" data-bet-type="${pick.bet_type}">
    <div class="pick-header">
      ${renderBadge(pick.bet_type)}
      ${renderDirectionPill(pick.direction)}
    </div>
    <div class="pick-headline">${pick.headline}</div>
    ${renderSignalBar(pick.signal)}
    <ul class="pick-reasons">${reasons}</ul>
  </div>`;
}

function formatGameTime(isoString) {
  try {
    return new Date(isoString).toLocaleTimeString('en-US', {
      hour: 'numeric',
      minute: '2-digit',
      timeZone: 'America/New_York',
      timeZoneName: 'short',
    });
  } catch (_) {
    return isoString;
  }
}

function renderGame(game, filter) {
  const picks = filter === 'ALL'
    ? game.picks
    : game.picks.filter(p => p.bet_type === filter);

  if (!picks || picks.length === 0) return '';

  const gameTime = formatGameTime(game.game_time);

  return `<section class="game-block">
    <div class="game-header">
      <div class="matchup">
        <span class="team">${game.away_team}</span>
        <span class="at">@</span>
        <span class="team">${game.home_team}</span>
      </div>
      <div class="game-meta">
        <span class="game-time">${gameTime}</span>
        <span class="venue">${game.venue}</span>
      </div>
      <div class="starters">
        <span>${game.away_sp}</span>
        <span class="vs-sep">vs</span>
        <span>${game.home_sp}</span>
      </div>
    </div>
    <div class="picks-list">
      ${picks.map(renderPick).join('')}
    </div>
  </section>`;
}

function renderAll() {
  const container = document.getElementById('games-container');
  const html = allGames.map(g => renderGame(g, activeFilter)).join('');
  container.innerHTML = html;

  const hasContent = html.trim().length > 0;
  document.getElementById('no-picks-state').classList.toggle('hidden', hasContent);
}

// ── Filter bar ────────────────────────────────────────────────────────────────
document.getElementById('filter-bar').addEventListener('click', e => {
  const btn = e.target.closest('.filter-btn');
  if (!btn) return;
  document.querySelectorAll('.filter-btn').forEach(b => {
    b.classList.remove('active');
    b.setAttribute('aria-selected', 'false');
  });
  btn.classList.add('active');
  btn.setAttribute('aria-selected', 'true');
  activeFilter = btn.dataset.filter;
  renderAll();
});

// ── Init ──────────────────────────────────────────────────────────────────────
async function init() {
  const loading = document.getElementById('loading-state');
  const errorEl = document.getElementById('error-state');

  try {
    const data = await loadPicks();
    allGames = data.games || [];

    const genAt = new Date(data.generated_at);
    const timeStr = genAt.toLocaleTimeString('en-US', {
      hour: 'numeric',
      minute: '2-digit',
      timeZone: 'America/New_York',
    });
    document.getElementById('last-updated').textContent = `Updated ${timeStr} ET`;

    loading.classList.add('hidden');

    if (allGames.length === 0) {
      document.getElementById('no-picks-state').classList.remove('hidden');
    } else {
      renderAll();
    }
  } catch (err) {
    console.error('Failed to load picks:', err);
    loading.classList.add('hidden');
    errorEl.classList.remove('hidden');
  }
}

document.getElementById('retry-btn')?.addEventListener('click', () => {
  document.getElementById('error-state').classList.add('hidden');
  document.getElementById('loading-state').classList.remove('hidden');
  init();
});

init();
