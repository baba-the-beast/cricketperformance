const $ = (sel) => document.querySelector(sel);
const titles = {
  overview: ["Home", "What is in the database, and where to start."],
  matches: ["Matches", "Every match in the database. Click one for its full scorecard."],
  players: ["Players", "Search everyone who batted or bowled. Click a player to open their profile."],
  upcoming: ["Upcoming Matches", "Fixtures from the CricketData.org feed, when one is configured."],
  analytics: ["Player Profile", "Career record, recent form, role, and how sure we are about it."],
  compare: ["Compare Players", "Two players side by side, metric by metric."],
  replay: ["Replay a Match", "Step through a real match ball by ball, extras and wickets included."],
  simulate: ["Simulate a Match", "Play out a what-if match from each player's history. A scenario, not a prediction."],
  agents: ["Ask the Agents", "Batting, bowling and fielding analysts review a shortlist; a coordinator writes the verdict."],
  report: ["Selection Report", "The coordinator's written recommendation from the last agent analysis."],
  predictor: ["Pick My XI", "Rank a squad for a match using form, the ground, and the opponent."],
  architecture: ["How It Works", "From raw match files to the answers on screen."],
  tournament: ["Simulate a Tournament", "Play a full round-robin and get a points table."],
};

const DEFAULT_QUESTION = "Which player should be considered for the next selection?";

/* Wicket kinds that are not dismissals (the batter can come back), and the
   ones credited to the bowler. Mirrors cricket_agent/dismissals.py. */
const NON_DISMISSAL_KINDS = ["retired hurt", "retired not out"];
const BOWLER_WICKET_KINDS = ["bowled", "caught", "lbw", "stumped", "hit wicket", "caught and bowled"];
const isDismissal = (w) => !NON_DISMISSAL_KINDS.includes(String(w.kind || "").toLowerCase());
const dismissalCount = (wickets) => (wickets || []).filter(isDismissal).length;

let currentDataset = null;
let availablePlayers = [];
let availableMatches = [];
let selectedSimulationFixture = null;

let lastReport = null;
let charts = {};
let replay = { session: null, balls: [], score: 0, wickets: 0, inningsIndex: null, timer: null, pitch: null, requestPending: false };

/* Responses derived only from the ingested dataset do not change while the
   page is open, so they are fetched once per session. Every GET is also
   de-duplicated while in flight (several tabs ask for the same lists). */
const SESSION_CACHEABLE = /^\/api\/(matches(\/|$)|players($|\?|\/[^/]+\/(career|profile|stats))|dataset$|venues$|historical-teams$|compare\?|team-roster\?|venue_profile\?)/;
const responseCache = new Map();
const inFlight = new Map();

async function requestJSON(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function fetchJSON(url, options) {
  const method = (options && options.method || "GET").toUpperCase();
  if (method !== "GET") {
    // Agent runs write player memory snapshots shown on profiles.
    return requestJSON(url, options).finally(() => {
      for (const key of responseCache.keys()) if (key.includes("/profile")) responseCache.delete(key);
    });
  }
  if (responseCache.has(url)) return Promise.resolve(responseCache.get(url));
  if (inFlight.has(url)) return inFlight.get(url);
  const pending = requestJSON(url, options)
    .then((data) => { if (SESSION_CACHEABLE.test(url)) responseCache.set(url, data); return data; })
    .finally(() => inFlight.delete(url));
  inFlight.set(url, pending);
  return pending;
}
function fmt(v, d = 1) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number" && !Number.isInteger(v)) return v.toFixed(d);
  return String(v);
}
function num(v) {
  return typeof v === "number" ? v.toLocaleString() : (v ?? "—");
}
function metric(v) {
  return Number.isInteger(v) ? v.toLocaleString() : fmt(v);
}
function humanize(key) {
  const s = String(key).replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
/* Render a long list a page at a time. Rendering all 13k players or 23k
   matches at once froze the tab and produced a page over a million pixels tall. */
function pagedList(container, items, renderItem, { pageSize = 48, noun = "items", empty = "Nothing found." } = {}) {
  let shown = 0;
  const list = container.querySelector("[data-list]");
  const more = container.querySelector("[data-more]");
  const count = container.querySelector("[data-count-line]");
  const update = () => {
    if (count) count.textContent = items.length
      ? `Showing ${Math.min(shown, items.length).toLocaleString()} of ${items.length.toLocaleString()} ${noun}`
      : "";
    if (more) more.hidden = shown >= items.length;
  };
  const page = () => {
    list.insertAdjacentHTML("beforeend", items.slice(shown, shown + pageSize).map(renderItem).join(""));
    shown += pageSize;
    update();
  };
  list.innerHTML = items.length ? "" : `<p class="empty">${empty}</p>`;
  if (more) more.onclick = page;
  if (items.length) page(); else update();
}
function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}
function extraLabel(extras = {}) {
  if (extras.wides) return "WIDE";
  if (extras.noballs) return "NO BALL";
  if (extras.byes) return "BYE";
  if (extras.legbyes) return "LEG BYE";
  if (extras.penalty) return "PENALTY";
  return "";
}
function destroyCharts(scope) {
  for (const [id, chart] of Object.entries(charts)) {
    if (!chart || !chart.destroy) continue;
    // Other tabs stay rendered, so only charts inside `scope` (or orphaned) go.
    if (!scope || !chart.canvas || !chart.canvas.isConnected || scope.contains(chart.canvas)) {
      chart.destroy();
      delete charts[id];
    }
  }
}
/* Series colours read clearly on both the dark and the light background. */
const SERIES = ["#FF6A13", "#3B82F6", "#D99A00", "#10A37F"];

function chartTheme() {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  const light = document.documentElement.dataset.theme === "light";
  return { text: v("--muted"), grid: light ? "rgba(15,23,42,0.08)" : "rgba(255,255,255,0.06)" };
}

/* Recolour axes, grid and legends of every live chart (after a theme switch). */
function applyChartTheme(chart) {
  const t = chartTheme();
  const legend = chart.options.plugins && chart.options.plugins.legend;
  if (legend && legend.labels) legend.labels.color = t.text;
  Object.values(chart.options.scales || {}).forEach((scale) => {
    if (scale.ticks) scale.ticks.color = t.text;
    if (scale.title) scale.title.color = t.text;
    if (scale.grid && scale.grid.display !== false) scale.grid.color = t.grid;
  });
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("theme", theme); } catch (_) { /* private mode: not remembered */ }
  const next = theme === "light" ? "dark" : "light";
  const btn = $("#theme-toggle");
  btn.setAttribute("aria-label", `Switch to ${next} mode`);
  btn.title = `Switch to ${next} mode`;
  btn.querySelector(".theme-toggle-label").textContent = next === "light" ? "Light" : "Dark";
  Object.values(charts).forEach((chart) => {
    if (!chart || !chart.options) return;
    applyChartTheme(chart);
    chart.update("none");
  });
}

function lineChart(id, labels, datasets) {
  const el = document.getElementById(id);
  if (!el || !window.Chart) return;
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(el, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: chartTheme().text } } },
      scales: {
        x: { ticks: { color: chartTheme().text }, grid: { color: chartTheme().grid } },
        y: { ticks: { color: chartTheme().text }, grid: { color: chartTheme().grid } },
      },
    },
  });
}
function barChart(id, labels, datasets) {
  const el = document.getElementById(id);
  if (!el || !window.Chart) return;
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(el, {
    type: "bar",
    data: { labels, datasets },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: chartTheme().text } } },
      scales: {
        x: { ticks: { color: chartTheme().text, maxRotation: 0, autoSkip: true }, grid: { display: false } },
        y: { beginAtZero: true, ticks: { color: chartTheme().text, precision: 0 }, grid: { color: chartTheme().grid } },
      },
    },
  });
}

document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => showTab(btn.dataset.tab));
});
$("#theme-toggle").addEventListener("click", () => {
  setTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
});
setTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark");

/* Tabs whose content depends only on the dataset (or on the argument they were
   opened with) are rendered once and shown again as-is: no refetch, no
   re-render, and filters, scroll position and "show more" state survive. */
const KEEP_ALIVE = new Set(["matches", "players", "analytics", "compare", "replay", "predictor", "simulate", "tournament", "architecture"]);
const renderedWith = new Map();

function showTab(name, arg) {
  if (name !== "replay" && replay.timer) stopPlay();
  document.querySelectorAll(".nav-btn").forEach((b) => {
    const on = b.dataset.tab === name;
    b.classList.toggle("active", on);
    if (on) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  });
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  const [t, s] = titles[name];
  $("#page-title").textContent = t;
  $("#page-sub").textContent = s;
  const tabArg = name === "simulate" ? selectedSimulationFixture : arg;
  if (KEEP_ALIVE.has(name) && renderedWith.has(name) && (tabArg === undefined || tabArg === renderedWith.get(name))) {
    return Promise.resolve();
  }
  const loaders = {
    overview: loadOverview,
    matches: loadMatches,
    players: loadPlayers,
    upcoming: loadUpcoming,
    analytics: loadAnalytics,
    compare: loadCompare,
    replay: loadReplay,
    simulate: loadSimulate,
    tournament: loadTournament,
    agents: loadAgents,
    report: loadReport,
    predictor: loadPredictor,
    architecture: loadArchitecture,
  };
  const root = document.getElementById(`tab-${name}`);
  return Promise.resolve(loaders[name](tabArg)).finally(() => {
    // A failed load is retried on the next visit instead of being kept.
    if (KEEP_ALIVE.has(name) && !root.querySelector(":scope > .error")) renderedWith.set(name, tabArg);
    else renderedWith.delete(name);
  });
}

/* Dataset provenance lives in the sidebar on every page; the header badge only
   appears when it needs attention (sample data or no database). */
function renderDatasetStatus(ds) {
  const badge = $("#data-badge");
  const note = $("#sidebar-dataset");
  if (!ds) {
    badge.hidden = false; badge.className = "badge warn"; badge.textContent = "Database unavailable";
    note.textContent = "No database loaded.";
    return;
  }
  badge.hidden = !ds.is_demo;
  badge.className = "badge warn";
  badge.textContent = "Sample data";
  note.innerHTML = `<strong>${esc(ds.dataset_name)}</strong>${num(ds.match_count)} matches · ${esc(ds.date_start || "?")} to ${esc(ds.date_end || "?")}`;
}

const icon = (paths) => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
const QUICK_ACTIONS = [
  ["analytics", "Look up a player", "Career numbers, recent form and role.", icon('<path d="M22 7l-8.5 8.5-5-5L2 17"/><path d="M16 7h6v6"/>')],
  ["compare", "Compare two players", "Side-by-side batting and bowling.", icon('<path d="M8 3L4 7l4 4M4 7h16M16 21l4-4-4-4M20 17H4"/>')],
  ["predictor", "Pick my XI", "Rank a squad for a ground and opponent.", icon('<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M9 14l2 2 4-4"/>')],
  ["agents", "Ask the agents", "Get a written selection recommendation.", icon('<rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4M8 16h.01M16 16h.01"/>')],
  ["replay", "Replay a match", "Watch a real match ball by ball.", icon('<circle cx="12" cy="12" r="10"/><path d="M10 8l6 4-6 4z"/>')],
];

async function loadOverview() {
  const root = $("#tab-overview");
  root.innerHTML = `<p class="muted">Loading dashboard…</p>`;
  try {
    const data = await fetchJSON("/api/overview");
    currentDataset = await fetchJSON("/api/dataset");
    renderDatasetStatus(currentDataset);
    const kpis = [
      ["Matches", data.total_matches],
      ["Players", data.total_players],
      ["Deliveries", data.total_deliveries],
      ["Saved player snapshots", data.players_in_memory],
    ];
    root.innerHTML = `
      <section class="dataset-status">
        <div>
          <h2>${esc(currentDataset.dataset_name)}${currentDataset.is_demo ? ` <span class="conf LOW">Sample data</span>` : ""}</h2>
          <p class="muted">${esc(currentDataset.date_start || "?")} to ${esc(currentDataset.date_end || "?")} · ${num(currentDataset.team_count)} teams · ${esc((currentDataset.formats || []).join(", ") || "formats not recorded")}</p>
          <p class="muted small">Source: ${esc(currentDataset.source)}. ${esc(data.note)}</p>
        </div>
        ${(currentDataset.competitions || []).length ? `
        <details class="competitions">
          <summary>Browse ${num(currentDataset.competitions.length)} competitions</summary>
          <input type="search" class="competition-filter" placeholder="Filter competitions" aria-label="Filter competitions">
          <ul>${currentDataset.competitions.slice().sort().map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
        </details>` : ""}
      </section>
      <p class="section-label">Start here</p>
      <div class="quick-actions">${QUICK_ACTIONS.map(([tab, title, text, icon]) => `
        <button class="quick-card" type="button" data-go="${tab}">${icon}<strong>${title}</strong><span>${text}</span></button>`).join("")}
      </div>
      <div class="grid kpis">${kpis.map(([l,v]) => `<article class="card kpi"><div class="label">${l}</div><div class="value">${num(v)}</div></article>`).join("")}</div>
      <div class="grid two" style="margin-top:14px">
        <article class="card">
          <h2>Recent matches</h2>
          <table>${data.recent_matches.map((m) => `<tr><td>${esc(m.team_home)} vs ${esc(m.team_away)}</td><td class="muted nowrap">${esc(m.match_date || "")}</td><td>${esc(m.winner || "—")}</td></tr>`).join("") || `<tr><td class="empty">No matches ingested.</td></tr>`}</table>
        </article>
        <article class="card">
          <h2>Top run-scorers</h2>
          <p class="hint">Click a name to open the player's profile.</p>
          <table>${data.top_performers.by_runs.map((p) => `<tr class="clickable" data-p="${esc(p.player)}" tabindex="0"><td>${esc(p.player)}</td><td class="num">${num(p.runs)}</td></tr>`).join("")}</table>
        </article>
      </div>
    `;
    root.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.go)));
    root.querySelectorAll("tr[data-p]").forEach((row) => {
      const open = () => showTab("analytics", row.dataset.p);
      row.addEventListener("click", open);
      row.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
    });
    const filter = root.querySelector(".competition-filter");
    if (filter) filter.addEventListener("input", () => {
      const q = filter.value.trim().toLowerCase();
      root.querySelectorAll(".competitions li").forEach((li) => { li.hidden = q && !li.textContent.toLowerCase().includes(q); });
    });
  } catch (err) {
    root.innerHTML = `<p class="error">${err.message}</p>`;
    renderDatasetStatus(null);
  }
}

