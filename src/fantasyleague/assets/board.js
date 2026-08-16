/* Draft board runtime. DATA is injected by render.py as a JSON literal. */
(function () {
  "use strict";

  var TIER_BREAK = DATA.tier_break;   // single source of truth: board.TIER_BREAK_THRESHOLD
  // CSV quoting constants: a field needs quotes if it holds a comma, quote or newline.
  var QUOTE = String.fromCharCode(34);
  var NEWLINE = String.fromCharCode(10);
  var NEEDS_QUOTE = new RegExp("[," + QUOTE + NEWLINE + "]");
  var DQ = new RegExp(QUOTE, "g");
  var FLAG_LABEL = { value: "Value", avoid: "Reach", watch: "Watch" };
  // Keyed by board identity (season + ordered roster + league), not just season:
  // on file:// some browsers give every local page one storage origin, so two
  // league boards would otherwise share crossed-off state.
  var KEY = "ff-warroom-" + DATA.board_id;

  var storageBroken = false;
  function storageFailed() {
    if (storageBroken) return;
    storageBroken = true;
    var n = document.getElementById("storenote");
    n.textContent = "This browser isn't saving your picks — they'll reset if you refresh.";
    n.hidden = false;
  }

  // State is an ordered pick log: [{rank, ts, mine}] in the order players were
  // crossed off. An entry with rank === null is an off-board pick — someone this
  // board doesn't rank was taken, which still burns an overall selection, so it
  // is counted but nobody is crossed off. `gone` is a derived index for O(1)
  // lookups. Storage schema v2; v1 was a bare array of rank strings, migrated in
  // load order (Set insertion order).
  var log = [];
  var gone = new Set();

  function rebuildIndex() {
    gone = new Set();
    log.forEach(function (e) { if (e.rank != null) gone.add(String(e.rank)); });
  }

  (function load() {
    var saved = null;
    try { saved = localStorage.getItem(KEY); } catch (e) { storageFailed(); return; }
    if (!saved) return;
    try {
      var parsed = JSON.parse(saved);
      if (Array.isArray(parsed)) {                                     // v1
        log = parsed.map(function (r) { return { rank: Number(r), ts: null }; });
      } else if (parsed && parsed.v === 2 && Array.isArray(parsed.log)) {
        log = parsed.log.filter(function (e) { return e && (typeof e.rank === "number" || e.rank === null); })
          .map(function (e) {
            return { rank: e.rank == null ? null : e.rank, name: e.name || "", ts: e.ts || null, mine: !!e.mine };
          });
      }
    } catch (e) { log = []; }
    rebuildIndex();
  })();

  function save() {
    try { localStorage.setItem(KEY, JSON.stringify({ v: 2, log: log })); } catch (e) { storageFailed(); }
  }

  // ---- live mode: a served page follows the server's log over SSE and echoes
  // local actions back with fetch(), so every open tab shows the same board.
  var LIVE = !!DATA.live;
  // Writes that never reached the server. A phone that drops Wi-Fi for a moment
  // keeps accepting taps; swallowing the failures meant the next state replay
  // quietly deleted those picks. Queue them, say so, and replay on reconnect.
  var unsynced = [];
  var setPill = function () {};

  function remote(body) {
    if (!LIVE) return null;
    try {
      return fetch("/state", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Source": "board" },
        body: JSON.stringify(body)
      }).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r;
      }).catch(function (err) { queueUnsynced(body); throw err; });
    } catch (e) {
      queueUnsynced(body);
      return null;
    }
  }

  function queueUnsynced(body) {
    unsynced.push(body);
    setPill(false);
    toast(unsynced.length === 1
      ? "Not synced — the server didn't get that. Retrying."
      : unsynced.length + " changes not synced. Retrying.");
    say("That change has not reached the server yet.");
  }

  function flushUnsynced() {
    if (!unsynced.length) return;
    var queued = unsynced.slice();
    unsynced = [];
    queued.forEach(function (body) { remote(body); });
    if (!unsynced.length) {
      setPill(true);
      say("Reconnected — queued changes sent.");
    }
  }

  function crossOff(rank, fromServer, mine) {
    if (gone.has(String(rank))) return false;
    log.push({ rank: rank, name: "", ts: Date.now(), mine: !!mine });
    rebuildIndex();
    // Ownership travels with the pick: the server is authoritative in live mode,
    // so a claim the client kept to itself would vanish on the next state replay.
    if (!fromServer) remote({ pick: { rank: rank, mine: !!mine } });
    return true;
  }

  // Someone off this board was taken. Counted, never crossed off — without it the
  // pick counter (and "your pick", and the odds) fall behind for the rest of the draft.
  function pickOffboard(name, fromServer) {
    log.push({ rank: null, name: name || "", ts: Date.now(), mine: false });
    rebuildIndex();
    if (!fromServer) remote({ offboard: { name: name || "" } });
  }

  function undoLastOffboard() {
    for (var i = log.length - 1; i >= 0; i--) {
      if (log[i].rank == null) {
        log.splice(i, 1);
        rebuildIndex();
        remote({ undo_pick: i + 1 });
        return true;
      }
    }
    return false;
  }

  function entryFor(rank) {
    if (rank == null) return null;
    for (var i = 0; i < log.length; i++) if (log[i].rank === rank) return log[i];
    return null;
  }

  function setMine(rank, mine) {
    var e = entryFor(rank);
    if (e) { e.mine = !!mine; save(); remote({ mine: { rank: rank, value: !!mine } }); }
  }

  function restore(rank, fromServer) {
    var before = log.length;
    log = log.filter(function (e) { return e.rank !== rank; });
    rebuildIndex();
    if (log.length === before) return false;
    if (!fromServer) remote({ undo: rank });
    return true;
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // Search has to ignore punctuation: seven players on the board have apostrophes
  // (Ja'Marr, De'Von, Ka'imi...) and nobody types them on the clock. Mirrors
  // board.normalise_name in Python.
  // Hyphens become spaces, other punctuation is dropped — same rule as
  // board.normalise_name in Python.
  var NON_NAME = new RegExp("[^a-z0-9 ]", "g");
  var HYPHEN = new RegExp("-", "g");
  var SPACES = new RegExp(" +", "g");
  function normName(s) {
    var lowered = String(s).toLowerCase().replace(HYPHEN, " ");
    if (lowered.normalize) lowered = lowered.normalize("NFD");
    return lowered.replace(NON_NAME, "").replace(SPACES, " ").trim();
  }

  // Escape first, then promote **bold** — the only markup guidance text may carry.
  function emph(s) {
    return esc(s).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  }

  var posFilter = "ALL";
  var board = document.getElementById("board");

  // Screen-reader announcements (WCAG 4.1.3). paint() appends tier-break
  // transitions to whatever the triggering action queued, then flushes once.
  var liveEl = document.getElementById("live");
  var pending = [];
  var wasBreaking = {};
  var booted = false;                           // silent during the initial paint
  function say(msg) { if (booted) pending.push(msg); }
  function flush() {
    if (!pending.length) return;
    var text = pending.join(" ");
    pending = [];
    liveEl.textContent = "";                    // retrigger identical messages
    setTimeout(function () { liveEl.textContent = text; }, 0);
  }

  function byRank(rank) {
    if (rank == null) return null;                // off-board pick
    for (var i = 0; i < DATA.players.length; i++) {
      if (DATA.players[i].rank === rank) return DATA.players[i];
    }
    return null;
  }

  // ---- draft position: teams + slot, persisted per board; snake math mirrors draft.py ----
  var DKEY = KEY + "-draft";
  var seed = { teams: (DATA.draft && DATA.draft.teams) || 12, slot: (DATA.draft && DATA.draft.slot) || null };
  var draft = { teams: seed.teams, slot: seed.slot };
  try {
    var dsaved = JSON.parse(localStorage.getItem(DKEY) || "null");
    if (dsaved && dsaved.teams) {
      // Page edits normally win, but a rebuild that passes a *different*
      // --teams/--slot is the operator changing their mind; honour it rather
      // than silently showing the settings they just replaced.
      var cliChanged = !dsaved.seed ||
        dsaved.seed.teams !== seed.teams || dsaved.seed.slot !== seed.slot;
      draft = cliChanged ? { teams: seed.teams, slot: seed.slot, keeper: dsaved.keeper,
                             liveval: dsaved.liveval } : dsaved;
    }
  } catch (e) { /* storage already reported */ }
  var keeperMode = !!draft.keeper;
  function saveDraft() {
    draft.keeper = keeperMode;
    draft.liveval = liveMode;
    draft.seed = seed;
    try { localStorage.setItem(DKEY, JSON.stringify(draft)); } catch (e) {}
  }

  // Rounds come from board depth, not a constant: a 10-team league drafts 20
  // rounds off a 200-player board, and assuming 16 told it "no picks left" from
  // pick 161 — exactly the late rounds a deep board exists for.
  function boardRounds(teams) {
    return Math.max(1, Math.ceil(DATA.players.length / teams));
  }
  function snakePicks(teams, slot, rounds) {
    var out = [];
    var n = rounds || boardRounds(teams);
    for (var r = 0; r < n; r++) {
      out.push(r * teams + (r % 2 === 0 ? slot : teams - slot + 1));
    }
    return out;
  }
  function nextPicks(current, count) {
    if (!draft.slot || draft.slot < 1 || draft.slot > draft.teams) return [];
    return snakePicks(draft.teams, draft.slot).filter(function (p) { return p >= current; }).slice(0, count || 2);
  }
  // Abramowitz–Stegun 7.1.26 erf; error < 1.5e-7 — plenty for a percentage.
  function erf(x) {
    var s = x < 0 ? -1 : 1; x = Math.abs(x);
    var t = 1 / (1 + 0.3275911 * x);
    var y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
    return s * y;
  }
  function phi(z) { return 0.5 * (1 + erf(z / Math.SQRT2)); }
  function availability(adp, sd, pick) {
    var sigma = Math.max(sd == null ? adp / 4 : sd, 0.5);
    return 1 - phi((pick - adp) / sigma);
  }
  // ---- live value: value over replacement, recomputed against who is left ----
  // A static rank cannot know that every startable tight end is gone. VOR against
  // the *remaining* pool can: as a position thins, its replacement level drops and
  // everyone still on the board at that position gains value.
  var FLEX_SHARE = { RB: 0.5, WR: 0.4, TE: 0.1 };
  var liveMode = !!draft.liveval;

  function remaining() {
    return DATA.players.filter(function (p) {
      return !gone.has(String(p.rank)) && p.projected != null;
    });
  }

  function liveVor() {
    var pool = remaining();
    if (!pool.length) return {};
    var demand = {};
    Object.keys(LINEUP_COUNT).forEach(function (pos) {
      if (pos === "FLEX") {
        Object.keys(FLEX_SHARE).forEach(function (f) {
          demand[f] = (demand[f] || 0) + LINEUP_COUNT.FLEX * FLEX_SHARE[f];
        });
      } else {
        demand[pos] = (demand[pos] || 0) + LINEUP_COUNT[pos];
      }
    });

    var byPos = {};
    pool.forEach(function (p) { (byPos[p.pos] = byPos[p.pos] || []).push(p.projected); });
    var levels = {};
    Object.keys(byPos).forEach(function (pos) {
      var pts = byPos[pos].sort(function (a, b) { return b - a; });
      var need = Math.round((demand[pos] || 1) * draft.teams);
      levels[pos] = pts[Math.max(0, Math.min(pts.length - 1, need - 1))];
    });

    var out = {};
    pool.forEach(function (p) {
      if (levels[p.pos] != null) out[p.rank] = Math.round(p.projected - levels[p.pos]);
    });
    return out;
  }

  var LINEUP_COUNT = DATA.lineup || { QB: 1, RB: 2, WR: 2, TE: 1, FLEX: 1, K: 1, DST: 1 };

  // ---- what the managers picking before you still need ----
  function opponentNeeds(current, myNext) {
    if (!draft.slot || !myNext || myNext <= current) return [];
    // Which slot owns each overall pick in a snake draft.
    var slotOf = function (pick) {
      var rnd = Math.floor((pick - 1) / draft.teams);
      var idx = (pick - 1) % draft.teams;
      return rnd % 2 === 0 ? idx + 1 : draft.teams - idx;
    };
    var rosters = {};
    log.forEach(function (e, i) {
      var owner = slotOf(i + 1);
      var p = byRank(e.rank);
      if (p) (rosters[owner] = rosters[owner] || []).push(p);
    });

    var out = [];
    for (var pick = current; pick < myNext; pick++) {
      var owner = slotOf(pick);
      var res = fillLineup(rosters[owner] || []);
      var open = res.filled.filter(function (r) { return !r.player; }).map(function (r) { return r.slot; });
      out.push({ pick: pick, slot: owner, open: open });
    }
    return out;
  }

  function paintOpponents(current, mine) {
    var card = document.getElementById("opponentcard");
    var rows = opponentNeeds(current, mine[0]);
    if (!rows.length) { card.hidden = true; return; }
    card.hidden = false;
    // A position two or more managers still need is the one that will not last.
    var counts = {};
    rows.forEach(function (r) {
      r.open.forEach(function (slot) { counts[slot] = (counts[slot] || 0) + 1; });
    });
    document.getElementById("opponents").innerHTML = rows.map(function (r) {
      var threat = r.open.some(function (slot) { return counts[slot] >= 2; });
      return '<div class="oppline' + (threat ? " threat" : "") + '">' +
        '<span class="who2">#' + r.pick + " · T" + r.slot + "</span>" +
        '<span class="needs2">' +
        (r.open.length ? "needs <b>" + r.open.slice(0, 4).join("</b>, <b>") + "</b>" : "lineup full") +
        "</span></div>";
    }).join("");
  }

  // Keeper value tracks the age curve: backs fall off a cliff around 28, receivers
  // and tight ends hold value later, quarterbacks longest of all.
  var AGE_CLIFF = { RB: 27, WR: 29, TE: 30, QB: 34, K: 99, DST: 99 };
  function ageClass(p) {
    if (!p.age) return "";
    var cliff = AGE_CLIFF[p.pos] || 30;
    if (p.age <= cliff - 3) return "young";
    return p.age >= cliff ? "old" : "";
  }
  function ageTitle(p) {
    var bits = [p.age + " years old"];
    if (p.exp != null) bits.push(p.exp === 0 ? "rookie" : p.exp + " seasons");
    return bits.join(" · ");
  }

  function bandOf(pr) { return pr >= 0.6 ? "wait" : pr <= 0.3 ? "now" : "toss-up"; }
  function oddsHtml(p, picks) {
    if (p.adp == null || !picks.length) return "";
    var pr = picks.map(function (k) { return availability(p.adp, p.adp_sd, k); });
    var cls = "odds " + bandOf(pr[0]);
    var t = "Chance " + p.name + " is still there at pick " + picks[0] +
      (pr[1] != null ? " / pick " + picks[1] : "") + " (ADP " + p.adp + ")";
    return '<span class="' + cls + '" title="' + esc(t) + '"><b>' + Math.round(pr[0] * 100) + "%</b>" +
      (pr[1] != null ? " · " + Math.round(pr[1] * 100) + "%" : "") + "</span>";
  }

  // ---- your roster: the board's own lineup, and what it still needs ----
  // Yahoo's default is 1QB 2RB 2WR 1TE 1FLEX 1K 1DEF (help.yahoo.com/kb/SLN22673),
  // but a superflex board starts two quarterbacks — assuming one made its roster
  // card, its lineup needs and its live value all wrong for the format it was
  // built for. DATA.lineup is what the board was priced against.
  var SLOT_ORDER = ["QB", "RB", "WR", "TE", "FLEX", "K", "DST"];
  var SLOT_LABEL = { DST: "DEF" };
  var LINEUP = (function () {
    var out = [];
    var order = SLOT_ORDER.concat(Object.keys(LINEUP_COUNT).filter(function (p) {
      return SLOT_ORDER.indexOf(p) === -1;
    }));
    order.forEach(function (pos) {
      for (var i = 0; i < (LINEUP_COUNT[pos] || 0); i++) {
        out.push({
          slot: SLOT_LABEL[pos] || pos,
          takes: pos === "FLEX" ? ["RB", "WR", "TE"] : [pos]
        });
      }
    });
    return out;
  })();

  function myPlayers() {
    return log.filter(function (e) { return e.mine; })
      .map(function (e) { return byRank(e.rank); })
      .filter(Boolean);
  }

  // Greedy fill in lineup order, dedicated slots before FLEX so a spare RB
  // doesn't consume the flex while RB2 sits empty.
  function fillLineup(players) {
    var pool = players.slice();
    var filled = LINEUP.map(function (l) { return { slot: l.slot, takes: l.takes, player: null }; });
    filled.forEach(function (row) {
      if (row.slot === "FLEX") return;
      for (var i = 0; i < pool.length; i++) {
        if (row.takes.indexOf(pool[i].pos) !== -1) { row.player = pool.splice(i, 1)[0]; return; }
      }
    });
    filled.forEach(function (row) {
      if (row.slot !== "FLEX" || row.player) return;
      for (var i = 0; i < pool.length; i++) {
        if (row.takes.indexOf(pool[i].pos) !== -1) { row.player = pool.splice(i, 1)[0]; return; }
      }
    });
    return { filled: filled, bench: pool };
  }

  function byeClashes(players) {
    var weeks = {};
    players.forEach(function (p) {
      if (p.bye) (weeks[p.bye] = weeks[p.bye] || []).push(p);
    });
    return Object.keys(weeks)
      .filter(function (w) { return weeks[w].length >= 3; })
      .map(function (w) { return { week: Number(w), players: weeks[w] }; });
  }

  function paintRoster() {
    var mine = myPlayers();
    var card = document.getElementById("rostercard");
    if (!mine.length) { card.hidden = true; myByeWeeks = {}; return; }
    card.hidden = false;

    var res = fillLineup(mine);
    document.getElementById("roster").innerHTML = res.filled.map(function (r) {
      var p = r.player;
      return '<div class="slotline' + (p ? "" : " empty") + '"><span class="sl">' + r.slot + "</span>" +
        '<span class="nm3">' + (p ? esc(p.name) + " " : "—") +
        (p ? '<span class="bw">' + esc(p.pos) + " " + esc(p.team) + "</span>" : "") + "</span>" +
        '<span class="bw">' + (p && p.bye ? "B" + p.bye : "") + "</span></div>";
    }).join("") + res.bench.map(function (p) {
      return '<div class="slotline"><span class="sl">BN</span><span class="nm3">' + esc(p.name) +
        ' <span class="bw">' + esc(p.pos) + " " + esc(p.team) + '</span></span><span class="bw">' +
        (p.bye ? "B" + p.bye : "") + "</span></div>";
    }).join("");

    var open = res.filled.filter(function (r) { return !r.player; }).map(function (r) { return r.slot; });
    document.getElementById("needs").innerHTML = open.length
      ? "Still needs <b>" + open.join("</b>, <b>") + "</b>"
      : "Starting lineup complete — " + res.bench.length + " on the bench";

    var clashes = byeClashes(mine);
    var warn = document.getElementById("byewarn");
    warn.hidden = !clashes.length;
    if (clashes.length) {
      warn.innerHTML = clashes.map(function (c) {
        return "Week " + c.week + ": " + c.players.length + " of your players are on bye (" +
          c.players.map(function (p) { return esc(p.name); }).join(", ") + ")";
      }).join("<br>");
    }

    myByeWeeks = {};
    mine.forEach(function (p) { if (p.bye) myByeWeeks[p.bye] = (myByeWeeks[p.bye] || 0) + 1; });
  }
  var myByeWeeks = {};

  // ---- toast with Undo (the safety net; there are no confirm dialogs) ----
  var toastEl = document.getElementById("toast");
  var toastMsg = document.getElementById("toastMsg");
  var toastUndo = document.getElementById("toastUndo");
  var toastMine = document.getElementById("toastMine");
  var toastTimer = null;
  var undoAction = null;                        // function that reverts the last action

  var mineRank = null;                          // rank the "That's mine" button toggles
  var TOAST_MS = 8000;
  var toastReturnFocus = null;                  // where to put focus back after Undo
  function toast(msg, undo, rank) {
    toastMsg.textContent = msg;
    undoAction = undo || null;
    toastUndo.hidden = !undo;
    mineRank = rank == null ? null : rank;
    if (mineRank !== null) {
      var e = entryFor(mineRank);
      toastMine.hidden = false;
      toastMine.textContent = e && e.mine ? "Not mine" : "That's mine";
    } else {
      toastMine.hidden = true;
    }
    toastEl.hidden = false;
    startToastTimer();
    // Keyboard users would otherwise have to Shift+Tab back through every row
    // between the board and the toast before Undo expires.
    if (undo && lastActionWasKeyboard) {
      toastReturnFocus = document.activeElement;
      toastUndo.focus();
    }
  }
  function startToastTimer() {
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, TOAST_MS);
  }
  function hideToast() {
    clearTimeout(toastTimer);
    toastEl.hidden = true;
    undoAction = null;
    mineRank = null;
    if (toastReturnFocus && document.contains(toastReturnFocus)) toastReturnFocus.focus();
    toastReturnFocus = null;
  }
  // Reading or reaching for the toast should not race its own timer.
  toastEl.addEventListener("mouseenter", function () { clearTimeout(toastTimer); });
  toastEl.addEventListener("focusin", function () { clearTimeout(toastTimer); });
  toastEl.addEventListener("mouseleave", startToastTimer);
  toastEl.addEventListener("focusout", function (e) {
    if (!toastEl.contains(e.relatedTarget)) startToastTimer();
  });

  // A click from Enter/Space reports detail 0; a real pointer click reports 1+.
  var lastActionWasKeyboard = false;
  toastMine.addEventListener("click", function () {
    if (mineRank === null) return;
    var e = entryFor(mineRank);
    var now = !(e && e.mine);
    setMine(mineRank, now);
    var p = byRank(mineRank);
    say(p.name + (now ? " added to your roster." : " removed from your roster."));
    toastMine.textContent = now ? "Not mine" : "That's mine";
    paint();
  });
  toastUndo.addEventListener("click", function () {
    var fn = undoAction;
    hideToast();
    if (fn) { fn(); paint(); }
  });

  function toggle(rank) {
    var k = String(rank);
    var p = byRank(rank);
    if (gone.has(k)) {
      restore(rank);
      say(p.name + " restored.");
      toast(p.name + " restored", function () { crossOff(rank); save(); say(p.name + " crossed off."); });
    } else {
      // If the board knows your slot and this is your pick, assume it's yours.
      var onTheClock = log.length + 1;
      var mine = nextPicks(onTheClock, 1)[0] === onTheClock;
      crossOff(rank, false, mine);
      say(p.name + (mine ? " crossed off — added to your roster." : " crossed off."));
      toast(
        p.name + (mine ? " — yours" : " crossed off"),
        function () { restore(rank); save(); say(p.name + " restored."); },
        rank
      );
    }
    save();
  }

  // ---- positional runs: 3 of the last 4 picks at one position ----
  var RUN_WINDOW = 4, RUN_MIN = 3;
  var lastRun = null;
  function detectRun() {
    var recent = log.slice(-RUN_WINDOW);
    var counts = {};
    recent.forEach(function (e) {
      var p = byRank(e.rank);
      if (p) counts[p.pos] = (counts[p.pos] || 0) + 1;
    });
    var run = null;
    Object.keys(counts).forEach(function (pos) { if (counts[pos] >= RUN_MIN) run = pos; });
    Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (c) {
      c.classList.toggle("run", c.dataset.pos === run);
    });
    if (run && run !== lastRun) {
      say(run + " run: " + counts[run] + " of the last " + recent.length + " picks.");
    }
    lastRun = run;
  }

  /* ---------- static shell built from DATA ---------- */

  function lineupSummary() {
    return LINEUP.reduce(function (acc, row) {
      var last = acc[acc.length - 1];
      if (last && last.slot === row.slot) last.n += 1;
      else acc.push({ slot: row.slot, n: 1 });
      return acc;
    }, []).map(function (g) { return g.n + g.slot; }).join(" · ");
  }

  function buildShell() {
    // Read from the dataset: this used to claim "Yahoo default · 0.5 PPR · QB
    // Single" on every board, including the superflex and full-PPR variants.
    document.getElementById("settings").innerHTML =
      "<span><b>Format</b> " + esc(DATA.format) + "</span>" +
      "<span><b>Lineup</b> " + esc(lineupSummary()) + "</span>" +
      "<span><b>Board</b> " + DATA.players.length + " ranked + rails</span>";

    // After a draft-morning refresh the ranks keep their date but the market data
    // is newer; showing only the older one reads as "the refresh didn't take".
    var eyebrow = document.getElementById("eyebrow");
    if (DATA.refreshed) {
      // Same day as the ranks? The time is the only new information.
      var stamp = DATA.refreshed.indexOf(DATA.updated) === 0
        ? DATA.refreshed.slice(DATA.updated.length).trim()
        : DATA.refreshed;
      eyebrow.textContent = eyebrow.textContent.replace("Board is live", "Refreshed " + stamp);
    }
    if (DATA.league) {
      eyebrow.textContent += " · " + DATA.league;
    }

    document.getElementById("plan").innerHTML = DATA.plan.map(function (p) {
      return '<div class="plan-cell"><h3>' + esc(p.position) + "</h3><p>" + emph(p.guidance) + "</p></div>";
    }).join("");

    document.getElementById("dnd").innerHTML = DATA.do_not_draft.map(function (e) {
      return '<li><span class="l-nm">' + esc(e.name) +
        ' <span class="p">' + esc(e.pos) + " " + esc(e.team) + "</span></span>" +
        '<span class="l-why">' + esc(e.why) + "</span></li>";
    }).join("");

    document.getElementById("inj").innerHTML = DATA.injuries.map(function (i) {
      return '<div class="statusline"><span class="s-nm">' + esc(i.name) +
        ' <span class="p">' + esc(i.team) + "</span></span>" +
        '<span class="s-st st-' + i.severity + '">' +
        String(i.status == null ? "" : i.status).split("|").map(esc).join("<br>") + "</span></div>";
    }).join("");

    document.getElementById("slp").innerHTML = DATA.sleepers.map(function (e) {
      var tag = e.team ? esc(e.pos) + " " + esc(e.team) : esc(e.pos);
      return '<li><span class="l-nm">' + esc(e.name) + ' <span class="p">' + tag + "</span></span>" +
        '<span class="l-why">' + esc(e.why) + "</span></li>";
    }).join("");

    if (DATA.trending && DATA.trending.length) {
      document.getElementById("trendcard").hidden = false;
      document.getElementById("trend").innerHTML = DATA.trending.map(function (t) {
        return '<div class="statusline"><span class="s-nm">' + esc(t.name) +
          ' <span class="p">' + esc(t.pos) + " " + esc(t.team) + "</span></span>" +
          '<span class="s-st st-ok">+' + Number(t.count).toLocaleString() + "</span></div>";
      }).join("");
    }

    // A board with no rails showed three header-only boxes and a 2px rule where
    // the plan would be. Trending already hid itself; the rest now match.
    [["plan", DATA.plan], ["dndcard", DATA.do_not_draft],
     ["injcard", DATA.injuries], ["slpcard", DATA.sleepers]].forEach(function (pair) {
      document.getElementById(pair[0]).hidden = !(pair[1] && pair[1].length);
    });

    // Where the ranks came from is a property of the dataset, not of the template:
    // a variant or a hand-written board must not credit Rotoworld for its order.
    document.getElementById("provenance").textContent =
      (DATA.provenance ? DATA.provenance + " " : "") +
      "Data current through " + DATA.updated + " — recheck the injury board before you draft.";

    var adpEl = document.getElementById("adpnote");
    if (DATA.adp) {
      adpEl.textContent = "ADP and bye weeks: " + DATA.adp.source + " — " + DATA.adp.format +
        ", " + DATA.adp.window + ". Survival odds assume a normal draft position with that spread.";
    } else {
      adpEl.textContent = "";
    }

    if (DATA.auction) {
      adpEl.textContent += " Auction values assume $" + DATA.auction.budget + " and " +
        DATA.auction.roster_size + " roster spots in a " + DATA.auction.teams + "-team league.";
    }
    if (DATA.refreshed) {
      adpEl.textContent += " Injuries and trending refreshed " + DATA.refreshed + ".";
    }

    // Only http(s) becomes a link: `esc()` stops attribute breakout but not the URL
    // scheme, and the CSP allows inline script, so a `javascript:` source in an
    // untrusted board would run on click. Anything else shows as plain text.
    // New tab: a mid-draft mis-click must not navigate the board away.
    document.getElementById("srcs").innerHTML = DATA.sources.map(function (s) {
      var url = String(s.url || "");
      if (!/^https?:\/\//i.test(url)) return '<span class="src-flat">' + esc(s.label) + "</span>";
      return '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + esc(s.label) + "</a>";
    }).join("");
  }

  /* ---------- tier sheet ---------- */

  function buildBoard() {
    board.innerHTML = "";
    DATA.tiers.forEach(function (t) {
      var list = DATA.players.filter(function (p) { return p.tier === t.n; });

      var sec = document.createElement("section");
      sec.className = "tier";
      sec.dataset.tier = t.n;

      var head = document.createElement("div");
      head.className = "tier-head";
      head.innerHTML =
        '<span class="tier-num">' + t.n + "</span>" +
        '<span class="tier-name">' + esc(t.name) + "</span>" +
        '<span class="tier-range">' + esc(t.range) + " · " + esc(t.note) + "</span>" +
        '<span class="tier-left"></span>';
      sec.appendChild(head);

      var rows = document.createElement("div");
      rows.className = "rows";

      list.forEach(function (p) {
        var b = document.createElement("button");
        b.className = "row";
        b.type = "button";
        b.dataset.rk = p.rank;
        b.dataset.pos = p.pos;
        b.dataset.nm = normName(p.name);
        b.innerHTML =
          '<span class="rk">' + p.rank + "</span>" +
          '<span class="who"><span class="nm">' + esc(p.name) + "</span>" +
          '<span class="pos">' + esc(p.pos) + " · " + esc(p.team) + "</span>" +
          (p.note ? '<span class="note">' + esc(p.note) + "</span>" : "") + "</span>" +
          '<span class="flags"><span class="vorslot"></span><span class="ageslot"></span><span class="valslot"></span><span class="byeslot"></span><span class="oddsslot"></span>' +
          (p.flag ? '<span class="flag f-' + p.flag + '">' + FLAG_LABEL[p.flag] + "</span>" : "") +
          "</span>";
        b.addEventListener("click", function (ev) {
          lastActionWasKeyboard = ev.detail === 0;
          toggle(p.rank);
          paint();
          lastActionWasKeyboard = false;
        });
        rows.appendChild(b);
      });

      sec.appendChild(rows);
      board.appendChild(sec);
    });
  }

  /* ---------- live state ---------- */

  function paintPickInfo(current, mine) {
    var el = document.getElementById("pickinfo");
    el.classList.remove("mine");
    if (!draft.slot) { el.textContent = "Set your slot for pick odds"; return; }
    if (!mine.length) { el.innerHTML = "Pick <b>" + current + "</b> · no picks left"; return; }
    if (mine[0] === current) {
      el.classList.add("mine");
      el.innerHTML = "Pick <b>" + current + "</b> · <b>your pick</b>";
    } else {
      el.innerHTML = "Pick <b>" + current + "</b> · yours in <b>" + (mine[0] - current) + "</b> (#" + mine[0] + ")";
    }
  }

  function paint() {
    var q = normName(document.getElementById("search").value);
    var current = log.length + 1;                 // every taken player crossed off => this is the pick on the clock
    var mine = nextPicks(current, 2);
    paintPickInfo(current, mine);
    paintRoster();
    paintOpponents(current, mine);
    var vor = liveMode ? liveVor() : {};

    Array.prototype.forEach.call(document.querySelectorAll(".row"), function (r) {
      var isGone = gone.has(r.dataset.rk);
      r.classList.toggle("gone", isGone);
      r.setAttribute("aria-pressed", isGone ? "true" : "false");
      var okPos = posFilter === "ALL" || r.dataset.pos === posFilter;
      var okQ = !q || r.dataset.nm.indexOf(q) !== -1;
      r.classList.toggle("hide", !(okPos && okQ));
      var pl = byRank(Number(r.dataset.rk));
      r.classList.toggle("mine", !!(isGone && (entryFor(pl.rank) || {}).mine));
      r.querySelector(".oddsslot").innerHTML = isGone ? "" : oddsHtml(pl, mine);
      r.querySelector(".vorslot").innerHTML = liveMode && vor[pl.rank] != null && !isGone
        ? '<span class="vor" title="Points above the replacement still available">+' +
          vor[pl.rank] + "</span>"
        : "";
      r.querySelector(".ageslot").innerHTML = keeperMode && pl.age
        ? '<span class="age ' + ageClass(pl) + '" title="' + esc(ageTitle(pl)) + '">' +
          pl.age + "y</span>"
        : "";
      r.querySelector(".valslot").innerHTML = pl.value
        ? '<span class="val" title="Auction value">$' + pl.value + "</span>"
        : "";
      r.querySelector(".byeslot").innerHTML = pl.bye
        ? '<span class="bye' + (myByeWeeks[pl.bye] >= 2 && !isGone ? " clash" : "") + '" title="Bye week ' +
          pl.bye + '">B' + pl.bye + "</span>"
        : "";
    });

    Array.prototype.forEach.call(document.querySelectorAll(".tier"), function (sec) {
      sec.classList.toggle("resorted", liveMode);
      var host = sec.querySelector(".rows");
      var rows = Array.prototype.slice.call(host.querySelectorAll(".row"));
      rows.sort(function (a, b) {
        if (liveMode) {
          var va = vor[Number(a.dataset.rk)], vb = vor[Number(b.dataset.rk)];
          if (va != null && vb != null && va !== vb) return vb - va;
          if (va != null && vb == null) return -1;
          if (va == null && vb != null) return 1;
        }
        return Number(a.dataset.rk) - Number(b.dataset.rk);
      });
      rows.forEach(function (r) { host.appendChild(r); });
    });

    var filtered = posFilter !== "ALL" || !!q;
    Array.prototype.forEach.call(document.querySelectorAll(".tier"), function (sec) {
      var rows = Array.prototype.slice.call(sec.querySelectorAll(".row"));
      var notGone = function (r) { return !r.classList.contains("gone"); };
      var visible = rows.filter(function (r) { return !r.classList.contains("hide"); });
      var left = visible.filter(notGone).length;
      // The break signal is about the whole tier, never the filtered slice of it —
      // filtering to TE must not make a full tier with one TE read "Tier break".
      var tierLeft = rows.filter(notGone).length;
      var breaking = tierLeft > 0 && tierLeft <= TIER_BREAK;
      var tn = sec.dataset.tier;
      if (breaking && !wasBreaking[tn]) {
        say("Tier " + tn + " is down to " + tierLeft + " — tier break.");
      }
      wasBreaking[tn] = breaking;
      sec.querySelector(".tier-left").innerHTML =
        left + " left" +
        (filtered && left !== tierLeft ? " · " + tierLeft + " in tier" : "") +
        (breaking ? '<span class="breakflag">Tier break</span>' : "");
      sec.classList.toggle("empty", left === 0);
      sec.style.display = visible.length ? "" : "none";
    });

    var avail = DATA.players.filter(function (p) {
      return !gone.has(String(p.rank)) && (posFilter === "ALL" || p.pos === posFilter);
    });

    document.getElementById("bestAvail").innerHTML = avail.length
      ? avail.slice(0, 8).map(function (p) {
          return '<div class="ba-item"><span class="n">' + p.rank + "</span>" +
            '<span class="nm2">' + esc(p.name) + "</span>" +
            '<span class="p">' + esc(p.pos) + " " + esc(p.team) + " " + oddsHtml(p, mine.slice(0, 1)) + "</span></div>";
        }).join("")
      : '<div class="ba-item"><span class="nm2" style="color:var(--ink-dim)">Board cleared.</span></div>';

    var total = posFilter === "ALL"
      ? DATA.players.length
      : DATA.players.filter(function (p) { return p.pos === posFilter; }).length;

    document.getElementById("tally").innerHTML =
      "<b>" + avail.length + "</b> of " + total + " on the board";

    detectRun();
    if (pending.length && avail.length) {
      say("Best available: " + avail[0].name + ".");
    }
    flush();
  }

  // Enter in the search box crosses off (or restores) the single visible match.
  function crossOffSearchMatch() {
    var q = normName(document.getElementById("search").value);
    if (!q) return;
    var matches = Array.prototype.filter.call(document.querySelectorAll(".row:not(.hide)"), function (r) {
      return r.dataset.nm.indexOf(q) !== -1;
    });
    if (matches.length === 1) {
      toggle(Number(matches[0].dataset.rk));
      document.getElementById("search").value = "";
    } else if (matches.length === 0) {
      say("No player matches “" + q + "”. Use Off-board pick if they were taken anyway.");
    } else {
      say(matches.length + " players match — keep typing.");
    }
    paint();
  }

  /* ---------- wiring ---------- */

  document.getElementById("chips").addEventListener("click", function (e) {
    var b = e.target.closest(".chip");
    if (!b) return;
    posFilter = b.dataset.pos;
    Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (c) {
      c.setAttribute("aria-pressed", c === b ? "true" : "false");
    });
    say(posFilter === "ALL" ? "Showing all positions." : "Showing " + posFilter + " only.");
    paint();
  });

  document.getElementById("search").addEventListener("input", paint);
  document.getElementById("search").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); crossOffSearchMatch(); }
  });

  var liveBtn = document.getElementById("liveval");
  liveBtn.setAttribute("aria-pressed", liveMode ? "true" : "false");
  liveBtn.addEventListener("click", function () {
    liveMode = !liveMode;
    draft.liveval = liveMode;
    liveBtn.setAttribute("aria-pressed", liveMode ? "true" : "false");
    saveDraft();
    say(liveMode ? "Sorted by live value over replacement." : "Back to consensus rank.");
    paint();
  });

  var keeperBtn = document.getElementById("keeper");
  keeperBtn.setAttribute("aria-pressed", keeperMode ? "true" : "false");
  keeperBtn.addEventListener("click", function () {
    keeperMode = !keeperMode;
    keeperBtn.setAttribute("aria-pressed", keeperMode ? "true" : "false");
    saveDraft();
    say(keeperMode ? "Showing age and experience." : "Age hidden.");
    paint();
  });

  document.getElementById("print").addEventListener("click", function () { window.print(); });

  document.getElementById("offboard").addEventListener("click", function () {
    pickOffboard("");
    save();
    say("Off-board pick recorded. Pick " + (log.length + 1) + " is on the clock.");
    toast("Off-board pick recorded", function () {
      undoLastOffboard(); save(); say("Off-board pick removed.");
    });
    paint();
  });

  // Roster as CSV. Clipboard first (works everywhere the page does); if the browser
  // refuses, fall back to selecting the text in a prompt the user can copy by hand.
  document.getElementById("exportRoster").addEventListener("click", function () {
    var btn = this;
    var rows = [["slot", "rank", "name", "pos", "team", "bye", "adp"]];
    var res = fillLineup(myPlayers());
    res.filled.forEach(function (r) {
      if (r.player) {
        rows.push([r.slot, r.player.rank, r.player.name, r.player.pos, r.player.team,
                   r.player.bye || "", r.player.adp == null ? "" : r.player.adp]);
      }
    });
    res.bench.forEach(function (p) {
      rows.push(["BN", p.rank, p.name, p.pos, p.team, p.bye || "", p.adp == null ? "" : p.adp]);
    });
    var csv = rows.map(function (r) {
      return r.map(function (c) {
        c = String(c);
        return NEEDS_QUOTE.test(c) ? QUOTE + c.replace(DQ, QUOTE + QUOTE) + QUOTE : c;
      }).join(",");
    }).join(NEWLINE);

    var done = function (ok) {
      btn.textContent = ok ? "Copied" : "Press Ctrl+C";
      btn.classList.toggle("done", ok);
      say(ok ? "Roster copied as CSV." : "Copy it from the box.");
      setTimeout(function () {
        btn.textContent = "Copy as CSV";
        btn.classList.remove("done");
      }, 3000);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(csv)
        .then(function () { done(true); })
        .catch(function () { window.prompt("Your roster as CSV", csv); done(false); });
    } else {
      window.prompt("Your roster as CSV", csv);
      done(false);
    }
  });

  var teamsIn = document.getElementById("teams");
  var slotIn = document.getElementById("slot");
  teamsIn.value = draft.teams;
  if (draft.slot) slotIn.value = draft.slot;
  function onDraftInput() {
    var t = parseInt(teamsIn.value, 10);
    var s = parseInt(slotIn.value, 10);
    draft.teams = t >= 2 ? t : 12;
    draft.slot = s >= 1 && s <= draft.teams ? s : null;
    slotIn.max = draft.teams;
    saveDraft();
    paint();
  }
  teamsIn.addEventListener("input", onDraftInput);
  slotIn.addEventListener("input", onDraftInput);

  document.getElementById("reset").addEventListener("click", function () {
    var before = log.slice();
    log = [];
    rebuildIndex();
    save();
    remote({ reset: true });
    document.getElementById("search").value = "";
    say("Board reset. " + DATA.players.length + " players on the board.");
    if (before.length) {
      toast("Board reset — " + before.length + " picks cleared", function () {
        log = before; rebuildIndex(); save();
        before.forEach(function (e) { remote({ pick: { rank: e.rank } }); });
        say("Reset undone. " + before.length + " picks restored.");
      });
    }
    paint();
  });

  // ---- SSE client + screen wake lock (served pages only) ----
  if (LIVE && typeof EventSource !== "undefined") {
    var pill = document.getElementById("livepill");
    setPill = function (on) {
      pill.hidden = false;
      var stuck = unsynced.length;
      pill.classList.toggle("off", !on || !!stuck);
      pill.textContent = stuck
        ? "Live · " + stuck + " change" + (stuck === 1 ? "" : "s") + " not synced"
        : on ? "Live · following the server" : "Live · reconnecting…";
    };
    var es = new EventSource("/events");
    es.addEventListener("open", function () { setPill(true); flushUnsynced(); });
    es.addEventListener("error", function () { setPill(false); });
    es.addEventListener("state", function (ev) {
      var st = JSON.parse(ev.data);
      // The server owns `mine` now, so replaying state no longer forgets which
      // picks are yours — that used to empty the roster card on every reload.
      log = (st.picks || []).map(function (p) {
        return { rank: p.rank == null ? null : p.rank, name: p.name || "", ts: p.ts || null, mine: !!p.mine };
      });
      if (st.teams) { draft.teams = st.teams; teamsIn.value = st.teams; }
      if (st.slot) { draft.slot = st.slot; slotIn.value = st.slot; }
      rebuildIndex(); save(); paint();
    });
    es.addEventListener("pick", function (ev) {
      var p = JSON.parse(ev.data);
      if (p.rank != null && gone.has(String(p.rank))) return;
      if (p.rank == null) {
        pickOffboard(p.name, true);
        if (p.source !== "board") say("Off-board pick" + (p.name ? ": " + p.name : "") + ".");
      } else {
        crossOff(p.rank, true, p.mine);
        var pl = byRank(p.rank);
        if (pl && p.source !== "board") {
          // A pick that arrives from Sleeper can be yours too — offer the same
          // claim the manual path does instead of silently ignoring it.
          say(pl.name + (p.mine ? " crossed off — added to your roster." : " crossed off (" + p.source + ")."));
          toast(pl.name + (p.mine ? " — yours" : " crossed off (" + p.source + ")"), null, p.rank);
        }
      }
      save();
      paint();
    });
    es.addEventListener("mine", function (ev) {
      var p = JSON.parse(ev.data);
      var e = entryFor(p.rank);
      if (e && e.mine !== !!p.mine) { e.mine = !!p.mine; save(); paint(); }
    });
    es.addEventListener("undo", function (ev) {
      var p = JSON.parse(ev.data);
      if (restore(p.rank, true)) { save(); paint(); }
    });
    es.addEventListener("reset", function () {
      log = []; rebuildIndex(); save(); paint();
    });

    var wakeLock = null;
    var keepAwake = function () {
      if (!("wakeLock" in navigator) || document.visibilityState !== "visible") return;
      navigator.wakeLock.request("screen").then(function (w) { wakeLock = w; }).catch(function () {});
    };
    document.addEventListener("visibilitychange", keepAwake);
    keepAwake();
  }

  // The controls bar wraps to two rows at some widths; the rail sticks below it,
  // so its offset is measured rather than assumed.
  (function trackControlsHeight() {
    var bar = document.querySelector(".controls");
    if (!bar) return;
    var apply = function () {
      var h = Math.round(bar.getBoundingClientRect().height);
      document.documentElement.style.setProperty("--controls-h", h + "px");
    };
    apply();
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(apply).observe(bar);
    else window.addEventListener("resize", apply);
  })();

  buildShell();
  buildBoard();
  paint();
  booted = true;
})();
