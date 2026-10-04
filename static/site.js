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
    else if (/^[0-9a-fA-F]{64}$/.test(v)) location.href = "/tx/?" + v.toLowerCase();
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

  // Summary pages carry the tip they were built at; when the site has a newer block, reload.
  // The build writes /tip.json last, so the reload finds pages that already show it.
  var tipHave = Number(document.body.getAttribute("data-tip") || 0);
  if (tipHave) {
    var checkTip = function () {
      if (document.hidden) return;
      fetch("/tip.json?m=" + Math.floor(Date.now() / 30000), { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (j) {
          if (!j || !(j.h > tipHave)) return;
          // Once per tip: a page the build no longer refreshes would otherwise reload every minute.
          var key = "tip-reload:" + location.pathname;
          try { if (sessionStorage.getItem(key) === String(j.h)) return; sessionStorage.setItem(key, String(j.h)); } catch (e) {}
          location.reload();
        })
        .catch(function () {});
    };
    setInterval(checkTip, 60000);
    document.addEventListener("visibilitychange", checkTip);
  }

  // Copy buttons
  document.querySelectorAll("button.copy[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var text = b.getAttribute("data-copy");
      var done = function () { var o = b.textContent; b.textContent = "Copied"; setTimeout(function () { b.textContent = o; }, 1400); };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, function () {});
    });
  });

  // Transaction page: /tx/?<txid> or /tx/?<height>:<index>. Everything is built with
  // createElement, text nodes and attributes; no transaction data is ever parsed as HTML.
  var txapp = document.getElementById("txapp");
  if (txapp) txPage(txapp);

  function el(tag, attrs) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === "text") e.textContent = attrs[k];
      else if (k === "cls") e.className = attrs[k];
      else e.setAttribute(k, attrs[k]);
    });
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c == null || c === false) continue;
      e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return e;
  }
  // Prose with `code` spans, from the catalog (our own text, still never parsed as HTML).
  function prose(text) {
    var f = document.createDocumentFragment();
    String(text || "").split("`").forEach(function (part, i) { f.appendChild(i % 2 ? el("code", { text: part }) : document.createTextNode(part)); });
    return f;
  }
  function num(x) { return Number(x).toLocaleString("en-US"); }
  function plural(k, word) { return num(k) + " " + word + (k === 1 ? "" : "s"); }
  var SHORT = { witness_v1_taproot: "P2TR", witness_v0_keyhash: "P2WPKH", witness_v0_scripthash: "P2WSH", scripthash: "P2SH",
    pubkeyhash: "P2PKH", nulldata: "OP_RETURN", multisig: "Bare multisig", pubkey: "P2PK", anchor: "Anchor",
    witness_unknown: "Unknown witness", nonstandard: "Nonstandard" };
  function short(t) { return SHORT[t] || t || "?"; }
  function addr(a) { return a.length > 22 ? a.slice(0, 10) + "…" + a.slice(-8) : a; }
  function getJSON(url) {
    return fetch(url).then(function (r) { if (r.status === 404) return null; if (!r.ok) throw new Error(r.status); return r.json(); });
  }

  function txPage(app) {
    var out = app.querySelector(".txout"), form = app.querySelector(".txform"), input = form.querySelector("input");
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var v = input.value.trim().toLowerCase();
      if (/^[0-9a-f]{64}$/.test(v)) location.search = "?" + v;
      else { out.textContent = ""; out.appendChild(el("p", { cls: "muted", text: "A transaction id is 64 hexadecimal characters." })); }
    });
    var q = "";
    try { q = decodeURIComponent((location.search || "").slice(1).split("&")[0]).trim().toLowerCase(); } catch (e) { q = "?"; }
    var m = /^(\d+):(\d+)$/.exec(q);
    if (!m && !/^[0-9a-f]{64}$/.test(q)) {
      if (q) out.appendChild(el("p", { cls: "muted", text: "A transaction id is 64 hexadecimal characters." }));
      return;
    }
    if (!m) input.value = q;
    out.textContent = "Loading…";
    var cat;
    getJSON("/d/catalog.json").then(function (c) {
      cat = c;
      if (m) return { h: +m[1], idx: +m[2] };
      return getJSON("/d/i/" + q.slice(0, 3) + ".json").then(function (shard) {
        var h = shard && shard[q.slice(3, 16)];
        return h ? { h: h } : null;
      });
    }).then(function (where) {
      if (!where) return notFound(out, q);
      return getJSON("/d/b/" + where.h + ".json").then(function (b) {
        if (!b && m) { out.textContent = ""; out.appendChild(el("p", { text: "There is no block " + where.h + " since the fork." })); return; }
        if (!b) return notFound(out, q);
        var tx = null;
        if (where.idx !== undefined) tx = where.idx === 0 ? b.cb : b.tx[where.idx - 1];
        else if (b.cb.id === q) tx = b.cb;
        else tx = b.tx.filter(function (t) { return t.id === q; })[0];
        if (!tx) {
          out.textContent = "";
          out.appendChild(el("p", { text: where.idx !== undefined ? "Block " + where.h + " has no transaction at position " + where.idx + "."
            : "Block " + where.h + " does not hold this transaction. Either the txid is mistyped after its first 16 characters, or a reorg replaced the block it was in." }));
          return;
        }
        if (where.idx !== undefined) { input.value = tx.id; history.replaceState(null, "", "/tx/?" + tx.id); }
        document.title = "Transaction " + tx.id.slice(0, 16) + "… · cesspool.lol";
        var hero = app.querySelector(".hero");
        if (hero) { hero.querySelector("h1").textContent = "Transaction"; var lede = hero.querySelector(".lede"); if (lede) lede.remove(); }
        render(out, tx, b, cat, app.getAttribute("data-plumb"));
      });
    }).catch(function () { out.textContent = "Could not load the transaction. Try again in a minute."; });
  }

  function notFound(out, q) {
    out.textContent = "";
    out.appendChild(el("p", {}, "No transaction ", el("code", { text: q }), " in any block since the fork. ",
      "cesspool covers blocks from 961640 on. A transaction still waiting in the mempool, one from before the fork, or one in a block from the last few minutes is not here."));
  }

  function shape(tx) {
    var nIn = tx.i.length, pays = tx.o.filter(function (o) { return o.t !== "nulldata"; }), nOut = pays.length;
    var hasNote = tx.o.length !== nOut;
    if (!hasNote && ((nIn >= 3 && nOut === 1) || (nIn >= 10 && nOut === 2))) {
      var dust = tx.i.filter(function (i) { return i.a <= 1000; }).length;
      var s = "Spends " + num(nIn) + " outputs into " + num(nOut) + (nOut === 1 ? ". Wallets do this to merge coins; it" : ". It") + " leaves the UTXO set " + num(nIn - nOut) + " entries smaller.";
      if (dust * 2 >= nIn) s += " " + num(dust) + " of the inputs hold 1,000 sat or less, small coins that would otherwise stay in every node's UTXO set.";
      return ["Consolidation", s];
    }
    if (nOut >= 5 && nIn <= Math.max(2, nOut / 2)) return ["Batch payment", "Pays " + plural(nOut, "output") + " from " + plural(nIn, "input") + " in one transaction."];
    return ["Payment", "Spends " + plural(nIn, "input") + " into " + plural(nOut, "output") + "."];
  }

  function stamp(text, color) { var s = el("span", { cls: "stamp sm", text: text }); if (color) s.style.color = color; return s; }

  function verdicts(tx, cat, plumb, normal) {
    // On spam, relaying is the failure; on a normal transaction or a small note, refusing is.
    if (tx.e) return el("p", { cls: "note", text: "No verdicts: the policy check did not run on this transaction." });
    var box = el("div", { cls: "verd" + (normal ? " normal" : "") });
    cat.verdicts.forEach(function (v) {
      var k = v[0], name = v[1];
      var rs = (tx.x && tx.x[k]) || [];
      var cell = el("div", { cls: k === "plumb" ? "plumb" : "" }, el("div", { cls: "who", text: name }));
      if (rs.length) {
        cell.appendChild(el("span", { cls: "stop", text: "Refuses it" }));
        cell.appendChild(el("div", { cls: "why", text: rs.map(function (r) { return cat.reasons[r] || r; }).join("; ") }));
      } else {
        cell.appendChild(el("span", { cls: "pass", text: "Relays and mines it" }));
        cell.appendChild(el("div", { cls: "why", text: k === "plumb" && tx.m ? "this one gets past its filters" : "no rule matches" }));
      }
      box.appendChild(cell);
    });
    return box;
  }

  function wsText(ws) {
    if (!ws || !ws.length) return "none";
    var sum = ws.reduce(function (a, b) { return a + b; }, 0);
    return plural(ws.length, "item") + ", " + num(sum) + " B";
  }

  var COMPACT = 10;
  function ioTable(rows, kind, full) {
    var t = el("table", { cls: "iot" });
    var head = kind === "in" ? ["", "Amount", "Type", "Address", "Spent by", "Witness", "Data"] : ["", "Amount", "Type", "Address or note", "Data"];
    t.appendChild(el("tr", {}, ...head.map(function (h, i) { return el("th", { cls: i === 1 || i === head.length - 1 ? "r" : "", text: h }); })));
    var groups = [];
    if (full) groups = rows.map(function (r, i) { return { r: r, n: 1, idx: i, ads: {} }; });
    else {
      // Rows that differ only in address collapse into one, with the number of addresses.
      var seen = {};
      rows.forEach(function (r, i) {
        var key = [r.a, r.t, r.sp, (r.ws || []).join(","), r.d || 0, r.l || "", r.hid || 0].join("|");
        if (!seen[key]) { seen[key] = { r: r, n: 0, idx: i, ads: {} }; groups.push(seen[key]); }
        seen[key].n++;
        if (r.ad) seen[key].ads[r.ad] = 1;
      });
    }
    var shown = full ? groups : groups.slice(0, COMPACT);
    shown.forEach(function (g) {
      var r = g.r, data = r.d ? el("span", { cls: "db", text: num(r.d) + " B" + (g.n > 1 ? " each" : "") }) : el("span", { cls: "x", text: r.t === "nulldata" ? num(r.n) + " B note" : r.hid ? "not counted" : "none" });
      var where;
      if (r.t === "nulldata") where = el("span", { text: r.l || "OP_RETURN" });
      else if (r.hid === 1) where = el("span", { cls: "x", text: "address hidden: counted as data" });
      else if (r.hid) where = el("span", { cls: "x", text: "address hidden: other " + short(r.t) + " outputs in this transaction carry data" });
      else if (Object.keys(g.ads).length > 1) where = el("span", { cls: "x", text: num(Object.keys(g.ads).length) + " different addresses" });
      else if (r.ad) where = el("span", { cls: "mono", title: r.ad, text: addr(r.ad) });
      else where = el("span", { cls: "x", text: "no address" });
      if (kind === "in" && full && r.p) {
        var pv = r.p.split(":");
        where = el("span", {}, where, el("br"), el("span", { cls: "x" }, "from ", el("a", { href: "/tx/?" + pv[0], cls: "mono", text: pv[0].slice(0, 12) + "…:" + pv[1] })));
      }
      var cells = [el("td", { cls: "x", text: g.n > 1 ? "×" + num(g.n) : "#" + g.idx }), el("td", { cls: "r num", text: num(r.a) + " sat" }),
        el("td", { text: short(r.t) }), el("td", {}, where)];
      if (kind === "in") cells.push(el("td", { text: r.sp || "" }), el("td", { cls: "x", text: wsText(r.ws) }));
      cells.push(el("td", { cls: "r" }, data));
      t.appendChild(el("tr", { cls: r.d ? "d" : "" }, ...cells));
    });
    if (shown.length < groups.length) {
      var rest = groups.slice(shown.length), restN = rest.reduce(function (a, g) { return a + g.n; }, 0), byType = {};
      rest.forEach(function (g) { var k = short(g.r.t); byType[k] = (byType[k] || 0) + g.n; });
      var summary = Object.keys(byType).map(function (k) { return k + " ×" + num(byType[k]); }).join(", ");
      t.appendChild(el("tr", {}, el("td", { cls: "x", text: "" }), el("td", { cls: "x", colspan: String(head.length - 1), text: num(restN) + " more: " + summary })));
    }
    return el("div", { cls: "tw" }, t);
  }

  function ioSection(title, rows, kind) {
    var box = el("div", {}, el("h4", { text: title + " (" + num(rows.length) + ")" }));
    var holder = el("div", {}, ioTable(rows, kind, rows.length <= 12));
    box.appendChild(holder);
    if (rows.length > 12) {
      var b = el("button", { cls: "btn ghost", type: "button", text: kind === "in" ? "Show every input" : "Show every output" });
      b.addEventListener("click", function () { holder.textContent = ""; holder.appendChild(ioTable(rows, kind, true)); b.remove(); });
      box.appendChild(b);
    }
    return box;
  }

  function render(out, tx, b, cat, plumb) {
    out.textContent = "";
    var tier = tx.cb ? "coinbase" : (tx.k || "clean"), types = tx.ty || [], info = types.length ? (cat.types[types[0]] || { name: types[0] }) : null;
    var title, st, sec = el("section", { cls: "dis" + (tier === "gray" ? " gray" : tier === "clean" ? " normal" : tier === "coinbase" ? " coinbase" : "") + (tx.m ? " missed" : "") }), what = [];
    if (tier === "coinbase") {
      title = "Coinbase"; st = [stamp("Block reward", "var(--water)")];
      what.push(el("p", { text: "Pays out the block subsidy and the fees of every transaction in block " + b.h + ", across " + plural(tx.o.filter(function (o) { return o.t !== "nulldata"; }).length, "paying output") + ". " + ((b.ws && b.ws !== b.ps) ? "A DATUM miner built the block through " + b.pool + "." : b.pool + " mined the block.") }));
    } else if (tier === "sewage") {
      title = info.name; st = [stamp("Sewage", "var(--sewage)")];
      if (tx.m) st.push(stamp(plumb + " miss", "var(--miss)"));
      what.push(el("p", {}, prose(info.what), " ", el("span", { cls: "muted" }, prose(info.how))));
      types.slice(1).forEach(function (k) {
        // classify files fake multisig inputs as witness data too, the same finding by its generic name;
        // name it only when an input outside the multisig spends carries data
        if (k === "witness-data" && types[0] === "fake-multisig" && tx.i.every(function (r) { return !r.d || /multisig/.test(r.sp || ""); })) return;
        var more = cat.types[k];
        if (more) what.push(el("p", {}, el("b", { text: "Also found: " + more.name + ". " }), prose(more.what)));
      });
      if (info.staged) what.push(el("p", {}, "Counted as data: ", el("b", { text: num(tx.i.concat(tx.o).reduce(function (a, r) { return a + (r.d || 0); }, 0)) + " bytes" }), " of a " + num(tx.s) + "-byte transaction, in the rows marked below. ",
        "The payload itself comes when those outputs are spent. This page shows sizes, never the bytes."));
      else what.push(el("p", {}, "Payload: ", el("b", { text: num(tx.dt || 0) + " bytes" }), " of a " + num(tx.s) + "-byte transaction. ",
        "The rows marked below are where it sits. This page shows sizes, never the bytes."));
      if (info.option && (info.filter === cat.filters.plumb || info.filter === cat.filters.knots)) {
        var line = el("p", { cls: "small" }, (info.filter === cat.filters.plumb ? "Plumb ships " : "Knots ships "), el("code", { text: info.option }));
        (info.prs || []).forEach(function (p) { line.appendChild(document.createTextNode(" ")); line.appendChild(el("a", { href: p[1], text: p[0] })); });
        what.push(line);
      }
      if (tx.m && info.note) what.push(el("div", { cls: "note" }, el("b", { text: plumb + " relays this. " }), prose(info.note)));
    } else if (tier === "gray") {
      title = (tx.lb && tx.lb[0]) || "Small note"; st = [stamp("Gray water", "var(--gray)")];
      what.push(el("p", { text: tx.dt ? "Carries a little data inside the default 83-byte allowance, the room Knots leaves for notes such as swap memos. cesspool does not count it as sewage."
        : "Carries an OP_RETURN with no data in it. cesspool does not count it as sewage." }));
    } else {
      var sh = shape(tx); title = sh[0]; st = [stamp("Normal", "var(--ok)")];
      what.push(el("p", { text: sh[1] }));
      var anyRefusal = tx.x && ["core", "knots", "plumb"].some(function (k) { return tx.x[k].length; });
      what.push(el("p", {}, el("b", { text: "Not spam. " }), anyRefusal
        ? "No input or output carries data that the policy code counts. One of the policies below refuses it for a reason that is not a data rule."
        : "No input or output carries data that Bitcoin Core, Knots or " + plumb + " counts, and all three relay it."));
    }
    var hd = el("div", { cls: "hd" }, ...st, el("h3", { text: title }));
    sec.appendChild(hd);
    var copy = el("button", { cls: "copy", type: "button", text: "Copy link" });
    copy.addEventListener("click", function () { if (navigator.clipboard) navigator.clipboard.writeText(location.origin + "/tx/?" + tx.id).then(function () { copy.textContent = "Copied"; setTimeout(function () { copy.textContent = "Copy link"; }, 1400); }, function () {}); });
    sec.appendChild(el("div", { cls: "txid", text: tx.id }));
    var t = new Date(b.t * 1000).toISOString().replace("T", " ").slice(0, 16) + " UTC";
    var meta = el("div", { cls: "meta small" }, el("span", {}, "Block ", el("a", { href: "/block/" + b.h + "/", text: String(b.h) })), el("span", { text: t }),
      (b.ws && b.ws !== b.ps)
        ? el("span", {}, "Built by ", el("a", { href: "/pool/" + b.ws + "/", text: "a DATUM miner" }), " via ", el("a", { href: "/pool/" + b.ps + "/", text: b.pool }))
        : el("span", {}, "Mined by ", el("a", { href: "/pool/" + b.ps + "/", text: b.pool })));
    if (!tx.cb) meta.appendChild(el("span", { text: "Fee " + num(tx.f) + " sat (" + (tx.f / tx.vs).toFixed(1) + " sat/vB)" }));
    meta.appendChild(el("span", { text: num(tx.s) + " B, " + num(tx.vs) + " vB" }));
    meta.appendChild(copy);
    sec.appendChild(meta);
    what.forEach(function (w) { sec.appendChild(w); });
    if (!tx.cb) sec.appendChild(verdicts(tx, cat, plumb, tier !== "sewage"));
    if (!tx.cb && !tx.e && tx.x && ["core", "knots", "plumb"].some(function (k) { return tx.x[k] && tx.x[k].length; })) {
      var policy = "Refusing is policy, not a consensus rule: a node that refuses it does not relay it or put it in a block it builds, but it still accepts a block that has it.";
      var pol = tx.x.knots && tx.x.knots.length
        ? el("p", { cls: "small muted" }, el("b", { text: "Past the defaults. " }), "Knots and Plumb refuse it at their default settings; a node at those defaults takes it only when its operator overrides the refusal. ",
            el("a", { href: "/past-defaults/", text: "Every block like this" }), ". ",
            el("a", { href: "/past-defaults/#settings", text: "Settings that keep it out" }), ". " + policy)
        : el("p", { cls: "small muted", text: policy });
      pol.style.marginTop = "10px"; sec.appendChild(pol);
      // As on the block page: say when only the coinbase text ties the block to the name it carries.
      if (tx.x.knots && tx.x.knots.length && b.pool && b.pool.indexOf("Unknown (") !== 0 && b.cb && b.cb.o) {
        var pays = b.cb.o.filter(function (o) { return o.ad && o.a > 0; }).map(function (o) { return o.ad; });
        fetch("/d/pools.json?m=" + Math.floor(Date.now() / 600000))
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (m) {
            if (!m) return;
            var on = m[b.pool] || [];
            if (pays.some(function (a) { return on.indexOf(a) >= 0; })) return;
            pol.appendChild(document.createTextNode(" The name " + b.pool + " comes from this block's coinbase text alone, which anyone can write."));
          })
          .catch(function () {});
      }
      if (tx.x.knots && tx.x.knots.length && (b.ws && b.ws !== b.ps))
        pol.appendChild(document.createTextNode(" A DATUM gateway with " + b.pool + " upstream built this block's template, so the node behind that gateway chose this transaction, normally the miner's own, not " + b.pool + "."));
    }
    var anat = el("div", { cls: "anat" });
    if (!tx.cb) anat.appendChild(ioSection("Inputs", tx.i, "in"));
    anat.appendChild(ioSection("Outputs", tx.o, "out"));
    if (!tx.cb) {
      var sumIn = tx.i.reduce(function (a, r) { return a + r.a; }, 0), sumOut = tx.o.reduce(function (a, r) { return a + r.a; }, 0);
      anat.appendChild(el("p", { cls: "small muted", text: "In " + num(sumIn) + " sat, out " + num(sumOut) + " sat, fee " + num(tx.f) + " sat." }));
    }
    sec.appendChild(anat);
    out.appendChild(sec);
  }

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
    if (s) html += (s.d ? "<br>" + s.d.toLocaleString() + " bytes of payload" : "") + (s.m ? "<br><span style=\"color:var(--miss)\">" + esc(data.pn) + " relays this</span>" : "");
    html += "<br><span style=\"opacity:.7\">Click for the details</span>";
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
    location.href = "/tx/?" + data.h + ":" + (h.q[0].i + 1);
  });
  draw();
  var rt;
  window.addEventListener("resize", function () { clearTimeout(rt); rt = setTimeout(draw, 120); });
  if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () { css = getComputedStyle(document.documentElement); draw(); });
})();