async function loadMatches() {
  const root = $("#tab-matches");
  root.innerHTML = `<p class="muted">Loading matches…</p>`;
  try {
    const matches = await fetchJSON("/api/matches");
    availableMatches = matches;
    if (!matches.length) {
      root.innerHTML = `<div class="card empty">No matches. Ingest fixtures: <code>python -m cricket_agent.ingest tests/fixtures</code></div>`;
      return;
    }
    const newestFirst = matches.slice().reverse();
    root.innerHTML = `
      <div id="match-detail"></div>
      <article class="card">
        <div class="list-head">
          <h2>Matches</h2>
          <input id="match-filter" type="search" placeholder="Team, venue or competition" aria-label="Filter matches">
        </div>
        <p class="hint" data-count-line></p>
        <table id="match-table">
          <thead><tr><th>Match</th><th>Date</th><th>Venue</th><th>Competition</th><th>Winner</th></tr></thead>
          <tbody data-list></tbody>
        </table>
        <button class="btn ghost more" type="button" data-more>Show more</button>
      </article>`;
    const card = root.querySelector("article.card");
    const row = (m) => `<tr class="clickable" data-id="${esc(m.match_id)}" tabindex="0"><td>${esc(m.team_home)} vs ${esc(m.team_away)}</td><td class="nowrap">${esc(m.match_date || "")}</td><td>${esc(m.venue || "")}</td><td class="muted">${esc(m.competition || "")}</td><td>${esc(m.winner || "—")}</td></tr>`;
    const render = () => {
      const q = $("#match-filter").value.trim().toLowerCase();
      const hits = q ? newestFirst.filter((m) => [m.team_home, m.team_away, m.venue, m.competition].some((v) => (v || "").toLowerCase().includes(q))) : newestFirst;
      pagedList(card, hits, row, { pageSize: 50, noun: "matches, newest first", empty: "No matches match that filter." });
    };
    let filterTimer = null;
    $("#match-filter").addEventListener("input", () => { clearTimeout(filterTimer); filterTimer = setTimeout(render, 150); });
    const open = (tr) => {
      showMatch(tr.dataset.id);
      $("#match-detail").scrollIntoView({ behavior: REDUCED_MOTION ? "auto" : "smooth", block: "start" });
    };
    const tbody = card.querySelector("tbody");
    tbody.addEventListener("click", (e) => { const tr = e.target.closest("tr[data-id]"); if (tr) open(tr); });
    tbody.addEventListener("keydown", (e) => { const tr = e.target.closest("tr[data-id]"); if (tr && e.key === "Enter") open(tr); });
    render();
  } catch (err) {
    root.innerHTML = `<p class="error">${err.message}</p>`;
  }
}

function buildMatchInsights(match, deliveries) {
  const inningsList = match.innings || [];
  const inningsStats = new Map(inningsList.map((innings) => [innings.innings_index, {
    ...innings,
    extrasByType: { wides: 0, noballs: 0, byes: 0, legbyes: 0, penalty: 0 },
    batting: new Map(),
    bowling: new Map(),
    falls: [],
    scoreAtWicket: 0,
    wicketsAtFall: 0,
  }]));
  const overs = {};
  const partnerships = [];
  let activePartnership = null;
  let activeInningsIndex = null;
  const keyMoments = [];

  deliveries.forEach((d) => {
    const innings = inningsStats.get(d.innings_index);
    if (!innings) return;
    const key = `${d.innings_index}:${d.over_number}`;
    if (!overs[key]) {
      overs[key] = { over: d.over_number, innings: d.innings_index, team: innings.batting_team, runs: 0, wickets: 0 };
    }
    overs[key].runs += Number(d.runs_total || 0);
    overs[key].wickets += dismissalCount(d.wickets);
    innings.scoreAtWicket += Number(d.runs_total || 0);

    Object.entries(d.extras || {}).forEach(([name, value]) => {
      if (value) innings.extrasByType[name] = (innings.extrasByType[name] || 0) + Number(value);
    });

    if (d.batter) {
      if (!innings.batting.has(d.batter)) {
        innings.batting.set(d.batter, { player: d.batter, runs: 0, balls: 0, fours: 0, sixes: 0, dismissal: "not out" });
      }
      const batter = innings.batting.get(d.batter);
      batter.runs += Number(d.runs_batter || 0);
      if (!(d.extras || {}).wides) batter.balls += 1;
      if (d.runs_batter === 4) batter.fours += 1;
      if (d.runs_batter === 6) batter.sixes += 1;
    }

    if (d.bowler) {
      if (!innings.bowling.has(d.bowler)) {
        innings.bowling.set(d.bowler, { player: d.bowler, balls: 0, runs: 0, wickets: 0, overs: {} });
      }
      const bowler = innings.bowling.get(d.bowler);
      const extras = d.extras || {};
      bowler.runs += Math.max(0, Number(d.runs_total || 0) - (extras.byes || 0) - (extras.legbyes || 0) - (extras.penalty || 0));
      if (!extras.wides && !extras.noballs) bowler.balls += 1;
      const bowlerOver = bowler.overs[d.over_number] || { conceded: 0, legalBalls: 0 };
      bowlerOver.conceded += Math.max(0, Number(d.runs_total || 0) - (extras.byes || 0) - (extras.legbyes || 0) - (extras.penalty || 0));
      if (!extras.wides && !extras.noballs) bowlerOver.legalBalls += 1;
      bowler.overs[d.over_number] = bowlerOver;
    }

    if ((d.wickets || []).length) {
      d.wickets.forEach((wicket) => {
        if (isDismissal(wicket)) {
          innings.wicketsAtFall += 1;
          innings.falls.push(`${innings.wicketsAtFall}-${innings.scoreAtWicket} (${wicket.player_out || "batter"}, ${d.actual_delivery})`);
        }
        keyMoments.push({
          over: `${d.innings_index + 1}.${d.actual_delivery}`,
          label: `${wicket.kind || "Wicket"} · ${wicket.player_out || ""}`.trim(),
        });
        const dismissed = innings.batting.get(wicket.player_out);
        if (dismissed) {
          const kind = (wicket.kind || "").toLowerCase();
          const fielders = (wicket.fielders || []).join(", ");
          const bowlerCredited = BOWLER_WICKET_KINDS.includes(kind);
          if (kind === "caught") dismissed.dismissal = `c ${fielders || "fielder"} b ${d.bowler || "bowler"}`;
          else if (kind === "run out") dismissed.dismissal = `run out (${fielders || "fielder"})`;
          else if (bowlerCredited) dismissed.dismissal = `${kind} b ${d.bowler || "bowler"}`;
          else dismissed.dismissal = kind;
        }
        const bowler = innings.bowling.get(d.bowler);
        if (bowler && BOWLER_WICKET_KINDS.includes((wicket.kind || "").toLowerCase())) {
          bowler.wickets += 1;
        }
      });
    }

    if (activeInningsIndex !== d.innings_index) {
      activeInningsIndex = d.innings_index;
      activePartnership = null;
    }
    if (d.batter && d.non_striker) {
      const pair = [d.batter, d.non_striker].sort();
      const pairKey = pair.join("::");
      if (!activePartnership || activePartnership.key !== pairKey) {
        activePartnership = { key: pairKey, pair: `${pair[0]} / ${pair[1]}`, runs: 0, balls: 0 };
        partnerships.push(activePartnership);
      }
      activePartnership.runs += Number(d.runs_total || 0);
      const extras = d.extras || {};
      if (!extras.wides && !extras.noballs) activePartnership.balls += 1;
      if (dismissalCount(d.wickets)) activePartnership = null;
    }
  });

  const overRows = Object.values(overs).sort((a, b) => {
    if (a.innings !== b.innings) return a.innings - b.innings;
    return a.over - b.over;
  });

  partnerships.sort((a, b) => b.runs - a.runs);
  const inningsSummary = [...inningsStats.values()].map((innings) => {
    (match.players?.[innings.batting_team] || []).forEach((player) => {
      if (!innings.batting.has(player)) {
        innings.batting.set(player, { player, runs: 0, balls: 0, fours: 0, sixes: 0, dismissal: "did not bat" });
      }
    });
    const batting = [...innings.batting.values()].map((batter) => ({
      ...batter,
      strikeRate: batter.balls ? (100 * batter.runs) / batter.balls : 0,
    }));
    const bowling = [...innings.bowling.values()].map((bowler) => {
      const maidenOvers = Object.values(bowler.overs).filter((over) => over.legalBalls === 6 && over.conceded === 0).length;
      return {
        ...bowler,
        maidenOvers,
        oversDisplay: `${Math.floor(bowler.balls / 6)}.${bowler.balls % 6}`,
        economy: bowler.balls ? (6 * bowler.runs) / bowler.balls : 0,
      };
    });
    const extras = Object.values(innings.extrasByType).reduce((total, value) => total + value, 0);
    return {
      ...innings,
      batting,
      bowling,
      extras,
      overCount: Math.max(1, Math.ceil(innings.legal_deliveries_faced / 6)),
      oversDisplay: `${Math.floor(innings.legal_deliveries_faced / 6)}${innings.legal_deliveries_faced % 6 ? `.${innings.legal_deliveries_faced % 6}` : ""}`,
      dotBalls: inningsSummaryDotBalls(deliveries, innings.innings_index),
    };
  });

  return {
    inningsSummary,
    overRows,
    partnerships,
    keyMoments,
  };
}

function inningsSummaryDotBalls(deliveries, inningsIndex) {
  return deliveries.filter((delivery) => delivery.innings_index === inningsIndex
    && delivery.runs_batter === 0
    && !(delivery.extras || {}).wides
    && !(delivery.extras || {}).noballs).length;
}

function renderWagonWheel(containerId, deliveries) {
  const svg = document.getElementById(containerId);
  if (!svg) return;
  svg.innerHTML = "";

  const size = 300;
  const cx = size / 2;
  const cy = size / 2;
  const rings = [55, 95, 135, 175];
  const sectors = [
    { start: -22.5, end: 22.5 },
    { start: 22.5, end: 67.5 },
    { start: 67.5, end: 112.5 },
    { start: 112.5, end: 157.5 },
    { start: 157.5, end: 202.5 },
    { start: 202.5, end: 247.5 },
    { start: 247.5, end: 292.5 },
    { start: 292.5, end: 337.5 },
  ];

  const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
  sectors.forEach((sector) => {
    const arc = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const start = (sector.start - 90) * (Math.PI / 180);
    const end = (sector.end - 90) * (Math.PI / 180);
    const innerR = 35;
    const outerR = 180;
    const x1 = cx + innerR * Math.cos(start);
    const y1 = cy + innerR * Math.sin(start);
    const x2 = cx + innerR * Math.cos(end);
    const y2 = cy + innerR * Math.sin(end);
    const x3 = cx + outerR * Math.cos(end);
    const y3 = cy + outerR * Math.sin(end);
    const x4 = cx + outerR * Math.cos(start);
    const y4 = cy + outerR * Math.sin(start);
    const largeArc = Math.abs(end - start) > Math.PI ? 1 : 0;
    arc.setAttribute("d", `M ${x1} ${y1} A ${innerR} ${innerR} 0 ${largeArc} 1 ${x2} ${y2} L ${x3} ${y3} A ${outerR} ${outerR} 0 ${largeArc} 0 ${x4} ${y4} Z`);
    arc.setAttribute("fill", "rgba(255,255,255,0.04)");
    arc.setAttribute("stroke", "rgba(255,255,255,0.08)");
    g.appendChild(arc);
  });

  rings.forEach((r) => {
    const ring = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    ring.setAttribute("cx", cx);
    ring.setAttribute("cy", cy);
    ring.setAttribute("r", r);
    ring.setAttribute("fill", "none");
    ring.setAttribute("stroke", "rgba(255,255,255,0.08)");
    g.appendChild(ring);
  });

  const mid = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  mid.setAttribute("cx", cx);
  mid.setAttribute("cy", cy);
  mid.setAttribute("r", 24);
  mid.setAttribute("fill", "rgba(61,214,198,0.12)");
  mid.setAttribute("stroke", "rgba(61,214,198,0.6)");
  g.appendChild(mid);

  const recent = deliveries.slice(-60);
  recent.forEach((d) => {
    const val = Number(d.runs_batter || 0);
    const angle = ((d.ball_in_over || 1) * 28 + (d.over_number || 0) * 3) % 360;
    const radius = val >= 6 ? 150 : val >= 4 ? 116 : val === 0 ? 60 : 82;
    const x = cx + radius * Math.cos((angle - 90) * (Math.PI / 180));
    const y = cy + radius * Math.sin((angle - 90) * (Math.PI / 180));
    const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("cx", x);
    dot.setAttribute("cy", y);
    dot.setAttribute("r", val >= 6 ? 5 : val >= 4 ? 4 : 3);
    dot.setAttribute("fill", val === 0 ? "#ef6b5b" : val >= 4 ? "#e2b857" : "#3dd6c6");
    dot.setAttribute("opacity", "0.9");
    g.appendChild(dot);
  });

  svg.appendChild(g);
}

async function showMatch(id, el = $("#match-detail")) {
  if (!el) return;
  el.innerHTML = `<p class="muted">Loading ${id}…</p>`;
  try {
    const match = await fetchJSON(`/api/matches/${encodeURIComponent(id)}`);
    const deliveries = await fetchJSON(`/api/matches/${encodeURIComponent(id)}/deliveries`);
    renderMatchScorecard(match, deliveries, el);
  } catch (err) {
    el.innerHTML = `<p class="error">${err.message}</p>`;
  }
}

