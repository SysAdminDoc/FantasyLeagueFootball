/* Draft board runtime. DATA is injected by render.py as a JSON literal. */
(function () {
  "use strict";

  var TIER_BREAK = DATA.tier_break;   // single source of truth: board.TIER_BREAK_THRESHOLD
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

  // State is an ordered pick log: [{rank, ts}] in the order players were crossed
  // off. `gone` is a derived index for O(1) lookups. Storage schema v2; v1 was a
  // bare array of rank strings, migrated in load order (Set insertion order).
  var log = [];
  var gone = new Set();

  function rebuildIndex() {
    gone = new Set(log.map(function (e) { return String(e.rank); }));
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
        log = parsed.log.filter(function (e) { return e && typeof e.rank === "number"; });
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
  function remote(body) {
    if (!LIVE) return;
    try {
      fetch("/state", { method: "POST", headers: { "Content-Type": "application/json", "X-Source": "board" },
        body: JSON.stringify(body) }).catch(function () {});
    } catch (e) { /* no fetch — nothing to sync */ }
  }

  function crossOff(rank, fromServer) {
    if (gone.has(String(rank))) return false;
    log.push({ rank: rank, ts: Date.now() });
    rebuildIndex();
    if (!fromServer) remote({ pick: { rank: rank } });
    return true;
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
    for (var i = 0; i < DATA.players.length; i++) {
      if (DATA.players[i].rank === rank) return DATA.players[i];
    }
    return null;
  }

  // ---- draft position: teams + slot, persisted per board; snake math mirrors draft.py ----
  var DKEY = KEY + "-draft";
  var draft = { teams: (DATA.draft && DATA.draft.teams) || 12, slot: (DATA.draft && DATA.draft.slot) || null };
  try {
    var dsaved = JSON.parse(localStorage.getItem(DKEY) || "null");
    if (dsaved && dsaved.teams) draft = dsaved;
  } catch (e) { /* storage already reported */ }
  function saveDraft() { try { localStorage.setItem(DKEY, JSON.stringify(draft)); } catch (e) {} }

  function snakePicks(teams, slot, rounds) {
    var out = [];
    for (var r = 0; r < (rounds || 16); r++) {
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

  // ---- toast with Undo (the safety net; there are no confirm dialogs) ----
  var toastEl = document.getElementById("toast");
  var toastMsg = document.getElementById("toastMsg");
  var toastUndo = document.getElementById("toastUndo");
  var toastTimer = null;
  var undoAction = null;                        // function that reverts the last action

  function toast(msg, undo) {
    toastMsg.textContent = msg;
    undoAction = undo || null;
    toastUndo.hidden = !undo;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, 6000);
  }
  function hideToast() {
    toastEl.hidden = true;
    undoAction = null;
  }
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
      crossOff(rank);
      say(p.name + " crossed off.");
      toast(p.name + " crossed off", function () { restore(rank); save(); say(p.name + " restored."); });
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

  function buildShell() {
    document.getElementById("settings").innerHTML =
      "<span><b>Format</b> Yahoo default</span>" +
      "<span><b>Scoring</b> 0.5 PPR</span>" +
      "<span><b>QB</b> Single</span>" +
      "<span><b>Board</b> " + DATA.players.length + " ranked + rails</span>";

    if (DATA.league) {
      document.getElementById("eyebrow").textContent += " · " + DATA.league;
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
        i.status.split("|").map(esc).join("<br>") + "</span></div>";
    }).join("");

    document.getElementById("slp").innerHTML = DATA.sleepers.map(function (e) {
      var tag = e.team ? esc(e.pos) + " " + esc(e.team) : esc(e.pos);
      return '<li><span class="l-nm">' + esc(e.name) + ' <span class="p">' + tag + "</span></span>" +
        '<span class="l-why">' + esc(e.why) + "</span></li>";
    }).join("");

    var adpEl = document.getElementById("adpnote");
    if (DATA.adp) {
      adpEl.textContent = "ADP and bye weeks: " + DATA.adp.source + " — " + DATA.adp.format +
        ", " + DATA.adp.window + ". Survival odds assume a normal draft position with that spread.";
    } else {
      adpEl.textContent = "";
    }

    document.getElementById("srcs").innerHTML = DATA.sources.map(function (s) {
      return '<a href="' + esc(s.url) + '">' + esc(s.label) + "</a>";
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
        b.dataset.nm = p.name.toLowerCase();
        b.innerHTML =
          '<span class="rk">' + p.rank + "</span>" +
          '<span class="who"><span class="nm">' + esc(p.name) + "</span>" +
          '<span class="pos">' + esc(p.pos) + " · " + esc(p.team) + "</span>" +
          (p.note ? '<span class="note">' + esc(p.note) + "</span>" : "") + "</span>" +
          '<span class="flags"><span class="oddsslot"></span>' +
          (p.flag ? '<span class="flag f-' + p.flag + '">' + FLAG_LABEL[p.flag] + "</span>" : "") +
          "</span>";
        b.addEventListener("click", function () {
          toggle(p.rank);
          paint();
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
    var q = document.getElementById("search").value.trim().toLowerCase();
    var current = log.length + 1;                 // every taken player crossed off => this is the pick on the clock
    var mine = nextPicks(current, 2);
    paintPickInfo(current, mine);

    Array.prototype.forEach.call(document.querySelectorAll(".row"), function (r) {
      var isGone = gone.has(r.dataset.rk);
      r.classList.toggle("gone", isGone);
      r.setAttribute("aria-pressed", isGone ? "true" : "false");
      var okPos = posFilter === "ALL" || r.dataset.pos === posFilter;
      var okQ = !q || r.dataset.nm.indexOf(q) !== -1;
      r.classList.toggle("hide", !(okPos && okQ));
      r.querySelector(".oddsslot").innerHTML = isGone ? "" : oddsHtml(byRank(Number(r.dataset.rk)), mine);
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
    var q = document.getElementById("search").value.trim().toLowerCase();
    if (!q) return;
    var matches = Array.prototype.filter.call(document.querySelectorAll(".row:not(.hide)"), function (r) {
      return r.dataset.nm.indexOf(q) !== -1;
    });
    if (matches.length === 1) {
      toggle(Number(matches[0].dataset.rk));
      document.getElementById("search").value = "";
    } else if (matches.length === 0) {
      say("No player matches “" + q + "”.");
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

  document.getElementById("print").addEventListener("click", function () { window.print(); });

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
    var setPill = function (on) {
      pill.hidden = false;
      pill.classList.toggle("off", !on);
      pill.textContent = on ? "Live · following the server" : "Live · reconnecting…";
    };
    var es = new EventSource("/events");
    es.addEventListener("open", function () { setPill(true); });
    es.addEventListener("error", function () { setPill(false); });
    es.addEventListener("state", function (ev) {
      var st = JSON.parse(ev.data);
      log = (st.picks || []).map(function (p) { return { rank: p.rank, ts: p.ts || null }; });
      rebuildIndex(); save(); paint();
    });
    es.addEventListener("pick", function (ev) {
      var p = JSON.parse(ev.data);
      if (crossOff(p.rank, true)) {
        save();
        var pl = byRank(p.rank);
        if (pl && p.source !== "board") say(pl.name + " crossed off" + (p.source ? " (" + p.source + ")" : "") + ".");
        paint();
      }
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

  buildShell();
  buildBoard();
  paint();
  booted = true;
})();
