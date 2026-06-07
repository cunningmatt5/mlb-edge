'use strict';

// ── Data sources ─────────────────────────────────────────────────────────────
const GAMES_URL    = './games.json';
const HISTORY_URL  = './history.json';
const BACKTEST_URL      = './backtest.json';
const PICKS_URL         = './picks.json';
const PROPS_HIST_URL    = './props_history.json';
const PITCHER_VALUE_URL = './pitcher_value.json';

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
let historyData   = [];
let backtestData  = null;
let picksData     = null;
let propsHistData = null;
let pitcherData   = null;
let expandedPk   = null;
let currentView  = 'games';
let lastCheckedAt = null;
let propsFilter  = 'all';   // 'all' | 'highconf' | 'value'
let parlayParams = { legs: 3, risk: 'medium', include: ['all'], allowCorr: true, sgp: false, sgpGamePk: null, locked: [] };

// player MLBAM id → full team name; rebuilt whenever gamesData loads
let _playerTeamMap = new Map();

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
      btn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
      document.getElementById('games-view').hidden    = currentView !== 'games';
      document.getElementById('props-view').hidden    = currentView !== 'props';
      document.getElementById('parlay-view').hidden   = currentView !== 'parlay';
      document.getElementById('record-view').hidden   = currentView !== 'record';
      document.getElementById('backtest-view').hidden = currentView !== 'backtest';
      document.getElementById('pitcher-view').hidden  = currentView !== 'pitcher';
      document.getElementById('simulate-view').hidden = currentView !== 'simulate';
      document.getElementById('support-view').hidden  = currentView !== 'support';
      document.getElementById('edges-view').hidden    = currentView !== 'edges';
      if (currentView === 'record')   Promise.all([loadBacktest(), loadPropsHistory()]).then(renderRecordView);
      if (currentView === 'backtest') Promise.all([loadBacktest(), loadPropsHistory()]).then(renderBacktestView);
      if (currentView === 'pitcher')  Promise.all([loadPitcherData(), loadGames()]).then(renderPitcherView);
      if (currentView === 'props')    loadPicks().then(renderPropsView);
      if (currentView === 'parlay')   loadPicks().then(renderParlayView);
      if (currentView === 'simulate') loadGames().then(renderSimulateView);
      if (currentView === 'support')  renderSupportView();
      if (currentView === 'edges')    loadGames().then(renderEdgesView);
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
  _buildPlayerTeamMap();
}

function _buildPlayerTeamMap() {
  _playerTeamMap = new Map();
  for (const g of (gamesData?.games || [])) {
    if (g.home_sp_id) _playerTeamMap.set(g.home_sp_id, g.home_team);
    if (g.away_sp_id) _playerTeamMap.set(g.away_sp_id, g.away_team);
    for (const lp of (g.home_lineup || [])) { if (lp.mlbam_id) _playerTeamMap.set(lp.mlbam_id, g.home_team); }
    for (const lp of (g.away_lineup || [])) { if (lp.mlbam_id) _playerTeamMap.set(lp.mlbam_id, g.away_team); }
  }
}