function renderMatchScorecard(match, deliveries, el) {
  const prefix = el.id || "match-detail";
  try {
    const insights = buildMatchInsights(match, deliveries);
    const firstInns = insights.inningsSummary[0] || {};
    const secondInns = insights.inningsSummary[1] || {};
    const battingCards = insights.inningsSummary.map((innings) => `
      <article class="innings-scorecard">
        <header class="innings-scorecard-head">
          <div><span>Innings ${innings.innings_index + 1}</span><h4>${innings.batting_team}</h4></div>
          <strong>${innings.total_runs}/${innings.wickets_lost}<small> (${innings.oversDisplay} ov)</small></strong>
        </header>
        <div class="scorecard-scroll">
          <table class="full-scorecard batting-scorecard">
            <thead><tr><th>Batter</th><th>Dismissal</th><th class="num">R</th><th class="num">B</th><th class="num">4s</th><th class="num">6s</th><th class="num">SR</th></tr></thead>
            <tbody>${innings.batting.map((batter) => `<tr><td><strong>${batter.player}</strong></td><td class="dismissal">${batter.dismissal}</td><td class="num">${batter.runs}</td><td class="num">${batter.balls}</td><td class="num">${batter.fours}</td><td class="num">${batter.sixes}</td><td class="num">${batter.strikeRate.toFixed(1)}</td></tr>`).join("")}</tbody>
            <tfoot>
              <tr><td>Extras</td><td colspan="6">${innings.extras} <span class="muted">(W ${innings.extrasByType.wides}, NB ${innings.extrasByType.noballs}, B ${innings.extrasByType.byes}, LB ${innings.extrasByType.legbyes}, P ${innings.extrasByType.penalty})</span></td></tr>
              <tr><td>Total</td><td colspan="6">${innings.total_runs}/${innings.wickets_lost} <span class="muted">· ${innings.legal_deliveries_faced} legal balls</span></td></tr>
            </tfoot>
          </table>
        </div>
        <h5>Bowling</h5>
        <div class="scorecard-scroll">
          <table class="full-scorecard bowling-scorecard">
            <thead><tr><th>Bowler</th><th class="num">O</th><th class="num">M</th><th class="num">R</th><th class="num">W</th><th class="num">Econ</th></tr></thead>
            <tbody>${innings.bowling.map((bowler) => `<tr><td><strong>${bowler.player}</strong></td><td class="num">${bowler.oversDisplay}</td><td class="num">${bowler.maidenOvers}</td><td class="num">${bowler.runs}</td><td class="num">${bowler.wickets}</td><td class="num">${bowler.economy.toFixed(2)}</td></tr>`).join("")}</tbody>
          </table>
        </div>
        <p class="fall-of-wickets"><strong>Fall of wickets:</strong> ${innings.falls.join(" · ") || "No wickets"}</p>
      </article>
    `).join("");

    const overRows = insights.overRows.map((over) => `
      <tr>
        <td>${over.innings + 1}</td>
        <td>${over.over + 1}</td>
        <td class="num">${over.runs}</td>
        <td class="num">${over.wickets}</td>
      </tr>
    `).join("");

    const partnershipRows = insights.partnerships.map((item) => `
      <tr>
        <td>${item.pair}</td>
        <td class="num">${item.runs}</td>
        <td class="num">${item.balls}</td>
      </tr>
    `).join("") || `<tr><td colspan="3" class="muted">No partnership data.</td></tr>`;

    const keyMoments = insights.keyMoments.slice(0, 8).map((moment, i) => `
      <li style="animation-delay: ${i * 0.08}s"><span>${moment.over}</span><strong>${moment.label}</strong></li>
    `).join("") || `<li class="muted">No wickets or key moments.</li>`;

    const year = match.simulated ? "Simulation" : match.season || (match.date && match.date.slice(0, 4)) || "Year unavailable";
    el.innerHTML = `
      <article class="card match-hero" style="margin-top:14px">
        <div class="hero-head">
          <div>
            <p class="eyebrow">${match.competition || "Match"} · ${match.match_type || "Cricket"} · ${year} · Match #${match.match_id}</p>
            <h2>${match.teams[0]} vs ${match.teams[1]}</h2>
          </div>
          <div class="topbar-actions"><div class="status-chip">${match.winner ? `${match.winner} won` : "Result pending"}</div><button class="btn ghost" type="button" onclick="window.print()">Export to PDF</button></div>
        </div>
        <div class="hero-scoreboard">
          <div class="score-pill big"><span>${firstInns.batting_team || "Innings 1"}</span><strong>${firstInns.total_runs ?? 0}/${firstInns.wickets_lost ?? 0}</strong></div>
          <div class="score-pill"><span>${secondInns.batting_team || "Innings 2"}</span><strong>${secondInns.total_runs == null ? "—" : `${secondInns.total_runs}/${secondInns.wickets_lost}`}</strong></div>
          <div class="score-pill"><span>Player of the match</span><strong>${match.player_of_match || (match.simulated ? "Not awarded" : "Not available")}</strong></div>
          <div class="score-pill"><span>Venue</span><strong>${match.venue || "Unknown"}</strong></div>
          <div class="score-pill"><span>Extras</span><strong>${(firstInns.extras || 0) + (secondInns.extras || 0)}</strong></div>
        </div>
        <p class="hint">${match.outcome_summary || "Result unavailable"}</p>
      </article>

      <div class="scorecard-grid">
        <article class="card full-scorecard-panel">
          <h3>Full scorecard</h3>
          <div class="innings-scorecards">${battingCards}</div>
        </article>
      </div>

      <div class="scorecard-grid">
        <article class="card">
          <h3>Over by over</h3>
          <table class="compact-table">
            <tr><th>Inns</th><th>Over</th><th>Runs</th><th>Wkts</th></tr>
            ${overRows}
          </table>
          <canvas id="${prefix}-over-chart" height="140"></canvas>
        </article>
        <article class="card">
          <h3>Partnerships</h3>
          <table class="compact-table">
            <tr><th>Pair</th><th>Runs</th><th>Balls</th></tr>
            ${partnershipRows}
          </table>
        </article>
      </div>

      <div class="scorecard-grid">
        <article class="card">
          <h3>Key moments</h3>
          <ul class="moment-list">${keyMoments}</ul>
        </article>
      </div>

    <article class="card">
        <h3>Run progression</h3>
        <canvas id="${prefix}-score-chart" height="120"></canvas>
      </article>
    `;

    const maximumOverCount = Math.max(0, ...insights.overRows.map((over) => over.over + 1));
    const overLabels = Array.from({ length: maximumOverCount }, (_, index) => `Over ${index + 1}`);
    barChart(`${prefix}-over-chart`, overLabels, insights.inningsSummary.map((innings, seriesIndex) => ({
      label: innings.batting_team,
      data: overLabels.map((_, overIndex) => insights.overRows.find((over) => over.innings === innings.innings_index && over.over === overIndex)?.runs ?? null),
      backgroundColor: SERIES[seriesIndex % SERIES.length],
      borderRadius: 2,
    })));

    const maxBalls = Math.max(0, ...insights.inningsSummary.map(innings =>
      deliveries.filter(d => d.innings_index === innings.innings_index).length
    ));
    const sharedProgressionLabels = Array.from({ length: maxBalls }, (_, i) => `Ball ${i + 1}`);

    const sharedProgressionDatasets = insights.inningsSummary.map((innings, seriesIndex) => {
      const inningsIndex = innings.innings_index;
      let total = 0;
      const inningsDeliveries = deliveries.filter(d => d.innings_index === inningsIndex);

      const wicketColors = [];
      const wicketRadii = [];

      const data = sharedProgressionLabels.map((_, i) => {
        if (i < inningsDeliveries.length) {
          const d = inningsDeliveries[i];
          total += Number(d.runs_total || 0);

          const hasWicket = dismissalCount(d.wickets) > 0;
          if (hasWicket) {
            wicketRadii.push(4);
            wicketColors.push('rgba(255, 60, 60, 1)');
          } else {
            wicketRadii.push(0);
            wicketColors.push('transparent');
          }

          return total;
        }
        return null;
      });

      return {
        label: innings.batting_team,
        data: data,
        borderColor: SERIES[seriesIndex % SERIES.length],
        tension: 0,
        pointRadius: wicketRadii,
        pointBackgroundColor: wicketColors,
        pointBorderColor: wicketColors,
        pointHoverRadius: wicketRadii.map(r => r > 0 ? 6 : 0)
      };
    });

    lineChart(
      `${prefix}-score-chart`,
      sharedProgressionLabels,
      sharedProgressionDatasets
    );
  } catch (err) {
    el.innerHTML = `<p class="error">${err.message}</p>`;
  }
}

async function loadPlayers() {
  const root = $("#tab-players");
  root.innerHTML = `<p class="muted">Loading players…</p>`;
  try {
    const dataset = currentDataset || await fetchJSON("/api/dataset");
    const teams = dataset.teams || [];
    const formats = dataset.formats || [];
    const allPlayers = await fetchJSON("/api/players?details=1");
    const byMatches = allPlayers.slice().sort((a, b) => b.matches_count - a.matches_count || a.player_name.localeCompare(b.player_name));
    const countries = [...new Set(allPlayers.map((p) => p.country).filter(Boolean))].sort();
    root.innerHTML = `
      <article class="card">
        <h2>Player Explorer</h2>
        <p class="hint">Search the ${dataset.player_count} players represented in the active database.</p>
        <form id="player-search-form" class="row">
          <div class="field grow"><label for="player-search">Search</label><input id="player-search" type="search" placeholder="Player name"></div>
          <div class="field"><label for="player-team">Team</label><select id="player-team"><option value="">All teams</option>${teams.map((t) => `<option>${t}</option>`).join("")}</select></div>
          <div class="field"><label for="player-format">Format</label><select id="player-format"><option value="">All formats</option>${formats.map((f) => `<option>${f}</option>`).join("")}</select></div>
          <div class="field"><label for="player-country">Country</label><select id="player-country"><option value="">All countries</option>${countries.map((c) => `<option>${c}</option>`).join("")}</select></div>
          <div class="field"><label for="player-sort">Sort</label><select id="player-sort"><option value="matches">Most matches</option><option value="name">Name</option></select></div>
        </form>
      </article>
      <section id="player-results-wrap">
        <p class="hint" data-count-line></p>
        <div id="player-results" class="player-grid" data-list></div>
        <button class="btn ghost more" type="button" data-more>Show more players</button>
      </section>`;
    let searchRequest = 0;
    const searchPlayers = async () => {
      const request = ++searchRequest; // an older, slower search must not overwrite a newer one
      const team = $("#player-team").value, format = $("#player-format").value;
      const byName = $("#player-sort").value === "name";
      // Team and format need match history from the server; name, country and
      // sort are applied here, so typing filters instantly.
      let base = byName ? allPlayers : byMatches;
      if (team || format) {
        $("#player-results-wrap [data-count-line]").textContent = "Filtering…";
        const server = await fetchJSON(`/api/players?${new URLSearchParams({ details: "1", team, format, sort: byName ? "name" : "matches" })}`);
        if (request !== searchRequest) return;
        base = server;
      }
      const q = $("#player-search").value.trim().toLowerCase();
      const country = $("#player-country").value;
      const players = base.filter((p) => (!q || p.player_name.toLowerCase().includes(q)) && (!country || p.country === country));
      availablePlayers = players.map((p) => p.player_name);
      const known = (v) => v && v !== "Not available";
      pagedList($("#player-results-wrap"), players, (p) => `
        <button class="player-card" type="button" data-p="${esc(p.player_name)}">
          <strong>${esc(p.player_name)}</strong>
          <span class="muted">${esc([p.team, p.country !== p.team ? p.country : ""].filter(known).join(" · ") || "Team not recorded")}</span>
          <span><b>${num(p.matches_count)}</b> ${p.matches_count === 1 ? "match" : "matches"} · ${esc((p.formats || []).join(", ") || "format not recorded")}</span>
        </button>`, { noun: "players", empty: "No players match these filters." });
    };
    $("#player-results").addEventListener("click", (e) => { const b = e.target.closest("[data-p]"); if (b) showTab("analytics", b.dataset.p); });
    let searchTimer = null;
    $("#player-search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(searchPlayers, 60); });
    root.querySelectorAll("#player-search-form select").forEach((sel) => sel.addEventListener("change", searchPlayers));
    $("#player-search-form").addEventListener("submit", (event) => { event.preventDefault(); searchPlayers(); });
    searchPlayers();
  } catch (err) {
    root.innerHTML = `<p class="error">${err.message}</p>`;
  }
}

async function loadUpcoming() {
  const root = $("#tab-upcoming");
  root.innerHTML = `<p class="muted">Checking configured match provider…</p>`;
  try {
    const data = await fetchJSON("/api/upcoming");
    if (!data.available) {
      const setupMessage = data.source === "unavailable"
        ? "Set CRICKETDATA_API_KEY in the server environment and restart Flask."
        : "Check the provider key and request quota, then retry.";
      root.innerHTML = data.source === "unavailable" ? `
        <article class="card empty-state">
          <h2>No fixture feed connected</h2>
          <p>Upcoming fixtures come from CricketData.org, a near-live third-party feed (not an official ICC/BCCI source). It is not configured on this server.</p>
          <ol>
            <li>Get a free API key at cricketdata.org.</li>
            <li>Set <code>CRICKETDATA_API_KEY</code> in the server environment.</li>
            <li>Restart the server.</li>
          </ol>
          <div class="actions">
            <button class="btn" type="button" data-go="predictor">Plan an XI in Match predictor</button>
            <button class="btn ghost" type="button" data-go="simulate">Simulate a custom fixture</button>
          </div>
        </article>` : `
        <article class="card empty-state">
          <h2>Fixture feed unavailable</h2>
          <p>${esc(data.message)}</p>
          <p>${esc(setupMessage)}</p>
          <div class="actions"><button class="btn ghost" type="button" data-go="upcoming-retry">Retry</button></div>
        </article>`;
      root.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => {
        if (b.dataset.go === "upcoming-retry") loadUpcoming(); else showTab(b.dataset.go);
      }));
      return;
    }
    root.innerHTML = `<div class="fixture-feed">${data.matches.map((match, index) => `
      <article class="card fixture-row">
        <div class="fixture-date"><strong>${match.date || "Date unavailable"}</strong><span>${match.time || "Start time unavailable"}</span></div>
        <div class="fixture-main"><span class="fixture-status">${match.status || "Scheduled"}</span><h2>${match.team_a} <i>vs</i> ${match.team_b}</h2><p>${match.competition || "Competition unavailable"} · ${match.format || "Format unavailable"}</p><p class="muted">${match.venue || "Venue unavailable"} · ${match.source}</p></div>
        <button class="btn fixture-simulate" data-fixture="${index}" type="button">Simulate match</button>
      </article>`).join("")}</div>`;
    root.querySelectorAll("button[data-fixture]").forEach((button) => {
      button.addEventListener("click", () => {
        selectedSimulationFixture = data.matches[Number(button.dataset.fixture)];
        showTab("simulate");
      });
    });
  } catch (err) {
    root.innerHTML = `<p class="error">${err.message}</p>`;
  }
}

function analyticsForm(prefill) {
  return `
    <form id="analytics-form" class="row">
      <div class="field"><label for="analytics-player">Player</label><input id="analytics-player" list="player-options" value="${esc(prefill || availablePlayers[0] || "")}" required></div>
      <div class="field"><label for="analytics-n">Form window</label><input id="analytics-n" type="number" min="1" placeholder="All matches"></div>
      <button class="btn" type="submit">Open portfolio</button>
    </form>
    <p class="hint">Lifetime figures always cover the whole career. The form window only changes the analysis section below them.</p>
    <datalist id="player-options"></datalist>
    <div id="analytics-out"></div>`;
}

async function loadAnalytics(prefill) {
  const root = $("#tab-analytics");
  if (!availablePlayers.length) availablePlayers = await fetchJSON("/api/players").catch(() => []);
  const current = prefill || ($("#analytics-player") ? $("#analytics-player").value : availablePlayers[0] || "");
  root.innerHTML = analyticsForm(current);
  fillPlayers();
  $("#analytics-form").addEventListener("submit", (e) => {
    e.preventDefault();
    loadPlayerProfile($("#analytics-player").value, $("#analytics-n").value);
  });
  if (current) return loadPlayerProfile(current);
}

