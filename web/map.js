/* Squad Chests: the live squad map, drawn like Chest Tracker's own live map (map_window.py):
   each squadmate's route, dots where they opened chests, a disc with a heading arrow, a name tag.
   A timeline scrubs back through the match; full screen with drag / pinch / wheel zoom.

   Data: the trackers' map messages (map_tracker.team_message), every 3 s during a match:
     {type:"map", id, name, match, t, pos:[x,y], chests:[[x,y]...],
      squad:[{name,pos,t}]  console squadmates from that tracker's replay (1.16+),
      hist:{start, route:[[s,x,y]], chests:[[s,x,y]]}  the match so far, every 15 s (1.16+)}
   and, kept on the relay (retained) once a player's match is over, last/<id>:
     {type:"last", id, name, match, end, game, hist:{...as above, the whole match}}
   so a phone that opens (or reloads) the page after the match still gets the post-game view.
   The finished match is also kept in this browser (localStorage) until the next one.
   Positions are pixels on fortnite-api.com's 2048 px map.
   Squad only: nothing here knows about other players, the storm or unopened chests. */
(function () {
  "use strict";

  var MAP_SIZE = 2048;
  var JUMP_PX = 150;          // a step this long starts a new route piece (map_tracker.JUMP_PX)
  var LIVE_FOR = 20;          // s after the last message that the match counts as live
  var STALE = 15;             // s: a player marker fades (map_window: 15 s)
  var FORGET = 12 * 3600;     // s after it ended: a finished match is cleared
  var SAVE = "squadchests.last.";
  var DARK = "rgba(16,17,28,";
  var PLAY_SPEED = 8;

  var $ = function (id) { return document.getElementById(id); };
  var tracks = {};            // key -> {name, replay, match, pts:[[t,x,y]], chests:[[t,x,y]], chestT:{}, histT, lastRecv}
  var colorFor = function () { return "#ffc53d"; };
  var lastT = 0, lastRecv = 0;  // newest message time (sender clock) and when it arrived (ms, this clock)
  var timeline = { live: true, T: 0, playing: false };
  var cam = { cx: 1024, cy: 1024, s: 0.2, ready: false };
  var mode = "follow";        // follow | island | free
  var disp = {};              // smoothed marker positions while live
  var img = new Image();
  var canvas, ctx, W = 1, H = 1, dpr = 1;
  var full = false;
  var dirty = true, lastFrame = 0, ticksBuilt = 0;

  // ------------------------------------------------------------------ messages
  function num(v) { var n = Number(v); return isFinite(n) ? n : null; }
  function xy(p) {
    if (!Array.isArray(p) || p.length < 2) return null;
    var x = num(p[0]), y = num(p[1]);
    return x === null || y === null || x < 0 || y < 0 || x > MAP_SIZE || y > MAP_SIZE ? null : [x, y];
  }
  function track(k, match) {
    var tr = tracks[k];
    if (!tr || tr.match !== match) { tr = tracks[k] = { pts: [], chests: [], chestT: {}, histT: -Infinity, match: match }; delete disp[k]; }
    return tr;
  }
  function addPoint(tr, t, p) {
    var last = tr.pts[tr.pts.length - 1];
    if (last && t <= last[0]) return;
    tr.pts.push([t, p[0], p[1]]);
    if (tr.pts.length > 2000) tr.pts.splice(0, tr.pts.length - 2000);
  }

  /* ctx: {key, peerName(id), claimed: {fortnite name: tracker id}, colorFor} */
  function onMessage(m, c) {
    if (!m || typeof m !== "object" || m.type !== "map" || m.team !== c.key) return;
    var sender = String(m.id == null ? "" : m.id).slice(0, 32), pos = xy(m.pos), t = num(m.t);
    if (!sender || !pos || t === null) return;
    colorFor = c.colorFor;
    var match = String(m.match == null ? "" : m.match).slice(0, 64);
    if (tracks[sender] && tracks[sender].over && tracks[sender].match === match) return;  // a late one after "last"
    var tr = track(sender, match);
    tr.name = c.peerName(sender) || String(m.name || "Teammate").slice(0, 20);
    tr.replay = false;
    tr.lastRecv = Date.now();
    var first = !tr.heard;  // chests in the first message we hear were opened earlier, time unknown (until "hist")
    tr.heard = true;

    // the match so far (sent every 15 s): fills in the timeline for someone who opened the page late
    applyHist(tr, m.hist);
    addPoint(tr, t, pos);

    // where they opened chests: their newest 40 (times from "hist", or when first seen here)
    var list = Array.isArray(m.chests) ? m.chests.slice(-40) : [], seen = {}, out = [];
    list.forEach(function (q) {
      var p = xy(q);
      if (!p) return;
      var k = p[0] + "," + p[1];
      if (!(k in tr.chestT)) tr.chestT[k] = first ? -Infinity : t;
      seen[k] = (seen[k] || 0) + 1;
      out.push([tr.chestT[k], p[0], p[1]]);
    });
    if (list.length >= 40) {  // older ones than the 40 in the message: keep the ones "hist" told us about
      Object.keys(tr.chestT).forEach(function (k) {
        if (seen[k]) return;
        var a = k.split(",");
        out.push([tr.chestT[k], +a[0], +a[1]]);
      });
    }
    tr.chests = out.sort(function (a, b) { return a[0] - b[0]; });

    // console squadmates from this tracker's replay (about 20 s behind)
    if (Array.isArray(m.squad)) {
      m.squad.slice(0, 4).forEach(function (s) {
        if (!s || typeof s !== "object") return;
        var gp = xy(s.pos), n = String(s.name == null ? "" : s.name).replace(/\s+/g, "").toLowerCase(), st = num(s.t);
        if (!gp || !n || st === null) return;
        var owner = c.claimed[n], k = owner || "g:" + n;
        var mine = tracks[k];
        if (mine && !mine.replay && Date.now() - mine.lastRecv < STALE * 1000) return; // they share it themselves
        var g = track(k, match);
        g.name = (owner && c.peerName(owner)) || String(s.name).slice(0, 32);
        g.replay = true;
        g.lastRecv = Date.now();
        addPoint(g, st, gp);
      });
    }
    if (t > lastT) { lastT = t; lastRecv = Date.now(); }
    dirty = true;
  }

  // {start, route:[[s,x,y]], chests:[[s,x,y]]}: the match so far (or all of it, in "last")
  function applyHist(tr, h) {
    if (!h || typeof h !== "object" || num(h.start) === null) return false;
    var start = num(h.start), hp = [];
    (Array.isArray(h.route) ? h.route : []).slice(0, 1200).forEach(function (q) {
      var p = Array.isArray(q) ? xy([q[1], q[2]]) : null, s = Array.isArray(q) ? num(q[0]) : null;
      if (p && s !== null) hp.push([start + s, p[0], p[1]]);
    });
    if (hp.length) {
      var upto = hp[hp.length - 1][0];
      tr.pts = hp.concat(tr.pts.filter(function (q) { return q[0] > upto; }));
      tr.histT = upto;
    }
    tr.start = start;
    (Array.isArray(h.chests) ? h.chests : []).slice(0, 300).forEach(function (q) {
      var p = Array.isArray(q) ? xy([q[1], q[2]]) : null, s = Array.isArray(q) ? num(q[0]) : null;
      if (p && s !== null) tr.chestT[p[0] + "," + p[1]] = start + s;
    });
    return true;
  }

  /* last/<id> (retained): a squadmate's whole match, sent once it's over. Drives the post-game view, also for a
     page opened after the match. */
  function onLast(m, c) {
    if (!m || typeof m !== "object" || m.type !== "last" || m.team !== c.key) return;
    var sender = String(m.id == null ? "" : m.id).slice(0, 32), end = num(m.end);
    if (!sender || end === null || Date.now() / 1000 - end > FORGET) return;
    var match = String(m.match == null ? "" : m.match).slice(0, 64), old = tracks[sender];
    if (old && old.match !== match && old.pts.length && old.pts[old.pts.length - 1][0] > end) return;  // a newer match is on
    colorFor = c.colorFor;
    var tr = track(sender, match);
    if (!applyHist(tr, m.hist) || !tr.pts.length) { if (!tr.pts.length) delete tracks[sender]; return; }
    tr.name = c.peerName(sender) || String(m.name || "Teammate").slice(0, 20);
    tr.replay = false; tr.heard = true; tr.over = true; tr.end = end;
    if (num(m.game) !== null) tr.game = num(m.game);
    tr.chests = Object.keys(tr.chestT).map(function (k) { var a = k.split(","); return [tr.chestT[k], +a[0], +a[1]]; })
      .sort(function (a, b) { return a[0] - b[0]; });
    dirty = true;
    save();
  }

  // ------------------------------------------------------------------ the finished match, kept in this browser
  var saveKey = "";
  function save() {
    var ks = keys();
    if (!saveKey || !ks.length) return;
    var out = {};
    ks.forEach(function (k) {
      var tr = tracks[k];
      out[k] = { name: tr.name, replay: !!tr.replay, match: tr.match, start: tr.start || null, end: tr.end || null,
                 pts: tr.pts.map(function (q) { return [Math.round(q[0] * 10) / 10, Math.round(q[1]), Math.round(q[2])]; }),
                 chests: tr.chests.map(function (q) { return [isFinite(q[0]) ? Math.round(q[0] * 10) / 10 : null, q[1], q[2]]; }) };
    });
    try { localStorage.setItem(SAVE + saveKey, JSON.stringify({ v: 1, tracks: out })); } catch (e) { /* private mode, full */ }
  }
  /* c: {key, colorFor}. Brings back the last match this browser saw for this team (until it's FORGET old). */
  function restore(c) {
    saveKey = c.key || ""; colorFor = c.colorFor || colorFor;
    var d = null;
    try { d = JSON.parse(localStorage.getItem(SAVE + saveKey) || "null"); } catch (e) { d = null; }
    if (!d || d.v !== 1 || !d.tracks || typeof d.tracks !== "object") return;
    Object.keys(d.tracks).slice(0, 12).forEach(function (k) {
      var s = d.tracks[k];
      if (tracks[k] || !s || !Array.isArray(s.pts)) return;
      var tr = { name: String(s.name || "Teammate").slice(0, 32), replay: !!s.replay, match: String(s.match || "").slice(0, 64),
                 pts: [], chests: [], chestT: {}, histT: -Infinity, heard: true, lastRecv: 0,
                 start: num(s.start) || undefined, end: num(s.end) || undefined };
      s.pts.slice(0, 3000).forEach(function (q) {
        var p = Array.isArray(q) ? xy([q[1], q[2]]) : null, t = Array.isArray(q) ? num(q[0]) : null;
        if (p && t !== null) addPoint(tr, t, p);
      });
      (Array.isArray(s.chests) ? s.chests : []).slice(0, 300).forEach(function (q) {
        if (!Array.isArray(q)) return;
        var p = xy([q[1], q[2]]), t = q[0] === null ? -Infinity : num(q[0]);
        if (!p || t === null) return;
        tr.chestT[p[0] + "," + p[1]] = t;
        tr.chests.push([t, p[0], p[1]]);
      });
      if (tr.pts.length) tracks[k] = tr;
    });
    if (!forget()) dirty = true;
  }
  function forget() {  // true if the finished match on show was old enough to clear
    var ks = keys();
    if (!ks.length || isLive() || Date.now() / 1000 - endT(ks) <= FORGET) return false;
    reset();
    try { localStorage.removeItem(SAVE + saveKey); } catch (e) { /* ignore */ }
    return true;
  }

  function reset() {
    tracks = {}; disp = {}; lastT = 0; lastRecv = 0; timeline = { live: true, T: 0, playing: false };
    cam.ready = false; cam.anim = false; wasLive = false; dirty = true;
  }

  // ------------------------------------------------------------------ time
  function nowT() { return lastT ? lastT + (Date.now() - lastRecv) / 1000 : 0; }
  // live while some squadmate's tracker is still sending (and hasn't said its match is over)
  function isLive() {
    var now = Date.now();
    return Object.keys(tracks).some(function (k) {
      var tr = tracks[k];
      return !tr.over && !tr.replay && tr.lastRecv && now - tr.lastRecv < LIVE_FOR * 1000;
    });
  }
  function firstT(tr) { return Math.min(tr.start || Infinity, tr.pts[0][0]); }
  function lastPtT(tr) { return tr.pts[tr.pts.length - 1][0]; }
  // the squad's newest match only: a squadmate's older match (or a saved one) never mixes into it
  function keys() {
    var ks = Object.keys(tracks).filter(function (k) { return tracks[k].pts.length; }), newest = null;
    ks.forEach(function (k) { if (!newest || lastPtT(tracks[k]) > lastPtT(newest)) newest = tracks[k]; });
    return newest ? ks.filter(function (k) { return lastPtT(tracks[k]) >= firstT(newest) - 120; }) : ks;
  }
  // when the match ended: the last position anyone had ("last"'s end is when it was sent, which can be later)
  function endT(ks) {
    return ks.reduce(function (e, k) { return Math.max(e, lastPtT(tracks[k])); }, 0);
  }
  function clock(t) {
    var d = new Date(t * 1000), hm = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    return d.toDateString() === new Date().toDateString() ? hm : d.toLocaleDateString([], { weekday: "short" }) + " " + hm;
  }
  function span() {
    var ks = keys(), t0 = Infinity, t1 = -Infinity;
    ks.forEach(function (k) {
      var tr = tracks[k];
      t0 = Math.min(t0, tr.start || tr.pts[0][0], tr.pts[0][0]);
      t1 = Math.max(t1, tr.pts[tr.pts.length - 1][0]);
    });
    if (!ks.length) return null;
    if (isLive()) t1 = Math.max(t1, nowT());
    return [t0, Math.max(t1, t0 + 1)];
  }
  function curT() {
    var sp = span();
    if (!sp) return 0;
    if (timeline.live) return sp[1];
    return Math.max(sp[0], Math.min(sp[1], timeline.T));
  }
  function fmt(s) { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2); }

  // position at time T: [x, y, heading or null, age of the last point]
  function at(tr, T) {
    var p = tr.pts, n = p.length;
    if (!n || T < p[0][0]) return null;
    var i = n - 1;
    if (T < p[n - 1][0]) { i = 0; var lo = 0, hi = n - 1; while (lo < hi) { var mid = (lo + hi + 1) >> 1; if (p[mid][0] <= T) lo = mid; else hi = mid - 1; } i = lo; }
    var a = p[i], b = p[i + 1], x = a[1], y = a[2];
    if (b && b[0] > a[0] && Math.abs(b[1] - a[1]) + Math.abs(b[2] - a[2]) < JUMP_PX) {
      var f = (T - a[0]) / (b[0] - a[0]);
      x = a[1] + (b[1] - a[1]) * f; y = a[2] + (b[2] - a[2]) * f;
    }
    var hd = null;
    for (var j = (b ? i + 1 : i); j > 0 && j > i - 6; j--) {
      var q = p[j], r = p[j - 1], dx = q[1] - r[1], dy = q[2] - r[2];
      if (dx * dx + dy * dy > 4 && dx * dx + dy * dy < JUMP_PX * JUMP_PX) { hd = Math.atan2(dy, dx); break; }
    }
    return [x, y, hd, T - a[0], i];
  }

  // ------------------------------------------------------------------ camera
  function fitScale() { return Math.min(W, H) / MAP_SIZE; }
  function clampCam() {
    var smin = fitScale() * 0.9, smax = 6;
    cam.s = Math.max(smin, Math.min(smax, cam.s));
    var hw = W / 2 / cam.s, hh = H / 2 / cam.s;
    cam.cx = hw * 2 >= MAP_SIZE ? MAP_SIZE / 2 : Math.max(hw, Math.min(MAP_SIZE - hw, cam.cx));
    cam.cy = hh * 2 >= MAP_SIZE ? MAP_SIZE / 2 : Math.max(hh, Math.min(MAP_SIZE - hh, cam.cy));
  }
  function target(markers) {
    if (mode === "island" || (mode === "follow" && !markers.length)) return { cx: MAP_SIZE / 2, cy: MAP_SIZE / 2, s: fitScale() };
    var xs = markers.map(function (m) { return m.x; }), ys = markers.map(function (m) { return m.y; });
    var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs), y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
    var pad = 140;  // map px around the squad
    var s = Math.min(W / Math.max(420, x1 - x0 + 2 * pad), H / Math.max(420, y1 - y0 + 2 * pad));
    return { cx: (x0 + x1) / 2, cy: (y0 + y1) / 2, s: s };
  }
  function routePts(ks) {
    var out = [];
    ks.forEach(function (k) {
      var p = tracks[k].pts, step = Math.max(1, Math.floor(p.length / 300));
      for (var i = 0; i < p.length; i += step) out.push({ x: p[i][1], y: p[i][2] });
      out.push({ x: p[p.length - 1][1], y: p[p.length - 1][2] });
      tracks[k].chests.forEach(function (c) { out.push({ x: c[1], y: c[2] }); });
    });
    return out;
  }
  function toScreen(x, y) { return [(x - cam.cx) * cam.s + W / 2, (y - cam.cy) * cam.s + H / 2]; }
  function toMap(sx, sy) { return [(sx - W / 2) / cam.s + cam.cx, (sy - H / 2) / cam.s + cam.cy]; }

  // ------------------------------------------------------------------ drawing
  function resize() {
    var box = $("map");
    var w = box.clientWidth, h = box.clientHeight;
    dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    if (w === W && h === H && canvas.width === Math.round(w * dpr)) return;
    W = Math.max(1, w); H = Math.max(1, h);
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    if (!cam.ready) { cam.s = fitScale(); cam.ready = true; }
    clampCam();
    dirty = true;
  }

  function rgba(hex, a) {
    var n = parseInt(hex.slice(1), 16);
    return "rgba(" + (n >> 16 & 255) + "," + (n >> 8 & 255) + "," + (n & 255) + "," + a + ")";
  }

  function draw(frameMs) {
    var ks = keys(), live = isLive(), T = curT(), dt = Math.min(0.1, (frameMs - lastFrame) / 1000 || 0);
    lastFrame = frameMs;

    // markers at T (smoothed while following live)
    var markers = [];
    ks.forEach(function (k) {
      var tr = tracks[k], p = at(tr, T);
      if (!p) return;
      var x = p[0], y = p[1];
      if (timeline.live && live) {
        var d = disp[k];
        if (!d || Math.abs(d[0] - x) + Math.abs(d[1] - y) > JUMP_PX) d = disp[k] = [x, y];
        var f = Math.min(1, dt * 3);
        d[0] += (x - d[0]) * f; d[1] += (y - d[1]) * f;
        if (Math.abs(d[0] - x) + Math.abs(d[1] - y) > 0.3) dirty = true;
        x = d[0]; y = d[1];
      } else delete disp[k];
      // replay positions are always ~20 s old: those fade only when their messages stop
      var stale = live && timeline.live ? (tr.replay ? Date.now() - tr.lastRecv > STALE * 1000
                                                     : nowT() - tr.pts[tr.pts.length - 1][0] > STALE) : false;
      markers.push({ k: k, tr: tr, x: x, y: y, hd: p[2], idx: p[4], stale: stale, col: colorFor(k) });
    });

    // camera; after the match, parked at the end, "Squad" shows the whole match
    if (mode !== "free") {
      var overview = !live && timeline.live && !timeline.playing && mode === "follow";
      var tg = target(overview ? routePts(ks) : markers), f2 = Math.min(1, dt * 4) || 1;
      if (!cam.anim) { cam.cx = tg.cx; cam.cy = tg.cy; cam.s = tg.s; cam.anim = true; }
      var before = cam.cx + cam.cy + cam.s;
      cam.cx += (tg.cx - cam.cx) * f2; cam.cy += (tg.cy - cam.cy) * f2; cam.s += (tg.s - cam.s) * f2;
      if (Math.abs(cam.cx + cam.cy + cam.s - before) > 0.01) dirty = true;
    }
    clampCam();

    var s = cam.s, ox = W / 2 - cam.cx * s, oy = H / 2 - cam.cy * s;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#123d66";
    ctx.fillRect(0, 0, W, H);
    ctx.setTransform(dpr * s, 0, 0, dpr * s, dpr * ox, dpr * oy);
    if (img.complete && img.naturalWidth) ctx.drawImage(img, 0, 0, MAP_SIZE, MAP_SIZE);
    var u = 1 / s;  // one screen pixel in map pixels
    ctx.lineJoin = "round"; ctx.lineCap = "round";

    // routes, oldest squadmates first
    markers.forEach(function (m) {
      var pts = m.tr.pts.slice(0, m.idx + 1).map(function (q) { return [q[1], q[2]]; });
      pts.push([m.x, m.y]);
      var pieces = [[]];
      pts.forEach(function (q, i) {
        var prev = pts[i - 1];
        if (prev && Math.abs(q[0] - prev[0]) + Math.abs(q[1] - prev[1]) >= JUMP_PX) pieces.push([]);
        pieces[pieces.length - 1].push(q);
      });
      var alpha = m.stale ? 0.45 : 0.9;
      ctx.setLineDash(m.tr.replay ? [7 * u, 6 * u] : []);
      pieces.forEach(function (pc) {
        if (pc.length < 2) return;
        ctx.beginPath();
        pc.forEach(function (q, i) { if (i) ctx.lineTo(q[0], q[1]); else ctx.moveTo(q[0], q[1]); });
        ctx.strokeStyle = DARK + (alpha * 0.7) + ")"; ctx.lineWidth = 6.5 * u; ctx.stroke();
        ctx.strokeStyle = rgba(m.col, alpha); ctx.lineWidth = 4 * u; ctx.stroke();
      });
      ctx.setLineDash([]);
    });

    // chests they opened (up to T)
    markers.forEach(function (m) {
      m.n = 0;
      m.tr.chests.forEach(function (c) {
        if (c[0] > T) return;
        m.n++;
        ctx.beginPath(); ctx.arc(c[1], c[2], 6.5 * u, 0, 7);
        ctx.fillStyle = DARK + "0.95)"; ctx.fill();
        ctx.beginPath(); ctx.arc(c[1], c[2], 5 * u, 0, 7);
        ctx.fillStyle = m.col; ctx.fill();
        ctx.beginPath(); ctx.arc(c[1], c[2], 1.8 * u, 0, 7);
        ctx.fillStyle = "rgba(255,255,255,0.85)"; ctx.fill();
      });
    });

    // player markers: Fortnite's map arrow, a dart in the player's colour with a white edge, pointing the way
    // they're going (the last way they went while standing still; north before they've moved)
    markers.forEach(function (m) {
      if (m.hd !== null) m.tr.lastHd = m.hd;
      var hd = m.hd !== null ? m.hd : (m.tr.lastHd !== undefined ? m.tr.lastHd : -Math.PI / 2);
      ctx.save();
      ctx.globalAlpha = m.stale ? 0.5 : 1;
      ctx.translate(m.x, m.y);
      ctx.rotate(hd);
      ctx.scale(u, u);  // from here on, units are screen pixels
      ctx.beginPath();  // tip, wing, notch, wing
      ctx.moveTo(14, 0); ctx.lineTo(-9, 11); ctx.lineTo(-4, 0); ctx.lineTo(-9, -11); ctx.closePath();
      ctx.lineJoin = "round";
      ctx.strokeStyle = DARK + "0.9)"; ctx.lineWidth = 6; ctx.stroke();    // dark edge so it reads on any ground
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 3;
      if (m.tr.replay) ctx.setLineDash([4, 3]);                            // dashed: from a teammate's replay
      ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = m.col; ctx.fill();
      ctx.restore();
    });

    drawTags(markers);
    drawChrome(ks, live, T, markers);
  }

  // name tags: HTML, above each marker
  function drawTags(markers) {
    var box = $("tags"), seen = {}, placed = [];
    // top-most markers first; a tag that would cover one already placed moves up past it (map_window._text)
    markers.slice().sort(function (a, b) { return a.y - b.y; }).forEach(function (m) {
      var id = "tag-" + m.k.replace(/[^a-z0-9]/gi, "_"), el = document.getElementById(id);
      if (!el) { el = document.createElement("div"); el.id = id; el.className = "tag"; box.appendChild(el); }
      seen[id] = true;
      var text = m.tr.name + (m.tr.replay ? " · ~20 s" : "");
      if (el.textContent !== text) el.textContent = text;
      var sp = toScreen(m.x, m.y), w = el.offsetWidth || 60, h = el.offsetHeight || 20;
      var r = { l: sp[0] - w / 2, r: sp[0] + w / 2, b: sp[1] - 14, t: sp[1] - 14 - h };
      for (var i = 0; i < 6; i++) {
        var hit = placed.filter(function (o) { return r.l < o.r && o.l < r.r && r.t < o.b && o.t < r.b; })[0];
        if (!hit) break;
        var up = r.b - hit.t + 2;
        r.t -= up; r.b -= up;
      }
      placed.push(r);
      el.style.transform = "translate(" + r.l.toFixed(1) + "px," + r.t.toFixed(1) + "px)";
      el.style.color = m.col;
      el.style.opacity = m.stale ? 0.55 : 1;
    });
    Array.prototype.slice.call(box.children).forEach(function (c) { if (!seen[c.id]) box.removeChild(c); });
  }

  // match state, timeline, legend
  function drawChrome(ks, live, T, markers) {
    var sp = span(), have = ks.length > 0;
    $("mapIdle").hidden = have;
    $("timeline").hidden = !have;
    $("legend").hidden = !have;
    var ms = $("matchState");
    ms.className = "match-state" + (live ? " live" : "");
    if (have) {
      $("matchText").textContent = live ? "Live match" : "Match over";
    } else $("matchText").textContent = window.SquadChestsHaveSquad && window.SquadChestsHaveSquad() ? "No match right now" : "Looking for your squad…";
    if (!have) return;

    var range = $("tlRange");
    if (!range.dragging) range.value = Math.round(1000 * (T - sp[0]) / (sp[1] - sp[0]));
    $("tlFill").style.width = (range.value / 10) + "%";
    $("tlTime").textContent = fmt(T - sp[0]) + (timeline.live ? "" : " / " + fmt(sp[1] - sp[0]));
    var lb = $("liveBtn");
    lb.textContent = live ? "LIVE" : "END";
    lb.className = "tl-live" + (timeline.live ? " on" : "") + (live ? " is-live" : "");
    $("playBtn").innerHTML = timeline.playing ? "&#10074;&#10074;" : "&#9654;";
    $("playBtn").setAttribute("aria-label", timeline.playing ? "Pause" : "Play");

    if (Date.now() - ticksBuilt > 1000) {  // chest ticks along the timeline
      ticksBuilt = Date.now();
      var box = $("tlTicks"), html = document.createDocumentFragment();
      ks.forEach(function (k) {
        tracks[k].chests.forEach(function (c) {
          if (!isFinite(c[0])) return;
          var d = document.createElement("i");
          d.style.left = (100 * (c[0] - sp[0]) / (sp[1] - sp[0])).toFixed(2) + "%";
          d.style.background = colorFor(k);
          html.appendChild(d);
        });
      });
      box.textContent = ""; box.appendChild(html);
    }

    var lg = $("legend"), parts = markers.map(function (m) {
      return m.k + "|" + m.tr.name + "|" + m.n + "|" + m.stale + "|" + m.tr.replay;
    }).join(";");
    if (lg.dataset.v !== parts) {
      lg.dataset.v = parts; lg.textContent = "";
      markers.forEach(function (m) {
        var chip = document.createElement("button");
        chip.type = "button"; chip.className = "chip" + (m.stale ? " stale" : "");
        var dot = document.createElement("span"); dot.className = "chip-dot"; dot.style.background = m.col;
        chip.appendChild(dot);
        chip.appendChild(document.createTextNode(m.tr.name + (m.tr.replay ? " · console" : " · " + m.n + " chest" + (m.n === 1 ? "" : "s"))));
        chip.addEventListener("click", function () { focusOn(m.k); });
        lg.appendChild(chip);
      });
    }
    var note = live ? (timeline.live ? "Live: positions every 3 s." : "Drag the timeline to see the match so far.")
                    : "Ended " + clock(endT(ks)) + ", " + fmt(sp[1] - sp[0]) + " long. Everyone's whole route: press ▶ to replay it, or drag the timeline.";
    if (markers.some(function (m) { return m.tr.replay; })) note += " Dashed: console players, from a teammate's replay (about 20 s behind).";
    note += " Dots: chests they opened.";
    if (live && hasConsole && !markers.some(function (m) { return m.tr.replay; })) {
      note += " Console players without Chest Tracker show up after the match.";
    }
    $("mapNote").textContent = note;
  }

  function focusOn(k) {
    var p = tracks[k] && at(tracks[k], curT());
    if (!p) return;
    setMode("free");
    cam.cx = p[0]; cam.cy = p[1]; cam.s = Math.max(cam.s, fitScale() * 4);
    dirty = true;
  }

  // ------------------------------------------------------------------ loop
  function frame(ms) {
    if (timeline.playing) {
      var sp = span();
      if (sp) {
        timeline.T = Math.min(sp[1], curT() + PLAY_SPEED * Math.min(0.1, (ms - (frame.prev || ms)) / 1000));
        if (timeline.T >= sp[1] - 0.05) { timeline.playing = false; timeline.live = true; }
      }
      dirty = true;
    }
    frame.prev = ms;
    var liveNow = isLive();
    if (wasLive && !liveNow) { save(); timeline.live = true; timeline.playing = false; dirty = true; }  // post-game view
    wasLive = liveNow;
    resize();
    var every = isLive() || timeline.playing ? 0 : 1000;  // idle: redraw once a second for the clocks
    if (dirty || ms - (frame.drawn || 0) > (every || 33)) {
      dirty = false; frame.drawn = ms;
      if (!document.hidden) draw(ms);
    }
    requestAnimationFrame(frame);
  }

  function setMode(m) {
    mode = m;
    $("viewSquad").className = m === "follow" ? "on" : "";
    $("viewIsland").className = m === "island" ? "on" : "";
    dirty = true;
  }

  // ------------------------------------------------------------------ full screen and gestures
  function setFull(on) {
    var card = $("mapCard");
    if (on === full) return;
    full = on;
    card.classList.toggle("full", on);
    document.documentElement.classList.toggle("noscroll", on);
    $("fullBtn").innerHTML = on ? ICON_CLOSE : ICON_FULL;
    $("fullBtn").setAttribute("aria-label", on ? "Close full screen" : "Full screen");
    try {
      if (on && card.requestFullscreen && !document.fullscreenElement) card.requestFullscreen().catch(function () {});
      if (!on && document.fullscreenElement) document.exitFullscreen().catch(function () {});
    } catch (e) { /* the CSS full screen is enough (iPhone) */ }
    dirty = true;
  }
  var ICON_FULL = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>';
  var ICON_CLOSE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>';

  function zoomAt(k, sx, sy) {
    var before = toMap(sx, sy);
    cam.s *= k; clampCam();
    var after = toMap(sx, sy);
    cam.cx += before[0] - after[0]; cam.cy += before[1] - after[1];
    clampCam(); setMode("free");
  }

  function gestures() {
    var box = $("map"), pts = {}, last = null, moved = 0;
    function rel(e) { var r = box.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
    function state() {
      var v = Object.keys(pts).map(function (k) { return pts[k]; });
      if (!v.length) return null;
      var cx = v.reduce(function (a, p) { return a + p[0]; }, 0) / v.length, cy = v.reduce(function (a, p) { return a + p[1]; }, 0) / v.length;
      var d = v.length > 1 ? Math.hypot(v[0][0] - v[1][0], v[0][1] - v[1][1]) : 0;
      return { x: cx, y: cy, d: d, n: v.length };
    }
    box.addEventListener("pointerdown", function (e) {
      if (!full) return;
      if (e.target.closest && e.target.closest("button")) return;
      box.setPointerCapture(e.pointerId);
      pts[e.pointerId] = rel(e); last = state(); moved = 0;
    });
    box.addEventListener("pointermove", function (e) {
      if (!(e.pointerId in pts)) return;
      pts[e.pointerId] = rel(e);
      var st = state();
      if (last && st.n === last.n) {
        var dx = st.x - last.x, dy = st.y - last.y;
        moved += Math.abs(dx) + Math.abs(dy);
        if (moved > 4) {
          if (st.n > 1 && last.d > 0) zoomAt(st.d / last.d, st.x, st.y);
          cam.cx -= dx / cam.s; cam.cy -= dy / cam.s; clampCam(); setMode("free");
        }
      }
      last = st; dirty = true;
    });
    function up(e) { delete pts[e.pointerId]; last = state(); }
    box.addEventListener("pointerup", up);
    box.addEventListener("pointercancel", up);
    box.addEventListener("click", function (e) {
      if (!full && !(e.target.closest && e.target.closest("button"))) setFull(true);
    });
    box.addEventListener("dblclick", function (e) { if (full) { var p = rel(e); zoomAt(2, p[0], p[1]); } });
    box.addEventListener("wheel", function (e) {
      if (!full) return;
      e.preventDefault();
      var p = rel(e); zoomAt(Math.exp(-e.deltaY * 0.0015), p[0], p[1]);
    }, { passive: false });
  }

  function init() {
    canvas = $("mapCanvas"); ctx = canvas.getContext("2d");
    img.onload = function () { dirty = true; };
    img.src = "https://fortnite-api.com/images/map.png";
    fetch("https://fortnite-api.com/v1/map").then(function (r) { return r.json(); }).then(function (j) {
      var u = j && j.data && j.data.images && j.data.images.blank;
      if (typeof u === "string" && /^https:\/\//.test(u) && u !== img.src) img.src = u;
    }).catch(function () { /* keep the usual address */ });

    $("viewSquad").addEventListener("click", function () { setMode("follow"); });
    $("viewIsland").addEventListener("click", function () { setMode("island"); });
    $("fullBtn").innerHTML = ICON_FULL;
    $("fullBtn").addEventListener("click", function (e) { e.stopPropagation(); setFull(!full); });
    $("zoomIn").addEventListener("click", function () { zoomAt(1.6, W / 2, H / 2); });
    $("zoomOut").addEventListener("click", function () { zoomAt(1 / 1.6, W / 2, H / 2); });
    document.addEventListener("fullscreenchange", function () { if (!document.fullscreenElement && full) setFull(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && full) setFull(false); });

    var range = $("tlRange");
    range.addEventListener("pointerdown", function () { range.dragging = true; });
    range.addEventListener("pointerup", function () { range.dragging = false; });
    range.addEventListener("input", function () {
      var sp = span();
      if (!sp) return;
      timeline.T = sp[0] + (sp[1] - sp[0]) * range.value / 1000;
      timeline.live = range.value >= 999;
      timeline.playing = false;
      dirty = true;
    });
    range.addEventListener("change", function () { range.dragging = false; });
    $("liveBtn").addEventListener("click", function () { timeline.live = true; timeline.playing = false; dirty = true; });
    $("playBtn").addEventListener("click", function () {
      if (timeline.playing) { timeline.playing = false; timeline.T = curT(); }
      else {
        var sp = span();
        if (!sp) return;
        if (timeline.live) timeline.T = sp[0];
        timeline.live = false; timeline.playing = true;
      }
      dirty = true;
    });
    gestures();
    setMode("follow");
    requestAnimationFrame(frame);
    setInterval(function () {  // keep the match in this browser; clear a finished one after a while
      if (isLive()) save(); else forget();
    }, 30000);
  }

  var hasConsole = false, wasLive = false;
  window.SquadMap = { init: init, onMessage: onMessage, onLast: onLast, restore: restore, reset: reset,
                      redraw: function () { dirty = true; },
                      isLive: function () { return !!isLive(); },
                      setConsole: function (on) { if (on !== hasConsole) { hasConsole = on; dirty = true; } } };
})();
