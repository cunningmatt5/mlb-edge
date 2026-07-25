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
let currentView  = 'edges';
let lastCheckedAt = null;

// ── Bootstrap ─────────────────────────────────────────────────────────────────
// Edges is the landing view. gamesData is still loaded — the Edges tab uses today's
// slate for its qualifiers and reversion tags — there's just no standalone Games tab.
document.addEventListener('DOMContentLoaded', async () => {
  setupNav();
  await Promise.all([loadGames(), loadHistory(), loadEdgeScoreboard()]);
  lastCheckedAt = Date.now();
  renderEdgesView();
  loadBacktest().then(renderEdgesView);   // heavy file fills in the season audit after
  startAutoRefresh();
});

// ── Navigation ────────────────────────────────────────────────────────────────
// Three top-level views: edges, performance, support. The Games tab (scores, lineups,
// per-game model) was cut — users come here for the data-driven UNDER + reversion edges,
// not box scores available anywhere. Each view is a single flat page.
const _PARENT_VIEWS = ['edges', 'performance', 'support'];

function setupNav() {
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      currentView = btn.dataset.view;
      document.querySelectorAll('.nav-btn').forEach(b => b.classList.toggle('active', b === btn));
      btn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
      for (const v of _PARENT_VIEWS) {
        document.getElementById(v + '-view').hidden = currentView !== v;
      }
      // backtest.json is large; render as soon as the light files land, then re-render when
      // the game-level record arrives so the qualifying-games audit fills in.
      if (currentView === 'edges') {
        Promise.all([loadGames(), loadEdgeScoreboard()]).then(renderEdgesView);
        loadBacktest().then(renderEdgesView);
      }
      if (currentView === 'support')   renderSupportView();
      if (currentView === 'performance') {
        Promise.all([loadBacktest(), loadEdgeScoreboard()]).then(renderPerformanceView);
      }
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
    if (currentView === 'edges') renderEdgesView();
    else updateFooter();
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
    if (currentView === 'edges') renderEdgesView();
    else updateFooter();
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
// Edge strength for ranking = the actual (vig-adjusted) signal boost.
const edgeStrength = g => Math.max(0, ...(g.edge_conditions || []).map(e => e.signal_boost ?? 0));

// Compact per-season ROI chips (green/red by sign), computed live from the reconciled data
// via edgeHistorical — not the pipeline's frozen season_roi. Empty until backtest loads.
function seasonRoiStripHTML(e) {
  const h = edgeHistorical(e.tag);
  if (!h || !h.bySeason) return '';
  const chips = Object.keys(h.bySeason).sort().map(yr => {
    const s = h.bySeason[yr];
    const roi = s.bets ? s.units / s.bets * 100 : 0;
    const pos = roi >= 0;
    return `<span class="ef-season-chip ${pos ? 'pos' : 'neg'}">`
         + `${yr.slice(2)} ${pos ? '+' : ''}${roi.toFixed(0)}%</span>`;
  }).join('');
  return chips ? `<div class="ef-season-strip" title="ROI by season (recomputed from data)">${chips}</div>` : '';
}
// Totals outcome vs a line: 'over' | 'under' | 'push' (final runs exactly on the line).
// Pushes are void — never scored as a win or loss. null when inputs are missing.
function totalsOutcome(actualTotal, line) {
  if (actualTotal == null || line == null) return null;
  if (Math.abs(actualTotal - line) < 1e-9) return 'push';
  return actualTotal > line ? 'over' : 'under';
}

// Plain-language definition of each edge, keyed by the tag the pipeline emits. One source
// for every place an edge is named — the play card, the how-to glossary, and the audit —
// so a user always sees the same short/long explanation of what triggered a play.
const EDGE_DEFINITIONS = {
  UNDER_LINE_8_0: {
    name: 'Total = 8.0',
    short: 'The total sits exactly on 8.0 — a round number the market has historically over-priced the OVER on. Bet the UNDER at standard vig.',
    long: 'Fires whenever the closing total is exactly 8.0 — on the line alone (the old model-lean requirement was dropped; it halved the plays for no ROI gain). Push-corrected, a blind UNDER at standard vig (−110 to −106) is the app’s one durably profitable spot across seasons. The price is what matters, not the model: at −105 or cheaper the market has already moved (edge thinner), and at −111 or steeper the vig eats it.',
    // The +EV window is narrow, so price matters more than for any other edge. Reconciled
    // 2021-26 blind slice: −110..−106 = +6.2%, cheaper than −106 = +1.6%, steeper = negative.
    price: {
      lo: -110, hi: -106, window: '−110 to −106', best: '−106',
      guide: 'Playable at −110 to −106 (−106 is the best number in that window). At −111 or steeper the extra juice has made it a loser historically — don’t take it there, but the edge is real, so shop your other books for the number. At −105 or cheaper the market has already leaned under, so the edge is thinner. Take the shown price or better within the window, never worse.',
    },
  },
  UNDER_LINE_9_0: {
    name: 'Total = 9.0',
    short: 'The total sits exactly on 9.0 — bet the UNDER at standard vig, same play as 8.0.',
    long: 'Fires whenever the closing total is exactly 9.0. Push-corrected, a blind UNDER at standard vig (−110 to −106) is +6.8% across 2021–25 — comparable to 8.0 — and the price matters the same way (cheaper than −106 the market has already leaned under; steeper than −110 the vig eats it). Two honest caveats: 2026 is running below the historical rate, and the team-reversion overlay is deliberately NOT applied here — we backtested it on 9.0 and the hot-offense signal underperformed the base, so that overlay is specific to 8.0.',
    price: {
      lo: -110, hi: -106, window: '−110 to −106', best: '−106',
      guide: 'Playable at −110 to −106 (−106 is the best number in that window). At −111 or steeper, shop your other books for the number rather than taking the extra juice. At −105 or cheaper the market has already leaned under, so the edge is thinner. Take the shown price or better within the window, never worse.',
    },
  },
  // UNDER_MODEL_DEV (Model–Vegas Gap) removed — demoted after un-anchoring the prediction and
  // testing it historically (−4.7% at its threshold; the one positive cut was a single season).
  // See the "what we tested" table in Support for the disclosure.
};

// Is a live under-price inside an edge's payable window? lo = steepest acceptable (most
// negative), hi = cheapest acceptable (least negative); null = unbounded on that side.
function priceInWindow(def, price) {
  const p = def && def.price;
  if (!p || price == null) return null;
  if (p.lo != null && price < p.lo) return false;   // too steep (e.g. −115 vs −110)
  if (p.hi != null && price > p.hi) return false;   // too cheap (e.g. −105 vs −106)
  return true;
}
const fmtOdds = v => v == null ? '—' : (v > 0 ? `+${v}` : `${v}`);

// The validated UNDER bands, defined once. Both the Performance tab's ROI figures and the
// Edges tab's qualifying-game lists read these, so a game shown as qualifying is by
// construction a game counted in the record — the list cannot drift from the number.
// Every UNDER edge the app publishes, each with the predicate that decides whether a game
// belongs to its record. `qualifies` defines the universe the ROI is built from; `flags`
// mirrors detect_edges() in pipeline/analytics/edge_detector.py — what the app would
// actually have surfaced. The edges do NOT share a trigger: 8.0 additionally requires the
// model below the line, while 9.0 deliberately fires on the line alone (the model filter
// measured worse there). Keep these in step with edge_detector.py — if they drift, the audit
// misstates what the app does. (The Model–Vegas Gap edge was demoted; see the note below.)
const EDGE_BANDS = [
  {
    key: 'u80', label: 'UNDER · Total = 8.0', tag: 'UNDER_LINE_8_0',
    qualifies: r => r.line >= 8.0 && r.line < 8.5 && inStdVig(r.under_price),
    note: 'The record is the standard-vig window (−110 to −106) — the only +EV price zone. Off-price 8.0 games are surfaced live to shop, but only std-vig bets count here.',
  },
  {
    key: 'u90', label: 'UNDER · Total = 9.0', tag: 'UNDER_LINE_9_0',
    qualifies: r => r.line >= 9.0 && r.line < 9.5 && inStdVig(r.under_price),
    note: 'Same basis as 8.0 — the standard-vig window (−110 to −106). Off-price 9.0 games are surfaced live to shop, but only std-vig bets count here.',
  },
  {
    // The 8.5 dead zone — no `tag`, so it is NOT shown as an edge or in the Edges audit; kept only
    // as an all-prices contrast in the Performance tab (proves the nearby line is not an edge).
    key: 'u85', label: 'UNDER · Total = 8.5', tag: null,
    qualifies: r => r.line >= 8.5 && r.line < 9.0,
    note: 'The 8.5 line — shown for contrast only; explicitly not an edge.',
  },
  // Model–Vegas Gap band removed — demoted after testing (the one positive cut was a single season).
];

// Std-vig band for the 8.0 edge: [-110, -106].
function inStdVig(underPrice) {
  return underPrice != null && underPrice <= -106 && underPrice >= -110;
}


// The full graded universe for edge auditing, in one shape.
//
// Two sources, because no single file covers every season:
//   2021-2025  backtest.json — graded on the archived CLOSING line.
//   2026       history.json  — graded on the BET-TIME line (vegas_total/under_price),
//                              matching edge_scoreboard.py's _grade_under exactly. 2026 has
//                              no closing_lines.parquet, so backtest.json's 2026 rows carry
//                              no odds at all and are skipped here to avoid double-counting.
// The line-source difference is real and is surfaced per row rather than blended away.
function edgeAuditUniverse() {
  const out = [];
  for (const g of (backtestData?.games || [])) {
    const yr = g.season || parseInt(g.date);
    if (yr === 2026) continue;            // no odds on these rows; 2026 comes from history
    if (g.closing_total == null || g.under_price == null) continue;
    out.push({
      date: g.date, season: yr, gamePk: g.gamePk,
      away_team: g.away_team, home_team: g.home_team,
      line: g.closing_total, under_price: g.under_price,
      away_score: g.away_score, home_score: g.home_score,
      actual_total: g.actual_total, lineSource: 'closing',
      predicted_total: g.predicted_total,
    });
  }
  for (const r of (historyData || [])) {
    const yr = parseInt((r.date || '').slice(0, 4));
    if (!yr || yr < 2026) continue;
    // 2026 is graded on the CLOSING line + closing odds, identical to the 2021-25 backtest
    // (closing_lines.parquet). The frozen bet-time line (vegas_total) drifts from the close, so
    // it is never used to grade. Games without a closing snapshot yet are excluded until backfilled.
    if (r.closing_total == null || r.closing_under_price == null || r.actual_total == null) continue;
    out.push({
      date: r.date, season: yr, gamePk: r.gamePk,
      away_team: r.away_team, home_team: r.home_team,
      line: r.closing_total, under_price: r.closing_under_price,
      away_score: r.away_score, home_score: r.home_score,
      actual_total: r.actual_total, lineSource: 'closing',
      betTimeLine: r.vegas_total ?? null,       // informational: the line the app flagged at
      predicted_total: r.predicted_total,
    });
  }
  return out;
}

// UNDER edge ROI for one band (same methodology as edge_research.py / edge_scoreboard.py).
// Returns the aggregate AND every qualifying game with a running cumulative-units figure,
// so the number can be shown alongside the rows it is built from.
function computeUnderEdge(band, allGames) {
  const bySeason = {};
  const rows = [];
  let totalUnits = 0, totalBets = 0, totalWins = 0;

  // Chronological, so the running total accumulates in the order the bets were actually made.
  const ordered = allGames.slice().sort((a, b) => String(a.date).localeCompare(String(b.date)));

  for (const g of ordered) {
    // Accept both shapes: universe rows use `line`, raw backtest rows use `closing_total`.
    const ct = g.line ?? g.closing_total;
    const uPrice = g.under_price;
    if (uPrice == null || ct == null) continue;
    if (!band.qualifies({ ...g, line: ct })) continue;
    // Push-aware: a total landing exactly on the line is void (refunded), not an UNDER win.
    const oc = totalsOutcome(g.actual_total, ct);
    if (oc == null || oc === 'push') continue;
    const yr = g.season || parseInt(g.date);
    // 2021 IS in the record. The edges are blind line+vig bets (no model input in `qualifies`)
    // and 2021's closing lines are complete (rebuilt from SBR), so it's legitimate data — and
    // including a down year is the more honest longer-window figure. The ONLY lookahead-flagged
    // part is 2021's lineups (neutral-0.5, no 2020 prior cache), which makes its predicted_total
    // carry no real model view; the per-row leansUnder below is nulled for 2021 so it never
    // counts as a model signal in the "+model lean" build.
    if (!yr || yr < 2021) continue;
    if (!bySeason[yr]) bySeason[yr] = { bets: 0, wins: 0, units: 0 };
    const s = bySeason[yr];
    const won = oc === 'under'; // betting UNDER wins when total stays under
    const dec = uPrice >= 0 ? 1 + uPrice / 100 : 1 - 100 / uPrice;
    const delta = won ? dec - 1 : -1;
    s.bets++; totalBets++;
    if (won) { s.wins++; totalWins++; }
    s.units += delta; totalUnits += delta;
    rows.push({
      date: g.date, season: yr, gamePk: g.gamePk,
      away: g.away_team, home: g.home_team,
      line: ct, underPrice: uPrice,
      awayScore: g.away_score, homeScore: g.home_score,
      actual: g.actual_total, won, units: delta,
      cum: totalUnits,                       // running units through this bet
      lineSource: g.lineSource || 'closing',
      predicted: g.predicted_total ?? null,
      // Separates "the line qualified" from "the app would have flagged it". 2021 lineups are
      // neutral-0.5 (no real model view), so its lean is unknown — excluded from the model-lean
      // build, shown as "?", never a fake signal.
      leansUnder: (yr >= 2022 && g.predicted_total != null) ? g.predicted_total < ct : null,
    });
  }
  const roi = totalBets ? (totalUnits / totalBets * 100) : null;
  return { bySeason, totalBets, totalWins, totalUnits, roi, rows };
}

// Look a band up by key. Used so the Performance tab and the Edges audit read the same
// definitions rather than each carrying its own copy of the line boundaries.
function edgeBand(key) {
  return EDGE_BANDS.find(b => b.key === key);
}

// SINGLE SOURCE OF TRUTH for an edge's validated historical figure. Recomputed live from the
// reconciled backtest+history data via computeUnderEdge — NEVER a frozen constant. The old
// hardcoded EDGE_METADATA roi_pct/n_games/season_roi drifted from the data after the odds
// reconciliation and showed conflicting numbers; this makes the figure the app displays and
// the figure the audit computes literally the same call, so they cannot disagree.
//
// This is the edge's FULL multi-season record (every season with archived lines through the
// live one), identical to the audit's "line only" headline — the same computeUnderEdge call —
// so the Performance tab, the play cards, and the audit all show one number that cannot
// disagree. The scoreboard's separate 2026 figure is the "this season" callout. Returns null
// until backtest data is loaded.
let _edgeHistCache = null, _edgeHistKey = null;
function edgeHistorical(tag) {
  if (!backtestData || !tag) return null;
  const key = (backtestData.generated_at || '') + '|' + (historyData ? historyData.length : 0);
  if (_edgeHistKey !== key) { _edgeHistCache = {}; _edgeHistKey = key; }
  if (tag in _edgeHistCache) return _edgeHistCache[tag];

  const band = EDGE_BANDS.find(b => b.tag === tag);
  if (!band) return (_edgeHistCache[tag] = null);
  const d = computeUnderEdge(band, edgeAuditUniverse());
  const yrs = Object.keys(d.bySeason).sort();
  const pos = yrs.filter(y => d.bySeason[y].units > 0).length;
  const multiSeason = yrs.length > 1;
  const res = {
    roi: d.roi, n: d.totalBets, wins: d.totalWins,
    seasonsProfitable: pos, seasonsTotal: yrs.length,
    bySeason: d.bySeason, full: d,
    seasonsStr: multiSeason
      ? `${pos}/${yrs.length} seasons profitable (${yrs[0]}–${yrs[yrs.length - 1]})`
      : `${yrs[0] || 2026} only, small sample`,
  };
  return (_edgeHistCache[tag] = res);
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
    const h = edgeHistorical(e.tag);   // live-recomputed validated ROI, not a frozen constant
    return `<tr>
      <td class="ep-name">${e.label || e.tag} ${confTag(e.confidence)}</td>
      <td class="${pctCls(h ? h.roi : null)}">${pctStr(h ? h.roi : null)}</td>
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

// Per-season prediction ACCURACY rows (win% of the model's predicted winner).
// Accuracy only — deliberately no per-season moneyline ROI. Betting the model's side on
// every game is not a validated edge (ML is ~0 EV), and showing it invited an
// apples-to-oranges read where 2026 (in-sample for the recalibrated model) dwarfed the
// out-of-sample prior seasons.
// 2026 is sourced ONLY from history (backtest.json also carries 2026 rows → double-count).
function seasonAccuracyRows() {
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
        <td>${d.n.toLocaleString()}</td>
        <td>${d.correct}–${d.n - d.correct}</td>
        <td class="${acc >= 55 ? 'edge-pos' : acc >= 50 ? '' : 'edge-neg'}">${acc}%</td>
      </tr>`;
    }).join('');
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