function _teamBadge(teamFull) {
  if (!teamFull) return '';
  const slug = TEAM_LOGO[teamFull];
  const abbr = abbrev(teamFull);
  const img  = slug
    ? `<img src="https://a.espncdn.com/combiner/i?img=/i/teamlogos/mlb/500/${slug}.png&h=32&w=32" width="14" height="14" alt="${abbr}" class="pick-team-logo" onerror="this.style.display='none'">`
    : '';
  return `<span class="pick-team-badge">${img}<span class="pick-team-abbr">${abbr}</span></span>`;
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

async function loadPropsHistory() {
  try {
    const r = await fetch(PROPS_HIST_URL + '?v=' + Date.now());
    if (r.ok) propsHistData = await r.json();
  } catch {
    propsHistData = null;
  }
}

async function loadPitcherData() {
  if (pitcherData) return;
  try {
    const r = await fetch(PITCHER_VALUE_URL + '?v=' + Date.now());
    if (r.ok) pitcherData = await r.json();
  } catch {
    pitcherData = null;
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

// ── Best Bets section (Elite Away + High Confidence) ─────────────────────────
function renderBestBetsSection(games) {
  const previewGames = games.filter(g => (g.game_status || 'preview') === 'preview');

  const eliteGames = previewGames.filter(g => g.prediction?.pick_tier === 'elite_away');
  const hiConfGames = previewGames.filter(g => {
    if (g.prediction?.pick_tier === 'elite_away') return false;
    const hwp = g.prediction?.home_win_pct;
    if (hwp == null) return false;
    return Math.max(hwp, 1 - hwp) >= 0.62;
  });

  if (!eliteGames.length && !hiConfGames.length) return '';

  function spLine(sp1, name1, sp2, name2) {
    const x1 = sp1?.season?.xera;
    const x2 = sp2?.season?.xera;
    return (x1 != null && x2 != null)
      ? `${name1} xERA ${x1.toFixed(2)} vs ${name2} xERA ${x2.toFixed(2)}`
      : `${name1} vs ${name2}`;
  }

  const eliteCards = eliteGames.map(g => {
    const pred    = g.prediction || {};
    const sig     = pred.model_signals || {};
    const odds    = g.odds || {};
    const awayPct = Math.round((1 - (pred.home_win_pct ?? 0.5)) * 100);
    const edgePct = pred.model_edge_ml != null ? (+(-pred.model_edge_ml * 100).toFixed(1)) : null;
    const awayMl  = odds.away_ml != null ? (odds.away_ml > 0 ? `+${odds.away_ml}` : String(odds.away_ml)) : null;
    const sl      = spLine(g.away_sp, g.away_sp?.name || abbrev(g.away_team), g.home_sp, g.home_sp?.name || abbrev(g.home_team));
    const awyL  = (g.away_lineup || []).filter(b => b.xwoba != null);
    const hmL   = (g.home_lineup  || []).filter(b => b.xwoba != null);
    const awyX  = awyL.length >= 3 ? awyL.reduce((s, b) => s + b.xwoba, 0) / awyL.length : null;
    const hmX   = hmL.length  >= 3 ? hmL.reduce((s, b)  => s + b.xwoba, 0) / hmL.length  : null;
    const luLine = (awyX != null && hmX != null)
      ? `Away .${Math.round(awyX * 1000)} vs Home .${Math.round(hmX * 1000)} xwOBA`
      : null;
    const awyDev = sig.last_start_dev_away;
    let formNote = '';
    if (awyDev != null && Math.abs(awyDev) >= 1.0) {
      const awySPName = g.away_sp?.name ? g.away_sp.name.split(',')[0] : 'Away SP';
      formNote = awyDev > 0
        ? ` · ${awySPName} running cold`
        : ` · ${awySPName} running hot`;
    }
    const oddsQual = oddsQualityBadge(odds.away_ml);
    return `
<div class="ea-card" onclick="toggleCard(${g.gamePk})">
  <div class="ea-card-inner">
    <div class="ea-card-accent ea-accent-elite"></div>
    <div class="ea-card-body">
      <div class="ea-left">
        <div class="ea-matchup">
          <span class="ea-away-name">${abbrev(g.away_team)}</span>
          <span class="ea-at">@</span>
          <span class="ea-home-name">${abbrev(g.home_team)}</span>
        </div>
        <div class="ea-sp-line">${sl}${formNote}</div>
        ${luLine ? `<div class="ea-lu-line">${luLine}</div>` : ''}
      </div>
      <div class="ea-right">
        <div class="ea-bet-row">
          <span class="ea-bet-label">BET AWAY</span>
          ${awayMl ? `<span class="ea-ml">${awayMl}</span>` : ''}
        </div>
        <div class="ea-win-pct">${awayPct}% model win</div>
        <div class="ea-bottom-row">
          ${edgePct != null ? `<span class="ea-edge-pill">+${edgePct}% vs Vegas</span>` : ''}
          ${oddsQual}
        </div>
      </div>
    </div>
  </div>
</div>`;
  }).join('');

  const hiConfCards = hiConfGames.map(g => {
    const pred    = g.prediction || {};
    const odds    = g.odds || {};
    const hwp     = pred.home_win_pct ?? 0.5;
    const awayFav = (1 - hwp) > hwp;
    const favPct  = Math.round((awayFav ? 1 - hwp : hwp) * 100);
    const favMl   = awayFav ? odds.away_ml : odds.home_ml;
    const favMlStr = favMl != null ? (favMl > 0 ? `+${favMl}` : String(favMl)) : null;
    const favSp   = awayFav ? g.away_sp : g.home_sp;
    const dogSp   = awayFav ? g.home_sp : g.away_sp;
    const favName = awayFav ? g.away_team : g.home_team;
    const dogName = awayFav ? g.home_team : g.away_team;
    const sl      = spLine(favSp, favSp?.name || abbrev(favName), dogSp, dogSp?.name || abbrev(dogName));
    return `
<div class="ea-card bb-conf-card" onclick="toggleCard(${g.gamePk})">
  <div class="ea-card-inner">
    <div class="ea-card-accent ea-accent-conf"></div>
    <div class="ea-card-body">
      <div class="ea-left">
        <div class="ea-matchup">
          <span class="ea-away-name">${abbrev(g.away_team)}</span>
          <span class="ea-at">@</span>
          <span class="ea-home-name">${abbrev(g.home_team)}</span>
        </div>
        <div class="ea-sp-line">${sl}</div>
      </div>
      <div class="ea-right">
        <div class="ea-bet-row">
          <span class="ea-bet-label">BET ${awayFav ? 'AWAY' : 'HOME'}</span>
          ${favMlStr ? `<span class="ea-ml">${favMlStr}</span>` : ''}
        </div>
        <div class="ea-win-pct">${favPct}% model win</div>
      </div>
    </div>
  </div>
</div>`;
  }).join('');

  let eliteBlock = '', hiConfBlock = '';

  if (eliteGames.length) {
    eliteBlock = `
  <div class="bb-subsection">
    <div class="bb-sub-hdr">
      <div class="bb-sub-title-row">
        <span class="bb-sub-title">◆ Elite Away Signal</span>
        <span class="bb-badge bb-badge-elite">${eliteGames.length} game${eliteGames.length > 1 ? 's' : ''} today</span>
      </div>
      <div class="bb-sub-desc">Model &amp; away pitcher both lean strongly against Vegas · <strong>+9.2% ROI</strong> on 884 bets (2022–2026)</div>
    </div>
    <div class="ea-cards">${eliteCards}</div>
  </div>`;
  }

  if (hiConfGames.length) {
    hiConfBlock = `
  <div class="bb-subsection${eliteGames.length ? ' bb-subsection-sep' : ''}">
    <div class="bb-sub-hdr">
      <div class="bb-sub-title-row">
        <span class="bb-sub-title">High Confidence</span>
        <span class="bb-badge bb-badge-conf">${hiConfGames.length} game${hiConfGames.length > 1 ? 's' : ''} today</span>
      </div>
      <div class="bb-sub-desc">Model ≥62% confident · Pitcher + lineup signals align · <strong>68.0% win rate</strong> at 65%+ confidence (2022–2026)</div>
    </div>
    <div class="ea-cards">${hiConfCards}</div>
  </div>`;
  }

  const totalCount = eliteGames.length + hiConfGames.length;
  return `
<div class="bb-section">
  <div class="bb-section-hdr">
    <span class="bb-section-title">Today's Best Bets</span>
    <span class="bb-total-count">${totalCount} pick${totalCount !== 1 ? 's' : ''}</span>
  </div>
  ${eliteBlock}${hiConfBlock}
</div>`;
}

// ── Games view ────────────────────────────────────────────────────────────────
function renderGamesView() {
  const view = document.getElementById('games-view');
  if (!gamesData || !gamesData.games.length) {
    view.innerHTML = `<div class="empty-state">No games scheduled today.</div>`;
    return;
  }

  const label    = formatDateLabel(gamesData.date);
  const eliteHtml = renderBestBetsSection(gamesData.games);

  view.innerHTML = `
    <div class="view-header">
      <h1>Today's Games</h1>
      <span class="sub-label">${label} &nbsp;·&nbsp; ${gamesData.game_count} games</span>
    </div>
    ${eliteHtml}
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

function _toAmericanOdds(decOdds) {
  if (!decOdds || decOdds <= 1) return null;
  return decOdds >= 2
    ? Math.round((decOdds - 1) * 100)
    : Math.round(-100 / (decOdds - 1));
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

function keySignalsHTML(g) {
  const sig = g.prediction?.model_signals || {};
  const chips = [];

  // SP mismatch: show away xERA first when away is better, home first otherwise
  const hXera = g.home_sp?.season?.xera;
  const aXera = g.away_sp?.season?.xera;
  if (hXera != null && aXera != null && Math.abs(hXera - aXera) > 0.7) {
    const awayBetter = aXera < hXera;
    const label = awayBetter
      ? `${aXera.toFixed(2)} vs ${hXera.toFixed(2)} xERA`
      : `${hXera.toFixed(2)} vs ${aXera.toFixed(2)} xERA`;
    chips.push(`<span class="key-sig-chip sp">${label}</span>`);
  }

  // Weather: only when modifier is meaningful (captures wind and cold)
  const wm = sig.weather_modifier;
  if (wm != null && Math.abs(wm) >= 0.5) {
    const sign = wm > 0 ? '+' : '';
    const dir  = wm > 0 ? 'out' : 'in';
    chips.push(`<span class="key-sig-chip weather">Wind ${dir} ${sign}${wm.toFixed(1)}</span>`);
  }

  // Rest edge: one team ≥5 days rest and gap ≥2 days
  const hr = sig.home_rest_days, ar = sig.away_rest_days;
  if (hr != null && ar != null) {
    const gap = hr - ar;
    if (Math.abs(gap) >= 2 && (hr >= 5 || ar >= 5)) {
      const who = gap > 0 ? g.home_team : g.away_team;
      chips.push(`<span class="key-sig-chip rest">Rest: ${abbrev(who)} +${Math.abs(gap)}d</span>`);
    }
  }

  if (!chips.length) return '';
  return `<div class="key-sig-row">${chips.join('')}</div>`;
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
<div class="game-card" data-pk="${g.gamePk}" data-status="${status}" data-fav="${fav}" data-pick-tier="${g.prediction?.pick_tier || ''}">
  <div class="game-card-header">
    ${gameEdgeBannerHTML(g)}
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
    ${vegasEdgeStripHTML(g)}
    ${keySignalsHTML(g)}
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

// Returns a small "good/moderate/heavy-fav" badge for the away team odds.
// Near-even or plus-money = highest historical ROI for Elite Away picks.
function oddsQualityBadge(awayMl) {
  if (awayMl == null) return '';
  if (awayMl >= -130) return '<span class="odds-q odds-q-good">Good odds</span>';
  if (awayMl >= -180) return '<span class="odds-q odds-q-mod">Moderate odds</span>';
  return '<span class="odds-q odds-q-heavy">Heavy fav</span>';
}

// Compact 1-2 line reasoning panel for pick-tier games.
// Surfaces the xERA matchup, lineup xwOBA edge, and SP form signal.
function buildPickReasoning(g) {
  const pred = g.prediction || {};
  if (!pred.pick_tier) return '';
  const sig   = pred.model_signals || {};
  const awySp = g.away_sp || {};
  const hmSp  = g.home_sp || {};

  const awyXera = awySp.season?.xera;
  const hmXera  = hmSp.season?.xera;
  const spLine  = (awyXera != null && hmXera != null)
    ? `xERA ${awyXera.toFixed(2)} vs ${hmXera.toFixed(2)}`
    : null;

  // Lineup xwOBA averages from actual lineup players
  const awyL  = (g.away_lineup || []).filter(b => b.xwoba != null);
  const hmL   = (g.home_lineup  || []).filter(b => b.xwoba != null);
  const awyX  = awyL.length >= 3 ? awyL.reduce((s, b) => s + b.xwoba, 0) / awyL.length : null;
  const hmX   = hmL.length  >= 3 ? hmL.reduce((s, b)  => s + b.xwoba, 0) / hmL.length  : null;
  const luLine = (awyX != null && hmX != null)
    ? `.${Math.round(awyX * 1000)} vs .${Math.round(hmX * 1000)} xwOBA`
    : null;

  // Form note for the away SP (positive dev = recently worse for the HOME pitcher — we care about AWAY dev)
  const awyDev = sig.last_start_dev_away;
  let formNote = '';
  if (awyDev != null && Math.abs(awyDev) >= 1.0) {
    const dir = awyDev > 0 ? `+${awyDev.toFixed(1)} (cold)` : `${awyDev.toFixed(1)} (hot)`;
    const awySPName = awySp.name ? awySp.name.split(',')[0] : 'Away SP';
    formNote = ` · ${awySPName} ${dir} last 3`;
  }

  const signal = pred.pick_signal === 'pitcher_lineup' ? 'SP + Lineup edge' : 'SP edge';
  const parts = [signal];
  if (spLine)  parts.push(spLine);
  if (luLine)  parts.push(luLine);

  return `<div class="pick-reasoning">${parts.join(' · ')}${formNote}</div>`;
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
  const pickTier = pred.pick_tier;
  const pickTierBadge = pickTier === 'elite_away'
    ? `<span class="pick-tier-badge tier-elite-away">◆ Elite Away</span>`
    : pickTier === 'strong_away'
    ? `<span class="pick-tier-badge tier-strong-away">▲ Strong Away</span>`
    : '';
  const oddsQual   = pickTier ? oddsQualityBadge(g.odds?.away_ml) : '';
  const pickReason = buildPickReasoning(g);

  // Inline probability bar
  const probBar = `
<div class="pred-prob-row">
  <span class="ppr-away ${awayFav ? 'ppr-fav' : 'ppr-dog'}">${abbrev(g.away_team)} ${awayPct}%</span>
  <div class="ppr-bar">
    <div class="ppr-seg ${awayFav ? 'ppr-fav-fill' : 'ppr-dog-fill'}" style="width:${awayPct}%"></div>
    <div class="ppr-seg ${awayFav ? 'ppr-dog-fill' : 'ppr-fav-fill'}" style="width:${homePct}%"></div>
  </div>
  <span class="ppr-home ${awayFav ? 'ppr-dog' : 'ppr-fav'}">${abbrev(g.home_team)} ${homePct}%</span>
</div>`;

  const badges = [tierBadge, pickTierBadge, oddsQual].filter(Boolean).join('');
  const badgesRow = badges ? `<div class="pred-badges-row">${badges}</div>` : '';

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
    ${probBar}
    ${badgesRow}
    ${pickReason}
  </div>
  ${scoreCenter}
  <span class="expand-arrow">▼</span>
</div>`;
}

function spEra(val, side = 'home') {
  const cls = side === 'away' ? 'xera-tag xera-tag-away' : 'xera-tag';
  return `<span class="${cls}">xERA ${val.toFixed(2)}</span>`;
}

// ── Game edge banner — prominent top-of-card callout for actionable picks ─────
function gameEdgeBannerHTML(g) {
  const pred   = g.prediction || {};
  const tier   = pred.pick_tier;
  const conds  = g.edge_conditions || [];
  const status = g.game_status || 'preview';
  if (status !== 'preview') return '';

  const parts = [];

  if (tier === 'elite_away') {
    const edgePct = pred.model_edge_ml != null
      ? `Model +${(Math.abs(pred.model_edge_ml) * 100).toFixed(1)}% vs Vegas`
      : 'Model strongly disagrees with Vegas';
    parts.push(`
<div class="game-edge-banner banner-tier-elite">
  <span class="geb-icon">◆</span>
  <div class="geb-body">
    <span class="geb-label">Elite Away</span>
    <span class="geb-sep"></span>
    <span class="geb-detail">${edgePct}</span>
  </div>
  <span class="geb-sub">+9.2% ROI · 884 bets</span>
</div>`);
  } else if (tier === 'strong_away') {
    parts.push(`
<div class="game-edge-banner banner-tier-strong">
  <span class="geb-icon">▲</span>
  <div class="geb-body">
    <span class="geb-label">Strong Away</span>
    <span class="geb-sep"></span>
    <span class="geb-detail">SP edge + model tilt</span>
  </div>
</div>`);
  }

  for (const e of conds) {
    const roiStr = `+${e.roi_pct.toFixed(1)}% ROI`;
    const icon   = e.direction === 'UNDER' ? '⚡' : '★';
    parts.push(`
<div class="game-edge-banner banner-edge-under">
  <span class="geb-icon">${icon}</span>
  <div class="geb-body">
    <span class="geb-label">${e.label}</span>
    <span class="geb-sep"></span>
    <span class="geb-detail">${roiStr} historical</span>
  </div>
  <span class="geb-sub">${e.seasons}</span>
</div>`);
  }

  return parts.join('');
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

// ── Edge condition badge (game card) ──────────────────────────────────────────
function edgeConditionsHTML(g) {
  const conds = g.edge_conditions;
  if (!conds || !conds.length) return '';
  const status = g.game_status || 'preview';
  if (status !== 'preview') return '';

  const CONF_ICON = { high: '⚡', medium: '~', new: '★' };
  const badges = conds.map(e => {
    const icon = CONF_ICON[e.confidence] || '⚡';
    const roiStr = `+${e.roi_pct.toFixed(1)}% ROI`;
    const cls = e.confidence === 'high' ? 'edge-cond-badge strong' : e.confidence === 'new' ? 'edge-cond-badge new' : 'edge-cond-badge medium';
    return `<span class="${cls}" title="${e.seasons}">${icon} ${e.label} · ${roiStr}</span>`;
  }).join('');

  return `<div class="edge-cond-strip">${badges}</div>`;
}

// ── Lineup status (collapsed card) — batter highlights moved to expanded lineup section ──
function lineupStatusHTML(g) {
  const status = g.lineup_status;
  let chip;
  if (status === 'official') {
    chip = `<span class="lineup-chip official">✓ Official</span>`;
  } else if (status === 'proxy') {
    chip = `<span class="lineup-chip proxy">~ Recent history</span>`;
  } else {
    chip = `<span class="lineup-chip tbd">Lineups TBD</span>`;
  }
  return `<div class="lineup-status-row"><div class="ls-center">${chip}</div></div>`;
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
    ${(() => {
      const mc = g.mc_simulation;
      const modelTotal = pred.predicted_total;
      if (mc && mc.mc_total != null && modelTotal != null) {
        const diff = Math.abs(mc.mc_total - modelTotal);
        const diffCls = diff >= 1.5 ? 'mc-total-hi' : 'mc-total-lo';
        return `<span class="total-label">Model <strong>${modelTotal}</strong> · <span class="mc-total-lbl ${diffCls}">Sim ${mc.mc_total}</span></span>`;
      }
      return `<span class="total-label">Total: ${modelTotal}</span>`;
    })()}
  </div>` : ''}

  ${(() => {
    const mc = g.mc_simulation;
    const status = g.game_status || 'preview';
    if (!mc || status !== 'preview') return '';
    const modelHome = pred.home_win_pct;
    if (modelHome == null || mc.mc_win_pct == null) return '';
    const gapPp = Math.round(Math.abs(mc.mc_win_pct - modelHome) * 100);
    if (gapPp < 8) return '';
    const mcFavors = mc.mc_win_pct >= 0.5 ? g.home_team : g.away_team;
    const modelFavors = modelHome >= 0.5 ? g.home_team : g.away_team;
    const agree = mcFavors === modelFavors;
    return `<div class="mc-diverge ${agree ? 'mc-div-agree' : 'mc-div-split'}" title="Simulation uses Statcast only — gap often reflects market info (injuries, sharp money) the model can't see">⚡ Sim ${Math.round(mc.mc_win_pct * 100)}% home vs model ${Math.round(modelHome * 100)}% <span class="mc-div-gap">${gapPp}pp gap</span></div>`;
  })()}

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

// ── Odds / unit helpers ───────────────────────────────────────────────────────

function americanToProfit(odds) {
  // Returns profit per 1-unit stake. Returns null when odds unavailable.
  if (odds == null || odds === 0) return null;
  return odds > 0 ? odds / 100 : 100 / Math.abs(odds);
}

function fmtUnits(u) {
  return (u >= 0 ? '+' : '') + u.toFixed(2) + 'u';
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

// ── Props performance ─────────────────────────────────────────────────────────
function renderPropsPerformance() {
  const records = propsHistData || [];
  const resolved = records.filter(r => r.hit !== null && r.hit !== undefined);

  const TYPE_LABELS = {
    HR_PROP: 'Home Run', HIT_PROP: 'Anytime Hit',
    TB_PROP: 'Total Bases 1.5+', K_PROP: 'Strikeouts 4.5+',
  };

  if (!resolved.length) {
    const snap = records.length;
    return `<div class="props-perf-section">
      <h3 class="props-perf-title">Props Performance Tracker</h3>
      <p class="props-perf-note">${snap ? `${snap} picks tracked — outcomes resolve after games finish. Check back tomorrow.` : 'No prop picks tracked yet. Data builds automatically each day.'}</p>
    </div>`;
  }

  // Aggregate by type
  const byType = {};
  for (const r of resolved) {
    const bt = r.bet_type;
    if (!byType[bt]) byType[bt] = { n: 0, hits: 0 };
    byType[bt].n++;
    if (r.hit) byType[bt].hits++;
  }

  // Aggregate by signal band
  const bands = [['5.0–5.9', 5.0, 6.0], ['6.0–6.4', 6.0, 6.5], ['6.5+', 6.5, 99]];
  const bySignal = bands.map(([label, lo, hi]) => {
    const sub = resolved.filter(r => (r.signal || 0) >= lo && (r.signal || 0) < hi);
    if (!sub.length) return null;
    const hits = sub.filter(r => r.hit).length;
    return { label, n: sub.length, hits, hit_rate: Math.round(hits / sub.length * 1000) / 10 };
  }).filter(Boolean);

  const totalHits = resolved.filter(r => r.hit).length;
  const totalHR   = Math.round(totalHits / resolved.length * 1000) / 10;

  // Color hit rate: green ≥54%, yellow 50-53%, red <50%
  const hrClass = hr => hr >= 54 ? 'hr-good' : hr >= 50 ? 'hr-ok' : 'hr-bad';

  const typeRows = Object.entries(byType).map(([bt, d]) => {
    const hr = Math.round(d.hits / d.n * 1000) / 10;
    return `<tr>
      <td>${TYPE_LABELS[bt] || bt}</td>
      <td>${d.n}</td>
      <td class="${hrClass(hr)}">${hr}%</td>
    </tr>`;
  }).join('');

  const sigRows = bySignal.map(r => `<tr>
    <td>Signal ${r.label}</td>
    <td>${r.n}</td>
    <td class="${hrClass(r.hit_rate)}">${r.hit_rate}%</td>
  </tr>`).join('');

  return `<div class="props-perf-section">
    <h3 class="props-perf-title">Props Performance Tracker <span class="props-perf-total">${resolved.length} resolved · ${totalHR}% hit rate</span></h3>
    <p class="props-perf-note">Break-even for standard -115 over props is ~53.5%. Green = edge, yellow = marginal, red = below market.</p>
    <div class="props-perf-tables">
      <table class="props-perf-table">
        <thead><tr><th>Prop Type</th><th>N</th><th>Hit Rate</th></tr></thead>
        <tbody>${typeRows}</tbody>
      </table>
      <table class="props-perf-table">
        <thead><tr><th>Signal Strength</th><th>N</th><th>Hit Rate</th></tr></thead>
        <tbody>${sigRows}</tbody>
      </table>
    </div>
  </div>`;
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

  // Game log summary stats (units assume 1-unit flat bet per qualifying call)
  let mlEdgeCalls = 0, mlEdgeHits = 0, mlEdgeUnits = 0;
  let totalLeanCalls = 0, totalLeanHits = 0, totalLeanUnits = 0;
  for (const r of decided) {
    if (r.model_edge_ml != null && Math.abs(r.model_edge_ml) >= 0.10) {
      mlEdgeCalls++;
      const edgeIsHome = r.model_edge_ml >= 0;
      const won  = edgeIsHome ? r.actual_winner === 'home' : r.actual_winner === 'away';
      const odds = edgeIsHome ? r.home_ml : r.away_ml;
      const prof = won ? (americanToProfit(odds) ?? 1.0) : -1.0;
      mlEdgeUnits += prof;
      if (won) mlEdgeHits++;
    }
    if (r.predicted_total != null && r.vegas_total != null && r.total_went_over != null) {
      const lean = +(r.predicted_total - r.vegas_total).toFixed(1);
      if (Math.abs(lean) >= 0.5) {
        totalLeanCalls++;
        const leanOver = lean > 0;
        const hit  = (leanOver && r.total_went_over === true) || (!leanOver && r.total_went_over === false);
        const odds = leanOver ? r.over_price : r.under_price;
        const prof = hit ? (americanToProfit(odds) ?? 0.909) : -1.0;  // default ~-110 equiv
        totalLeanUnits += prof;
        if (hit) totalLeanHits++;
      }
    }
  }
  const mlPct    = mlEdgeCalls    > 0 ? Math.round(mlEdgeHits    / mlEdgeCalls    * 100) : null;
  const totalPct = totalLeanCalls > 0 ? Math.round(totalLeanHits / totalLeanCalls * 100) : null;
  const mlUnitsCls    = mlEdgeUnits    >= 0 ? 'units-pos' : 'units-neg';
  const totalUnitsCls = totalLeanUnits >= 0 ? 'units-pos' : 'units-neg';
  const gameLogSummaryHTML = `
<div class="game-log-summary">
  <span class="log-stat">ML Value Calls (|edge|≥10%): <strong>${mlEdgeCalls > 0 ? `${mlEdgeHits}/${mlEdgeCalls} (${mlPct}%)` : '—'}</strong>${mlEdgeCalls > 0 ? ` <span class="log-units ${mlUnitsCls}">${fmtUnits(mlEdgeUnits)}</span>` : ''}</span>
  <span class="log-stat">Total Lean (≥0.5 run): <strong>${totalLeanCalls > 0 ? `${totalLeanHits}/${totalLeanCalls} (${totalPct}%)` : '—'}</strong>${totalLeanCalls > 0 ? ` <span class="log-units ${totalUnitsCls}">${fmtUnits(totalLeanUnits)}</span>` : ''}</span>
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

${renderPropsPerformance()}

<div class="history-section">
  <div class="section-heading">Game Log</div>
  ${gameLogSummaryHTML}
  ${byDate.map(({ date: d, games }) => {
    const dc = games.filter(r => r.predicted_winner === r.actual_winner).length;
    const dLabel = formatDateLabel(d);

    // Per-day model audit (1-unit flat bet on each qualifying call)
    let mlW = 0, mlL = 0, mlUnits = 0;
    let totW = 0, totL = 0, totUnits = 0;
    for (const r of games) {
      if (r.model_edge_ml != null && Math.abs(r.model_edge_ml) >= 0.10) {
        const edgeIsHome = r.model_edge_ml >= 0;
        const won  = edgeIsHome ? r.actual_winner === 'home' : r.actual_winner === 'away';
        const odds = edgeIsHome ? r.home_ml : r.away_ml;
        const prof = won ? (americanToProfit(odds) ?? 1.0) : -1.0;
        mlUnits += prof;
        if (won) mlW++; else mlL++;
      }
      if (r.predicted_total != null && r.vegas_total != null && r.total_went_over != null) {
        const lean = +(r.predicted_total - r.vegas_total).toFixed(1);
        if (Math.abs(lean) >= 0.5) {
          const leanOver = lean > 0;
          const hit  = (leanOver && r.total_went_over === true) || (!leanOver && r.total_went_over === false);
          const odds = leanOver ? r.over_price : r.under_price;
          const prof = hit ? (americanToProfit(odds) ?? 0.909) : -1.0;
          totUnits += prof;
          if (hit) totW++; else totL++;
        }
      }
    }
    const mlCls  = mlUnits  >= 0 ? 'audit-pos' : 'audit-neg';
    const totCls = totUnits >= 0 ? 'audit-pos' : 'audit-neg';
    const mlAuditHTML = (mlW + mlL > 0)
      ? `<span class="day-audit-stat ${mlCls}">ML Edge ${mlW}–${mlL} <span class="audit-units">${fmtUnits(mlUnits)}</span></span>`
      : '';
    const totAuditHTML = (totW + totL > 0)
      ? `<span class="day-audit-stat ${totCls}">Total ${totW}–${totL} <span class="audit-units">${fmtUnits(totUnits)}</span></span>`
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

// ── Backtest segmentation ─────────────────────────────────────────────────────

// ── Backtest view ─────────────────────────────────────────────────────────────

function renderBacktestView() {
  const el = document.getElementById('backtest-view');

  if (!backtestData) {
    el.innerHTML = `<div class="empty-state"><p>Backtest data not available yet. Run the pipeline to generate it.</p></div>`;
    return;
  }

  const { stats, games = [], roi_stats, segmentation } = backtestData;
  if (!stats) {
    el.innerHTML = `<div class="empty-state"><p>No backtest stats found in data.</p></div>`;
    return;
  }

  const fmtRoi   = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
  const fmtUnits = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2);
  const pct      = v => v != null ? (v * 100).toFixed(1) + '%' : '—';
  const roiCls   = v => v == null ? '' : v >= 0 ? 'roi-pos' : 'roi-neg';
  const segCls   = v => v == null ? '' : v >= 0 ? 'seg-pos' : 'seg-neg';
  const roiColor = v => v == null ? '' : v >= 5 ? 'bt-val-green' : v >= 0 ? 'bt-val-pos' : 'bt-val-neg';

  // ── Compute away tier × pitcher advantage from games array ────────────────
  const awayTierDefs = [
    { label: 'Slight Lean',  sub: '0–3% edge',    cond: e => e < 0 && e >= -0.03 },
    { label: 'Moderate',     sub: '3–6% edge',    cond: e => e < -0.03 && e >= -0.06 },
    { label: 'Strong',       sub: '6–10% edge',   cond: e => e < -0.06 && e >= -0.10 },
    { label: 'Very Strong',  sub: '10%+ edge',    cond: e => e < -0.10 },
  ];
  const mkCell = () => ({ bets: 0, wins: 0, units: 0 });
  const awayTierData = awayTierDefs.map(() => ({ sp: mkCell(), no_sp: mkCell() }));

  for (const g of games) {
    const edge = g.model_edge_ml;
    if (edge == null || edge >= 0 || !g.bet_side) continue;
    const ml = g.bet_side === 'home' ? g.home_ml : g.away_ml;
    if (ml == null) continue;
    const spAdv = (g.pitcher_score_home - g.pitcher_score_away) < -0.05;
    for (let i = 0; i < awayTierDefs.length; i++) {
      if (awayTierDefs[i].cond(edge)) {
        const c = spAdv ? awayTierData[i].sp : awayTierData[i].no_sp;
        c.bets++;
        if (g.bet_won) {
          c.wins++;
          c.units += ml > 0 ? ml / 100 : 100 / Math.abs(ml);
        } else {
          c.units -= 1.0;
        }
        break;
      }
    }
  }

  function fmtCell(c) {
    if (!c.bets) return { roi: '—', detail: '—', roiClass: '', isBlank: true };
    const roi = c.units / c.bets * 100;
    const wr  = c.wins / c.bets * 100;
    return {
      roi:      (roi >= 0 ? '+' : '') + roi.toFixed(1) + '%',
      detail:   c.bets.toLocaleString() + ' bets · ' + wr.toFixed(0) + '% win',
      roiClass: roi >= 0 ? 'pos' : 'neg',
      isBlank:  false,
    };
  }

  // ── Compute home tier ROI from games array ───────────────────────────────
  const homeTierDefs = [
    { label: 'Slight Lean', sub: '0–3% edge',   cond: e => e > 0 && e <= 0.03 },
    { label: 'Moderate',    sub: '3–6% edge',   cond: e => e > 0.03 && e <= 0.06 },
    { label: 'Strong',      sub: '6–10% edge',  cond: e => e > 0.06 && e <= 0.10 },
    { label: 'Very Strong', sub: '10%+ edge',   cond: e => e > 0.10 },
  ];
  const homeTierData = homeTierDefs.map(() => mkCell());

  for (const g of games) {
    const edge = g.model_edge_ml;
    if (edge == null || edge <= 0 || !g.bet_side) continue;
    const ml = g.bet_side === 'home' ? g.home_ml : g.away_ml;
    if (ml == null) continue;
    for (let i = 0; i < homeTierDefs.length; i++) {
      if (homeTierDefs[i].cond(edge)) {
        const c = homeTierData[i];
        c.bets++;
        if (g.bet_won) {
          c.wins++;
          c.units += ml > 0 ? ml / 100 : 100 / Math.abs(ml);
        } else {
          c.units -= 1.0;
        }
        break;
      }
    }
  }

  const homeTierRows = homeTierDefs.map((def, i) => {
    const c = homeTierData[i];
    if (!c.bets) return `<tr><td class="bt-away-tier-label">${def.label}<span class="bt-tier-meta">${def.sub}</span></td><td>—</td><td>—</td><td>—</td></tr>`;
    const roi = c.units / c.bets * 100;
    const wr  = c.wins / c.bets * 100;
    const rClass = roi >= 0 ? 'pos' : 'neg';
    return `<tr>
      <td class="bt-away-tier-label">${def.label}<span class="bt-tier-meta">${def.sub}</span></td>
      <td>${c.bets.toLocaleString()}</td>
      <td>${wr.toFixed(1)}%</td>
      <td class="bt-cell-roi ${rClass}">${roi >= 0 ? '+' : ''}${roi.toFixed(1)}%</td>
    </tr>`;
  }).join('');

  // ── Compute totals accuracy by year from games array ─────────────────────
  const totalsByYear = {};
  for (const g of games) {
    const ct = g.closing_total, at = g.actual_total, pt = g.predicted_total;
    if (ct == null || at == null || pt == null || at === ct) continue;
    const priceKey = pt > ct ? 'over_price' : 'under_price';
    if (!g[priceKey]) continue;
    const yr = g.season || g.date?.slice(0, 4);
    if (!yr) continue;
    if (!totalsByYear[yr]) totalsByYear[yr] = { ob: 0, ow: 0, ub: 0, uw: 0 };
    const t = totalsByYear[yr];
    if (pt > ct) { t.ob++; if (at > ct) t.ow++; }
    else         { t.ub++; if (at < ct) t.uw++; }
  }

  // ── Compute year-over-year win rate from games array ─────────────────────
  const byYr = {};
  for (const g of games) {
    const yr = g.season || g.date?.slice(0, 4);
    if (!yr) continue;
    if (!byYr[yr]) byYr[yr] = { n: 0, correct: 0 };
    if (g.actual_winner && g.actual_winner !== 'tie') {
      byYr[yr].n++;
      if (g.correct) byYr[yr].correct++;
    }
  }

  // ── Hero KPIs ─────────────────────────────────────────────────────────────
  const totalGames = games.length;
  const roi = roi_stats || {};
  const roiValCls = v => v == null ? '' : v > 0 ? 'kpi-green' : v < 0 ? 'kpi-red' : '';

  const heroSection = `
    <div class="bt-context-bar">${totalGames.toLocaleString()} games logged since 2021 &nbsp;·&nbsp; ROI = profit per $1 flat bet &nbsp;·&nbsp; Pinnacle closing lines</div>
    <div class="bt-kpi-row">
      <div class="bt-kpi-card">
        <div class="bt-kpi-val ${roiValCls(roi.ml_roi_pct)}">${fmtRoi(roi.ml_roi_pct)}</div>
        <div class="bt-kpi-label">2026 ML ROI</div>
        <div class="bt-kpi-sub">${fmtUnits(roi.ml_units_won)} units · ${roi.ml_bets ?? 0} bets</div>
      </div>
      <div class="bt-kpi-card">
        <div class="bt-kpi-val ${roiValCls(roi.total_roi_pct)}">${fmtRoi(roi.total_roi_pct)}</div>
        <div class="bt-kpi-label">2026 Totals ROI</div>
        <div class="bt-kpi-sub">${fmtUnits(roi.total_units_won)} units · ${roi.total_bets ?? 0} bets</div>
      </div>
      <div class="bt-kpi-card">
        <div class="bt-kpi-val">${pct(stats.win_pct_overall)}</div>
        <div class="bt-kpi-label">Prediction Accuracy</div>
        <div class="bt-kpi-sub">${(stats.total_correct ?? 0).toLocaleString()} / ${(stats.total_decided ?? 0).toLocaleString()} games</div>
      </div>
      <div class="bt-kpi-card">
        <div class="bt-kpi-val">${totalGames.toLocaleString()}</div>
        <div class="bt-kpi-label">Games Tracked</div>
        <div class="bt-kpi-sub">2021–2026 · all seasons</div>
      </div>
    </div>`;

  // liveSection replaced by heroSection above

  // ── Section 02: Moneyline Analysis ────────────────────────────────────────
  const awayTierRows = awayTierDefs.map((def, i) => {
    const sp    = fmtCell(awayTierData[i].sp);
    const no_sp = fmtCell(awayTierData[i].no_sp);
    const isElite = i === 3;
    return `<tr class="${isElite ? 'bt-away-elite-row' : ''}">
      <td class="bt-away-tier-label">
        ${def.label}
        <span class="bt-tier-meta">${def.sub}</span>
      </td>
      <td class="${isElite ? 'bt-cell-elite' : ''}">
        <div class="bt-cell-inner">
          <div class="bt-cell-roi ${sp.roiClass}">${sp.roi}</div>
          <div class="bt-cell-detail">${sp.detail}</div>
        </div>
      </td>
      <td>
        <div class="bt-cell-inner">
          <div class="bt-cell-roi ${no_sp.roiClass}">${no_sp.roi}</div>
          <div class="bt-cell-detail">${no_sp.detail}</div>
        </div>
      </td>
    </tr>`;
  }).join('');

  const moneylineSection = `
    <div class="bt-sec-head">
      <span class="bt-sec-num">02</span>
      <span class="bt-sec-title">Moneyline Analysis</span>
      <span class="bt-sec-sub">Away edge vs. home favorite trap · 2021–2025</span>
    </div>
    <div class="bt-ml-grid">
      <div>
        <div class="bt-subsection-title">Away Advantage · Edge × Pitcher Quality</div>
        <p class="bt-sec-desc">When the model strongly favors the away team and the away starter has better metrics, the combination has produced the highest ROI in the database — on underdogs priced around +170.</p>
        <div class="bt-away-grid-wrap">
          <table class="bt-away-grid">
            <thead><tr>
              <th class="bt-away-tier-col">Model Away Edge vs. Vegas</th>
              <th class="bt-away-sp-col bt-col-sp">Away SP Better</th>
              <th class="bt-away-sp-col">Home SP Better / Even</th>
            </tr></thead>
            <tbody>${awayTierRows}</tbody>
          </table>
        </div>
        <div class="bt-signal-note">
          <strong>Why does 40% win rate beat 73%?</strong> The 10%+ / pitcher advantage picks are on underdogs (~+170). Winning 40% at those odds is very profitable. The 73% column bets heavy favorites — you need ~75% just to break even.
        </div>
      </div>
      <div>
        <div class="bt-subsection-title">Home Favorite Trap</div>
        <p class="bt-sec-desc">When the model and Vegas both strongly favor the home team, the combined signal becomes over-confident. The home coefficient was cut from 1.0× to 0.4× after this data.</p>
        <div class="bt-away-grid-wrap">
          <table class="bt-away-grid">
            <thead><tr>
              <th class="bt-away-tier-col">Model Home Edge vs. Vegas</th>
              <th>Bets</th><th>Win%</th><th>ROI</th>
            </tr></thead>
            <tbody>${homeTierRows}</tbody>
          </table>
        </div>
        <div class="bt-signal-note">
          The 10%+ tier had <strong>-9.4% ROI</strong> across five seasons. Dampening this signal lets the model trust Vegas pricing rather than amplifying it.
        </div>
      </div>
    </div>`;

  // ── SECTION 4: Model Calibration ─────────────────────────────────────────
  const tierLabels = { '50_55': '50–55%', '55_60': '55–60%', '60_65': '60–65%', '65_plus': '65%+' };
  const confRows = Object.entries(stats.win_pct_by_confidence || {}).map(([key, t]) => {
    const pctTxt = t.pct != null ? (t.pct * 100).toFixed(1) + '%' : '—';
    const pctCls = (t.pct ?? 0) >= 0.60 ? 'tier-pct-good' : (t.pct ?? 0) >= 0.53 ? 'tier-pct-ok' : '';
    const vs = t.pct != null ? ((t.pct - 0.5) >= 0 ? '+' : '') + ((t.pct - 0.5) * 100).toFixed(1) + 'pp' : '—';
    const vsCls = (t.pct ?? 0) >= 0.5 ? 'seg-pos' : 'seg-neg';
    return `<tr>
      <td><strong>${tierLabels[key] || key}</strong></td>
      <td>${(t.total ?? 0).toLocaleString()}</td>
      <td class="${pctCls}">${pctTxt}</td>
      <td class="${vsCls}">${vs}</td>
    </tr>`;
  }).join('');

  const yrRows = Object.keys(byYr).sort().map(yr => {
    const y = byYr[yr];
    const wr = y.n > 0 ? (y.correct / y.n * 100).toFixed(1) + '%' : '—';
    const wrf = y.n > 0 ? y.correct / y.n : 0;
    const wrCls = wrf >= 0.56 ? 'tier-pct-good' : wrf >= 0.53 ? 'tier-pct-ok' : '';
    const isLive = parseInt(yr) === 2026;
    return `<tr${isLive ? ' class="yr-live"' : ''}>
      <td><strong>${yr}${isLive ? ' <span class="live-tag">live</span>' : ''}</strong></td>
      <td>${y.n.toLocaleString()}</td>
      <td class="${wrCls}">${wr}</td>
    </tr>`;
  }).join('');

  const calibrationSection = `
    <div class="bt-sec-head">
      <span class="bt-sec-num">03</span>
      <span class="bt-sec-title">Model Accuracy</span>
      <span class="bt-sec-sub">Win rate by confidence level and by season</span>
    </div>
    <p class="bt-sec-desc">
      Higher model confidence correlates directly with better accuracy. At 65%+, the model has been right
      nearly 4 out of 5 times — but these picks are rare by design (only when pitcher stats are very lopsided).
    </p>
    <div class="bt-two-col">
      <div>
        <div class="bt-subsection-title">By Confidence Level</div>
        <div class="bt-table-wrap">
          <table class="seg-table">
            <thead><tr><th>Model Confidence</th><th>Games</th><th>Actual Win%</th><th>vs. Coin Flip</th></tr></thead>
            <tbody>${confRows}</tbody>
          </table>
        </div>
      </div>
      <div>
        <div class="bt-subsection-title">Year-over-Year</div>
        <div class="bt-table-wrap">
          <table class="seg-table">
            <thead><tr><th>Season</th><th>Games</th><th>Win Rate</th></tr></thead>
            <tbody>${yrRows}</tbody>
          </table>
        </div>
      </div>
    </div>`;

  // ── SECTION 5: Totals (UNDER edge) ───────────────────────────────────────
  const totalsYrRows = Object.keys(totalsByYear).sort().filter(yr => parseInt(yr) < 2026).map(yr => {
    const t = totalsByYear[yr];
    const uAcc = t.ub ? (t.uw / t.ub * 100).toFixed(1) + '%' : '—';
    const oAcc = t.ob ? (t.ow / t.ob * 100).toFixed(1) + '%' : '—';
    const uCls = t.ub && (t.uw / t.ub) >= 0.52 ? 'tier-pct-ok' : '';
    return `<tr>
      <td><strong>${yr}</strong></td>
      <td>${t.ob.toLocaleString()}</td>
      <td>${oAcc}</td>
      <td>${t.ub.toLocaleString()}</td>
      <td class="${uCls}">${uAcc}</td>
    </tr>`;
  }).join('');

  const tsb = backtestData.totals_signal_backtest || {};
  const tsbDir = tsb.by_direction || [];
  const tsbTier = tsb.by_tier || [];
  const tsbOverall = tsb.overall || {};
  const tsbDirRows = ['OVER','UNDER'].map(d => {
    const row = tsbDir.find(r => r.direction === d);
    if (!row) return '';
    const roiCl = (row.roi_pct >= 0) ? 'seg-pos' : 'seg-neg';
    return `<tr>
      <td><strong>${d}</strong></td>
      <td>${row.n.toLocaleString()}</td>
      <td>${row.win_rate != null ? (row.win_rate * 100).toFixed(1) + '%' : '—'}</td>
      <td class="${roiCl}">${row.roi_pct != null ? (row.roi_pct >= 0 ? '+' : '') + row.roi_pct.toFixed(2) + '%' : '—'}</td>
    </tr>`;
  }).join('');
  const tsbTierRows = tsbTier.map(row => {
    const roiCl = (row.roi_pct >= 0) ? 'seg-pos' : 'seg-neg';
    return `<tr>
      <td><strong>${row.tier}</strong></td>
      <td>${row.n.toLocaleString()}</td>
      <td>${row.win_rate != null ? (row.win_rate * 100).toFixed(1) + '%' : '—'}</td>
      <td class="${roiCl}">${row.roi_pct != null ? (row.roi_pct >= 0 ? '+' : '') + row.roi_pct.toFixed(2) + '%' : '—'}</td>
    </tr>`;
  }).join('');

  const tsbYrNote = tsb.total_picks > 0
    ? `Based on ${tsb.total_picks.toLocaleString()} signal picks from ${(tsb.by_year || []).map(r => r.year).join(', ')}.`
    : '';

  const signalRoiBlock = tsbDirRows ? `
    <div class="bt-two-col" style="margin-top:12px">
      <div>
        <div class="bt-subsection-title">ROI by Direction</div>
        <div class="bt-table-wrap">
          <table class="seg-table">
            <thead><tr><th>Direction</th><th>Picks</th><th>Win Rate</th><th>ROI</th></tr></thead>
            <tbody>${tsbDirRows}</tbody>
          </table>
        </div>
      </div>
      <div>
        <div class="bt-subsection-title">ROI by Signal Strength</div>
        <div class="bt-table-wrap">
          <table class="seg-table">
            <thead><tr><th>Signal Tier</th><th>Picks</th><th>Win Rate</th><th>ROI</th></tr></thead>
            <tbody>${tsbTierRows}</tbody>
          </table>
        </div>
      </div>
    </div>
    <div class="bt-signal-note">${tsbYrNote} Signal scored using pitcher quality (xFIP/SIERA/barrel%) + park factor + lineup xwOBA where available. Weather and bullpen modifiers not applied historically.</div>` : '';

  const totalsSection = `
    <div class="bt-sec-head">
      <span class="bt-sec-num">04</span>
      <span class="bt-sec-title">Totals Performance</span>
      <span class="bt-sec-sub">OVER/UNDER prediction accuracy · historical seasons</span>
    </div>
    <p class="bt-sec-desc">
      The model consistently predicts fewer runs than the Vegas line — a lean that has been correct 52–53%
      of the time every season. Combined with the edge-band findings above, UNDER bets generate the
      steadiest returns in the database.
    </p>
    ${totalsYrRows ? `<div class="bt-table-wrap">
      <table class="seg-table">
        <thead><tr><th>Season</th><th>Over Bets</th><th>Over Accuracy</th><th>Under Bets</th><th>Under Accuracy</th></tr></thead>
        <tbody>${totalsYrRows}</tbody>
      </table>
    </div>` : ''}
    ${signalRoiBlock}`;

  // ── SECTION 6: Validated Edges ───────────────────────────────────────────
  // Compute edge-band UNDER ROI from backtest games (same methodology as edge_research.py)
  function computeUnderBandRoi(lo, hi, allGames) {
    const bySeason = {};
    let totalUnits = 0, totalBets = 0, totalWins = 0;
    for (const g of allGames) {
      const ct = g.closing_total;
      const over = g.total_went_over;
      const uPrice = g.under_price;
      if (ct == null || over == null || uPrice == null) continue;
      if (ct < lo || ct >= hi) continue;
      const yr = g.season || parseInt(g.date);
      if (!yr || yr < 2022) continue;
      if (!bySeason[yr]) bySeason[yr] = { bets: 0, wins: 0, units: 0 };
      const s = bySeason[yr];
      const won = !over; // betting UNDER wins when total does NOT go over
      const dec = uPrice >= 0 ? 1 + uPrice / 100 : 1 - 100 / uPrice;
      s.bets++; totalBets++;
      if (won) { s.wins++; totalWins++; s.units += dec - 1; totalUnits += dec - 1; }
      else      { s.units -= 1; totalUnits -= 1; }
    }
    const roi = totalBets ? (totalUnits / totalBets * 100) : null;
    return { bySeason, totalBets, totalWins, roi };
  }

  const edge8to85  = computeUnderBandRoi(8.0, 8.5, games);
  const edge9to95  = computeUnderBandRoi(9.0, 9.5, games);
  const edge85to9  = computeUnderBandRoi(8.5, 9.0, games); // the dead zone — for contrast

  function edgeSeasonRows(bandData) {
    return Object.keys(bandData.bySeason).sort().map(yr => {
      const s = bandData.bySeason[yr];
      const roi = s.bets ? (s.units / s.bets * 100) : null;
      const wr  = s.bets ? (s.wins / s.bets * 100) : null;
      const isLive = parseInt(yr) >= 2026;
      const roiCl = roi == null ? '' : roi >= 0 ? 'seg-pos' : 'seg-neg';
      const roiStr = roi == null ? '—' : (roi >= 0 ? '+' : '') + roi.toFixed(1) + '%';
      return `<tr${isLive ? ' class="yr-live"' : ''}>
        <td><strong>${yr}${isLive ? ' <span class="live-tag">live</span>' : ''}</strong></td>
        <td>${s.bets.toLocaleString()}</td>
        <td>${wr != null ? wr.toFixed(1) + '%' : '—'}</td>
        <td class="${roiCl}">${roiStr}</td>
      </tr>`;
    }).join('');
  }

  // Determine confidence based on how many seasons are profitable
  function bandConf(bySeason) {
    const yrs = Object.keys(bySeason).filter(y => parseInt(y) < 2026);
    const pos = yrs.filter(y => bySeason[y].units > 0).length;
    if (pos >= 4) return 'high';
    if (pos >= 3) return 'medium';
    return 'low';
  }

  const ec8Roi = edge8to85.roi;
  const ec9Roi = edge9to95.roi;
  const ec8RoiStr = ec8Roi != null ? (ec8Roi >= 0 ? '+' : '') + ec8Roi.toFixed(1) + '%' : '—';
  const ec9RoiStr = ec9Roi != null ? (ec9Roi >= 0 ? '+' : '') + ec9Roi.toFixed(1) + '%' : '—';
  const ec9Conf = bandConf(edge9to95.bySeason);

  const edgesSection = `
    <div class="bt-sec-head">
      <span class="bt-sec-num">01</span>
      <span class="bt-sec-title">Validated Edges</span>
      <span class="bt-sec-sub">Structural market inefficiencies · 9,400+ games · 2022–2026</span>
    </div>
    <p class="bt-sec-desc">
      A systematic scan of every game found two total-line bands where a flat $1 bet on the UNDER
      produces consistent positive ROI across multiple seasons — structural market inefficiencies, not model signals.
      The <strong>Edges tab</strong> flags today's qualifying games.
    </p>
    <div class="bt-edges-grid">

      <div class="bt-edge-card2">
        <div class="bt-ec2-stat-col">
          <div class="bt-ec2-roi-num ecr-green">${ec8RoiStr}</div>
          <div class="bt-ec2-roi-lbl">avg ROI</div>
          <div class="bt-ec2-n-stat">${edge8to85.totalBets.toLocaleString()} games</div>
          <span class="edge-cond-badge strong">⚡ Strong</span>
        </div>
        <div class="bt-ec2-body">
          <div class="bt-ec2-band-label">UNDER · Total 8.0 – 8.5</div>
          <p class="bt-ec2-desc">Games at the pitcher's duel boundary. The market shades toward the over, leaving UNDER value. Profitable in 4 of 5 full seasons — 2023 was the only losing year.</p>
          <div class="bt-table-wrap">
            <table class="seg-table">
              <thead><tr><th>Season</th><th>Games</th><th>Win%</th><th>ROI</th></tr></thead>
              <tbody>${edgeSeasonRows(edge8to85)}</tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="bt-edge-card2 ${ec9Conf === 'medium' ? 'bt-ec2-watch' : ''}">
        <div class="bt-ec2-stat-col ${ec9Conf === 'medium' ? 'ec2-watch-col' : ''}">
          <div class="bt-ec2-roi-num ${ec9Conf === 'medium' ? 'ecr-gold' : 'ecr-green'}">${ec9RoiStr}</div>
          <div class="bt-ec2-roi-lbl">avg ROI</div>
          <div class="bt-ec2-n-stat">${edge9to95.totalBets.toLocaleString()} games</div>
          <span class="edge-cond-badge medium">~ Watch</span>
        </div>
        <div class="bt-ec2-body">
          <div class="bt-ec2-band-label">UNDER · Total 9.0 – 9.5</div>
          <p class="bt-ec2-desc">Strong 2022–2025 record (+10–14% each year), but 2026 has reversed sharply. May be market adjustment or early-season variance. Treat as a secondary signal.</p>
          <div class="bt-table-wrap">
            <table class="seg-table">
              <thead><tr><th>Season</th><th>Games</th><th>Win%</th><th>ROI</th></tr></thead>
              <tbody>${edgeSeasonRows(edge9to95)}</tbody>
            </table>
          </div>
        </div>
      </div>

    </div>
    <div class="bt-edge-footer-row">
      <div class="bt-edge-dead2">
        <strong>Dead Zone (8.5–9.0):</strong> UNDER shows negative ROI in this band
        ${edge85to9.roi != null ? `(${edge85to9.roi >= 0 ? '+' : ''}${edge85to9.roi.toFixed(1)}% · ${edge85to9.totalBets.toLocaleString()} games)` : ''}.
        Avoid betting UNDER when the total is in this range.
      </div>
      <div class="bt-edge-takeaway2">
        <strong>How to use:</strong> When the Edges tab highlights a game, its closing total falls in one of these bands — the strongest structural signal found in five years of data.
      </div>
    </div>`;

  // ── Section 05: Props ──────────────────────────────────────────────────────
  const propCounts = {};
  for (const p of (propsHistData || [])) {
    if (p.hit != null) propCounts[p.bet_type] = (propCounts[p.bet_type] || 0) + 1;
  }
  const hasEnoughProps = Object.values(propCounts).some(v => v >= 30);
  const propsSection = `
    <div class="bt-sec-head">
      <span class="bt-sec-num">05</span>
      <span class="bt-sec-title">Props Performance</span>
      <span class="bt-sec-sub">HR, strikeout, and hits prop picks</span>
    </div>
    ${hasEnoughProps ? renderPropsPerformance() : `
    <div class="bt-props-holding">
      <div class="bt-props-msg">Building data — tracking since May 31, 2026</div>
      <div class="bt-props-sub">Hit-rate analysis by signal strength appears once we have 30+ resolved picks per prop type.</div>
    </div>`}`;

  // ── Section 06: Game Log ──────────────────────────────────────────────────
  const logRows = games.slice(0, 200).map(g => {
    const winnerTeam = g.predicted_winner === 'home' ? g.home_team : g.away_team;
    const actualTeam = g.actual_winner === 'home' ? g.home_team : g.away_team;
    const conf = Math.round(Math.max(g.home_win_pct, g.away_win_pct) * 100);
    const rowClass = g.correct ? 'row-hit' : g.actual_winner === 'tie' ? '' : 'row-miss';
    const icon = g.actual_winner === 'tie' ? '—' : (g.correct ? '✓' : '✗');
    const dateFmt = g.date ? g.date.slice(5).replace('-', '/') : '—';
    const edgeVal = g.model_edge_ml;
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

  el.innerHTML = `
    <div class="backtest-wrap">
      ${heroSection}
      ${edgesSection}
      ${moneylineSection}
      ${calibrationSection}
      ${totalsSection}
      ${propsSection}
      <div class="bt-sec-head bt-sec-head-log">
        <span class="bt-sec-num">06</span>
        <span class="bt-sec-title">Game Log</span>
        <span class="bt-sec-sub">${games.length.toLocaleString()} games · most recent first</span>
      </div>
      <div class="bt-table-wrap">
        <table class="bt-table">
          <thead><tr><th>Season</th><th>Date</th><th>Matchup</th><th>Predicted</th><th>Actual</th><th>Edge</th><th></th></tr></thead>
          <tbody>${logRows}</tbody>
        </table>
      </div>
    </div>`;
}

// ── Pitcher Value Tab ─────────────────────────────────────────────────────────

let _pvSort = { col: 'ml_roi', dir: -1 };
let _pvMinStarts = 20;
let _pvSearch = '';
let _pvRegularOnly = true;

function renderPitcherView() {
  const el = document.getElementById('pitcher-view');
  if (!pitcherData) {
    el.innerHTML = `<div class="empty-state"><p>Pitcher value data not available. Run the pipeline to generate it.</p></div>`;
    return;
  }

  const { pitchers = [], seasons = [], min_starts } = pitcherData;

  // Compute regular-starter threshold: 35% of avg 2026 starts among active pitchers
  const _active2026 = pitchers.filter(p => p.seasons.includes(2026));
  const _avg2026 = _active2026.reduce((s, p) => s + (p.starts_2026 || 0), 0) / Math.max(_active2026.length, 1);
  const _regularThreshold = Math.floor(_avg2026 * 0.35); // e.g. floor(8.4 * 0.35) = 2

  // Today's scheduled starters + live team names from gamesData (refreshed on tab open)
  const _todaySpIds = new Set();
  const _liveTeam   = new Map(); // pitcher id → current team (handles mid-season trades)
  for (const g of (gamesData?.games || [])) {
    if (g.home_sp_id) { _todaySpIds.add(g.home_sp_id); _liveTeam.set(g.home_sp_id, g.home_team); }
    if (g.away_sp_id) { _todaySpIds.add(g.away_sp_id); _liveTeam.set(g.away_sp_id, g.away_team); }
  }

  const fmtRoi    = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
  const fmtWr     = v => v == null ? '—' : (v * 100).toFixed(1) + '%';
  const roiCls    = v => v == null ? '' : v >= 30 ? 'pv-exceptional' : v >= 0 ? 'seg-pos' : 'seg-neg';
  const baseRoiCls = v => v == null ? '' : v >= 0 ? 'seg-pos' : 'seg-neg';

  function _renderTable() {
    const search = _pvSearch.trim().toLowerCase();
    let filtered = pitchers.filter(p => p.ml.n >= _pvMinStarts && p.seasons.includes(2026));
    if (_pvRegularOnly) filtered = filtered.filter(p => (p.starts_2026 || 0) > _regularThreshold);
    if (search) filtered = filtered.filter(p => p.name.toLowerCase().includes(search));

    const colKey = _pvSort.col;
    filtered.sort((a, b) => {
      let av, bv;
      if (colKey === 'name')     { av = a.name;           bv = b.name; return _pvSort.dir * av.localeCompare(bv); }
      if (colKey === 'team')     { av = a.team;           bv = b.team; return _pvSort.dir * av.localeCompare(bv); }
      if (colKey === 'starts')    { av = a.ml.n;              bv = b.ml.n; }
      if (colKey === 'starts_26') { av = a.starts_2026 ?? 0;  bv = b.starts_2026 ?? 0; }
      if (colKey === 'ml_roi')   { av = a.ml.roi_pct;     bv = b.ml.roi_pct; }
      if (colKey === 'ml_wr')    { av = a.ml.win_rate;    bv = b.ml.win_rate; }
      if (colKey === 'h_roi')    { av = a.ml.home.roi_pct;  bv = b.ml.home.roi_pct; }
      if (colKey === 'a_roi')    { av = a.ml.away.roi_pct;  bv = b.ml.away.roi_pct; }
      if (colKey === 'un_roi')   { av = a.under.roi_pct;  bv = b.under.roi_pct; }
      if (colKey === 'un_wr')    { av = a.under.win_rate; bv = b.under.win_rate; }
      av = av ?? -999; bv = bv ?? -999;
      return _pvSort.dir * (av - bv);
    });

    const rows = filtered.map(p => {
      const star     = (p.ml.roi_pct >= 5 && p.ml.n >= 30) ? '<span class="pv-star" title="Consistent edge: ML ROI ≥ +5% with 30+ starts">★</span>' : '';
      const todayCls = _todaySpIds.has(p.id) ? ' pv-row-today' : '';
      const teamName = _liveTeam.get(p.id) || p.team;
      const pvSlug   = TEAM_LOGO[teamName];
      const pvLogoHtml = pvSlug
        ? `<img src="https://a.espncdn.com/combiner/i?img=/i/teamlogos/mlb/500/${pvSlug}.png&h=32&w=32" width="14" height="14" alt="${abbrev(teamName)}" class="pv-team-logo" onerror="this.style.display='none'">`
        : '';
      return `<tr class="${todayCls}">
        <td class="pv-name">${star}${p.name}</td>
        <td class="pv-team">${pvLogoHtml}${abbrev(teamName)}</td>
        <td class="pv-n">${p.ml.n}</td>
        <td class="pv-n">${p.starts_2026 ?? 0}</td>
        <td class="pv-roi ${baseRoiCls(p.ml.roi_pct)}">${fmtRoi(p.ml.roi_pct)}</td>
        <td class="pv-wr">${fmtWr(p.ml.win_rate)}</td>
        <td class="pv-roi ${roiCls(p.ml.home.roi_pct)}">${fmtRoi(p.ml.home.roi_pct)}</td>
        <td class="pv-roi ${roiCls(p.ml.away.roi_pct)}">${fmtRoi(p.ml.away.roi_pct)}</td>
        <td class="pv-roi ${roiCls(p.under.roi_pct)}">${fmtRoi(p.under.roi_pct)}</td>
        <td class="pv-wr">${fmtWr(p.under.win_rate)}</td>
      </tr>`;
    }).join('');

    document.getElementById('pv-tbody').innerHTML = rows || '<tr><td colspan="10" class="pv-empty">No pitchers match filters.</td></tr>';
    document.getElementById('pv-count').textContent = `${filtered.length} pitchers`;
  }

  function _thClick(col) {
    if (_pvSort.col === col) _pvSort.dir *= -1;
    else { _pvSort.col = col; _pvSort.dir = -1; }
    document.querySelectorAll('.pv-th').forEach(th => {
      const isSorted = th.dataset.col === col;
      th.classList.toggle('pv-th-asc', isSorted && _pvSort.dir === 1);
      th.classList.toggle('pv-th-desc', isSorted && _pvSort.dir === -1);
    });
    _renderTable();
  }

  const cols = [
    { col: 'name',   label: 'Pitcher' },
    { col: 'team',   label: 'Team' },
    { col: 'starts',    label: 'GS' },
    { col: 'starts_26', label: '2026 GS' },
    { col: 'ml_roi',    label: 'ML ROI' },
    { col: 'ml_wr',  label: 'ML Win%' },
    { col: 'h_roi',  label: 'Home ROI' },
    { col: 'a_roi',  label: 'Away ROI' },
    { col: 'un_roi', label: 'Under ROI' },
    { col: 'un_wr',  label: 'Under Win%' },
  ];

  const thead = cols.map(c => {
    const active = _pvSort.col === c.col;
    const cls = ['pv-th', active ? (_pvSort.dir === -1 ? 'pv-th-desc' : 'pv-th-asc') : ''].join(' ');
    return `<th class="${cls}" data-col="${c.col}">${c.label}</th>`;
  }).join('');

  el.innerHTML = `
    <div class="pv-wrap">
      <div class="pv-header">
        <div class="bt-narrative-title">Pitcher Value</div>
        <div class="bt-narrative-sub">Historical ROI betting team ML or UNDER · ${seasons.join(', ')} · Pinnacle closing lines</div>
      </div>
      <p class="bt-section-intro">
        Each row shows what would have happened if you flat-bet $1 on the pitcher's team to win (ML ROI) or bet the
        game UNDER every time they started (Under ROI). <strong>★</strong> marks pitchers with
        sustained ML edge — ≥5% ROI over 30+ starts. Sort any column to surface the strongest edges.
      </p>
      <div class="pv-controls">
        <input type="text" id="pv-search" class="pv-search" placeholder="Search pitcher…" value="${_pvSearch}">
        <div class="pv-toggle" role="group" aria-label="Min career starts">
          <button class="pv-toggle-btn${_pvMinStarts === 20 ? ' active' : ''}" data-pv-min="20">20+ GS</button>
          <button class="pv-toggle-btn${_pvMinStarts === 40 ? ' active' : ''}" data-pv-min="40">40+ GS</button>
          <button class="pv-toggle-btn${_pvMinStarts === 60 ? ' active' : ''}" data-pv-min="60">60+ GS</button>
        </div>
        <div class="pv-toggle" role="group" aria-label="Starter type">
          <button class="pv-toggle-btn${_pvRegularOnly ? ' active' : ''}" data-pv="regular">Regular Starter</button>
          <button class="pv-toggle-btn${!_pvRegularOnly ? ' active' : ''}" data-pv="all">All Starters</button>
        </div>
        <span id="pv-count" class="pv-count"></span>
      </div>
      <div class="pv-table-wrap">
        <table class="pv-table">
          <thead><tr>${thead}</tr></thead>
          <tbody id="pv-tbody"></tbody>
        </table>
      </div>
      <div class="bt-signal-note">GS = career games started with Pinnacle closing lines available. 2026 GS = starts this season. Regular Starter = above 35% of the 2026 league-average start count (threshold: >${_regularThreshold} starts). ROI = profit per $1 risked.</div>
    </div>`;

  // Single delegated listener on the persistent container — survives innerHTML re-renders
  el.addEventListener('click', e => {
    const th = e.target.closest('.pv-th[data-col]');
    if (th) { _thClick(th.dataset.col); return; }

    const minBtn = e.target.closest('.pv-toggle-btn[data-pv-min]');
    if (minBtn) {
      _pvMinStarts = parseInt(minBtn.dataset.pvMin);
      el.querySelectorAll('.pv-toggle-btn[data-pv-min]').forEach(b =>
        b.classList.toggle('active', b.dataset.pvMin === minBtn.dataset.pvMin));
      _renderTable();
      return;
    }

    const typeBtn = e.target.closest('.pv-toggle-btn[data-pv]');
    if (typeBtn) {
      _pvRegularOnly = typeBtn.dataset.pv === 'regular';
      el.querySelectorAll('.pv-toggle-btn[data-pv]').forEach(b =>
        b.classList.toggle('active', b.dataset.pv === typeBtn.dataset.pv));
      _renderTable();
    }
  });
  el.addEventListener('input', e => {
    if (e.target.id === 'pv-search') { _pvSearch = e.target.value; _renderTable(); }
  });

  _renderTable();
}

// ── Monte Carlo Simulation Tab ────────────────────────────────────────────────

// 2024 MLB league averages — fallbacks when per-player stats are null
const MC_LEAGUE = {
  k_pct: 0.224, bb_pct: 0.084,
  hr_per_bip: 0.032, babip: 0.299,
  single_of_hit: 0.535, double_of_hit: 0.285, triple_of_hit: 0.018,
  pitcher_k_pct: 0.224, pitcher_bb_pct: 0.084,
  xera_avg: 4.15,
};

// Estimate K%/BB% from xERA when season stats unavailable.
// Slopes calibrated to real MLB distributions (~2.8pp K% per run of xERA; ~1.4pp BB%).
function mcXeraToSpStats(xera) {
  if (xera == null) return { k_pct: MC_LEAGUE.pitcher_k_pct, bb_pct: MC_LEAGUE.pitcher_bb_pct };
  const d = xera - MC_LEAGUE.xera_avg;
  return {
    k_pct:  mcClamp(MC_LEAGUE.pitcher_k_pct  - d * 0.028, 0.10, 0.35),
    bb_pct: mcClamp(MC_LEAGUE.pitcher_bb_pct + d * 0.014, 0.03, 0.18),
  };
}

// Adjust effective pitcher_score for recent form (last_start_dev = ERA − xERA over last 3 starts).
// Negative deviation = hot (ERA below xERA) → raise effective score → lower BABIP allowed.
// Uses ~60% of the primary model's progressive weights to avoid over-trusting short streaks.
function mcFormAdjPs(score, dev) {
  if (dev == null || score == null) return score;
  const a = Math.abs(dev);
  const w = a < 1.0 ? 0.030 : a < 2.0 ? 0.060 : 0.080;
  return mcClamp(score - dev * w, 0.10, 0.90);
}

// Log5 formula: combine batter rate + pitcher rate relative to league average
function mcLog5(b, p, L) {
  if (!L || L <= 0 || L >= 1) return Math.max(0.01, Math.min(0.98, (b + p) / 2));
  const num = b * p / L;
  const den = num + (1 - b) * (1 - p) / (1 - L);
  return Math.max(0.01, Math.min(0.98, num / den));
}

// Sample from normal distribution using Box-Muller
function mcRandn(mean, std) {
  const u = Math.random(), v = Math.random();
  const n = Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  return mean + std * n;
}

// Clamp a value between lo and hi
function mcClamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

// Derive HR rate per PA from batter's barrel_pct or hard_hit_pct proxy
function mcHrRate(batter, parkFactor) {
  const pf = (parkFactor || 100) / 100;
  if (batter.barrel_pct != null)   return mcClamp(batter.barrel_pct  * 0.25 * pf, 0.005, 0.07);
  if (batter.hard_hit_pct != null) return mcClamp(batter.hard_hit_pct * 0.06 * pf, 0.005, 0.06);
  return MC_LEAGUE.hr_per_bip * pf;
}

// Advance baserunner state (8-state bitmask: bit0=1B, bit1=2B, bit2=3B)
// Returns { state, outs, runs }
function mcAdvance(state, outs, outcome) {
  let runs = 0;
  const on1 = !!(state & 1), on2 = !!(state & 2), on3 = !!(state & 4);
  switch (outcome) {
    case 'K':
      return { state, outs: outs + 1, runs: 0 };
    case 'OUT':
      // flyout — runner on 3rd scores (sac fly), others hold
      runs += on3 ? 1 : 0;
      return { state: state & ~4, outs: outs + 1, runs };
    case 'BB': {
      // force advance only if bases are loaded or base being forced
      let ns = state;
      if (on1 && on2) { runs += on3 ? 1 : 0; ns = 0b111 & ~4 | (on3 ? 0 : 4); }
      else if (on1)   { ns = state | 2; }
      else             { ns = state | 1; }
      ns = ns | 1; // batter to 1st
      return { state: ns & 0b111, outs, runs };
    }
    case 'S': {
      // single: batter to 1B; runner on 3B scores; 2B to 3B; 1B to 2B (50% chance scores from 2B)
      runs += on3 ? 1 : 0;
      const score2 = on2 && Math.random() < 0.50;
      runs += score2 ? 1 : 0;
      let ns = 1; // batter on 1B
      if (on1) ns |= 2;              // prev 1B to 2B
      if (on2 && !score2) ns |= 4;   // prev 2B to 3B if didn't score
      return { state: ns & 0b111, outs, runs };
    }
    case 'D': {
      // double: batter to 2B; all runners on 2B/3B score; runner on 1B to 3B
      runs += on3 ? 1 : 0;
      runs += on2 ? 1 : 0;
      let ns = 2; // batter on 2B
      if (on1) ns |= 4;
      return { state: ns & 0b111, outs, runs };
    }
    case 'T': {
      // triple: batter to 3B; all runners score
      runs += on1 ? 1 : 0;
      runs += on2 ? 1 : 0;
      runs += on3 ? 1 : 0;
      return { state: 4, outs, runs };
    }
    case 'HR': {
      runs += 1 + (on1 ? 1 : 0) + (on2 ? 1 : 0) + (on3 ? 1 : 0);
      return { state: 0, outs, runs };
    }
    default:
      return { state, outs: outs + 1, runs: 0 };
  }
}

// Simulate one half-inning. Returns runs scored.
function mcHalfInning(lineup, lineupPos, pitcher, isRelief, parkFactor) {
  let outs = 0, runs = 0, state = 0;
  let pos = lineupPos;

  while (outs < 3) {
    const batter = lineup[pos % lineup.length];
    pos++;

    // Apply per-batter and per-pitcher performance noise (pre-sampled per sim)
    const bK  = mcClamp((batter.k_pct  ?? MC_LEAGUE.k_pct)  * batter._noise, 0.05, 0.55);
    const bBB = mcClamp((batter.bb_pct ?? MC_LEAGUE.bb_pct)  * batter._noise, 0.02, 0.30);
    const pK  = mcClamp((pitcher.k_pct ?? MC_LEAGUE.pitcher_k_pct) * pitcher._noise, 0.05, 0.55);
    const pBB = mcClamp((pitcher.bb_pct ?? MC_LEAGUE.pitcher_bb_pct) * pitcher._noise, 0.02, 0.30);

    const pStrikeout = mcLog5(bK, pK, MC_LEAGUE.k_pct);
    const pWalk      = mcLog5(bBB, pBB, MC_LEAGUE.bb_pct);
    const hrRate     = mcHrRate(batter, parkFactor) * pitcher._noise;

    // Effective BABIP: pitcher contact suppression (from pitcher_score) × batter contact quality (xwOBA)
    // xwOBA 0.318 = league avg; higher → better contact → higher BABIP; lower → worse contact → lower BABIP
    const pitcherBabipMult = pitcher._babip_mult ?? 1.0;
    const batterBabipMult  = batter.xwoba != null
      ? mcClamp(1 + 0.4 * (batter.xwoba / 0.318 - 1), 0.80, 1.20) : 1.0;
    const effectiveBabip   = MC_LEAGUE.babip * pitcherBabipMult * batterBabipMult;

    const roll = Math.random();
    let outcome;
    if (roll < pStrikeout) {
      outcome = 'K';
    } else if (roll < pStrikeout + pWalk) {
      outcome = 'BB';
    } else {
      const bipRoll      = Math.random();
      const hrThreshold  = hrRate / (1 - pStrikeout - pWalk + 0.001);
      // BABIP is the hit rate on non-HR balls in play — additive with HR, not competing
      const hitThreshold = hrThreshold + effectiveBabip * (1 - hrThreshold);
      if (bipRoll < hrThreshold) {
        outcome = 'HR';
      } else if (bipRoll < hitThreshold) {
        // non-HR hit — determine type
        const hitRoll = Math.random();
        if (hitRoll < MC_LEAGUE.single_of_hit)                                  outcome = 'S';
        else if (hitRoll < MC_LEAGUE.single_of_hit + MC_LEAGUE.double_of_hit)   outcome = 'D';
        else if (hitRoll < MC_LEAGUE.single_of_hit + MC_LEAGUE.double_of_hit + MC_LEAGUE.triple_of_hit) outcome = 'T';
        else outcome = 'S'; // fallback
      } else {
        outcome = 'OUT';
      }
    }

    const adv = mcAdvance(state, outs, outcome);
    state = adv.state; outs = adv.outs; runs += adv.runs;
  }

  return { runs, nextLineupPos: pos };
}

// Simulate one full game (9 innings each side). Returns { homeRuns, awayRuns }
function mcSimulateOneGame(game, scenario) {
  const parkFactor = game.park_run_factor || 100;
  const homeSp = game.home_sp || {}, awaySp = game.away_sp || {};
  const sig = game.prediction?.model_signals || {};
  const homeLineup = (game.home_lineup || []).filter(b => b.xwoba != null || b.hard_hit_pct != null);
  const awayLineup = (game.away_lineup || []).filter(b => b.xwoba != null || b.hard_hit_pct != null);

  // Fallback mini-lineup if no data (9 league-avg batters)
  const leagueAvgBatter = { xwoba: 0.318, k_pct: 0.224, bb_pct: 0.084, hard_hit_pct: 0.37 };
  const hl = homeLineup.length >= 3 ? homeLineup : Array(9).fill(leagueAvgBatter);
  const al = awayLineup.length >= 3 ? awayLineup : Array(9).fill(leagueAvgBatter);

  // Build pitcher stat objects from games.json SP season data
  const spNoise = () => mcClamp(mcRandn(1.0, 0.20), 0.5, 1.6);
  const bNoise  = () => mcClamp(mcRandn(1.0, 0.15), 0.5, 1.5);

  // pitcher_score (0–1, 0.5 = avg) captures xERA, whiff%, chase%, barrel% against —
  // all of which determine how many balls in play become hits (BABIP).
  // Higher score → better contact suppression → lower BABIP against this pitcher.
  const pitcherScoreToBabipMult = s =>
    s != null ? mcClamp(1 + 0.5 * (0.5 - s), 0.75, 1.25) : 1.0;

  // Bullpen: derive BABIP mult from bullpen xERA when available (league avg BP xERA ≈ 4.15)
  const bpXeraToBabipMult = xera =>
    xera != null ? mcClamp(1 + 0.4 * (xera - 4.15) / 4.15, 0.80, 1.20) : 1.0;

  // Directional K%/BB% blend: only adjust when actual rate and pitcher_score are misaligned
  // relative to league average. Fixes cases like a ground-ball pitcher with low K% but good
  // overall quality (pull K% up), or a weak pitcher with deceptively high K% (pull K% down).
  // Aligned cases (elite K% + high score, or low K% + weak score) are left unchanged.
  const directionalBlend = (rawK, rawBB, ps, blend = 0.40) => {
    if (ps == null) return { k: rawK, bb: rawBB };
    const kMisaligned  = (ps > 0.5) !== (rawK  > MC_LEAGUE.pitcher_k_pct);
    const bbMisaligned = (ps > 0.5) === (rawBB > MC_LEAGUE.pitcher_bb_pct);
    return {
      k:  kMisaligned  ? rawK  * (1 - blend) + MC_LEAGUE.pitcher_k_pct  * (ps / 0.5)                * blend : rawK,
      bb: bbMisaligned ? rawBB * (1 - blend) + MC_LEAGUE.pitcher_bb_pct * (0.5 / Math.max(ps, 0.15)) * blend : rawBB,
    };
  };

  // Effective pitcher scores adjusted for recent form (ERA vs xERA over last 3 starts)
  const homePsEff = mcFormAdjPs(sig.pitcher_score_home, sig.last_start_dev_home);
  const awayPsEff = mcFormAdjPs(sig.pitcher_score_away, sig.last_start_dev_away);

  // xERA-derived fallbacks replace league-average when season K%/BB% are unavailable
  const homeXeraFb = mcXeraToSpStats(homeSp.season?.xera);
  const awayXeraFb = mcXeraToSpStats(awaySp.season?.xera);

  const homeSpBlend = directionalBlend(
    homeSp.season?.k_pct  ?? homeXeraFb.k_pct,
    homeSp.season?.bb_pct ?? homeXeraFb.bb_pct,
    homePsEff,
  );
  const homeSpStats = {
    k_pct:       homeSpBlend.k,
    bb_pct:      homeSpBlend.bb,
    _babip_mult: pitcherScoreToBabipMult(homePsEff),
    _noise:      spNoise(),
  };
  const awaySpBlend = directionalBlend(
    awaySp.season?.k_pct  ?? awayXeraFb.k_pct,
    awaySp.season?.bb_pct ?? awayXeraFb.bb_pct,
    awayPsEff,
  );
  const awaySpStats = {
    k_pct:       awaySpBlend.k,
    bb_pct:      awaySpBlend.bb,
    _babip_mult: pitcherScoreToBabipMult(awayPsEff),
    _noise:      spNoise(),
  };

  // Bullpen fallback to SP stats if no bullpen data
  const homeBpStats = {
    k_pct:       sig.bullpen_k_pct_home  ?? homeSpStats.k_pct * 0.9,
    bb_pct:      sig.bullpen_bb_pct_home ?? homeSpStats.bb_pct * 1.1,
    _babip_mult: bpXeraToBabipMult(sig.bullpen_xera_home),
    _noise:      spNoise(),
  };
  const awayBpStats = {
    k_pct:       sig.bullpen_k_pct_away  ?? awaySpStats.k_pct * 0.9,
    bb_pct:      sig.bullpen_bb_pct_away ?? awaySpStats.bb_pct * 1.1,
    _babip_mult: bpXeraToBabipMult(sig.bullpen_xera_away),
    _noise:      spNoise(),
  };

  // Apply scenario modifier to home or away SP (preserve _babip_mult)
  const applyScenario = (spStats) => {
    const s = { ...spStats };
    if (scenario === 'cold_sp_home' || scenario === 'cold_sp_away') {
      s.k_pct  = s.k_pct  * 0.78;
      s.bb_pct = s.bb_pct * 1.35;
    } else if (scenario === 'sharp_sp_home' || scenario === 'sharp_sp_away') {
      s.k_pct  = s.k_pct  * 1.22;
      s.bb_pct = s.bb_pct * 0.75;
    }
    return s;
  };

  const effectiveHomeSp = (scenario === 'cold_sp_home' || scenario === 'sharp_sp_home')
    ? applyScenario(homeSpStats) : homeSpStats;
  const effectiveAwaySp = (scenario === 'cold_sp_away' || scenario === 'sharp_sp_away')
    ? applyScenario(awaySpStats) : awaySpStats;

  // Assign per-batter noise once per sim
  const withNoise = lineup => lineup.map(b => ({ ...b, _noise: bNoise() }));
  const homeL = withNoise(hl);
  const awayL = withNoise(al);

  // SP pitches ~24 PAs (randomized); bullpen after
  const spThreshold = Math.round(24 + (Math.random() - 0.5) * 6);

  function simTeamInnings(lineup, sp, bp, isAway) {
    let runs = 0, pos = 0, totalPa = 0;
    for (let inn = 0; inn < 9; inn++) {
      const pitcher = totalPa < spThreshold ? sp : bp;
      const result = mcHalfInning(lineup, pos, pitcher, totalPa >= spThreshold, parkFactor);
      runs += result.runs;
      pos = result.nextLineupPos % lineup.length;
      totalPa += 3; // approximate: 3 outs per inning
    }
    return runs;
  }

  // Away lineup bats against HOME SP/BP; home lineup bats against AWAY SP/BP
  const awayRuns = simTeamInnings(awayL, effectiveHomeSp, homeBpStats, true);
  const homeRuns = simTeamInnings(homeL, effectiveAwaySp, awayBpStats, false);

  return { homeRuns, awayRuns };
}

// Run N simulations and return aggregated results
function mcSimulateGame(game, nSims, scenario) {
  scenario = scenario || 'normal';
  const homeScores = [], awayScores = [];

  for (let i = 0; i < nSims; i++) {
    const { homeRuns, awayRuns } = mcSimulateOneGame(game, scenario);
    homeScores.push(homeRuns);
    awayScores.push(awayRuns);
  }

  const homeWins = homeScores.filter((h, i) => h > awayScores[i]).length;
  const rawHomeWinPct = homeWins / nSims;

  // Apply weather and rest modifiers — model uses logit += weather*0.2 + rest; at p≈0.5, dp≈dlogit*0.25
  const msig = game.prediction?.model_signals || {};
  const weatherAdj = (msig.weather_modifier ?? 0) * 0.2 * 0.25;
  const restAdj    = (msig.rest_modifier    ?? 0) * 0.25;
  const homeWinPct = Math.max(0.05, Math.min(0.95, rawHomeWinPct + weatherAdj + restAdj));

  // 95% CI using Wilson score interval for proportions
  const z = 1.96;
  const ci = z * Math.sqrt((homeWinPct * (1 - homeWinPct)) / nSims);

  // Over/under probability
  const ouLine = game.odds?.total;
  const overProb = ouLine != null
    ? homeScores.filter((h, i) => (h + awayScores[i]) > ouLine).length / nSims
    : null;

  // Score distributions (0–9+ buckets)
  function buildDist(scores) {
    const counts = Array(11).fill(0);
    scores.forEach(s => counts[Math.min(s, 10)]++);
    return counts.map((c, i) => ({ runs: i === 10 ? '10+' : i, pct: c / nSims }));
  }

  // Most likely specific score
  const scoreCounts = {};
  homeScores.forEach((h, i) => {
    const key = `${h}-${awayScores[i]}`;
    scoreCounts[key] = (scoreCounts[key] || 0) + 1;
  });
  const modeEntry = Object.entries(scoreCounts).sort((a, b) => b[1] - a[1])[0];
  const [modeHome, modeAway] = modeEntry[0].split('-').map(Number);
  const modePct = modeEntry[1] / nSims;

  // Median and mean scores
  const sorted = [...homeScores].sort((a, b) => a - b);
  const medianHome = sorted[Math.floor(nSims / 2)];
  const meanHome = homeScores.reduce((s, v) => s + v, 0) / nSims;
  const sortedAway = [...awayScores].sort((a, b) => a - b);
  const medianAway = sortedAway[Math.floor(nSims / 2)];
  const meanAway = awayScores.reduce((s, v) => s + v, 0) / nSims;

  return {
    homeWinPct: Math.round(homeWinPct * 1000) / 10,
    awayWinPct: Math.round((1 - homeWinPct) * 1000) / 10,
    winCI: Math.round(ci * 1000) / 10,
    overProb: overProb != null ? Math.round(overProb * 1000) / 10 : null,
    underProb: overProb != null ? Math.round((1 - overProb) * 1000) / 10 : null,
    homeDist: buildDist(homeScores),
    awayDist: buildDist(awayScores),
    mostLikelyHome: modeHome, mostLikelyAway: modeAway, modePct: Math.round(modePct * 1000) / 10,
    meanHome: meanHome.toFixed(1), meanAway: meanAway.toFixed(1),
    medianHome, medianAway,
    nSims,
  };
}

// Analyse what drives the gap between model and MC win probabilities.
// Returns { gap, drivers[] } where gap = MC home% − model home%.
// NOTE: MC now incorporates pitcher_score → BABIP, batter xwOBA → BABIP, and weather/rest.
// Remaining gap is primarily the model's Vegas market anchor vs MC's pure Statcast approach.
function mcBuildDrivers(game, r) {
  const pred   = game.prediction || {};
  const sig    = pred.model_signals || {};
  const modelHomePct = pred.home_win_pct != null ? pred.home_win_pct * 100 : null;
  if (modelHomePct == null) return { gap: 0, drivers: [] };

  const gap   = +(r.homeWinPct - modelHomePct).toFixed(1);
  const homeA = abbrev(game.home_team);
  const awayA = abbrev(game.away_team);
  const drivers = [];

  // Vegas market anchor — the primary structural reason model and MC diverge.
  // Model starts from market-implied probability; MC starts from pure Statcast.
  if (game.odds?.total != null) {
    drivers.push({ type: 'market', label: 'Vegas anchor',
      body: `Primary model uses market-implied odds (O/U ${game.odds.total}) as its baseline; MC is a pure Statcast simulation with no market information. This is the main expected source of any residual gap.` });
  }

  // SP quality: pitcher_score drives BABIP in MC; directional K% blend also applied when misaligned
  const pHome = sig.pitcher_score_home, pAway = sig.pitcher_score_away;
  const homeSp = game.home_sp || {}, awaySp = game.away_sp || {};
  const homeK = homeSp.season?.k_pct, awayK = awaySp.season?.k_pct;
  if (pHome != null && pAway != null) {
    const modelEdge = pHome - pAway;
    const favA = modelEdge >= 0 ? homeA : awayA;
    const babipEffect = Math.abs(modelEdge) >= 0.03;
    if (babipEffect) {
      // Check if directional K% blend was applied (K%/score misaligned)
      const LK = MC_LEAGUE.pitcher_k_pct;
      // Use xERA-derived estimates when season K% unavailable (same as simulation)
      const homeKeff = homeK ?? mcXeraToSpStats(homeSp.season?.xera).k_pct;
      const awayKeff = awayK ?? mcXeraToSpStats(awaySp.season?.xera).k_pct;
      const homePsEff = mcFormAdjPs(pHome, sig.last_start_dev_home);
      const awayPsEff = mcFormAdjPs(pAway, sig.last_start_dev_away);
      const homeKMisaligned = (homePsEff > 0.5) !== (homeKeff > LK);
      const awayKMisaligned = (awayPsEff > 0.5) !== (awayKeff > LK);
      const kSrc = (homeK == null || awayK == null) ? ' (xERA-estimated)' : '';
      const kNote = `, K-rate ${(Math.max(homeKeff,awayKeff)*100).toFixed(0)}% vs ${(Math.min(homeKeff,awayKeff)*100).toFixed(0)}%${kSrc}`;
      let blendNote = '';
      if (homeKMisaligned || awayKMisaligned) {
        const who = homeKMisaligned && awayKMisaligned ? 'both SPs'
          : homeKMisaligned ? `${homeA} SP` : `${awayA} SP`;
        blendNote = `; K% corrected for ${who} (misaligned with pitcher_score)`;
      }
      drivers.push({ type: 'agree', label: 'SP quality',
        body: `${favA} has the stronger pitcher profile (score ${(Math.max(pHome,pAway)*100).toFixed(0)} vs ${(Math.min(pHome,pAway)*100).toFixed(0)})${kNote} — MC reflects this via BABIP adjustment${blendNote}` });
    }
  }

  // Lineup: MC now uses per-batter xwOBA to adjust BABIP — confirm alignment with lineup_score
  const homeL = (game.home_lineup || []).filter(b => b.xwoba != null);
  const awayL = (game.away_lineup || []).filter(b => b.xwoba != null);
  const lHome = sig.lineup_score_home, lAway = sig.lineup_score_away;
  if (homeL.length >= 3 && awayL.length >= 3 && lHome != null && lAway != null) {
    const mcHomeX = homeL.reduce((s, b) => s + b.xwoba, 0) / homeL.length;
    const mcAwayX = awayL.reduce((s, b) => s + b.xwoba, 0) / awayL.length;
    const xwobaEdge = mcHomeX - mcAwayX;
    const modelEdge = lHome - lAway;
    const agree = Math.sign(xwobaEdge) === Math.sign(modelEdge)
               || (Math.abs(xwobaEdge) < 0.012 && Math.abs(modelEdge) < 0.025);
    const favXwoba = xwobaEdge >= 0 ? homeA : awayA;
    const favModel = modelEdge >= 0 ? homeA : awayA;
    if (Math.abs(xwobaEdge) >= 0.015 || Math.abs(modelEdge) >= 0.03) {
      if (agree) {
        const bigX = Math.max(mcHomeX, mcAwayX);
        drivers.push({ type: 'agree', label: 'Lineup strength',
          body: `Both inputs favor ${favXwoba} offense — avg xwOBA .${(bigX*1000).toFixed(0)} vs .${(Math.min(mcHomeX,mcAwayX)*1000).toFixed(0)}, model lineup score ${(Math.max(lHome,lAway)*100).toFixed(0)} vs ${(Math.min(lHome,lAway)*100).toFixed(0)} — per-batter xwOBA now drives MC's BABIP` });
      } else {
        drivers.push({ type: 'disagree', label: 'Lineup strength',
          body: `MC avg xwOBA favors ${favXwoba} (.${(Math.max(mcHomeX,mcAwayX)*1000).toFixed(0)} vs .${(Math.min(mcHomeX,mcAwayX)*1000).toFixed(0)}); model lineup score favors ${favModel} (${(Math.max(lHome,lAway)*100).toFixed(0)} vs ${(Math.min(lHome,lAway)*100).toFixed(0)}) — model also weights xSLG, barrel%, and avg EV beyond xwOBA` });
      }
    }
  }

  // Recent SP form: ERA vs xERA deviation — now incorporated into MC via form-adjusted pitcher_score
  const devH = sig.last_start_dev_home, devA = sig.last_start_dev_away;
  if (devH != null && devA != null && Math.abs((devH||0) - (devA||0)) >= 1.0) {
    const homeFav = (devH||0) < (devA||0);
    const adjH = mcFormAdjPs(pHome, devH), adjA = mcFormAdjPs(pAway, devA);
    const adjDiff = Math.abs((adjH||0) - (adjA||0));
    const dType = adjDiff >= 0.03 ? 'agree' : 'model_only';
    const note  = adjDiff >= 0.03
      ? `MC adjusts BABIP via effective score (${adjH?.toFixed(2)} vs ${adjA?.toFixed(2)}); model uses progressive form weights`
      : `Small form signal — MC applies minor BABIP adjustment; model weight is stronger`;
    drivers.push({ type: dType, label: 'SP recent form',
      body: `${homeA} SP: ${devH > 0 ? '+' : ''}${devH?.toFixed(1)} ERA-vs-xERA; ${awayA} SP: ${devA > 0 ? '+' : ''}${devA?.toFixed(1)} — ${note}` });
  }

  return { gap, drivers };
}

// Render the model vs MC comparison block below the win strip.
function mcRenderComparison(game, r) {
  const pred         = game.prediction || {};
  const modelRaw     = pred.home_win_pct;
  if (modelRaw == null) return '';

  const modelHomePct = Math.round(modelRaw * 1000) / 10;
  const modelAwayPct = Math.round((1 - modelRaw) * 1000) / 10;
  const homeA = abbrev(game.home_team);
  const awayA = abbrev(game.away_team);

  const { gap, drivers } = mcBuildDrivers(game, r);
  const absGap  = Math.abs(gap);
  const gapSign = gap > 0 ? '+' : '';
  const gapCls  = absGap < 1.5 ? 'neutral' : gap > 0 ? 'pos' : 'neg';
  const gapText = absGap < 2.5 ? 'Closely aligned' : `MC ${gapSign}${gap.toFixed(1)}pp vs model`;

  const driverRows = drivers.length === 0
    ? `<div class="sim-driver-empty">Simulation closely tracks the model — no material divergence.</div>`
    : drivers.map(d => {
        const tagCls  = d.type === 'agree'      ? 'sim-dtag-agree'
                      : d.type === 'disagree'   ? 'sim-dtag-diverge'
                      : d.type === 'market'     ? 'sim-dtag-market'
                      : 'sim-dtag-model';
        const tagText = d.type === 'agree'      ? 'Aligned'
                      : d.type === 'disagree'   ? 'Diverges'
                      : d.type === 'market'     ? 'Market'
                      : 'Model only';
        return `<div class="sim-driver">
  <span class="sim-dtag ${tagCls}">${tagText}</span>
  <span class="sim-driver-label">${d.label}:</span>
  <span class="sim-driver-body">${d.body}</span>
</div>`;
      }).join('');

  const driversSection = `
  <div class="sim-drivers-hdr">What's driving the gap</div>
  <div class="sim-drivers">${driverRows}</div>`;

  return `
<div class="sim-compare">
  <div class="sim-compare-hdr">Model vs Simulation</div>
  <div class="sim-compare-row">
    <div class="sim-compare-col">
      <div class="sim-compare-sublabel">Primary Model</div>
      <div class="sim-compare-pcts">
        <span class="sim-cpct">${awayA} ${modelAwayPct}%</span><span class="sim-csep"> · </span><span class="sim-cpct">${homeA} ${modelHomePct}%</span>
      </div>
    </div>
    <div class="sim-compare-gap ${gapCls}">${gapText}</div>
    <div class="sim-compare-col sim-compare-right">
      <div class="sim-compare-sublabel">MC Simulation</div>
      <div class="sim-compare-pcts">
        <span class="sim-cpct">${awayA} ${r.awayWinPct}%</span><span class="sim-csep"> · </span><span class="sim-cpct">${homeA} ${r.homeWinPct}%</span>
      </div>
    </div>
  </div>
  ${driversSection}
</div>`;
}

// Render an SVG score distribution bar chart
function mcRenderChart(homeDist, awayDist, homeAbbr, awayAbbr) {
  const TEAM_COLORS = {
    ARI: '#A71930', ATL: '#CE1141', BAL: '#DF4601', BOS: '#BD3039',
    CHC: '#1E6BC5', CWS: '#C0C0C0', CIN: '#C6011F', CLE: '#D4182E',
    COL: '#8B5CF6', DET: '#FA7C2B', HOU: '#EB6E1F', KC:  '#C09A5B',
    LAA: '#CE1126', LAD: '#3788C7', MIA: '#00A3E0', MIL: '#FFC52F',
    MIN: '#D31145', NYM: '#FF5910', NYY: '#4A90D9', OAK: '#3EA843',
    PHI: '#E81828', PIT: '#FDB827', SD:  '#C8941B', SF:  '#FD5A1E',
    SEA: '#4DBDAF', STL: '#C41E3A', TB:  '#8FBCE6', TEX: '#2B6CB0',
    TOR: '#1B8FC8', WSH: '#AB0003',
  };
  const COLOR_FAMILY = {
    ARI: 'red',    ATL: 'red',    BAL: 'orange', BOS: 'red',
    CHC: 'blue',   CWS: 'silver', CIN: 'red',    CLE: 'red',
    COL: 'purple', DET: 'orange', HOU: 'orange', KC:  'gold',
    LAA: 'red',    LAD: 'blue',   MIA: 'blue',   MIL: 'gold',
    MIN: 'red',    NYM: 'orange', NYY: 'blue',   OAK: 'green',
    PHI: 'red',    PIT: 'gold',   SD:  'brown',  SF:  'orange',
    SEA: 'teal',   STL: 'red',    TB:  'blue',   TEX: 'blue',
    TOR: 'blue',   WSH: 'red',
  };

  const homeColor = TEAM_COLORS[homeAbbr] || '#3b82f6';
  let awayColor   = TEAM_COLORS[awayAbbr] || '#34d399';
  if ((COLOR_FAMILY[homeAbbr] || homeColor) === (COLOR_FAMILY[awayAbbr] || awayColor)) {
    awayColor = '#9ca3af';
  }

  const VW = 400, VH = 170;
  const topPad = 34, bottomPad = 28, sidePad = 10;
  const chartH = VH - topPad - bottomPad;
  const maxPct = Math.max(...homeDist.map(d => d.pct), ...awayDist.map(d => d.pct), 0.001);
  const scale  = chartH / maxPct;
  const buckets = homeDist.length;
  const pairW  = (VW - sidePad * 2) / buckets;
  const barW   = Math.max(4, Math.floor(pairW * 0.40));
  const baseY  = topPad + chartH;

  let svg = '';

  // Legend — top-left
  svg += `<rect x="${sidePad}" y="10" width="10" height="10" fill="${homeColor}" rx="1"/>`;
  svg += `<text x="${sidePad + 14}" y="19" font-size="10" fill="#cbd5e1" font-weight="500">${homeAbbr}</text>`;
  svg += `<rect x="${sidePad + 52}" y="10" width="10" height="10" fill="${awayColor}" rx="1"/>`;
  svg += `<text x="${sidePad + 66}" y="19" font-size="10" fill="#cbd5e1" font-weight="500">${awayAbbr}</text>`;

  // Bars + labels
  for (let i = 0; i < buckets; i++) {
    const hd = homeDist[i], ad = awayDist[i];
    const hH  = Math.max(1, Math.round(hd.pct * scale));
    const aH  = Math.max(1, Math.round(ad.pct * scale));
    const x   = sidePad + i * pairW;
    const hX  = x;
    const aX  = x + barW + 2;
    const lbl = i < buckets - 1 ? String(hd.runs) : `${hd.runs}+`;

    svg += `<rect x="${hX.toFixed(1)}" y="${baseY - hH}" width="${barW}" height="${hH}" fill="${homeColor}" opacity="0.9" rx="1"><title>${homeAbbr} ${lbl} runs: ${(hd.pct*100).toFixed(1)}%</title></rect>`;
    if (hd.pct >= 0.06)
      svg += `<text x="${(hX + barW/2).toFixed(1)}" y="${baseY - hH - 4}" text-anchor="middle" font-size="9" fill="${homeColor}">${(hd.pct*100).toFixed(0)}%</text>`;

    svg += `<rect x="${aX.toFixed(1)}" y="${baseY - aH}" width="${barW}" height="${aH}" fill="${awayColor}" opacity="0.85" rx="1"><title>${awayAbbr} ${lbl} runs: ${(ad.pct*100).toFixed(1)}%</title></rect>`;
    if (ad.pct >= 0.06)
      svg += `<text x="${(aX + barW/2).toFixed(1)}" y="${baseY - aH - 4}" text-anchor="middle" font-size="9" fill="${awayColor}">${(ad.pct*100).toFixed(0)}%</text>`;

    svg += `<text x="${(x + pairW/2).toFixed(1)}" y="${baseY + 18}" text-anchor="middle" font-size="9" fill="#475569">${lbl}</text>`;
  }

  // Baseline
  svg += `<line x1="${sidePad}" y1="${baseY}" x2="${VW - sidePad}" y2="${baseY}" stroke="#334155" stroke-width="1"/>`;

  return `<svg viewBox="0 0 ${VW} ${VH}" width="100%" height="auto" class="sim-chart-svg">${svg}</svg>`;
}

// Render the Simulate tab
function renderSimulateView() {
  const el = document.getElementById('simulate-view');
  if (!el) return;
  const games = (gamesData?.games || []).filter(g => (g.game_status || 'preview') === 'preview');

  if (!games.length) {
    el.innerHTML = `<div class="sim-empty">No games scheduled today.</div>`;
    return;
  }

  const dateStr = gamesData?.date || '';

  const cards = games.map(g => {
    const pk = g.gamePk;
    const homeSp = g.home_sp?.name || 'TBD';
    const awaySp = g.away_sp?.name || 'TBD';
    const homeXera = g.home_sp?.season?.xera?.toFixed(2) ?? '—';
    const awayXera = g.away_sp?.season?.xera?.toFixed(2) ?? '—';
    const ouLine = g.odds?.total;
    const ouText = ouLine != null ? `O/U ${ouLine}` : 'No line';
    const timeStr = g.game_time_et || '';
    const homeAbbr = abbrev(g.home_team), awayAbbr = abbrev(g.away_team);

    return `
<div class="sim-card" id="sim-card-${pk}">
  <div class="sim-game-hdr">
    <div class="sim-matchup">
      <span class="sim-away">${awayAbbr}</span>
      <span class="sim-at">@</span>
      <span class="sim-home">${homeAbbr}</span>
    </div>
    <div class="sim-meta">${timeStr}</div>
    <div class="sim-pitchers">${awaySp} <span class="sim-xera">${awayXera}</span> vs ${homeSp} <span class="sim-xera">${homeXera}</span> xERA</div>
    <div class="sim-ou-pre">${ouText}</div>
  </div>
  <div class="sim-controls">
    <button class="sim-run-btn" onclick="mcRunSim(${pk})">&#9654; Run 100,000 Simulations</button>
  </div>
  <div class="sim-results" id="sim-results-${pk}" hidden></div>
</div>`;
  }).join('');

  el.innerHTML = `
<div class="sim-header">
  <div class="sim-title">Game Simulations</div>
  <div class="sim-subtitle">Full plate-appearance Monte Carlo · ${dateStr}</div>
</div>
${cards}`;
}

// Called when user clicks "Run Simulation" on a specific game
function mcRunSim(pk, scenario) {
  scenario = scenario || 'normal';
  const game = (gamesData?.games || []).find(g => g.gamePk === pk);
  if (!game) return;

  const btn = document.querySelector(`#sim-card-${pk} .sim-run-btn`);
  if (btn) { btn.textContent = 'Simulating…'; btn.disabled = true; }

  // Use setTimeout to allow the DOM to update before blocking JS runs
  setTimeout(() => {
    const result = mcSimulateGame(game, 100000, scenario);
    mcRenderResults(pk, game, result, scenario);
    if (btn) { btn.textContent = '↺ Re-run Simulation'; btn.disabled = false; }
  }, 16);
}

function mcRenderResults(pk, game, r, scenario) {
  const el = document.getElementById(`sim-results-${pk}`);
  if (!el) return;

  const homeAbbr = abbrev(game.home_team), awayAbbr = abbrev(game.away_team);
  const ouLine = game.odds?.total;

  const ouHtml = ouLine != null ? `
    <div class="sim-ou-row">
      <span class="sim-ou-label">O/U ${ouLine}:</span>
      <span class="sim-ou-val ${r.overProb > 52 ? 'over' : r.underProb > 52 ? 'under' : ''}"
        >OVER ${r.overProb}%</span>
      <span class="sim-ou-sep">/</span>
      <span class="sim-ou-val ${r.underProb > 52 ? 'under' : r.overProb > 52 ? 'over' : ''}"
        >UNDER ${r.underProb}%</span>
    </div>` : '';

  const compareHtml = mcRenderComparison(game, r);

  const scenarioLabels = {
    normal:       'Normal',
    cold_sp_home: `Cold SP (${homeAbbr})`,
    sharp_sp_home:`Sharp SP (${homeAbbr})`,
    cold_sp_away: `Cold SP (${awayAbbr})`,
    sharp_sp_away:`Sharp SP (${awayAbbr})`,
  };

  const scenarioBtns = Object.entries(scenarioLabels).map(([sc, lbl]) =>
    `<button class="sim-scenario-btn ${sc === scenario ? 'active' : ''}"
       onclick="mcRunSim(${pk}, '${sc}')">${lbl}</button>`
  ).join('');

  el.hidden = false;
  el.innerHTML = `
<div class="sim-win-strip">
  <div class="sim-win-side away">
    <div class="sim-win-pct">${r.awayWinPct}%</div>
    <div class="sim-win-label">${awayAbbr} wins</div>
  </div>
  <div class="sim-win-ci">±${r.winCI}%</div>
  <div class="sim-win-side home">
    <div class="sim-win-pct">${r.homeWinPct}%</div>
    <div class="sim-win-label">${homeAbbr} wins</div>
  </div>
</div>
${compareHtml}
<div class="sim-chart-section">
  <div class="sim-chart-title">Score distribution (${r.nSims.toLocaleString()} simulations)</div>
  <div class="sim-chart-wrap">
    ${mcRenderChart(r.homeDist, r.awayDist, homeAbbr, awayAbbr)}
  </div>
  <div class="sim-chart-note">
    Avg: ${homeAbbr} ${r.meanHome} — ${awayAbbr} ${r.meanAway} &nbsp;·&nbsp;
    Most likely: ${homeAbbr} ${r.mostLikelyHome}, ${awayAbbr} ${r.mostLikelyAway} (${r.modePct}%)
  </div>
</div>
${ouHtml}
<div class="sim-scenarios">
  <div class="sim-scenarios-label">Scenarios:</div>
  ${scenarioBtns}
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
  K_PROP:     { label: 'K',      color: '#7c3aed' },
  HR_PROP:    { label: 'HR',     color: '#e11d48' },
  HIT_PROP:   { label: 'HIT',    color: '#0284c7' },
  TB_PROP:    { label: 'TB',     color: '#0891b2' },
  TOTAL:      { label: 'TOT',    color: '#059669' },
  TEAM_TOTAL: { label: 'T-TOT',  color: '#047857' },
  F5_TOTAL:   { label: 'F5-TOT', color: '#0d9488' },
  MONEYLINE:  { label: 'ML',     color: '#b45309' },
  ML_F5:      { label: 'F5',     color: '#92400e' },
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
  const typeOrder = ['K_PROP','HR_PROP','HIT_PROP','TB_PROP','TOTAL','TEAM_TOTAL','F5_TOTAL','MONEYLINE','ML_F5'];
  const grouped = {};
  for (const p of (g.picks || []).filter(filterPick)) {
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
    F5_TOTAL: 'First 5 Inn. Total', MONEYLINE: 'Moneyline', ML_F5: 'First 5 Innings',
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

  const lineupWarnBadge = p.lineup_unconfirmed
    ? '<span class="data-quality-badge lineup-warn" title="Based on projected lineup — verify before betting">⚠ Projected lineup</span>'
    : '';

  const reasonsHtml = (p.reasons || []).map(r =>
    `<li class="pick-reason">${escapeHtml(r)}</li>`
  ).join('');

  const statsHtml  = renderPickStatsRow(p);
  const last5Html  = renderLast5Row(p);
  const oddsHtml   = renderPickOdds(p);

  const _PLAYER_PROP_TYPES = new Set(['K_PROP', 'WALK_PROP', 'HR_PROP', 'HIT_PROP', 'TB_PROP']);
  let teamBadgeHtml = '';
  if (_PLAYER_PROP_TYPES.has(p.bet_type) && p.subject_id) {
    teamBadgeHtml = _teamBadge(_playerTeamMap.get(p.subject_id) || '');
  } else if (p.bet_type === 'TEAM_TOTAL') {
    teamBadgeHtml = _teamBadge(p.subject);
  }

  const _pickFpCard = `${p.bet_type}:${p.subject_id ?? p._gamePk}:${p.direction}`;
  const _isPinned = parlayParams.locked.includes(_pickFpCard);
  const pinBtnHtml = p._gamePk
    ? `<button class="parlay-pin-btn${_isPinned ? ' active' : ''}" data-fp="${escapeHtml(_pickFpCard)}" title="${_isPinned ? 'Unpin from parlay' : 'Pin to parlay'}" onclick="event.stopPropagation();_togglePin(this.dataset.fp)">📌</button>`
    : '';

  return `
<div class="pick-card" style="--bet-color:${meta.color}">
  <div class="pick-card-top">
    <div class="pick-subject-row">
      <span class="bet-badge" style="--bet-color:${meta.color}">${meta.label}</span>
      ${teamBadgeHtml}
      <span class="pick-subject">${escapeHtml(p.subject)}</span>
      <span class="pick-dir ${dirCls}">${p.direction}</span>
      ${noLineup ? '<span class="data-quality-badge">Pitcher-only signal</span>' : ''}
      ${lineupWarnBadge}
      ${consensusBadge}
      ${pinBtnHtml}
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


// ── Parlay Generator ─────────────────────────────────────────────────────────

function getPickRawOdds(p) {
  const o = p.odds;
  if (!o || !o.has_line) return null;
  return (p.direction === 'OVER' || p.direction === 'HOME') ? (o.over_price ?? null) : (o.under_price ?? null);
}

function generateParlays(params, allPicks) {
  const { legs, risk, include, allowCorr, sgp } = params;

  // Step 1: filter by bet type — each type maps to exactly one include bucket.
  // Exclude lineup-dependent props where the lineup hasn't been confirmed yet;
  // those players may not start or have props available at sportsbooks.
  const _INC = new Set(
    include.includes('all')
      ? ['ml', 'total', 'f5total', 'hr', 'batter', 'pitcher']
      : include
  );
  let pool = allPicks.filter(p => {
    if (p.lineup_unconfirmed)                                     return false;
    if (p.bet_type === 'ML_F5')                                   return _INC.has('ml');
    if (p.bet_type === 'TOTAL' || p.bet_type === 'TEAM_TOTAL')    return _INC.has('total');
    if (p.bet_type === 'F5_TOTAL')                                return _INC.has('f5total');
    if (p.bet_type === 'HR_PROP')                                 return _INC.has('hr');
    if (p.bet_type === 'HIT_PROP' || p.bet_type === 'TB_PROP')    return _INC.has('batter');
    if (p.bet_type === 'K_PROP'   || p.bet_type === 'WALK_PROP')  return _INC.has('pitcher');
    return false;
  });

  // SGP: narrow pool to the user-selected game (or the one with the most picks)
  let sgpGame = null;
  if (sgp && pool.length) {
    if (params.sgpGamePk) {
      sgpGame = params.sgpGamePk;
    } else {
      const gameCounts = {};
      pool.forEach(p => { if (p._gamePk) gameCounts[p._gamePk] = (gameCounts[p._gamePk] || 0) + 1; });
      const topEntry = Object.entries(gameCounts).sort((a, b) => b[1] - a[1])[0];
      if (topEntry) sgpGame = +topEntry[0];
    }
    if (sgpGame) pool = pool.filter(p => p._gamePk === sgpGame);
  }

  // Step 2: annotate each pick with _prob and _decOdds
  pool = pool.map(p => {
    const prob = p.model_prob != null ? p.model_prob
      : 1 / (1 + Math.exp(-(( p.signal ?? 5) - 7.5) * 0.45));
    const rawOdds = getPickRawOdds(p);
    const hasOdds = rawOdds != null;
    const decOdds = hasOdds ? americanToDecimal(rawOdds)
      : (prob > 0.5 ? 1.85 : 2.10);
    return { ...p, _prob: prob, _decOdds: decOdds, _oddsEstimated: !hasOdds };
  });

  // Step 3: apply risk-level filter.
  // Low Risk requires each leg to be individually favored (>50% model prob).
  // Medium/High use signal floors since sub-50% legs may still carry positive EV.
  if (risk === 'low') {
    pool = pool.filter(p => (p._prob ?? 0) > 0.50);
  } else {
    const minSignal = { medium: 6.0, high: 5.5 }[risk] ?? 6.0;
    pool = pool.filter(p => (p.signal ?? 0) >= minSignal);
  }

  // Step 4: exhaustive combo search over top candidates for each objective.
  // Sorting candidates by the per-pick proxy for each objective ensures the
  // globally optimal parlay is found within the candidate window.
  //
  // Same-game legs are positively correlated (shared pitcher, conditions, etc.).
  // Apply a 10% probability discount per same-game leg pair so the optimizer
  // naturally avoids stacking correlated props from one game.
  function _corrFactor(combo) {
    const counts = {};
    combo.forEach(p => { if (p._gamePk) counts[p._gamePk] = (counts[p._gamePk] || 0) + 1; });
    const pairs = Object.values(counts).reduce((s, n) => s + n * (n - 1) / 2, 0);
    return Math.pow(0.90, pairs);
  }
  const evScore     = ls => ls.reduce((a, l) => a * l._prob * l._decOdds, 1) * _corrFactor(ls);
  const payoutScore = ls => ls.reduce((a, l) => a * l._decOdds, 1);
  const safestScore = ls => ls.reduce((a, l) => a * l._prob, 1) * _corrFactor(ls);

  const byEV     = [...pool].sort((a, b) => (b._prob * b._decOdds) - (a._prob * a._decOdds));
  const byPayout = [...pool].sort((a, b) => b._decOdds - a._decOdds);
  const bySafest = [...pool].sort((a, b) => b._prob - a._prob);

  const evResult     = _findBestCombo(byEV,     legs, evScore);
  const payoutResult = _findBestCombo(byPayout, legs, payoutScore);
  const safestResult = _findBestCombo(bySafest, legs, safestScore);

  const mkOption = (key, label, result) => ({
    key, label,
    legs: result.legs,
    stats: _computeParlayStats(result.legs),
    combosChecked: result.combosChecked,
    note: result.legs.length < legs
      ? `Only ${result.legs.length} leg${result.legs.length !== 1 ? 's' : ''} met your criteria today — showing ${result.legs.length}-leg parlay`
      : null,
  });

  return {
    ev:     mkOption('ev',     'Best EV',     evResult),
    payout: mkOption('payout', 'Best Payout', payoutResult),
    safest: mkOption('safest', 'Safest',      safestResult),
    sgpGame,
  };
}

function _findBestCombo(candidatePool, n, scoreFn) {
  // Exhaustive search over top-25 candidates for the combination that truly
  // maximises scoreFn (parlay-level product). Falls back to greedy when no
  // valid n-leg combo exists (e.g. diversity constraints block everything).
  const MAX_CANDS = 40;
  const cands = candidatePool.slice(0, MAX_CANDS);
  const m = cands.length;
  if (m === 0) return { legs: [], combosChecked: 0 };

  function isValid(combo) {
    const batters = new Set(), games = new Set(); let tt = 0;
    for (const p of combo) {
      if (p.subject_id && batters.has(p.subject_id))                          return false;
      if (p.bet_type === 'TEAM_TOTAL' && ++tt > 1)                            return false;
      if (!parlayParams.allowCorr && !parlayParams.sgp && p._gamePk && games.has(p._gamePk)) return false;
      if (p.subject_id) batters.add(p.subject_id);
      if (p._gamePk)    games.add(p._gamePk);
    }
    return true;
  }

  let bestScore = -Infinity;
  let bestCombo = [];
  let combosChecked = 0;

  function search(start, current) {
    if (current.length === n) {
      combosChecked++;
      if (isValid(current)) {
        const score = scoreFn(current);
        if (score > bestScore) { bestScore = score; bestCombo = [...current]; }
      }
      return;
    }
    if (m - start < n - current.length) return; // prune: not enough picks left
    for (let i = start; i < m; i++) {
      current.push(cands[i]);
      search(i + 1, current);
      current.pop();
    }
  }

  search(0, []);

  // Fallback: no valid n-leg combo found — greedy selection from candidate list
  if (bestCombo.length === 0) {
    const batters = new Set(), games = new Set(); let tt = 0;
    for (const p of cands) {
      if (bestCombo.length >= n) break;
      if (p.subject_id && batters.has(p.subject_id)) continue;
      if (p.bet_type === 'TEAM_TOTAL' && tt >= 1) continue;
      if (!parlayParams.allowCorr && !parlayParams.sgp && p._gamePk && games.has(p._gamePk)) continue;
      bestCombo.push(p);
      if (p.subject_id) batters.add(p.subject_id);
      if (p.bet_type === 'TEAM_TOTAL') tt++;
      if (p._gamePk) games.add(p._gamePk);
    }
  }

  // Tag same-game warnings
  const seenGames = new Set();
  const legs = bestCombo.map(p => {
    const warn = Boolean(p._gamePk && seenGames.has(p._gamePk));
    if (p._gamePk) seenGames.add(p._gamePk);
    return { ...p, _sameGameWarning: warn };
  });

  return { legs, combosChecked };
}

function _computeParlayStats(legs) {
  if (!legs.length) return { payout: 0, winPct: '0.0', hasEstimate: false, totalDecOdds: 1, corrAdj: false };
  let totalDecOdds = 1;
  let totalProb = 1;
  let hasEstimate = false;
  for (const leg of legs) {
    totalDecOdds *= leg._decOdds;
    totalProb    *= leg._prob;
    if (leg._oddsEstimated) hasEstimate = true;
  }
  // Apply same-game correlation discount (10% per same-game leg pair)
  const counts = {};
  legs.forEach(l => { if (l._gamePk) counts[l._gamePk] = (counts[l._gamePk] || 0) + 1; });
  const pairs = Object.values(counts).reduce((s, n) => s + n * (n - 1) / 2, 0);
  const corrAdj = pairs > 0;
  return {
    payout: Math.round((totalDecOdds - 1) * 100),
    winPct: (totalProb * Math.pow(0.90, pairs) * 100).toFixed(1),
    hasEstimate,
    totalDecOdds,
    corrAdj,
  };
}

function renderParlayView() {
  const view = document.getElementById('parlay-view');
  const ts = picksData?.generated_at ? `Updated ${formatGeneratedAt(picksData.generated_at)}` : '';

  const mkParamRow = (label, paramKey, options, currentVal) => {
    const btns = options.map(({ value, text }) =>
      `<button class="parlay-opt-btn${currentVal === value ? ' active' : ''}" data-param="${paramKey}" data-value="${value}">${text}</button>`
    ).join('');
    return `<div class="parlay-param-row"><span class="parlay-param-label">${label}</span><div class="parlay-param-options">${btns}</div></div>`;
  };

  const mkIncludeRow = () => {
    const inc = parlayParams.include;
    const isAll = inc.includes('all');
    const opts = [
      { value: 'ml',      text: 'Moneyline'    },
      { value: 'total',   text: 'Total'        },
      { value: 'f5total', text: 'F5 Total'     },
      { value: 'hr',      text: 'HR Props'     },
      { value: 'batter',  text: 'Batter Props' },
      { value: 'pitcher', text: 'Pitcher Props'},
      { value: 'all',     text: 'All'          },
    ];
    const btns = opts.map(({ value, text }) => {
      const active = value === 'all' ? isAll : (!isAll && inc.includes(value));
      return `<button class="parlay-opt-btn${active ? ' active' : ''}" data-param="include" data-value="${value}">${text}</button>`;
    }).join('');
    return `<div class="parlay-param-row"><span class="parlay-param-label">Include</span><div class="parlay-param-options parlay-include-options">${btns}</div></div>`;
  };

  const mkCorrRow = () => {
    if (parlayParams.sgp) return '';
    return mkParamRow('Correlated Legs', 'allowCorr',
      [{value:'true',text:'Yes'},{value:'false',text:'No'}],
      String(parlayParams.allowCorr));
  };

  const mkSgpRow = () => {
    const toggleRow = mkParamRow('Same-Game Parlay', 'sgp',
      [{value:'false',text:'Off'},{value:'true',text:'On'}],
      String(parlayParams.sgp));
    if (!parlayParams.sgp || !picksData?.games?.length) return toggleRow;

    // Build per-game pick counts so the user can see which games have the most coverage
    const pickCounts = {};
    for (const g of picksData.games) {
      pickCounts[g.gamePk] = (g.picks || []).length;
    }
    const gameOpts = picksData.games
      .filter(g => g.gamePk && pickCounts[g.gamePk] > 0)
      .sort((a, b) => (pickCounts[b.gamePk] || 0) - (pickCounts[a.gamePk] || 0))
      .map(g => {
        const lbl = `${abbrev(g.away_team)} @ ${abbrev(g.home_team)}`;
        const cnt = pickCounts[g.gamePk] || 0;
        const isActive = parlayParams.sgpGamePk === g.gamePk ||
          (parlayParams.sgpGamePk == null && g === picksData.games
            .filter(x => x.gamePk && pickCounts[x.gamePk] > 0)
            .sort((a, b) => (pickCounts[b.gamePk] || 0) - (pickCounts[a.gamePk] || 0))[0]);
        return `<button class="parlay-opt-btn parlay-sgp-game-btn${isActive ? ' active' : ''}" data-param="sgpGamePk" data-value="${g.gamePk}">${escapeHtml(lbl)}<span class="parlay-sgp-pick-count">${cnt}</span></button>`;
      }).join('');
    const gameRow = `<div class="parlay-param-row"><span class="parlay-param-label">Game</span><div class="parlay-param-options parlay-include-options">${gameOpts}</div></div>`;
    return toggleRow + gameRow;
  };

  const mkLockedStrip = () => {
    if (!parlayParams.locked.length) return '';
    const chips = parlayParams.locked.map(fp => {
      const parts = fp.split(':');
      const label = `${parts[0]} ${parts[2] || ''}`.trim();
      return `<span class="parlay-locked-chip">${escapeHtml(label)}<button class="parlay-locked-remove" data-fp="${escapeHtml(fp)}">✕</button></span>`;
    }).join('');
    return `<div class="parlay-locked-strip"><span class="parlay-locked-label">📌 Locked:</span>${chips}</div>`;
  };

  view.innerHTML = `
<div class="view-header">
  <h1>Parlay Generator</h1>
  <span class="sub-label">${ts || 'Build the day\'s best parlay from model picks'}</span>
</div>
<div class="parlay-params">
  ${mkParamRow('Legs', 'legs', [{value:'2',text:'2'},{value:'3',text:'3'},{value:'4',text:'4'},{value:'5',text:'5'}], String(parlayParams.legs))}
  ${mkParamRow('Risk Level', 'risk', [{value:'low',text:'Low'},{value:'medium',text:'Medium'},{value:'high',text:'High'}], parlayParams.risk)}
  ${mkIncludeRow()}
  ${mkCorrRow()}
  ${mkSgpRow()}
  ${mkLockedStrip()}
  <button class="parlay-generate-btn">Generate Parlay</button>
</div>
<div id="parlay-results" class="parlay-results">
  <div class="empty-state">Configure parameters above and tap Generate.</div>
</div>`;

  view.querySelectorAll('.parlay-opt-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const param = btn.dataset.param;
      const val   = btn.dataset.value;

      if (param === 'include') {
        if (val === 'all') {
          parlayParams.include = ['all'];
        } else {
          const curr = parlayParams.include;
          if (curr.includes('all')) {
            parlayParams.include = [val];
          } else if (curr.includes(val)) {
            const next = curr.filter(v => v !== val);
            parlayParams.include = next.length ? next : ['all'];
          } else {
            parlayParams.include = [...curr, val];
          }
        }
        const isAll = parlayParams.include.includes('all');
        view.querySelectorAll('.parlay-opt-btn[data-param="include"]').forEach(b => {
          const bv = b.dataset.value;
          b.classList.toggle('active', bv === 'all' ? isAll : (!isAll && parlayParams.include.includes(bv)));
        });
        return;
      }

      if (param === 'legs') parlayParams.legs = parseInt(val, 10);
      if (param === 'risk') parlayParams.risk = val;
      if (param === 'allowCorr') parlayParams.allowCorr = val === 'true';
      if (param === 'sgp') {
        parlayParams.sgp = val === 'true';
        parlayParams.sgpGamePk = null; // reset game selection on toggle
        // Re-render so Correlated Legs row and game picker show/hide correctly
        renderParlayView();
        return;
      }
      if (param === 'sgpGamePk') {
        parlayParams.sgpGamePk = parseInt(val, 10);
        view.querySelectorAll('.parlay-sgp-game-btn').forEach(b =>
          b.classList.toggle('active', b.dataset.value === val));
        return;
      }
      view.querySelectorAll(`.parlay-opt-btn[data-param="${param}"]`).forEach(b =>
        b.classList.toggle('active', b === btn));
    });
  });

  // Locked strip remove buttons
  view.querySelectorAll('.parlay-locked-remove').forEach(btn => {
    btn.addEventListener('click', () => {
      const fp = btn.dataset.fp;
      parlayParams.locked = parlayParams.locked.filter(f => f !== fp);
      renderParlayView();
    });
  });

  view.querySelector('.parlay-generate-btn').addEventListener('click', () => {
    _runParlayGeneration();
  });
}

function _togglePin(fp) {
  if (!fp) return;
  if (parlayParams.locked.includes(fp)) {
    parlayParams.locked = parlayParams.locked.filter(f => f !== fp);
  } else {
    parlayParams.locked = [...parlayParams.locked, fp];
  }
  // Refresh pin button state across both props and parlay views
  document.querySelectorAll(`.parlay-pin-btn[data-fp="${CSS.escape(fp)}"]`).forEach(b => {
    b.classList.toggle('active', parlayParams.locked.includes(fp));
    b.title = parlayParams.locked.includes(fp) ? 'Unpin from parlay' : 'Pin to parlay';
  });
  // Re-render the locked strip in the parlay view if visible
  const pv = document.getElementById('parlay-view');
  if (pv && !pv.hidden) renderParlayView();
}

function _runParlayGeneration() {
  const resultsDiv = document.getElementById('parlay-results');
  if (!resultsDiv) return;

  if (!picksData?.games?.length) {
    resultsDiv.innerHTML = '<div class="empty-state">No picks available today — run the pipeline first.</div>';
    return;
  }

  // Flatten all picks, annotating each with game context
  const allPicks = [];
  for (const game of picksData.games) {
    const gameStr = `${abbrev(game.away_team)} @ ${abbrev(game.home_team)}`;
    for (const pick of (game.picks || [])) {
      allPicks.push({ ...pick, _gamePk: game.gamePk, _gameStr: gameStr });
    }
  }

  // Resolve locked picks fingerprints against the flat pool
  const _pickFp = p => `${p.bet_type}:${p.subject_id ?? p._gamePk}:${p.direction}`;
  const lockedPicks = parlayParams.locked
    .map(fp => allPicks.find(p => _pickFp(p) === fp))
    .filter(Boolean);
  const missingLocked = parlayParams.locked.filter(fp => !allPicks.find(p => _pickFp(p) === fp));

  // Warn about any locked picks that disappeared from the data
  const lockedWarning = missingLocked.length
    ? `<div class="parlay-locked-warn">⚠ ${missingLocked.length} locked pick(s) not found in today's data — skipped.</div>`
    : '';

  // When locked picks exist, exclude their subjects/games from the pool so
  // the generator fills the remaining slots without duplicating them.
  let poolForGen = allPicks;
  if (lockedPicks.length) {
    const lockedSubjects = new Set(lockedPicks.map(p => p.subject_id).filter(Boolean));
    const lockedGames    = parlayParams.allowCorr ? new Set() : new Set(lockedPicks.map(p => p._gamePk).filter(Boolean));
    poolForGen = allPicks.filter(p => {
      if (p.subject_id && lockedSubjects.has(p.subject_id)) return false;
      if (lockedGames.size && p._gamePk && lockedGames.has(p._gamePk)) return false;
      return true;
    });
  }

  const remainingLegs = Math.max(1, parlayParams.legs - lockedPicks.length);
  const genParams = { ...parlayParams, legs: remainingLegs };
  const result = generateParlays(genParams, poolForGen);

  // Prepend locked picks to each option's legs
  const prependLocked = opt => {
    if (!lockedPicks.length) return opt;
    const allLegs = [...lockedPicks.map(p => ({ ...p, _locked: true })), ...opt.legs];
    return { ...opt, legs: allLegs, stats: _computeParlayStats(allLegs) };
  };

  const options = [result.ev, result.payout, result.safest].map(prependLocked);

  // Deduplicate: when two objectives produce the same set of legs, merge their
  // labels into one card rather than showing identical parlays twice.
  const _legFp = opt => opt.legs.map(l => `${l.bet_type}:${l.subject_id ?? l._gamePk}:${l.direction}`).sort().join('|');
  const _seen  = new Map();
  for (const opt of options) {
    const fp = _legFp(opt);
    if (_seen.has(fp)) { _seen.get(fp).label += ` / ${opt.label}`; }
    else { _seen.set(fp, { ...opt }); }
  }
  const uniqueOptions = [..._seen.values()];

  // SGP banner: find game string for the selected SGP game
  let sgpBanner = '';
  if (parlayParams.sgp && result.sgpGame) {
    const sgpGameObj = picksData.games.find(g => g.gamePk === result.sgpGame);
    const sgpLabel = sgpGameObj
      ? `${abbrev(sgpGameObj.away_team)} @ ${abbrev(sgpGameObj.home_team)}`
      : `Game ${result.sgpGame}`;
    sgpBanner = `<div class="parlay-sgp-banner">🏟 Same-Game Parlay: ${escapeHtml(sgpLabel)}</div>`;
  }

  resultsDiv.innerHTML = lockedWarning + sgpBanner +
    `<div class="parlay-options">${uniqueOptions.map((opt, i) => _renderParlayOption(opt, i === 0)).join('')}</div>`;

  // Expand/collapse toggle
  resultsDiv.querySelectorAll('.parlay-option-header').forEach(header => {
    header.addEventListener('click', () => {
      const card     = header.closest('.parlay-option-card');
      const isOpen   = card.classList.contains('expanded');
      resultsDiv.querySelectorAll('.parlay-option-card').forEach(c => {
        c.classList.remove('expanded');
        c.querySelector('.parlay-chevron').textContent = '▶';
      });
      if (!isOpen) {
        card.classList.add('expanded');
        card.querySelector('.parlay-chevron').textContent = '▼';
      }
    });
  });

  // Copy buttons
  resultsDiv.querySelectorAll('.parlay-copy-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const key = btn.dataset.optKey;
      const opt = uniqueOptions.find(o => o.key === key);
      if (opt) _copyParlayText(opt, btn);
    });
  });

  // Pin buttons inside parlay results
  resultsDiv.querySelectorAll('.parlay-pin-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      _togglePin(btn.dataset.fp);
    });
  });
}

function _renderParlayOption(opt, expanded) {
  const { key, label, legs, stats, note, combosChecked } = opt;
  const { payout, winPct, hasEstimate, corrAdj, totalDecOdds } = stats;
  const hasCorr = legs.some(l => l._sameGameWarning);
  const payStr  = `${hasEstimate ? '~' : ''}$${payout.toLocaleString()} on $100`;

  const americanStr = (() => {
    const am = _toAmericanOdds(totalDecOdds);
    if (am == null) return '';
    return `${hasEstimate ? '~' : ''}${am > 0 ? '+' + am : am}`;
  })();

  const corrBadge = hasCorr
    ? '<span class="parlay-corr-badge">⚠ Correlated legs</span>'
    : '';

  const verifyBadge = combosChecked > 0
    ? `<span class="parlay-verify-badge">✓ ${combosChecked.toLocaleString()} combos checked</span>`
    : '';

  const shortfall = note
    ? `<div class="parlay-shortfall">⚠ ${escapeHtml(note)}</div>`
    : '';

  const legsHtml = legs.length
    ? legs.map((leg, i) => (i > 0 ? '<div class="parlay-leg-divider"></div>' : '') + _renderParlayLeg(leg)).join('')
    : '<div class="parlay-empty-legs">No qualifying picks found — try adjusting parameters.</div>';

  const metaParts = [];
  if (americanStr) metaParts.push(`<span class="parlay-american-odds">${escapeHtml(americanStr)}</span>`);
  metaParts.push(`${legs.length}-leg`);
  metaParts.push(`Win: ${winPct}%${corrAdj ? ' (corr. adj.)' : ''}`);
  metaParts.push(`(~${escapeHtml(payStr)})`);

  return `
<div class="parlay-option-card${expanded ? ' expanded' : ''}" data-opt-key="${key}">
  <div class="parlay-option-header">
    <div class="parlay-option-summary">
      <span class="parlay-option-label">${label}</span>
      <span class="parlay-option-meta">${metaParts.join(' · ')}</span>
      ${corrBadge}
    </div>
    <div class="parlay-option-actions">
      ${verifyBadge}
      <button class="parlay-copy-btn" data-opt-key="${key}">Copy</button>
      <span class="parlay-chevron">${expanded ? '▼' : '▶'}</span>
    </div>
  </div>
  <div class="parlay-option-body">
    ${shortfall}
    ${legsHtml}
  </div>
</div>`;
}

function _renderParlayLeg(leg) {
  const badges = [];
  if (leg._sameGameWarning) badges.push('<span class="data-quality-badge lineup-warn">⚠ Same game</span>');
  if (leg._oddsEstimated)   badges.push('<span class="data-quality-badge parlay-est-badge">~est. odds</span>');
  if (leg._locked)          badges.push('<span class="data-quality-badge parlay-locked-badge">📌 Locked</span>');

  const probPct = leg.model_prob != null
    ? `<span class="parlay-leg-prob">${Math.round(leg.model_prob * 100)}%</span>`
    : '';
  const fp = `${leg.bet_type}:${leg.subject_id ?? leg._gamePk}:${leg.direction}`;
  const isPinned = parlayParams.locked.includes(fp);
  const pinBtn = `<button class="parlay-pin-btn${isPinned ? ' active' : ''}" data-fp="${escapeHtml(fp)}" title="${isPinned ? 'Unpin from parlay' : 'Pin to parlay'}">📌</button>`;

  const gameLabel = `<div class="parlay-leg-game">${escapeHtml(leg._gameStr || '')}${probPct}${pinBtn}</div>`;
  const badgeRow  = badges.length ? `<div class="parlay-leg-badge-row">${badges.join('')}</div>` : '';

  return `<div class="parlay-leg-wrap">${gameLabel}${badgeRow}${renderPick(leg)}</div>`;
}

function _copyParlayText(opt, btn) {
  const { label, legs, stats } = opt;
  const payStr = `${stats.hasEstimate ? '~' : ''}$${stats.payout.toLocaleString()} on $100`;
  const am = _toAmericanOdds(stats.totalDecOdds);
  const amStr = am != null ? `${stats.hasEstimate ? '~' : ''}${am > 0 ? '+' + am : am}  ` : '';
  let text = `MLBEdge Parlay — ${amStr}${label} · ${legs.length}-leg · Pays ${payStr}\n`;
  legs.forEach((leg, i) => {
    const dir  = leg.direction || '';
    const line = leg.odds?.line != null ? ` ${leg.odds.line}` : '';
    const rawOdds = getPickRawOdds(leg);
    const oddsStr = rawOdds != null ? ` (${rawOdds > 0 ? '+' : ''}${rawOdds}${leg._oddsEstimated ? ' ~est' : ''})` : '';
    text += `Leg ${i + 1}: ${leg.subject || ''} ${dir}${line}${oddsStr} — Signal ${(leg.signal ?? 0).toFixed(1)}\n`;
  });

  const origText = btn.textContent;
  navigator.clipboard.writeText(text)
    .then(() => {
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = origText; }, 1500);
    })
    .catch(() => {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = origText; }, 1500);
    });
}

