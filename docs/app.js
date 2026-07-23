'use strict';

// ── Data sources ─────────────────────────────────────────────────────────────
const GAMES_URL    = './games.json';
const HISTORY_URL  = './history.json';
const BACKTEST_URL      = './backtest.json';
const EDGE_SCOREBOARD_URL = './edge_scoreboard.json';

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

// ── Team colors (matchup identity) ────────────────────────────────────────────
const TEAM_COLORS = {
  ARI:'#A71930', ATL:'#CE1141', BAL:'#DF4601', BOS:'#BD3039', CHC:'#1E6BC5', CWS:'#C0C0C0',
  CIN:'#C6011F', CLE:'#D4182E', COL:'#8B5CF6', DET:'#FA7C2B', HOU:'#EB6E1F', KC:'#C09A5B',
  LAA:'#CE1126', LAD:'#3788C7', MIA:'#00A3E0', MIL:'#FFC52F', MIN:'#D31145', NYM:'#FF5910',
  NYY:'#4A90D9', OAK:'#3EA843', PHI:'#E81828', PIT:'#FDB827', SD:'#C8941B', SF:'#FD5A1E',
  SEA:'#4DBDAF', STL:'#C41E3A', TB:'#8FBCE6', TEX:'#2B6CB0', TOR:'#1B8FC8', WSH:'#AB0003',
};

function teamColor(teamName) {
  const a = TEAM_LOGO[teamName];
  if (!a) return '#94a3b8';
  const key = a.toUpperCase() === 'CHW' ? 'CWS' : a.toUpperCase();
  return TEAM_COLORS[key] || '#94a3b8';
}

// Short nickname for compact matchup display: drop the city, keep the team name.
const _TEAM_NICK = {
  'Boston Red Sox':'Red Sox','Chicago White Sox':'White Sox','Chicago Cubs':'Cubs',
  'Toronto Blue Jays':'Blue Jays','Los Angeles Angels':'Angels','Los Angeles Dodgers':'Dodgers',
  'New York Yankees':'Yankees','New York Mets':'Mets','San Francisco Giants':'Giants',
  'San Diego Padres':'Padres','St. Louis Cardinals':'Cardinals','Tampa Bay Rays':'Rays',
  'Kansas City Royals':'Royals','Arizona Diamondbacks':'D-backs','Washington Nationals':'Nationals',
};
function teamNick(name) {
  if (!name) return '—';
  if (_TEAM_NICK[name]) return _TEAM_NICK[name];
  const parts = name.split(' ');
  return parts[parts.length - 1];
}

// ── App state ─────────────────────────────────────────────────────────────────
let gamesData    = null;
let historyData   = [];
let backtestData  = null;
let scoreboardData = null;
let expandedPk   = null;
let currentView  = 'games';
let lastCheckedAt = null;
let _perfSub     = 'record';   // Performance tab active sub-view: 'record' | 'backtest'

// player MLBAM id → full team name; rebuilt whenever gamesData loads
let _playerTeamMap = new Map();

// ── Bootstrap ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  setupNav();
  setupSubNav();
  await Promise.all([loadGames(), loadHistory()]);
  lastCheckedAt = Date.now();
  renderGamesView();
  startAutoRefresh();
});

// ── Navigation ────────────────────────────────────────────────────────────────
// Four top-level views: games, edges, performance, support.
// `performance` is a parent container that hosts sub-views via setupSubNav():
//   performance → record | backtest
const _PARENT_VIEWS = ['games', 'edges', 'performance', 'support'];

function setupNav() {
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      currentView = btn.dataset.view;
      document.querySelectorAll('.nav-btn').forEach(b => b.classList.toggle('active', b === btn));
      btn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
      for (const v of _PARENT_VIEWS) {
        document.getElementById(v + '-view').hidden = currentView !== v;
      }
      if (currentView === 'games')     renderGamesView();
      if (currentView === 'edges')     Promise.all([loadGames(), loadEdgeScoreboard()]).then(renderEdgesView);
      if (currentView === 'support')   renderSupportView();
      if (currentView === 'performance') _renderSub('performance', _perfSub);
    });
  });
}

// Lazy loader+render for each sub-view, reusing the existing per-view promises.
function _renderSub(parent, sub) {
  if (parent === 'performance') {
    if (sub === 'record')   Promise.all([loadBacktest(), loadEdgeScoreboard()]).then(renderRecordView);
    if (sub === 'backtest') Promise.all([loadBacktest(), loadEdgeScoreboard()]).then(renderBacktestView);
  }
}

