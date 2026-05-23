'use strict';

const PICKS_URL   = './picks.json';
const HISTORY_URL = './picks_history.json';

let allGames    = [];
let historyData = null;
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
    try {
      const cached = await caches.match(PICKS_URL);
      if (cached) return await cached.json();
    } catch (_) {}
    throw networkErr;
  }
}

async function loadHistory() {
  try {
    const res = await fetch(`${HISTORY_URL}?t=${Date.now()}`);
    if (!res.ok) return null;
    return await res.json();
  } catch (_) {
    return null;
  }
}

// ── Rendering helpers ─────────────────────────────────────────────────────────
const BET_LABELS = {
  K_PROP:     'Strikeouts',
  HR_PROP:    'Home Run',
  HIT_PROP:   'Hit Prop',
  TB_PROP:    'Total Bases',
  WALK_PROP:  'Walks',
  TOTAL:      'Game Total',
  TEAM_TOTAL: 'Team Total',
  ML_F5:      'ML / F5',
};

const BET_COLORS = {
  K_PROP:     '#7c3aed',
  HR_PROP:    '#e11d48',
  HIT_PROP:   '#0284c7',
  TB_PROP:    '#0891b2',
  WALK_PROP:  '#b45309',
  TOTAL:      '#059669',
  TEAM_TOTAL: '#047857',
  ML_F5:      '#d97706',
};

function signalColor(signal) {
  if (signal >= 9.0) return '#00e676';
  if (signal >= 8.0) return '#ffab00';
  if (signal >= 6.5) return '#00d4ff';
  return '#7a88a0';
}

const TIERS = [
  { id: 'ELITE',     label: 'Elite',     color: '#ffab00' },
  { id: 'GREAT',     label: 'Great',     color: '#00d4ff' },
  { id: 'APPEALING', label: 'Appealing', color: '#7a88a0' },
];

function renderSignalBar(signal) {
  const pct   = Math.round(((signal - 5) / 5) * 100);
  const color = signalColor(signal);
  return `<div class="signal-row">
    <div class="signal-track">
      <div class="signal-fill" style="width:${pct}%;background:${color};box-shadow:0 0 8px ${color}80"></div>
    </div>
    <span class="signal-label" style="color:${color}">${signal.toFixed(1)}</span>
  </div>`;
}

function renderBadge(betType) {
  const label = BET_LABELS[betType] || betType;
  return `<span class="badge badge-${betType.toLowerCase().replaceAll('_', '')}">${label}</span>`;
}

function renderDirectionPill(direction) {
  const cls = `dir-${direction.toLowerCase()}`;
  return `<span class="direction-pill ${cls}">${direction}</span>`;
}

