(function () {
  "use strict";

  // Relative times: <time data-t="unix">
  function ago(t) {
    var s = Math.max(0, Date.now() / 1000 - t);
    if (s < 90) return "just now";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400 * 2) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " days ago";
  }
  document.querySelectorAll("time[data-t]").forEach(function (el) {
    var t = +el.getAttribute("data-t");
    el.title = new Date(t * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC";
    if (el.hasAttribute("data-ago")) el.textContent = ago(t);
  });

  // Height jump
  var jump = document.querySelector(".jump");
  if (jump) jump.addEventListener("submit", function (e) {
    e.preventDefault();
    var v = jump.querySelector("input").value.trim();
    if (/^\d+$/.test(v)) location.href = "/block/" + v + "/";
  });

  // Tabs: <div class="tabs" data-group="x"><button data-pane="id">
  document.querySelectorAll(".tabs").forEach(function (tabs) {
    tabs.addEventListener("click", function (e) {
      var b = e.target.closest("button[data-pane]");
      if (!b) return;
      tabs.querySelectorAll("button").forEach(function (x) { x.classList.toggle("on", x === b); });
      var group = tabs.getAttribute("data-group");
      document.querySelectorAll('.tabpane[data-group="' + group + '"]').forEach(function (p) {
        p.classList.toggle("on", p.id === b.getAttribute("data-pane"));
      });
      try { localStorage.setItem("tab-" + group, b.getAttribute("data-pane")); } catch (err) {}
    });
    try {
      var saved = localStorage.getItem("tab-" + tabs.getAttribute("data-group"));
      var sb = saved && tabs.querySelector('button[data-pane="' + saved + '"]');
      if (sb) sb.click();
    } catch (err) {}
  });

  // Copy buttons
  document.querySelectorAll("button.copy[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var text = b.getAttribute("data-copy");
      var done = function () { var o = b.textContent; b.textContent = "Copied"; setTimeout(function () { b.textContent = o; }, 1400); };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, function () {});
    });
  });

  // Block map: squarified treemap of every transaction, sized by vsize.
  var canvas = document.getElementById("map");
  var src = document.getElementById("mapdata");
  if (!canvas || !src) return;
  var data = JSON.parse(src.textContent);
  var tip = document.querySelector(".tip");
  var css = getComputedStyle(document.documentElement);
  function col(n) { return css.getPropertyValue(n).trim(); }
  var rects = [];

  function worst(row, w) {
    var s = 0, mx = 0, mn = Infinity;
    row.forEach(function (r) { s += r.a; if (r.a > mx) mx = r.a; if (r.a < mn) mn = r.a; });
    return Math.max(w * w * mx / (s * s), (s * s) / (w * w * mn));
  }
  function layout(items, x, y, w, h) {
    var out = [];
    var rest = items.slice();
    while (rest.length) {
      var side = Math.min(w, h), row = [rest[0]], i = 1;
      while (i < rest.length && worst(row.concat([rest[i]]), side) <= worst(row, side)) { row.push(rest[i]); i++; }
      rest = rest.slice(i);
      var s = row.reduce(function (a, r) { return a + r.a; }, 0);
      if (w >= h) {
        var cw = s / h, cy = y;
        row.forEach(function (r) { var rh = r.a / cw; out.push([r, x, cy, cw, rh]); cy += rh; });
        x += cw; w -= cw;
      } else {
        var rh2 = s / w, cx = x;
        row.forEach(function (r) { var rw = r.a / rh2; out.push([r, cx, y, rw, rh2]); cx += rw; });
        y += rh2; h -= rh2;
      }
    }
    return out;
  }

  function draw() {
    var dpr = window.devicePixelRatio || 1;
    var W = canvas.clientWidth, H = canvas.clientHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    var ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);
    var total = data.map.reduce(function (a, t) { return a + t[0]; }, 0) || 1;
    var items = data.map.map(function (t, i) { return { a: t[0] / total * W * H, t: t, i: i }; })
      .sort(function (a, b) { return b.a - a.a; });
    rects = layout(items, 0, 0, W, H);
    var c = { 0: col("--water"), 1: col("--gray"), 2: col("--sewage") };
    var miss = col("--miss"), bg = col("--panel");
    rects.forEach(function (q) {
      var r = q[0], x = q[1], y = q[2], w = q[3], h = q[4];
      var t = r.t, s = t[3] >= 0 ? data.spam[t[3]] : null;
      ctx.fillStyle = c[t[2]];
      ctx.globalAlpha = t[2] === 0 ? 0.55 : 0.95;
      ctx.fillRect(x + 0.5, y + 0.5, Math.max(0, w - 1), Math.max(0, h - 1));
      ctx.globalAlpha = 1;
      if (s && s.m) {
        ctx.strokeStyle = miss; ctx.lineWidth = 2;
        ctx.strokeRect(x + 1.5, y + 1.5, Math.max(0, w - 3), Math.max(0, h - 3));
      }
      ctx.strokeStyle = bg; ctx.lineWidth = 1;
      ctx.strokeRect(x, y, w, h);
    });
  }

  function hit(ev) {
    var b = canvas.getBoundingClientRect(), x = ev.clientX - b.left, y = ev.clientY - b.top;
    for (var i = 0; i < rects.length; i++) {
      var q = rects[i];
      if (x >= q[1] && x < q[1] + q[3] && y >= q[2] && y < q[2] + q[4]) return { q: q, x: x, y: y };
    }
    return null;
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  canvas.addEventListener("mousemove", function (ev) {
    var h = hit(ev);
    if (!h) { tip.style.display = "none"; return; }
    var t = h.q[0].t, s = t[3] >= 0 ? data.spam[t[3]] : null;
    var rate = t[0] ? (t[1] / t[0]).toFixed(1) : "0";
    var html = "<b>" + (s ? esc(s.n) : "Payment") + "</b><br>" + t[0].toLocaleString() + " vB &middot; " + rate + " sat/vB";
    if (s) html += "<br>" + (s.d ? s.d.toLocaleString() + " bytes of payload" : "") + (s.m ? "<br><span style=\"color:var(--miss)\">" + esc(data.pn) + " relays this</span>" : "");
    tip.innerHTML = html;
    tip.style.display = "block";
    var bx = canvas.parentNode.getBoundingClientRect(), cb = canvas.getBoundingClientRect();
    var left = h.x + (cb.left - bx.left) + 14, top = h.y + (cb.top - bx.top) + 14;
    if (left + 280 > bx.width) left -= 300;
    tip.style.left = left + "px"; tip.style.top = top + "px";
  });
  canvas.addEventListener("mouseleave", function () { tip.style.display = "none"; });
  canvas.addEventListener("click", function (ev) {
    var h = hit(ev);
    if (!h) return;
    var t = h.q[0].t, s = t[3] >= 0 ? data.spam[t[3]] : null;
    if (s) location.hash = "tx-" + s.id;
  });
  draw();
  var rt;
  window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(draw, 120); });
  if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () { css = getComputedStyle(document.documentElement); draw(); });
})();
