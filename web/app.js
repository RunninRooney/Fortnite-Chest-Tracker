/* Squad Chests: a read-only view of a Chest Tracker team (live squad map + squad numbers).

   Reads the trackers' team relay (public MQTT brokers, over WebSockets) under
   chesttracker/v3/<team channel>/, the same messages chest_tracker.NetLink sends, each locked with the
   team code (crypto.js, like the tracker's teamcrypt.py):
     state/<id>   name, game, today, total, fortnite_names, guests (console teammates)  (retained)
     status/<id>  online / offline                                                       (retained)
     map/<id>     position on the 2048 px map during matches, every 3 s (map_tracker.team_message)
     last/<id>    retained: their whole last match, once it's over (the post-game view)
   The team code never leaves this page: the channel name and the key are both made from it here.
   Nothing is ever published. Squad only: the relay carries nothing about other players.
   The map itself is map.js (window.SquadMap). */
(function () {
  "use strict";

  // Brokers that the trackers use (chest_tracker.py cfg "mqtt_brokers"), on their WebSocket ports.
  var BROKERS = [
    "wss://broker.hivemq.com:8884/mqtt",
    "wss://broker.emqx.io:8084/mqtt",
    "wss://test.mosquitto.org:8081/"
  ];
  var NET_TIMEOUT = 100;      // s without a heartbeat before a tracker counts as offline (Team.NET_TIMEOUT)
  // the Chest Tracker map's colours (map_window.py ME, then MATES), then a few more
  var COLORS = ["#ffc53d", "#50c8ff", "#ff6eb4", "#82e178", "#b996ff", "#ff965a", "#40cee2", "#e6f6fc"];
  var STORE = "squadchests.code";

  var $ = function (id) { return document.getElementById(id); };
  var clients = [];
  var key = "";        // the team's relay channel
  var secret = null;   // the key that unlocks its messages
  var peers = {};      // tracker id -> {name, game, total, today, ts, online, fortnite_names, guests, hasState}
  var colors = {};
  var startedAt = 0;
  var drawQueued = false;
  var haveSquad = false;
  window.SquadChestsHaveSquad = function () { return haveSquad; };

  // ---------------------------------------------------------------- team code (teamcrypt.py / crypto.js)
  var normalizeCode = window.TeamCrypt.normalizeCode;

  // ---------------------------------------------------------------- team messages (chest_tracker.Team)
  function count(v, max) {
    var n = Number(v);
    if (!isFinite(n)) return 0;
    return Math.max(0, Math.min(max || 10000000, Math.trunc(n)));
  }
  function normName(n) { return String(n == null ? "" : n).replace(/\s+/g, "").toLowerCase(); }
  function isObj(v) { return v !== null && typeof v === "object" && !Array.isArray(v); }
  function nowS() { return Date.now() / 1000; }
  function localMidnight() { var d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime() / 1000; }

  function byMatch(v) { // chest_tracker._by_match
    if (!isObj(v)) return null;
    var out = {};
    Object.keys(v).slice(0, 300).forEach(function (k) {
      var x = v[k];
      if (!Array.isArray(x) || x.length < 2) return;
      var c = Number(x[0]), st = Number(x[1]);
      if (!isFinite(c) || !isFinite(st)) return;
      out[String(k).slice(0, 16)] = [Math.max(0, Math.min(Math.trunc(c), 500)), Math.trunc(st)];
    });
    return out;
  }

  function isOnline(p) { return p.online && Math.abs(nowS() - p.ts) < NET_TIMEOUT; }

  function onTeamMessage(m) { // Team.update(m, "net")
    if (!isObj(m) || m.app !== "chesttracker") return;
    var pid = String(m.id == null ? "" : m.id);
    if (!/^[0-9a-f]{4,32}$/.test(pid) || String(m.team || "") !== key) return;
    var p = peers[pid] || { name: "", game: 0, total: 0, today: null, ts: 0, online: false, hasState: false };
    var ts = typeof m.ts === "number" ? m.ts : 0;
    if ("total" in m) {
      p.hasState = true;
      p.name = String(m.name == null ? "" : m.name).slice(0, 20);
      p.game = count(m.game);
      p.total = count(m.total);
      p.today = "today" in m ? count(m.today) : null;
      if (Array.isArray(m.fortnite_names)) {
        p.fortnite_names = m.fortnite_names.slice(0, 6).filter(function (n) { return typeof n === "string"; })
          .map(function (n) { return normName(n).slice(0, 32); });
      }
      if (isObj(m.guests)) {
        var old = p.guests || {}, gs = {};
        Object.keys(m.guests).slice(0, 10).forEach(function (n) {
          var g = m.guests[n], k = normName(n).slice(0, 32);
          if (!isObj(g) || !normName(n)) return;
          gs[k] = {
            name: String(g.name !== undefined ? g.name : n).slice(0, 32),
            game: count(g.game), total: count(g.total), today: count(g.today),
            platform: String(g.platform == null ? "" : g.platform).slice(0, 8),
            by_match: "by_match" in g ? byMatch(g.by_match) : (old[k] || {}).by_match || null
          };
        });
        p.guests = gs;
      }
    }
    if (ts >= p.ts) { p.ts = ts; p.online = m.online === undefined ? true : !!m.online; }
    peers[pid] = p;
    queueDraw();
  }

  function claimedNames() {
    var out = {};
    Object.keys(peers).forEach(function (id) { (peers[id].fortnite_names || []).forEach(function (n) { out[n] = id; }); });
    return out;
  }

  function hasByMatch(g) { return g.by_match && Object.keys(g.by_match).length > 0; }

  function mergeGuest(sources) { // chest_tracker.merge_guest: [[guest, online], ...]
    var best = sources[0][0];
    sources.forEach(function (s) { if ((s[0].total || 0) > (best.total || 0)) best = s[0]; });
    var out = Object.assign({}, best, { online: sources.some(function (s) { return s[1]; }) });
    var lists = sources.filter(function (s) { return hasByMatch(s[0]); }).map(function (s) { return s[0].by_match; });
    if (lists.length) {
      var union = {};
      lists.forEach(function (bm) {
        Object.keys(bm).forEach(function (m) { if (!union[m] || bm[m][0] > union[m][0]) union[m] = bm[m]; });
      });
      var midnight = localMidnight(), sum = 0, today = 0, ms = Object.keys(union);
      ms.forEach(function (m) { sum += union[m][0]; if (union[m][1] >= midnight) today += union[m][0]; });
      var totals = sources.filter(function (s) { return !hasByMatch(s[0]); }).map(function (s) { return s[0].total || 0; });
      out.total = Math.max.apply(null, [sum].concat(totals));
      out.today = Math.max.apply(null, [today].concat(sources.map(function (s) { return s[0].today || 0; })));
    }
    out.game = Math.max.apply(null, [0].concat(sources.filter(function (s) { return s[1]; }).map(function (s) { return s[0].game || 0; })));
    delete out.by_match;
    return out;
  }

  function guests() { // Team.guests(), without a "this PC" of our own
    var claimed = claimedNames(), sources = {};
    Object.keys(peers).forEach(function (id) {
      var p = peers[id], on = isOnline(p), gs = p.guests || {};
      Object.keys(gs).forEach(function (n) { if (!(n in claimed)) (sources[n] = sources[n] || []).push([gs[n], on]); });
    });
    var out = {};
    Object.keys(sources).forEach(function (n) { out[n] = mergeGuest(sources[n]); });
    return out;
  }

  var PLATFORM = { PSN: "PlayStation", PS5: "PlayStation", PS4: "PlayStation", XBL: "Xbox", XSX: "Xbox", XB1: "Xbox", SWT: "Switch" };

  function players() { // Team.players(), minus "me"
    var midnight = localMidnight(), list = [];
    Object.keys(peers).forEach(function (id) {
      var p = peers[id];
      if (!p.hasState) return;
      list.push({ key: id, name: p.name || "?", game: p.game, total: p.total,
                  today: p.today === null ? null : (p.ts >= midnight ? p.today : 0),
                  online: isOnline(p), kind: "Chest Tracker" });
    });
    var gs = guests();
    Object.keys(gs).forEach(function (n) {
      var g = gs[n];
      list.push({ key: "g:" + n, name: g.name, game: g.game, total: g.total, today: g.today || 0, online: !!g.online,
                  kind: PLATFORM[String(g.platform || "").toUpperCase()] || "Console", guest: true });
    });
    list.sort(function (a, b) { return (a.online === b.online ? 0 : a.online ? -1 : 1) || b.total - a.total || a.name.localeCompare(b.name); });
    return list;
  }

  function colorFor(k) {
    if (!colors[k]) colors[k] = COLORS[Object.keys(colors).length % COLORS.length];
    return colors[k];
  }

  function mapCtx() {
    return { key: key, claimed: claimedNames(), colorFor: colorFor,
             peerName: function (id) { return peers[id] && peers[id].hasState ? peers[id].name : ""; } };
  }
  function onMapMessage(m) { window.SquadMap.onMessage(m, mapCtx()); }
  function onLastMatch(m) { window.SquadMap.onLast(m, mapCtx()); }

  // ---------------------------------------------------------------- relay connection (read-only)
  function connect() {
    disconnect();
    window.SquadMap.restore(mapCtx());  // the last match this browser saw, until the relay says more
    startedAt = Date.now();
    var base = "chesttracker/v3/" + key;
    var id = "ct-web-" + Math.random().toString(16).slice(2, 10);
    BROKERS.forEach(function (url) {
      var c;
      try {
        c = mqtt.connect(url, { clientId: id, clean: true, keepalive: 30, reconnectPeriod: 4000,
                                connectTimeout: 12000, protocolVersion: 4 });
      } catch (e) { return; }
      c.ok = false;
      c.on("connect", function () {
        if (window.console) console.info("relay connected", url, Date.now() - startedAt, "ms");
        c.ok = true;
        c.subscribe([base + "/state/+", base + "/status/+", base + "/map/+", base + "/last/+"], { qos: 0 });
        queueDraw();
      });
      c.on("close", function () { c.ok = false; queueDraw(); });
      c.on("error", function (e) { if (window.console) console.info("relay", url, e && e.message); });
      c.on("message", function (topic, payload) {
        var m = window.TeamCrypt.unlock(secret, payload);
        if (!m || typeof m !== "object") return;  // not locked with this team code
        if (topic.indexOf("/map/") >= 0) onMapMessage(m);
        else if (topic.indexOf("/last/") >= 0) onLastMatch(m);
        else onTeamMessage(m);
      });
      clients.push(c);
    });
  }

  function disconnect() {
    clients.forEach(function (c) { try { c.end(true); } catch (e) { /* ignore */ } });
    clients = []; peers = {}; colors = {};
    window.SquadMap.reset();
  }

  // ---------------------------------------------------------------- drawing
  function fmt(n) { return n === null || n === undefined ? "–" : Number(n).toLocaleString(); }

  function queueDraw() {
    if (drawQueued) return;
    drawQueued = true;
    requestAnimationFrame(function () { drawQueued = false; draw(); });
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function statsBlock(game, today, total) {
    var s = el("div", "stats");
    [[game, "this game"], [today, "today"], [total, "all time"]].forEach(function (v) {
      var d = el("div", v[1] === "all time" ? "all-time" : ""); d.appendChild(el("b", "", fmt(v[0]))); d.appendChild(el("span", "", v[1])); s.appendChild(d);
    });
    return s;
  }

  function draw() {
    var connected = clients.some(function (c) { return c.ok; });
    var conn = $("conn");
    conn.hidden = false;
    if (connected) { conn.textContent = "Connected"; conn.className = "pill pill-ok"; }
    else if (Date.now() - startedAt > 15000) { conn.textContent = "Can't reach relay"; conn.className = "pill pill-bad"; }
    else { conn.textContent = "Connecting"; conn.className = "pill pill-wait"; }

    // numbers
    var list = players();
    // colours stay put once given: trackers first (most chests first), then console teammates
    list.slice().sort(function (a, b) { return (a.guest ? 1 : 0) - (b.guest ? 1 : 0) || b.total - a.total; })
      .forEach(function (p) { colorFor(p.key); });
    // Fortnite only lets the replay be read once a match ends, so console players' numbers (and positions) arrive
    // after the match: during one, their "this game" is last game's and is left out
    var inMatch = window.SquadMap.isLive();
    var game = 0, today = 0, total = 0;
    list.forEach(function (p) {
      if (p.online && !(p.guest && inMatch)) game += p.game;
      today += p.today || 0; total += p.total;
    });
    window.SquadMap.setConsole(list.some(function (p) { return p.guest; }));
    $("teamGame").textContent = fmt(game);
    $("teamToday").textContent = fmt(today);
    $("teamTotal").textContent = fmt(total);
    var ul = $("members");
    ul.textContent = "";
    list.forEach(function (p) {
      var li = el("li", "member" + (p.online ? "" : " offline"));
      var who = el("div", "who"), sw = el("span", "swatch"), txt = el("div", "who-text");
      sw.style.background = colorFor(p.key);
      txt.appendChild(el("div", "name", p.name));
      var sub = el("div", "sub");
      var later = p.guest && inMatch;
      sub.appendChild(later ? el("span", "later", "updates after the match")
                            : el("span", p.online ? "on" : "", p.online ? "● online" : "offline"));
      if (!later) sub.appendChild(el("span", "", "· " + p.kind));
      txt.appendChild(sub);
      who.appendChild(sw); who.appendChild(txt);
      li.appendChild(who);
      li.appendChild(statsBlock(p.online && !later ? p.game : null, p.today, p.total));
      ul.appendChild(li);
    });
    var empty = $("empty");
    if (!list.length && connected && Date.now() - startedAt > 8000) {
      empty.hidden = false;
      empty.textContent = "No one has shared with this team code yet. Check it's the same code as in Chest Tracker's settings (spaces and capitals don't matter).";
    } else if (!list.length) {
      empty.hidden = false;
      empty.textContent = connected ? "Loading the squad…" : "Connecting to the relay…";
    } else empty.hidden = true;
    $("guestNote").hidden = !list.some(function (p) { return p.guest; });

    haveSquad = list.length > 0;
    window.SquadMap.redraw();
  }

  // ---------------------------------------------------------------- code, URL fragment, menu
  function readHash() {
    var h = location.hash.replace(/^#/, "");
    if (/^code=/i.test(h)) h = h.slice(5);
    try { return decodeURIComponent(h).trim(); } catch (e) { return h.trim(); }
  }
  function store(v) { try { if (v) localStorage.setItem(STORE, v); else localStorage.removeItem(STORE); } catch (e) { /* private mode */ } }
  function stored() { try { return localStorage.getItem(STORE) || ""; } catch (e) { return ""; } }

  function showCode() {
    disconnect(); key = "";
    $("liveView").hidden = true; $("codeView").hidden = false;
    $("conn").hidden = true; $("menuBtn").hidden = true; $("menu").hidden = true;
    $("codeInput").value = "";
    setTimeout(function () { $("codeInput").focus(); }, 50);
  }

  function start(code) {
    window.TeamCrypt.secrets(code).then(function (sec) {
      var k = sec.topic;
      if (!k) { showCode(); return; }
      if (k === key && clients.length) return;
      key = k; secret = sec.key;
      store(code);
      $("codeView").hidden = true; $("liveView").hidden = false; $("menuBtn").hidden = false;
      if (typeof mqtt === "undefined") { toast("Couldn't load the relay library. Check the connection and reload."); return; }
      connect();
      queueDraw();
    }, function (e) { toast(e && e.message || "Couldn't unlock the squad's messages."); });
  }

  function route() {
    var code = readHash();
    if (normalizeCode(code)) { start(code); return; }
    var s = stored();
    if (normalizeCode(s)) { history.replaceState(null, "", "#" + encodeURIComponent(s)); start(s); return; }
    showCode();
  }

  var toastTimer;
  function toast(text) {
    var t = $("toast");
    t.textContent = text; t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.hidden = true; }, 3500);
  }

  $("codeForm").addEventListener("submit", function (e) {
    e.preventDefault();
    var code = $("codeInput").value.trim();
    if (!normalizeCode(code)) { toast("Type the team code from Chest Tracker."); return; }
    location.hash = encodeURIComponent(code);  // hashchange starts it
    if (readHash() === code) start(code);
  });
  window.addEventListener("hashchange", route);
  $("menuBtn").addEventListener("click", function (e) { e.stopPropagation(); $("menu").hidden = !$("menu").hidden; });
  document.addEventListener("click", function () { $("menu").hidden = true; });
  $("changeBtn").addEventListener("click", function () { store(""); history.replaceState(null, "", location.pathname + location.search); showCode(); });
  $("shareBtn").addEventListener("click", function () {
    var url = location.href;
    if (navigator.share) { navigator.share({ title: "Squad Chests", url: url }).catch(function () {}); return; }
    if (navigator.clipboard) navigator.clipboard.writeText(url).then(function () { toast("Link copied. It includes the team code, so only share it with the squad."); });
  });
  window.SquadMap.init();

  setInterval(function () { if (key) queueDraw(); }, 1000);
  route();

  // handy from the browser console
  window.SquadChests = { normalizeCode: normalizeCode, mergeGuest: mergeGuest };
})();
