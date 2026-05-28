'use strict';

// ── Data sources ─────────────────────────────────────────────────────────────
const GAMES_URL    = './games.json';
const HISTORY_URL  = './history.json';
const BACKTEST_URL = './backtest.json';
const PICKS_URL    = './picks.json';

// ── Team logo map (ESPN CDN abbreviations) ────────────────────────────────────
const TEAM_LOGO = {
  'Arizona Diamondbacks':  'ari',  'Atlanta Braves':         'atl',
  'Baltimore Orioles':     'bal',  'Boston Red Sox':          'bos',
  'Chicago Cubs':          'chc',  'Chicago White Sox':       'chw',
  'Cincinnati Reds':       'cin',  'Cleveland Guardians':     'cle',
  'Colorado Rockies':      'col',  'Detroit Tigers':          'det',
  'Houston Astros':        'hou',  'Kansas City Royals':      'kc',
  'Los Angeles Angels':    'laa',  'Los Angeles Dodgers':     'lad',
  'Miami Marlins':         'mia',  'Milwaukee Brewers':       'mil',
  'Minnesota Twins':       'min',  'New York Mets':           'nym',
  'New York Yankees':      'nyy',  'Athletics':               'oak',
  'Oakland Athletics':     'oak',  'Philadelphia Phillies':   'phi',
  'Pittsburgh Pirates':    'pit',  'San Diego Padres':        'sd',
  'San Francisco Giants':  'sf',   'Seattle Mariners':        'sea',
  'St. Louis Cardinals':   'stl',  'Tampa Bay Rays':          'tb',
  'Texas Rangers':         'tex',  'Toronto Blue Jays':       'tor',
  'Washington Nationals':  'wsh',
};

// ── App state ─────────────────────────────────────────────────────────────────
let gamesData    = null;
let historyData  = [];
let backtestData = null;
let picksData    = null;
let expandedPk   = null;
let currentView  = 'games';
let lastCheckedAt = null;
let propsFilter  = 'all';   // 'all' | 'highconf' | 'value'

// ── Bootstrap ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  setupNav();
  await Promise.all([loadGames(), loadHistory()]);
  lastCheckedAt = Date.now();
  renderGamesView();
  startAutoRefresh();
});

// ── Navigation ────────────────────────────────────────────────────────────────
function setupNav() {
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      currentView = btn.dataset.view;
      document.querySelectorAll('.nav-btn').forEach(b => b.classList.toggle('active', b === btn));
      document.getElementById('games-view').hidden    = currentView !== 'games';
      document.getElementById('props-view').hidden    = currentView !== 'props';
      document.getElementById('record-view').hidden   = currentView !== 'record';
      document.getElementById('backtest-view').hidden = currentView !== 'backtest';
      if (currentView === 'record')   loadBacktest().then(renderRecordView);
      if (currentView === 'backtest') loadBacktest().then(renderBacktestView);
      if (currentView === 'props')    loadPicks().then(renderPropsView);
    });
  });
}

// ── Data loading ──────────────────────────────────────────────────────────────
async function loadGames() {
  try {
    const r = await fetch(GAMES_URL + '?v=' + Date.now());
    gamesData = await r.json();
  } catch {
    gamesData = { games: [], date: new Date().toISOString().slice(0, 10), game_count: 0 };
  }
}

async function loadHistory() {
  try {
    const r = await fetch(HISTORY_URL);
    if (r.ok) historyData = await r.json();
  } catch {
    historyData = [];
  }
}

async function loadBacktest() {
  if (backtestData) return;
  try {
    const r = await fetch(BACKTEST_URL + '?v=' + Date.now());
    if (r.ok) backtestData = await r.json();
  } catch {
    backtestData = null;
  }
}

async function loadPicks() {
  try {
    const r = await fetch(PICKS_URL + '?v=' + Date.now());
    if (r.ok) picksData = await r.json();
  } catch {
    picksData = null;
  }
}

// ── Auto-refresh ──────────────────────────────────────────────────────────────
// Polls every 60s when any game is live, 5 min otherwise.
let _autoTimer = null;

function _hasLiveGames() {
  return (gamesData?.games || []).some(g => g.game_status === 'live');
}

function _refreshInterval() {
  return _hasLiveGames() ? 60 * 1000 : 5 * 60 * 1000;
}

async function _doRefresh() {
  const prev = gamesData?.generated_at;
  await loadGames();
  lastCheckedAt = Date.now();
  if (gamesData?.generated_at !== prev) {
    expandedPk = null;
    renderGamesView();
  } else {
    updateFooter();
  }
  _scheduleRefresh();
}

function _scheduleRefresh() {
  if (_autoTimer) clearTimeout(_autoTimer);
  _autoTimer = setTimeout(_doRefresh, _refreshInterval());
}

function startAutoRefresh() {
  _scheduleRefresh();
}

async function manualRefresh() {
  const btn = document.getElementById('refresh-btn');
  if (btn) btn.classList.add('spinning');
  const prev = gamesData?.generated_at;
  await loadGames();
  lastCheckedAt = Date.now();
  setTimeout(() => { if (btn) btn.classList.remove('spinning'); }, 550);
  if (gamesData?.generated_at !== prev) {
    expandedPk = null;
    renderGamesView();
  } else {
    updateFooter();
  }
}

function updateFooter() {
  const el = document.getElementById('data-footer-text');
  if (el) el.innerHTML = dataFooterText();
}

function dataFooterText() {
  const parts = [];
  if (gamesData?.generated_at) parts.push(`Updated: ${formatGeneratedAt(gamesData.generated_at)}`);
  if (lastCheckedAt) parts.push(`Checked ${timeAgo(lastCheckedAt)}`);
  return parts.join(' &nbsp;·&nbsp; ');
}

function timeAgo(ts) {
  const sec = Math.round((Date.now() - ts) / 1000);
  if (sec < 90) return 'just now';
  const min = Math.floor(sec / 60);
  return `${min} min ago`;
}

// ── Games view ────────────────────────────────────────────────────────────────
function renderGamesView() {
  const view = document.getElementById('games-view');
  if (!gamesData || !gamesData.games.length) {
    view.innerHTML = `<div class="empty-state">No games scheduled today.</div>`;
    return;
  }

  const label = formatDateLabel(gamesData.date);
  view.innerHTML = `
    <div class="view-header">
      <h1>Today's Games</h1>
      <span class="sub-label">${label} &nbsp;·&nbsp; ${gamesData.game_count} games</span>
    </div>
    <div class="game-list" id="game-list">
      ${gamesData.games.map(g => gameCardHTML(g)).join('')}
    </div>
    <div class="data-footer">
      <span id="data-footer-text">${dataFooterText()}</span>
      <button class="refresh-btn" id="refresh-btn" onclick="manualRefresh()" title="Refresh data">↻</button>
    </div>
  `;

  view.querySelectorAll('.game-card-header').forEach(h => {
    h.addEventListener('click', () => {
      const card = h.closest('.game-card');
      const pk   = +card.dataset.pk;
      toggleCard(pk);
    });
  });
}

function toggleCard(pk) {
  const prevPk = expandedPk;

  // Collapse all
  document.querySelectorAll('.game-card').forEach(c => {
    c.classList.remove('expanded');
    const body = c.querySelector('.game-card-body');
    if (body) body.hidden = true;
  });

  if (prevPk !== pk) {
    expandedPk = pk;
    const card = document.querySelector(`.game-card[data-pk="${pk}"]`);
    if (!card) return;
    card.classList.add('expanded');
    card.querySelector('.game-card-body').hidden = false;
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  } else {
    expandedPk = null;
  }
}

// ── Game card HTML ─────────────────────────────────────────────────────────────

// Determine which side is "favored" for card coloring.
// Uses predicted run scores when available so colors always match the displayed
// score numbers. Falls back to win probability (home-field-adjusted) when run
// scores are missing or tied.
function gameFav(g) {
  const pred = g.prediction || {};
  const hr = pred.predicted_home_runs;
  const ar = pred.predicted_away_runs;
  if (hr != null && ar != null && hr !== ar) return hr > ar ? 'home' : 'away';
  return (pred.home_win_pct ?? 0.5) >= 0.5 ? 'home' : 'away';
}

function americanToDecimal(odds) {
  return odds >= 0 ? 1 + odds / 100 : 1 - 100 / odds;
}

function noVigProb(oddsA, oddsB) {
  const rawA = 1 / americanToDecimal(oddsA);
  const rawB = 1 / americanToDecimal(oddsB);
  const total = rawA + rawB;
  return [rawA / total, rawB / total];
}

function vsVegasHTML(g) {
  const odds = g.odds;
  const pred = g.prediction || {};
  if (!odds) return '';

  const sections = [];

  // ── Moneyline edge ─────────────────────────────────────────────────────────
  if (odds.home_ml != null && odds.away_ml != null) {
    const [vegasHomePct, vegasAwayPct] = noVigProb(odds.home_ml, odds.away_ml);
    const modelHomePct = pred.home_win_pct ?? 0.5;
    const modelAwayPct = 1 - modelHomePct;
    const homeEdge = modelHomePct - vegasHomePct;
    const awayEdge = modelAwayPct - vegasAwayPct;

    // Show edge on whichever column has a positive model advantage
    const edgeSide = homeEdge >= awayEdge ? 'home' : 'away';
    const edgePct  = Math.abs(edgeSide === 'home' ? homeEdge : awayEdge);
    const edgeCls  = edgePct >= 0.05 ? 'vv-edge-strong' : edgePct >= 0.02 ? 'vv-edge-mild' : 'vv-edge-flat';

    const homeEdgeStr = homeEdge >= 0 ? `+${(homeEdge*100).toFixed(1)}%` : `${(homeEdge*100).toFixed(1)}%`;
    const awayEdgeStr = awayEdge >= 0 ? `+${(awayEdge*100).toFixed(1)}%` : `${(awayEdge*100).toFixed(1)}%`;

    sections.push(`
<div class="vv-section">
  <div class="vv-title">Moneyline Edge</div>
  <div class="vv-row vv-row-head">
    <span></span>
    <span>${abbrev(g.away_team)}</span>
    <span>${abbrev(g.home_team)}</span>
  </div>
  <div class="vv-row">
    <span class="vv-lbl">Model</span>
    <span>${(modelAwayPct*100).toFixed(1)}%</span>
    <span>${(modelHomePct*100).toFixed(1)}%</span>
  </div>
  <div class="vv-row">
    <span class="vv-lbl">Vegas</span>
    <span>${(vegasAwayPct*100).toFixed(1)}%</span>
    <span>${(vegasHomePct*100).toFixed(1)}%</span>
  </div>
  <div class="vv-edge-row ${edgeCls}">
    <span class="vv-lbl">Edge</span>
    <span>${awayEdgeStr}</span>
    <span>${homeEdgeStr}</span>
  </div>
</div>`);
  }

  // ── Totals edge ────────────────────────────────────────────────────────────
  if (odds.total != null && pred.predicted_total != null
      && odds.over_price != null && odds.under_price != null) {
    const modelTotal = pred.predicted_total;
    const vegasLine  = odds.total;
    const diff       = +(modelTotal - vegasLine).toFixed(1);
    const direction  = diff > 0 ? 'OVER' : diff < 0 ? 'UNDER' : 'PUSH';
    const [vegasOverPct, vegasUnderPct] = noVigProb(odds.over_price, odds.under_price);
    const absDiff  = Math.abs(diff);
    const diffCls  = absDiff >= 0.5 ? 'vv-edge-strong' : absDiff >= 0.2 ? 'vv-edge-mild' : 'vv-edge-flat';
    const dirCls   = direction === 'OVER' ? 'dir-over' : direction === 'UNDER' ? 'dir-under' : '';

    sections.push(`
<div class="vv-section">
  <div class="vv-title">Totals Edge</div>
  <div class="vv-totals-grid">
    <div class="vv-totals-cell">
      <span class="vv-totals-label">Model Total</span>
      <span class="vv-totals-val">${modelTotal.toFixed(1)}</span>
    </div>
    <div class="vv-totals-sep">vs</div>
    <div class="vv-totals-cell">
      <span class="vv-totals-label">Vegas Line</span>
      <span class="vv-totals-val">O/U ${vegasLine}</span>
    </div>
  </div>
  <div class="vv-edge-row ${diffCls}">
    <span class="vv-lbl">Direction</span>
    <span class="pick-dir ${dirCls}">${direction}</span>
    <span class="vv-diff">${diff >= 0 ? '+' : ''}${diff.toFixed(1)} runs</span>
  </div>
  <div class="vv-row vv-row-small">
    <span class="vv-lbl">Vegas implied</span>
    <span>OVER ${(vegasOverPct*100).toFixed(1)}%</span>
    <span>UNDER ${(vegasUnderPct*100).toFixed(1)}%</span>
  </div>
</div>`);
  }

  if (!sections.length) return '';
  return `<div class="vs-vegas-block">${sections.join('')}</div>`;
}