async function fillPlayers() {
  try {
    const players = await fetchJSON("/api/players");
    document.querySelectorAll("#player-options").forEach((dl) => {
      dl.innerHTML = players.map((p) => `<option value="${esc(p)}">`).join("");
    });
  } catch (_) { /* autocomplete optional */ }
}

/* ---------- Player portfolio: motion helpers ---------- */

const REDUCED_MOTION = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let profileRequest = 0;
let revealObserver = null;
const revealCallbacks = new WeakMap();

function onReveal(el, fn) {
  if (el) revealCallbacks.set(el, fn);
}

function countUp(el) {
  if (el.dataset.counted) return;
  el.dataset.counted = "1";
  const target = Number(el.dataset.count);
  const decimals = Number(el.dataset.decimals || 0);
  const render = (v) => {
    el.textContent = decimals ? v.toFixed(decimals) : Math.round(v).toLocaleString();
  };
  if (REDUCED_MOTION || !Number.isFinite(target) || target === 0) { render(target || 0); return; }
  const duration = 900;
  const start = performance.now();
  const step = (now) => {
    const t = Math.min(1, (now - start) / duration);
    render(target * (1 - Math.pow(2, -10 * t))); // expo-out: fast start, gentle settle
    if (t < 1) requestAnimationFrame(step);
    else render(target);
  };
  requestAnimationFrame(step);
}

function revealIn(el) {
  el.classList.add("is-in");
  el.querySelectorAll("[data-count]").forEach(countUp);
  const fn = revealCallbacks.get(el);
  if (fn) { revealCallbacks.delete(el); fn(); }
}

function observeReveals(root) {
  const items = root.querySelectorAll(".reveal:not(.is-in)");
  if (REDUCED_MOTION || !("IntersectionObserver" in window)) { items.forEach(revealIn); return; }
  if (!revealObserver) {
    revealObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        revealIn(entry.target);
        revealObserver.unobserve(entry.target);
      });
    }, { threshold: 0.15 });
  }
  items.forEach((el) => revealObserver.observe(el));
}

/* ---------- Player portfolio: rendering ---------- */

function stat(label, value, { decimals = 0, suffix = "", sub = "" } = {}) {
  const shown = value === null || value === undefined
    ? `<strong>—</strong>`
    : `<strong><span data-count="${value}" data-decimals="${decimals}">0</span>${suffix}</strong>`;
  return `<div class="pf-stat"><span>${label}</span>${shown}${sub ? `<small>${sub}</small>` : ""}</div>`;
}

function battingLedger(b) {
  if (!b) return "";
  const hs = `${b.highest.runs}${b.highest.not_out ? "*" : ""}`;
  return `
    <section class="pf-ledger reveal" aria-label="Lifetime batting">
      <h3>Batting</h3>
      <div class="pf-stats">
        ${stat("Runs", b.runs, { sub: `${b.innings} inns · ${b.not_outs} NO` })}
        ${stat("Average", b.average, { decimals: 2 })}
        ${stat("Strike rate", b.strike_rate, { decimals: 1 })}
        ${stat("100s", b.hundreds, { sub: `${b.fifties} fifties` })}
        <div class="pf-stat"><span>Highest</span><strong>${hs}</strong><small>${b.ducks} ducks</small></div>
        ${stat("4s / 6s", b.fours, { sub: `${b.sixes.toLocaleString()} sixes` })}
      </div>
    </section>`;
}

function bowlingLedger(b) {
  if (!b || !b.legal_deliveries) return "";
  return `
    <section class="pf-ledger reveal" aria-label="Lifetime bowling">
      <h3>Bowling</h3>
      <div class="pf-stats">
        ${stat("Wickets", b.wickets, { sub: `${b.overs} overs` })}
        ${stat("Economy", b.economy, { decimals: 2 })}
        ${stat("Average", b.average, { decimals: 2 })}
        ${stat("Strike rate", b.strike_rate, { decimals: 1 })}
        <div class="pf-stat"><span>Best</span><strong>${b.best.wickets}/${b.best.runs}</strong><small>in an innings</small></div>
        ${stat("5W hauls", b.five_wicket_hauls, { sub: `${b.three_wicket_hauls} three-fors` })}
      </div>
    </section>`;
}

function formatSplits(rows, career) {
  const totalRuns = (career.batting && career.batting.runs) || 0;
  const totalWkts = (career.bowling && career.bowling.wickets) || 0;
  const body = rows.map((f, i) => {
    const runShare = totalRuns && f.batting ? f.batting.runs / totalRuns : 0;
    const wktShare = totalWkts && f.bowling ? f.bowling.wickets / totalWkts : 0;
    return `
      <tr class="reveal" style="--i:${i}">
        <th scope="row">${esc(f.label)}</th>
        <td class="num">${f.matches}</td>
        <td class="num">${f.batting ? f.batting.runs.toLocaleString() : "—"}
          <span class="pf-share" style="--share:${runShare.toFixed(3)}" aria-hidden="true"></span></td>
        <td class="num">${f.batting ? fmt(f.batting.average, 2) : "—"}</td>
        <td class="num">${f.batting ? fmt(f.batting.strike_rate) : "—"}</td>
        <td class="num">${f.batting ? `${f.batting.hundreds}/${f.batting.fifties}` : "—"}</td>
        <td class="num">${f.bowling && f.bowling.legal_deliveries ? f.bowling.wickets : "—"}
          <span class="pf-share alt" style="--share:${wktShare.toFixed(3)}" aria-hidden="true"></span></td>
        <td class="num">${f.bowling && f.bowling.legal_deliveries ? fmt(f.bowling.economy, 2) : "—"}</td>
      </tr>`;
  }).join("");
  return `
    <article class="card pf-block reveal">
      <h3>By format</h3>
      <div class="scorecard-scroll">
        <table class="pf-table">
          <thead><tr><th>Format</th><th class="num">M</th><th class="num">Runs</th><th class="num">Avg</th><th class="num">SR</th><th class="num">100/50</th><th class="num">Wkts</th><th class="num">Econ</th></tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </article>`;
}

function inningsLine(i) {
  return `<li class="reveal" style="--i:${i.idx}">
    <strong>${i.runs}${i.not_out ? "*" : ""}<small> (${i.balls})</small></strong>
    <span>v ${esc(i.opponent || "—")} · ${esc(i.format)} · ${esc(i.date || "")}</span>
    <em>${esc(i.venue || "")}</em></li>`;
}

function spellLine(s) {
  return `<li class="reveal" style="--i:${s.idx}">
    <strong>${s.wickets}/${s.conceded}<small> (${Math.floor(s.legal / 6)}.${s.legal % 6} ov)</small></strong>
    <span>v ${esc(s.opponent || "—")} · ${esc(s.format)} · ${esc(s.date || "")}</span>
    <em>${esc(s.venue || "")}</em></li>`;
}

function renderCareer(c) {
  const bat = c.batting, bowl = c.bowling;
  // Lead with the discipline the career is built on.
  const bowlingFirst = bowl && (!bat || bowl.wickets * 25 > bat.runs);
  const ledgers = bowlingFirst ? bowlingLedger(bowl) + battingLedger(bat) : battingLedger(bat) + bowlingLedger(bowl);
  const teams = c.teams.slice(0, 4).map(esc).join(" · ") + (c.teams.length > 4 ? ` · +${c.teams.length - 4} more` : "");
  const first = (c.span.first_match || "").slice(0, 4), last = (c.span.last_match || "").slice(0, 4);
  return `
    <header class="pf-hero reveal">
      <div>
        <p class="pf-span">${first || "—"} <i aria-hidden="true">→</i> ${last || "—"} · ${c.span.matches.toLocaleString()} matches · ${c.span.seasons} seasons</p>
        <h2 class="pf-name">${esc(c.player)}</h2>
        <p class="pf-teams">${teams || "Team not recorded"}</p>
      </div>
      <button class="btn ghost" type="button" onclick="window.print()">Export to PDF</button>
    </header>
    <div class="pf-ledgers">${ledgers}</div>
    <article class="card pf-block reveal">
      <div class="pf-block-head">
        <h3>Career timeline</h3>
        <p class="muted">Runs per calendar year (bars) and wickets (line).</p>
      </div>
      <canvas id="career-chart" height="110" role="img" aria-label="Runs and wickets per year for ${esc(c.player)}"></canvas>
    </article>
    ${formatSplits(c.by_format, c)}
    <div class="grid pf-split">
      <article class="card pf-block reveal">
        <h3>Best innings</h3>
        ${c.best_innings.length ? `<ol class="pf-list">${c.best_innings.map((x, idx) => inningsLine({ ...x, idx })).join("")}</ol>` : `<p class="muted">Did not bat.</p>`}
      </article>
      <article class="card pf-block reveal">
        <h3>Best bowling</h3>
        ${c.best_bowling.length ? `<ol class="pf-list">${c.best_bowling.map((x, idx) => spellLine({ ...x, idx })).join("")}</ol>` : `<p class="muted">No wickets recorded.</p>`}
      </article>
    </div>
    <article class="card pf-block reveal">
      <h3>Competitions</h3>
      <table class="pf-table">
        <thead><tr><th>Competition</th><th class="num">M</th><th class="num">Runs</th><th class="num">Wkts</th></tr></thead>
        <tbody>${c.competitions.map((x) => `<tr><td>${esc(x.competition)}</td><td class="num">${x.matches}</td><td class="num">${x.runs.toLocaleString()}</td><td class="num">${x.wickets}</td></tr>`).join("")}</tbody>
      </table>
      <p class="hint">${esc(c.note)}</p>
    </article>`;
}

function careerChart(c) {
  const el = document.getElementById("career-chart");
  if (!el || !window.Chart) return;
  if (charts["career-chart"]) charts["career-chart"].destroy();
  const t = c.timeline.filter((y) => y.year !== "Unknown");
  const hasBowling = t.some((y) => y.wickets > 0);
  charts["career-chart"] = new Chart(el, {
    data: {
      labels: t.map((y) => y.year),
      datasets: [
        { type: "bar", label: "Runs", data: t.map((y) => y.runs), backgroundColor: "rgba(255, 106, 19, 0.75)", hoverBackgroundColor: SERIES[0], borderRadius: 3, yAxisID: "y" },
        ...(hasBowling ? [{ type: "line", label: "Wickets", data: t.map((y) => y.wickets), borderColor: SERIES[1], backgroundColor: SERIES[1], pointRadius: 3, tension: 0, yAxisID: "y1" }] : []),
      ],
    },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      // Bars rise left to right, so the career reads as a story in time.
      animation: REDUCED_MOTION ? false : {
        duration: 700,
        easing: "easeOutQuart",
        delay: (ctx) => (ctx.type === "data" && ctx.mode === "default" ? ctx.dataIndex * 45 : 0),
      },
      plugins: {
        legend: { labels: { color: chartTheme().text } },
        tooltip: { callbacks: { afterBody: (items) => { const y = t[items[0].dataIndex]; return `${y.matches} matches`; } } },
      },
      scales: {
        x: { ticks: { color: chartTheme().text }, grid: { display: false } },
        y: { beginAtZero: true, position: "left", ticks: { color: chartTheme().text }, grid: { color: chartTheme().grid }, title: { display: true, text: "Runs", color: chartTheme().text } },
        ...(hasBowling ? { y1: { beginAtZero: true, position: "right", ticks: { color: chartTheme().text, precision: 0 }, grid: { display: false }, title: { display: true, text: "Wickets", color: chartTheme().text } } } : {}),
      },
    },
  });
}

function windowSkeleton() {
  return `
    <div class="pf-section-head"><h2>Form analysis</h2><p class="muted">Computing form, role, matchups and venues…</p></div>
    <div class="grid two">${'<div class="card skeleton" aria-hidden="true"></div>'.repeat(2)}</div>
    <div class="grid two" style="margin-top:20px">${'<div class="card skeleton tall" aria-hidden="true"></div>'.repeat(2)}</div>`;
}

