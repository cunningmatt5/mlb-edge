'use strict';

// ── Data sources ─────────────────────────────────────────────────────────────
const GAMES_URL    = './games.json';
const HISTORY_URL  = './history.json';
const BACKTEST_URL = './backtest.json';
const PICKS_URL      = './picks.json';
const PROPS_HIST_URL = './props_history.json';

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
let picksData      = null;
let propsHistData  = null;
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
      document.getElementById('support-view').hidden  = currentView !== 'support';
      if (currentView === 'record')   Promise.all([loadBacktest(), loadPropsHistory()]).then(renderRecordView);
      if (currentView === 'backtest') Promise.all([loadBacktest(), loadPropsHistory()]).then(renderBacktestView);
      if (currentView === 'props')    loadPicks().then(renderPropsView);
      if (currentView === 'support')  renderSupportView();
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

async function loadPropsHistory() {
  try {
    const r = await fetch(PROPS_HIST_URL + '?v=' + Date.now());
    if (r.ok) propsHistData = await r.json();
  } catch {
    propsHistData = null;
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
    const odds    = g.odds || {};
    const awayPct = Math.round((1 - (pred.home_win_pct ?? 0.5)) * 100);
    const edgePct = pred.model_edge_ml != null ? (+(-pred.model_edge_ml * 100).toFixed(1)) : null;
    const awayMl  = odds.away_ml != null ? (odds.away_ml > 0 ? `+${odds.away_ml}` : String(odds.away_ml)) : null;
    const sl      = spLine(g.away_sp, g.away_sp?.name || abbrev(g.away_team), g.home_sp, g.home_sp?.name || abbrev(g.home_team));
    return `
<div class="ea-card" onclick="toggleCard(${g.gamePk})">
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
      <span class="ea-bet-label">BET AWAY</span>
      ${awayMl ? `<span class="ea-ml">${awayMl}</span>` : ''}
      <span class="ea-win-pct">${awayPct}% win</span>
    </div>
    ${edgePct != null ? `<div class="ea-edge-pill">Model +${edgePct}% vs Vegas</div>` : ''}
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
      <span class="ea-win-pct">${favPct}% win</span>
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
        <span class="bb-sub-title">Elite Away Signal</span>
        <span class="bb-badge bb-badge-elite">${eliteGames.length} game${eliteGames.length > 1 ? 's' : ''} today</span>
      </div>
      <div class="bb-sub-desc">Model disagrees with Vegas by 10%+ &amp; away pitcher has better stats · <strong>+20.4% ROI</strong> backtested (536 bets, 2021–2025)</div>
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
      <div class="bb-sub-desc">Model ≥62% confident · Pitcher stats confirm the pick · <strong>68.8% win rate</strong> on 362 games (2021–2025)</div>
    </div>
    <div class="ea-cards">${hiConfCards}</div>
  </div>`;
  }

  return `
<div class="bb-section">
  <div class="bb-section-hdr">
    <span class="bb-section-title">Today's Best Bets</span>
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
<div class="game-card" data-pk="${g.gamePk}" data-status="${status}" data-fav="${fav}" data-pick-tier="${g.prediction?.pick_tier || ''}">
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
  const pickTier = pred.pick_tier;
  const pickTierBadge = pickTier === 'elite_away'
    ? `<span class="pick-tier-badge tier-elite-away">Elite Away</span>`
    : pickTier === 'strong_away'
    ? `<span class="pick-tier-badge tier-strong-away">Strong Away</span>`
    : '';
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
    ${tierBadge}${pickTierBadge}
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

  // ── SECTION 1: Intro callout ─────────────────────────────────────────────
  const totalGames = games.length;
  const introSection = `
    <div class="bt-intro">
      Every prediction logged here — <strong>${totalGames.toLocaleString()} games since 2021</strong> — was scored using
      the same model and checked against actual results. <strong>ROI</strong> is profit per $1 flat-bet
      (e.g., +23% means a $100 bankroll grew to $123). <strong>Win Rate</strong> is how often the
      predicted winner was correct. All odds are Pinnacle closing lines — the sharpest market available.
    </div>`;

  // ── SECTION 2: 2026 Live Performance ─────────────────────────────────────
  const roi = roi_stats || {};
  let liveSection = '';
  if (roi.ml_bets > 0 || roi.total_bets > 0) {
    liveSection = `
      <div class="bt-narrative-header">
        <div class="bt-narrative-title">2026 Live Performance</div>
        <div class="bt-narrative-sub">Current season · flat $1 bet on model's pick · vs. Pinnacle closing lines</div>
      </div>
      <div class="bt-live-grid">
        <div class="bt-live-card">
          <div class="bt-live-val ${roiCls(roi.ml_roi_pct)}">${fmtRoi(roi.ml_roi_pct)}</div>
          <div class="bt-live-label">Moneyline ROI</div>
          <div class="bt-live-sub">${fmtUnits(roi.ml_units_won)} units · ${roi.ml_bets ?? 0} bets</div>
        </div>
        <div class="bt-live-card">
          <div class="bt-live-val ${roiCls(roi.total_roi_pct)}">${fmtRoi(roi.total_roi_pct)}</div>
          <div class="bt-live-label">Totals ROI</div>
          <div class="bt-live-sub">${fmtUnits(roi.total_units_won)} units · ${roi.total_bets ?? 0} bets</div>
        </div>
        <div class="bt-live-card">
          <div class="bt-live-val">${pct(stats.win_pct_overall)}</div>
          <div class="bt-live-label">Prediction Win Rate</div>
          <div class="bt-live-sub">${(stats.total_correct ?? 0).toLocaleString()} / ${(stats.total_decided ?? 0).toLocaleString()} games</div>
        </div>
      </div>`;
  }

  // ── SECTION 3: The Away Advantage ────────────────────────────────────────
  const awayTierRows = awayTierDefs.map((def, i) => {
    const sp    = fmtCell(awayTierData[i].sp);
    const no_sp = fmtCell(awayTierData[i].no_sp);
    const isElite = i === 3; // Very Strong tier
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

  const awaySection = `
    <div class="bt-narrative-header">
      <div class="bt-narrative-title">The Away Advantage</div>
      <div class="bt-narrative-sub">Where the model consistently finds value — 2021–2025 · ${totalGames.toLocaleString()} games</div>
    </div>
    <p class="bt-section-intro">
      The model compares its win probability estimate to what Vegas is pricing. When it strongly favors the
      away team — and the away starting pitcher has better stats — the edge has been real and repeatable
      across five seasons.
    </p>
    <div class="bt-away-grid-wrap">
      <table class="bt-away-grid">
        <thead>
          <tr>
            <th class="bt-away-tier-col">Model's Away Edge vs. Vegas</th>
            <th class="bt-away-sp-col bt-col-sp">Away Pitcher Stronger</th>
            <th class="bt-away-sp-col">Home Pitcher Stronger or Even</th>
          </tr>
        </thead>
        <tbody>${awayTierRows}</tbody>
      </table>
    </div>
    <div class="bt-signal-note">
      <strong>Why does a 40% win rate beat a 73% win rate?</strong> The 10%+ / pitcher advantage picks
      are on <em>underdogs</em> — priced around +170 or better. Winning 40% at those odds is very
      profitable. The 73% column bets heavy favorites where you need ~75% just to break even — the
      house edge erases the accuracy advantage.
    </div>`;

  // ── SECTION 3b: Home Favorite Trap ───────────────────────────────────────
  const homeSection = `
    <hr class="bt-rule">
    <div class="bt-narrative-header">
      <div class="bt-narrative-title">The Home Favorite Trap</div>
      <div class="bt-narrative-sub">Why the model now dampens its home confidence — 2021–2025</div>
    </div>
    <p class="bt-section-intro">
      When the model strongly favors the home team and Vegas already agrees, the combined
      signal becomes over-confident. The model's home coefficient was cut from 1.0× to 0.4×
      after this data showed that consensus home picks are consistently unprofitable.
    </p>
    <div class="bt-away-grid-wrap">
      <table class="bt-away-grid">
        <thead>
          <tr>
            <th class="bt-away-tier-col">Model's Home Edge vs. Vegas</th>
            <th>Bets</th><th>Win Rate</th><th>ROI</th>
          </tr>
        </thead>
        <tbody>${homeTierRows}</tbody>
      </table>
    </div>
    <div class="bt-signal-note">
      The 10%+ tier had a <strong>-9.4% ROI</strong> across five seasons — worse than random.
      Dampening this signal lets the model trust Vegas pricing instead of doubling down on it.
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
    <hr class="bt-rule">
    <div class="bt-narrative-header">
      <div class="bt-narrative-title">Model Accuracy</div>
      <div class="bt-narrative-sub">How often the model picks the correct winner — by confidence level and by season</div>
    </div>
    <p class="bt-section-intro">
      Higher model confidence correlates directly with better accuracy. At 65%+, the model has been right
      nearly 4 out of 5 times — but these high-confidence picks are rare by design (the model only expresses
      that level of certainty when pitcher stats are very lopsided).
    </p>
    <div class="bt-two-col">
      <div>
        <div class="bt-subsection-title">By Confidence Level</div>
        <div class="bt-table-wrap">
          <table class="seg-table">
            <thead><tr><th>Model Says</th><th>Games</th><th>Actual Win%</th><th>vs. Coin Flip</th></tr></thead>
            <tbody>${confRows}</tbody>
          </table>
        </div>
      </div>
      <div>
        <div class="bt-subsection-title">Year-over-Year Consistency</div>
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

  const totalsSection = totalsYrRows ? `
    <hr class="bt-rule">
    <div class="bt-narrative-header">
      <div class="bt-narrative-title">Totals: The UNDER Edge</div>
      <div class="bt-narrative-sub">Run total predictions vs. Vegas line · historical seasons only</div>
    </div>
    <p class="bt-section-intro">
      MLB totals tend to be set slightly high — the market knows casual bettors prefer overs.
      Our model has consistently predicted fewer runs than the Vegas line, and that lean has been
      correct 52–53% of the time every single season. UNDER bets generate small but steady returns.
    </p>
    <div class="bt-table-wrap">
      <table class="seg-table">
        <thead><tr><th>Season</th><th>Over Bets</th><th>Over Accuracy</th><th>Under Bets</th><th>Under Accuracy</th></tr></thead>
        <tbody>${totalsYrRows}</tbody>
      </table>
    </div>` : '';

  // ── SECTION 6: Props ─────────────────────────────────────────────────────
  const propCounts = {};
  for (const p of (propsHistData || [])) {
    if (p.hit != null) propCounts[p.bet_type] = (propCounts[p.bet_type] || 0) + 1;
  }
  const hasEnoughProps = Object.values(propCounts).some(v => v >= 30);
  const propsSection = `
    <hr class="bt-rule">
    <div class="bt-narrative-header">
      <div class="bt-narrative-title">Props Performance</div>
      <div class="bt-narrative-sub">HR, strikeout, and hits prop picks</div>
    </div>
    ${hasEnoughProps ? renderPropsPerformance() : `
    <div class="bt-props-holding">
      <div class="bt-props-msg">Building data — tracking since May 31, 2026</div>
      <div class="bt-props-sub">Hit-rate analysis by signal strength appears once we have 30+ resolved picks per prop type.</div>
    </div>`}`;

  // ── SECTION 7: Game Log ───────────────────────────────────────────────────
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
      ${introSection}
      ${liveSection}
      ${awaySection}
      ${homeSection}
      ${calibrationSection}
      ${totalsSection}
      ${propsSection}
      <hr class="bt-rule">
      <div class="bt-narrative-header">
        <div class="bt-narrative-title">Game Log</div>
        <div class="bt-narrative-sub">${games.length.toLocaleString()} games · most recent first</div>
      </div>
      <div class="bt-table-wrap">
        <table class="bt-table">
          <thead><tr><th>Season</th><th>Date</th><th>Matchup</th><th>Predicted</th><th>Actual</th><th>Edge</th><th></th></tr></thead>
          <tbody>${logRows}</tbody>
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


// ── Support view ──────────────────────────────────────────────────────────────

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

    </div>`;
}
