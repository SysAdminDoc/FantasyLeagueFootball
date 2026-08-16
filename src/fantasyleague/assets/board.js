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

  var gone = new Set();
  try {
    var saved = localStorage.getItem(KEY);
    if (saved) gone = new Set(JSON.parse(saved));
  } catch (e) { storageFailed(); }

  function save() {
    try { localStorage.setItem(KEY, JSON.stringify([].concat(Array.from(gone)))); } catch (e) { storageFailed(); }
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

  function toggle(rank) {
    var k = String(rank);
    var p = byRank(rank);
    if (gone.has(k)) {
      gone.delete(k);
      say(p.name + " restored.");
    } else {
      gone.add(k);
      say(p.name + " crossed off.");
    }
    save();
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
          '<span class="flags">' +
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

  function paint() {
    var q = document.getElementById("search").value.trim().toLowerCase();

    Array.prototype.forEach.call(document.querySelectorAll(".row"), function (r) {
      var isGone = gone.has(r.dataset.rk);
      r.classList.toggle("gone", isGone);
      r.setAttribute("aria-pressed", isGone ? "true" : "false");
      var okPos = posFilter === "ALL" || r.dataset.pos === posFilter;
      var okQ = !q || r.dataset.nm.indexOf(q) !== -1;
      r.classList.toggle("hide", !(okPos && okQ));
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
            '<span class="p">' + esc(p.pos) + " " + esc(p.team) + "</span></div>";
        }).join("")
      : '<div class="ba-item"><span class="nm2" style="color:var(--ink-dim)">Board cleared.</span></div>';

    var total = posFilter === "ALL"
      ? DATA.players.length
      : DATA.players.filter(function (p) { return p.pos === posFilter; }).length;

    document.getElementById("tally").innerHTML =
      "<b>" + avail.length + "</b> of " + total + " on the board";

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

  document.getElementById("reset").addEventListener("click", function () {
    gone.clear();
    save();
    document.getElementById("search").value = "";
    say("Board reset. " + DATA.players.length + " players on the board.");
    paint();
  });

  buildShell();
  buildBoard();
  paint();
  booted = true;
})();