/* The snapshot an agent run stored for this player, as the coach reads it. */
function memoryBlock(m) {
  if (!m) return `<p class="muted">No snapshot yet. An agent analysis that includes this player stores one, so later sessions can compare against it.</p>`;
  const when = String(m.memory_updated_at || "").replace("T", " ").slice(0, 16);
  const conf = m.confidence || {};
  const role = m.role && m.role.role ? m.role.role : null;
  const form = m.recent_form || {};
  const rows = [
    ["Stored", esc(when || "unknown")],
    ["Matches analysed", num((m.matches_analyzed || []).length)],
    ["Confidence", conf.level ? `<span class="conf ${esc(conf.level)}">${esc(conf.level)}</span>` : "—"],
    ["Role then", esc(role || "Unclassified")],
    ["Batting trend", esc(form.batting && form.batting.statement || "—")],
    ["Bowling trend", esc(form.bowling && form.bowling.statement || "—")],
  ];
  return `<dl class="kv">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
}

function paceSpinBlock(ps) {
  if (!ps) return `<p class="muted">No pace/spin data available.</p>`;
  const line = (label, s) => s
    ? `<div class="pf-stat compact"><span>${label}</span><strong>${s.runs}</strong><small>${s.balls_faced} balls · ${s.dismissals} outs · avg ${fmt(s.average)}</small></div>`
    : `<div class="pf-stat compact"><span>${label}</span><strong>—</strong><small>No known-style deliveries</small></div>`;
  const cov = ps.coverage === null || ps.coverage === undefined ? null : Math.round(ps.coverage * 100);
  return `
    <p><strong>${esc(ps.weakness_analysis)}</strong></p>
    <div class="pf-stats two-up">${line("vs Pace", ps.matchup_stats.Pace)}${line("vs Spin", ps.matchup_stats.Spin)}</div>
    <p class="hint">Cricsheet does not record bowling style. ${cov === null ? "No deliveries in this window." : `Only ${cov}% of balls faced came from bowlers with a known style; no verdict is given below 60%.`}</p>`;
}

function venueBlock(v) {
  if (!v) return `<p class="muted">No venue data available.</p>`;
  const list = (rows, fmtRow) => rows && rows.length ? `<ol class="pf-list plain">${rows.map(fmtRow).join("")}</ol>` : `<p class="muted">None in this window.</p>`;
  return `
    <div class="grid pf-split">
      <div><h4>Most runs</h4>${list(v.top_batting_venues, (r) => `<li><strong>${r.runs}</strong><span>${esc(r.venue || "Unknown venue")}</span><em>${r.matches} matches · ${r.balls_faced} balls</em></li>`)}</div>
      <div><h4>Most wickets</h4>${list(v.top_bowling_venues.filter((r) => r.wickets > 0), (r) => `<li><strong>${r.wickets}</strong><span>${esc(r.venue || "Unknown venue")}</span><em>${r.matches} matches · ${r.legal_deliveries} balls</em></li>`)}</div>
    </div>`;
}

function renderWindow(p, lastN) {
  const bat = p.batting, bowl = p.bowling, role = p.role.primary;
  const scope = lastN ? `Last ${esc(lastN)} matches` : `All ${p.matches_considered.length} matches`;
  return `
    <div class="pf-section-head reveal"><h2>Form analysis</h2><p class="muted">${scope} · <span class="conf ${p.confidence.level}">${p.confidence.level}</span> ${esc(p.confidence.reason)}</p></div>
    <div class="grid two">
      <article class="card reveal">
        <h3>Role</h3>
        <p><strong>${role ? esc(role.role) : "Insufficient data"}</strong></p>
        <p class="muted">${esc(role ? role.reason : p.role.note)}</p>
        <p class="hint">${esc(p.sample_size_warning || p.data_note)}</p>
      </article>
      <article class="card reveal">
        <h3>Historical memory</h3>
        ${memoryBlock(p.historical_memory)}
      </article>
    </div>
    <div class="grid two" style="margin-top:20px">
      <article class="card reveal">
        <h3>Batting form</h3>
        ${bat ? `<div class="pf-stats three-up">
          ${stat("Runs", bat.runs)}${stat("Average", bat.average, { decimals: 1 })}${stat("Strike rate", bat.strike_rate, { decimals: 1 })}
          ${stat("Balls", bat.balls_faced)}${stat("Boundary %", bat.boundary_percentage, { decimals: 1 })}<div class="pf-stat"><span>4s / 6s</span><strong>${bat.fours} / ${bat.sixes}</strong></div>
        </div>` : `<p class="muted">Insufficient batting data.</p>`}
        <p class="muted">${esc(p.batting_form.trend.statement)}</p>
        <canvas id="runs-chart" height="120"></canvas>
      </article>
      <article class="card reveal">
        <h3>Bowling form</h3>
        ${bowl ? `<div class="pf-stats three-up">
          ${stat("Wickets", bowl.wickets)}${stat("Economy", bowl.economy, { decimals: 2 })}${stat("Average", bowl.average, { decimals: 1 })}
          ${stat("Dot %", bowl.dot_ball_percentage, { decimals: 1 })}
        </div>` : `<p class="muted">Insufficient bowling data.</p>`}
        <p class="muted">${esc(p.bowling_form.trend.statement)}</p>
        <canvas id="econ-chart" height="120"></canvas>
      </article>
    </div>
    <div class="grid two" style="margin-top:20px">
      <article class="card reveal">
        <h3>Frequent matchups</h3>
        ${p.matchups.length ? `<table class="pf-table"><thead><tr><th>Bowler</th><th class="num">Runs</th><th class="num">Balls</th><th>Sample</th></tr></thead><tbody>${p.matchups.map((m) => `<tr><td>${esc(m.bowler)}</td><td class="num">${m.runs || 0}</td><td class="num">${m.balls_faced}</td><td><span class="conf ${m.confidence.level}">${m.confidence.level}</span></td></tr>`).join("")}</tbody></table>` : `<p class="muted">No matchup rows.</p>`}
        <p class="muted">Fielding: ${esc(p.fielding.note || `${p.fielding.total_credits} dismissal credit(s).`)}</p>
      </article>
      <article class="card reveal">
        <h3>Pace vs spin</h3>
        ${paceSpinBlock(p.pace_vs_spin)}
      </article>
    </div>
    <article class="card reveal" style="margin-top:20px">
      <h3>Best venues in this window</h3>
      ${venueBlock(p.best_venues)}
    </article>`;
}

function formCharts(p) {
  const batSeries = p.batting_form.matches || [];
  const label = (m) => m.match_date || m.match_id;
  lineChart("runs-chart", batSeries.map(label), [
    { label: "Runs", data: batSeries.map((m) => m.runs), borderColor: SERIES[0], tension: 0 },
    { label: "Strike rate", data: batSeries.map((m) => m.strike_rate), borderColor: SERIES[1], tension: 0 },
  ]);
  const bowlSeries = p.bowling_form.matches || [];
  lineChart("econ-chart", bowlSeries.map(label), [
    { label: "Wickets", data: bowlSeries.map((m) => m.wickets), borderColor: SERIES[0], tension: 0 },
    { label: "Economy", data: bowlSeries.map((m) => m.economy), borderColor: SERIES[1], tension: 0 },
  ]);
}

async function loadPlayerProfile(player, lastN) {
  const out = $("#analytics-out");
  if (!out || !player) return;
  renderedWith.set("analytics", player); // the kept analytics tab now shows this player
  const request = ++profileRequest; // later clicks win; stale responses are dropped
  const isStale = () => request !== profileRequest || !document.body.contains(out);
  destroyCharts($("#tab-analytics"));
  out.innerHTML = `
    <div id="pf-career" class="pf" aria-busy="true">
      <div class="skeleton hero" aria-hidden="true"></div>
      <div class="pf-ledgers">${'<div class="skeleton ledger" aria-hidden="true"></div>'.repeat(2)}</div>
      <p class="sr-only">Loading lifetime analytics for ${esc(player)}…</p>
    </div>
    <div id="pf-window" class="pf" aria-busy="true">${windowSkeleton()}</div>`;

  const q = lastN ? `?last_n=${encodeURIComponent(lastN)}` : "";
  const careerReq = fetchJSON(`/api/players/${encodeURIComponent(player)}/career`);
  const profileReq = fetchJSON(`/api/players/${encodeURIComponent(player)}/profile${q}`);

  try {
    const career = await careerReq;
    if (isStale()) return;
    const box = $("#pf-career");
    box.innerHTML = renderCareer(career);
    box.removeAttribute("aria-busy");
    // Build the chart as its card enters view so the bar stagger is actually seen.
    onReveal($("#career-chart").closest(".reveal"), () => careerChart(career));
    observeReveals(box);
  } catch (err) {
    if (isStale()) return;
    $("#pf-career").innerHTML = `<p class="error">${esc(err.message)}</p>`;
    $("#pf-window").innerHTML = "";
    return;
  }

  try {
    const p = await profileReq;
    if (isStale()) return;
    const box = $("#pf-window");
    box.innerHTML = renderWindow(p, lastN);
    box.removeAttribute("aria-busy");
    observeReveals(box);
    formCharts(p);
  } catch (err) {
    if (isStale()) return;
    $("#pf-window").innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}

async function loadCompare() {
  const root = $("#tab-compare");
  if (!availablePlayers.length) availablePlayers = await fetchJSON("/api/players").catch(() => []);
  // Default to the two leading run-scorers rather than the alphabetically first names.
  const leaders = await fetchJSON("/api/overview").then((d) => d.top_performers.by_runs.map((p) => p.player)).catch(() => []);
  const [defA, defB] = leaders.length >= 2 ? leaders : availablePlayers;
  root.innerHTML = `
    <form id="compare-form" class="row">
      <div class="field"><label for="cmp-a">Player A</label><input id="cmp-a" list="player-options" value="${esc(defA || "")}"></div>
      <div class="field"><label for="cmp-b">Player B</label><input id="cmp-b" list="player-options" value="${esc(defB || defA || "")}"></div>
      <button class="btn" type="submit">Compare</button>
    </form>
    <datalist id="player-options"></datalist>
    <div id="compare-out"><p class="muted">Loading players…</p></div>`;
  fillPlayers();
  $("#compare-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const a = $("#cmp-a").value, b = $("#cmp-b").value;
    const out = $("#compare-out");
    out.innerHTML = `<p class="muted">Comparing…</p>`;
    try {
      const data = await fetchJSON(`/api/compare?players=${encodeURIComponent(a + "," + b)}`);
      const bat = data.comparison.batting;
      out.innerHTML = `
        <p class="hint">${data.comparison.note}</p>
        <article class="card">
          <table>
            <thead><tr><th>Metric</th><th class="num-head">${esc(data.players[0])}</th><th class="num-head">${esc(data.players[1])}</th></tr></thead>
            <tbody>
            <tr class="group"><th colspan="3">Batting</th></tr>
            ${Object.keys(bat).map((k) => `<tr><td>${humanize(k)}</td><td class="num">${metric(bat[k][0])}</td><td class="num">${metric(bat[k][1])}</td></tr>`).join("")}
            <tr class="group"><th colspan="3">Bowling</th></tr>
            ${Object.keys(data.comparison.bowling).map((k) => `<tr><td>${humanize(k)}</td><td class="num">${metric(data.comparison.bowling[k][0])}</td><td class="num">${metric(data.comparison.bowling[k][1])}</td></tr>`).join("")}
            <tr class="group"><th colspan="3">Evidence</th></tr>
            <tr><td>Confidence</td><td class="num"><span class="conf ${esc(data.comparison.confidence[0])}">${esc(data.comparison.confidence[0])}</span></td><td class="num"><span class="conf ${esc(data.comparison.confidence[1])}">${esc(data.comparison.confidence[1])}</span></td></tr>
            </tbody>
          </table>
        </article>`;
    } catch (err) {
      out.innerHTML = `<p class="error">${esc(err.message)}</p>`;
    }
  });
  if (defA && defB) $("#compare-form").requestSubmit();
}

const REPLAY_OPTIONS = 200;

async function loadReplay() {
  const root = $("#tab-replay");
  if (replay.timer) stopPlay();
  const matches = await fetchJSON("/api/matches").catch(() => []);
  availableMatches = matches;
  root.innerHTML = `
    <div class="row">
      <div class="field"><label for="replay-filter">Find a match</label>
        <input id="replay-filter" type="search" placeholder="Team, venue or year">
      </div>
      <div class="field grow"><label for="replay-match">Match</label>
        <select id="replay-match"></select>
      </div>
      <button class="btn" id="replay-start">Start</button>
      <div class="segmented" role="group" aria-label="Playback">
        <button class="btn ghost" id="replay-prev" disabled aria-label="Previous ball">Prev</button>
        <button class="btn ghost" id="replay-play" disabled>Play</button>
        <button class="btn ghost" id="replay-pause" disabled>Pause</button>
        <button class="btn ghost" id="replay-next" disabled aria-label="Next ball">Next</button>
      </div>
    </div>
    <article id="replay-match-meta" class="replay-match-meta"></article>
    <div class="replay-layout">
      <article class="card">
        <div class="scoreboard">
          <div><span>Innings</span><strong id="sb-inn">—</strong></div>
          <div><span>Ball</span><strong id="sb-ball">—</strong></div>
          <div><span>Score</span><strong id="sb-score">0/0</strong></div>
          <div><span>Progress</span><strong id="sb-prog">—</strong></div>
        </div>
        <div class="replay-live" aria-live="polite">
          <div class="replay-live-title"><span id="sb-delivery">READY TO REPLAY</span><span id="sb-outcome">Select a delivery</span></div>
          <div class="replay-live-players">
            <div><span>Striker</span><strong id="sb-batter">—</strong></div>
            <div><span>Non-striker</span><strong id="sb-non-striker">—</strong></div>
            <div><span>Bowler</span><strong id="sb-bowler">—</strong></div>
          </div>
        </div>
        <div id="pitch-host" class="pitch-stage"></div>
      </article>
      <article class="card"><h3>Ball by ball</h3><ol id="replay-feed" class="replay-feed"><li class="feed-empty muted">Choose a match and press Start. Each delivery appears here, newest first, with extras and wickets labelled.</li></ol></article>
    </div>`;
  root.insertAdjacentHTML("beforeend", `<section id="replay-match-summary" class="replay-scorecard" hidden></section>`);
  // A <select> with all ~23k matches took over a second to build and could
  // not be scrolled; list the newest REPLAY_OPTIONS matches for the filter.
  const newestFirst = matches.slice().reverse();
  const fillReplayOptions = () => {
    const q = $("#replay-filter").value.trim().toLowerCase();
    const hits = (q
      ? newestFirst.filter((m) => `${m.team_home} ${m.team_away} ${m.venue || ""} ${m.match_date || ""} ${m.competition || ""}`.toLowerCase().includes(q))
      : newestFirst).slice(0, REPLAY_OPTIONS);
    $("#replay-match").innerHTML = hits.map((m) => {
      const year = m.match_date ? m.match_date.slice(0, 10) : m.season || "Date unavailable";
      return `<option value="${esc(m.match_id)}">${esc(year)} · ${esc(m.team_home)} vs ${esc(m.team_away)}</option>`;
    }).join("") || `<option value="" disabled>No matches found</option>`;
    updateReplayMatchMeta();
  };
  let replayFilterTimer = null;
  $("#replay-filter").addEventListener("input", () => { clearTimeout(replayFilterTimer); replayFilterTimer = setTimeout(fillReplayOptions, 120); });
  fillReplayOptions();
  updateReplayMatchMeta();
  replay.pitch = window.Pitch3D ? Pitch3D.createPitch($("#pitch-host")) : null;
  $("#pitch-host").insertAdjacentHTML("beforeend", `
    <div class="pitch-player-labels" aria-live="polite">
      <div class="pitch-player-label bowler-label"><span>Bowler</span><strong id="pitch-bowler">—</strong></div>
      <div class="pitch-player-label striker-label"><span>Striker</span><strong id="pitch-striker">—</strong></div>
      <div class="pitch-player-label non-striker-label"><span>Non-striker</span><strong id="pitch-non-striker">—</strong></div>
    </div>`);
  $("#replay-match").addEventListener("change", () => {
    stopPlay();
    replay.session = null;
    $("#replay-next").disabled = true;
    $("#replay-prev").disabled = true;
    $("#replay-play").disabled = true;
    updateReplayMatchMeta();
  });
  $("#replay-start").onclick = startReplay;
  $("#replay-next").onclick = () => nextBall(false);
  $("#replay-prev").onclick = prevBall;
  $("#replay-play").onclick = () => {
    if (replay.timer) return;
    replay.timer = setInterval(() => nextBall(true), 900);
    $("#replay-play").disabled = true;
    $("#replay-pause").disabled = false;
  };
  $("#replay-pause").onclick = stopPlay;
}

function updateReplayMatchMeta() {
  const match = availableMatches.find((item) => item.match_id === $("#replay-match")?.value);
  const meta = $("#replay-match-meta");
  if (!match || !meta) return;
  const year = match.match_date ? match.match_date.slice(0, 4) : match.season || "Year unavailable";
  meta.innerHTML = `<strong>${match.competition || "Cricket"} · ${match.match_type || "Match"} · ${year} · Match #${match.match_id}</strong><span>${match.team_home} vs ${match.team_away}</span><span>${match.venue || "Venue not recorded"}</span>`;
}