// ── Performance view ──────────────────────────────────────────────────────────

// The single Performance page: validated edges and their realized return up top, then the
// supporting model diagnostics. Deliberately absent — the old "Moneyline Analysis" section,
// which carried its own banner saying there is no validated moneyline edge and that the
// away-ML angle was overfit and demoted. Pages of ROI tiers for a demoted signal read as a
// recommendation no matter how they are captioned.
function renderPerformanceView() {
  const el = document.getElementById('performance-view');

  if (!backtestData) {
    el.innerHTML = `<div class="empty-state"><p>Backtest data not available yet. Run the pipeline to generate it.</p></div>`;
    return;
  }

  const { stats, games = [] } = backtestData;
  if (!stats) {
    el.innerHTML = `<div class="empty-state"><p>No backtest stats found in data.</p></div>`;
    return;
  }

  const pct = v => v != null ? (v * 100).toFixed(1) + '%' : '—';

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

  // ── Hero KPIs ─────────────────────────────────────────────────────────────
  const totalGames = games.length;
  const roiValCls = v => v == null ? '' : v > 0 ? 'kpi-green' : v < 0 ? 'kpi-red' : '';
  // Honest hero: the only realized betting ROI is the validated edge (from the live scoreboard);
  // the model's blind ML/totals ROI is ~0 EV and is no longer surfaced as a headline.
  const sbEdges = (scoreboardData && scoreboardData.edges) || [];
  const u8 = sbEdges.find(e => e.tag === 'UNDER_LINE_8_0');
  const u8h = edgeHistorical('UNDER_LINE_8_0');   // live-recomputed, not a frozen constant

  // Three KPIs, not four. The old "2026 Win Rate · in-sample" card put an in-sample number in
  // the most prominent slot on the page; in-sample accuracy belongs in the season table where
  // its caveat sits next to it, not in a headline.
  const heroSection = `
    <div class="bt-context-bar">${totalGames.toLocaleString()} games logged since 2021 &nbsp;·&nbsp; closing lines &nbsp;·&nbsp; the only betting ROI shown anywhere on this page is the validated UNDER edge</div>
    <div class="bt-kpi-row">
      <div class="bt-kpi-card">
        <div class="bt-kpi-val ${roiValCls(u8h ? u8h.roi : null)}">${u8h && u8h.roi != null ? (u8h.roi >= 0 ? '+' : '') + u8h.roi.toFixed(1) + '%' : '—'}</div>
        <div class="bt-kpi-label">UNDER 8.0 ROI <span class="bt-kpi-tag tag-good">validated · push-corrected</span></div>
        <div class="bt-kpi-sub">${u8h ? `${u8h.n.toLocaleString()} bets · std-vig · ${u8h.seasonsStr.replace(/^\d+\/\d+ seasons profitable /, '')}` : 'see edge scoreboard'}${u8 && u8.recent_n ? ` · ${u8.recent_roi_pct >= 0 ? '+' : ''}${u8.recent_roi_pct}% live (n=${u8.recent_n})` : ''}</div>
      </div>
      <div class="bt-kpi-card">
        <div class="bt-kpi-val">${pct(stats.win_pct_overall)}</div>
        <div class="bt-kpi-label">Prediction Accuracy <span class="bt-kpi-tag">accuracy, not profit</span></div>
        <div class="bt-kpi-sub">${(stats.total_correct ?? 0).toLocaleString()} / ${(stats.total_decided ?? 0).toLocaleString()} games</div>
      </div>
      <div class="bt-kpi-card">
        <div class="bt-kpi-val">${totalGames.toLocaleString()}</div>
        <div class="bt-kpi-label">Games Tracked</div>
        <div class="bt-kpi-sub">2021–2026 · all seasons</div>
      </div>
    </div>`;

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

  // Season rows come from seasonAccuracyRows() rather than the backtest games array, because
  // backtest.json also carries 2026 rows — counting those AND history would double-count 2026.
  const calibrationSection = `
    <summary class="bt-sec-head bt-sec-toggle">
      <span class="bt-sec-num">02</span>
      <span class="bt-sec-title">Model Accuracy</span>
      <span class="bt-sec-sub">Win rate by confidence level and by season</span>
    </summary>
    <p class="bt-sec-desc">
      Higher model confidence correlates with better accuracy. This is <strong>accuracy, not profit</strong> —
      picking winners more than half the time does not by itself beat the vig. The only surface where
      accuracy has converted into realized return is the validated UNDER edge above.
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
        <div class="bt-subsection-title">By Season</div>
        <div class="bt-table-wrap">
          <table class="seg-table">
            <thead><tr><th>Season</th><th>Games</th><th>Record</th><th>Acc%</th></tr></thead>
            <tbody>${seasonAccuracyRows()}</tbody>
          </table>
        </div>
        <p class="bt-sec-desc" style="margin-top:8px">2026 is <b>in-sample</b> for the recalibrated model — not directly comparable to the out-of-sample prior seasons.</p>
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

  // 2026 totals-lean accuracy, folded in here rather than living in its own section — it is the
  // live continuation of the same historical table beneath it, not a separate statistic.
  const decLive = (historyData || []).filter(r => (r.actual_winner === 'home' || r.actual_winner === 'away') && !r.sp_scratched);
  const tl = totalsLeanAccuracy(decLive);
  const tlRow = (label, b) => {
    const n = b.w + b.l, p = n ? Math.round(b.w / n * 100) : null;
    return `<tr><td><strong>${label}</strong></td><td>${n.toLocaleString()}</td><td>${b.w}–${b.l}</td><td class="${p == null ? '' : p >= 55 ? 'seg-pos' : p >= 50 ? '' : 'seg-neg'}">${p == null ? '—' : p + '%'}</td></tr>`;
  };
  const leanLiveBlock = (tl.under.w + tl.under.l + tl.over.w + tl.over.l) ? `
    <div class="bt-subsection-title" style="margin-top:14px">2026 live · model lean ≥ 0.5 runs</div>
    <div class="bt-table-wrap">
      <table class="seg-table">
        <thead><tr><th>Model lean</th><th>Graded</th><th>Record</th><th>Win%</th></tr></thead>
        <tbody>${tlRow('UNDER', tl.under)}${tlRow('OVER', tl.over)}</tbody>
      </table>
    </div>
    <div class="bt-signal-note">Push-corrected${tl.push ? ` — ${tl.push} push${tl.push === 1 ? '' : 'es'} excluded` : ''}. Accuracy of the lean only; realized return on the validated edge is in section 01.</div>` : '';

  const totalsSection = `
    <summary class="bt-sec-head bt-sec-toggle">
      <span class="bt-sec-num">03</span>
      <span class="bt-sec-title">Totals Performance</span>
      <span class="bt-sec-sub">OVER/UNDER prediction accuracy · all seasons</span>
    </summary>
    <p class="bt-sec-desc">
      The model consistently predicts fewer runs than the Vegas line — a lean that has been correct ~52–53%
      of the time most seasons. That modest accuracy only becomes a (small, push-corrected) edge at the
      specific 8.0 standard-vig line above; betting the lean on every game is roughly break-even.
    </p>
    ${leanLiveBlock}
    ${totalsYrRows ? `<div class="bt-table-wrap">
      <table class="seg-table">
        <thead><tr><th>Season</th><th>Over Bets</th><th>Over Accuracy</th><th>Under Bets</th><th>Under Accuracy</th></tr></thead>
        <tbody>${totalsYrRows}</tbody>
      </table>
    </div>` : ''}
    ${signalRoiBlock}`;

  // ── SECTION 6: Validated Edges ───────────────────────────────────────────
  // Same universe and same band definitions the Edges tab audits against, so the two tabs
  // cannot report different ROI for the same edge. This includes the live 2026 season via
  // history.json — backtest.json alone has no 2026 odds, which previously left the live
  // season out of these cards entirely.
  const edgeUniverse = edgeAuditUniverse();
  const edge8to85 = computeUnderEdge(edgeBand('u80'), edgeUniverse);
  const edge9to95 = computeUnderEdge(edgeBand('u90'), edgeUniverse);
  const edge85to9 = computeUnderEdge(edgeBand('u85'), edgeUniverse); // the dead zone — contrast

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

  // Card copy states its figures from the same data the card displays. These used to be
  // written into the prose by hand ("+5.5%", "2026 reversed sharply (-13.9%)") and drifted
  // the moment the underlying odds were reconciled, so a card could contradict its own
  // headline number. Anything numeric in this section is derived.
  const seasonsOf = d => Object.keys(d.bySeason).sort();
  const profitableOf = d => seasonsOf(d).filter(y => d.bySeason[y].units > 0).length;
  const liveOf = d => {
    const yrs = seasonsOf(d).filter(y => parseInt(y) >= 2026);
    if (!yrs.length) return null;
    const s = yrs.reduce((a, y) => ({ bets: a.bets + d.bySeason[y].bets, units: a.units + d.bySeason[y].units }), { bets: 0, units: 0 });
    return s.bets ? { bets: s.bets, roi: s.units / s.bets * 100, year: yrs[0] } : null;
  };
  const pctStr = v => (v >= 0 ? '+' : '') + v.toFixed(1) + '%';
  const liveStr = d => {
    const l = liveOf(d);
    return l ? `${l.year} so far: <strong>${pctStr(l.roi)}</strong> over ${l.bets} qualifying game${l.bets === 1 ? '' : 's'}` : 'no qualifying games this season yet';
  };
  const totalGraded = edge8to85.totalBets + edge85to9.totalBets + edge9to95.totalBets;
  const scopeYrs = seasonsOf(edge9to95);
  const scopeStr = scopeYrs.length ? `${scopeYrs[0]}–${scopeYrs[scopeYrs.length - 1]}` : '';

  const edgesSection = `
    <div class="bt-sec-head">
      <span class="bt-sec-num">01</span>
      <span class="bt-sec-title">Validated Edges</span>
      <span class="bt-sec-sub">Per-line UNDER performance · ${totalGraded.toLocaleString()} graded bets · ${scopeStr}</span>
    </div>
    <p class="bt-sec-desc">
      MLB totals are set at discrete half-run increments (8.0, 8.5, 9.0, …). Each line behaves
      differently — push-corrected, a flat UNDER on 8.0 at standard vig is ${ec8RoiStr}
      (${profitableOf(edge8to85)} of ${seasonsOf(edge8to85).length} seasons profitable), while 8.5
      returns ${ec85RoiStr}. Figures void pushes — totals landing exactly on the line. The
      <strong>Edges tab</strong> lists every game behind these numbers, game by game.
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
          <p class="bt-ec2-desc">Our most reliable edge — push-corrected ${ec8RoiStr} at standard vig, profitable in ${profitableOf(edge8to85)} of ${seasonsOf(edge8to85).length} seasons. The market tends to over-price the over at the round 8.0 number. Only the standard-vig band qualifies; cheaper or vig-against prices don't. ${liveStr(edge8to85)}.</p>
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
          <p class="bt-ec2-desc">The most common total line in MLB, and a money-loser for UNDER bettors over the full record (${ec85RoiStr}, ${profitableOf(edge85to9)} of ${seasonsOf(edge85to9).length} seasons profitable). No edge flag is generated for 8.5 — the 8.0 and 9.0 edges do not extend here. ${liveStr(edge85to9)}.</p>
          <div class="bt-table-wrap">
            <table class="seg-table">
              <thead><tr><th>Season</th><th>Games</th><th>Win%</th><th>ROI</th></tr></thead>
              <tbody>${edgeSeasonRows(edge85to9)}</tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="bt-edge-card2">
        <div class="bt-ec2-stat-col">
          <div class="bt-ec2-roi-num ecr-green">${ec9RoiStr}</div>
          <div class="bt-ec2-roi-lbl">avg ROI</div>
          <div class="bt-ec2-n-stat">${edge9to95.totalBets.toLocaleString()} games</div>
          <span class="edge-cond-badge strong">⚡ Strong</span>
        </div>
        <div class="bt-ec2-body">
          <div class="bt-ec2-band-label">UNDER · Total = 9.0</div>
          <p class="bt-ec2-desc">A validated edge on par with 8.0 — push-corrected ${ec9RoiStr} at standard vig, profitable in ${profitableOf(edge9to95)} of ${seasonsOf(edge9to95).length} seasons, graded the same way (closing line, std-vig band only). It swings harder between seasons than 8.0 and is running below its rate in 2026, but the multi-season edge is comparable. ${liveStr(edge9to95)}.</p>
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

  // ── Section 04: Model win-prob vs the market-anchored headline ────────────
  // Kept because it is a genuine model diagnostic — how close the pure-input model gets to
  // the market-anchored number — not a bettable surface.
  const winProbSection = (() => {
    const s = modelWinProbStats(decLive);
    const body = !s.n
      ? (() => {
          const tracked = (historyData || []).filter(r => r.model_home_win_pct != null).length;
          return `<p class="bt-sec-desc">The independent model win-prob (lineups + Statcast, no market line) is recorded with every prediction${tracked ? ` — <strong>${tracked.toLocaleString()}</strong> logged so far` : ''}. Winner accuracy and calibration appear here as those games resolve.</p>`;
        })()
      : `<div class="bt-table-wrap">
      <table class="seg-table">
        <thead><tr><th></th><th>Winner accuracy</th><th>Brier (lower = better)</th></tr></thead>
        <tbody>
          <tr><td><strong>Our model (input-only)</strong></td><td class="${s.mAcc >= s.hAcc ? 'seg-pos' : ''}">${s.mAcc}%</td><td>${s.mBrier}</td></tr>
          <tr><td>Headline (market-anchored)</td><td>${s.hAcc}%</td><td>${s.hBrier}</td></tr>
        </tbody>
      </table>
    </div>
    <div class="bt-signal-note">Independent model vs the market-anchored headline over the same ${s.n.toLocaleString()} resolved game${s.n === 1 ? '' : 's'}. The market-anchored number is expected to be sharper — this tracks how close the pure-input model gets.</div>`;
    return `
    <summary class="bt-sec-head bt-sec-toggle">
      <span class="bt-sec-num">04</span>
      <span class="bt-sec-title">Model vs Market</span>
      <span class="bt-sec-sub">Input-only win-prob against the market-anchored headline</span>
    </summary>
    ${body}`;
  })();

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

  // Lead with the headline KPIs + the validated edge and its realized return; the supporting
  // diagnostics and the full game log are collapsed by default so the tab isn't a wall of tables.
  el.innerHTML = `
    <div class="backtest-wrap">
      ${heroSection}
      ${edgesSection}
      ${edgePerfSummaryHTML()}
      <details class="bt-sec-collapse">${calibrationSection}</details>
      <details class="bt-sec-collapse">${totalsSection}</details>
      <details class="bt-sec-collapse">${winProbSection}</details>
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
    return `
      <div class="ef-sb-row">
        <span class="ef-sb-label">${c.icon} ${escapeHtml(e.label)}</span>
        <span class="ef-sb-roi ${cls(e.roi_pct)}">${sgn(e.roi_pct)}${e.roi_pct}%</span>
        <span class="ef-sb-n">${e.win_pct}% · ${e.n}</span>
        ${last10(e)}
        ${recent}
      </div>`;
  }).join('');
  const headline = (t.roi_pct != null)
    ? `<span class="ef-sb-headline ${cls(t.roi_pct)}">${sgn(t.roi_pct)}${t.roi_pct}% · ${t.n} bets</span>` : '';
  const recentHead = (t.recent_n >= 5 && t.recent_roi_pct != null)
    ? ` · 30d ${sgn(t.recent_roi_pct)}${t.recent_roi_pct}%` : '';
  return `
    <div class="ef-scoreboard">
      <div class="ef-sb-hdr">
        <span class="ef-sb-title">This season's results · ${sb.season}</span>
        <span class="ef-sb-head-metrics">${headline}</span>
      </div>
      <div class="ef-sb-sub">Realized ROI, flat $1/bet, on the closing-line std-vig plays${recentHead}. Each edge's full multi-season track record is on its play card and in the audit below.</div>
      ${equityChartHTML(sb)}
      <div class="ef-sb-rowhdr">By edge</div>
      ${rows}
      ${reversionJuiceRowHTML(sb)}
    </div>`;
}

// Emerging "reversion juice" 8.0 subset — forward-tracked out-of-sample from pick time.
// Hidden until juice-tagged plays actually resolve (n>0), so an empty tracker never reads
// as a broken 0-0 record. This is the live confirmation of the backtest-only overlay.
function reversionJuiceRowHTML(sb) {
  const j = sb.reversion_juice;
  if (!j || !j.n) return '';
  const sgn = v => (v >= 0 ? '+' : '');
  const cls = v => (v == null ? '' : (v >= 0 ? 'sb-pos' : 'sb-neg'));
  return `
    <div class="ef-sb-row ef-sb-juice">
      <span class="ef-sb-label">🔥 Reversion juice <span class="ef-sb-tag">emerging</span></span>
      <span class="ef-sb-roi ${cls(j.roi_pct)}">${sgn(j.roi_pct)}${j.roi_pct}%</span>
      <span class="ef-sb-n">${j.win_pct}% · ${j.n}</span>
      <span class="ef-sb-juice-note">both offenses hot · forward-tracking, not proven</span>
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

// Track-record line for a play, computed live via edgeHistorical (validated = out-of-sample
// seasons). Falls back to the live scoreboard roi_pct only if backtest hasn't loaded.
function _recLine(e) {
  const h = edgeHistorical(e.tag);
  if (h && h.roi != null) {
    const roi = `${h.roi >= 0 ? '+' : ''}${h.roi.toFixed(1)}% ROI`;
    return `${roi} · ${h.seasonsStr}`;
  }
  if (e.roi_pct == null) return 'validated under edge';
  const roi = `${e.roi_pct >= 0 ? '+' : ''}${e.roi_pct.toFixed(1)}% ROI`;
  return `${roi} · validated`;
}

function _whyLine(g, e) {
  const total = g.odds?.total, pred = g.prediction?.predicted_total;
  if (total == null || pred == null) return 'A validated under edge on this total line.';
  return `Our model projects ${pred.toFixed(1)} runs — under the ${total} line.`;
}

// One card per 8.0 game, in one of three price states so we surface the opportunity rather
// than hide it when a book's live price is off. playable = bet now; shop = the edge is real,
// go find −110 to −106 at another book; weaker = cheaper than −106, market has moved.
function edgePlayCardHTML(g) {
  const unders = (g.edge_conditions || [])
    .filter(e => e.direction === 'UNDER')
    .sort((a, b) => (b.signal_boost ?? 0) - (a.signal_boost ?? 0));
  const top = unders.find(e => (e.signal_boost ?? 0) > 0) || unders[0];
  const total = g.odds?.total;
  const cur = g.odds?.under_price;
  const time = g.game_time_et ? ` · ${escapeHtml(g.game_time_et)}` : '';
  const def = EDGE_DEFINITIONS[top?.tag];
  const edgeName = def?.name || top?.label || 'UNDER edge';
  const status = top?.price_status || 'playable';   // playable | shop | weaker
  const topHist = edgeHistorical(top?.tag);

  const badge = status === 'shop'   ? { t: '🔍 Shop for the price', cls: 'ef-conf-shop' }
              : status === 'weaker' ? { t: '◦ Off-price', cls: 'ef-conf-weaker' }
              :                       { t: '⚡ Playable now', cls: 'ef-conf-high' };
  const betLine = status === 'shop'   ? `UNDER ${total ?? ''} — find −110 to −106`
                : status === 'weaker' ? `UNDER ${total ?? ''} — better priced at −110 to −106`
                :                       `Bet the UNDER ${total ?? ''}`;
  const statusRow = status === 'shop'
      ? `<div class="ef-play-status ef-status-shop">This book has ${fmtOdds(cur)}, steeper than the payable window. The edge is real at −110 to −106 — shop your other books for that number. Don't take it at this price; it only counts toward the record at −110 to −106.</div>`
      : status === 'weaker'
      ? `<div class="ef-play-status ef-status-weaker">This book has ${fmtOdds(cur)}, cheaper than −106 — the market has already leaned under, so the edge is thinner here. It's strongest at −110 to −106.</div>`
      : `<div class="ef-play-status ef-status-tracked">✓ Playable at the current price — this is the validated bet, counted in the season results below.</div>`;

  const inRange = def ? priceInWindow(def, cur) : null;
  const priceBlock = (def && def.price) ? `
      <div class="ef-play-price">
        <div class="ef-play-price-top">
          <span class="ef-play-price-k">${status === 'playable' ? 'Bet at' : 'Target'}</span>
          <span class="ef-play-price-window">${def.price.window}</span>
          ${cur != null ? `<span class="ef-play-price-now ${inRange === false ? 'price-out' : 'price-in'}">this book ${fmtOdds(cur)} ${inRange === false ? '✗' : '✓'}</span>` : ''}
        </div>
        <div class="ef-play-price-guide">${escapeHtml(def.price.guide)}</div>
      </div>` : '';

  const others = unders.filter(e => e !== top);
  const details = `
      <details class="ef-play-details">
        <summary>details</summary>
        <div class="ef-play-detail-body">
          ${topHist ? `<div>Validated on ${topHist.n.toLocaleString()} games — ${escapeHtml(topHist.seasonsStr)}</div>` : ''}
          ${seasonRoiStripHTML(top)}
          ${others.length ? `<div>Also flagged: ${others.map(e => escapeHtml(e.label)).join(', ')}</div>` : ''}
        </div>
      </details>`;

  // Emerging team-reversion overlay: both offenses running hot vs their own norm → historically
  // a stronger UNDER at 8.0 (top-20% +12.1% vs +4.5% base). Underpowered, so framed as emerging
  // and forward-tracked, not a promise. Only present on 8.0 games (pipeline attaches it there).
  const rev = top?.reversion;
  let revBlock = '';
  if (rev && rev.tier) {
    const o3 = x => x.toFixed(3).replace(/^0/, '');                       // .750 (baseball style)
    const sOps = x => (x >= 0 ? '+' : '-') + Math.abs(x).toFixed(3).replace(/^0/, '');
    const sRuns = x => (x >= 0 ? '+' : '-') + Math.abs(x).toFixed(1);
    const isJuice = rev.tier === 'juice';
    // Per-team drivers: show which offense is hot, on which metric, by how much. Deltas are
    // computed from the displayed rounded components so they always visually add up.
    const teamRows = (rev.teams || []).map(t => {
      const dOps = t.ops_l5 - t.ops_l20, dRuns = t.runs_l10 - t.runs_std;
      const kOps = dOps > 0 ? 'rev-up' : (dOps < 0 ? 'rev-dn' : '');
      const kRuns = dRuns > 0 ? 'rev-up' : (dRuns < 0 ? 'rev-dn' : '');
      return `
        <div class="ef-rev-team">
          <span class="ef-rev-tname">${escapeHtml(t.name)}</span>
          <span class="ef-rev-metric">OPS ${o3(t.ops_l5)}<span class="ef-rev-sub">L5</span> vs ${o3(t.ops_l20)}<span class="ef-rev-sub">L20</span> <b class="${kOps}">${sOps(dOps)}</b></span>
          <span class="ef-rev-metric">${t.runs_l10.toFixed(1)}<span class="ef-rev-sub">R/g L10</span> vs ${t.runs_std.toFixed(1)}<span class="ef-rev-sub">season</span> <b class="${kRuns}">${sRuns(dRuns)}</b></span>
        </div>`;
    }).join('');
    revBlock = `
      <div class="ef-play-rev ef-rev-${rev.tier}">
        <div class="ef-rev-hdr">
          <span class="ef-rev-badge">${isJuice ? '🔥 Reversion juice' : '↗ Offenses leaning hot'}</span>
          <span class="ef-rev-score" title="Combined z-score of both offenses' recent scoring vs their own baseline, standardized on the 2021-25 sample. ${isJuice ? '≥1.37 = top ~20% of games.' : '≥0.69 = top ~33%.'}">rev ${rev.rev_score >= 0 ? '+' : ''}${rev.rev_score} · ${isJuice ? 'top-20%' : 'top-33%'}</span>
        </div>
        <div class="ef-rev-why">Both offenses are running above their <b>own</b> recent baseline — the model expects them to cool toward norm, favoring the UNDER. Drivers:</div>
        <div class="ef-rev-teams">${teamRows}</div>
        <div class="ef-rev-caveat">${isJuice
          ? 'Backtest: the top-20% reversion 8.0s cashed UNDER at ~+12% ROI (2021-25) — but small (n≈82) and not yet confirmed live. An overlay on the 8.0 edge, forward-tracked. Not a guarantee.'
          : 'Softer lean (top-33%, ~+8% backtest). Same caveats — emerging, underpowered, forward-tracked.'}</div>
      </div>`;
  }

  return `
    <div class="ef-play ef-play-${status}${rev && rev.tier === 'juice' ? ' ef-play-juiced' : ''}">
      <div class="ef-play-top">
        <span class="ef-conf-badge ${badge.cls}">${badge.t}</span>
        <span class="ef-edge-name" title="${def ? escapeHtml(def.short) : ''}">${escapeHtml(edgeName)}</span>
        <span class="ef-play-match">${escapeHtml(g.away_team)} @ ${escapeHtml(g.home_team)}${time}</span>
      </div>
      <div class="ef-play-bet">${betLine}</div>
      <div class="ef-play-why">${def ? escapeHtml(def.short) : escapeHtml(_whyLine(g, top))}</div>
      ${priceBlock}
      ${revBlock}
      ${statusRow}
      <div class="ef-play-row"><span class="ef-play-k">Track record</span><span class="ef-play-rec">${escapeHtml(_recLine(top))}</span></div>
      ${details}
    </div>`;
}

function edgesHowToHTML() {
  return `
    <details class="ef-howto">
      <summary>How to read this</summary>
      <div class="ef-howto-body">
        <p>Each play is an UNDER bet that several seasons of closing-line data agree the market has mispriced. We only surface bets that beat real closing odds across multiple seasons.</p>
        <p><b>The two edges:</b> both the <b>8.0</b> and <b>9.0</b> lines are validated UNDER spots — a blind UNDER at standard vig (−110 to −106) is positive across 2021–25 on each. Everything is graded on the closing line, so bet the price shown or better within that window.</p>
        <p><b>Track record</b> on each play is its validated multi-season ROI. <b>This season's results</b> below show how the edges have actually done live in 2026 — which can run above or below the long-run rate (9.0 is running soft this year, 8.0 hot).</p>
        <p><b>The reversion overlay (🔥) is 8.0-only, on purpose.</b> When both offenses are running hot vs their own norm, the 8.0 UNDER has historically hit at a higher rate — but we backtested the same signal on 9.0 and it <i>underperformed</i> the base, so it's applied to 8.0 alone.</p>
        <div class="ef-glossary">
          <div class="ef-glossary-hdr">The edge categories</div>
          ${Object.values(EDGE_DEFINITIONS).map(d => `
            <div class="ef-gloss-item">
              <div class="ef-gloss-name">${escapeHtml(d.name)}</div>
              <div class="ef-gloss-def">${escapeHtml(d.long)}</div>
              ${d.price ? `<div class="ef-gloss-price"><b>Price to bet:</b> ${escapeHtml(d.price.window)}. ${escapeHtml(d.price.guide)}</div>` : ''}
            </div>`).join('')}
        </div>
        <p class="ef-howto-pricenote"><b>Why a play can vanish:</b> the app reads the live line and only flags a bet while its price is in the payable window. If a play showed at −110 and the line drifts to −115, it drops off — the edge is gone at that number. Bet the price shown (or better within the window); never chase a worse one.</p>
      </div>
    </details>`;
}

// ── Qualifying-games audit ────────────────────────────────────────────────────
// Every game behind each edge's record, so the headline ROI can be sourced and checked
// rather than taken on faith. Rows come from computeUnderEdge — the same call that
// produces the number — so the list and the number cannot disagree.

let _edgeAuditOpen = {};   // band key → showing all rows rather than the recent slice
const EDGE_AUDIT_PAGE = 50;

// Today's games that land on an edge, regardless of whether the pipeline flagged them
// actionable. Uses the band's own `qualifies` predicate — the same one the historical
// record is built from — so "qualifies today" means exactly "would be counted".
function todaysBandQualifiers(band) {
  return (gamesData?.games || []).filter(g => {
    const o = g.odds || {};
    if (o.total == null || o.under_price == null) return false;
    return band.qualifies({
      line: o.total,
      under_price: o.under_price,
      predicted_total: g.prediction?.predicted_total ?? null,
    });
  });
}

function edgeAuditCsv(band, rows) {
  const head = 'date,season,gamePk,away,home,closing_line,closing_under_price,away_score,home_score,actual_total,result,units,running_units';
  const body = rows.map(r => [
    r.date, r.season, r.gamePk, `"${r.away}"`, `"${r.home}"`,
    r.line, r.underPrice, r.awayScore, r.homeScore, r.actual,
    r.won ? 'WIN' : 'LOSS', r.units.toFixed(4), r.cum.toFixed(4),
  ].join(',')).join('\n');
  return head + '\n' + body;
}

function copyEdgeCsv(bandKey) {
  const payload = window.__edgeCsv?.[bandKey];
  if (!payload) return;
  const done = () => {
    const btn = document.querySelector(`[data-csv="${bandKey}"]`);
    if (!btn) return;
    const old = btn.textContent;
    btn.textContent = `Copied ${payload.n} rows`;
    setTimeout(() => { btn.textContent = old; }, 2000);
  };
  navigator.clipboard?.writeText(payload.csv).then(done, () => {});
}

function toggleEdgeAudit(bandKey) {
  _edgeAuditOpen[bandKey] = !_edgeAuditOpen[bandKey];
  renderEdgesView();
}

function edgeAuditSectionHTML() {
  const btGames = backtestData?.games;
  if (!btGames) {
    return `<section class="ef-section">
      <div class="ef-section-hdr"><h2 class="ef-section-title">Qualifying games</h2></div>
      <div class="ef-empty">Loading the game-level record…</div>
    </section>`;
  }

  window.__edgeCsv = {};

  const universe = edgeAuditUniverse();

  const blocks = EDGE_BANDS.filter(b => b.tag).map(band => {   // only real edges (8.0, 9.0)
    const data = computeUnderEdge(band, universe);
    // Most recent first — verification starts from the latest games, and it puts the final
    // running total on the top row, where it should equal the headline units exactly.
    const rows = data.rows.slice().sort((a, b) => String(b.date).localeCompare(String(a.date)));
    const showAll = !!_edgeAuditOpen[band.key];
    const shown = showAll ? rows : rows.slice(0, EDGE_AUDIT_PAGE);
    window.__edgeCsv[band.key] = { csv: edgeAuditCsv(band, rows), n: rows.length };

    // The spot-check: the five most recent qualifying games, each showing the line it
    // qualified on and the final total, so "yep, that worked" takes one glance. Prefers the
    // live season — if 2026 has any, show only those rather than diluting with old games.
    const live26 = rows.filter(r => r.season >= 2026);
    const lastFiveLive = live26.length > 0;
    const lastFive = (lastFiveLive ? live26 : rows).slice(0, 5);
    const l5Units = lastFive.reduce((a, r) => a + r.units, 0);
    const l5Wins = lastFive.filter(r => r.won).length;
    const lastFiveHTML = lastFive.length ? `
      <div class="ea-l5">
        ${lastFive.map(r => `
          <div class="ea-l5-row ${r.won ? 'ea-l5-win' : 'ea-l5-loss'}">
            <span class="ea-l5-mark">${r.won ? '✓' : '✗'}</span>
            <span class="ea-l5-date">${r.date}</span>
            <span class="ea-l5-match">${abbrev(r.away)} @ ${abbrev(r.home)}</span>
            <span class="ea-l5-line">UNDER ${r.line} <span class="ea-l5-px">${r.underPrice > 0 ? '+' : ''}${r.underPrice}</span></span>
            <span class="ea-l5-res">${r.awayScore}–${r.homeScore} = <b>${r.actual}</b></span>
            <span class="ea-l5-verdict">${r.won ? `stayed under by ${(r.line - r.actual).toFixed(1)}` : `went over by ${(r.actual - r.line).toFixed(1)}`}</span>
            <span class="ea-l5-units ${r.units >= 0 ? 'seg-pos' : 'seg-neg'}">${(r.units >= 0 ? '+' : '') + r.units.toFixed(2)}u</span>
          </div>`).join('')}
        <div class="ea-l5-foot">
          ${l5Wins}–${lastFive.length - l5Wins} over the last ${lastFive.length}
          · <span class="${l5Units >= 0 ? 'seg-pos' : 'seg-neg'}">${(l5Units >= 0 ? '+' : '') + l5Units.toFixed(2)}u</span>
          ${lastFiveLive ? '' : ' · no 2026 qualifiers yet, so these are the most recent from prior seasons'}
        </div>
      </div>` : `<div class="ea-audit-none">No graded qualifying games yet.</div>`;

    const today = todaysBandQualifiers(band);
    const todayHTML = today.length
      ? `<table class="ea-audit-table">
          <thead><tr><th>Matchup</th><th>Line</th><th>Under</th><th>Status</th></tr></thead>
          <tbody>${today.map(g => {
            const o = g.odds || {};
            const live = (g.game_status || 'preview') !== 'preview';
            return `<tr class="ea-audit-today">
              <td>${abbrev(g.away_team)} @ ${abbrev(g.home_team)}</td>
              <td><strong>${o.total}</strong></td>
              <td>${o.under_price > 0 ? '+' : ''}${o.under_price ?? '—'}</td>
              <td>${live ? `<span class="ea-audit-live">${escapeHtml(g.game_status)}</span>` : (g.game_time_et || 'scheduled')}</td>
            </tr>`;
          }).join('')}</tbody>
        </table>`
      : `<div class="ea-audit-none">No game today lands on this line.</div>`;

    let runningCum = 0;
    const seasonRows = Object.keys(data.bySeason).sort().map(yr => {
      const s = data.bySeason[yr];
      const roi = s.bets ? s.units / s.bets * 100 : null;
      const wr = s.bets ? s.wins / s.bets * 100 : null;
      runningCum += s.units;
      const isLive = parseInt(yr) >= 2026;
      const cls = v => v == null ? '' : v >= 0 ? 'seg-pos' : 'seg-neg';
      return `<tr${isLive ? ' class="ea-audit-liverow"' : ''}>
        <td><strong>${yr}</strong>${isLive ? ' <span class="ea-audit-livetag">live</span>' : ''}</td>
        <td>${s.bets.toLocaleString()}</td>
        <td>${wr == null ? '—' : wr.toFixed(1) + '%'}</td>
        <td class="${cls(roi)}">${roi == null ? '—' : (roi >= 0 ? '+' : '') + roi.toFixed(1) + '%'}</td>
        <td class="${cls(runningCum)}">${(runningCum >= 0 ? '+' : '') + runningCum.toFixed(2)}u</td>
      </tr>`;
    }).join('');

    const gameRows = shown.map(r => `<tr class="${r.won ? 'row-hit' : 'row-miss'}">
      <td class="ea-audit-date">${r.date}</td>
      <td>${abbrev(r.away)} @ ${abbrev(r.home)}</td>
      <td>${r.line}</td>
      <td>${r.underPrice > 0 ? '+' : ''}${r.underPrice}</td>
      <td>${r.awayScore}–${r.homeScore}</td>
      <td><strong>${r.actual}</strong></td>
      <td class="${r.won ? 'seg-pos' : 'seg-neg'}">${r.won ? 'WIN' : 'LOSS'}</td>
      <td class="${r.units >= 0 ? 'seg-pos' : 'seg-neg'}">${(r.units >= 0 ? '+' : '') + r.units.toFixed(2)}</td>
      <td class="ea-audit-cum ${r.cum >= 0 ? 'seg-pos' : 'seg-neg'}">${(r.cum >= 0 ? '+' : '') + r.cum.toFixed(2)}</td>
    </tr>`).join('');

    const roiStr = data.roi == null ? '—' : (data.roi >= 0 ? '+' : '') + data.roi.toFixed(1) + '%';

    return `
    <div class="ea-audit-band">
      <div class="ea-audit-hdr">
        <span class="ea-audit-title">${band.label}</span>
        ${EDGE_DEFINITIONS[band.tag] ? `<span class="ea-audit-defname">${EDGE_DEFINITIONS[band.tag].name}</span>` : ''}
        <span class="ea-audit-agg">
          <span class="${data.roi >= 0 ? 'seg-pos' : 'seg-neg'}">${roiStr}</span>
          · ${data.totalBets.toLocaleString()} graded
          · ${(data.totalUnits >= 0 ? '+' : '') + data.totalUnits.toFixed(2)}u
        </span>
      </div>
      ${EDGE_DEFINITIONS[band.tag] ? `<div class="ea-audit-def">${EDGE_DEFINITIONS[band.tag].long}</div>` : ''}
      <div class="ea-audit-note">${band.note} All seasons are graded on the closing line + closing odds; pushes are voided, so "graded" is below the raw count of games on this line.</div>

      <div class="ea-audit-subhdr">Today · ${todaysGameDateLabel()}</div>
      ${todayHTML}

      <div class="ea-audit-subhdr">Last 5 qualifying games${lastFiveLive ? ' · 2026' : ''}</div>
      ${lastFiveHTML}

      <div class="ea-audit-subhdr">By season</div>
      <div class="bt-table-wrap">
        <table class="ea-audit-table">
          <thead><tr><th>Season</th><th>n</th><th>Win%</th><th>ROI</th><th>Running</th></tr></thead>
          <tbody>${seasonRows}</tbody>
        </table>
      </div>

      <div class="ea-audit-subhdr">
        Every qualifying game
        <button class="ea-audit-csv" data-csv="${band.key}" onclick="copyEdgeCsv('${band.key}')">Copy all ${rows.length.toLocaleString()} as CSV</button>
      </div>
      <div class="bt-table-wrap">
        <table class="ea-audit-table">
          <thead><tr><th>Date</th><th>Matchup</th><th>Line</th><th>Under</th><th>Score</th><th>Total</th><th>Result</th><th>Units</th><th>Running</th></tr></thead>
          <tbody>${gameRows}</tbody>
        </table>
      </div>
      <div class="ea-audit-note">Newest first, so the top row's <b>Running</b> figure is the band total — it equals the units in the header.</div>
      ${rows.length > EDGE_AUDIT_PAGE ? `
        <button class="ea-audit-more" onclick="toggleEdgeAudit('${band.key}')">
          ${showAll ? `Show only the most recent ${EDGE_AUDIT_PAGE}` : `Show all ${rows.length.toLocaleString()} games`}
        </button>` : ''}
    </div>`;
  }).join('');

  return `<section class="ef-section">
    <div class="ef-section-hdr">
      <h2 class="ef-section-title">Qualifying games</h2>
      <span class="ef-section-count">source data</span>
    </div>
    <p class="ea-audit-intro">
      Every game counted in each edge's record — 2021 through the live 2026 season — with the
      running unit total, so you can see the ROI build rather than take it on faith. Every season
      is graded on the <b>closing line + closing odds</b> (2021–25 from archived closing lines,
      2026 from the captured close), and today's qualifiers use the same standard-vig rule as the
      historical rows.
    </p>
    ${blocks}
  </section>`;
}

function todaysGameDateLabel() {
  const d = gamesData?.date;
  if (!d) return 'today';
  try { return formatDateLabel(d); } catch { return d; }
}

function renderEdgesView() {
  const el = document.getElementById('edges-view');
  const allGames = gamesData?.games || [];
  const preview = allGames.filter(g => g.edge_conditions?.length && (g.game_status || 'preview') === 'preview');
  const psRank = { playable: 0, shop: 1, weaker: 2 };
  const condOf = (g, tag) => (g.edge_conditions || []).find(e => e.tag === tag);
  const psOf = (g, tag) => condOf(g, tag)?.price_status || 'playable';
  // Juiced 8.0 plays surface first within the playable group (reversion overlay is 8.0-only).
  const juice8 = g => (condOf(g, 'UNDER_LINE_8_0')?.reversion?.tier === 'juice') ? 0 : 1;

  // One "Today's plays" section per edge line (8.0, then 9.0), each sorted playable-first.
  const playsSection = (tag, lineLabel, tiebreak) => {
    const games = preview.filter(g => condOf(g, tag)).sort((a, b) =>
      (psRank[psOf(a, tag)] - psRank[psOf(b, tag)]) ||
      (tiebreak ? tiebreak(a, b) : 0) ||
      String(a.game_time_utc || '').localeCompare(String(b.game_time_utc || '')));
    const playable = games.filter(g => psOf(g, tag) === 'playable').length;
    const count = games.length
      ? `${games.length} game${games.length !== 1 ? 's' : ''} · ${playable} playable now`
      : '0 games';
    const body = games.length
      ? games.map(edgePlayCardHTML).join('')
      : `<div class="ef-empty">No game is on the ${lineLabel} line today. The track record below still applies — check back, or set an alert.</div>`;
    return `
      <section class="ef-section">
        <div class="ef-section-hdr">
          <h2 class="ef-section-title">Today's UNDER ${lineLabel}</h2>
          <span class="ef-section-count">${count}</span>
        </div>
        ${body}
      </section>`;
  };

  el.innerHTML = `
    <div class="view-header">
      <h1>Edges</h1>
      <span class="sub-label">Validated UNDER spots — surfaced whenever a game lands on the line, so you can shop for the price.</span>
    </div>
    ${edgesHowToHTML()}
    ${playsSection('UNDER_LINE_8_0', '8.0', (a, b) => juice8(a) - juice8(b))}
    ${playsSection('UNDER_LINE_9_0', '9.0')}
    <section class="ef-section">
      ${edgeScoreboardHTML()}
    </section>
    ${edgeAuditSectionHTML()}
    <div class="data-footer">
      <span id="data-footer-text">${dataFooterText()}</span>
      <button class="refresh-btn" id="refresh-btn" onclick="manualRefresh()" title="Refresh data">↻</button>
    </div>`;
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
          than recommended picks. The angles that clear the bar are the UNDER on specific low
          total lines at standard vig — 8.0 (+6.0%) and 9.0 (+6.8%) across 2021–25, push-corrected
          and graded on the closing line — which form the basis of the recommended edges.
          Everything else either failed validation, was driven by a single lucky season, or proved
          statistically indistinguishable from random noise. (All ROI voids pushes — totals landing
          exactly on the line — which we found had previously been miscounted as wins.)
        </p>
        <table class="val-table">
          <thead><tr><th>Bet / Angle</th><th>What the data showed</th><th>Status</th></tr></thead>
          <tbody>
            <tr><td>UNDER total = 8.0 (standard vig)</td><td>+6.0% across 2021–25, push-corrected, 5 of 6 seasons positive — live figure on the Edges tab</td><td><span class="val-tag val-yes">Active edge</span></td></tr>
            <tr><td>UNDER total = 9.0 (standard vig)</td><td>+6.8% across 2021–25, comparable to 8.0 — graded the same way; 2026 is running below its historical rate</td><td><span class="val-tag val-yes">Active edge</span></td></tr>
            <tr><td>Team-reversion overlay on 9.0</td><td>The 8.0 "both offenses hot" signal was re-tested on 9.0 and the hot bucket underperformed the base — kept 8.0-only</td><td><span class="val-tag val-no">Not applied</span></td></tr>
            <tr><td>Model–Vegas total gap (UNDER)</td><td>Looked strong in 2026, but un-anchoring the model and testing 2022–25 showed −4.7% (worse than a blind under); the one positive threshold was a single season</td><td><span class="val-tag val-no">Not used</span></td></tr>
            <tr><td>Moneyline — "Elite Away"</td><td>Overfit: ~+14% in-sample vs ~−12% out-of-sample</td><td><span class="val-tag val-no">Not used</span></td></tr>
            <tr><td>Moneyline — "High Confidence"</td><td>64% win rate but −2% ROI (favorites don't pay)</td><td><span class="val-tag val-no">Not used</span></td></tr>
            <tr><td>Monte Carlo divergence</td><td>No predictive edge; mildly anti-predictive on the moneyline</td><td><span class="val-tag val-no">Not used</span></td></tr>
            <tr><td>Player props (HR / Hit / K)</td><td>−EV on hit rate; real-odds validation now in progress</td><td><span class="val-tag val-watch">Testing</span></td></tr>
            <tr><td>Pitcher moneyline "consistent edge"</td><td>Zero persistence (r ≈ 0) — small-sample noise</td><td><span class="val-tag val-no">Removed</span></td></tr>
            <tr><td>Team totals</td><td>No skill vs a fair line (+0.4 pp over a blind bet)</td><td><span class="val-tag val-no">Not pursued</span></td></tr>
          </tbody>
        </table>
      </div>


    </div>`;
}