// Wires the in-tab segmented sub-nav under Performance.
function setupSubNav() {
  const subViews = {
    performance: ['record', 'backtest'],
  };
  document.querySelectorAll('.subnav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const parent = btn.closest('.subnav').dataset.parent;
      const sub = btn.dataset.sub;
      if (parent === 'performance') _perfSub = sub;
      btn.parentElement.querySelectorAll('.subnav-btn').forEach(b => b.classList.toggle('active', b === btn));
      for (const v of subViews[parent]) {
        document.getElementById(v + '-view').hidden = v !== sub;
      }
      _renderSub(parent, sub);
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

async function loadEdgeScoreboard() {
  try {
    const r = await fetch(EDGE_SCOREBOARD_URL + '?v=' + Date.now());
    if (r.ok) scoreboardData = await r.json();
  } catch {
    scoreboardData = null;
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

function gotoView(v) {
  const b = document.querySelector(`.nav-btn[data-view="${v}"]`);
  if (b) b.click();
}

// Landing block that leads with TODAY'S VALIDATED edge plays (actionable edges only —
// signal_boost > 0, i.e. the 8.0 UNDERs, not the 9.0 watch). Reuses the edge-confidence
// map, per-season ROI chips, and the Kelly sizer.
function todaysEdgePlaysHTML(games) {
  const isActionable = e => e.direction === 'UNDER' && (e.signal_boost ?? 0) > 0;
  const plays = (games || [])
    .filter(g => (g.game_status || 'preview') === 'preview'
                 && (g.edge_conditions || []).some(isActionable))
    .sort((a, b) => edgeStrength(b) - edgeStrength(a));

  if (!plays.length) {
    return `
    <div class="edge-plays">
      <div class="ep-hdr"><span class="ep-title">⚡ Today's Edge Plays</span></div>
      <div class="ep-empty">No validated edge plays on today's slate — full game list below.
        <span class="ep-link" onclick="gotoView('edges')">View season performance →</span></div>
    </div>`;
  }

  const cards = plays.map(g => {
    const top = [...g.edge_conditions].filter(isActionable)
      .sort((a, b) => (b.signal_boost ?? 0) - (a.signal_boost ?? 0))[0];
    const c = edgeConf(top);
    const kelly = kellyStripHTML(g);   // '' if no bankroll set
    return `
    <div class="ep-card" onclick="toggleCard(${g.gamePk})">
      <div class="ep-row">
        <span class="ep-match">${escapeHtml(abbrev(g.away_team))} @ ${escapeHtml(abbrev(g.home_team))}</span>
        <span class="ep-bet ${c.cls}">${c.icon} ${escapeHtml(top.label)}</span>
        <span class="ep-roi sb-pos">${top.roi_pct >= 0 ? '+' : ''}${top.roi_pct.toFixed(1)}% hist</span>
      </div>
      ${kelly}
    </div>`;
  }).join('');

  return `
    <div class="edge-plays">
      <div class="ep-hdr">
        <span class="ep-title">⚡ Today's Edge Plays</span>
        <span class="ep-sub">${plays.length} validated · <span class="ep-link" onclick="gotoView('edges')">scoreboard →</span></span>
      </div>
      ${cards}
    </div>`;
}

function renderGamesView() {
  const view = document.getElementById('games-view');
  if (!gamesData || !gamesData.games.length) {
    view.innerHTML = `<div class="empty-state">No games scheduled today.</div>`;
    return;
  }

  const label     = formatDateLabel(gamesData.date);
  const savedBank = localStorage.getItem('mlbedge_bankroll') || '';
  const savedFrac = parseFloat(localStorage.getItem('mlbedge_kelly_fraction') || '0.5');
  const fracName  = _kellyFracName(savedFrac);

  view.innerHTML = `
    <div class="view-header">
      <h1>Today's Games</h1>
      <span class="sub-label">${label} &nbsp;·&nbsp; ${gamesData.game_count} games</span>
    </div>
    <div class="bankroll-row">
      <span class="bankroll-label">Bankroll</span>
      <span class="bankroll-prefix">$</span>
      <input type="number" id="bankroll-input" class="bankroll-input"
             placeholder="10000" min="0" step="100" value="${savedBank}">
      <button class="kelly-frac-btn" id="kelly-frac-btn"
              title="Bet-sizing aggressiveness. Tap to cycle: Full Kelly → ½ Kelly → ¼ Kelly. Smaller = safer (less variance).">${fracName}</button>
    </div>
    <div class="bankroll-hint">Optional — enter your bankroll to get a suggested stake on each validated <b>UNDER</b> edge below, sized by the <b>Kelly</b> setting (most pros use ½ or ¼ Kelly to limit swings).</div>
    ${todaysEdgePlaysHTML(gamesData.games)}
    ${localStorage.getItem('mlbedge_legend_hidden') ? '' : `
    <div class="games-legend" id="games-legend">
      <span class="gl-item"><b>Win %</b> our model's chance to win (lineups + Statcast)</span>
      <span class="gl-item"><b class="gl-i">⌁ pp vs market</b> gap from the de-vigged Vegas line</span>
      <span class="gl-item"><b>Est. runs</b> expected combined total</span>
      <button class="games-legend-x" title="Dismiss" onclick="this.parentElement.style.display='none';localStorage.setItem('mlbedge_legend_hidden','1')">×</button>
    </div>`}
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

  const bankInput = document.getElementById('bankroll-input');
  const fracBtn   = document.getElementById('kelly-frac-btn');

  function refreshKellyStrips() {
    view.querySelectorAll('.game-card').forEach(card => {
      const pk  = +card.dataset.pk;
      const g   = gamesData.games.find(x => x.gamePk === pk);
      if (!g) return;
      const existing = card.querySelector('.kelly-strip');
      const html = kellyStripHTML(g);
      if (existing) {
        existing.outerHTML = html || '';
      } else if (html) {
        const edgeStrip = card.querySelector('.edge-strip');
        if (edgeStrip) edgeStrip.insertAdjacentHTML('afterend', html);
      }
    });
  }

  bankInput?.addEventListener('input', () => {
    const val = parseFloat(bankInput.value);
    if (!isNaN(val) && val >= 0) localStorage.setItem('mlbedge_bankroll', val);
    else localStorage.removeItem('mlbedge_bankroll');
    refreshKellyStrips();
  });

  fracBtn?.addEventListener('click', () => {
    const cur  = parseFloat(localStorage.getItem('mlbedge_kelly_fraction') || '0.5');
    const next = cur >= 1.0 ? 0.25 : cur >= 0.5 ? 1.0 : 0.5;
    localStorage.setItem('mlbedge_kelly_fraction', next);
    fracBtn.textContent = _kellyFracName(next);
    refreshKellyStrips();
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
  // Match the displayed win-prob leader (our model) so card coloring never contradicts the bar.
  return winProbDisplay(g).mw >= 0.5 ? 'home' : 'away';
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
      ? `${abbrev(g.away_team)} ${aXera.toFixed(2)} vs ${abbrev(g.home_team)} ${hXera.toFixed(2)} xERA`
      : `${abbrev(g.home_team)} ${hXera.toFixed(2)} vs ${abbrev(g.away_team)} ${aXera.toFixed(2)} xERA`;
    chips.push(`<span class="key-sig-chip sp" title="Starting-pitcher expected ERA — lower is better. The pitcher with the edge is shown first.">${label}</span>`);
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

// One side of the matchup: logo in a team-color ring + name + record · SP.
function gc2TeamHTML(g, side) {
  const name   = side === 'away' ? g.away_team : g.home_team;
  const rec    = side === 'away' ? g.away_record : g.home_record;
  const sp     = side === 'away' ? g.away_sp : g.home_sp;
  const color  = teamColor(name);
  const recStr = rec ? `${rec.wins}-${rec.losses}` : '';
  const spStr  = sp?.name ? shortName(sp.name) : 'TBD';
  const sub    = [recStr, spStr].filter(Boolean).join(' · ');
  return `
  <div class="gc2-team gc2-${side}" style="--team:${color}">
    <div class="gc2-logo">${teamLogoHTML(name)}</div>
    <div class="gc2-team-text">
      <span class="gc2-name">${teamNick(name)}</span>
      <span class="gc2-sub">${sub}</span>
    </div>
  </div>`;
}

// Scoreboard region: win-probability hero (preview) or live/final score.
function gc2ScoreboardHTML(g) {
  const status = g.game_status || 'preview';
  const aSc = g.away_score ?? '–', hSc = g.home_score ?? '–';

  if (status === 'live') {
    const outs = g.outs != null ? ` · ${g.outs} OUT${g.outs !== 1 ? 'S' : ''}` : '';
    return `<div class="gc2-score-row">
      <span class="gc2-live"><span class="live-dot"></span>${g.inning_state || 'Live'}${outs}</span>
      <span class="gc2-bigscore">${abbrev(g.away_team)} ${aSc} – ${hSc} ${abbrev(g.home_team)}</span>
      <span class="gc2-chev">▾</span></div>`;
  }
  if (status === 'final') {
    return `<div class="gc2-score-row">
      <span class="final-badge">FINAL</span>
      <span class="gc2-bigscore">${abbrev(g.away_team)} ${aSc} – ${hSc} ${abbrev(g.home_team)}</span>
      <span class="gc2-chev">▾</span></div>`;
  }

  // Preview — OUR MODEL's win probability is the hero
  const d = winProbDisplay(g);
  return `
  <div class="gc2-winprob">
    <div class="gc2-wp-head">
      <span class="gc2-wp-label">Win Probability${d.isModel ? ' <span class="gc2-wp-src">our model</span>' : ''}</span>
      <span class="gc2-info" title="Our model's win probability from lineups + Statcast (out-of-sample AUC 0.60). The ⌁ line below shows how far it sits from the vig-adjusted market.">ⓘ</span>
    </div>
    <div class="gc2-wp-bar">
      <div class="gc2-wp-seg gc2-wp-away" style="width:${d.awayPct}%"></div>
      <div class="gc2-wp-seg gc2-wp-home" style="width:${d.homePct}%"></div>
    </div>
    <div class="gc2-wp-pcts">
      <span>${abbrev(g.away_team)} <strong>${d.awayPct}%</strong></span>
      <span><strong>${d.homePct}%</strong> ${abbrev(g.home_team)}</span>
    </div>
  </div>
  ${gc2ModelRowHTML(g, d)}
  <div class="gc2-chev-center">▾ <span>tap for pitchers · lineups · edges</span></div>`;
}

// Unified win-prob for display: OUR MODEL (fallback to headline only if model missing),
// plus the percentage-point variance vs the no-vig (vig-adjusted) market for the leaned side.
// One number everywhere — the bar, the reference line, the tier all use this.
function winProbDisplay(g) {
  const pred = g.prediction || {};
  const mwRaw = pred.model_home_win_pct;
  const mw = mwRaw != null ? mwRaw : (pred.home_win_pct != null ? pred.home_win_pct : 0.5);
  const homePct = Math.round(mw * 100);
  const leanHome = mw >= 0.5;
  let varPp = null, marketSidePct = null;
  if (g.odds && g.odds.home_ml != null && g.odds.away_ml != null) {
    const mh = noVigProb(g.odds.home_ml, g.odds.away_ml)[0];
    const modelSide = leanHome ? mw : 1 - mw;
    const marketSide = leanHome ? mh : 1 - mh;
    varPp = (modelSide - marketSide) * 100;
    marketSidePct = Math.round(marketSide * 100);
  }
  return {
    mw, homePct, awayPct: 100 - homePct, isModel: mwRaw != null,
    leanHome, leanTeamName: leanHome ? g.home_team : g.away_team,
    leanPct: Math.round((leanHome ? mw : 1 - mw) * 100), varPp, marketSidePct,
  };
}

// Reference row under the win bar: pp variance vs the no-vig market + est. total.
// (The bar itself is now our model's number, so this no longer repeats the model %.)
function gc2ModelRowHTML(g, d) {
  d = d || winProbDisplay(g);
  const pred = g.prediction || {};
  const parts = [];
  if (d.isModel && d.varPp != null) {
    const sign = d.varPp >= 0 ? '+' : '−';
    parts.push(`<span class="gc2-model"><span class="gc2-model-i">⌁</span> <strong>${sign}${Math.abs(d.varPp).toFixed(1)}pp</strong> vs market</span><span class="gc2-var" title="Our model ${d.leanPct}% vs the vig-adjusted (no-vig) moneyline ${d.marketSidePct}% ${abbrev(d.leanTeamName)} — transparency, not a bet signal.">de-vigged ${d.marketSidePct}% ${abbrev(d.leanTeamName)}</span>`);
  } else if (d.isModel) {
    parts.push(`<span class="gc2-model"><span class="gc2-model-i">⌁</span> our model · no market line</span>`);
  }
  if (pred.predicted_total != null) {
    parts.push(`<span class="gc2-total" title="Expected combined runs, anchored to the market total — a run-environment estimate, not a margin prediction.">Est. ${pred.predicted_total} runs</span>`);
  }
  if (!parts.length) return '';
  return `<div class="gc2-model-row">${parts.join('')}</div>`;
}

// Compact Vegas betting lines for the collapsed card: away ML · O/U total · home ML.
function gc2OddsHTML(g) {
  const o = g.odds;
  if (!o || (o.away_ml == null && o.home_ml == null && o.total == null)) return '';
  const ml = v => v == null ? '—' : (v > 0 ? `+${v}` : `${v}`);
  return `
  <div class="gc2-odds" title="Vegas betting lines — moneyline for each team and the Over/Under total.">
    <span class="gc2-ml">${o.away_ml != null ? `${abbrev(g.away_team)} ${ml(o.away_ml)}` : ''}</span>
    <span class="gc2-ou">${o.total != null ? `O/U ${o.total}` : ''}</span>
    <span class="gc2-ml gc2-ml-r">${o.home_ml != null ? `${ml(o.home_ml)} ${abbrev(g.home_team)}` : ''}</span>
  </div>`;
}

function gameCardHTML(g) {
  const status = g.game_status || 'preview';
  const fav    = gameFav(g);
  const pred   = g.prediction || {};
  const timeStr = g.game_time_et || formatTimeET(g.game_time_utc);
  const spChanged = g.sp_changed
    ? `<span class="gc2-spchg" title="Starting pitcher changed — stats updating on next rebuild">⚠ SP change</span>`
    : '';
  const tier = status === 'preview' ? gameTier(winProbDisplay(g).mw) : null;
  const tierBadge = tier ? `<span class="gc2-tier tier-${tier}">${tier.toUpperCase()}</span>` : '';
  const cAway = teamColor(g.away_team), cHome = teamColor(g.home_team);

  return `
<div class="game-card gc2" data-pk="${g.gamePk}" data-status="${status}" data-fav="${fav}" data-pick-tier="${pred.pick_tier || ''}" style="--c-away:${cAway};--c-home:${cHome}">
  <div class="game-card-header">
    <div class="gc2-meta">
      <span class="gc2-time">${timeStr}</span>
      <span class="gc2-venue">${g.venue || ''}</span>
      ${spChanged}
      ${tierBadge}
    </div>
    <div class="gc2-matchup">
      ${gc2TeamHTML(g, 'away')}
      <span class="gc2-at">@</span>
      ${gc2TeamHTML(g, 'home')}
    </div>
    ${gc2OddsHTML(g)}
    ${gc2ScoreboardHTML(g)}
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
// Shown alongside the informational "model leans away" flag (price context only).
// (Removed dead helpers buildPickReasoning / statusStrip / spEra — orphaned by the gc2 redesign.)

// ── Game edge banner — prominent top-of-card callout for actionable picks ─────
function gameEdgeBannerHTML(g) {
  const conds  = g.edge_conditions || [];
  const status = g.game_status || 'preview';
  if (status !== 'preview') return '';

  const parts = [];

  const pk = g.gamePk;

  for (const e of conds) {
    const roiStr = `+${e.roi_pct.toFixed(1)}% ROI`;
    const icon   = e.direction === 'UNDER' ? '⚡' : '★';
    parts.push(`
<div class="game-edge-banner banner-edge-under" onclick="toggleCard(${pk})">
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

// ── Kelly criterion stake strip ───────────────────────────────────────────────
function _kellyFracLabel(frac) {
  return frac >= 1.0 ? '1K' : frac >= 0.5 ? '½K' : '¼K';
}

// Fuller, self-explanatory label for the toggle button (vs the compact ¼K/½K/1K tag).
function _kellyFracName(frac) {
  return frac >= 1.0 ? 'Full Kelly' : frac >= 0.5 ? '½ Kelly' : '¼ Kelly';
}

function kellyStripHTML(g) {
  const bankroll = parseFloat(localStorage.getItem('mlbedge_bankroll') || '0');
  if (!bankroll || bankroll <= 0) return '';
  const status = g.game_status || 'preview';
  if (status !== 'preview') return '';
  const odds = g.odds;
  const fraction = parseFloat(localStorage.getItem('mlbedge_kelly_fraction') || '0.5');
  const fracLabel = _kellyFracLabel(fraction);
  const items = [];

  // Kelly only sizes the VALIDATED, vig-acceptable UNDER edge — driven entirely by
  // edge_detector's edge_conditions (single source of truth). This excludes the
  // demoted 9.0 (watch, boost 0), heavy-vig 8.0 (suppressed), the emerging model-dev
  // edge (no trusted win rate), and moneyline (no validated edge — ML Kelly removed,
  // since home_win_pct overestimates the true win rate).
  const underEdge = (g.edge_conditions || []).find(
    e => e.direction === 'UNDER' && e.kelly_win_prob != null && (e.signal_boost ?? 0) > 0);
  if (underEdge && odds?.total != null && odds?.under_price != null) {
    const winProb = underEdge.kelly_win_prob;
    const decOdds = americanToDecimal(odds.under_price);
    const b       = decOdds - 1;
    const fullK   = b > 0 ? (b * winProb - (1 - winProb)) / b : 0;
    if (fullK > 0) {
      const stake = Math.round(bankroll * fullK * fraction);
      if (stake >= 1) {
        items.push(`<span class="kelly-label kelly-under" title="Suggested stake at ${_kellyFracName(fraction)} of your bankroll">${fracLabel}</span><span class="kelly-amt">$${stake.toLocaleString()}</span><span class="kelly-on">UNDER ${odds.total}</span>`);
      }
    }
  }

  if (!items.length) return '';
  return `<div class="kelly-strip">${items.map(i => `<span class="kelly-item">${i}</span>`).join('<span class="kelly-sep">·</span>')}</div>`;
}

// ── Vegas edge strip (collapsed card) — surfaces ML and total model edge ──────
function vegasEdgeStripHTML(g) {
  const odds = g.odds;
  const pred = g.prediction || {};
  const status = g.game_status || 'preview';
  if (!odds || status !== 'preview') return '';

  const pills = [];

  // Total lean
  if (odds.total != null && pred.predicted_total != null) {
    const diff = +(pred.predicted_total - odds.total).toFixed(1);
    if (Math.abs(diff) >= 0.2) {
      const dir = diff > 0 ? 'OVER' : 'UNDER';
      const dirCls = diff > 0 ? 'dir-over' : 'dir-under';
      const strCls = Math.abs(diff) >= 0.5 ? 'strong' : 'mild';
      const tip = `The model predicts ${pred.predicted_total.toFixed(1)} total runs vs the Vegas line of ${odds.total} — a ${Math.abs(diff).toFixed(1)}-run ${dir} lean. Only a validated UNDER line (see the Edges tab) is an actionable edge.`;
      pills.push(`<span class="edge-pill ${strCls} ${dirCls}" title="${tip}">${dir} ${pred.predicted_total.toFixed(1)} vs ${odds.total}</span>`);
    }
  }

  if (!pills.length) return '';
  return `<div class="edge-strip">${pills.join('')}</div>`;
}

// ── Edge condition badge (game card) ──────────────────────────────────────────
// Canonical edge-confidence presentation (back-compat with legacy medium/new).
const EDGE_CONF = {
  high:     { label: 'Strong',   cls: 'strong', efCls: 'ef-conf-high',   icon: '⚡' },
  watch:    { label: 'Watch',    cls: 'watch',  efCls: 'ef-conf-watch',  icon: '⚠' },
  emerging: { label: 'Emerging', cls: 'new',    efCls: 'ef-conf-new',    icon: '★' },
  medium:   { label: 'Watch',    cls: 'medium', efCls: 'ef-conf-medium', icon: '~' },
  new:      { label: 'Emerging', cls: 'new',    efCls: 'ef-conf-new',    icon: '★' },
};
const edgeConf = e => EDGE_CONF[e.confidence] || EDGE_CONF.high;
// Edge strength for ranking = the actual (vig-adjusted) signal boost; ROI breaks ties.
const edgeStrength = g => Math.max(0, ...(g.edge_conditions || []).map(e => e.signal_boost ?? 0));
const edgeTopRoi   = g => Math.max(...(g.edge_conditions || []).map(e => e.roi_pct ?? 0));

// Compact per-season ROI chips (green/red by sign). Empty if the pipeline hasn't
// emitted season_roi yet (older games.json).
function seasonRoiStripHTML(e) {
  if (!e.season_roi || !Object.keys(e.season_roi).length) return '';
  const chips = Object.entries(e.season_roi).map(([yr, roi]) => {
    const pos = roi >= 0;
    return `<span class="ef-season-chip ${pos ? 'pos' : 'neg'}">`
         + `${yr.slice(2)} ${pos ? '+' : ''}${roi.toFixed(0)}%</span>`;
  }).join('');
  return `<div class="ef-season-strip" title="Historical ROI by season">${chips}</div>`;
}

function edgeConditionsHTML(g) {
  const conds = g.edge_conditions;
  if (!conds || !conds.length) return '';
  const status = g.game_status || 'preview';
  if (status !== 'preview') return '';

  // Strongest (highest live boost) first so the actionable edge leads.
  const ordered = [...conds].sort((a, b) => (b.signal_boost ?? 0) - (a.signal_boost ?? 0));
  const badges = ordered.map(e => {
    const c = edgeConf(e);
    const roiStr = `${e.roi_pct >= 0 ? '+' : ''}${e.roi_pct.toFixed(1)}% ROI`;
    const muted = (e.signal_boost ?? 0) === 0 ? ' muted' : '';
    return `<span class="edge-cond-badge ${c.cls}${muted}" title="${e.seasons}">`
         + `${c.icon} ${e.label} · ${roiStr}</span>`;
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
  // Betting + signals (relocated from the collapsed card to keep it clean)
  const edgeBits = [vegasEdgeStripHTML(g), kellyStripHTML(g), keySignalsHTML(g)].filter(Boolean).join('');
  const edgeBlock = (gameEdgeBannerHTML(g) || edgeBits)
    ? `<div class="expanded-section">
         <div class="section-heading">Edges &amp; Betting</div>
         ${gameEdgeBannerHTML(g)}
         ${edgeBits}
       </div>`
    : '';

  return `
<div class="expanded-inner">
  <div class="expanded-section">
    <div class="section-heading">${(g.game_status && g.game_status !== 'preview') ? 'Pre-game Prediction' : 'Prediction'}</div>
    ${predictionHTML(g)}
  </div>
  <div class="expanded-section">
    <div class="section-heading">Pitchers</div>
    ${pitcherTableHTML(g)}
  </div>
  <div class="expanded-section">
    <div class="section-heading">Lineups ${lineupStatusHTML(g)}</div>
    ${lineupsHTML(g)}
  </div>
  ${edgeBlock}
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
  const _wp     = winProbDisplay(g);          // OUR MODEL (one number, same as the card)
  const homePct = _wp.homePct;
  const awayPct = _wp.awayPct;
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

  ${(() => {
    if (!_wp.isModel) return '';
    const tip = "The bar above is OUR MODEL's win probability (lineups + Statcast, out-of-sample AUC 0.60). "
      + "Below shows how far it sits from the vig-adjusted (no-vig) market — transparency, NOT a betting signal (the market is sharper, AUC 0.64).";
    if (_wp.varPp == null) {
      return `<div class="model-read" title="${tip}"><span class="mr-icon">⌁</span><span class="mr-main">Bar above is <strong>our model</strong> — no market line to compare</span></div>`;
    }
    const sign = _wp.varPp >= 0 ? '+' : '−';
    const varCls = Math.abs(_wp.varPp) >= 5 ? 'mr-var-hi' : 'mr-var-lo';
    return `
  <div class="model-read" title="${tip}">
    <span class="mr-icon">⌁</span>
    <span class="mr-main">Bar above is <strong>our model</strong></span>
    <span class="mr-var ${varCls}">${sign}${Math.abs(_wp.varPp).toFixed(1)}pp vs market</span>
    <span class="mr-note">de-vigged ${_wp.marketSidePct}% ${abbrev(_wp.leanTeamName)}</span>
    <span class="mr-info">ⓘ</span>
  </div>`;
  })()}

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
    const modelHome = _wp.mw;   // our model — consistent with the bar above
    if (mc.mc_win_pct == null) return '';
    const gapPp = Math.round(Math.abs(mc.mc_win_pct - modelHome) * 100);
    if (gapPp < 8) return '';
    // Exploratory only — a 2024-25 backtest found MC divergence does NOT predict
    // outcomes, so this is shown as neutral info, not a betting signal.
    return `<div class="mc-diverge mc-div-info" title="Statcast-pure sim vs the model — exploratory only; divergence is NOT a validated betting signal">◇ Sim ${Math.round(mc.mc_win_pct * 100)}% vs model ${Math.round(modelHome * 100)}% home <span class="mc-div-gap">${gapPp}pp</span></div>`;
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

// Totals outcome vs a line: 'over' | 'under' | 'push' (final runs exactly on the line).
// Pushes are void — never scored as a win or loss. null when inputs are missing.
function totalsOutcome(actualTotal, line) {
  if (actualTotal == null || line == null) return null;
  if (Math.abs(actualTotal - line) < 1e-9) return 'push';
  return actualTotal > line ? 'over' : 'under';
}

// Compact "by edge" performance summary from the live edge scoreboard (the only validated,
// bet-and-tracked surface). Mirrors the Edges-tab data; links there for full detail.
function edgePerfSummaryHTML() {
  const sb = scoreboardData;
  const edges = (sb && sb.edges) || [];
  if (!edges.length) {
    return `<div class="section-heading">Edge performance <span class="scope-tag">2026 · realized</span></div>
      <p class="rec-priced-note">Edge tracking populates as flagged plays resolve. Full detail in the <b>Edges</b> tab.</p>`;
  }
  const confTag = c => c === 'high' ? '<span class="ep-conf ep-conf-high">strong</span>'
    : c === 'emerging' ? '<span class="ep-conf ep-conf-new">emerging</span>'
    : '<span class="ep-conf ep-conf-watch">watch</span>';
  const pctCls = v => v == null ? '' : v >= 0 ? 'edge-pos' : 'edge-neg';
  const pctStr = v => v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
  const rows = edges.map(e => {
    const wins = Math.round((e.win_pct || 0) / 100 * (e.n || 0));
    const clv = e.clv_beat_pct != null ? `${Math.round(e.clv_beat_pct)}%` : '—';
    const live = e.n ? `${wins}–${e.n - wins} <span class="${pctCls(e.roi_pct)}">${pctStr(e.roi_pct)}</span>` : '—';
    return `<tr>
      <td class="ep-name">${e.label || e.tag} ${confTag(e.confidence)}</td>
      <td class="${pctCls(e.hist_roi_pct)}">${pctStr(e.hist_roi_pct)}</td>
      <td>${live}</td>
      <td>${clv}</td>
    </tr>`;
  }).join('');
  return `<div class="section-heading">Edge performance <span class="scope-tag">validated + live</span></div>
    <p class="rec-priced-note">The only bet-and-tracked edges. <b>Validated</b> = multi-season push-corrected ROI; <b>2026</b> = realized this season (small samples are noisy). CLV = % of bets that beat the closing line. Full detail in the <b>Edges</b> tab.</p>
    <table class="season-year-table">
      <thead><tr><th>Edge</th><th>Validated</th><th>2026 (rec · ROI)</th><th>CLV</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
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
  // Push-aware: grade against the actual total vs the line (a push is void, not a miss).
  const oc = totalsOutcome(r.actual_total, r.vegas_total);
  if (oc && !r.sp_scratched) {
    if (oc === 'push') {
      icon = '<span class="edge-call-push" title="Pushed — final total landed on the line">P</span>';
    } else {
      const hit = (lean > 0 && oc === 'over') || (lean < 0 && oc === 'under');
      icon = hit ? '<span class="edge-call-hit">✓</span>' : '<span class="edge-call-miss">✗</span>';
    }
  }
  return `<td class="hist-total"><span class="${dirCls}">${dir} +${gap}</span> ${icon}</td>`;
}

// ── Props performance ─────────────────────────────────────────────────────────
function modelWinProbStats(decided) {
  const rows = decided.filter(r => r.model_home_win_pct != null
    && (r.actual_winner === 'home' || r.actual_winner === 'away'));
  if (!rows.length) return { n: 0 };
  let mCorrect = 0, hCorrect = 0, mBrier = 0, hBrier = 0;
  for (const r of rows) {
    const home = r.actual_winner === 'home' ? 1 : 0;
    const m = r.model_home_win_pct;
    const h = r.home_win_pct ?? 0.5;
    if ((m >= 0.5 ? 'home' : 'away') === r.actual_winner) mCorrect++;
    if ((h >= 0.5 ? 'home' : 'away') === r.actual_winner) hCorrect++;
    mBrier += (m - home) ** 2;
    hBrier += (h - home) ** 2;
  }
  const n = rows.length;
  return {
    n,
    mAcc: Math.round(mCorrect / n * 100), hAcc: Math.round(hCorrect / n * 100),
    mBrier: (mBrier / n).toFixed(3), hBrier: (hBrier / n).toFixed(3),
  };
}

function renderRecordView() {
  const view = document.getElementById('record-view');
  // Graded universe: resolved games, EXCLUDING scratched-SP games (the predicted starter never
  // pitched → the prediction is invalid). Ties/unresolved already excluded. Keeps accuracy honest
  // and consistent with the edge ROI (which also drops scratched).
  const decidedAll = historyData.filter(r => r.actual_winner === 'home' || r.actual_winner === 'away');
  const scratched  = decidedAll.filter(r => r.sp_scratched).length;
  const decided    = decidedAll.filter(r => !r.sp_scratched);

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
  const tl        = totalsLeanAccuracy(decided);
  const tlRow = (label, b) => {
    const n = b.w + b.l, p = n ? Math.round(b.w / n * 100) : null;
    return `<tr><td>${label}</td><td>${b.w}–${b.l}</td><td class="${p == null ? '' : p >= 55 ? 'edge-pos' : p >= 50 ? '' : 'edge-neg'}">${p == null ? '—' : p + '%'}</td></tr>`;
  };

  view.innerHTML = `
<div class="view-header">
  <h1>Prediction Record</h1>
  <span class="sub-label">${correct}–${decided.length - correct} (${pct}%) &nbsp;·&nbsp; ${decided.length} games graded${scratched ? ` &nbsp;·&nbsp; ${scratched} excluded (SP scratched)` : ''} ${streakLabel}</span>
</div>

<div class="rec-lens-label">① Model accuracy — how often the prediction is right (accuracy, not betting)</div>

<div class="rec-vegas-section">
  ${renderVegasSection()}
</div>

<div class="record-top-grid">
  <div class="record-conf-section">
    <div class="section-heading">Record by confidence <span class="scope-tag">2026 · in-sample</span></div>
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
    <div class="section-heading">Signal accuracy <span class="scope-tag">2026 · in-sample</span></div>
    <div class="signal-grid compact-signal-grid">
      ${Object.values(signals).map(s => signalCardHTML(s)).join('')}
    </div>
  </div>
</div>

<div class="rec-totals-lean">
  <div class="section-heading">Totals-lean accuracy <span class="scope-tag">2026 · in-sample</span></div>
  <table class="season-year-table">
    <thead><tr><th>Model lean</th><th>Record</th><th>Win%</th></tr></thead>
    <tbody>${tlRow('UNDER lean', tl.under)}${tlRow('OVER lean', tl.over)}</tbody>
  </table>
  <p class="rec-priced-note">Win% of the model's ≥0.5-run total lean (push-corrected${tl.push ? `, ${tl.push} pushes excluded` : ''}). Accuracy only — realized ROI is in Edge performance below.</p>
</div>

<div class="rec-modelwp">
  <div class="section-heading">Model win-prob accuracy <span class="scope-tag">independent model · forward</span></div>
  ${(() => {
    const s = modelWinProbStats(decided);
    if (!s.n) {
      const tracked = historyData.filter(r => r.model_home_win_pct != null).length;
      return `<p class="rec-priced-note">The independent model win-prob (lineups + Statcast, no market line) is now recorded with every prediction${tracked ? ` — <strong>${tracked}</strong> logged so far` : ''}. Winner accuracy and calibration will appear here as those games resolve.</p>`;
    }
    const accCls = s.mAcc >= s.hAcc ? 'edge-pos' : '';
    return `
  <table class="season-year-table">
    <thead><tr><th></th><th>Winner accuracy</th><th>Brier (lower = better)</th></tr></thead>
    <tbody>
      <tr><td>Our model (input-only)</td><td class="${accCls}">${s.mAcc}%</td><td>${s.mBrier}</td></tr>
      <tr><td>Headline (market-anchored)</td><td>${s.hAcc}%</td><td>${s.hBrier}</td></tr>
    </tbody>
  </table>
  <p class="rec-priced-note">Independent model vs the market-anchored headline over the same ${s.n} resolved game${s.n === 1 ? '' : 's'}. The market-anchored number is expected to be sharper — this tracks how close our pure-input model gets.</p>`;
  })()}
</div>

<div class="rec-lens-label">② Edge performance — realized returns on the validated edges</div>
${edgePerfSummaryHTML()}

<div class="history-section">
  <div class="section-heading">Game Log <span class="scope-tag">2026 · prediction hit/miss</span></div>
  ${byDate.map(({ date: d, games }) => {
    const dc = games.filter(r => r.predicted_winner === r.actual_winner).length;
    const dLabel = formatDateLabel(d);
    return `
  <div class="day-group">
    <div class="day-header">
      <div class="day-header-main">
        <span class="day-label">${dLabel}</span>
        <span class="day-record ${dc / games.length >= 0.5 ? 'day-win' : 'day-loss'}">Picks ${dc}–${games.length - dc}</span>
      </div>
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

// ── Season accuracy / totals-lean accuracy ────────────────────────────────────
// (The old blind-bet Vegas buckets / "simulated ROI" were removed — betting performance now
//  lives only in the validated edge scoreboard. See edgePerfSummaryHTML + the Edges tab.)

// Model totals-lean accuracy over a set of records (push-aware). Returns {over:{w,l},under:{w,l},push}.
function totalsLeanAccuracy(records) {
  const out = { over: { w: 0, l: 0 }, under: { w: 0, l: 0 }, push: 0 };
  for (const r of records) {
    const line = r.vegas_total ?? r.closing_total;
    const pt   = r.predicted_total;
    const at   = r.actual_total;
    if (line == null || pt == null || at == null) continue;
    const lean = +(pt - line).toFixed(1);
    if (Math.abs(lean) < 0.5) continue;          // only count meaningful leans
    const oc = totalsOutcome(at, line);
    if (oc === 'push') { out.push++; continue; } // void
    const side = lean > 0 ? 'over' : 'under';
    const hit  = (side === 'over' && oc === 'over') || (side === 'under' && oc === 'under');
    if (hit) out[side].w++; else out[side].l++;
  }
  return out;
}

function renderVegasSection() {
  // Prediction accuracy by season (accuracy ONLY). Betting ROI is intentionally not shown here —
  // the only validated, bet-and-tracked surface is the edge scoreboard (edgePerfSummaryHTML + the
  // Edges tab). Reads backtestData (2021–25) + historyData (2026).
  if (!(backtestData && backtestData.games) && !(historyData || []).length) return '';

  // Per-season prediction ACCURACY (win% of the model's predicted winner).
  // NOTE: this is accuracy only — we deliberately do NOT show a per-season moneyline ROI here.
  // Betting the model's side on every game is not a validated edge (ML is ~0 EV; see the Edges
  // tab for the validated UNDER edges), and showing it invited an apples-to-oranges read where
  // 2026 (in-sample for the recalibrated model) dwarfed the out-of-sample prior seasons.
  // 2026 is sourced ONLY from history (backtest.json also carries 2026 rows → would double-count).
  function buildSeasonRows() {
    const byYear = {};
    for (const r of (backtestData && backtestData.games) || []) {
      const yr = String(r.season || (r.date || '').slice(0, 4));
      if (!yr || yr === '2026') continue;   // 2026 comes from history below (avoid double-count)
      if (!byYear[yr]) byYear[yr] = { n: 0, correct: 0 };
      byYear[yr].n++;
      if (r.correct) byYear[yr].correct++;
    }
    for (const r of (historyData || [])) {
      if (r.actual_winner !== 'home' && r.actual_winner !== 'away') continue;
      const yr = (r.date || '').slice(0, 4) || '2026';
      if (!byYear[yr]) byYear[yr] = { n: 0, correct: 0 };
      byYear[yr].n++;
      if (r.predicted_winner === r.actual_winner) byYear[yr].correct++;
    }
    return Object.entries(byYear)
      .sort(([a], [b]) => Number(a) - Number(b))
      .map(([yr, d]) => {
        const acc = d.n ? Math.round(d.correct / d.n * 100) : 0;
        const inSample = yr === '2026'
          ? ' <span class="syr-insample" title="2026 is in-sample for the recalibrated model — not directly comparable to the out-of-sample prior seasons">in-sample</span>'
          : '';
        return `<tr>
          <td class="syr-yr">${yr}${inSample}</td>
          <td>${d.correct}–${d.n - d.correct}</td>
          <td class="${acc >= 55 ? 'edge-pos' : acc >= 50 ? '' : 'edge-neg'}">${acc}%</td>
        </tr>`;
      }).join('');
  }

  return `
<div class="section-heading">Prediction accuracy by season <span class="scope-tag">2021–26</span></div>
<p class="rec-priced-note">Win% of the model's predicted winner, by season. 2026 is <b>in-sample</b> for the recalibrated model — not directly comparable to the out-of-sample prior seasons. Betting ROI lives in the <b>Edges</b> tab and the Edge-performance section below — not here (the model's moneyline is ~0 EV).</p>
<table class="season-year-table">
  <thead><tr><th>Season</th><th>Record</th><th>Acc%</th></tr></thead>
  <tbody>${buildSeasonRows()}</tbody>
</table>`;
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

// (Removed dead helpers formatOddsLine / formatWeather — orphaned by the gc2 redesign.)

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


// (Removed dead helper teamRecordHTML — orphaned by the gc2 redesign.)

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
  const roiValCls = v => v == null ? '' : v > 0 ? 'kpi-green' : v < 0 ? 'kpi-red' : '';
  // Honest hero: the only realized betting ROI is the validated edge (from the live scoreboard);
  // the model's blind ML/totals ROI is ~0 EV and is no longer surfaced as a headline.
  const sbEdges = (scoreboardData && scoreboardData.edges) || [];
  const u8 = sbEdges.find(e => e.tag === 'UNDER_LINE_8_0');
  const dec26 = (historyData || []).filter(r => (r.actual_winner === 'home' || r.actual_winner === 'away') && !r.sp_scratched);
  const acc26 = dec26.length ? Math.round(dec26.filter(r => r.predicted_winner === r.actual_winner).length / dec26.length * 100) : null;

  const heroSection = `
    <div class="bt-context-bar">${totalGames.toLocaleString()} games logged since 2021 &nbsp;·&nbsp; Pinnacle closing lines &nbsp;·&nbsp; betting ROI shown = the validated edge only (the model's blind ML/totals is ~0 EV and isn't surfaced)</div>
    <div class="bt-kpi-row">
      <div class="bt-kpi-card">
        <div class="bt-kpi-val ${roiValCls(u8 ? u8.hist_roi_pct : null)}">${u8 && u8.hist_roi_pct != null ? (u8.hist_roi_pct >= 0 ? '+' : '') + u8.hist_roi_pct.toFixed(1) + '%' : '—'}</div>
        <div class="bt-kpi-label">UNDER 8.0 ROI <span class="bt-kpi-tag tag-good">validated · push-corrected</span></div>
        <div class="bt-kpi-sub">${u8 ? `${u8.hist_n} bets · std-vig · 2021–26${u8.recent_n ? ` · ${u8.recent_roi_pct >= 0 ? '+' : ''}${u8.recent_roi_pct}% live (n=${u8.recent_n})` : ''}` : 'see edge scoreboard'}</div>
      </div>
      <div class="bt-kpi-card">
        <div class="bt-kpi-val">${acc26 != null ? acc26 + '%' : '—'}</div>
        <div class="bt-kpi-label">2026 Win Rate <span class="bt-kpi-tag">in-sample</span></div>
        <div class="bt-kpi-sub">${dec26.length.toLocaleString()} games · predicted winner</div>
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
    <summary class="bt-sec-head bt-sec-toggle">
      <span class="bt-sec-num">02</span>
      <span class="bt-sec-title">Moneyline Analysis</span>
      <span class="bt-sec-sub">Away edge vs. home favorite trap · 2021–2025</span>
    </summary>
    <div class="bt-sec-caveat">⚠ Exploratory / calibration only — there is <b>no validated moneyline edge</b> (the away-ML angle was overfit and demoted). These OOS 2021–25 ROI tiers show where the model is least wrong, not a bettable edge. The only validated edges are in the <b>Edges</b> tab.</div>
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
    <summary class="bt-sec-head bt-sec-toggle">
      <span class="bt-sec-num">03</span>
      <span class="bt-sec-title">Model Accuracy</span>
      <span class="bt-sec-sub">Win rate by confidence level and by season</span>
    </summary>
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
    <div class="bt-signal-note">${tsbYrNote} Signal scored using pitcher quality (xFIP/SIERA/barrel%) + park factor + lineup xwOBA where available. Weather modifiers not applied historically. Bullpen xERA/K%/BB% applied from 2022 onward via prior-season caches.</div>` : '';

  const totalsSection = `
    <summary class="bt-sec-head bt-sec-toggle">
      <span class="bt-sec-num">04</span>
      <span class="bt-sec-title">Totals Performance</span>
      <span class="bt-sec-sub">OVER/UNDER prediction accuracy · historical seasons</span>
    </summary>
    <p class="bt-sec-desc">
      The model consistently predicts fewer runs than the Vegas line — a lean that has been correct ~52–53%
      of the time most seasons. That modest accuracy only becomes a (small, push-corrected) edge at the
      specific 8.0 standard-vig line above; betting the lean on every game is roughly break-even.
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
  function computeUnderBandRoi(lo, hi, allGames, stdVigOnly = false) {
    const bySeason = {};
    let totalUnits = 0, totalBets = 0, totalWins = 0;
    for (const g of allGames) {
      const ct = g.closing_total;
      const uPrice = g.under_price;
      if (ct == null || uPrice == null) continue;
      if (ct < lo || ct >= hi) continue;
      if (stdVigOnly && (uPrice > -106 || uPrice < -110)) continue;  // std-vig band [-110,-106]
      // Push-aware: a total landing exactly on the line is void (refunded), not an UNDER win.
      const oc = totalsOutcome(g.actual_total, ct);
      if (oc == null || oc === 'push') continue;
      const yr = g.season || parseInt(g.date);
      if (!yr || yr < 2022) continue;
      if (!bySeason[yr]) bySeason[yr] = { bets: 0, wins: 0, units: 0 };
      const s = bySeason[yr];
      const won = oc === 'under'; // betting UNDER wins when total stays under
      const dec = uPrice >= 0 ? 1 + uPrice / 100 : 1 - 100 / uPrice;
      s.bets++; totalBets++;
      if (won) { s.wins++; totalWins++; s.units += dec - 1; totalUnits += dec - 1; }
      else      { s.units -= 1; totalUnits -= 1; }
    }
    const roi = totalBets ? (totalUnits / totalBets * 100) : null;
    return { bySeason, totalBets, totalWins, roi };
  }

  const edge8to85  = computeUnderBandRoi(8.0, 8.5, games, true);  // 8.0 edge lives at std vig only
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
  const ec85Roi = edge85to9.roi;
  const ec8RoiStr  = ec8Roi  != null ? (ec8Roi  >= 0 ? '+' : '') + ec8Roi.toFixed(1)  + '%' : '—';
  const ec9RoiStr  = ec9Roi  != null ? (ec9Roi  >= 0 ? '+' : '') + ec9Roi.toFixed(1)  + '%' : '—';
  const ec85RoiStr = ec85Roi != null ? (ec85Roi >= 0 ? '+' : '') + ec85Roi.toFixed(1) + '%' : '—';
  const ec9Conf = bandConf(edge9to95.bySeason);

  const edgesSection = `
    <div class="bt-sec-head">
      <span class="bt-sec-num">01</span>
      <span class="bt-sec-title">Validated Edges</span>
      <span class="bt-sec-sub">Per-line UNDER performance · 9,400+ games · 2022–2026</span>
    </div>
    <p class="bt-sec-desc">
      MLB totals are set at discrete half-run increments (8.0, 8.5, 9.0, …). Each line behaves
      differently — push-corrected, a flat UNDER on 8.0 at standard vig is +5.5% (4 of 6 seasons),
      while 8.5 loses money. (Figures here void pushes — totals landing exactly on the line.)
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
          <div class="bt-ec2-band-label">UNDER · Total = 8.0</div>
          <p class="bt-ec2-desc">Our most reliable edge — push-corrected +5.5% at standard vig, profitable in 4 of 6 seasons. The market tends to over-price the over at the round 8.0 number. (Only the standard-vig band qualifies; cheaper or vig-against prices don't.)</p>
          <div class="bt-table-wrap">
            <table class="seg-table">
              <thead><tr><th>Season</th><th>Games</th><th>Win%</th><th>ROI</th></tr></thead>
              <tbody>${edgeSeasonRows(edge8to85)}</tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="bt-edge-card2 bt-ec2-dead">
        <div class="bt-ec2-stat-col ec2-dead-col">
          <div class="bt-ec2-roi-num ecr-red">${ec85RoiStr}</div>
          <div class="bt-ec2-roi-lbl">avg ROI</div>
          <div class="bt-ec2-n-stat">${edge85to9.totalBets.toLocaleString()} games</div>
          <span class="edge-cond-badge" style="background:var(--neg,#6b2a2a);color:#fca;border-color:#8b3a3a">✗ No Edge</span>
        </div>
        <div class="bt-ec2-body">
          <div class="bt-ec2-band-label">UNDER · Total = 8.5</div>
          <p class="bt-ec2-desc">The single most common total line in MLB (~22% of games), and a consistent money-loser for UNDER bettors. No edge flag is generated for 8.5 games. The 8.0 and 9.0 edges do not extend here.</p>
          <div class="bt-table-wrap">
            <table class="seg-table">
              <thead><tr><th>Season</th><th>Games</th><th>Win%</th><th>ROI</th></tr></thead>
              <tbody>${edgeSeasonRows(edge85to9)}</tbody>
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
          <div class="bt-ec2-band-label">UNDER · Total = 9.0</div>
          <p class="bt-ec2-desc">Strong 2022–2025 record (+10–14% ROI each year), but 2026 has reversed sharply (-13.9%). May be market correction or early-season variance — watch closely before leaning on this one.</p>
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
      <div class="bt-edge-takeaway2">
        <strong>How to use:</strong> Totals of 8.0 and 9.0 have historically been profitable UNDER bets.
        The 8.5 line — despite sitting between them — has not. When the Edges tab highlights a game,
        check which line triggered it and its current-season trend before betting.
      </div>
    </div>`;

  // (Props Performance lives once under Performance → Live record, not duplicated here.)

  // ── Section 05: Game Log ──────────────────────────────────────────────────
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

  // Lead with the headline KPIs + the validated edge; the deeper analytical grids and the
  // full game log are collapsed by default (tap to expand) so the tab isn't a wall of tables.
  el.innerHTML = `
    <div class="backtest-wrap">
      ${heroSection}
      ${edgesSection}
      <details class="bt-sec-collapse">${moneylineSection}</details>
      <details class="bt-sec-collapse">${calibrationSection}</details>
      <details class="bt-sec-collapse">${totalsSection}</details>
      <details class="bt-sec-collapse">
        <summary class="bt-sec-head bt-sec-toggle bt-sec-head-log">
          <span class="bt-sec-num">05</span>
          <span class="bt-sec-title">Game Log</span>
          <span class="bt-sec-sub">${games.length.toLocaleString()} games · most recent first</span>
        </summary>
        <div class="bt-table-wrap">
          <table class="bt-table">
            <thead><tr><th>Season</th><th>Date</th><th>Matchup</th><th>Predicted</th><th>Actual</th><th>Edge</th><th></th></tr></thead>
            <tbody>${logRows}</tbody>
          </table>
        </div>
      </details>
    </div>`;
}

// ── Pitcher Value Tab ─────────────────────────────────────────────────────────

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

function edgeScoreboardHTML() {
  const sb = scoreboardData;
  if (!sb || !sb.edges || !sb.edges.length) return '';
  const t = sb.actionable_total || {};
  const sgn = v => (v >= 0 ? '+' : '');
  const cls = v => (v == null ? '' : (v >= 0 ? 'sb-pos' : 'sb-neg'));
  const CLV_MIN = 5;   // need a few graded closes before CLV is worth showing
  const CLV_TIP = 'Closing-line value: how often this edge’s bet-time line beat the closing line, plus the average run-line gained. Beating the close confirms an edge faster and with less variance than ROI.';
  // 50% beat = coin-flip vs the close. A ±3pt dead band means an edge that profits for
  // other reasons (e.g. the static 8.0 mispricing) but gains no line value reads as
  // neutral, not a red "fail". The arrow doubles the colour for accessibility.
  const clvKind = p => (p >= 53) ? { c: 'sb-pos', a: '↑' }
                     : (p <= 47) ? { c: 'sb-neg', a: '↓' }
                     :             { c: 'sb-neutral', a: '→' };
  // CLV inline column: "CLV 64% ↑ +0.2" — rounded beat-rate, arrow, avg run-line value.
  const clvCell = e => {
    if (!(e.clv_n >= CLV_MIN && e.clv_beat_pct != null))
      return `<span class="ef-sb-clv ef-sb-clv-pending" title="${CLV_TIP} Accrues as games resolve with a captured closing line.">CLV –</span>`;
    const k = clvKind(e.clv_beat_pct);
    const avg = e.avg_clv_line != null ? ` <span class="ef-sb-clv-avg">${sgn(e.avg_clv_line)}${e.avg_clv_line.toFixed(1)}</span>` : '';
    const tip = `${CLV_TIP}${e.avg_clv_line != null ? ` Avg ${sgn(e.avg_clv_line)}${e.avg_clv_line} runs of line value over ${e.clv_n} graded closes.` : ''}`;
    return `<span class="ef-sb-clv ${k.c}" title="${tip}">CLV ${Math.round(e.clv_beat_pct)}% ${k.a}${avg}</span>`;
  };
  // Last-10 recent-form strip (oldest → newest): green pip = under cashed, red = lost.
  const last10 = e => {
    if (!Array.isArray(e.last10) || !e.last10.length) return '';
    const w = e.last10.filter(Boolean).length, l = e.last10.length - w;
    const pips = e.last10.map(x => `<i class="ef-pip ${x ? 'pip-w' : 'pip-l'}"></i>`).join('');
    return `<span class="ef-sb-l10" title="Last ${e.last10.length} graded bets, oldest → newest"><span class="ef-sb-l10-k">L10</span>${pips}<span class="ef-sb-l10-rec">${w}-${l}</span></span>`;
  };
  const rows = sb.edges.map(e => {
    const c = edgeConf({ confidence: e.confidence });
    const recent = (e.recent_n >= 5 && e.recent_roi_pct != null)
      ? `<span class="ef-sb-recent ${cls(e.recent_roi_pct)}">30d ${sgn(e.recent_roi_pct)}${e.recent_roi_pct}%</span>` : '';
    const tag = e.actionable ? '' : ' <span class="ef-sb-tag">watch</span>';
    return `
      <div class="ef-sb-row${e.actionable ? '' : ' ef-sb-muted'}">
        <span class="ef-sb-label">${c.icon} ${escapeHtml(e.label)}${tag}</span>
        <span class="ef-sb-roi ${cls(e.roi_pct)}">${sgn(e.roi_pct)}${e.roi_pct}%</span>
        <span class="ef-sb-n">${e.win_pct}% · ${e.n}</span>
        ${clvCell(e)}
        ${last10(e)}
        ${recent}
      </div>`;
  }).join('');
  const headline = (t.roi_pct != null)
    ? `<span class="ef-sb-headline ${cls(t.roi_pct)}">${sgn(t.roi_pct)}${t.roi_pct}% · ${t.n} bets</span>` : '';
  const headClv = (t.clv_n >= CLV_MIN && t.clv_beat_pct != null)
    ? `<span class="ef-sb-headclv ${clvKind(t.clv_beat_pct).c}" title="${CLV_TIP}">CLV ${Math.round(t.clv_beat_pct)}%</span>` : '';
  const recentHead = (t.recent_n >= 5 && t.recent_roi_pct != null)
    ? ` · 30d ${sgn(t.recent_roi_pct)}${t.recent_roi_pct}%` : '';
  const clvNote = (t.clv_n >= CLV_MIN && t.clv_beat_pct != null)
    ? `CLV beat the close ${t.clv_beat_pct}% of ${t.clv_n} — a faster, lower-variance read than ROI.`
    : 'CLV: gathering… (how often the bet-time line beat the close).';
  // Honesty flag: a strong realized ROI that CLV is NOT confirming (beat-rate below the
  // ↑ band) is almost always small-sample variance, not a durable edge. A real edge shows
  // up as closing-line value first, so surface the divergence instead of letting the big
  // green ROI stand unqualified.
  const CLV_CONFIRM = 53;   // matches clvKind's ↑ (confirming) threshold
  const roiUnconfirmed = (
    t.roi_pct != null && t.roi_pct >= 8 &&
    t.clv_n >= CLV_MIN && t.clv_beat_pct != null && t.clv_beat_pct < CLV_CONFIRM
  );
  const caution = roiUnconfirmed
    ? `<div class="ef-sb-caution" title="A genuine edge beats the closing line first; ROI catches up later. High ROI with a coin-flip CLV is usually variance over a small sample, not a repeatable edge.">⚠ The ${sgn(t.roi_pct)}${t.roi_pct}% isn't confirmed by CLV — it beat the close only ${Math.round(t.clv_beat_pct)}% of ${t.clv_n}. Treat as small-sample variance until CLV follows; don't size up on it.</div>`
    : '';
  return `
    <div class="ef-scoreboard">
      <div class="ef-sb-hdr">
        <span class="ef-sb-title">This season's results · ${sb.season}</span>
        <span class="ef-sb-head-metrics">${headline}${headClv}</span>
      </div>
      <div class="ef-sb-sub">ROI = realized return, flat $1/bet${recentHead}. <span class="${(t.clv_n >= CLV_MIN) ? '' : 'ef-sb-clv-pending'}" title="${CLV_TIP}">${clvNote}</span></div>
      ${caution}
      ${equityChartHTML(sb)}
      <div class="ef-sb-rowhdr">By edge <span class="ef-sb-rowhdr-note">(record per category · watch shown for context, not in the curve)</span></div>
      ${rows}
    </div>`;
}

// Season equity curve — cumulative units (flat 1u/bet) over the actionable plays.
// Inline SVG (no chart lib); stretches to width via preserveAspectRatio="none".
function equityChartHTML(sb) {
  const pts = sb.equity || [];
  if (pts.length < 2) return '';
  const t = sb.actionable_total || {};
  const W = 320, H = 72, pad = 5;
  const us = pts.map(p => p.u);
  // Autoscale to the data (padded) so the per-bet wins/losses use the full height.
  let lo = Math.min(...us), hi = Math.max(...us);
  const padU = (hi - lo) * 0.08 || 1;
  lo -= padU; hi += padU;
  const range = (hi - lo) || 1;
  const X = i => pad + (i / (pts.length - 1)) * (W - 2 * pad);
  const Y = u => pad + (1 - (u - lo) / range) * (H - 2 * pad);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)} ${Y(p.u).toFixed(1)}`).join(' ');
  const area = `${line} L${X(pts.length - 1).toFixed(1)} ${Y(lo).toFixed(1)} L${X(0).toFixed(1)} ${Y(lo).toFixed(1)} Z`;
  // Breakeven line only when 0 is within the visible (padded) range.
  const zeroLine = (lo <= 0 && hi >= 0)
    ? `<line x1="${pad}" y1="${Y(0).toFixed(1)}" x2="${W - pad}" y2="${Y(0).toFixed(1)}" class="ef-equity-zero"/>` : '';
  const up = (t.units ?? 0) >= 0;
  const col = up ? '#34d399' : '#f87171';
  const uSign = (t.units ?? 0) >= 0 ? '+' : '';
  const rSign = (t.roi_pct ?? 0) >= 0 ? '+' : '';
  // Tie the curve explicitly to the categories below it: it's every ACTIONABLE play
  // (the boosted edges), one bet per game, excluding the watch edge(s).
  const shortLabel = s => (s || '').replace(/^Under Edge:\s*/, '');
  const incl = (sb.edges || []).filter(e => e.actionable).map(e => shortLabel(e.label));
  const excl = (sb.edges || []).filter(e => !e.actionable).map(e => shortLabel(e.label));
  const legend = `Combines every actionable play — ${incl.join(' + ') || 'the boosted edges'}` +
                 (excl.length ? ` · excludes ${excl.join(', ')} (watch)` : '');
  return `
      <div class="ef-equity">
        <div class="ef-equity-hdr">
          <span class="ef-equity-title">Cumulative profit · all actionable plays</span>
          <span class="ef-equity-val ${up ? 'sb-pos' : 'sb-neg'}">${uSign}${(t.units ?? 0).toFixed(1)}u · ${rSign}${t.roi_pct}%</span>
        </div>
        <div class="ef-equity-legend">${escapeHtml(legend)}</div>
        <svg class="ef-equity-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Cumulative profit over the season, per bet">
          ${zeroLine}
          <path d="${area}" fill="${col}" fill-opacity="0.13"/>
          <path d="${line}" fill="none" stroke="${col}" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
        </svg>
        <div class="ef-equity-foot">
          <span>${escapeHtml(pts[0].d)}</span>
          <span>flat 1u/bet · ${t.n} plays</span>
          <span>${escapeHtml(pts[pts.length - 1].d)}</span>
        </div>
      </div>`;
}

// ── Edges tab: plain-language plays + track record ────────────────────────────

function _seasonsRecord(e) {
  const sr = e.season_roi || {};
  const yrs = Object.keys(sr);
  if (yrs.length <= 1) return yrs.length === 1 ? `${yrs[0]} only` : '';
  const pos = yrs.filter(y => (sr[y] ?? 0) > 0).length;
  return `${pos}/${yrs.length} seasons profitable`;
}

function _recLine(e) {
  const roi = `${e.roi_pct >= 0 ? '+' : ''}${e.roi_pct.toFixed(1)}% ROI`;
  return (e.confidence === 'emerging' || e.confidence === 'new')
    ? `${roi} · 2026 only, small sample`
    : `${roi} · ${_seasonsRecord(e)}`;
}

function _whyLine(g, e) {
  const total = g.odds?.total, pred = g.prediction?.predicted_total;
  if (total == null || pred == null) return 'A validated under edge on this total line.';
  if (e.tag === 'UNDER_MODEL_DEV') {
    const gap = (total - pred).toFixed(1);
    return `Our model projects ${pred.toFixed(1)} runs — ${gap} below the ${total} line.`;
  }
  return `Our model projects ${pred.toFixed(1)} runs — under the ${total} line.`;
}

// ½-Kelly suggested stake for a play. Returns {stake, frac} | 'no-bank' | null.
function edgePlayStake(g, e) {
  const bankroll = parseFloat(localStorage.getItem('mlbedge_bankroll') || '0');
  if (!bankroll || bankroll <= 0) return 'no-bank';
  if (!e || e.kelly_win_prob == null || g.odds?.under_price == null) return null;
  const fraction = parseFloat(localStorage.getItem('mlbedge_kelly_fraction') || '0.5');
  const dec = americanToDecimal(g.odds.under_price), b = dec - 1;
  const fullK = b > 0 ? (b * e.kelly_win_prob - (1 - e.kelly_win_prob)) / b : 0;
  if (fullK <= 0) return null;
  const stake = Math.round(bankroll * fullK * fraction);
  return stake >= 1 ? { stake, frac: _kellyFracLabel(fraction) } : null;
}

function edgePlayCardHTML(g) {
  const unders = (g.edge_conditions || [])
    .filter(e => e.direction === 'UNDER')
    .sort((a, b) => (b.signal_boost ?? 0) - (a.signal_boost ?? 0));
  const top = unders.find(e => (e.signal_boost ?? 0) > 0) || unders[0];
  const c = edgeConf(top);
  const total = g.odds?.total;
  const time = g.game_time_et ? ` · ${escapeHtml(g.game_time_et)}` : '';

  const st = edgePlayStake(g, top);
  let stakeRow = '';
  if (st === 'no-bank') {
    stakeRow = `<button class="ef-play-setbank" onclick="gotoView('games')">Set a bankroll to size your bet →</button>`;
  } else if (st) {
    stakeRow = `<div class="ef-play-row"><span class="ef-play-k">Suggested bet</span><span class="ef-play-amt">$${st.stake.toLocaleString()}</span><span class="ef-play-frac">${st.frac}</span></div>`;
  }

  const others = unders.filter(e => e !== top);
  const details = `
      <details class="ef-play-details">
        <summary>details</summary>
        <div class="ef-play-detail-body">
          <div>Validated on ${top.n_games.toLocaleString()} games — ${escapeHtml(top.seasons)}</div>
          ${seasonRoiStripHTML(top)}
          ${g.odds?.under_price != null ? `<div>Bet price: UNDER ${total} at ${g.odds.under_price}</div>` : ''}
          ${others.length ? `<div>Also flagged: ${others.map(e => escapeHtml(e.label)).join(', ')}</div>` : ''}
        </div>
      </details>`;

  return `
    <div class="ef-play">
      <div class="ef-play-top">
        <span class="ef-conf-badge ${c.efCls}">${c.icon} ${escapeHtml(c.label)}</span>
        <span class="ef-play-match">${escapeHtml(g.away_team)} @ ${escapeHtml(g.home_team)}${time}</span>
      </div>
      <div class="ef-play-bet">Bet the UNDER ${total ?? ''}</div>
      <div class="ef-play-why">${escapeHtml(_whyLine(g, top))}</div>
      <div class="ef-play-row"><span class="ef-play-k">Track record</span><span class="ef-play-rec">${escapeHtml(_recLine(top))}</span></div>
      ${stakeRow}
      ${details}
    </div>`;
}

function edgeWatchHTML(games) {
  if (!games.length) return '';
  const items = games.map(g =>
    `<li>${escapeHtml(g.away_team)} @ ${escapeHtml(g.home_team)}${g.odds?.total != null ? ` · ${g.odds.total} total` : ''}</li>`).join('');
  return `
    <details class="ef-watch">
      <summary>Watching, not betting — ${games.length} game${games.length !== 1 ? 's' : ''}</summary>
      <div class="ef-watch-body">
        <p>These hit the 9.0-line under — historically strong, but down in 2026, so we're sitting them out.</p>
        <ul class="ef-watch-list">${items}</ul>
      </div>
    </details>`;
}

function edgesHowToHTML() {
  return `
    <details class="ef-howto">
      <summary>How to read this</summary>
      <div class="ef-howto-body">
        <p>Each play is an UNDER bet that our model and several seasons of data agree the market has mispriced. We only surface bets that beat real closing odds across multiple seasons.</p>
        <p><b>Confidence:</b> ⚡ Strong = positive multi-season ROI (push-corrected) · ★ Emerging = promising but 2026 only · ⚠ Watch = historically positive but down this season (not bet).</p>
        <p><b>Track record</b> on each play is its validated multi-season ROI. <b>This season's results</b> below show how the edges have actually done in 2026 — including CLV, whether our bet-time line beat the closing line.</p>
      </div>
    </details>`;
}

function renderEdgesView() {
  const el = document.getElementById('edges-view');
  const allGames = gamesData?.games || [];
  const preview = allGames.filter(g => g.edge_conditions?.length && (g.game_status || 'preview') === 'preview');
  const isActionable = e => e.direction === 'UNDER' && (e.signal_boost ?? 0) > 0;

  const plays = preview
    .filter(g => (g.edge_conditions || []).some(isActionable))
    .sort((a, b) => (edgeStrength(b) - edgeStrength(a)) || (edgeTopRoi(b) - edgeTopRoi(a)));
  const watch = preview.filter(g => !(g.edge_conditions || []).some(isActionable));

  const playsHTML = plays.length
    ? plays.map(edgePlayCardHTML).join('')
    : `<div class="ef-empty">No actionable plays today — no game landed on a validated edge line. The track record below still applies.</div>`;

  el.innerHTML = `
    <div class="view-header">
      <h1>Edges</h1>
      <span class="sub-label">Where our model and the market disagree — validated on 9,400+ games since 2021.</span>
    </div>
    ${edgesHowToHTML()}
    <section class="ef-section">
      <div class="ef-section-hdr">
        <h2 class="ef-section-title">Today's Plays</h2>
        <span class="ef-section-count">${plays.length} play${plays.length !== 1 ? 's' : ''}</span>
      </div>
      ${playsHTML}
    </section>
    <section class="ef-section">
      ${edgeScoreboardHTML()}
    </section>
    ${edgeWatchHTML(watch)}`;
}

// ── Reversion tool: good hitters slumping on bad luck (informational) ──────────
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
        <h2 class="support-section-title">What We Test — and What We Don't Bet</h2>
        <p class="support-body">
          Every signal in this app is held to one standard: it must beat real closing odds,
          across multiple seasons, and survive out-of-sample testing — not just look good on
          recent data. Most betting angles fail that bar. We've run season-by-season ROI
          backtests (2021–2026) on every bet type and model signal the app produces; the table
          below summarizes what we found and why most are shown as informational context rather
          than recommended picks. The main angle that clears the bar is the UNDER on
          specific low total lines — notably 8.0 at standard vig — which is push-corrected +5.5%
          and profitable in 4 of 6 seasons, and forms the basis of the recommended edges.
          Everything else either failed validation, was driven by a single lucky season, or proved
          statistically indistinguishable from random noise. (All ROI voids pushes — totals landing
          exactly on the line — which we found had previously been miscounted as wins.)
        </p>
        <table class="val-table">
          <thead><tr><th>Bet / Angle</th><th>What the data showed</th><th>Status</th></tr></thead>
          <tbody>
            <tr><td>UNDER total = 8.0 (standard vig)</td><td>+5.5% ROI, profitable 4 of 6 seasons (2021–26), push-corrected</td><td><span class="val-tag val-yes">Active edge</span></td></tr>
            <tr><td>UNDER total = 9.0</td><td>Only +2.6% 2021–25 (push-corrected), then −11.3% in 2026</td><td><span class="val-tag val-watch">Watch only</span></td></tr>
            <tr><td>Model–Vegas total gap (UNDER)</td><td>+36% in 2026 but only one season of data</td><td><span class="val-tag val-watch">Emerging</span></td></tr>
            <tr><td>Moneyline — "Elite Away"</td><td>Overfit: ~+14% in-sample vs ~−12% out-of-sample</td><td><span class="val-tag val-no">Not used</span></td></tr>
            <tr><td>Moneyline — "High Confidence"</td><td>64% win rate but −2% ROI (favorites don't pay)</td><td><span class="val-tag val-no">Not used</span></td></tr>
            <tr><td>Monte Carlo divergence</td><td>No predictive edge; mildly anti-predictive on the moneyline</td><td><span class="val-tag val-no">Informational</span></td></tr>
            <tr><td>Player props (HR / Hit / K)</td><td>−EV on hit rate; real-odds validation now in progress</td><td><span class="val-tag val-watch">Testing</span></td></tr>
            <tr><td>Pitcher moneyline "consistent edge"</td><td>Zero persistence (r ≈ 0) — small-sample noise</td><td><span class="val-tag val-no">Removed</span></td></tr>
            <tr><td>Team totals</td><td>No skill vs a fair line (+0.4 pp over a blind bet)</td><td><span class="val-tag val-no">Not pursued</span></td></tr>
          </tbody>
        </table>
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
