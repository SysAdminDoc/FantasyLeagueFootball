/* Draft board runtime. DATA is injected by render.py as a JSON literal. */
(function () {
  "use strict";

  var TIER_BREAK = DATA.tier_break;   // single source of truth: board.TIER_BREAK_THRESHOLD
  var FLAG_LABEL = { value: "Value", avoid: "Reach", watch: "Watch" };
  var KEY = "ff-warroom-" + DATA.season;

  var gone = new Set();
  try {
    var saved = localStorage.getItem(KEY);
    if (saved) gone = new Set(JSON.parse(saved));
  } catch (e) { /* private mode — fall back to in-memory only */ }

  function save() {
    try { localStorage.setItem(KEY, JSON.stringify([].concat(Array.from(gone)))); } catch (e) {}
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var posFilter = "ALL";
  var board = document.getElementById("board");

  /* ---------- static shell built from DATA ---------- */

  function buildShell() {
    document.getElementById("settings").innerHTML =
      "<span><b>Format</b> Yahoo default</span>" +
      "<span><b>Scoring</b> 0.5 PPR</span>" +
      "<span><b>QB</b> Single</span>" +
      "<span><b>Board</b> " + DATA.players.length + " ranked + rails</span>";

    document.getElementById("plan").innerHTML = DATA.plan.map(function (p) {
      return '<div class="plan-cell"><h3>' + esc(p.position) + "</h3><p>" + p.guidance + "</p></div>";
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
          var k = String(p.rank);
          if (gone.has(k)) gone.delete(k); else gone.add(k);
          save();
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
  }

  /* ---------- wiring ---------- */

  document.getElementById("chips").addEventListener("click", function (e) {
    var b = e.target.closest(".chip");
    if (!b) return;
    posFilter = b.dataset.pos;
    Array.prototype.forEach.call(document.querySelectorAll(".chip"), function (c) {
      c.setAttribute("aria-pressed", c === b ? "true" : "false");
    });
    paint();
  });

  document.getElementById("search").addEventListener("input", paint);

  document.getElementById("reset").addEventListener("click", function () {
    gone.clear();
    save();
    document.getElementById("search").value = "";
    paint();
  });

  buildShell();
  buildBoard();
  paint();
})();
