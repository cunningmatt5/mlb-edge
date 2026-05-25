'use strict';

// ── Data sources ─────────────────────────────────────────────────────────────
const GAMES_URL    = './games.json';
const HISTORY_URL  = './history.json';
const BACKTEST_URL = './backtest.json';

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
let expandedPk   = null;
let currentView  = 'games';
let lastCheckedAt = null;

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
      document.getElementById('record-view').hidden   = currentView !== 'record';
      document.getElementById('backtest-view').hidden = currentView !== 'backtest';
      if (currentView === 'record') renderRecordView();
      if (currentView === 'backtest') { loadBacktest().then(renderBacktestView); }
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

// ── Auto-refresh ──────────────────────────────────────────────────────────────
let _autoTimer = null;

function startAutoRefresh() {
  if (_autoTimer) clearInterval(_autoTimer);
  _autoTimer = setInterval(async () => {
    const prev = gamesData?.generated_at;
    await loadGames();
    lastCheckedAt = Date.now();
    if (gamesData?.generated_at !== prev) {
      expandedPk = null;
      renderGamesView();
    } else {
      updateFooter();
    }
  }, 5 * 60 * 1000);
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
function gameCardHTML(g) {
  const hXera = g.home_sp?.season?.xera;
  const aXera = g.away_sp?.season?.xera;

  const timeStr  = g.game_time_et || formatTimeET(g.game_time_utc);
  const oddsStr  = g.odds ? formatOddsLine(g.odds, g.home_team) : '';
  const wxStr    = formatWeather(g.weather);

  const hFlags  = (g.home_sp?.trend_flags || []).slice(0, 1);
  const aFlags  = (g.away_sp?.trend_flags || []).slice(0, 1);
  const status  = g.game_status || 'preview';
  const fav     = (g.prediction?.home_win_pct ?? 0.5) >= 0.5 ? 'home' : 'away';

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
        ${aFlags.map(f => `<span class="trend-pill">${f}</span>`).join('')}
      </div>
      <div class="game-info-cell">
        <span class="game-time">${timeStr}</span>
        <span class="venue-name">${g.venue}</span>
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
        ${hFlags.map(f => `<span class="trend-pill">${f}</span>`).join('')}
      </div>
    </div>
    ${lineupStatusHTML(g)}
    ${statusStrip(g)}
  </div>
  <div class="game-card-body" hidden>
    ${expandedBodyHTML(g)}
  </div>
</div>`;
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
  const favTeam = abbrev(homePct >= awayPct ? g.home_team : g.away_team);
  const favPct  = Math.max(homePct, awayPct);
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
  <span class="pred-fav">${favTeam} ${favPct}%</span>
  ${scoreCenter}
  <span class="expand-arrow">▼</span>
</div>`;
}

function spEra(val, side = 'home') {
  const cls = side === 'away' ? 'xera-tag xera-tag-away' : 'xera-tag';
  return `<span class="${cls}">xERA ${val.toFixed(2)}</span>`;
}

// ── Lineup status + batter highlights (collapsed card) ───────────────────────
function lineupStatusHTML(g) {
  const isOfficial = g.lineup_status !== 'tbd';
  const statusChip = isOfficial
    ? `<span class="lineup-chip official">✓ Official</span>`
    : `<span class="lineup-chip tbd">Lineups TBD</span>`;

  if (!isOfficial) {
    return `
<div class="lineup-status-row">
  <div class="bh-side bh-away"></div>
  <div class="ls-center">${statusChip}</div>
  <div class="bh-side bh-home"></div>
</div>`;
  }

  const awayBatters = getNotableBatters(g.away_lineup);
  const homeBatters = getNotableBatters(g.home_lineup);
  const chip = b =>
    `<span class="bh-chip ${b._type}">${shortName(b.name)} · ${b._label}</span>`;

  return `
<div class="lineup-status-row">
  <div class="bh-side bh-away">${awayBatters.map(chip).join('')}</div>
  <div class="ls-center">${statusChip}</div>
  <div class="bh-side bh-home">${homeBatters.map(chip).join('')}</div>
</div>`;
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
${allFlags.length ? `<div class="flag-row">${allFlags.join('')}</div>` : ''}`;
}

// ── Lineups ───────────────────────────────────────────────────────────────────
function lineupsHTML(g) {
  if (g.lineup_status === 'tbd') {
    return `<div class="lineup-tbd">
      Lineups not yet posted — check back closer to game time.
    </div>`;
  }

  return `
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

// ── Record view ───────────────────────────────────────────────────────────────
function renderRecordView() {
  const view     = document.getElementById('record-view');
  const resolved = historyData.filter(r => r.actual_winner != null);

  if (!resolved.length) {
    view.innerHTML = `<div class="empty-state">No resolved predictions yet.<br>Check back after games have been played.</div>`;
    return;
  }

  const signals  = calcSignalAccuracy(resolved);
  const overall  = signals.overall;
  const pct      = overall.total > 0 ? Math.round(overall.correct / overall.total * 100) : null;

  view.innerHTML = `
<div class="view-header">
  <h1>Prediction Record</h1>
  <span class="sub-label">${resolved.length} resolved games${pct != null ? ' &nbsp;·&nbsp; ' + pct + '% overall' : ''}</span>
</div>

<div class="signal-section">
  <div class="section-heading">Signal Accuracy</div>
  <div class="signal-grid">
    ${Object.values(signals).map(s => signalCardHTML(s)).join('')}
  </div>
</div>

<div class="history-section">
  <div class="section-heading">Game Log</div>
  <table class="history-table">
    <thead>
      <tr><th>Date</th><th>Matchup</th><th>Predicted</th><th>Actual</th><th></th></tr>
    </thead>
    <tbody>
      ${resolved.slice().reverse().map(r => {
        const hit  = r.predicted_winner === r.actual_winner;
        const pred = r.predicted_winner === 'home' ? r.home_team : r.away_team;
        const act  = r.actual_winner    === 'home' ? r.home_team : r.away_team;
        const score = (r.home_score != null && r.away_score != null)
          ? ` (${r.away_score}–${r.home_score})`
          : '';
        return `
      <tr class="${hit ? 'row-hit' : 'row-miss'}">
        <td>${r.date}</td>
        <td>${abbrev(r.away_team)} @ ${abbrev(r.home_team)}</td>
        <td>${abbrev(pred)} (${Math.round((r.home_win_pct || 0.5) * 100)}%)</td>
        <td>${abbrev(act)}${score}</td>
        <td class="result-icon">${hit ? '✓' : '✗'}</td>
      </tr>`;
      }).join('')}
    </tbody>
  </table>
</div>`;
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

function calcSignalAccuracy(resolved) {
  const m = {
    overall:       { label: 'Overall pick accuracy',           correct: 0, total: 0 },
    pitcherHome:   { label: 'Home pitcher score edge ≥ 5pts',  correct: 0, total: 0 },
    pitcherAway:   { label: 'Away pitcher score edge ≥ 5pts',  correct: 0, total: 0 },
    lineupHome:    { label: 'Home lineup score edge ≥ 5pts',   correct: 0, total: 0 },
    compsHome:     { label: 'Comps home rate ≥ 55%',           correct: 0, total: 0 },
  };

  for (const r of resolved) {
    const homeWon = r.actual_winner === 'home';
    m.overall.total++;
    if ((r.predicted_winner === 'home') === homeWon) m.overall.correct++;

    const ph = r.pitcher_score_home, pa = r.pitcher_score_away;
    if (ph != null && pa != null) {
      if (ph - pa >= 0.05) { m.pitcherHome.total++; if (homeWon) m.pitcherHome.correct++; }
      if (pa - ph >= 0.05) { m.pitcherAway.total++; if (!homeWon) m.pitcherAway.correct++; }
    }

    const lh = r.lineup_score_home, la = r.lineup_score_away;
    if (lh != null && la != null && lh - la >= 0.05) {
      m.lineupHome.total++;
      if (homeWon) m.lineupHome.correct++;
    }

    if (r.comps_home_win_rate != null && r.comps_home_win_rate >= 0.55) {
      m.compsHome.total++;
      if (homeWon) m.compsHome.correct++;
    }
  }

  return m;
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

function formatOddsLine(odds, homeTeam) {
  const parts = [];
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

function abbrev(team) {
  if (!team) return '';
  const words = team.split(' ');
  return words[words.length - 1];
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

  const { stats, games = [], total_games, season } = backtestData;
  if (!stats) {
    el.innerHTML = `<div class="empty-state"><p>No backtest stats found in data.</p></div>`;
    return;
  }

  const pct = v => v != null ? (v * 100).toFixed(1) + '%' : '—';
  const num = (v, d=2) => v != null ? v.toFixed(d) : '—';
  const signedNum = v => v != null ? (v >= 0 ? '+' : '') + v.toFixed(2) : '—';

  // ── Summary bar ──────────────────────────────────────────────────────────
  const overallPct  = pct(stats.win_pct_overall);
  const totalDecided = stats.total_decided || 0;
  const totalMAE    = num(stats.total_mae);
  const totalBias   = signedNum(stats.total_bias);
  const biasClass   = (stats.total_bias || 0) > 0 ? 'bias-high' : 'bias-low';

  // ── Confidence bars ───────────────────────────────────────────────────────
  const tierLabels = { '50_55': '50–55%', '55_60': '55–60%', '60_65': '60–65%', '65_plus': '65%+' };
  const confGrid = Object.entries(stats.win_pct_by_confidence || {}).map(([key, t]) => {
    const barPct = t.pct != null ? Math.round(t.pct * 100) : 0;
    const barClass = barPct >= 55 ? 'conf-bar-good' : barPct >= 50 ? 'conf-bar-ok' : 'conf-bar-bad';
    return `
      <div class="conf-bar-row">
        <span class="conf-label">${tierLabels[key] || key}</span>
        <div class="conf-bar-track">
          <div class="conf-bar-fill ${barClass}" style="width:${Math.min(100,barPct*1.4)}%"></div>
        </div>
        <span class="conf-stat">${pct(t.pct)}</span>
        <span class="conf-count">(${t.total ?? 0} games)</span>
      </div>`;
  }).join('');

  // ── Signal accuracy ───────────────────────────────────────────────────────
  const sigAcc = stats.signal_accuracy || {};
  const pit = sigAcc.pitcher || {};
  const cmp = sigAcc.comps   || {};

  // ── Game log ─────────────────────────────────────────────────────────────
  const rows = games.slice(0, 200).map(g => {
    const winnerTeam = g.predicted_winner === 'home' ? g.home_team : g.away_team;
    const actualTeam = g.actual_winner === 'home' ? g.home_team : g.away_team;
    const conf = Math.round(Math.max(g.home_win_pct, g.away_win_pct) * 100);
    const rowClass = g.correct ? 'row-hit' : g.actual_winner === 'tie' ? '' : 'row-miss';
    const icon = g.actual_winner === 'tie' ? '—' : (g.correct ? '✓' : '✗');
    const dateFmt = g.date ? g.date.slice(5).replace('-', '/') : '—';
    const predTotal = g.predicted_total != null ? g.predicted_total.toFixed(1) : '—';
    return `<tr class="${rowClass}">
      <td class="bt-date">${dateFmt}</td>
      <td class="bt-matchup">${abbrev(g.away_team)} @ ${abbrev(g.home_team)}</td>
      <td class="bt-pred">${abbrev(winnerTeam)} <span class="bt-conf">${conf}%</span></td>
      <td class="bt-actual">${abbrev(actualTeam)} <span class="bt-score">${g.away_score}–${g.home_score}</span></td>
      <td class="bt-total">${predTotal} / ${g.actual_total ?? '—'}</td>
      <td class="bt-icon ${g.correct ? 'icon-correct' : (g.actual_winner === 'tie' ? '' : 'icon-wrong')}">${icon}</td>
    </tr>`;
  }).join('');

  el.innerHTML = `
    <div class="backtest-wrap">

      <div class="backtest-summary">
        <div class="bt-summary-stat">
          <span class="bt-big">${overallPct}</span>
          <span class="bt-label">Win Accuracy</span>
        </div>
        <div class="bt-summary-divider"></div>
        <div class="bt-summary-stat">
          <span class="bt-big">${totalDecided}</span>
          <span class="bt-label">${season} Games</span>
        </div>
        <div class="bt-summary-divider"></div>
        <div class="bt-summary-stat">
          <span class="bt-big">${totalMAE}</span>
          <span class="bt-label">Total MAE</span>
        </div>
        <div class="bt-summary-divider"></div>
        <div class="bt-summary-stat">
          <span class="bt-big ${biasClass}">${totalBias}</span>
          <span class="bt-label">Pred Bias</span>
        </div>
      </div>

      <div class="bt-section-title">Win % by Confidence Tier</div>
      <div class="confidence-grid">${confGrid}</div>

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
      </div>

      <div class="bt-section-title">Game Log <span class="bt-count">(${games.length} games, most recent first)</span></div>
      <div class="bt-table-wrap">
        <table class="bt-table">
          <thead>
            <tr>
              <th>Date</th><th>Matchup</th><th>Predicted</th><th>Actual</th><th>Total P/A</th><th></th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>

    </div>`;
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

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
