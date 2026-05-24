'use strict';

const PICKS_URL   = './picks.json';
const HISTORY_URL = './picks_history.json';
const TRENDS_URL  = './trends.json';

let allGames     = [];
let historyData  = null;
let trendsData   = null;
let activeFilter = 'ALL';

// ── Service Worker ─────────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('./sw.js').catch(console.error);
}

// ── Data fetching ──────────────────────────────────────────────────────────────
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

async function loadTrends() {
  try {
    const res = await fetch(`${TRENDS_URL}?t=${Date.now()}`);
    if (!res.ok) return null;
    return await res.json();
  } catch (_) {
    return null;
  }
}

// ── Label maps ─────────────────────────────────────────────────────────────────
const BET_LABELS = {
  K_PROP:     'Strikeouts',
  HR_PROP:    'Home Run',
  HIT_PROP:   'Hit Prop',
  TB_PROP:    'Total Bases',
  TOTAL:      'Game Total',
  TEAM_TOTAL: 'Team Total',
  ML_F5:      'ML / F5',
};

const BET_COLORS = {
  K_PROP:     '#7c3aed',
  HR_PROP:    '#e11d48',
  HIT_PROP:   '#0284c7',
  TB_PROP:    '#0891b2',
  TOTAL:      '#059669',
  TEAM_TOTAL: '#047857',
  ML_F5:      '#d97706',
};

// ── Helpers ────────────────────────────────────────────────────────────────────
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

function fmtPct(val) {
  if (val === null || val === undefined) return '—';
  return (val * 100).toFixed(1) + '%';
}

function teamAbbr(fullName) {
  if (!fullName) return '';
  const parts = fullName.trim().split(' ');
  return parts[parts.length - 1];
}

// ── Edge badge for insights panel ──────────────────────────────────────────────
function edgeBadge(edge) {
  if (edge === null || edge === undefined) return '';
  const pct  = (edge * 100).toFixed(1);
  const sign = edge >= 0 ? '+' : '';
  if (edge >= 0.05) return `<span class="insight-edge edge-green">${sign}${pct}% ✓</span>`;
  if (edge >= 0.02) return `<span class="insight-edge edge-amber">${sign}${pct}%</span>`;
  return `<span class="insight-edge edge-plain">${sign}${pct}%</span>`;
}

// ── SP + lineup stat line ──────────────────────────────────────────────────────
function renderSpLine(game) {
  const hs = game.home_sp_stats || {};
  const as = game.away_sp_stats || {};

  function spBit(name, stats) {
    const xfip  = stats.xfip  != null ? `xFIP ${stats.xfip.toFixed(2)}`               : null;
    const kpct  = stats.k_pct != null ? `${(stats.k_pct * 100).toFixed(0)}%K`           : null;
    const stuff = stats.stuff_plus != null ? `Stuff+ ${Math.round(stats.stuff_plus)}`  : null;
    const info  = [xfip, kpct, stuff].filter(Boolean).join(' · ');
    return `<span class="sp-name">${name || 'TBD'}</span>${info ? `<span class="sp-stats">${info}</span>` : ''}`;
  }

  const homeAbbr = teamAbbr(game.home_team);
  const awayAbbr = teamAbbr(game.away_team);
  const homeXw   = game.home_lineup_xwoba != null ? `.${Math.round(game.home_lineup_xwoba * 1000)} xwOBA` : null;
  const awayXw   = game.away_lineup_xwoba != null ? `.${Math.round(game.away_lineup_xwoba * 1000)} xwOBA` : null;
  const park     = game.park_run_factor   != null ? `Park ${Math.round(game.park_run_factor)}`            : null;

  const lineupParts = [
    homeXw ? `${homeAbbr} ${homeXw}` : null,
    awayXw ? `${awayAbbr} ${awayXw}` : null,
    park,
  ].filter(Boolean);

  return `<div class="game-sp-line">
    <div class="sp-matchup">
      <div class="sp-entry">${spBit(game.home_sp, hs)}</div>
      <span class="vs-sep">vs</span>
      <div class="sp-entry">${spBit(game.away_sp, as)}</div>
    </div>
    ${lineupParts.length ? `<div class="lineup-line">${lineupParts.join(' · ')}</div>` : ''}
  </div>`;
}