function renderPick(pick) {
  const color   = BET_COLORS[pick.bet_type] || '#00d4ff';
  const reasons = pick.reasons.map(r => `<li>${r}</li>`).join('');
  return `<div class="pick-card" data-bet-type="${pick.bet_type}" style="--bet-color:${color}">
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

function renderGame(game, picks, idx = 0) {
  if (!picks || picks.length === 0) return '';

  const gameTime = formatGameTime(game.game_time);

  return `<section class="game-block" style="animation-delay:${idx * 55}ms">
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

function renderTierDivider(tier, count) {
  const label = count === 1 ? '1 pick' : `${count} picks`;
  return `<div class="tier-divider" style="--tier-color:${tier.color}">
    <div class="tier-divider-line"></div>
    <span class="tier-label">
      <span class="tier-name">${tier.label}</span>
      <span class="tier-count">${label}</span>
    </span>
    <div class="tier-divider-line"></div>
  </div>`;
}

// ── Record view ───────────────────────────────────────────────────────────────
function renderRecord(history) {
  if (!history || !history.summary) {
    return '<div class="state-view"><p class="state-icon">📋</p><p>No record data yet.</p><p class="state-sub">Results will appear after picks are graded.</p></div>';
  }

  const s  = history.summary;
  const wr = s.win_rate !== null ? (s.win_rate * 100).toFixed(1) + '%' : '—';
  const graded = s.wins + s.losses;

  let html = `<div class="record-summary">
    <div class="record-stat">
      <span class="record-num">${s.wins}–${s.losses}</span>
      <span class="record-label">W – L</span>
    </div>
    <div class="record-stat">
      <span class="record-num ${s.win_rate !== null ? (s.win_rate >= 0.55 ? 'good' : s.win_rate < 0.45 ? 'bad' : '') : ''}">${wr}</span>
      <span class="record-label">Win Rate</span>
    </div>
    <div class="record-stat">
      <span class="record-num muted">${s.pending}</span>
      <span class="record-label">Pending</span>
    </div>
  </div>`;

  if (graded === 0) {
    html += '<p class="record-empty">No graded picks yet — check back after today\'s games finish.</p>';
    return html;
  }

  // By bet type
  const byType = s.by_type || {};
  if (Object.keys(byType).length > 0) {
    html += '<div class="record-section-title">By Bet Type</div><div class="record-table">';
    for (const [type, data] of Object.entries(byType)) {
      if (data.total === 0) continue;
      const label  = BET_LABELS[type] || type;
      const typeWR = data.win_rate !== null ? (data.win_rate * 100).toFixed(0) + '%' : '—';
      const cls    = data.win_rate !== null ? (data.win_rate >= 0.55 ? 'good' : data.win_rate < 0.45 ? 'bad' : '') : '';
      html += `<div class="record-row">
        <span class="record-type">${label}</span>
        <span class="record-wl">${data.wins}–${data.losses}</span>
        <span class="record-wr ${cls}">${typeWR}</span>
      </div>`;
    }
    html += '</div>';
  }

  // By signal band
  const byBand = s.by_signal_band || {};
  const hasBandData = Object.values(byBand).some(b => b.total > 0);
  if (hasBandData) {
    html += '<div class="record-section-title">By Signal Strength</div><div class="record-table">';
    for (const [band, data] of Object.entries(byBand)) {
      if (data.total === 0) continue;
      const bandWR = data.win_rate !== null ? (data.win_rate * 100).toFixed(0) + '%' : '—';
      const cls    = data.win_rate !== null ? (data.win_rate >= 0.55 ? 'good' : data.win_rate < 0.45 ? 'bad' : '') : '';
      html += `<div class="record-row">
        <span class="record-type">Signal ${band}</span>
        <span class="record-wl">${data.wins}–${data.losses}</span>
        <span class="record-wr ${cls}">${bandWR}</span>
      </div>`;
    }
    html += '</div>';
  }

  // Recent picks list (last 20 graded)
  const recent = (history.picks || [])
    .filter(p => p.outcome !== 'PENDING')
    .slice(-20)
    .reverse();

  if (recent.length > 0) {
    html += '<div class="record-section-title">Recent Results</div><div class="record-table">';
    for (const p of recent) {
      const label    = BET_LABELS[p.bet_type] || p.bet_type;
      const outcomeClass = p.outcome === 'WIN' ? 'win' : 'loss';
      html += `<div class="record-row">
        <span class="record-type">${p.subject}<span class="record-date"> · ${p.date}</span></span>
        <span class="record-type-badge badge badge-${p.bet_type.toLowerCase().replaceAll('_','')} badge-sm">${label}</span>
        <span class="record-outcome ${outcomeClass}">${p.outcome}</span>
      </div>`;
    }
    html += '</div>';
  }

  return html;
}

// ── Render picks or record ────────────────────────────────────────────────────
function renderAll() {
  const container = document.getElementById('games-container');
  const noPicks   = document.getElementById('no-picks-state');

  if (activeFilter === 'RECORD') {
    container.innerHTML = renderRecord(historyData);
    noPicks.classList.add('hidden');
    return;
  }

  let html = '';
  let totalPicks = 0;
  let blockIdx = 0;

  for (const tier of TIERS) {
    // For each game find picks that match this tier + active filter, sorted by signal desc
    const tierGames = allGames.map(game => {
      const picks = game.picks
        .filter(p => p.tier === tier.id && (activeFilter === 'ALL' || p.bet_type === activeFilter))
        .sort((a, b) => b.signal - a.signal);
      return { game, picks };
    }).filter(({ picks }) => picks.length > 0);

    if (tierGames.length === 0) continue;

    const tierCount = tierGames.reduce((n, { picks }) => n + picks.length, 0);
    html += renderTierDivider(tier, tierCount);

    for (const { game, picks } of tierGames) {
      html += renderGame(game, picks, blockIdx++);
    }

    totalPicks += tierCount;
  }

  container.innerHTML = html;
  noPicks.classList.toggle('hidden', totalPicks > 0);
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
    const [data, hist] = await Promise.all([loadPicks(), loadHistory()]);
    historyData = hist;
    allGames = data.games || [];

    const genAt  = new Date(data.generated_at);
    const isToday = new Date().toDateString() === new Date(genAt.toLocaleDateString('en-US', {timeZone: 'America/New_York'})).toDateString();
    const timeStr = genAt.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      timeZone: 'America/New_York',
      timeZoneName: 'short',
    });
    document.getElementById('last-updated').textContent = `Updated ${timeStr}`;

    loading.classList.add('hidden');

    if (allGames.length === 0 && activeFilter !== 'RECORD') {
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