function stopPlay() {
  if (replay.timer) clearInterval(replay.timer);
  replay.timer = null;
  const play = $("#replay-play");
  const pause = $("#replay-pause");
  if (play) play.disabled = !replay.session;
  if (pause) pause.disabled = true;
}

async function startReplay() {
  stopPlay();
  replay.balls = [];
  replay.score = 0;
  replay.wickets = 0;
  replay.inningsIndex = null;
  replay.requestPending = false;
  $("#replay-feed").innerHTML = "";
  const summary = $("#replay-match-summary");
  if (summary) {
    summary.hidden = true;
    summary.innerHTML = "";
  }
  $("#sb-inn").textContent = "—";
  $("#sb-ball").textContent = "—";
  $("#sb-score").textContent = "0/0";
  $("#sb-batter").textContent = "—";
  $("#sb-non-striker").textContent = "—";
  $("#sb-bowler").textContent = "—";
  $("#sb-delivery").textContent = "READY TO REPLAY";
  $("#sb-outcome").textContent = "Select a delivery";
  try {
    replay.session = await fetchJSON("/api/replay/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ match_id: $("#replay-match").value }),
    });
    $("#replay-next").disabled = false;
    $("#replay-play").disabled = false;
    $("#replay-prev").disabled = true;
    $("#sb-prog").textContent = `0 / ${replay.session.total_deliveries}`;
    $("#sb-inn").textContent = "1";
    $("#sb-score").textContent = "0/0";
  } catch (err) {
    $("#replay-feed").innerHTML = `<li class="error">${err.message}</li>`;
  }
}

function renderBall(ball, atEnd) {
  const newInnings = replay.inningsIndex !== ball.innings_index;
  if (newInnings) {
    replay.inningsIndex = ball.innings_index;
    replay.score = 0;
    replay.wickets = 0;
  }
  replay.score += ball.runs_total || 0;
  replay.wickets += dismissalCount(ball.wickets);
  $("#sb-inn").textContent = ball.innings_index + 1;
  $("#sb-ball").textContent = ball.actual_delivery;
  $("#sb-score").textContent = `${replay.score}/${replay.wickets}`;
  $("#sb-prog").textContent = `${ball.position} / ${ball.total_deliveries}`;
  $("#sb-batter").textContent = ball.batter || "—";
  $("#sb-non-striker").textContent = ball.non_striker || "—";
  $("#sb-bowler").textContent = ball.bowler || "—";
  updatePitchPlayerLabels(ball);
  $("#sb-delivery").textContent = `INNINGS ${ball.innings_index + 1} · ${ball.actual_delivery}`;
  const tag = extraLabel(ball.extras);
  const wicket = (ball.wickets || []).length;
  $("#sb-outcome").textContent = wicket ? `WICKET · ${ball.wickets[0].kind}` : `${ball.runs_total} RUN${ball.runs_total === 1 ? "" : "S"}${tag ? ` · ${tag}` : ""}`;
  if (newInnings && ball.position > 1) {
    const divider = document.createElement("li");
    divider.className = "replay-innings-divider";
    divider.textContent = `Innings ${ball.innings_index + 1} · score reset`;
    $("#replay-feed").prepend(divider);
  }
  $("#replay-feed").prepend(createReplayFeedEntry(ball));
  if (replay.pitch) replay.pitch.playBall(ball);
  $("#replay-prev").disabled = ball.position <= 1;
  if (atEnd) {
    $("#replay-next").disabled = true;
    stopPlay();
    $("#replay-play").disabled = true;
    $("#replay-pause").disabled = true;
  }
}

async function nextBall(fromTimer) {
  if (!replay.session || replay.requestPending) return;
  replay.requestPending = true;
  try {
    const ball = await fetchJSON("/api/replay/next", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: replay.session.session_id }),
    });
    replay.balls.push(ball);
    const atEnd = ball.position >= ball.total_deliveries;
    renderBall(ball, atEnd);
    if (atEnd) {
      const summary = $("#replay-match-summary");
      if (summary) {
        summary.hidden = false;
        await showMatch(ball.match_id, summary);
        summary.scrollIntoView({ behavior: REDUCED_MOTION ? "auto" : "smooth", block: "start" });
      }
    }
  } catch (err) {
    stopPlay();
    $("#replay-next").disabled = true;
    if (!err.message.toLowerCase().includes("end")) {
      const li = document.createElement("li");
      li.className = "muted";
      li.textContent = err.message;
      $("#replay-feed").prepend(li);
    }
  } finally {
    replay.requestPending = false;
  }
}

function createReplayFeedEntry(ball, delayIndex = 0) {
  const tag = extraLabel(ball.extras);
  const wicket = (ball.wickets || []).length;
  const li = document.createElement("li");
  li.className = "replay-ball" + (wicket ? " wicket" : ball.runs_batter >= 4 ? " boundary" : "");
  if (delayIndex > 0) li.style.animationDelay = (delayIndex * 0.05) + "s";
  li.innerHTML = `<span class="over">${ball.actual_delivery}</span><span class="replay-ball-detail"><strong>${ball.batter || "Batter unavailable"}</strong><span>Bowler: ${ball.bowler || "Bowler unavailable"}</span><small>Non-striker: ${ball.non_striker || "—"}${tag ? ` · ${tag}` : ""}${wicket ? ` · ${ball.wickets[0].kind}` : ""}</small></span><span class="num">${ball.runs_total}</span>`;
  return li;
}

function updatePitchPlayerLabels(ball) {
  const labelValues = {
    "#pitch-bowler": ball.bowler || "—",
    "#pitch-striker": ball.batter || "—",
    "#pitch-non-striker": ball.non_striker || "—",
  };
  Object.entries(labelValues).forEach(([selector, value]) => {
    const label = $(selector);
    if (!label) return;
    label.textContent = value;
    const plate = label.closest(".pitch-player-label");
    plate.classList.remove("player-arrive");
    void plate.offsetWidth;
    plate.classList.add("player-arrive");
  });
}

async function prevBall() {
  if (!replay.session) return;
  try {
    const ball = await fetchJSON("/api/replay/previous", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: replay.session.session_id }),
    });
    replay.balls.pop();
    replay.inningsIndex = ball.innings_index;
    const currentInningsBalls = replay.balls.filter((b) => b.innings_index === ball.innings_index);
    replay.score = currentInningsBalls.reduce((total, b) => total + (b.runs_total || 0), 0);
    replay.wickets = currentInningsBalls.reduce((total, b) => total + dismissalCount(b.wickets), 0);
    $("#replay-feed").innerHTML = "";
    let previousInnings = null;
    replay.balls.forEach((b) => {
      if (previousInnings !== null && previousInnings !== b.innings_index) {
        const divider = document.createElement("li");
        divider.className = "replay-innings-divider";
        divider.textContent = `Innings ${b.innings_index + 1} · score reset`;
        $("#replay-feed").prepend(divider);
      }
      $("#replay-feed").prepend(createReplayFeedEntry(b));
      previousInnings = b.innings_index;
    });
    $("#sb-inn").textContent = ball.innings_index + 1;
    $("#sb-score").textContent = `${replay.score}/${replay.wickets}`;
    $("#sb-ball").textContent = ball.actual_delivery;
    $("#sb-prog").textContent = `${ball.position} / ${ball.total_deliveries}`;
    $("#sb-batter").textContent = ball.batter || "—";
    $("#sb-non-striker").textContent = ball.non_striker || "—";
    $("#sb-bowler").textContent = ball.bowler || "—";
    updatePitchPlayerLabels(ball);
    $("#sb-delivery").textContent = `INNINGS ${ball.innings_index + 1} · ${ball.actual_delivery}`;
    const wicket = (ball.wickets || []).length;
    $("#sb-outcome").textContent = wicket ? `WICKET · ${ball.wickets[0].kind}` : `${ball.runs_total} RUN${ball.runs_total === 1 ? "" : "S"}`;
    $("#replay-next").disabled = false;
    $("#replay-prev").disabled = ball.position <= 1;
    const summary = $("#replay-match-summary");
    if (summary) {
      summary.hidden = true;
      summary.innerHTML = "";
    }
    if (replay.pitch) replay.pitch.playBall(ball);
  } catch (err) {
    $("#replay-prev").disabled = true;
  }
}

// One line per side: where its squad came from, or why there is none.
function rosterNote(team, roster) {
  if (!roster) return `${esc(team)}: squad could not be loaded; enter players manually.`;
  return `${esc(team)}: ${esc(roster.available ? roster.squad_note || "" : roster.note || "No squad found; enter players manually.")}`;
}