// ── Insights panel ─────────────────────────────────────────────────────────────
function insightRow(dirCls, label, hist, impl, edge) {
  return `<div class="insight-row">
    <span class="insight-dir ${dirCls}">${label}</span>
    <div class="insight-data">
      <div class="insight-data-top">
        <span class="insight-hist">${fmtPct(hist)}</span>
        ${edgeBadge(edge)}
      </div>
      <span class="insight-impl">vs ${fmtPct(impl)}</span>
    </div>
  </div>`;
}

function renderInsightsPanel(game) {
  const ins = game.insights;

  if (!ins || (!ins.total && !ins.moneyline)) {
    const msg = !ins
      ? 'Comps unavailable — trigger backfill to build game_comps.json'
      : 'No Pinnacle line';
    return `<div class="insights-panel insights-na">
      <span class="insights-na-msg">${msg}</span>
    </div>`;
  }

  // Total column
  let totalCol = '';
  if (ins.total) {
    const t = ins.total;
    const lineStr = t.line != null ? `O/U ${t.line}` : '';
    totalCol = `<div class="insight-col">
      <div class="insight-mkt-label">TOTAL <span class="insight-line-val">${lineStr}</span></div>
      ${insightRow('ins-over',  'OVER',  t.historical_over_rate,  t.pinnacle_over_prob,  t.over_edge)}
      ${insightRow('ins-under', 'UNDER', t.historical_under_rate, t.pinnacle_under_prob, t.under_edge)}
    </div>`;
  } else {
    totalCol = `<div class="insight-col insight-col-empty">
      <div class="insight-mkt-label">TOTAL</div>
      <span class="insight-na-inline">No line</span>
    </div>`;
  }

  // Moneyline column
  let mlCol = '';
  if (ins.moneyline) {
    const m = ins.moneyline;
    mlCol = `<div class="insight-col">
      <div class="insight-mkt-label">MONEYLINE</div>
      ${insightRow('ins-home', teamAbbr(game.home_team), m.historical_home_rate, m.pinnacle_home_prob, m.home_edge)}
      ${insightRow('ins-away', teamAbbr(game.away_team), m.historical_away_rate, m.pinnacle_away_prob, m.away_edge)}
    </div>`;
  } else {
    mlCol = `<div class="insight-col insight-col-empty">
      <div class="insight-mkt-label">MONEYLINE</div>
      <span class="insight-na-inline">No line</span>
    </div>`;
  }

  const footer = game.comps_count > 0
    ? `<div class="comps-footer">Based on ${game.comps_count} similar games (2023–2025)</div>`
    : '';

  // Collect reasons from whichever side has the actionable edge
  const allReasons = [
    ...((ins.total    && ins.total.reasons)    || []),
    ...((ins.moneyline && ins.moneyline.reasons) || []),
  ];
  const uniqueReasons = [...new Set(allReasons)];
  const reasonsHtml = uniqueReasons.length
    ? `<ul class="insight-reasons">${uniqueReasons.map(r => `<li>${r}</li>`).join('')}</ul>`
    : '';

  return `<div class="insights-panel">
    <div class="insights-grid">
      ${totalCol}
      <div class="insights-divider"></div>
      ${mlCol}
    </div>
    ${footer}
    ${reasonsHtml}
  </div>`;
}

// ── Prop pick cards ────────────────────────────────────────────────────────────
function signalColor(signal) {
  if (signal >= 9.0) return '#00e676';
  if (signal >= 8.0) return '#ffab00';
  if (signal >= 6.5) return '#00d4ff';
  return '#7a88a0';
}

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

