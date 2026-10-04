// cesspool.lol: read the startup lines a Knots or Plumb node writes to debug.log and say which spam
// filters it is using and what set each one. It runs in the browser; the pasted text goes nowhere.
// The rules follow Knots 29.4.2: src/common/settings.cpp (GetSetting, MergeSettings), src/common/args.cpp
// (LogArgs) and the -corepolicy block in src/init.cpp.
(function () {
  "use strict";

  // Highest priority first, the order MergeSettings reads them. In config files the first value wins;
  // on the command line and in settings.json the last one does.
  var SOURCES = [
    {key: "cmd", first: false}, {key: "rw", first: true}, {key: "set", first: false},
    {key: "net", first: true}, {key: "def", first: true}
  ];
  var NET_SECTION = {main: "main", testnet3: "test", testnet4: "testnet4", signet: "signet", regtest: "regtest"};

  function parseValue(raw) {
    try { return JSON.parse(raw); } catch (e) { return raw; }
  }

  // A negated setting (-noX, or false in settings.json) is logged as false and wipes out what came before it.
  function pick(vals, first) {
    var neg = -1;
    vals.forEach(function (v, i) { if (v === false) neg = i; });
    var after = vals.slice(neg + 1);
    if (after.length) return first ? after[0] : after[after.length - 1];
    return neg >= 0 ? false : undefined;
  }

  // InterpretBool: empty means true, otherwise the leading integer, nonzero is true.
  function asBool(v) {
    if (v === true || v === false) return v;
    var s = String(v);
    if (s === "") return true;
    var n = parseInt(s, 10);
    return !isNaN(n) && n !== 0;
  }

  function asNum(v) {
    if (v === true) return 1;
    if (v === false) return 0;
    var n = parseFloat(String(v));
    return isNaN(n) ? 0 : n;
  }

  function readLog(text) {
    var lines = String(text || "").split(/\r?\n/), last = -1, startups = 0;
    lines.forEach(function (l, i) { if (/Bitcoin (Knots|Core) version /.test(l)) { last = i; startups++; } });
    var out = {software: null, version: null, chain: null, plumb: Object.create(null), args: Object.create(null), startups: startups, lines: 0};
    var raw = [];
    (last >= 0 ? lines.slice(last) : lines).forEach(function (l) {
      var m;
      if ((m = /Bitcoin (Knots|Core) version (\S+)/.exec(l))) { out.software = m[1]; out.version = m[2]; out.lines++; return; }
      if ((m = /Using data directory (.*\S)/.exec(l))) {
        var c = /[\/\\](regtest|testnet3|testnet4|signet)[\/\\]?$/.exec(m[1]);
        out.chain = c ? c[1] : "main"; out.lines++; return;
      }
      if ((m = /Plumb filter -([a-z]+)=(\d+)/.exec(l))) { out.plumb[m[1]] = m[2] !== "0"; out.lines++; return; }
      if ((m = /(Config file|R\/W config file|Command-line) arg: (?:\[([^\]]+)\] )?([A-Za-z0-9_.-]+)=(.*)$/.exec(l))) {
        raw.push({src: m[1], section: m[2] || "", name: m[3], value: parseValue(m[4])}); out.lines++; return;
      }
      if ((m = /Setting file arg: ([A-Za-z0-9_.-]+) = (.*)$/.exec(l))) {
        raw.push({src: "Setting file", section: "", name: m[1], value: parseValue(m[2])}); out.lines++;
      }
    });
    var net = NET_SECTION[out.chain || "main"];
    raw.forEach(function (r) {
      var key = r.src === "Command-line" ? "cmd" : r.src === "R/W config file" ? "rw" : r.src === "Setting file" ? "set"
        : r.section === "" ? "def" : r.section === net ? "net" : null;
      if (!key) return; // a section for another network
      var a = out.args[r.name] || (out.args[r.name] = Object.create(null));
      (a[key] || (a[key] = [])).push(r.value);
    });
    return out;
  }

  function resolve(args) {
    if (!args) return null;
    for (var i = 0; i < SOURCES.length; i++) {
      var vals = args[SOURCES[i].key];
      if (!vals || !vals.length) continue;
      var v = pick(vals, SOURCES[i].first);
      if (v !== undefined) return {value: v, from: SOURCES[i].key};
    }
    return null;
  }

  function versionAtLeast(v, want) {
    var a = /v?(\d+)\.(\d+)(?:\.(\d+))?/.exec(v || ""), b = /v?(\d+)\.(\d+)(?:\.(\d+))?/.exec(want);
    if (!a) return true;
    for (var i = 1; i <= 3; i++) {
      var x = parseInt(a[i] || "0", 10), y = parseInt(b[i] || "0", 10);
      if (x !== y) return x > y;
    }
    return true;
  }

  // spec: {options: [{name, label, kind, def, core, good, falseIs, max, over, min, under, std, before}], plumb: [{name, label, std}],
  // since, oldest}. std: acceptnonstdtxn=1 turns it off. before: [[version, default]] for Knots older than that version.
  function checkNode(text, spec) {
    var log = readLog(text);
    var res = {log: log, rows: [], off: 0, notes: [], empty: log.lines === 0};
    if (res.empty) return res;
    if (log.software === "Core") { res.core = true; res.notes.push("core"); return res; }
    if (log.software === "Knots" && !versionAtLeast(log.version, spec.oldest)) { res.tooold = true; res.notes.push("tooold"); return res; }
    if (log.software === "Knots" && !versionAtLeast(log.version, spec.since)) res.notes.push("old");
    if (log.startups > 1) res.notes.push("restarts");
    if (!log.software) res.notes.push("noversion");
    var cp = resolve(log.args.corepolicy), corepolicy = cp ? asBool(cp.value) : false;
    var nst = resolve(log.args.acceptnonstdtxn), nonstd = nst ? asBool(nst.value) : false;
    if (corepolicy) res.notes.push("corepolicy");
    if (nonstd) { res.notes.push("acceptnonstdtxn"); res.nonstdFrom = nst.from; }
    spec.options.forEach(function (o) {
      var r = resolve(log.args[o.name]), value, from, def = o.def;
      (o.before || []).forEach(function (b) { if (log.version && !versionAtLeast(log.version, b[0])) def = b[1]; });
      if (r) { value = r.value; from = r.from; }
      else if (corepolicy && o.core !== undefined) { value = o.core; from = "corepolicy"; }
      else { value = def; from = "default"; }
      var row = {name: o.name, label: res.notes.indexOf("old") >= 0 && o.oldLabel ? o.oldLabel : o.label, from: from, status: "on"};
      if (o.kind === "bool") {
        var b = asBool(value);
        row.value = b ? "1" : "0";
        if (b !== o.good) row.status = (o.falseIs && !b) ? o.falseIs : "off";
      } else {
        var n = asNum(value);
        row.value = String(n);
        if (o.max !== undefined && n > o.max) row.status = o.over;
        else if (o.min !== undefined && n < o.min) row.status = o.under;
        else if ((o.max !== undefined && n < o.max) || (o.min !== undefined && n > o.min)) row.status = "stricter";
      }
      if (nonstd && o.std) { row.status = "off"; row.from = "acceptnonstdtxn"; }
      if (row.status === "off" || row.status === "looser") res.off++;
      res.rows.push(row);
    });
    var plumbSeen = Object.keys(log.plumb).length > 0;
    if (plumbSeen) {
      spec.plumb.forEach(function (p) {
        var on = log.plumb[p.name];
        var row = {name: p.name, label: p.label, plumb: true, from: "plumb", value: on === undefined ? "?" : on ? "1" : "0",
                   status: on === undefined ? "missing" : on ? "on" : "off"};
        if (nonstd && p.std && on) { row.status = "off"; row.from = "acceptnonstdtxn"; }
        if (row.status === "off") res.off++;
        res.rows.push(row);
      });
    } else if (/plumb/i.test(log.version || "")) res.notes.push("plumbmissing");
    else if (log.software === "Knots") res.notes.push("stock");
    return res;
  }

  if (typeof module === "object" && module.exports) {
    module.exports = {checkNode: checkNode, readLog: readLog};
    return;
  }

  // ---------------------------------------------------------------- page

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

  var FROM = {
    cmd: "the command line", rw: "bitcoin_rw.conf", set: "settings.json", net: "bitcoin.conf, network section",
    def: "bitcoin.conf", corepolicy: "corepolicy=1", "default": "default", plumb: "Plumb's report", acceptnonstdtxn: "acceptnonstdtxn=1"
  };
  var FIX = {
    cmd: "Remove it from the command that starts the node: a service file, a docker-compose file or a start script.",
    rw: "Remove its line from bitcoin_rw.conf in the data directory. The Knots GUI writes that file.",
    set: "Remove it from settings.json in the data directory, or change it back in the Knots GUI.",
    net: "Change its line in the network section of bitcoin.conf ([main] on mainnet).",
    def: "Change its line in bitcoin.conf, or in a file bitcoin.conf includes. On Umbrel that file is umbrel-bitcoin.conf, which the app writes: change it in the app's Settings, Policy tab.",
    corepolicy: "corepolicy=1 turned it off. Set corepolicy=0, or set this option yourself.",
    plumb: "Find the line that sets it to 0 the same way: bitcoin.conf, settings.json, bitcoin_rw.conf or the command line."
  };
  var NOTES = {
    core: "Bitcoin Core does not follow this chain: it has no BLAKE2b proof of work. Run Knots 29.4.2 or Plumb.",
    tooold: "This checker covers Knots 29.4 and later. Knots before 29.4.1 has no Counterparty check and does not follow the BLAKE2b chain: upgrade to Knots 29.4.2 or Plumb.",
    old: "This is Knots 29.4. It does not follow the BLAKE2b chain, which needs 29.4.1 or later, it has no Counterparty check, and its rejecttokens starts off. Upgrade to Knots 29.4.2 or Plumb; the table shows the rest of its settings.",
    restarts: "The text covers more than one start of the node; this reads the last one.",
    noversion: "No version line found, so this assumes Knots 29.4.2 on mainnet. Include the version line for a sure answer.",
    corepolicy: "corepolicy=1 is set. It turns off every filter below that nothing else sets.",
    acceptnonstdtxn: "acceptnonstdtxn=1 is set. It turns off the checks marked below: tokens, parasites, bare outputs, script sizes and Plumb's token messages. The data size limits still apply.",
    plumbmissing: "The version says Plumb, but no Plumb filter lines were pasted. Include them to check Plumb's filters.",
    stock: "Stock Knots: Plumb's five extra filters are not in this build."
  };
  var STATUS = {on: "on", off: "OFF", looser: "looser", stricter: "stricter", capped: "capped at 83", missing: "not reported"};

  function render(out, res) {
    out.textContent = "";
    if (res.empty) {
      out.appendChild(el("p", {cls: "muted", text: "No startup lines found. Paste the output of the command above."}));
      return;
    }
    if (res.core || res.tooold) {
      out.appendChild(el("p", {cls: "verdict bad"}, el("b", {text: res.core ? "This is Bitcoin Core, not Knots." : "Upgrade first."}),
        " Bitcoin " + res.log.software + " " + res.log.version + "."));
      out.appendChild(el("p", {cls: "small", text: NOTES[res.notes[0]]}));
      return;
    }
    var old = res.notes.indexOf("old") >= 0;
    var head = old ? "Upgrade first." : res.off ? (res.off === 1 ? "1 filter is off." : res.off + " filters are off.") : "Every filter is on.";
    out.appendChild(el("p", {cls: "verdict " + (res.off || old ? "bad" : "good")}, el("b", {text: head}),
      res.log.software ? " Bitcoin " + res.log.software + " " + res.log.version + (res.log.chain && res.log.chain !== "main" ? ", " + res.log.chain : "") + "." : ""));
    res.notes.forEach(function (n) { out.appendChild(el("p", {cls: "small" + (n === "acceptnonstdtxn" || n === "corepolicy" || n === "old" ? "" : " muted"), text: NOTES[n]})); });
    var t = el("table", {}, el("tr", {}, el("th", {text: "Filter"}), el("th", {text: "Value"}), el("th", {cls: "from-d", text: "Set by"}), el("th", {text: "Status"})));
    res.rows.forEach(function (r) {
      var from = FROM[r.from] || r.from;
      t.appendChild(el("tr", {cls: r.status === "off" || r.status === "looser" ? "off" : ""},
        el("td", {}, el("code", {text: r.name}), " ", el("span", {cls: "muted small lbl", text: r.label}),
          el("span", {cls: "small from-m", text: "set by " + from})),
        el("td", {cls: "num", text: r.value}), el("td", {cls: "from-d", text: from}),
        el("td", {}, el("b", {text: STATUS[r.status] || r.status}))));
    });
    out.appendChild(el("div", {cls: "tw"}, t));
    var fixes = res.rows.filter(function (r) { return r.status === "off" || r.status === "looser"; });
    if (fixes.length) {
      var ul = el("ul", {cls: "fixes"});
      fixes.forEach(function (r) {
        var fix = r.from === "acceptnonstdtxn" ? "acceptnonstdtxn=1 turned it off. Remove that setting; it comes from " + FROM[res.nonstdFrom] + ". " + FIX[res.nonstdFrom]
          : r.from === "default" ? (res.notes.indexOf("old") >= 0 ? "This Knots starts it off. Set it to 1, or upgrade." : FIX.def) : FIX[r.from];
        ul.appendChild(el("li", {}, el("code", {text: r.name}), ": " + fix));
      });
      out.appendChild(el("h4", {text: "What to change"}));
      out.appendChild(ul);
    }
  }

  var app = document.getElementById("checkapp");
  if (!app) return;
  var spec = JSON.parse(document.getElementById("checkdata").textContent);
  var area = app.querySelector("textarea"), out = app.querySelector(".checkout");
  app.querySelector("form").addEventListener("submit", function (e) {
    e.preventDefault();
    render(out, checkNode(area.value, spec));
  });
})();