function gameCardHTML(g) {
  const hXera = g.home_sp?.season?.xera;
  const aXera = g.away_sp?.season?.xera;

  const timeStr  = g.game_time_et || formatTimeET(g.game_time_utc);
  const oddsStr  = g.odds ? formatOddsLine(g.odds, g.away_team, g.home_team) : '';
  const wxStr    = formatWeather(g.weather);
  const spChangedBadge = g.sp_changed
    ? `<span class="sp-changed-badge" title="Starting pitcher changed — stats updating on next rebuild">⚠ SP Changed</span>`
    : '';

  const status  = g.game_status || 'preview';
  const fav     = gameFav(g);

  return `
<div class="game-card" data-pk="${g.gamePk}" data-status="${status}" data-fav="${fav}">
  <div class="game-card-header">
    <div class="matchup-grid">
      <div class="team-cell away-cell">
        <div class="logo-namerow">
          <div class="logo-col">
            <div class="logo-bubble away-bubble">${teamLogoHTML(g.away_team)}</div>
            ${teamRecordHTML(g.away_record)}
          </div>
          <div class="team-info">
            <span class="team-name away-name">${g.away_team}</span>
            <span class="sp-line">${g.away_sp?.name || 'TBD'}</span>
            ${aXera != null ? `<span class="xera-line">${spEra(aXera, 'away')}</span>` : ''}
          </div>
        </div>
      </div>
      <div class="game-info-cell">
        <span class="game-time">${timeStr}</span>
        <span class="venue-name">${g.venue}${spChangedBadge}</span>
        ${wxStr || oddsStr ? `<span class="game-meta">${[wxStr, oddsStr].filter(Boolean).join(' · ')}</span>` : ''}
      </div>
      <div class="team-cell home-cell">
        <div class="logo-namerow home-namerow">
          <div class="team-info home-info">
            <span class="team-name home-name">${g.home_team}</span>
            <span class="sp-line">${g.home_sp?.name || 'TBD'}</span>
            ${hXera != null ? `<span class="xera-line">${spEra(hXera, 'home')}</span>` : ''}
          </div>
          <div class="logo-col">
            <div class="logo-bubble home-bubble">${teamLogoHTML(g.home_team)}</div>
            ${teamRecordHTML(g.home_record)}
          </div>
        </div>
      </div>
    </div>
    ${lineupStatusHTML(g)}
    ${lineMovementHTML(g)}
    ${vegasEdgeStripHTML(g)}
    ${statusStrip(g)}
  </div>
  <div class="game-card-body" hidden>
    ${expandedBodyHTML(g)}
  </div>
</div>`;
}

function gameTier(homeWinPct) {
  const conf = Math.max(homeWinPct || 0.5, 1 - (homeWinPct || 0.5));
  if (conf >= 0.70) return 'elite';
  if (conf >= 0.65) return 'great';
  if (conf >= 0.60) return 'good';
  return null;
}

function statusStrip(g) {
  const status = g.game_status || 'preview';

  if (status === 'live') {
    const aSc     = g.away_score ?? '–';
    const hSc     = g.home_score ?? '–';
    const inning  = g.inning_state || 'Live';
    const outsStr = g.outs != null ? ` · ${g.outs} OUT${g.outs !== 1 ? 'S' : ''}` : '';
    return `
<div class="pred-strip">
  <span class="live-state"><span class="live-dot"></span>${inning}${outsStr}</span>
  <span class="live-score">${abbrev(g.away_team)} ${aSc} – ${hSc} ${abbrev(g.home_team)}</span>
  <span class="expand-arrow">▼</span>
</div>`;
  }

  if (status === 'final') {
    const aSc = g.away_score ?? '–';
    const hSc = g.home_score ?? '–';
    return `
<div class="pred-strip">
  <span class="final-badge">FINAL</span>
  <span class="live-score">${abbrev(g.away_team)} ${aSc} – ${hSc} ${abbrev(g.home_team)}</span>
  <span class="expand-arrow">▼</span>
</div>`;
  }

  // Preview
  const pred    = g.prediction || {};
  const homePct = Math.round((pred.home_win_pct || 0.5) * 100);
  const awayPct = 100 - homePct;
  const awayFav = awayPct > homePct;
  const tier    = gameTier(pred.home_win_pct);
  const tierLabel = tier === 'elite' ? 'ELITE' : tier === 'great' ? 'GREAT' : tier === 'good' ? 'GOOD' : '';
  const tierBadge = tier ? `<span class="tier-badge tier-${tier}">${tierLabel}</span>` : '';
  const scoreCenter = pred.predicted_away_runs != null ? `
  <span class="pred-score-est">
    <span class="pse-team">${abbrev(g.away_team)}</span>
    <strong class="pse-num pse-away">${pred.predicted_away_runs}</strong>
    <span class="pse-dash">–</span>
    <strong class="pse-num pse-home">${pred.predicted_home_runs}</strong>
    <span class="pse-team">${abbrev(g.home_team)}</span>
  </span>` : '<span></span>';
  return `
<div class="pred-strip">
  <div class="pred-left">
    <div class="pred-both-pct">
      <span class="${awayFav ? 'pf-fav' : 'pf-dog'}">${abbrev(g.away_team)} ${awayPct}%</span>
      <span class="pf-sep">—</span>
      <span class="${awayFav ? 'pf-dog' : 'pf-fav'}">${abbrev(g.home_team)} ${homePct}%</span>
    </div>
    ${tierBadge}
  </div>
  ${scoreCenter}
  <span class="expand-arrow">▼</span>
</div>`;
}

function spEra(val, side = 'home') {
  const cls = side === 'away' ? 'xera-tag xera-tag-away' : 'xera-tag';
  return `<span class="${cls}">xERA ${val.toFixed(2)}</span>`;
}

// ── Vegas edge strip (collapsed card) — surfaces ML and total model edge ──────
function vegasEdgeStripHTML(g) {
  const odds = g.odds;
  const pred = g.prediction || {};
  const status = g.game_status || 'preview';
  if (!odds || status !== 'preview') return '';

  const pills = [];

  // ML edge
  if (odds.home_ml != null && odds.away_ml != null && pred.home_win_pct != null) {
    const [vegasHomePct, vegasAwayPct] = noVigProb(odds.home_ml, odds.away_ml);
    const modelHomePct = pred.home_win_pct;
    const homeEdge = modelHomePct - vegasHomePct;
    const awayEdge = (1 - modelHomePct) - vegasAwayPct;
    const edgeSide = homeEdge >= awayEdge ? 'home' : 'away';
    const edgePct  = Math.abs(edgeSide === 'home' ? homeEdge : awayEdge);
    const edgeTeam = edgeSide === 'home' ? g.home_team : g.away_team;
    if (edgePct >= 0.02) {
      const cls = edgePct >= 0.05 ? 'edge-pill strong' : 'edge-pill mild';
      pills.push(`<span class="${cls}">ML +${(edgePct * 100).toFixed(1)}% ${abbrev(edgeTeam)}</span>`);
    }
  }

  // Total lean
  if (odds.total != null && pred.predicted_total != null) {
    const diff = +(pred.predicted_total - odds.total).toFixed(1);
    if (Math.abs(diff) >= 0.2) {
      const dir = diff > 0 ? 'OVER' : 'UNDER';
      const dirCls = diff > 0 ? 'dir-over' : 'dir-under';
      const strCls = Math.abs(diff) >= 0.5 ? 'strong' : 'mild';
      pills.push(`<span class="edge-pill ${strCls} ${dirCls}">${dir} ${pred.predicted_total.toFixed(1)} vs ${odds.total}</span>`);
    }
  }

  if (!pills.length) return '';
  return `<div class="edge-strip">${pills.join('')}</div>`;
}

// ── Lineup status (collapsed card) — batter highlights moved to expanded lineup section ──
function lineupStatusHTML(g) {
  const isOfficial = g.lineup_status !== 'tbd';
  const statusChip = isOfficial
    ? `<span class="lineup-chip official">✓ Official</span>`
    : `<span class="lineup-chip tbd">Lineups TBD</span>`;
  return `<div class="lineup-status-row"><div class="ls-center">${statusChip}</div></div>`;
}

function getNotableBatters(lineup, maxShow = 3) {
  if (!lineup?.length) return [];
  const notable = [];
  for (const b of lineup) {
    let type = null, label = null, mag = 0;

    if (b.trend_flags?.length) {
      const flag = b.trend_flags[0];
      const isHot = flag.startsWith('Hot');
      if (isHot || flag.startsWith('Cold')) {
        const m = flag.match(/(\d+)H in last (\d+)/);
        type  = isHot ? 'hot' : 'cold';
        label = m ? `${m[1]}H/${m[2]}G` : (isHot ? 'streak' : '0H streak');
        mag   = 3;
      }
    }

    if (type === null && b.woba != null && b.xwoba != null) {
      const gap = b.woba - b.xwoba;
      if (Math.abs(gap) >= 0.025) {
        type  = gap > 0 ? 'over' : 'under';
        label = `w${fmtWoba(b.woba)} xw${fmtWoba(b.xwoba)}`;
        mag   = Math.abs(gap);
      }
    }

    if (type) notable.push({ ...b, _type: type, _label: label, _mag: mag });
  }
  notable.sort((a, b) => b._mag - a._mag);
  return notable.slice(0, maxShow);
}

// ── Expanded card body ────────────────────────────────────────────────────────
function expandedBodyHTML(g) {
  return `
<div class="expanded-inner">
  <div class="expanded-section">
    <div class="section-heading">Pitchers</div>
    ${pitcherTableHTML(g)}
  </div>
  <div class="expanded-section">
    <div class="section-heading">Lineups</div>
    ${lineupsHTML(g)}
  </div>
  <div class="expanded-section">
    <div class="section-heading">${(g.game_status && g.game_status !== 'preview') ? 'Pre-game Prediction' : 'Prediction'}</div>
    ${predictionHTML(g)}
  </div>
</div>`;
}