function renderPickOdds(pick) {
  if (!pick.odds || !pick.has_line) {
    return '<div class="pick-odds-none">No line available</div>';
  }
  const { line, over_price, under_price, edge_pct } = pick.odds;
  const edgePct = (edge_pct * 100).toFixed(1);
  const edgeCls = edge_pct >= 0.08 ? 'edge-high' : edge_pct >= 0.03 ? 'edge-mid' : 'edge-low';
  const price = (pick.direction === 'OVER' || pick.direction === 'HOME') ? over_price : under_price;
  const priceStr = price > 0 ? `+${price}` : `${price}`;
  const lineStr  = (line !== null && line !== undefined) ? `Line ${line} · ` : '';
  return `<div class="pick-odds">
    <span class="odds-line">${lineStr}<span class="odds-price">${priceStr}</span></span>
    <span class="edge-badge ${edgeCls}">+${edgePct}% edge</span>
  </div>`;
}

function renderPick(pick) {
  const color   = BET_COLORS[pick.bet_type] || '#00d4ff';
  const reasons = pick.reasons.map(r => `<li>${r}</li>`).join('');
  return `<div class="pick-card" data-bet-type="${pick.bet_type}" style="--bet-color:${color}">
    <div class="pick-header">
      ${renderBadge(pick.bet_type)}
      <span class="direction-pill dir-${pick.direction.toLowerCase()}">${pick.direction}</span>
    </div>
    <div class="pick-headline">${pick.headline}</div>
    ${renderSignalBar(pick.signal)}
    ${renderPickOdds(pick)}
    <ul class="pick-reasons">${reasons}</ul>
  </div>`;
}