// ── Support view ──────────────────────────────────────────────────────────────

// ── Edges View ────────────────────────────────────────────────────────────────
function renderEdgesView() {
  const el = document.getElementById('edges-view');

  const allGames = gamesData?.games || [];
  const edgeGames = allGames
    .filter(g => g.edge_conditions?.length && (g.game_status || 'preview') === 'preview')
    .sort((a, b) => {
      const topRoi = g => Math.max(...g.edge_conditions.map(e => e.roi_pct));
      return topRoi(b) - topRoi(a);
    });

  if (!edgeGames.length) {
    el.innerHTML = `
      <div class="view-header"><h1>Edge Finder</h1></div>
      <div class="empty-state">No edge conditions matched today's games.</div>`;
    return;
  }

  const CONF_LABEL = { high: 'Strong', medium: 'Watch', new: 'Emerging' };
  const CONF_CLASS = { high: 'ef-conf-high', medium: 'ef-conf-medium', new: 'ef-conf-new' };

  const cards = edgeGames.map(g => {
    const total = g.odds?.total;
    const pred  = g.prediction?.predicted_total;
    const totalStr = total != null ? `${total}` : '—';
    const devStr   = (total != null && pred != null) ? ` (model: ${pred.toFixed(1)})` : '';

    const condRows = g.edge_conditions.map(e => `
      <div class="ef-cond-row ${CONF_CLASS[e.confidence] || ''}">
        <span class="ef-cond-label">${e.label}</span>
        <span class="ef-cond-roi">+${e.roi_pct.toFixed(1)}% ROI</span>
        <span class="ef-cond-n">${e.n_games.toLocaleString()} games</span>
        <span class="ef-cond-seasons">${e.seasons}</span>
      </div>`).join('');

    const confBadge = (() => {
      const top = g.edge_conditions.reduce((best, e) => e.roi_pct > (best?.roi_pct ?? 0) ? e : best, null);
      return top ? `<span class="ef-conf-badge ${CONF_CLASS[top.confidence] || ''}">${CONF_LABEL[top.confidence] || top.confidence}</span>` : '';
    })();

    return `
    <div class="ef-card">
      <div class="ef-card-header">
        <span class="ef-matchup">${escapeHtml(g.away_team)} @ ${escapeHtml(g.home_team)}</span>
        ${confBadge}
        <span class="ef-total">Total ${totalStr}${escapeHtml(devStr)}</span>
      </div>
      ${condRows}
    </div>`;
  }).join('');

  el.innerHTML = `
    <div class="view-header">
      <h1>Edge Finder</h1>
      <span class="sub-label">${edgeGames.length} game${edgeGames.length !== 1 ? 's' : ''} matched today</span>
    </div>
    <div class="ef-explainer">
      Edges are derived from 9,400+ games (2022–2026). Only UNDER conditions on specific
      total-line bands are validated as multi-season edges. Confidence:
      <span class="ef-conf-badge ef-conf-high">Strong</span> = 4+ seasons,
      <span class="ef-conf-badge ef-conf-medium">Watch</span> = 4 seasons / 2026 reversal,
      <span class="ef-conf-badge ef-conf-new">Emerging</span> = 2026 data only.
    </div>
    <div class="ef-list">${cards}</div>`;
}