async function loadSimulate(fixture) {
  const root = $("#tab-simulate");
  if (fixture) root.innerHTML = `<p class="muted">Loading squads for ${esc(fixture.team_a)} and ${esc(fixture.team_b)}…</p>`;
  const [players, dataset, historicalTeams] = await Promise.all([
    fetchJSON("/api/players").catch(() => []),
    currentDataset ? Promise.resolve(currentDataset) : fetchJSON("/api/dataset").catch(() => ({ formats: [] })),
    fetchJSON("/api/historical-teams").catch(() => [])
  ]);
  availablePlayers = players;
  const opts = players.map((p) => `<option value="${p}">${p}</option>`).join("");
  const teamA = fixture?.team_a || "Team A";
  const teamB = fixture?.team_b || "Team B";
  let rosterA = null;
  let rosterB = null;
  if (fixture) {
    const rosterUrl = (team) => `/api/team-roster?${new URLSearchParams({ team, format: fixture.format || "", opponent: team === teamA ? teamB : teamA, venue: fixture.venue || "" })}`;
    [rosterA, rosterB] = await Promise.all([
      fetchJSON(rosterUrl(teamA)).catch(() => null),
      fetchJSON(rosterUrl(teamB)).catch(() => null),
    ]);
  }
  const battingA = rosterA?.recommended_xi || [];
  const battingB = rosterB?.recommended_xi || [];
  const bowlingA = rosterA?.bowling_suggestions?.length >= 2 ? rosterA.bowling_suggestions : battingA.slice(-5);
  const bowlingB = rosterB?.bowling_suggestions?.length >= 2 ? rosterB.bowling_suggestions : battingB.slice(-5);
  const simulationFormat = /t20/i.test(fixture?.format || "") ? "T20" : "ODI";
  const defaultOvers = simulationFormat === "T20" ? 20 : 50;
  const formatHistoryNote = (format) => {
    const formats = dataset.formats || [];
    const hasHistory = formats.some((availableFormat) => availableFormat.toUpperCase() === format);
    return hasHistory
      ? `The active dataset includes ${format} delivery history.`
      : `No ${format} deliveries are present in ${dataset.dataset_name || "the active dataset"} (${formats.join(", ") || "no formats recorded"}).`;
  };


  const compOptions = Object.keys(historicalTeams).sort().map(c => `<option value="${c}">${c}</option>`).join("");

  root.innerHTML = `
    <article class="card sim-config">
      <div class="sim-config-head"><div><p class="eyebrow">Historical delivery model</p><h2>Set up a match</h2><p class="hint">Randomized ball-by-ball scenario, not a match prediction.</p></div><div class="sim-format-controls"><div class="field"><label for="sim-format">Format</label><select id="sim-format"><option value="ODI" ${simulationFormat === "ODI" ? "selected" : ""}>ODI</option><option value="T20" ${simulationFormat === "T20" ? "selected" : ""}>T20</option></select></div><div class="field overs-field"><label for="sim-overs">Overs per innings</label><input id="sim-overs" type="number" value="${defaultOvers}" min="1" max="50"></div></div></div>


      <div class="row">
        <div class="field inset-panel">
            <label>Optional: load teams from a live series</label>
            <div class="inline-row">
                <button type="button" class="btn ghost" id="sim-btn-fetch-live">Fetch live series</button>
                <select id="sim-upcoming-select" aria-label="Series"><option value="">Select a series…</option></select>
            </div>
            <div class="inline-row">
                <select id="sim-live-team-a" aria-label="Team A"><option value="">Team A</option></select>
                <select id="sim-live-team-b" aria-label="Team B"><option value="">Team B</option></select>
                <button type="button" class="btn ghost" id="sim-btn-load-live" disabled>Apply</button>
            </div>
        </div>
      </div>

      <div class="squad-importer row">
        <div class="field" style="flex:1; min-width: min(350px, 100%);"><label>Import Team A Squad</label>
            <div style="display:flex;gap:4px;flex-wrap:wrap;">
                <select id="sim-import-comp-a" aria-label="Competition"><option value="">Competition</option>${compOptions}</select>
                <select id="sim-import-year-a" aria-label="Season"><option value="">Year</option></select>
                <select id="sim-import-team-a" aria-label="Team"><option value="">Team</option></select>
                <button type="button" class="btn ghost" id="sim-btn-import-a">Import</button>
            </div>
        </div>
        <div class="field" style="flex:1; min-width: min(350px, 100%);"><label>Import Team B Squad</label>
            <div style="display:flex;gap:4px;flex-wrap:wrap;">
                <select id="sim-import-comp-b" aria-label="Competition"><option value="">Competition</option>${compOptions}</select>
                <select id="sim-import-year-b" aria-label="Season"><option value="">Year</option></select>
                <select id="sim-import-team-b" aria-label="Team"><option value="">Team</option></select>
                <button type="button" class="btn ghost" id="sim-btn-import-b">Import</button>
            </div>
        </div>
      </div>

      <p id="sim-history-note" class="sim-history-note">${formatHistoryNote(simulationFormat)}</p>
      <form id="sim-match-form">
        <datalist id="sim-player-options">${opts}</datalist>
        <div class="sim-team-names">
          <label class="lineup-field"><span>Team A</span><input id="sim-team-a" value="${teamA}" required></label>
          <label class="lineup-field"><span>Team B</span><input id="sim-team-b" value="${teamB}" required></label>
        </div>
        <div class="lineup-grid">
          <label class="lineup-field"><span>${teamA} · Batting XI</span><textarea id="sim-bat-a" placeholder="Player one, Player two, …" required>${battingA.join(", ")}</textarea></label>
          <label class="lineup-field"><span>${teamA} · Bowling options</span><textarea id="sim-bowl-a" placeholder="At least two bowlers" required>${bowlingA.join(", ")}</textarea></label>
          <label class="lineup-field"><span>${teamB} · Batting XI</span><textarea id="sim-bat-b" placeholder="Player one, Player two, …" required>${battingB.join(", ")}</textarea></label>
          <label class="lineup-field"><span>${teamB} · Bowling options</span><textarea id="sim-bowl-b" placeholder="At least two bowlers" required>${bowlingB.join(", ")}</textarea></label>
        </div>
        <p id="sim-roster-note" class="hint"${fixture ? "" : " hidden"}>${fixture ? `${rosterNote(teamA, rosterA)}<br>${rosterNote(teamB, rosterB)}` : ""}</p>
        <div class="player-picker"><label for="sim-player-search">Find a local player</label><input id="sim-player-search" list="sim-player-options" placeholder="Type a name"><div class="player-picker-actions">${[["sim-bat-a", "Add to A batting"], ["sim-bowl-a", "Add to A bowling"], ["sim-bat-b", "Add to B batting"], ["sim-bowl-b", "Add to B bowling"]].map(([target, label]) => `<button class="btn ghost" type="button" data-player-target="${target}">${label}</button>`).join("")}</div></div>
        <div class="sim-actions"><label class="field seed-field"><span>Seed (optional)</span><input id="sim-seed" type="number" placeholder="Random"></label><button class="btn" type="submit">Simulate Match</button></div>
      </form>
    </article>
    <div id="sim-assumptions"></div>
    <div id="sim-match-result"></div>
  `;

  let currentSimSeries = [];
  const btnFetchLive = $("#sim-btn-fetch-live");
  const selectLiveSeries = $("#sim-upcoming-select");
  const selectLiveTeamA = $("#sim-live-team-a");
  const selectLiveTeamB = $("#sim-live-team-b");
  const btnLoadLive = $("#sim-btn-load-live");

  if(btnFetchLive) {
      btnFetchLive.addEventListener("click", async () => {
          btnFetchLive.textContent = "Fetching...";
          try {
              const res = await fetchJSON("/api/upcoming-tournaments");
              if(!res.error) {
                  currentSimSeries = res;
                  selectLiveSeries.innerHTML = `<option value="">Select a series…</option>` + res.map(u => `<option value="${esc(u.id)}">${esc(u.name)}</option>`).join("");
              }
          } catch(e) {}
          btnFetchLive.textContent = "Fetch live series";
      });
  }

  if(selectLiveSeries) {
      selectLiveSeries.addEventListener("change", async () => {
          const sel = selectLiveSeries.value;
          const t = currentSimSeries.find(x => x.id === sel);
          if(!t) return;

          let teams = [];
          if(t.teams && t.teams.length > 0) {
              teams = t.teams;
          } else {
              selectLiveSeries.disabled = true;
              try {
                  const infoRes = await fetchJSON(`/api/live-tournament-info?id=${encodeURIComponent(t.id)}`);
                  if(infoRes.teams && infoRes.teams.length > 0) {
                      teams = infoRes.teams;
                  }
              } catch(e) {}
              selectLiveSeries.disabled = false;
          }

          if(teams.length > 0) {
              const opts = teams.map(x => `<option value="${x}">${x}</option>`).join("");
              selectLiveTeamA.innerHTML = `<option value="">Team A</option>` + opts;
              selectLiveTeamB.innerHTML = `<option value="">Team B</option>` + opts;
              btnLoadLive.disabled = false;
          } else {
              alert("No teams found for this series.");
          }
      });
  }

  if(btnLoadLive) {
      btnLoadLive.addEventListener("click", async () => {
          const ta = selectLiveTeamA.value;
          const tb = selectLiveTeamB.value;
          if(ta && tb) {
              $("#sim-team-a").value = ta;
              $("#sim-team-b").value = tb;

              const rosterUrl = (team) => `/api/team-roster?${new URLSearchParams({ team, format: $("#sim-format").value, opponent: team === ta ? tb : ta, venue: "" })}`;

              btnLoadLive.textContent = "Importing...";
              try {
                  const [rosterA, rosterB] = await Promise.all([
                      fetchJSON(rosterUrl(ta)).catch(() => null),
                      fetchJSON(rosterUrl(tb)).catch(() => null),
                  ]);

                  if(rosterA && rosterA.recommended_xi) {
                      $("#sim-bat-a").value = rosterA.recommended_xi.join(", ");
                      const bowlA = rosterA.bowling_suggestions?.length >= 2 ? rosterA.bowling_suggestions : rosterA.recommended_xi.slice(-5);
                      $("#sim-bowl-a").value = bowlA.join(", ");
                  }
                  if(rosterB && rosterB.recommended_xi) {
                      $("#sim-bat-b").value = rosterB.recommended_xi.join(", ");
                      const bowlB = rosterB.bowling_suggestions?.length >= 2 ? rosterB.bowling_suggestions : rosterB.recommended_xi.slice(-5);
                      $("#sim-bowl-b").value = bowlB.join(", ");
                  }
                  const note = $("#sim-roster-note");
                  note.innerHTML = `${rosterNote(ta, rosterA)}<br>${rosterNote(tb, rosterB)}`;
                  note.hidden = false;
              } catch(e) {}
              btnLoadLive.textContent = "Apply";
          }
      });
  }

  const setupImporter = (compSelect, yearSelect, teamSelect, btn, batArea, bowlArea, nameInput) => {
      compSelect.addEventListener("change", () => {
          const comp = historicalTeams[compSelect.value];
          yearSelect.innerHTML = `<option value="">Year</option>` + (comp ? Object.keys(comp).sort((a,b)=>b.localeCompare(a)).map(y => `<option value="${y}">${y}</option>`).join("") : "");
          teamSelect.innerHTML = `<option value="">Team</option>`;
      });
      yearSelect.addEventListener("change", () => {
          const comp = historicalTeams[compSelect.value];
          const teams = comp ? comp[yearSelect.value] : null;
          teamSelect.innerHTML = `<option value="">Team</option>` + (teams ? teams.sort().map(t => `<option value="${t}">${t}</option>`).join("") : "");
      });
      btn.addEventListener("click", async () => {
          if(!teamSelect.value) return;
          const url = `/api/team-roster?team=${encodeURIComponent(teamSelect.value)}&year=${yearSelect.value}`;
          btn.textContent = "Loading...";
          try {
              const res = await fetchJSON(url);
              if(res.available && res.recommended_xi.length > 0) {
                  batArea.value = res.recommended_xi.join(", ");
                  bowlArea.value = res.bowling_suggestions.join(", ");
                  nameInput.value = teamSelect.value + (yearSelect.value ? " " + yearSelect.value : "");
                  nameInput.dispatchEvent(new Event('input'));
              } else {
                  alert("Could not find enough players for this squad.");
              }
          } catch(e) {
              alert("Error importing squad.");
          }
          btn.textContent = "Import";
      });
  };

  setupImporter($("#sim-import-comp-a"), $("#sim-import-year-a"), $("#sim-import-team-a"), $("#sim-btn-import-a"), $("#sim-bat-a"), $("#sim-bowl-a"), $("#sim-team-a"));
  setupImporter($("#sim-import-comp-b"), $("#sim-import-year-b"), $("#sim-import-team-b"), $("#sim-btn-import-b"), $("#sim-bat-b"), $("#sim-bowl-b"), $("#sim-team-b"));


  const updateLabels = () => {
      const a = $("#sim-team-a").value || "Team A";
      const b = $("#sim-team-b").value || "Team B";
      const spans = root.querySelectorAll(".lineup-field span");
      if(spans.length >= 6) {
          spans[2].textContent = `${a} · Batting XI`;
          spans[3].textContent = `${a} · Bowling options`;
          spans[4].textContent = `${b} · Batting XI`;
          spans[5].textContent = `${b} · Bowling options`;
      }
      const btn = root.querySelector("button[type='submit']");
      if(btn) btn.textContent = `Simulate ${a} vs ${b}`;
  };
  $("#sim-team-a").addEventListener("input", updateLabels);
  $("#sim-team-b").addEventListener("input", updateLabels);

  $("#sim-match-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const out = $("#sim-match-result");
    const parseLineup = (selector) => $(selector).value.split(",").map((player) => player.trim()).filter(Boolean);
    const simulationPayload = {
      team_a: $("#sim-team-a").value.trim(),
      team_b: $("#sim-team-b").value.trim(),
      batting_lineup_a: parseLineup("#sim-bat-a"),
      bowling_lineup_a: parseLineup("#sim-bowl-a"),
      batting_lineup_b: parseLineup("#sim-bat-b"),
      bowling_lineup_b: parseLineup("#sim-bowl-b"),
      overs: Number($("#sim-overs").value),
      match_type: $("#sim-format").value,
      seed: $("#sim-seed").value || null,
    };
    try {
      out.innerHTML = `<p class="muted">Simulating deliveries…</p>`;
      const sim = await fetchJSON("/api/simulate-match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(simulationPayload),
      });
      if (!sim.available) {
        out.innerHTML = `<article class="card error">${sim.note}</article>`;
        return;
      }
      sim.match_id = `SIM-${sim.seed}`;
      sim.season = "Simulation";
      sim.competition = fixture?.competition || "Historical simulation";
      sim.match_type = `${simulationPayload.overs}-over limited overs`;
      sim.venue = fixture?.venue || "Conditions not modeled";
      sim.simulated = true;
      $("#sim-assumptions").innerHTML = `<article class="card sim-assumptions"><h3>${sim.label} · Seed ${sim.seed}</h3><ul>${sim.assumptions.map((assumption) => `<li>${assumption}</li>`).join("")}</ul></article>`;
      renderMatchScorecard(sim, sim.deliveries, out);
    } catch (err) {
      out.innerHTML = `<p class="error">${err.message}</p>`;
    }
  });
  root.querySelectorAll("button[data-player-target]").forEach((button) => {
    button.addEventListener("click", () => {
      const playerInput = $("#sim-player-search");
      const target = $(`#${button.dataset.playerTarget}`);
      const player = playerInput.value.trim();
      if (!player) return;
      const names = target.value.split(",").map((name) => name.trim()).filter(Boolean);
      if (!names.includes(player)) names.push(player);
      target.value = names.join(", ");
      playerInput.value = "";
    });
  });
  $("#sim-format").addEventListener("change", () => {
    const format = $("#sim-format").value;
    $("#sim-overs").value = format === "T20" ? 20 : 50;
    $("#sim-history-note").textContent = formatHistoryNote(format);
  });
}

async function loadAgents() {
  const root = $("#tab-agents");
  const leaders = await fetchJSON("/api/overview").then((d) => d.top_performers.by_runs.map((p) => p.player)).catch(() => []);
  root.innerHTML = `
    <form id="agent-form" class="row">
      <div class="field grow"><label for="agent-players">Shortlist (comma-separated)</label>
        <input id="agent-players" value="${esc(leaders.slice(0, 3).join(", "))}" placeholder="e.g. V Kohli, RG Sharma"></div>
      <div class="field grow"><label for="agent-q">Selection question</label>
        <input id="agent-q" value="${esc(DEFAULT_QUESTION)}"></div>
      <button class="btn">Start analysis</button>
    </form>
    <p class="hint">Each specialist reviews the shortlist independently; the coordinator then writes the selection report.</p>
    <div class="grid three" id="agent-status">
      ${["Batting Analyst","Bowling Analyst","Fielding Analyst"].map((n) => `<article class="card agent-card"><h3>${n}</h3><div class="status" data-state="idle">Not started</div><p class="muted">Runs when you start an analysis.</p></article>`).join("")}
    </div>
    <article class="card agent-card" style="margin-top:20px"><h3>Coordinator</h3><p id="coord-status" class="muted">Waiting for the specialists' reports.</p></article>
    <div id="agent-out"></div>
  `;
  $("#agent-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const names = $("#agent-players").value.split(",").map((s) => s.trim()).filter(Boolean);
    if (!names.length) { $("#coord-status").textContent = "Add at least one player to the shortlist."; return; }
    const submit = $("#agent-form button");
    submit.disabled = true; submit.textContent = "Analysing…";
    document.querySelectorAll("#agent-status .status").forEach((el) => { el.textContent = "Running"; el.dataset.state = "running"; });
    document.querySelectorAll("#agent-status .agent-card p").forEach((el) => { el.textContent = `Reviewing ${names.length} player${names.length === 1 ? "" : "s"}…`; });
    $("#coord-status").textContent = "Waiting for the specialists, then writing the explanation…";
    try {
      const result = await fetchJSON("/api/analysis/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ players: names, question: $("#agent-q").value }),
      });
      lastReport = result.report;
      const runs = result.session.agent_runs || [];
      const by = {};
      runs.forEach((r) => { by[r.agent] = r; });
      const cards = document.querySelectorAll("#agent-status .agent-card");
      ["Batting Analyst","Bowling Analyst","Fielding Analyst"].forEach((name, i) => {
        const st = by[name];
        const status = cards[i].querySelector(".status");
        status.textContent = humanize(String(st ? st.status : "completed").toLowerCase());
        status.dataset.state = "done";
        cards[i].querySelector("p").textContent = "Finished; evidence stored with the session.";
      });
      const story = result.report.narrative;
      $("#coord-status").textContent = result.report.selection_summary
        + (story ? (story.source === "llm" ? ` Explanation written by ${story.model}.` : " Rule-based explanation (no LLM).") : "");
      $("#agent-out").innerHTML = renderReport(result.report);
    } catch (err) {
      document.querySelectorAll("#agent-status .status").forEach((el) => { el.textContent = "Failed"; el.dataset.state = "error"; });
      $("#coord-status").textContent = err.message;
    } finally {
      submit.disabled = false; submit.textContent = "Start analysis";
    }
  });
}

/* The coordinator's verdict, explained by Claude (or by rules when no model is available). */
function narrativeCard(n) {
  if (!n) return "";
  const byLlm = n.source === "llm";
  const paragraphs = esc(n.text).split(/\n+/).filter(Boolean).map((line) => `<p>${line}</p>`).join("");
  return `
    <article class="card narrative ${byLlm ? "is-llm" : "is-rules"}" style="margin-bottom:20px">
      <div class="narrative-head">
        <h2>Coordinator's explanation</h2>
        <span class="source-chip">${byLlm ? `Claude · ${esc(n.model)}` : "Rule-based"}</span>
      </div>
      <div class="narrative-body">${paragraphs}</div>
      <p class="hint">${esc(n.note)}</p>
    </article>`;
}