// ── Game card ──────────────────────────────────────────────────────────────────
function renderGame(game, idx) {
  const gameTime = formatGameTime(game.game_time);
  const picks    = game.picks || [];
  const isPropsFilter = activeFilter === 'PROPS';

  const propsSection = picks.length > 0
    ? `<details class="props-section" ${isPropsFilter ? 'open' : ''}>
        <summary class="props-toggle">
          Player Props <span class="props-count">${picks.length}</span>
        </summary>
        <div class="picks-list">${picks.map(renderPick).join('')}</div>
      </details>`
    : '';

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
      ${renderSpLine(game)}
    </div>
    ${renderInsightsPanel(game)}
    ${propsSection}
  </section>`;
}

// ── Trends view ────────────────────────────────────────────────────────────────
function trendBadge(signal) {
  const MAP = {
    ERA_LUCK:    { cls: 'over',  label: 'ERA Lucky'   },
    ERA_STRUGGLE:{ cls: 'under', label: 'ERA Unlucky' },
    HOT_K:       { cls: 'hot',   label: 'K Surge'     },
    COLD_K:      { cls: 'cold',  label: 'K Fade'      },
    COLD_BAT:    { cls: 'under', label: 'Cold Bat'    },
    HOT_BAT:     { cls: 'over',  label: 'Hot Bat'     },
  };
  const m = MAP[signal] || { cls: '', label: signal };
  return `<span class="trend-badge trend-badge-${m.cls}">${m.label}</span>`;
}

function renderTrendCard(entry) {
  const isKTrend = entry.signal === 'HOT_K' || entry.signal === 'COLD_K';
  const fmtA = isKTrend
    ? `${(entry.stat_a * 100).toFixed(1)}%`
    : entry.stat_a != null ? entry.stat_a.toFixed(2) : '—';
  const fmtB = isKTrend
    ? `${(entry.stat_b * 100).toFixed(1)}%`
    : entry.stat_b != null ? entry.stat_b.toFixed(2) : '—';
  const isBatter = entry.signal === 'COLD_BAT' || entry.signal === 'HOT_BAT';
  const fmtDelta = isBatter
    ? `+.${Math.round(entry.delta * 1000)}`
    : isKTrend
      ? `+${(entry.delta * 100).toFixed(1)}pp`
      : `+${entry.delta.toFixed(2)}`;

  return `<div class="trend-card">
    <div class="trend-header">
      <div class="trend-identity">
        <span class="trend-name">${entry.name}</span>
        <span class="trend-meta">${entry.team} · ${entry.game}</span>
      </div>
      ${trendBadge(entry.signal)}
    </div>
    <div class="trend-stats-row">
      <div class="trend-stat"><label>${entry.stat_a_label}</label><span>${fmtA}</span></div>
      <div class="trend-stat"><label>${entry.stat_b_label}</label><span>${fmtB}</span></div>
      <div class="trend-stat"><label>Gap</label><span class="delta-pos">${fmtDelta}</span></div>
    </div>
    <div class="trend-implication">${entry.implication}</div>
  </div>`;
}

const TREND_SECTIONS = [
  {
    key:         'pitcher_lucky',
    title:       'Lucky Pitchers — Due for Regression',
    subtext:     'ERA significantly below xFIP. Results are outpacing process — lean OVER against these starters.',
  },
  {
    key:         'pitcher_unlucky',
    title:       'Unlucky Pitchers — Rebound Candidates',
    subtext:     'ERA significantly above xFIP. Pitching better than results — back these starters or lean UNDER.',
  },
  {
    key:         'pitcher_hot_k',
    title:       'K Rate Surging',
    subtext:     'Strikeout rate up 3+ pp vs. season average in recent starts. K props have value.',
  },
  {
    key:         'pitcher_cold_k',
    title:       'K Rate Fading',
    subtext:     'Strikeout rate down 3+ pp vs. season average in recent starts. Fade K overs.',
  },
  {
    key:         'batter_cold',
    title:       'Cold Batters — Rebound Candidates',
    subtext:     'xwOBA 25+ points above wOBA. Hitting well below expected quality — positive regression likely.',
  },
  {
    key:         'batter_hot',
    title:       'Hot Batters — Fade Candidates',
    subtext:     'wOBA 25+ points above xwOBA. Results outpacing expected quality — negative regression likely.',
  },
];

function renderTrends(trends) {
  if (!trends) {
    return `<div class="state-view">
      <p class="state-icon">📡</p>
      <p>Trends data unavailable.</p>
      <p class="state-sub">Run the pipeline to generate trends.json.</p>
    </div>`;
  }

  let html = '';
  for (const sec of TREND_SECTIONS) {
    const entries = trends[sec.key] || [];
    html += `<div class="trend-section">
      <div class="trend-section-header">${sec.title}</div>
      <div class="trend-section-sub">${sec.subtext}</div>`;
    if (entries.length === 0) {
      html += `<div class="trend-section-empty">None today</div>`;
    } else {
      html += entries.map(renderTrendCard).join('');
    }
    html += `</div>`;
  }

  return html;
}

// ── Record view ────────────────────────────────────────────────────────────────
function renderRecord(history) {
  if (!history || !history.summary) {
    return `<div class="state-view">
      <p class="state-icon">📋</p>
      <p>No record data yet.</p>
      <p class="state-sub">Results will appear after picks are graded.</p>
    </div>`;
  }

  const s      = history.summary;
  const wr     = s.win_rate !== null ? (s.win_rate * 100).toFixed(1) + '%' : '—';
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

  const byTier = s.by_tier || {};
  const TIER_META = {
    ELITE:     { label: 'Elite',     color: '#ffab00' },
    GREAT:     { label: 'Great',     color: '#00d4ff' },
    APPEALING: { label: 'Appealing', color: '#7a88a0' },
  };
  if (Object.values(byTier).some(t => t.total > 0)) {
    html += '<div class="record-section-title">By Tier</div><div class="record-table">';
    for (const [tier, data] of Object.entries(byTier)) {
      if (data.total === 0) continue;
      const meta   = TIER_META[tier] || { label: tier, color: 'var(--text-muted)' };
      const tierWR = data.win_rate !== null ? (data.win_rate * 100).toFixed(0) + '%' : '—';
      const cls    = data.win_rate !== null ? (data.win_rate >= 0.55 ? 'good' : data.win_rate < 0.45 ? 'bad' : '') : '';
      html += `<div class="record-row">
        <span class="record-type" style="color:${meta.color};font-weight:700">${meta.label}</span>
        <span class="record-wl">${data.wins}–${data.losses}</span>
        <span class="record-wr ${cls}">${tierWR}</span>
      </div>`;
    }
    html += '</div>';
  }

  const recent = (history.picks || [])
    .filter(p => p.outcome !== 'PENDING')
    .slice(-20)
    .reverse();

  if (recent.length > 0) {
    html += '<div class="record-section-title">Recent Results</div><div class="record-table">';
    for (const p of recent) {
      const label     = BET_LABELS[p.bet_type] || p.bet_type;
      const outcomeCls = p.outcome === 'WIN' ? 'win' : 'loss';
      const dir       = (p.direction || '').toLowerCase();
      const dirPill   = p.direction
        ? `<span class="direction-pill dir-${dir}">${p.direction}</span>`
        : '';

      // Cushion: how far the actual result cleared (or missed) the line
      let cushionHtml = '';
      const av   = parseFloat(p.actual_value);
      const line = parseFloat(p.line_at_pick);
      if (!isNaN(av) && !isNaN(line)) {
        const raw = (p.direction === 'OVER' || p.direction === 'HOME')
          ? av - line
          : line - av;
        const sign = raw >= 0 ? '+' : '';
        const cls  = raw >= 0 ? 'pos' : 'neg';
        cushionHtml = `<span class="record-cushion ${cls}">${sign}${raw.toFixed(1)}</span>`;
      }

      html += `<div class="record-row">
        <span class="record-type">${p.subject}<span class="record-date"> · ${p.date}</span></span>
        <div class="record-row-right">
          <span class="badge badge-${p.bet_type.toLowerCase().replaceAll('_','')} badge-sm">${label}</span>
          ${dirPill}
          ${cushionHtml}
          <span class="record-outcome ${outcomeCls}">${p.outcome}</span>
        </div>
      </div>`;
    }
    html += '</div>';
  }

  return html;
}

// ── Render all ─────────────────────────────────────────────────────────────────
function renderAll() {
  const container = document.getElementById('games-container');
  const noPicks   = document.getElementById('no-picks-state');

  if (activeFilter === 'RECORD') {
    container.innerHTML = renderRecord(historyData);
    noPicks.classList.add('hidden');
    return;
  }

  if (activeFilter === 'TRENDS') {
    container.innerHTML = renderTrends(trendsData);
    noPicks.classList.add('hidden');
    return;
  }

  const filtered = allGames.filter(game => {
    if (activeFilter === 'ALL')       return true;
    if (activeFilter === 'TOTAL')     return game.insights && game.insights.total;
    if (activeFilter === 'MONEYLINE') return game.insights && game.insights.moneyline;
    if (activeFilter === 'PROPS')     return (game.picks || []).length > 0;
    return true;
  });

  container.innerHTML = filtered.map((game, i) => renderGame(game, i)).join('');
  noPicks.classList.toggle('hidden', filtered.length > 0);
}

// ── Filter bar ─────────────────────────────────────────────────────────────────
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

// ── Init ───────────────────────────────────────────────────────────────────────
async function init() {
  const loading = document.getElementById('loading-state');
  const errorEl = document.getElementById('error-state');

  try {
    const [data, hist, trends] = await Promise.all([loadPicks(), loadHistory(), loadTrends()]);
    historyData = hist;
    trendsData  = trends;
    allGames    = data.games || [];

    const genAt   = new Date(data.generated_at);
    const timeStr = genAt.toLocaleString('en-US', {
      month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit',
      timeZone: 'America/New_York', timeZoneName: 'short',
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