// ── Pitcher table ─────────────────────────────────────────────────────────────
function pitcherTableHTML(g) {
  const hsp = g.home_sp || {};
  const asp = g.away_sp || {};
  const hs  = hsp.season || {};
  const as_ = asp.season || {};
  const hr  = hsp.recent || {};
  const ar  = asp.recent || {};

  // [label, awayVal, homeVal, lowerIsBetter, recentAway, recentHome]
  const rows = [
    ['xERA',        as_.xera,      hs.xera,      true,  ar.xera,      hr.xera],
    ['xBA Against', as_.xba,       hs.xba,       true,  null,         null],
    ['Whiff%',      as_.whiff_pct, hs.whiff_pct, false, ar.whiff_pct, hr.whiff_pct],
    ['Chase%',      as_.chase_pct, hs.chase_pct, false, ar.chase_pct, hr.chase_pct],
    ['K%',          as_.k_pct,     hs.k_pct,     false, ar.k_pct,     hr.k_pct],
    ['BB%',         as_.bb_pct,    hs.bb_pct,    true,  ar.bb_pct,    hr.bb_pct],
    ['RV/100',      as_.rv100,     hs.rv100,     false, null,         null],
  ];

  let tbody = '';
  for (const [label, av, hv, lowerBetter, ar_v, hr_v] of rows) {
    const awayBetter = av != null && hv != null && (lowerBetter ? av < hv : av > hv);
    const homeBetter = av != null && hv != null && (lowerBetter ? hv < av : hv > av);
    tbody += `
    <tr>
      <td class="stat-lbl">${label}</td>
      <td class="stat-val away-val${awayBetter ? ' better' : homeBetter ? ' worse' : ''}">
        ${fmtStatVal(av, label)}${ar_v != null ? ` <span class="rcnt">(${fmtStatVal(ar_v, label)})</span>` : ''}
      </td>
      <td class="stat-val home-val${homeBetter ? ' better' : awayBetter ? ' worse' : ''}">
        ${fmtStatVal(hv, label)}${hr_v != null ? ` <span class="rcnt">(${fmtStatVal(hr_v, label)})</span>` : ''}
      </td>
    </tr>`;
  }

  const awayFlags = asp.trend_flags || [];
  const homeFlags = hsp.trend_flags || [];
  const allFlags  = [
    ...awayFlags.map(f => `<span class="trend-pill away-pill">${asp.name || g.away_team}: ${f}</span>`),
    ...homeFlags.map(f => `<span class="trend-pill home-pill">${hsp.name || g.home_team}: ${f}</span>`),
  ];

  // Last-start deviation badges (data from MLB game log)
  const lastStartPills = [];
  for (const [sp, label] of [[asp, asp.name || g.away_team], [hsp, hsp.name || g.home_team]]) {
    const dev = sp.last_start?.deviation;
    if (dev == null) continue;
    if (dev <= -1.5) {
      lastStartPills.push(`<span class="trend-pill trend-pill-hot">↑ ${label}: last start ${dev.toFixed(1)} vs xERA</span>`);
    } else if (dev >= 1.5) {
      lastStartPills.push(`<span class="trend-pill trend-pill-cold">↓ ${label}: last start +${dev.toFixed(1)} vs xERA</span>`);
    }
  }

  const flagPills = [...allFlags, ...lastStartPills];

  return `
<table class="pitcher-table">
  <thead>
    <tr>
      <th></th>
      <th class="away-th">${asp.name || g.away_team}</th>
      <th class="home-th">${hsp.name || g.home_team}</th>
    </tr>
  </thead>
  <tbody>${tbody}</tbody>
</table>
${flagPills.length ? `<div class="flag-row">${flagPills.join('')}</div>` : ''}`;
}

// ── Lineups ───────────────────────────────────────────────────────────────────
function lineupsHTML(g) {
  if (g.lineup_status === 'tbd') {
    return `<div class="lineup-tbd">
      Lineups not yet posted — check back closer to game time.
    </div>`;
  }

  const chip = b =>
    `<span class="bh-chip ${b._type}">${shortName(b.name)} · ${b._label}</span>`;

  const awayNotable = getNotableBatters(g.away_lineup);
  const homeNotable = getNotableBatters(g.home_lineup);
  const allNotable  = [...awayNotable, ...homeNotable];
  const insightsRow = allNotable.length
    ? `<div class="lineup-insights-row">${allNotable.map(chip).join('')}</div>` : '';

  return `
${insightsRow}
<div class="lineup-pair">
  <div class="lineup-half">
    <div class="lineup-team-label">${g.away_team} <span class="side-tag">Away</span></div>
    ${lineupTableHTML(g.away_lineup || [])}
  </div>
  <div class="lineup-half">
    <div class="lineup-team-label">${g.home_team} <span class="side-tag">Home</span></div>
    ${lineupTableHTML(g.home_lineup || [])}
  </div>
</div>`;
}

function lineupTableHTML(lineup) {
  if (!lineup || !lineup.length) {
    return `<div class="lineup-empty">Lineup not available</div>`;
  }

  const rows = lineup.map(b => {
    const streakPill = (() => {
      if (!b.trend_flags?.length) return '';
      const isHot = b.trend_flags[0].startsWith('Hot');
      return ` <span class="streak-pill ${isHot ? 'hot' : 'cold'}">${isHot ? 'HOT' : 'COLD'}</span>`;
    })();
    const wobaCls = wobaClass(b);
    return `
  <tr>
    <td class="bo">${b.batting_order}</td>
    <td class="bname">${shortName(b.name)}${streakPill}</td>
    <td>${b.xwoba != null ? fmtWoba(b.xwoba) : dash()}</td>
    <td class="${wobaCls}">${b.woba != null ? fmtWoba(b.woba) : dash()}</td>
    <td>${b.avg_ev != null ? b.avg_ev.toFixed(1) : dash()}</td>
    <td>${b.hard_hit_pct != null ? fmtPct(b.hard_hit_pct) : dash()}</td>
    <td>${b.k_pct != null ? fmtPct(b.k_pct) : dash()}</td>
    <td>${b.bb_pct != null ? fmtPct(b.bb_pct) : dash()}</td>
  </tr>`;
  }).join('');

  return `
<table class="lineup-table">
  <thead>
    <tr><th>#</th><th>Name</th><th>xwOBA</th><th>wOBA</th><th>EV</th><th>HH%</th><th>K%</th><th>BB%</th></tr>
  </thead>
  <tbody>${rows}</tbody>
</table>`;
}

// ── Prediction section ────────────────────────────────────────────────────────
function predictionHTML(g) {
  const pred    = g.prediction || {};
  const signals = pred.model_signals || {};
  const homePct = Math.round((pred.home_win_pct || 0.5) * 100);
  const awayPct = 100 - homePct;
  const pHome   = signals.pitcher_score_home;
  const pAway   = signals.pitcher_score_away;
  const lHome   = signals.lineup_score_home;
  const lAway   = signals.lineup_score_away;

  const pitchEdge  = pHome != null && pAway != null ? pHome - pAway : null;
  const lineupEdge = lHome != null && lAway != null ? lHome - lAway : null;

  const awayFav = awayPct > homePct;

  function edgeBadge(val, homeLabel, awayLabel) {
    if (val == null) return '';
    const abs = Math.abs(val);
    if (abs < 0.03) return `<span class="sig-badge neutral">Even</span>`;
    const homeEdge = val > 0;
    const label = homeEdge
      ? `${homeLabel} +${(abs * 100).toFixed(0)}`
      : `${awayLabel} +${(abs * 100).toFixed(0)}`;
    // Green when the edge aligns with the predicted winner; red when it works against
    const confirmsWinner = (homeEdge && !awayFav) || (!homeEdge && awayFav);
    const cls = confirmsWinner ? 'edge-winner' : 'edge-loser';
    return `<span class="sig-badge ${cls}">${label}</span>`;
  }
  return `
<div class="prediction-block">
  <div class="prob-bar-wrap">
    <span class="prob-label ${awayFav ? 'win-label' : 'lose-label'}">${g.away_team} ${awayPct}%</span>
    <div class="prob-bar">
      <div class="prob-fill ${awayFav ? 'win-fill' : 'lose-fill'}" style="width:${awayPct}%"></div>
      <div class="prob-fill ${awayFav ? 'lose-fill' : 'win-fill'}" style="width:${homePct}%"></div>
    </div>
    <span class="prob-label ${awayFav ? 'lose-label' : 'win-label'}">${g.home_team} ${homePct}%</span>
  </div>

  ${pred.predicted_home_runs != null ? `
  <div class="score-est">
    <span>${g.away_team} <strong>${pred.predicted_away_runs}</strong></span>
    <span class="score-dash">–</span>
    <span><strong>${pred.predicted_home_runs}</strong> ${g.home_team}</span>
    <span class="total-label">Total: ${pred.predicted_total}</span>
  </div>` : ''}

  ${vsVegasHTML(g)}

  ${pred.narrative ? `<p class="narrative">${pred.narrative}</p>` : ''}

  <div class="signals-row">
    <span class="sig-label">Signals:</span>
    ${edgeBadge(pitchEdge,  'Home pitching', 'Away pitching')}
    ${edgeBadge(lineupEdge, 'Home lineup',   'Away lineup')}
    ${signals.comps_home_win_rate != null
      ? `<span class="sig-badge neutral">Comps: ${Math.round(signals.comps_home_win_rate * 100)}% home (n=${signals.comps_count})</span>`
      : ''}
  </div>
</div>`;
}

// ── Game log cell helpers ─────────────────────────────────────────────────────

function mlEdgeCell(r) {
  if (r.model_edge_ml == null) {
    return `<td class="hist-edge-ml"><span style="color:var(--text-dim)">—</span></td>`;
  }
  const edge     = r.model_edge_ml;
  const absPct   = Math.abs(edge * 100).toFixed(1);
  const sign     = edge >= 0 ? '+' : '–';
  const teamAbbr = abbrev(edge >= 0 ? r.home_team : r.away_team);
  const edgeWon  = edge >= 0 ? r.actual_winner === 'home' : r.actual_winner === 'away';
  const icon     = r.sp_scratched ? '' : (edgeWon
    ? '<span class="edge-call-hit">✓</span>'
    : '<span class="edge-call-miss">✗</span>');
  const cellCls  = Math.abs(edge) >= 0.10
    ? (edge < 0 ? 'hist-edge-ml edge-strong-away' : 'hist-edge-ml edge-strong-home')
    : 'hist-edge-ml';
  return `<td class="${cellCls}">${sign}${absPct}% ${teamAbbr} ${icon}</td>`;
}

function totalLeanCell(r) {
  if (r.predicted_total == null || r.vegas_total == null) {
    return `<td class="hist-total"><span style="color:var(--text-dim)">—</span></td>`;
  }
  const lean = +(r.predicted_total - r.vegas_total).toFixed(1);
  if (lean === 0) return `<td class="hist-total"><span style="color:var(--text-dim)">—</span></td>`;
  const dir    = lean > 0 ? 'OVER' : 'UNDER';
  const dirCls = lean > 0 ? 'dir-over' : 'dir-under';
  const gap    = Math.abs(lean).toFixed(1);
  let icon = '';
  if (r.total_went_over != null && !r.sp_scratched) {
    const hit = (lean > 0 && r.total_went_over === true) || (lean < 0 && r.total_went_over === false);
    icon = hit ? '<span class="edge-call-hit">✓</span>' : '<span class="edge-call-miss">✗</span>';
  }
  return `<td class="hist-total"><span class="${dirCls}">${dir} +${gap}</span> ${icon}</td>`;
}