function renderReport(report) {
  if (!report) return `
    <article class="card empty-state">
      <h2>No report yet</h2>
      <p>A selection report is written by the coordinator after the batting, bowling and fielding analysts review a shortlist. Run an analysis and the report appears here.</p>
      <div class="actions"><button class="btn" type="button" onclick="showTab('agents')">Start an agent analysis</button></div>
    </article>`;
  return `
    <div class="page-actions"><h2>Coach's Selection Report</h2><button class="btn ghost" type="button" onclick="window.print()">Export to PDF</button></div>
    ${narrativeCard(report.narrative)}
    <article class="card">
      <h2>Selection question</h2>
      <p>${report.question}</p>
      <p class="muted">Opponent: ${report.context.opponent || "n/a"} · Range: ${report.context.last_n_matches || "all ingested matches"}</p>
      <h3>Recommendations</h3>
      ${report.recommendations.map((r) => `
        <p><strong>${r.player}</strong> · ${r.role || "Unclassified"} · <span class="conf ${r.confidence}">${r.confidence}</span><br>
        <span class="muted">${(r.notes || []).join(" ")}</span></p>`).join("")}
      <h3>Risks / flags</h3>
      <ul>${(report.risks_flags || []).map((f) => `<li>${f}</li>`).join("") || "<li class='muted'>None</li>"}</ul>
      <h3>Conflicts resolved</h3>
      <ul>${(report.conflicts_resolved || []).map((f) => `<li>${f}</li>`).join("") || "<li class='muted'>None</li>"}</ul>
      <p class="hint">${report.note}</p>
    </article>`;
}

function loadReport() {
  $("#tab-report").innerHTML = renderReport(lastReport);
}

async function loadPredictor() {
  const root = $("#tab-predictor");
  const [players, venues, dataset] = await Promise.all([
    fetchJSON("/api/players?details=1").catch(() => []),
    fetchJSON("/api/venues").catch(() => []),
    fetchJSON("/api/dataset").catch(() => ({ teams: [] })),
  ]);
  const teams = dataset.teams && dataset.teams.length
    ? dataset.teams
    : [...new Set(players.map((p) => p.team).filter(Boolean))];
  const rosterByTeam = {};
  players.forEach((p) => {
    if (!p.team) return;
    (rosterByTeam[p.team] = rosterByTeam[p.team] || []).push(p.player_name);
  });

  root.innerHTML = `
    <p class="hint">Ranks a named squad using recent form, ground history at the selected venue, and
      head-to-head record against the opponent's known roster. It is not a win-probability model, and it
      cannot factor pace-vs-spin or a live weather forecast — Cricsheet records neither.</p>
    <form id="predictor-form" class="row">
      <div class="field" style="min-width:220px"><label>My team</label>
        <select id="pred-team">${teams.map((t) => `<option value="${t}">${t}</option>`).join("")}</select></div>
      <div class="field" style="min-width:220px"><label>Opponent</label>
        <select id="pred-opponent"><option value="">(none)</option>${teams.map((t) => `<option value="${t}">${t}</option>`).join("")}</select></div>
      <div class="field" style="min-width:220px"><label>Venue / ground</label>
        <select id="pred-venue"><option value="">(none)</option>${venues.map((v) => `<option value="${v.venue}">${v.venue}${v.city ? " — " + v.city : ""}</option>`).join("")}</select></div>
      <div class="field" style="min-width:90px"><label>XI size</label>
        <input id="pred-xi" type="number" min="1" value="11"></div>
      <div class="field" style="min-width:320px"><label>Squad (comma-separated, editable)</label>
        <input id="pred-squad" value=""><small id="pred-squad-note" class="muted"></small></div>
      <div class="field" style="min-width:220px"><label>Pitch note (optional, manual)</label>
        <input id="pred-pitch" placeholder="e.g. dry, expected to turn"></div>
      <div class="field" style="min-width:220px"><label>Weather note (optional, manual)</label>
        <input id="pred-weather" placeholder="e.g. overcast, heavy dew expected"></div>
      <button class="btn">Recommend XI</button>
    </form>
    <div id="predictor-out"></div>
  `;

  // Prefill with the team's latest season (and its men's or women's side, since
  // IPL and WPL teams share names); fall back to everyone who played for it.
  let squadRequest = 0;
  const fillSquadFromTeam = async () => {
    const team = $("#pred-team").value;
    const request = ++squadRequest;
    $("#pred-squad").value = (rosterByTeam[team] || []).join(", ");
    try {
      const res = await fetchJSON(`/api/team-squad?team=${encodeURIComponent(team)}`);
      if (request === squadRequest && res.squad && res.squad.length) {
        $("#pred-squad").value = res.squad.join(", ");
        $("#pred-squad-note").textContent = `Squad from ${team}'s ${res.season} season (${res.squad.length} players). Edit freely.`;
      }
    } catch (_) { /* keep the all-time list */ }
  };
  $("#pred-team").addEventListener("change", fillSquadFromTeam);
  if (teams.length) fillSquadFromTeam();

  $("#predictor-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const squad = $("#pred-squad").value.split(",").map((s) => s.trim()).filter(Boolean);
    const out = $("#predictor-out");
    out.innerHTML = `<p class="muted">Recommending…</p>`;
    try {
      const result = await fetchJSON("/api/recommend_lineup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          squad,
          opponent: $("#pred-opponent").value || null,
          venue: $("#pred-venue").value || null,
          pitch_note: $("#pred-pitch").value || null,
          weather_note: $("#pred-weather").value || null,
          xi_size: Number($("#pred-xi").value) || 11,
        }),
      });
      out.innerHTML = renderPredictor(result);
    } catch (err) {
      out.innerHTML = `<p class="error">${err.message}</p>`;
    }
  });
}

function renderPredictor(result) {
  const vp = result.venue_profile;
  const cond = result.conditions;
  const playerCard = (c, benched) => `
    <article class="card" style="margin-bottom:10px">
      <h3>${c.player} <span class="conf ${c.confidence}">${c.confidence}</span> ${benched ? '<span class="muted">(bench)</span>' : ""}</h3>
      <p class="muted">${c.team || "Team unknown"} · ${c.role || "Unclassified role"}</p>
      <ul>${c.evidence.map((e) => `<li>${e}</li>`).join("")}</ul>
    </article>`;
  return `
    <article class="card">
      <h2>Context</h2>
      <p class="muted">Opponent: ${result.opponent || "n/a"} · Venue: ${result.venue || "n/a"}</p>
      ${vp ? `<p>${vp.available ? `<strong>${vp.characterisation || ""}</strong> — ${vp.note}` : vp.note}</p>` : ""}
      <p class="hint">${cond.available ? `Conditions (${cond.source}): ${[cond.pitch_note, cond.weather_note].filter(Boolean).join(" · ") || "no notes given"}` : cond.note}</p>
    </article>
    <h3>Recommended XI (${result.recommended_xi.length})</h3>
    ${result.recommended_xi.map((c) => playerCard(c, false)).join("") || `<p class="muted">No players evaluated.</p>`}
    ${result.bench.length ? `<h3>Bench</h3>${result.bench.map((c) => playerCard(c, true)).join("")}` : ""}
    <article class="card">
      <h3>Team balance</h3>
      <ul>${(result.team_balance.observations || []).map((o) => `<li>${o}</li>`).join("")}</ul>
    </article>
    <article class="card">
      <h3>Flags</h3>
      <ul>${(result.flags || []).map((f) => `<li>${f}</li>`).join("") || "<li class='muted'>None</li>"}</ul>
      <p class="hint">${result.ranking_method}</p>
      <p class="hint"><strong>${result.prediction_type}:</strong> ${result.disclaimer}</p>
    </article>
  `;
}

async function loadArchitecture() {
  let data;
  try {
    data = await fetchJSON("/api/architecture");
  } catch (err) {
    $("#tab-architecture").innerHTML = `<p class="error">${esc(err.message)}</p>`;
    return;
  }
  $("#tab-architecture").innerHTML = `
    <div class="page-actions"><button class="btn ghost" type="button" onclick="window.print()">Export to PDF</button></div>
    <article class="card">
      <h2>System pipeline</h2>
      <div class="pipeline">${data.pipeline.map((s, i) => `${i ? '<span class="pipe-arrow">→</span>' : ""}<span class="pipe">${s}</span>`).join("")}</div>
    </article>
    <article class="card">
      <h2>MCP tools</h2>
      <p class="hint">${data.honesty}</p>
      <table><tr><th>Tool</th><th>Purpose</th><th>Status</th></tr>
      ${data.mcp_tools.map((t) => `<tr><td><code>${t.tool}</code></td><td>${t.purpose}</td><td>${t.status}</td></tr>`).join("")}</table>
    </article>
    <article class="card">
      <h2>Recent calls</h2>
      <table>${data.recent_calls.map((c) => `<tr><td>${c.tool}</td><td>${c.channel}</td><td class="muted">${c.at}</td></tr>`).join("") || "<tr><td class='muted'>None yet</td></tr>"}</table>
    </article>`;
}


async function loadTournament() {
  const root = $("#tab-tournament");
  root.innerHTML = `
    <article class="card sim-config">
      <div class="sim-config-head">
        <div><p class="eyebrow">Tournament Mode</p><h2>Simulate Tournament</h2><p class="hint">Run a full round-robin tournament and generate a points table.</p></div>
      </div>

      <div class="row">
        <div class="field inset-panel">
            <label>Optional: load teams from a tournament schedule</label>
            <div class="inline-row">
                <button type="button" class="btn ghost" id="tourn-btn-fetch">Fetch series</button>
                <select id="tourn-upcoming-select" aria-label="Series"><option value="">Select a series…</option></select>
                <button type="button" class="btn ghost" id="tourn-btn-load" disabled>Load teams</button>
            </div>
            <p class="hint" style="margin:8px 0 0">Series come from CricketData.org using the server's CRICKETDATA_API_KEY. If it is not set, you get two sample tournaments (IPL and T20 World Cup).</p>
        </div>
      </div>

      <div class="field" style="margin-bottom:10px;"><label>Format</label><select id="tourn-format"><option value="T20">T20 (Round Robin)</option><option value="ODI">ODI (Round Robin)</option></select></div>

      <div class="field" style="margin-bottom:10px;">
        <label>Selected Teams (minimum 2)</label>
        <textarea id="tourn-teams-area" placeholder="E.g. Chennai Super Kings, Mumbai Indians, Royal Challengers Bangalore" style="min-height: 80px;" required></textarea>
        <p class="hint" style="margin-top:8px">Separate team names with commas. Teams must exist in the database.</p>
      </div>

      <div class="sim-actions">
        <button class="btn" type="button" id="tourn-btn-sim">Simulate Full Tournament</button>
      </div>
    </article>
    <div id="tourn-results"></div>
  `;

  let currentSeriesList = [];

  const btnFetch = $("#tourn-btn-fetch");
  const selectUpcoming = $("#tourn-upcoming-select");
  const btnLoad = $("#tourn-btn-load");

  if(btnFetch) {
      btnFetch.addEventListener("click", async () => {
          btnFetch.textContent = "Fetching...";
          try {
              const res = await fetchJSON(`/api/upcoming-tournaments`);
              if(res.error) {
                  alert("API Error: " + res.error);
              } else {
                  currentSeriesList = res;
                  selectUpcoming.innerHTML = `<option value="">Select a series…</option>` + res.map(u => `<option value="${esc(u.id)}">${esc(u.name)}</option>`).join("");
                  btnLoad.disabled = false;
              }
          } catch(e) {
              alert("Could not fetch series: " + e.message);
          }
          btnFetch.textContent = "Fetch series";
      });
  }

  if(btnLoad) {
      btnLoad.addEventListener("click", async () => {
          const sel = selectUpcoming.value;
          if(!sel) return;
          const t = currentSeriesList.find(x => x.id === sel);
          if(!t) return;

          if(t.teams && t.teams.length > 0) {
              $("#tourn-format").value = t.format;
              $("#tourn-teams-area").value = t.teams.join(", ");
          } else {
              // Fetch team list dynamically from series_info
              btnLoad.textContent = "Loading...";
              try {
                  const infoRes = await fetchJSON(`/api/live-tournament-info?id=${encodeURIComponent(t.id)}`);
                  if(infoRes.error) {
                      alert("API Error: " + infoRes.error);
                  } else if (infoRes.teams && infoRes.teams.length > 0) {
                      $("#tourn-format").value = t.format;
                      $("#tourn-teams-area").value = infoRes.teams.join(", ");
                  } else {
                      alert("This series has no published fixtures yet, so its teams are not known. Pick another series or type the teams.");
                  }
              } catch(e) {
                  alert("Failed to fetch series info.");
              }
              btnLoad.textContent = "Load teams";
          }
      });
  }

  const btnSim = $("#tourn-btn-sim");
  if(btnSim) {
      btnSim.addEventListener("click", async () => {
          const teamsStr = $("#tourn-teams-area").value;
          const teams = teamsStr.split(",").map(x => x.trim()).filter(x => x.length > 0);
          if(teams.length < 2) {
              alert("Enter at least 2 teams.");
              return;
          }

          btnSim.textContent = "Simulating... (This may take a minute)";
          btnSim.disabled = true;
          const resultsDiv = $("#tourn-results");
          resultsDiv.innerHTML = `<p class="muted">Running every fixture…</p>`;

          try {
              const res = await fetchJSON("/api/simulate-tournament", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({
                      teams: teams,
                      year: "2024",
                      format: $("#tourn-format").value
                  })
              });

              if(res.error) {
                  resultsDiv.innerHTML = `<p class="error">${esc(res.error)}</p>`;
              } else if (!res.standings.length) {
                  resultsDiv.innerHTML = `<p class="error">None of these teams has 11 players in the database, so no matches could be played. Check the spelling against the Matches page.</p>`;
              } else {
                  const skippedNote = (res.skipped_teams || []).length
                      ? `<p class="hint">Left out (fewer than 11 players in the database): ${res.skipped_teams.map(esc).join(", ")}.</p>` : "";
                  let html = skippedNote + `<article class="card"><h2>Points table</h2><table><thead><tr><th>Team</th><th class="num-head">P</th><th class="num-head">W</th><th class="num-head">L</th><th class="num-head">Pts</th><th class="num-head">NRR</th></tr></thead><tbody>`;
                  for (const s of res.standings) {
                      html += `<tr><td><strong>${esc(s.team)}</strong></td><td class="num">${s.played}</td><td class="num">${s.won}</td><td class="num">${s.lost}</td><td class="num"><strong>${s.points}</strong></td><td class="num">${Number(s.nrr).toFixed(3)}</td></tr>`;
                  }
                  html += `</tbody></table></article>`;
                  html += `<article class="card" style="margin-top:20px;"><h2>Match results</h2><ul class="result-list">`;
                  for (const m of res.matches) {
                      html += `<li><span class="team">${esc(m.team_a)}</span><span class="score">${esc(m.score_a)}</span><span class="vs">vs</span><span class="score">${esc(m.score_b)}</span><span class="team">${esc(m.team_b)}</span><span class="winner">Winner: ${esc(m.winner)}</span></li>`;
                  }
                  html += `</ul></article>`;

                  resultsDiv.innerHTML = html;
              }
          } catch(e) {
              resultsDiv.innerHTML = `<p class="error">${esc(e.message)}. Check that the team names exist in the database.</p>`;
          }
          btnSim.textContent = "Simulate Full Tournament";
          btnSim.disabled = false;
      });
  }
}


showTab("overview");