function renderSupportView() {
  document.getElementById('support-view').innerHTML = `
    <div class="support-wrap">

      <div class="support-section">
        <h2 class="support-section-title">Model Methodology</h2>
        <p class="support-body">
          The model scores each starting pitcher on a 0–1 scale using eight Statcast metrics
          (xERA, barrel% against, Stuff+, whiff rate, chase rate, K%, BB%, xBA against), then
          does the same for each lineup using six hitting metrics (xwOBA, xSLG, barrel%,
          hard-hit%, average exit velocity, walk/strikeout rates). Those two pitcher scores and
          two lineup scores are combined — pitching weighted at 80%, lineup at 65% of run
          suppression/production — against a league baseline of 4.1 runs per team to produce a
          predicted run total for each side. Win probability is derived from the pitcher and
          lineup differentials plus a home-field prior (52.5%), then passed through a
          Platt-scaling calibration fitted on 13,000+ historical games to correct for systematic
          over/under-confidence. When a Vegas closing total is available, the model uses it as a
          soft anchor for its run prediction rather than ignoring market information entirely.
        </p>
      </div>

      <div class="support-section">
        <h2 class="support-section-title">Monte Carlo Simulations</h2>
        <p class="support-body">
          The Monte Carlo simulation plays out your selected game 100,000 times from scratch,
          stepping through every single at-bat using real Statcast data for each batter and
          pitcher in the lineup. On each plate appearance, the model calculates the probability
          of a strikeout, walk, or ball in play for that specific matchup — blending the batter's
          tendencies, the pitcher's tendencies, and a league baseline using a formula called Log5.
          If the ball is put in play, the model rolls for a home run based on the batter's barrel
          rate and park factor, or otherwise determines whether it becomes a hit or an out using
          real batting-average-on-balls-in-play rates, then advances baserunners around the
          diamond accordingly. After 9 innings, one simulated score is recorded; after 100,000
          games, the model counts how often each team won, tallies the full run-scoring
          distribution, and compares total runs to the Vegas line to compute an over/under
          probability. The percentages you see are empirical frequencies from a very large sample
          of statistically rigorous plate appearances — not gut feelings or adjusted team ratings.
        </p>
      </div>

    </div>`;
}