// ── Record view ───────────────────────────────────────────────────────────────
function renderRecordView() {
  const view = document.getElementById('record-view');
  // Ties (true ties, not postponements) and unresolved games excluded from grading
  const decided = historyData.filter(r => r.actual_winner === 'home' || r.actual_winner === 'away');

  if (!decided.length) {
    view.innerHTML = `<div class="empty-state">No resolved predictions yet.<br>Check back after games have been played.</div>`;
    return;
  }

  const correct = decided.filter(r => r.predicted_winner === r.actual_winner).length;
  const pct     = Math.round(correct / decided.length * 100);
  const streak  = calcStreak(decided);
  const streakLabel = streak.count > 1
    ? `<span class="streak-badge streak-${streak.type}">${streak.type === 'W' ? '🔥' : '❄'} ${streak.count}-game ${streak.type === 'W' ? 'win' : 'loss'} streak</span>`
    : '';

  const confRows  = calcConfidenceTiers(decided);
  const signals   = calcSignalAccuracy(decided);
  const byDate    = groupByDate(decided);

  // Game log summary stats
  let mlEdgeCalls = 0, mlEdgeHits = 0, totalLeanCalls = 0, totalLeanHits = 0;
  for (const r of decided) {
    if (r.model_edge_ml != null && Math.abs(r.model_edge_ml) >= 0.10) {
      mlEdgeCalls++;
      const won = r.model_edge_ml >= 0 ? r.actual_winner === 'home' : r.actual_winner === 'away';
      if (won) mlEdgeHits++;
    }
    if (r.predicted_total != null && r.vegas_total != null && r.total_went_over != null) {
      const lean = +(r.predicted_total - r.vegas_total).toFixed(1);
      if (lean !== 0) {
        totalLeanCalls++;
        if ((lean > 0 && r.total_went_over) || (lean < 0 && !r.total_went_over)) totalLeanHits++;
      }
    }
  }
  const mlPct    = mlEdgeCalls    > 0 ? Math.round(mlEdgeHits    / mlEdgeCalls    * 100) : null;
  const totalPct = totalLeanCalls > 0 ? Math.round(totalLeanHits / totalLeanCalls * 100) : null;
  const gameLogSummaryHTML = `
<div class="game-log-summary">
  <span class="log-stat">ML Value Calls (|edge|≥10%): <strong>${mlEdgeCalls > 0 ? `${mlEdgeHits}/${mlEdgeCalls} (${mlPct}%)` : '—'}</strong></span>
  <span class="log-stat">Total Lean: <strong>${totalLeanCalls > 0 ? `${totalLeanHits}/${totalLeanCalls} (${totalPct}%)` : '—'}</strong></span>
</div>`;

  view.innerHTML = `
<div class="view-header">
  <h1>Prediction Record</h1>
  <span class="sub-label">${correct}–${decided.length - correct} (${pct}%) &nbsp;·&nbsp; ${decided.length} games graded ${streakLabel}</span>
</div>

<div class="rec-vegas-section">
  ${renderVegasSection()}
</div>

<div class="record-top-grid">
  <div class="record-conf-section">
    <div class="section-heading">Record by Confidence</div>
    <table class="conf-tier-table">
      <thead><tr><th>Confidence</th><th>Record</th><th>Win%</th></tr></thead>
      <tbody>${confRows.map(t => `
        <tr>
          <td>${t.label}${t.badge ? ' <span class="tier-badge tier-' + t.badgeCls + '">' + t.badge + '</span>' : ''}</td>
          <td class="conf-record">${t.correct}–${t.total - t.correct}</td>
          <td class="conf-pct ${t.cls}">${t.total > 0 ? Math.round(t.correct / t.total * 100) + '%' : '—'}</td>
        </tr>`).join('')}
      </tbody>
    </table>
  </div>
  <div class="record-signal-section">
    <div class="section-heading">Signal Accuracy</div>
    <div class="signal-grid compact-signal-grid">
      ${Object.values(signals).map(s => signalCardHTML(s)).join('')}
    </div>
  </div>
</div>

<div class="history-section">
  <div class="section-heading">Game Log</div>
  ${gameLogSummaryHTML}
  ${byDate.map(({ date: d, games }) => {
    const dc = games.filter(r => r.predicted_winner === r.actual_winner).length;
    const dLabel = formatDateLabel(d);

    // Per-day model audit
    let mlW = 0, mlL = 0, totW = 0, totL = 0;
    for (const r of games) {
      if (r.model_edge_ml != null && Math.abs(r.model_edge_ml) >= 0.10) {
        const won = r.model_edge_ml >= 0 ? r.actual_winner === 'home' : r.actual_winner === 'away';
        if (won) mlW++; else mlL++;
      }
      if (r.predicted_total != null && r.vegas_total != null && r.total_went_over != null) {
        const lean = +(r.predicted_total - r.vegas_total).toFixed(1);
        if (Math.abs(lean) >= 0.5) {
          const hit = (lean > 0 && r.total_went_over === true) || (lean < 0 && r.total_went_over === false);
          if (hit) totW++; else totL++;
        }
      }
    }
    const mlAuditHTML = (mlW + mlL > 0)
      ? `<span class="day-audit-stat ${mlW > mlL ? 'audit-pos' : mlL > mlW ? 'audit-neg' : 'audit-even'}">ML Edge ${mlW}–${mlL}</span>`
      : '';
    const totAuditHTML = (totW + totL > 0)
      ? `<span class="day-audit-stat ${totW > totL ? 'audit-pos' : totL > totW ? 'audit-neg' : 'audit-even'}">Total ${totW}–${totL}</span>`
      : '';
    const auditRowHTML = (mlW + mlL + totW + totL > 0)
      ? `<div class="day-audit-row"><span class="day-audit-label">Audit</span>${mlAuditHTML}${totAuditHTML}</div>`
      : '';

    return `
  <div class="day-group">
    <div class="day-header">
      <div class="day-header-main">
        <span class="day-label">${dLabel}</span>
        <span class="day-record ${dc / games.length >= 0.5 ? 'day-win' : 'day-loss'}">Picks ${dc}–${games.length - dc}</span>
      </div>
      ${auditRowHTML}
    </div>
    <div class="history-table-wrap">
      <table class="history-table">
        <thead>
          <tr>
            <th>Matchup</th>
            <th>Predicted</th>
            <th>Actual</th>
            <th>ML Edge</th>
            <th>Total</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          ${games.slice().reverse().map(r => {
            const hit      = r.predicted_winner === r.actual_winner;
            const predTeam = r.predicted_winner === 'home' ? r.home_team : r.away_team;
            const actTeam  = r.actual_winner    === 'home' ? r.home_team : r.away_team;
            const predPct  = r.predicted_winner === 'home'
              ? Math.round((r.home_win_pct || 0.5) * 100)
              : Math.round((1 - (r.home_win_pct || 0.5)) * 100);
            const conf     = Math.max(r.home_win_pct || 0.5, 1 - (r.home_win_pct || 0.5));
            const tierCls  = conf >= 0.70 ? 'elite' : conf >= 0.65 ? 'great' : conf >= 0.60 ? 'good' : '';
            const score    = r.home_score != null
              ? `<span class="hist-score">${r.away_score}–${r.home_score}</span>`
              : '';
            const spBadge  = r.sp_scratched
              ? ' <span class="sp-scratch-badge" title="Predicted starter did not start">⚠ SP</span>' : '';
            return `
          <tr class="${r.sp_scratched ? 'row-scratch' : (hit ? 'row-hit' : 'row-miss')}">
            <td class="hist-matchup">${abbrev(r.away_team)} @ ${abbrev(r.home_team)}${spBadge}</td>
            <td class="hist-pred">
              ${abbrev(predTeam)} <span class="hist-pct${tierCls ? ' tier-badge tier-' + tierCls : ''}">${predPct}%</span>
            </td>
            <td class="hist-actual">${abbrev(actTeam)} ${score}</td>
            ${mlEdgeCell(r)}
            ${totalLeanCell(r)}
            <td class="result-icon">${r.sp_scratched ? '<span class="res-scratch">–</span>' : (hit ? '<span class="res-hit">✓</span>' : '<span class="res-miss">✗</span>')}</td>
          </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>
  </div>`;
  }).join('')}
</div>`;
}

function calcStreak(decided) {
  if (!decided.length) return { type: 'W', count: 0 };
  const last = decided[decided.length - 1];
  const type = last.predicted_winner === last.actual_winner ? 'W' : 'L';
  let count = 0;
  for (let i = decided.length - 1; i >= 0; i--) {
    const hit = decided[i].predicted_winner === decided[i].actual_winner;
    if ((type === 'W') === hit) count++;
    else break;
  }
  return { type, count };
}

function calcConfidenceTiers(decided) {
  const tiers = [
    { label: '70%+',   badge: 'ELITE', badgeCls: 'elite', lo: 0.70, hi: 1.00, correct: 0, total: 0 },
    { label: '65–70%', badge: 'GREAT', badgeCls: 'great', lo: 0.65, hi: 0.70, correct: 0, total: 0 },
    { label: '60–65%', badge: 'GOOD',  badgeCls: 'good',  lo: 0.60, hi: 0.65, correct: 0, total: 0 },
    { label: 'Under 60%', badge: null, badgeCls: '',       lo: 0.50, hi: 0.60, correct: 0, total: 0 },
  ];
  for (const r of decided) {
    const conf = Math.max(r.home_win_pct || 0.5, 1 - (r.home_win_pct || 0.5));
    const hit  = r.predicted_winner === r.actual_winner;
    for (const t of tiers) {
      if (conf >= t.lo && (t.hi === 1.00 ? conf <= t.hi : conf < t.hi)) {
        t.total++;
        if (hit) t.correct++;
        break;
      }
    }
  }
  return tiers.map(t => ({
    ...t,
    cls: t.total === 0 ? '' : (t.correct / t.total >= 0.60 ? 'conf-strong' : t.correct / t.total >= 0.50 ? 'conf-ok' : 'conf-weak'),
  }));
}

function groupByDate(decided) {
  const map = new Map();
  for (const r of decided) {
    if (!map.has(r.date)) map.set(r.date, []);
    map.get(r.date).push(r);
  }
  return Array.from(map.entries())
    .sort(([a], [b]) => b.localeCompare(a))
    .map(([date, games]) => ({ date, games }));
}

function signalCardHTML(s) {
  const rate = s.total > 0 ? Math.round(s.correct / s.total * 100) : null;
  const cls  = rate == null ? '' : rate >= 60 ? 'sig-good' : rate >= 52 ? 'sig-ok' : 'sig-weak';
  return `
<div class="sig-card ${cls}">
  <div class="sig-card-label">${s.label}</div>
  <div class="sig-card-rate">${rate != null ? rate + '%' : '—'}</div>
  <div class="sig-card-count">${s.correct}/${s.total}</div>
</div>`;
}

function calcSignalAccuracy(decided) {
  const m = {
    pitcher: { label: 'Pitcher edge ≥ 5pts', correct: 0, total: 0 },
    comps:   { label: 'Comps signal ≥ 55%',  correct: 0, total: 0 },
    conf60:  { label: 'Picks at 60%+',        correct: 0, total: 0 },
    conf65:  { label: 'Picks at 65%+',        correct: 0, total: 0 },
  };

  for (const r of decided) {
    const homeWon = r.actual_winner === 'home';
    const hit     = r.predicted_winner === r.actual_winner;
    const conf    = Math.max(r.home_win_pct || 0.5, 1 - (r.home_win_pct || 0.5));

    const ph = r.pitcher_score_home, pa = r.pitcher_score_away;
    if (ph != null && pa != null && Math.abs(ph - pa) >= 0.05) {
      const favouredHomeWin = ph > pa;
      m.pitcher.total++;
      if (favouredHomeWin === homeWon) m.pitcher.correct++;
    }

    if (r.comps_home_win_rate != null) {
      const compsHome = r.comps_home_win_rate >= 0.55;
      const compsAway = r.comps_home_win_rate <= 0.45;
      if (compsHome || compsAway) {
        m.comps.total++;
        if ((compsHome && homeWon) || (compsAway && !homeWon)) m.comps.correct++;
      }
    }

    if (conf >= 0.60) { m.conf60.total++; if (hit) m.conf60.correct++; }
    if (conf >= 0.65) { m.conf65.total++; if (hit) m.conf65.correct++; }
  }

  return m;
}

// ── Vegas performance analysis ────────────────────────────────────────────────

// Normalise a record from either history.json or backtest.json to a common shape.
function _normaliseVegasRecord(r) {
  return {
    ...r,
    vegas_total:     r.vegas_total    ?? r.closing_total ?? null,
    total_went_over: r.total_went_over ?? (
      r.closing_total != null && r.actual_total != null
        ? r.actual_total > r.closing_total
        : null
    ),
  };
}

function _allVegasRecords() {
  // 2026 live records from history.json
  const hist = (historyData || [])
    .filter(r => r.actual_winner === 'home' || r.actual_winner === 'away')
    .map(_normaliseVegasRecord);

  // 2021-2025 historical records from backtest.json (only games with Pinnacle odds)
  const bt = ((backtestData && backtestData.games) || [])
    .filter(r => r.home_ml != null && (r.actual_winner === 'home' || r.actual_winner === 'away'))
    .map(_normaliseVegasRecord);

  return { hist, bt, all: [...hist, ...bt] };
}

function computeVegasStats(records) {
  const priced = records.filter(r => r.home_ml != null && r.away_ml != null);

  function mlUnits(odds, won) {
    const ret = odds > 0 ? odds / 100 : 100 / Math.abs(odds);
    return won ? ret : -1;
  }

  // ML edge buckets: classify by model_edge_ml (model's home_win_pct − pinnacle implied home prob)
  // Positive edge = model favours home more than Vegas; negative = model favours away
  const mlBuckets = {
    negative: { label: 'Away Pick',   desc: 'Model favours away', n: 0, wins: 0, units: 0 },
    low:      { label: '0–3% Edge',   desc: 'Marginal home lean',  n: 0, wins: 0, units: 0 },
    mid:      { label: '3–6% Edge',   desc: 'Moderate home edge',  n: 0, wins: 0, units: 0 },
    high:     { label: '6%+ Edge',    desc: 'Strong home edge',    n: 0, wins: 0, units: 0 },
  };

  for (const r of priced) {
    const edge    = r.model_edge_ml ?? 0;
    const homeWon = r.actual_winner === 'home';
    // Determine which side we'd bet (model's pick direction) and appropriate odds
    let bucket, won, odds;
    if (edge < 0) {
      bucket = mlBuckets.negative;
      won    = !homeWon;         // model picked away
      odds   = r.away_ml;
    } else if (edge < 0.03) {
      bucket = mlBuckets.low;
      won    = homeWon;
      odds   = r.home_ml;
    } else if (edge < 0.06) {
      bucket = mlBuckets.mid;
      won    = homeWon;
      odds   = r.home_ml;
    } else {
      bucket = mlBuckets.high;
      won    = homeWon;
      odds   = r.home_ml;
    }
    if (odds == null) continue;
    bucket.n++;
    if (won) bucket.wins++;
    bucket.units += mlUnits(odds, won);
  }

  // Totals direction
  const pricedTotals = priced.filter(r => r.vegas_total != null && r.actual_total != null);
  const totalsBuckets = {
    over:  { label: 'Model Over',  n: 0, hits: 0, units: 0 },
    under: { label: 'Model Under', n: 0, hits: 0, units: 0 },
    push:  { label: 'No Lean',     n: 0, hits: 0, units: 0 },
  };

  for (const r of pricedTotals) {
    const diff    = (r.predicted_total ?? 0) - r.vegas_total;
    const wentOver = r.actual_total > r.vegas_total;
    let bucket, odds, won;
    if (diff > 0.5) {
      bucket = totalsBuckets.over;
      odds   = r.over_price  ?? -110;
      won    = wentOver;
    } else if (diff < -0.5) {
      bucket = totalsBuckets.under;
      odds   = r.under_price ?? -110;
      won    = !wentOver;
    } else {
      bucket = totalsBuckets.push;
      odds   = -110;
      won    = false;  // no-lean bets not counted toward ROI
    }
    bucket.n++;
    if (won) bucket.hits++;
    if (bucket !== totalsBuckets.push) bucket.units += mlUnits(odds, won);
  }

  // Overall ROI summaries
  const mlUnitsTotal  = Object.values(mlBuckets).reduce((s, b) => s + b.units, 0);
  const mlBetsTotal   = Object.values(mlBuckets).reduce((s, b) => s + b.n, 0);
  const totUnitsTotal = totalsBuckets.over.units + totalsBuckets.under.units;
  const totBetsTotal  = totalsBuckets.over.n + totalsBuckets.under.n;

  return {
    ml:       { buckets: mlBuckets,   totalUnits: mlUnitsTotal,  totalBets: mlBetsTotal },
    totals:   { buckets: totalsBuckets, totalUnits: totUnitsTotal, totalBets: totBetsTotal },
    n_priced: priced.length,
    n_total:  records.length,
  };
}

function renderVegasSection() {
  const { hist, bt, all } = _allVegasRecords();
  const v = computeVegasStats(all);
  const MIN_GAMES = 5;

  const histPriced = hist.filter(r => r.home_ml != null).length;
  const btNote = bt.length > 0
    ? `${bt.length.toLocaleString()} historical (2021–25) + ${histPriced} this season`
    : `${histPriced} games this season`;

  if (v.n_priced < MIN_GAMES) {
    return `
<div class="section-heading">Performance vs. Vegas Lines</div>
<div class="rec-vegas-placeholder">
  Vegas line tracking active — section populates as games accumulate (${v.n_priced} games have line data).
</div>`;
  }

  function winPct(b) {
    return b.n > 0 ? Math.round(b.wins / b.n * 100) + '%' : '—';
  }
  function hitPct(b) {
    return b.n > 0 ? Math.round(b.hits / b.n * 100) + '%' : '—';
  }
  function roiStr(units, n) {
    if (!n) return '—';
    const pct  = (units / n * 100).toFixed(1);
    const sign = units >= 0 ? '+' : '';
    return `${sign}${pct}%`;
  }
  function roiCls(units) {
    return units > 0 ? 'edge-pos' : units < 0 ? 'edge-neg' : '';
  }
  function edgeCls(bucket) {
    const { n, wins } = bucket;
    if (!n) return 'edge-card';
    const rate = wins / n;
    return `edge-card ${rate >= 0.55 ? 'edge-green' : rate >= 0.50 ? 'edge-amber' : 'edge-red'}`;
  }
  function totalsCls(bucket) {
    const { n, hits } = bucket;
    if (!n) return 'edge-card';
    const rate = hits / n;
    return `edge-card ${rate >= 0.55 ? 'edge-green' : rate >= 0.50 ? 'edge-amber' : 'edge-red'}`;
  }

  // Build per-season accuracy rows from backtest games + 2026 history
  function buildSeasonRows() {
    const byYear = {};
    // backtest games (2021-2025): use correct/actual_winner fields
    for (const r of (backtestData && backtestData.games) || []) {
      const yr = r.season || (r.date || '').slice(0, 4);
      if (!yr) continue;
      if (!byYear[yr]) byYear[yr] = { n: 0, correct: 0, units: 0, bets: 0 };
      byYear[yr].n++;
      if (r.correct) byYear[yr].correct++;
      if (r.home_ml != null && r.away_ml != null) {
        const homeWon = r.actual_winner === 'home';
        const edge    = r.model_edge_ml ?? 0;
        const betHome = edge >= 0;
        const won     = betHome ? homeWon : !homeWon;
        const odds    = betHome ? r.home_ml : r.away_ml;
        const ret     = odds > 0 ? odds / 100 : 100 / Math.abs(odds);
        byYear[yr].units += won ? ret : -1;
        byYear[yr].bets++;
      }
    }
    // 2026 history
    for (const r of (historyData || [])) {
      if (r.actual_winner !== 'home' && r.actual_winner !== 'away') continue;
      const yr = (r.date || '').slice(0, 4) || '2026';
      if (!byYear[yr]) byYear[yr] = { n: 0, correct: 0, units: 0, bets: 0 };
      byYear[yr].n++;
      if (r.predicted_winner === r.actual_winner) byYear[yr].correct++;
      if (r.home_ml != null && r.away_ml != null) {
        const homeWon = r.actual_winner === 'home';
        const edge    = r.model_edge_ml ?? 0;
        const betHome = edge >= 0;
        const won     = betHome ? homeWon : !homeWon;
        const odds    = betHome ? r.home_ml : r.away_ml;
        const ret     = odds > 0 ? odds / 100 : 100 / Math.abs(odds);
        byYear[yr].units += won ? ret : -1;
        byYear[yr].bets++;
      }
    }
    return Object.entries(byYear)
      .sort(([a], [b]) => Number(a) - Number(b))
      .map(([yr, d]) => {
        const acc    = d.n ? Math.round(d.correct / d.n * 100) : 0;
        const roiPct = d.bets ? (d.units / d.bets * 100).toFixed(1) : null;
        const roiTxt = roiPct != null ? `<span class="${d.units >= 0 ? 'edge-pos' : 'edge-neg'}">${d.units >= 0 ? '+' : ''}${roiPct}%</span>` : '—';
        return `<tr>
          <td class="syr-yr">${yr}</td>
          <td>${d.correct}–${d.n - d.correct}</td>
          <td class="${acc >= 55 ? 'edge-pos' : acc >= 50 ? '' : 'edge-neg'}">${acc}%</td>
          <td>${d.bets > 0 ? d.bets : '—'}</td>
          <td>${roiTxt}</td>
        </tr>`;
      }).join('');
  }

  const { ml, totals } = v;
  const b = ml.buckets;
  const t = totals.buckets;
  const mlRoi  = roiStr(ml.totalUnits,     ml.totalBets);
  const totRoi = roiStr(totals.totalUnits, totals.totalBets);
  const mlSign  = ml.totalUnits  >= 0 ? '+' : '';
  const totSign = totals.totalUnits >= 0 ? '+' : '';

  return `
<div class="section-heading">Performance vs. Vegas Lines</div>
<p class="rec-priced-note">${v.n_priced.toLocaleString()} games with Pinnacle lines &nbsp;·&nbsp; ${btNote}</p>

<div class="section-subheading">Season Breakdown</div>
<table class="season-year-table">
  <thead><tr><th>Season</th><th>Record</th><th>Acc%</th><th>Bets</th><th>ML ROI</th></tr></thead>
  <tbody>${buildSeasonRows()}</tbody>
</table>

<div class="section-subheading" style="margin-top:16px;">Moneyline Edge Buckets</div>
<div class="edge-bucket-grid">
  <div class="${edgeCls(b.negative)}">
    <div class="edge-card-label">${b.negative.label}</div>
    <div class="edge-rate">${winPct(b.negative)}</div>
    <div class="edge-n">${b.negative.n} games</div>
    <div class="edge-desc">${b.negative.desc}</div>
    <div class="edge-badge ${roiCls(b.negative.units) === 'edge-pos' ? 'badge-green' : roiCls(b.negative.units) === 'edge-neg' ? 'badge-red' : 'badge-amber'}">${roiStr(b.negative.units, b.negative.n)} ROI</div>
  </div>
  <div class="${edgeCls(b.low)}">
    <div class="edge-card-label">${b.low.label}</div>
    <div class="edge-rate">${winPct(b.low)}</div>
    <div class="edge-n">${b.low.n} games</div>
    <div class="edge-desc">${b.low.desc}</div>
    <div class="edge-badge ${roiCls(b.low.units) === 'edge-pos' ? 'badge-green' : roiCls(b.low.units) === 'edge-neg' ? 'badge-red' : 'badge-amber'}">${roiStr(b.low.units, b.low.n)} ROI</div>
  </div>
  <div class="${edgeCls(b.mid)}">
    <div class="edge-card-label">${b.mid.label}</div>
    <div class="edge-rate">${winPct(b.mid)}</div>
    <div class="edge-n">${b.mid.n} games</div>
    <div class="edge-desc">${b.mid.desc}</div>
    <div class="edge-badge ${roiCls(b.mid.units) === 'edge-pos' ? 'badge-green' : roiCls(b.mid.units) === 'edge-neg' ? 'badge-red' : 'badge-amber'}">${roiStr(b.mid.units, b.mid.n)} ROI</div>
  </div>
  <div class="${edgeCls(b.high)}">
    <div class="edge-card-label">${b.high.label}</div>
    <div class="edge-rate">${winPct(b.high)}</div>
    <div class="edge-n">${b.high.n} games</div>
    <div class="edge-desc">${b.high.desc}</div>
    <div class="edge-badge ${roiCls(b.high.units) === 'edge-pos' ? 'badge-green' : roiCls(b.high.units) === 'edge-neg' ? 'badge-red' : 'badge-amber'}">${roiStr(b.high.units, b.high.n)} ROI</div>
  </div>
</div>

<div class="section-subheading" style="margin-top:16px;">Totals Direction</div>
<div class="rec-totals-grid">
  <div class="${totalsCls(t.over)}">
    <div class="edge-card-label">${t.over.label}</div>
    <div class="edge-rate">${hitPct(t.over)}</div>
    <div class="edge-n">${t.over.n} games</div>
    <div class="edge-desc">Model predicted &gt; Vegas total</div>
    <div class="edge-badge ${roiCls(t.over.units) === 'edge-pos' ? 'badge-green' : roiCls(t.over.units) === 'edge-neg' ? 'badge-red' : 'badge-amber'}">${roiStr(t.over.units, t.over.n)} ROI</div>
  </div>
  <div class="${totalsCls(t.under)}">
    <div class="edge-card-label">${t.under.label}</div>
    <div class="edge-rate">${hitPct(t.under)}</div>
    <div class="edge-n">${t.under.n} games</div>
    <div class="edge-desc">Model predicted &lt; Vegas total</div>
    <div class="edge-badge ${roiCls(t.under.units) === 'edge-pos' ? 'badge-green' : roiCls(t.under.units) === 'edge-neg' ? 'badge-red' : 'badge-amber'}">${roiStr(t.under.units, t.under.n)} ROI</div>
  </div>
  <div class="edge-card">
    <div class="edge-card-label">${t.push.label}</div>
    <div class="edge-rate">${t.push.n}</div>
    <div class="edge-n">games</div>
    <div class="edge-desc">Model within 0.5 of Vegas line</div>
    <div class="edge-badge badge-amber">No bet</div>
  </div>
</div>

<div class="section-subheading" style="margin-top:16px;">Simulated ROI (flat $1 bets)</div>
<div class="rec-roi-grid">
  <div class="roi-card">
    <div class="roi-label">Moneyline (${ml.totalBets} bets)</div>
    <div class="roi-val ${ml.totalUnits >= 0 ? 'roi-pos' : 'roi-neg'}">${mlSign}${ml.totalUnits.toFixed(2)} u</div>
    <div class="roi-sub">${mlRoi} ROI</div>
  </div>
  <div class="roi-card">
    <div class="roi-label">Totals (${totals.totalBets} bets)</div>
    <div class="roi-val ${totals.totalUnits >= 0 ? 'roi-pos' : 'roi-neg'}">${totSign}${totals.totalUnits.toFixed(2)} u</div>
    <div class="roi-sub">${totRoi} ROI</div>
  </div>
</div>`;
}

// ── Formatting helpers ────────────────────────────────────────────────────────
function formatDateLabel(dateStr) {
  const d = new Date(dateStr + 'T12:00:00');
  return d.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
}

function formatGeneratedAt(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('en-US', {
      month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit',
      timeZone: 'America/New_York', timeZoneName: 'short',
    });
  } catch { return ''; }
}

function formatTimeET(utcStr) {
  if (!utcStr) return '';
  try {
    const d = new Date(utcStr);
    return d.toLocaleTimeString('en-US', {
      hour: 'numeric', minute: '2-digit',
      timeZone: 'America/New_York', timeZoneName: 'short',
    });
  } catch { return ''; }
}

function formatOddsLine(odds, awayTeam, homeTeam) {
  const parts = [];
  if (odds.away_ml != null) {
    const sign = odds.away_ml > 0 ? '+' : '';
    parts.push(`${abbrev(awayTeam)} ${sign}${odds.away_ml}`);
  }
  if (odds.home_ml != null) {
    const sign = odds.home_ml > 0 ? '+' : '';
    parts.push(`${abbrev(homeTeam)} ${sign}${odds.home_ml}`);
  }
  if (odds.total != null) {
    parts.push(`O/U ${odds.total}`);
  }
  return parts.join(' · ');
}

function lineMovementHTML(g) {
  const mv = g.odds?.line_movement;
  if (!mv) return '';
  const parts = [];
  if (mv.total_move != null) {
    const dir = mv.total_move > 0 ? '▲' : '▼';
    const side = mv.total_move > 0 ? 'OVER' : 'UNDER';
    const sign = mv.total_move > 0 ? '+' : '';
    parts.push(`${dir} Line ${sign}${mv.total_move} · Sharp ${side}`);
  }
  if (mv.ml_move != null) {
    const side = mv.ml_move > 0 ? abbrev(g.home_team) : abbrev(g.away_team);
    const dir = '▲';
    parts.push(`${dir} ${side} ML sharp action`);
  }
  if (!parts.length) return '';
  return `<div class="sharp-badge">${parts.join(' &nbsp;·&nbsp; ')}</div>`;
}

function formatWeather(wx) {
  if (!wx) return '';
  if (wx.condition === 'Dome') return 'Dome';
  const parts = [];
  if (wx.temp_f != null) parts.push(`${wx.temp_f}°F`);
  if (wx.wind_mph != null && wx.wind_mph > 0) {
    const dir = wx.blowing_out === true ? 'Out' : wx.blowing_out === false ? 'In' : '';
    parts.push(`${wx.wind_mph} mph${dir ? ' ' + dir : ''}`);
  }
  return parts.join(' · ');
}

function fmtStatVal(val, label) {
  if (val == null) return dash();
  if (label === 'xERA' || label === 'RV/100') return val.toFixed(2);
  if (label === 'xBA Against') return fmtWoba(val);
  if (label.endsWith('%')) return fmtPct(val);
  return String(val);
}

function fmtWoba(v) {
  return '.' + Math.round(v * 1000).toString().padStart(3, '0');
}

function wobaClass(b) {
  if (b.woba == null || b.xwoba == null) return '';
  const gap = b.woba - b.xwoba;
  if (gap >= 0.025)  return 'woba-over';
  if (gap <= -0.025) return 'woba-under';
  return '';
}

function fmtPct(v) {
  return (v * 100).toFixed(1) + '%';
}

function dash() {
  return '<span class="dash">—</span>';
}


function teamRecordHTML(rec) {
  if (!rec) return '';
  const isWin = rec.streak && rec.streak.startsWith('W');
  return `
<div class="team-record-stack">
  <span class="rec-overall">${rec.wins}-${rec.losses}</span>
  ${rec.l10_w != null ? `<span class="rec-l10">${rec.l10_w}-${rec.l10_l} L10</span>` : ''}
  ${rec.streak ? `<span class="rec-streak ${isWin ? 'rec-win' : 'rec-loss'}">${rec.streak}</span>` : ''}
</div>`;
}

function teamLogoHTML(teamName) {
  const abbrev = TEAM_LOGO[teamName];
  if (!abbrev) return '';
  const url = `https://a.espncdn.com/i/teamlogos/mlb/500/${abbrev}.png`;
  return `<img class="team-logo" src="${url}" alt="" width="44" height="44" loading="lazy" onerror="this.style.display='none'">`;
}

