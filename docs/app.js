'use strict';

// ── Data sources ─────────────────────────────────────────────────────────────
const GAMES_URL   = './games.json';
const HISTORY_URL = './history.json';

// ── App state ─────────────────────────────────────────────────────────────────
let gamesData   = null;
let historyData = [];
let expandedPk  = null;
let currentView = 'games';

// ── Bootstrap ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  setupNav();
  await Promise.all([loadGames(), loadHistory()]);
  renderGamesView();
});

// ── Navigation ────────────────────────────────────────────────────────────────
function setupNav() {
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      currentView = btn.dataset.view;
      document.querySelectorAll('.nav-btn').forEach(b => b.classList.toggle('active', b === btn));
      document.getElementById('games-view').hidden  = currentView !== 'games';
      document.getElementById('record-view').hidden = currentView !== 'record';
      if (currentView === 'record') renderRecordView();
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
    <div class="generated-at">Updated: ${formatGeneratedAt(gamesData.generated_at)}</div>
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
  const pred   = g.prediction || {};
  const homePct = Math.round((pred.home_win_pct || 0.5) * 100);
  const awayPct = 100 - homePct;
  const favIsHome = homePct >= awayPct;
  const favTeam = abbrev(favIsHome ? g.home_team : g.away_team);
  const favPct  = Math.max(homePct, awayPct);

  const hXera = g.home_sp?.season?.xera;
  const aXera = g.away_sp?.season?.xera;

  const timeStr  = g.game_time_et || formatTimeET(g.game_time_utc);
  const oddsStr  = g.odds ? formatOddsLine(g.odds, g.home_team) : '';
  const wxStr    = formatWeather(g.weather);

  const hFlags = (g.home_sp?.trend_flags || []).slice(0, 1);
  const aFlags = (g.away_sp?.trend_flags || []).slice(0, 1);

  return `
<div class="game-card" data-pk="${g.gamePk}">
  <div class="game-card-header">
    <div class="matchup-grid">
      <div class="team-cell away-cell">
        <span class="team-name">${g.away_team}</span>
        <span class="sp-line">${g.away_sp?.name || 'TBD'}${aXera != null ? spEra(aXera) : ''}</span>
        ${aFlags.map(f => `<span class="trend-pill">${f}</span>`).join('')}
      </div>
      <div class="game-info-cell">
        <span class="game-time">${timeStr}</span>
        <span class="venue-name">${g.venue}</span>
        ${oddsStr ? `<span class="odds-display">${oddsStr}</span>` : ''}
        ${wxStr   ? `<span class="weather-display">${wxStr}</span>`   : ''}
      </div>
      <div class="team-cell home-cell">
        <span class="team-name">${g.home_team}</span>
        <span class="sp-line">${g.home_sp?.name || 'TBD'}${hXera != null ? spEra(hXera) : ''}</span>
        ${hFlags.map(f => `<span class="trend-pill">${f}</span>`).join('')}
      </div>
    </div>
    <div class="pred-strip">
      <span class="pred-fav">${favTeam} ${favPct}%</span>
      ${pred.predicted_away_runs != null
        ? `<span class="pred-score">${pred.predicted_away_runs} – ${pred.predicted_home_runs} est.</span>`
        : ''}
      <span class="expand-arrow">▼</span>
    </div>
  </div>
  <div class="game-card-body" hidden>
    ${expandedBodyHTML(g)}
  </div>
</div>`;
}

function spEra(val) {
  return ` <span class="xera-tag">xERA ${val.toFixed(2)}</span>`;
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
    <div class="section-heading">Prediction</div>
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
    const flagDot = b.trend_flags?.length
      ? ` <span class="batter-dot" title="${escapeHtml(b.trend_flags[0])}">●</span>`
      : '';
    return `
  <tr${b.trend_flags?.length ? ' class="batter-flagged"' : ''}>
    <td class="bo">${b.batting_order}</td>
    <td class="bname">${shortName(b.name)}${flagDot}</td>
    <td>${b.xwoba != null ? fmtWoba(b.xwoba) : dash()}</td>
    <td>${b.avg_ev != null ? b.avg_ev.toFixed(1) : dash()}</td>
    <td>${b.hard_hit_pct != null ? fmtPct(b.hard_hit_pct) : dash()}</td>
    <td>${b.k_pct != null ? fmtPct(b.k_pct) : dash()}</td>
    <td>${b.bb_pct != null ? fmtPct(b.bb_pct) : dash()}</td>
  </tr>`;
  }).join('');

  return `
<table class="lineup-table">
  <thead>
    <tr><th>#</th><th>Name</th><th>xwOBA</th><th>EV</th><th>HH%</th><th>K%</th><th>BB%</th></tr>
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

  function edgeBadge(val, homeLabel, awayLabel) {
    if (val == null) return '';
    const abs = Math.abs(val);
    const label = abs < 0.03
      ? 'Even'
      : val > 0
        ? `${homeLabel} +${(abs * 100).toFixed(0)}`
        : `${awayLabel} +${(abs * 100).toFixed(0)}`;
    const cls = abs < 0.03 ? 'neutral' : val > 0 ? 'edge-home' : 'edge-away';
    return `<span class="sig-badge ${cls}">${label}</span>`;
  }

  return `
<div class="prediction-block">
  <div class="prob-bar-wrap">
    <span class="prob-label">${g.away_team} ${awayPct}%</span>
    <div class="prob-bar">
      <div class="prob-fill away-fill" style="width:${awayPct}%"></div>
      <div class="prob-fill home-fill" style="width:${homePct}%"></div>
    </div>
    <span class="prob-label">${g.home_team} ${homePct}%</span>
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
  if (wx.condition === 'Dome') return '🏟 Dome';
  const parts = [];
  if (wx.temp_f != null) parts.push(`${wx.temp_f}°F`);
  if (wx.wind_dir) parts.push(wx.wind_dir);
  return parts.join(', ');
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

function shortName(name) {
  if (!name) return '—';
  const parts = name.split(' ');
  if (parts.length >= 2) return parts[0][0] + '. ' + parts.slice(1).join(' ');
  return name;
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
