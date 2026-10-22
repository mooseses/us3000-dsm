/* US3000 Companion UI. Plain JS, no dependencies; talks to api.cgi (read-only). */
(function () {
  "use strict";

  var SVGNS = "http://www.w3.org/2000/svg";
  var state = { range: "24h", history: null, outages: [], tables: {} };
  var KIND_LABEL = {
    ON_BATTERY: "On battery", ON_MAINS: "Power restored", LOW_BATTERY: "Low battery",
    FORCED_SHUTDOWN: "Forced shutdown", OUTAGE_CLOSED: "Outage closed", REPLACE_BATTERY: "Replace battery",
    COMM_LOST: "UPS unreachable", COMM_OK: "UPS reachable", COLLECTOR_START: "Collector started",
    BEEPER: "Beeper", TEST_RESULT: "Battery test", CONTROL: "Command sent"
  };
  var KIND_LEVEL = {
    ON_MAINS: "good", COMM_OK: "good", ON_BATTERY: "warning", OUTAGE_CLOSED: "warning", COMM_LOST: "warning",
    LOW_BATTERY: "critical", FORCED_SHUTDOWN: "critical", REPLACE_BATTERY: "critical",
    CONTROL: "info", BEEPER: "info", TEST_RESULT: "info"
  };
  var CMD_LABEL = {
    "beeper.enable": "Enable beeper", "beeper.disable": "Disable beeper", "beeper.mute": "Mute alarm",
    "test.battery.start.quick": "Quick battery test", "test.battery.start.deep": "Deep battery test",
    "test.battery.stop": "Stop battery test"
  };

  // ------------------------------------------------------------ helpers
  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    for (var k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  }
  function svg(tag, attrs) {
    var e = document.createElementNS(SVGNS, tag);
    for (var k in attrs || {}) e.setAttribute(k, attrs[k]);
    return e;
  }
  function num(v) { var n = parseFloat(v); return isNaN(n) ? null : n; }
  function dur(s) {
    if (s == null) return "–";
    s = Math.max(0, Math.round(s));
    if (s < 60) return s + "s";
    if (s < 3600) return Math.floor(s / 60) + "m " + String(s % 60).padStart(2, "0") + "s";
    if (s < 86400) return Math.floor(s / 3600) + "h " + String(Math.floor(s % 3600 / 60)).padStart(2, "0") + "m";
    return Math.floor(s / 86400) + "d " + Math.floor(s % 86400 / 3600) + "h";
  }
  function dt(ts, withDate) {
    var d = new Date(ts * 1000);
    var hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return withDate === false ? hm : d.toLocaleDateString([], { month: "short", day: "numeric" }) + " " + hm;
  }
  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  function synoToken() {
    try { return window.parent.SYNO.SDS.Session.SynoToken || ""; } catch (e) { return ""; }
  }
  function api(params) {
    var tok = synoToken();
    var q = Object.keys(params).map(function (k) { return k + "=" + encodeURIComponent(params[k]); }).join("&");
    if (tok) q += "&SynoToken=" + encodeURIComponent(tok);
    return fetch("api.cgi?" + q, { credentials: "same-origin", headers: tok ? { "X-SYNO-TOKEN": tok } : {} })
      .then(function (r) {
        return r.json().catch(function () { return { error: "HTTP " + r.status }; })
          .then(function (b) { if (!r.ok) throw new Error(b.error || ("HTTP " + r.status)); return b; });
      });
  }
  function post(params, payload) {
    var tok = synoToken();
    var q = Object.keys(params).map(function (k) { return k + "=" + encodeURIComponent(params[k]); }).join("&");
    if (tok) q += "&SynoToken=" + encodeURIComponent(tok);
    var headers = { "Content-Type": "application/json", "X-US3000": "1" };
    if (tok) headers["X-SYNO-TOKEN"] = tok;
    return fetch("api.cgi?" + q, { method: "POST", credentials: "same-origin", headers: headers,
                                   body: JSON.stringify(payload) })
      .then(function (r) {
        return r.json().catch(function () { return { ok: false, error: "HTTP " + r.status }; });
      });
  }
  function banner(msg) { var b = $("banner"); b.textContent = msg || ""; b.hidden = !msg; }

  // ------------------------------------------------------------ tooltip
  var tip = $("tooltip");
  function showTip(evt, title, rows) {
    tip.innerHTML = "";
    tip.appendChild(el("div", { "class": "t" }, title));
    rows.forEach(function (r) {
      var row = el("div", { "class": "row" });
      if (r.color) { var sw = el("span", { "class": "sw" }); sw.style.background = r.color; row.appendChild(sw); }
      row.appendChild(el("span", {}, r.label + ": " + r.value));
      tip.appendChild(row);
    });
    tip.hidden = false;
    var x = evt.clientX + 14, y = evt.clientY + 14, w = tip.offsetWidth, h = tip.offsetHeight;
    if (x + w > window.innerWidth - 8) x = evt.clientX - w - 14;
    if (y + h > window.innerHeight - 8) y = evt.clientY - h - 14;
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }
  function hideTip() { tip.hidden = true; }

  // ------------------------------------------------------------ charts
  function niceTicks(lo, hi, n) {
    if (hi <= lo) { hi = lo + 1; }
    var step = Math.pow(10, Math.floor(Math.log10((hi - lo) / n)));
    var err = (hi - lo) / n / step;
    if (err >= 7.5) step *= 10; else if (err >= 3.5) step *= 5; else if (err >= 1.5) step *= 2;
    var ticks = [];
    for (var v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) ticks.push(+v.toFixed(6));
    return ticks;
  }

  function timeTicks(start, end, width) {
    var span = end - start, want = Math.max(2, Math.min(7, Math.floor(width / 110)));
    var steps = [300, 900, 1800, 3600, 3 * 3600, 6 * 3600, 12 * 3600, 86400, 2 * 86400, 7 * 86400];
    var step = steps[steps.length - 1];
    for (var i = 0; i < steps.length; i++) if (span / steps[i] <= want) { step = steps[i]; break; }
    var off = new Date(start * 1000).getTimezoneOffset() * 60;   // align ticks to local midnight/hours
    var ticks = [];
    for (var t = Math.ceil((start - off) / step) * step + off; t <= end; t += step) ticks.push(t);
    return { ticks: ticks, withDate: step >= 86400 || span > 86400 };
  }

  /* opts: {series:[{key,label,color,fmt}], points, start, end, bucket, yMin, yMax, bands, height, xKey} */
  function lineChart(box, opts) {
    box.innerHTML = "";
    var pts = opts.points.filter(function (p) {
      return opts.series.some(function (s) { return p[s.key] != null; });
    });
    if (!pts.length) { box.appendChild(el("div", { "class": "empty" }, opts.empty || "No data for this range yet.")); return; }
    var W = box.clientWidth || 600, H = opts.height || 180, m = { l: 40, r: 56, t: 8, b: 22 };
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var lo = opts.yMin, hi = opts.yMax;
    if (lo == null || hi == null) {
      var vals = [];
      pts.forEach(function (p) { opts.series.forEach(function (s) { if (p[s.key] != null) vals.push(p[s.key]); }); });
      var vmin = Math.min.apply(null, vals), vmax = Math.max.apply(null, vals), pad = Math.max((vmax - vmin) * 0.15, 0.5);
      if (lo == null) lo = Math.max(0, vmin - pad);
      if (hi == null) hi = vmax + pad;
    }
    var yt = niceTicks(lo, hi, 4); lo = Math.min(lo, yt[0]); hi = Math.max(hi, yt[yt.length - 1]);
    var x = function (t) { return m.l + (t - opts.start) / (opts.end - opts.start) * iw; };
    var y = function (v) { return m.t + ih - (v - lo) / (hi - lo) * ih; };
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, height: H, role: "img", "aria-label": opts.label || "" });

    (opts.bands || []).forEach(function (b) {
      var x0 = Math.max(m.l, x(b.start_ts)), x1 = Math.min(m.l + iw, x(b.end_ts));
      if (x1 - x0 < 2) { x0 -= 1; x1 = x0 + 2; }
      s.appendChild(svg("rect", { x: x0, y: m.t, width: x1 - x0, height: ih, fill: cssVar("--band") }));
    });
    yt.forEach(function (v) {
      s.appendChild(svg("line", { "class": v === yt[0] ? "baseline" : "grid", x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v) }));
      var t = svg("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }); t.textContent = v; s.appendChild(t);
    });
    var tt = timeTicks(opts.start, opts.end, iw);
    tt.ticks.forEach(function (t) {
      var lab = svg("text", { x: x(t), y: H - 6, "text-anchor": "middle" });
      var d = new Date(t * 1000);
      lab.textContent = tt.withDate && d.getHours() === 0 && d.getMinutes() === 0
        ? d.toLocaleDateString([], { month: "short", day: "numeric" }) : dt(t, false);
      s.appendChild(lab);
    });

    var gap = (opts.bucket || 30) * 2.5, ends = [];
    opts.series.forEach(function (ser) {
      var d = "", prev = null, last = null;
      pts.forEach(function (p) {
        var v = p[ser.key];
        if (v == null) { prev = null; return; }
        d += (prev == null || p[opts.xKey] - prev > gap ? "M" : "L") + x(p[opts.xKey]).toFixed(1) + " " + y(v).toFixed(1);
        prev = p[opts.xKey]; last = p;
      });
      s.appendChild(svg("path", { d: d, fill: "none", stroke: ser.color, "stroke-width": 2,
        "stroke-linejoin": "round", "stroke-linecap": "round" }));
      if (last) ends.push({ ser: ser, y: y(last[ser.key]), x: x(last[opts.xKey]), v: last[ser.key] });
    });
    // Direct end labels, nudged apart when series overlap (e.g. input == output voltage).
    ends.sort(function (a, b) { return a.y - b.y; });
    for (var i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 13) ends[i].y = ends[i - 1].y + 13;
    ends.forEach(function (e) {
      var t = svg("text", { "class": "endlabel", x: Math.min(e.x, m.l + iw) + 6, y: e.y + 4 });
      t.textContent = e.ser.label; s.appendChild(t);
    });

    // hover layer: crosshair + markers + tooltip
    var cross = svg("line", { "class": "cross", y1: m.t, y2: m.t + ih, visibility: "hidden" });
    s.appendChild(cross);
    var marks = opts.series.map(function (ser) {
      var c = svg("circle", { r: 4, fill: ser.color, stroke: cssVar("--surface-1"), "stroke-width": 2, visibility: "hidden" });
      s.appendChild(c); return c;
    });
    var hit = svg("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent" });
    s.appendChild(hit);
    hit.addEventListener("mousemove", function (evt) {
      var r = s.getBoundingClientRect(), px = (evt.clientX - r.left) * (W / r.width);
      var t = opts.start + (px - m.l) / iw * (opts.end - opts.start), best = null, bd = Infinity;
      pts.forEach(function (p) { var dd = Math.abs(p[opts.xKey] - t); if (dd < bd) { bd = dd; best = p; } });
      if (!best) return;
      var cx = x(best[opts.xKey]);
      cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.setAttribute("visibility", "visible");
      var rows = [];
      opts.series.forEach(function (ser, i) {
        var v = best[ser.key];
        if (v == null) { marks[i].setAttribute("visibility", "hidden"); return; }
        marks[i].setAttribute("cx", cx); marks[i].setAttribute("cy", y(v)); marks[i].setAttribute("visibility", "visible");
        rows.push({ color: ser.color, label: ser.label, value: ser.fmt(v) });
      });
      if (best.ob) rows.push({ label: "Power", value: "on battery" });
      if (best.nocomm) rows.push({ label: "UPS", value: "unreachable" });
      showTip(evt, dt(best[opts.xKey]), rows);
    });
    hit.addEventListener("mouseleave", function () {
      cross.setAttribute("visibility", "hidden");
      marks.forEach(function (c) { c.setAttribute("visibility", "hidden"); });
      hideTip();
    });
    box.appendChild(s);
  }

  function dotChart(box, items) {
    box.innerHTML = "";
    if (items.length < 1) {
      box.appendChild(el("div", { "class": "empty" }, "No outage of 2 minutes or more yet. The trend builds up over time."));
      return;
    }
    var W = box.clientWidth || 400, H = 170, m = { l: 40, r: 16, t: 22, b: 22 }, iw = W - m.l - m.r, ih = H - m.t - m.b;
    var ts = items.map(function (o) { return o.start_ts; }), rates = items.map(function (o) { return o.rate; });
    var t0 = Math.min.apply(null, ts), t1 = Math.max.apply(null, ts);
    if (t1 - t0 < 86400) { t0 -= 43200; t1 += 43200; }
    var padT = (t1 - t0) * 0.04; t0 -= padT; t1 += padT;   // keep end dots off the axes
    var yt = niceTicks(0, Math.max.apply(null, rates) * 1.2, 4), hi = yt[yt.length - 1];
    var x = function (t) { return m.l + (t - t0) / (t1 - t0) * iw; }, y = function (v) { return m.t + ih - v / hi * ih; };
    var s = svg("svg", { viewBox: "0 0 " + W + " " + H, height: H, role: "img", "aria-label": "Discharge rate per outage" });
    yt.forEach(function (v) {
      s.appendChild(svg("line", { "class": v === 0 ? "baseline" : "grid", x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v) }));
      var t = svg("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }); t.textContent = v; s.appendChild(t);
    });
    [t0, t1].forEach(function (t, i) {
      var lab = svg("text", { x: x(t), y: H - 6, "text-anchor": i ? "end" : "start" });
      lab.textContent = new Date(t * 1000).toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
      s.appendChild(lab);
    });
    var unit = svg("text", { x: m.l, y: 11 }); unit.textContent = "% per min"; s.appendChild(unit);
    items.forEach(function (o) {
      var c = svg("circle", { cx: x(o.start_ts), cy: y(o.rate), r: 5, fill: cssVar("--series-1"),
        stroke: cssVar("--surface-1"), "stroke-width": 2 });
      var hitc = svg("circle", { cx: x(o.start_ts), cy: y(o.rate), r: 12, fill: "transparent" });
      hitc.addEventListener("mousemove", function (evt) {
        showTip(evt, dt(o.start_ts), [
          { label: "Rate", value: o.rate.toFixed(2) + " %/min" },
          { label: "Duration", value: dur(o.duration) },
          { label: "Charge", value: fmtPct(o.start_charge) + " → " + fmtPct(o.min_charge) }]);
      });
      hitc.addEventListener("mouseleave", hideTip);
      s.appendChild(c); s.appendChild(hitc);
    });
    box.appendChild(s);
  }

  function dataTable(box, cols, rows) {
    box.innerHTML = "";
    var t = el("table"), hr = el("tr");
    cols.forEach(function (c) { hr.appendChild(el("th", c.num ? { "class": "num" } : {}, c.label)); });
    var th = el("thead"); th.appendChild(hr); t.appendChild(th);
    var tb = el("tbody");
    rows.slice().reverse().forEach(function (r) {
      var tr = el("tr");
      cols.forEach(function (c) { tr.appendChild(el("td", c.num ? { "class": "num" } : {}, c.fmt(r))); });
      tb.appendChild(tr);
    });
    t.appendChild(tb); box.appendChild(t);
  }

  function fmtPct(v) { return v == null ? "–" : Math.round(v) + "%"; }
  function fmtV(v) { return v == null ? "–" : v.toFixed(1) + " V"; }

  // ------------------------------------------------------------ renderers
  function renderStatus(b) {
    var v = b.live;
    if (!v) {
      setState("critical", "No communication", "upsd: " + (b.comm_error || "unreachable"));
    } else {
      var flags = (v["ups.status"] || "").split(" ");
      var ob = flags.indexOf("OB") >= 0, lb = flags.indexOf("LB") >= 0, fsd = flags.indexOf("FSD") >= 0;
      if (fsd) setState("critical", "Shutting down", "DSM forced shutdown");
      else if (ob && lb) setState("critical", "Low battery", "on battery for " + dur(b.now - b.since));
      else if (ob) setState("warning", "On battery", "for " + dur(b.now - b.since));
      else setState("good", "On mains", "since " + dt(b.since));
      renderBattery(num(v["battery.charge"]), num(v["battery.charge.low"]), v["battery.runtime"], ob);
      $("load").textContent = fmtPct(num(v["ups.load"]));
      $("vin").textContent = fmtV(num(v["input.voltage"]));
      $("vout").textContent = fmtV(num(v["output.voltage"]));
      $("beeper").textContent = v["ups.beeper.status"] || "–";
      $("beeper").title = "As reported by the UPS (a read-only field; see Controls)";
      $("device").textContent = [v["device.mfr"], v["device.model"], v["device.serial"] && "serial " + v["device.serial"],
        v["driver.version"] && "NUT " + v["driver.version"] + " (" + v["driver.parameter.subdriver"] + ")"]
        .filter(Boolean).join(" · ");
    }
    $("updated").textContent = "Updated " + dt(b.now, false) +
      (b.collector.running ? "" : " · collector not running");
    banner(b.collector.running ? "" :
      "The background collector isn't recording (last reading " + (b.collector.last_poll ? dt(b.collector.last_poll) : "never") +
      "). Start the package in Package Center.");
    renderHealth(b.health);
    renderControls(b);
  }

  // ------------------------------------------------------------ controls
  var ctlBusy = false;
  function renderControls(b) {
    var c = b.controls || {}, v = b.live || {};
    var ob = v["ups.status"] && v["ups.status"].split(" ").indexOf("OB") >= 0;
    $("ctl-beeper").textContent = v["ups.beeper.status"] || "unknown";
    $("ctl-test").textContent = v["ups.test.result"] || "none reported";
    $("ctl-state").textContent = c.available ? "via us3000-ctl v" + c.version
      : "unavailable: " + (c.error || "control service not reachable");
    document.querySelectorAll("#controls [data-cmd], #deep-ask").forEach(function (btn) {
      var cmd = btn.getAttribute("data-cmd") || "test.battery.start.deep";
      var allowed = c.available && (c.allowed || []).indexOf(cmd) >= 0;
      var testWhileOB = ob && cmd.indexOf("test.battery.start") === 0;
      btn.disabled = ctlBusy || !allowed || testWhileOB || !b.live;
      if (btn.dataset.tip === undefined) btn.dataset.tip = btn.title || "";
      btn.title = !c.available ? "Needs the us3000 addon v1.2.0 or later"
        : testWhileOB ? "Not while the UPS is on battery" : btn.dataset.tip;
    });
  }
  function ctlMsg(text, ok) {
    var m = $("ctl-msg"); m.textContent = text; m.className = "ctl-msg " + (ok ? "ok" : "err");
  }
  function sendCmd(cmd, confirmed) {
    if (ctlBusy) return;
    ctlBusy = true;
    document.querySelectorAll("#controls .btn").forEach(function (x) { x.disabled = true; });
    ctlMsg("Sending “" + (CMD_LABEL[cmd] || cmd) + "”…", true);
    post({ action: "control" }, { cmd: cmd, confirm: !!confirmed })
      .then(function (r) {
        if (r.ok) ctlMsg((CMD_LABEL[cmd] || cmd) + ": accepted by the UPS.", true);
        else ctlMsg((CMD_LABEL[cmd] || cmd) + ": " + (r.error || r.result || "failed") + ".", false);
      })
      .catch(function (e) { ctlMsg("Couldn't reach the app's backend: " + e.message, false); })
      .then(function () {
        ctlBusy = false;
        $("deep-confirm").hidden = true;
        // the UPS needs a poll cycle or two before the new state shows up
        loadStatus(); setTimeout(loadStatus, 6000); setTimeout(loadSlow, 6000);
      });
  }
  document.querySelectorAll("#controls [data-cmd]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      sendCmd(btn.getAttribute("data-cmd"), btn.getAttribute("data-confirm") === "1");
    });
  });
  $("deep-ask").addEventListener("click", function () { $("deep-confirm").hidden = false; });
  $("deep-cancel").addEventListener("click", function () { $("deep-confirm").hidden = true; });

  var STATE_ICON = { good: "✓", warning: "!", critical: "×", pending: "…" };
  function setState(level, text, hint) {
    $("state").textContent = text;
    $("since").textContent = hint || "";
    $("hero-state").setAttribute("data-level", level);
    $("state-icon").textContent = STATE_ICON[level] || "";
  }

  function renderBattery(charge, low, runtime, ob) {
    $("charge").textContent = fmtPct(charge);
    $("charge-low").textContent = low != null ? "low battery at " + fmtPct(low) : "";
    var g = $("gauge"), pct = charge == null ? 0 : Math.max(0, Math.min(100, charge));
    $("gauge-fill").style.width = pct + "%";
    g.setAttribute("aria-valuenow", pct);
    g.setAttribute("data-level", low != null && charge != null
      ? (charge <= low ? "critical" : charge <= low * 2 ? "warning" : "good") : "good");
    $("gauge-low").style.left = (low != null ? Math.max(0, Math.min(100, low)) : 0) + "%";
    $("gauge-low").hidden = low == null;
    $("runtime-line").textContent = runtime != null
      ? "About " + dur(runtime) + " of runtime left (UPS estimate)"
      : ob ? "Runtime estimate not available yet" : "Runtime is only estimated while on battery";
  }

  function renderHealth(h) {
    var ul = $("checks"); ul.innerHTML = "";
    if (!h) { $("health-summary").textContent = "Waiting for the collector's first check…"; return; }
    var words = { ok: "Everything checks out.", warning: "Something needs a look.", critical: "The addon isn't working as expected." };
    $("health-summary").textContent = words[h.level] + " Checked " + dt(h.checked_at) + ".";
    var chip = { ok: ["✓", "Healthy"], warning: ["!", "Needs a look"], critical: ["×", "Problem"] }[h.level] || ["…", "Checking"];
    $("health-chip").setAttribute("data-level", h.level);
    $("health-chip-icon").textContent = chip[0];
    $("health-chip-text").textContent = chip[1];
    var glyph = { ok: "✓", warning: "!", critical: "×" };
    h.checks.forEach(function (c) {
      var li = el("li");
      li.appendChild(el("span", { "class": "icon " + c.level, "aria-label": c.level }, glyph[c.level]));
      li.appendChild(el("span", {}, c.name));
      li.appendChild(el("span", { "class": "d" }, c.detail));
      ul.appendChild(li);
    });
    $("addon-log").textContent = h.addon_log ? h.addon_log.join("") :
      "The addon log (/var/log/rr-us3000.log) isn't readable: it's root-only in older us3000 addon builds. Install the latest us3000.addon to fix this.";
  }

  function renderHistory() {
    var h = state.history; if (!h) return;
    var common = { points: h.points, start: h.start, end: h.end, bucket: h.bucket, bands: h.outages, xKey: "t" };
    lineChart($("chart-charge"), Object.assign({}, common, { yMin: 0, yMax: 100, label: "Battery charge",
      series: [{ key: "charge", label: "Charge", color: cssVar("--series-1"), fmt: fmtPct }] }));
    lineChart($("chart-volts"), Object.assign({}, common, { label: "Input and output voltage",
      series: [{ key: "vin", label: "Input", color: cssVar("--series-1"), fmt: fmtV },
               { key: "vout", label: "Output", color: cssVar("--series-2"), fmt: fmtV }] }));
    state.tables.charge = [[{ label: "Time", fmt: function (r) { return dt(r.t); } },
      { label: "Charge", num: true, fmt: function (r) { return fmtPct(r.charge); } },
      { label: "Lowest", num: true, fmt: function (r) { return fmtPct(r.charge_min); } },
      { label: "Power", fmt: function (r) { return r.nocomm ? "no data" : r.ob ? "battery" : "mains"; } }], h.points];
    state.tables.volts = [[{ label: "Time", fmt: function (r) { return dt(r.t); } },
      { label: "Input", num: true, fmt: function (r) { return fmtV(r.vin); } },
      { label: "Output", num: true, fmt: function (r) { return fmtV(r.vout); } },
      { label: "Load", num: true, fmt: function (r) { return fmtPct(r.load); } }], h.points];
    ["charge", "volts"].forEach(function (k) {
      var box = $("table-" + k); if (!box.hidden) dataTable(box, state.tables[k][0], state.tables[k][1]);
    });
  }

  function renderOutages() {
    var list = state.outages, tb = document.querySelector("#outages tbody");
    tb.innerHTML = "";
    $("outages-empty").hidden = list.length > 0;
    var reason = { power_restored: "power restored", nas_down: "NAS went down" };
    list.forEach(function (o) {
      var tr = el("tr");
      [dt(o.start_ts), dur(o.duration) + (o.ongoing ? " (ongoing)" : ""),
       fmtPct(o.start_charge) + " → " + fmtPct(o.min_charge),
       o.rate != null ? o.rate.toFixed(2) + " %/min" : "–",
       o.runtime_start != null ? dur(o.runtime_start) : "–",
       o.lb_ts ? "at " + fmtPct(o.lb_charge) + " after " + dur(o.lb_after) : "–",
       o.ongoing ? "–" : (reason[o.end_reason] || o.end_reason || "–")]
        .forEach(function (v, i) { tr.appendChild(el("td", i >= 2 && i <= 4 ? { "class": "num" } : {}, v)); });
      tb.appendChild(tr);
    });
    var wear = list.filter(function (o) { return o.rate != null; }).reverse();
    dotChart($("chart-wear"), wear);
    var lbs = list.filter(function (o) { return o.lb_ts; });
    $("lb-note").textContent = lbs.length
      ? "Low battery last triggered at " + fmtPct(lbs[0].lb_charge) + ", " + dur(lbs[0].lb_after) + " into the outage of " + dt(lbs[0].start_ts) + "."
      : "Low battery hasn't triggered yet. When it does, this shows how long into the outage it came.";
  }

  function renderEvents(evts) {
    var ul = $("events"); ul.innerHTML = "";
    evts.filter(function (e) { return e.kind !== "COLLECTOR_START"; }).slice(0, 50).forEach(function (e) {
      var li = el("li");
      li.appendChild(el("span", { "class": "ts" }, dt(e.ts)));
      li.appendChild(el("span", {}, "")).appendChild(
        el("span", { "class": "badge " + (KIND_LEVEL[e.kind] || "") }, KIND_LABEL[e.kind] || e.kind));
      li.appendChild(el("span", {}, e.detail || ""));
      ul.appendChild(li);
    });
    if (!ul.children.length) ul.appendChild(el("li", {}, "No power events yet."));
  }

  // ------------------------------------------------------------ loading
  function loadStatus() {
    return api({ action: "status" }).then(renderStatus).catch(function (e) { banner(e.message); });
  }
  function loadHistory() {
    return api({ action: "history", range: state.range })
      .then(function (h) { state.history = h; renderHistory(); }).catch(function (e) { banner(e.message); });
  }
  function loadSlow() {
    return Promise.all([
      api({ action: "outages" }).then(function (b) { state.outages = b.outages; renderOutages(); }),
      api({ action: "events", limit: 80 }).then(function (b) { renderEvents(b.events); })
    ]).catch(function (e) { banner(e.message); });
  }

  $("ranges").addEventListener("click", function (e) {
    var r = e.target.getAttribute("data-range"); if (!r) return;
    state.range = r;
    Array.prototype.forEach.call(this.children, function (b) { b.classList.toggle("on", b === e.target); });
    loadHistory();
  });
  document.querySelectorAll("[data-table]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var k = btn.getAttribute("data-table"), box = $("table-" + k);
      box.hidden = !box.hidden;
      btn.textContent = box.hidden ? "Table" : "Hide table";
      if (!box.hidden && state.tables[k]) dataTable(box, state.tables[k][0], state.tables[k][1]);
    });
  });
  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { renderHistory(); renderOutages(); }, 150);
  });

  loadStatus(); loadHistory(); loadSlow();
  setInterval(loadStatus, 10000);
  setInterval(loadHistory, 60000);
  setInterval(loadSlow, 60000);
})();