function shortName(name) {
  if (!name) return '—';
  const comma = name.indexOf(',');
  if (comma !== -1) {
    const last  = name.slice(0, comma).trim();
    const first = name.slice(comma + 1).trim();
    return first ? `${last}, ${first[0]}.` : last;
  }
  // Fallback for "First Last" format
  const parts = name.split(' ');
  return parts.length >= 2 ? `${parts[parts.length - 1]}, ${parts[0][0]}.` : name;
}

// ── Backtest view ─────────────────────────────────────────────────────────────

function renderBacktestView() {
  const el = document.getElementById('backtest-view');

  if (!backtestData) {
    el.innerHTML = `<div class="empty-state"><p>Backtest data not available yet. Run the pipeline to generate it.</p></div>`;
    return;
  }

  const { stats, games = [], ev_stats, roi_stats } = backtestData;
  if (!stats) {
    el.innerHTML = `<div class="empty-state"><p>No backtest stats found in data.</p></div>`;
    return;
  }

  const pct = v => v != null ? (v * 100).toFixed(1) + '%' : '—';
  const signedPct = v => v != null ? (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%' : '—';

  // ── Hero strip ────────────────────────────────────────────────────────────
  const hasVegas = ev_stats && ev_stats.n_with_lines > 0;
  const vegasN = ev_stats?.n_with_lines;
  const vegasNStr = vegasN != null ? vegasN.toLocaleString() : '—';
  const brierModel = ev_stats?.brier_score != null ? ev_stats.brier_score.toFixed(4) : '—';
  const avgEdge = ev_stats?.ml_edge_mean != null ? signedPct(ev_stats.ml_edge_mean) : '—';
  const avgEdgeCls = (ev_stats?.ml_edge_mean ?? 0) >= 0 ? 'hero-green' : 'hero-red';
  const totalDecided = (stats.total_decided || stats.total || 0).toLocaleString();

  const heroCards = [
    { val: vegasNStr,                        label: 'Games w/ Vegas Lines',    sub: null },
    { val: pct(stats.win_pct_overall),       label: 'Model Win Rate',          sub: null },
    { val: brierModel,                       label: 'Model Brier Score',       sub: 'Vegas baseline: 0.2229' },
    { val: avgEdge,                          label: 'Avg Model Edge vs Vegas', sub: null, cls: avgEdgeCls },
    { val: totalDecided,                     label: 'Total Games Graded',      sub: null },
  ].map(c => `
    <div class="bt-hero-card">
      <div class="bt-hero-val${c.cls ? ' ' + c.cls : ''}">${c.val}</div>
      <div class="bt-hero-label">${c.label}</div>
      ${c.sub ? `<div class="bt-hero-sub">${c.sub}</div>` : ''}
    </div>`).join('');

  // ── Vegas Edge Analysis ───────────────────────────────────────────────────
  let edgeSection = '';
  if (hasVegas && ev_stats.by_edge_bucket) {
    const eb = ev_stats.by_edge_bucket;
    const buckets = [
      { key: 'negative',  label: 'Model Picks Away',    desc: 'Model rates home below Vegas — away team pick', cls: 'edge-green', badge: 'badge-green', badgeText: 'Best Zone' },
      { key: '0_to_3pct', label: '0–3% Home Edge',      desc: 'Slight model advantage on home team',           cls: 'edge-amber', badge: 'badge-amber', badgeText: 'Marginal' },
      { key: '3_to_6pct', label: '3–6% Home Edge',      desc: 'Moderate model advantage on home team',         cls: 'edge-amber', badge: 'badge-amber', badgeText: 'Marginal' },
      { key: '6pct_plus', label: '6%+ Home Edge',       desc: 'Large model confidence — historically wrong',   cls: 'edge-red',   badge: 'badge-red',   badgeText: 'Caution' },
    ];
    const cards = buckets.map(b => {
      const d = eb[b.key] || {};
      const wr = d.win_rate != null ? (d.win_rate * 100).toFixed(1) + '%' : '—';
      const n = d.n != null ? d.n.toLocaleString() : '—';
      return `<div class="edge-card ${b.cls}">
        <div class="edge-card-label">${b.label}</div>
        <div class="edge-rate">${wr}</div>
        <div class="edge-n">${n} games</div>
        <div class="edge-desc">${b.desc}</div>
        <span class="edge-badge ${b.badge}">${b.badgeText}</span>
      </div>`;
    }).join('');
    edgeSection = `
      <div class="bt-section-title">Vegas Edge Analysis <span class="bt-count">(pick win rate by model edge vs closing line)</span></div>
      <div class="edge-bucket-grid">${cards}</div>`;
  }

  // ── Calibration curve ─────────────────────────────────────────────────────
  let calSection = '';
  if (hasVegas && ev_stats.calibration_curve?.length) {
    const calRows = ev_stats.calibration_curve.map(row => {
      const delta = row.actual_win_rate != null && row.model_prob_mean != null
        ? row.actual_win_rate - row.model_prob_mean : null;
      const deltaTxt = delta != null ? (delta >= 0 ? '+' : '') + (delta * 100).toFixed(2) + '%' : '—';
      const deltaCls = delta == null ? '' : delta >= 0 ? 'delta-pos' : 'delta-neg';
      return `<tr>
        <td>${row.bin ?? '—'}</td>
        <td>${row.n?.toLocaleString() ?? '—'}</td>
        <td>${row.model_prob_mean != null ? (row.model_prob_mean * 100).toFixed(1) + '%' : '—'}</td>
        <td>${row.actual_win_rate != null ? (row.actual_win_rate * 100).toFixed(1) + '%' : '—'}</td>
        <td class="${deltaCls}">${deltaTxt}</td>
      </tr>`;
    }).join('');
    calSection = `
      <div class="bt-section-title">Model Calibration — Predicted vs. Actual <span class="bt-count">(${vegasNStr} games with Vegas lines)</span></div>
      <div class="bt-table-wrap" style="margin-bottom:16px">
        <table class="bt-cal-table">
          <thead><tr><th>Prob Bin</th><th>Games</th><th>Model Avg</th><th>Actual Win Rate</th><th>Delta</th></tr></thead>
          <tbody>${calRows}</tbody>
        </table>
      </div>`;
  }

  // ── Confidence tier table ─────────────────────────────────────────────────
  const tierLabels = { '50_55': '50–55%', '55_60': '55–60%', '60_65': '60–65%', '65_plus': '65%+' };
  const confRows = Object.entries(stats.win_pct_by_confidence || {}).map(([key, t]) => {
    const base = t.pct != null ? t.pct - 0.5 : null;
    const baseTxt = base != null ? (base >= 0 ? '+' : '') + (base * 100).toFixed(1) + '%' : '—';
    const baseCls = base == null ? '' : base >= 0 ? 'baseline-pos' : 'baseline-neg';
    const pctTxt = t.pct != null ? (t.pct * 100).toFixed(1) + '%' : '—';
    const pctCls = (t.pct ?? 0) >= 0.55 ? 'tier-pct tier-pct-good' : (t.pct ?? 0) >= 0.5 ? 'tier-pct tier-pct-ok' : 'tier-pct tier-pct-bad';
    return `<tr>
      <td><strong>${tierLabels[key] || key}</strong></td>
      <td>${t.total ?? '—'}</td>
      <td>${t.correct ?? '—'}</td>
      <td class="${pctCls}">${pctTxt}</td>
      <td class="${baseCls}">${baseTxt}</td>
    </tr>`;
  }).join('');

  // ── Signal accuracy ───────────────────────────────────────────────────────
  const sigAcc = stats.signal_accuracy || {};
  const pit = sigAcc.pitcher    || {};
  const cmp = sigAcc.comps      || {};
  const tot = sigAcc.totals_dir || {};

  // ── Game log ──────────────────────────────────────────────────────────────
  const rows = games.slice(0, 200).map(g => {
    const winnerTeam = g.predicted_winner === 'home' ? g.home_team : g.away_team;
    const actualTeam = g.actual_winner === 'home' ? g.home_team : g.away_team;
    const conf = Math.round(Math.max(g.home_win_pct, g.away_win_pct) * 100);
    const rowClass = g.correct ? 'row-hit' : g.actual_winner === 'tie' ? '' : 'row-miss';
    const icon = g.actual_winner === 'tie' ? '—' : (g.correct ? '✓' : '✗');
    const dateFmt = g.date ? g.date.slice(5).replace('-', '/') : '—';
    const edgeVal = g.model_edge_ml != null ? g.model_edge_ml : null;
    const edgeTxt = edgeVal != null ? (edgeVal >= 0 ? '+' : '') + (edgeVal * 100).toFixed(1) + '%' : '—';
    const edgeCls = edgeVal == null ? '' : edgeVal >= 0 ? 'edge-pos' : 'edge-neg';
    return `<tr class="${rowClass}">
      <td class="bt-season">${g.season ?? '—'}</td>
      <td class="bt-date">${dateFmt}</td>
      <td class="bt-matchup">${abbrev(g.away_team)} @ ${abbrev(g.home_team)}</td>
      <td class="bt-pred">${abbrev(winnerTeam)} <span class="bt-conf">${conf}%</span></td>
      <td class="bt-actual">${abbrev(actualTeam)} <span class="bt-score">${g.away_score}–${g.home_score}</span></td>
      <td class="bt-edge ${edgeCls}">${edgeTxt}</td>
      <td class="bt-icon ${g.correct ? 'icon-correct' : (g.actual_winner === 'tie' ? '' : 'icon-wrong')}">${icon}</td>
    </tr>`;
  }).join('');

  // ── ROI section ───────────────────────────────────────────────────────────
  const roiHtml = (() => {
    const roi = roi_stats;
    if (!roi || (!roi.ml_bets && !roi.total_bets)) return '';
    const fmtRoi = v => v != null ? (v >= 0 ? '+' : '') + v.toFixed(2) + '%' : '—';
    const fmtUnits = v => v != null ? (v >= 0 ? '+' : '') + v.toFixed(2) : '—';
    const roiCls = v => v == null ? '' : v >= 0 ? 'roi-pos' : 'roi-neg';
    return `
      <div class="bt-section-title">ROI — Live Lines <span class="bt-count">(from records with Pinnacle lines stored)</span></div>
      <div class="roi-grid">
        <div class="roi-card">
          <span class="roi-label">Moneyline ROI</span>
          <span class="roi-val ${roiCls(roi.ml_roi_pct)}">${fmtRoi(roi.ml_roi_pct)}</span>
          <span class="roi-sub">${fmtUnits(roi.ml_units_won)} units · ${roi.ml_bets ?? 0} bets</span>
        </div>
        <div class="roi-card">
          <span class="roi-label">Totals ROI</span>
          <span class="roi-val ${roiCls(roi.total_roi_pct)}">${fmtRoi(roi.total_roi_pct)}</span>
          <span class="roi-sub">${fmtUnits(roi.total_units_won)} units · ${roi.total_bets ?? 0} bets</span>
        </div>
      </div>`;
  })();

  el.innerHTML = `
    <div class="backtest-wrap">

      <div class="bt-hero-grid">${heroCards}</div>

      ${edgeSection}

      ${calSection}

      <div class="bt-section-title">Win % by Confidence Tier</div>
      <div class="bt-table-wrap" style="margin-bottom:16px">
        <table class="bt-conf-table">
          <thead><tr><th>Confidence</th><th>Games</th><th>Correct</th><th>Win Rate</th><th>vs. Baseline</th></tr></thead>
          <tbody>${confRows}</tbody>
        </table>
      </div>

      <div class="bt-section-title">Signal Accuracy</div>
      <div class="signal-accuracy-grid">
        <div class="sig-acc-card">
          <span class="sig-acc-label">Pitcher Edge</span>
          <span class="sig-acc-pct">${pct(pit.pct)}</span>
          <span class="sig-acc-sub">${pit.correct ?? 0}/${pit.total ?? 0} games</span>
        </div>
        <div class="sig-acc-card">
          <span class="sig-acc-label">Comps Match</span>
          <span class="sig-acc-pct">${pct(cmp.pct)}</span>
          <span class="sig-acc-sub">${cmp.correct ?? 0}/${cmp.total ?? 0} games</span>
        </div>
        <div class="sig-acc-card">
          <span class="sig-acc-label">Totals Direction</span>
          <span class="sig-acc-pct">${pct(tot.pct)}</span>
          <span class="sig-acc-sub">${tot.correct ?? 0}/${tot.total ?? 0} games</span>
        </div>
      </div>

      ${roiHtml}

      <div class="bt-section-title">Game Log <span class="bt-count">(${games.length} games, most recent first)</span></div>
      <div class="bt-table-wrap">
        <table class="bt-table">
          <thead>
            <tr><th>Season</th><th>Date</th><th>Matchup</th><th>Predicted</th><th>Actual</th><th>Edge</th><th></th></tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>

    </div>`;
}

function escapeHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function abbrev(teamName) {
  if (!teamName) return '?';
  const map = {
    'Arizona Diamondbacks': 'ARI', 'Atlanta Braves': 'ATL', 'Baltimore Orioles': 'BAL',
    'Boston Red Sox': 'BOS', 'Chicago Cubs': 'CHC', 'Chicago White Sox': 'CWS',
    'Cincinnati Reds': 'CIN', 'Cleveland Guardians': 'CLE', 'Colorado Rockies': 'COL',
    'Detroit Tigers': 'DET', 'Houston Astros': 'HOU', 'Kansas City Royals': 'KC',
    'Los Angeles Angels': 'LAA', 'Los Angeles Dodgers': 'LAD', 'Miami Marlins': 'MIA',
    'Milwaukee Brewers': 'MIL', 'Minnesota Twins': 'MIN', 'New York Mets': 'NYM',
    'New York Yankees': 'NYY', 'Athletics': 'OAK', 'Oakland Athletics': 'OAK',
    'Philadelphia Phillies': 'PHI', 'Pittsburgh Pirates': 'PIT', 'San Diego Padres': 'SD',
    'San Francisco Giants': 'SF', 'Seattle Mariners': 'SEA', 'St. Louis Cardinals': 'STL',
    'Tampa Bay Rays': 'TB', 'Texas Rangers': 'TEX', 'Toronto Blue Jays': 'TOR',
    'Washington Nationals': 'WSH',
  };
  return map[teamName] || teamName.split(' ').pop().slice(0, 3).toUpperCase();
}


// ── Props tab ─────────────────────────────────────────────────────────────────

const BET_META = {
  K_PROP:     { label: 'K',     color: '#7c3aed' },
  HR_PROP:    { label: 'HR',    color: '#e11d48' },
  HIT_PROP:   { label: 'HIT',   color: '#0284c7' },
  TB_PROP:    { label: 'TB',    color: '#0891b2' },
  TOTAL:      { label: 'TOT',   color: '#059669' },
  TEAM_TOTAL: { label: 'T-TOT', color: '#047857' },
  MONEYLINE:  { label: 'ML',    color: '#b45309' },
  ML_F5:      { label: 'F5',    color: '#92400e' },
};

function renderPropsView() {
  const view = document.getElementById('props-view');
  if (!picksData || !picksData.games || !picksData.games.length) {
    view.innerHTML = `
<div class="view-header">
  <h1>Props</h1>
  <span class="sub-label">Player & game props — signal-driven picks</span>
</div>
<div class="empty-state">No props available — pipeline generates picks after each run.</div>`;
    return;
  }

  const ts = picksData.generated_at
    ? `Updated ${formatGeneratedAt(picksData.generated_at)}`
    : '';

  const tabs = [
    { id: 'all',      label: 'All Picks' },
    { id: 'highconf', label: 'High Confidence' },
    { id: 'value',    label: 'Value (Edge ≥3%)' },
  ];
  const tabsHtml = tabs.map(t =>
    `<button class="pf-tab${propsFilter === t.id ? ' active' : ''}" data-filter="${t.id}">${t.label}</button>`
  ).join('');

  const filteredCards = picksData.games
    .map(g => renderPickGameCard(g))
    .filter(html => html.trim());

  view.innerHTML = `
<div class="view-header">
  <h1>Props</h1>
  <span class="sub-label">${ts}</span>
</div>
<div class="props-filter-row">${tabsHtml}</div>
<div class="picks-list">
  ${filteredCards.length ? filteredCards.join('') : '<div class="empty-state">No picks match the current filter.</div>'}
</div>`;

  view.querySelectorAll('.pf-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      propsFilter = btn.dataset.filter;
      renderPropsView();
    });
  });
}

function filterPick(p) {
  if (propsFilter === 'highconf') return (p.signal ?? 0) >= 7.0;
  if (propsFilter === 'value')    return (p.odds?.edge_pct ?? 0) >= 3;
  return true;
}

function renderPickGameCard(g) {
  const timeStr = g.game_time ? formatTimeET(g.game_time) : '';
  const matchup = `${abbrev(g.away_team)} @ ${abbrev(g.home_team)}`;

  // Group picks by type, applying current filter
  const typeOrder = ['K_PROP','HR_PROP','HIT_PROP','TB_PROP','TOTAL','TEAM_TOTAL','MONEYLINE','ML_F5'];
  const grouped = {};
  for (const p of g.picks.filter(filterPick)) {
    (grouped[p.bet_type] = grouped[p.bet_type] || []).push(p);
  }
  if (!Object.keys(grouped).length) return '';

  const sections = typeOrder
    .filter(t => grouped[t])
    .map(t => {
      const meta = BET_META[t] || { label: t, color: '#6b7280' };
      const count = grouped[t].length;
      return `
<div class="prop-type-group">
  <div class="prop-type-header">
    <span class="bet-badge" style="--bet-color:${meta.color}">${meta.label}</span>
    <span class="prop-type-label">${betTypeLabel(t)}</span>
    <span class="prop-group-count">${count} pick${count !== 1 ? 's' : ''}</span>
  </div>
  ${grouped[t].map(p => renderPick(p)).join('')}
</div>`;
    }).join('');

  return `
<div class="pick-game-card">
  <div class="pick-game-header">
    <span class="pick-matchup">${matchup}</span>
    <span class="pick-time">${timeStr}</span>
    <span class="pick-venue">${g.venue || ''}</span>
  </div>
  ${sections}
</div>`;
}

function betTypeLabel(t) {
  const labels = {
    K_PROP: 'Strikeouts', HR_PROP: 'Home Runs', HIT_PROP: 'Hits',
    TB_PROP: 'Total Bases', TOTAL: 'Game Total', TEAM_TOTAL: 'Team Totals',
    MONEYLINE: 'Moneyline', ML_F5: 'First 5 Innings',
  };
  return labels[t] || t;
}

function renderPick(p) {
  const meta    = BET_META[p.bet_type] || { label: '?', color: '#6b7280' };
  const signal  = p.signal ?? 0;
  const sigW    = Math.round((signal / 10) * 100);
  const sigCls  = signal >= 7.5 ? 'sig-hi' : signal >= 6.0 ? 'sig-mid' : 'sig-lo';
  const dirCls  = p.direction === 'OVER' ? 'dir-over' : 'dir-under';

  const isTotal   = p.bet_type === 'TOTAL' || p.bet_type === 'TEAM_TOTAL';
  const noLineup  = isTotal && p.raw_scores && p.raw_scores.lineup_data === false;

  let consensusBadge = '';
  if (p.bet_type === 'ML_F5' && p.consensus_tag) {
    if (p.consensus_tag === 'CONTRARIAN') {
      consensusBadge = '<span class="consensus-badge contrarian">CONTRARIAN</span>';
    } else if (p.consensus_tag === 'CONFIRMS_MARKET') {
      consensusBadge = '<span class="consensus-badge confirms-market">CONFIRMS MARKET</span>';
    }
  }

  const reasonsHtml = (p.reasons || []).map(r =>
    `<li class="pick-reason">${escapeHtml(r)}</li>`
  ).join('');

  const statsHtml  = renderPickStatsRow(p);
  const last5Html  = renderLast5Row(p);
  const oddsHtml   = renderPickOdds(p);

  return `
<div class="pick-card" style="--bet-color:${meta.color}">
  <div class="pick-card-top">
    <div class="pick-subject-row">
      <span class="bet-badge" style="--bet-color:${meta.color}">${meta.label}</span>
      <span class="pick-subject">${escapeHtml(p.subject)}</span>
      <span class="pick-dir ${dirCls}">${p.direction}</span>
      ${noLineup ? '<span class="data-quality-badge">Pitcher-only signal</span>' : ''}
      ${consensusBadge}
    </div>
    <div class="pick-headline">${escapeHtml(p.headline)}</div>
    <div class="signal-bar-wrap">
      <div class="signal-bar-track">
        <div class="signal-bar-fill ${sigCls}" style="width:${sigW}%"></div>
      </div>
      <span class="signal-label">Signal ${signal.toFixed(1)}</span>
    </div>
  </div>
  ${oddsHtml}
  ${statsHtml}
  ${last5Html}
  ${reasonsHtml ? `<ul class="pick-reasons">${reasonsHtml}</ul>` : ''}
</div>`;
}

function renderPickOdds(p) {
  const o = p.odds;
  if (!o || !o.has_line) return '';
  const edgeCls = (o.edge_pct >= 0.03) ? 'edge-pos' : (o.edge_pct <= -0.03) ? 'edge-neg' : 'edge-neu';
  const price   = p.direction === 'OVER' ? o.over_price : o.under_price;
  const edgePct = o.edge_pct != null ? `${(o.edge_pct * 100).toFixed(1)}%` : '—';
  return `
<div class="pick-odds-row">
  <span class="odds-line">Line: ${o.line}</span>
  <span class="odds-price">${price > 0 ? '+' : ''}${price}</span>
  <span class="edge-badge ${edgeCls}">Edge ${edgePct}</span>
</div>`;
}

function renderPickStatsRow(p) {
  const rs = p.raw_scores || {};
  const chips = [];

  if (p.bet_type === 'K_PROP') {
    if (rs.sp_k_pct    != null) chips.push(['K%',      rs.sp_k_pct]);
    if (rs.whiff_pct   != null) chips.push(['Whiff',   rs.whiff_pct]);
    if (rs.stuff_plus  != null) chips.push(['Stuff+',  rs.stuff_plus]);
    if (rs.o_swing_pct != null) chips.push(['Chase',   rs.o_swing_pct]);
    if (rs.opp_k_pct   != null) chips.push(['OppK%',   rs.opp_k_pct]);
  } else if (p.bet_type === 'HR_PROP' || p.bet_type === 'HIT_PROP' || p.bet_type === 'TB_PROP') {
    if (rs.xwoba       != null) chips.push(['xwOBA',   rs.xwoba]);
    if (rs.hard_hit_pct!= null) chips.push(['HH%',     rs.hard_hit_pct]);
    if (rs.barrel_pct  != null) chips.push(['Brl%',    rs.barrel_pct]);
    if (rs.bb_pct      != null) chips.push(['BB%',     rs.bb_pct]);
    if (rs.k_pct       != null) chips.push(['K%',      rs.k_pct]);
    const edge = rs.edge_score;
    if (edge != null) {
      const ecls = edge >= 70 ? 'edge-hi' : edge >= 45 ? 'edge-mid' : 'edge-lo';
      return `<div class="stat-pills-row">${chips.map(([l,v]) => `<span class="stat-pill">${l} ${v}</span>`).join('')}<span class="edge-score-badge ${ecls}">Edge ${edge}</span></div>`;
    }
  } else {
    if (rs.avg_lineup_xwoba   != null) chips.push(['xwOBA', rs.avg_lineup_xwoba]);
    if (rs.home_sp_xfip        != null) chips.push(['H-xFIP', rs.home_sp_xfip]);
    if (rs.away_sp_xfip        != null) chips.push(['A-xFIP', rs.away_sp_xfip]);
    if (rs.park_run_factor     != null) chips.push(['Park', rs.park_run_factor]);
    if (rs.lineup_xwoba        != null) chips.push(['xwOBA', rs.lineup_xwoba]);
    if (rs.sp_xfip             != null) chips.push(['xFIP', rs.sp_xfip]);
  }

  if (!chips.length) return '';
  return `<div class="stat-pills-row">${chips.map(([l,v]) => `<span class="stat-pill">${l} ${v}</span>`).join('')}</div>`;
}

function renderLast5Row(p) {
  const rs = p.raw_scores || {};

  if (p.bet_type === 'HR_PROP' && rs.recent_hr_games) {
    const cells = rs.recent_hr_games.map(n => {
      const cls = n >= 2 ? 'hr-multi' : n === 1 ? 'hr-hit' : 'hr-miss';
      return `<span class="last5-cell ${cls}">${n >= 1 ? n : '○'}</span>`;
    }).join('');
    return `<div class="last5-row"><span class="last5-label">Last 5</span>${cells}</div>`;
  }

  if ((p.bet_type === 'HIT_PROP' || p.bet_type === 'TB_PROP') && rs.recent_h_games) {
    const cells = rs.recent_h_games.map(n => {
      const cls = n >= 2 ? 'h-multi' : n === 1 ? 'h-hit' : 'h-miss';
      return `<span class="last5-cell ${cls}">${n >= 1 ? n : '—'}</span>`;
    }).join('');
    return `<div class="last5-row"><span class="last5-label">Last 5</span>${cells}</div>`;
  }

  if (p.bet_type === 'K_PROP' && rs.recent_k_games) {
    const cells = rs.recent_k_games.map(n => {
      const cls = n >= 8 ? 'k-hot' : n >= 5 ? 'k-mid' : 'k-cold';
      return `<span class="last5-cell ${cls}">${n}K</span>`;
    }).join('');
    return `<div class="last5-row"><span class="last5-label">Last ${rs.recent_k_games.length}</span>${cells}</div>`;
  }

  return '';
}
