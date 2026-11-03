/* BSR Radar dashboard: fetches bsr_shirts.json, polls for new scrapes,
   sorts/filters client-side. Configure through window.BSR_CONFIG. */
(function () {
  'use strict';

  var cfg = Object.assign({
    dataUrl: '',
    pollMinutes: 10,   // how often the page re-checks the JSON
    scrapeHours: 4,    // GitHub Action cadence, used for the "next scrape" hint
    newDays: 3         // uploads younger than this get a NEW tag
  }, window.BSR_CONFIG || {});

  var root = document.getElementById('bsr-app');
  if (!root) return;
  function $(id) { return document.getElementById(id); }

  var PREF_KEY = 'bsr-radar-prefs';
  var DAY = 86400000;
  var state = {
    data: null, stamp: null, loading: false, nextPoll: 0, failures: 0,
    sort: 'bsr', q: '', maxBsr: 0, maxAge: 0, auto: true
  };

  // ---------- prefs (per-viewer convenience only) ----------
  try {
    var saved = JSON.parse(localStorage.getItem(PREF_KEY) || '{}');
    ['sort', 'maxBsr', 'maxAge', 'auto'].forEach(function (k) { if (k in saved) state[k] = saved[k]; });
  } catch (e) { /* storage blocked */ }
  function savePrefs() {
    try {
      localStorage.setItem(PREF_KEY, JSON.stringify({ sort: state.sort, maxBsr: state.maxBsr, maxAge: state.maxAge, auto: state.auto }));
    } catch (e) { /* ignore */ }
  }

  // ---------- helpers ----------
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function safeUrl(u) { return /^https:\/\//i.test(u || '') ? u : ''; }
  function num(n) { return Number(n).toLocaleString('en-US'); }
  function compact(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1).replace(/\.0$/, '') + 'M';
    if (n >= 1e3) return Math.round(n / 1e3) + 'k';
    return String(n);
  }
  function parseDay(iso) { // 'YYYY-MM-DD' -> UTC ms
    if (!iso) return NaN;
    var p = iso.split('-');
    return Date.UTC(+p[0], +p[1] - 1, +p[2]);
  }
  function ageDays(iso) {
    var t = parseDay(iso);
    return isNaN(t) ? null : Math.max(0, Math.floor((Date.now() - t) / DAY));
  }
  function fmtDay(iso) {
    var t = parseDay(iso);
    return isNaN(t) ? 'Unknown' : new Date(t).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
  }
  function ago(ms) {
    var s = Math.round((Date.now() - ms) / 1000);
    if (s < 60) return 'just now';
    var m = Math.round(s / 60);
    if (m < 60) return m + ' min ago';
    var h = Math.floor(m / 60);
    if (h < 48) return h + ' h ' + (m % 60) + ' min ago';
    return Math.floor(h / 24) + ' days ago';
  }
  function clock(ms) {
    var s = Math.max(0, Math.round(ms / 1000));
    var m = Math.floor(s / 60);
    return m + ':' + String(s % 60).padStart(2, '0');
  }
  function tier(bsr) {
    if (bsr <= 50000) return 'hot';
    if (bsr <= 200000) return 'strong';
    if (bsr <= 500000) return 'ok';
    if (bsr <= 1500000) return 'slow';
    return 'cold';
  }
  // Relative BSR change from the oldest to the newest history point; positive = improving.
  function trend(s) {
    var h = (s.bsr_history || []).filter(function (p) { return p && p.bsr > 0; });
    if (h.length < 2) return null;
    var first = h[0].bsr, last = h[h.length - 1].bsr;
    return { from: first, to: last, pct: (first - last) / first, points: h };
  }
  function sparkline(points) {
    var w = 84, hgt = 24, pad = 2;
    var vals = points.map(function (p) { return Math.log10(p.bsr); });
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    var span = max - min || 1;
    var d = vals.map(function (v, i) {
      var x = pad + (i / (vals.length - 1)) * (w - pad * 2);
      var y = pad + ((v - min) / span) * (hgt - pad * 2); // lower BSR draws higher
      return (i ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1);
    }).join(' ');
    var improving = vals[vals.length - 1] <= vals[0];
    return '<svg aria-hidden="true" width="' + w + '" height="' + hgt + '" viewBox="0 0 ' + w + ' ' + hgt + '">' +
      '<path d="' + d + '" fill="none" stroke="' + (improving ? 'var(--bsr-good)' : 'var(--bsr-bad)') +
      '" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  }
  function median(arr) {
    if (!arr.length) return null;
    var a = arr.slice().sort(function (x, y) { return x - y; });
    var mid = a.length >> 1;
    return a.length % 2 ? a[mid] : Math.round((a[mid - 1] + a[mid]) / 2);
  }

  var toastTimer;
  function toast(msg) {
    var el = $('bsr-toast');
    el.textContent = msg;
    el.classList.add('is-on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove('is-on'); }, 3200);
  }
  function notice(msg) {
    var el = $('bsr-notice');
    el.hidden = !msg;
    el.textContent = msg || '';
  }
  function setDot(s) { $('bsr-dot').setAttribute('data-state', s); }

  // ---------- data ----------
  function shirts() {
    return ((state.data && state.data.shirts) || []).filter(function (s) { return s && s.bsr > 0; });
  }

  function visible() {
    var q = state.q.trim().toLowerCase();
    var list = shirts().filter(function (s) {
      if (state.maxBsr && s.bsr > state.maxBsr) return false;
      if (state.maxAge) {
        var a = ageDays(s.date_first_available);
        if (a === null || a > state.maxAge) return false;
      }
      if (q) {
        var hay = [s.title, s.brand, s.asin].join(' ').toLowerCase();
        if (hay.indexOf(q) === -1) return false;
      }
      return true;
    });
    var byPos = function (a, b) { return (a.search_position || 0) - (b.search_position || 0); };
    var cmp = {
      bsr: function (a, b) { return a.bsr - b.bsr || byPos(a, b); },
      newest: function (a, b) {
        var ta = parseDay(a.date_first_available), tb = parseDay(b.date_first_available);
        if (isNaN(ta) !== isNaN(tb)) return isNaN(ta) ? 1 : -1;
        return (tb - ta) || byPos(a, b);
      },
      movers: function (a, b) {
        var ta = trend(a), tb = trend(b);
        if (!ta !== !tb) return ta ? -1 : 1;
        return ((tb ? tb.pct : 0) - (ta ? ta.pct : 0)) || a.bsr - b.bsr;
      },
      price: function (a, b) {
        var pa = a.price_value, pb = b.price_value;
        if ((pa == null) !== (pb == null)) return pa == null ? 1 : -1;
        return (pa - pb) || a.bsr - b.bsr;
      }
    }[state.sort] || function () { return 0; };
    return list.sort(cmp);
  }

  // ---------- render ----------
  function renderStats() {
    var all = shirts();
    var ranks = all.map(function (s) { return s.bsr; });
    var prices = all.map(function (s) { return s.price_value; }).filter(function (p) { return p > 0; });
    var best = all.slice().sort(function (a, b) { return a.bsr - b.bsr; })[0];
    var newest = all.map(function (s) { return s.date_first_available; }).filter(Boolean).sort().pop();
    var fresh = all.filter(function (s) { var a = ageDays(s.date_first_available); return a !== null && a <= 7; }).length;
    var tiles = [
      ['Shirts with BSR', num(all.length), state.data && state.data.stats ? num(state.data.stats.watching || 0) + ' new uploads on watch' : ''],
      ['Best BSR', best ? '#' + num(best.bsr) : '-', best ? best.brand || best.asin : ''],
      ['Median BSR', ranks.length ? '#' + compact(median(ranks)) : '-', ranks.filter(function (r) { return r <= 100000; }).length + ' under 100k'],
      ['Avg price', prices.length ? '$' + (prices.reduce(function (a, b) { return a + b; }, 0) / prices.length).toFixed(2) : '-', prices.length ? '$' + Math.min.apply(null, prices).toFixed(2) + ' - $' + Math.max.apply(null, prices).toFixed(2) : ''],
      ['Newest upload', newest ? fmtDay(newest) : '-', fresh + ' uploaded in last 7 days']
    ];
    $('bsr-stats').innerHTML = tiles.map(function (t) {
      return '<div class="bsr-stat"><span class="bsr-stat-label">' + esc(t[0]) + '</span>' +
        '<span class="bsr-stat-value">' + esc(t[1]) + '</span>' +
        '<span class="bsr-stat-note">' + esc(t[2] || ' ') + '</span></div>';
    }).join('');
  }

  function card(s) {
    var url = safeUrl(s.url) || 'https://www.amazon.com/dp/' + encodeURIComponent(s.asin);
    var age = ageDays(s.date_first_available);
    var tr = trend(s);
    var trendHtml = tr
      ? sparkline(tr.points) + '<span class="' + (tr.pct >= 0 ? 'bsr-up' : 'bsr-down') + '">' +
        (tr.pct >= 0 ? '▲ ' : '▼ ') + Math.abs(tr.pct * 100).toFixed(0) + '%</span>' +
        '<span>from #' + compact(tr.from) + '</span>'
      : '<span>Tracking since ' + (s.first_seen ? ago(Date.parse(s.first_seen)) : 'this run') + '</span>';
    var sub = (s.sub_ranks || []).slice(0, 2).map(function (r) {
      return '#' + num(r.rank) + ' in ' + esc(r.category);
    }).join(' &#183; ');
    var img = safeUrl(s.image);
    return '<article class="bsr-card">' +
      '<a class="bsr-thumb" href="' + esc(url) + '" target="_blank" rel="noopener nofollow">' +
        (img ? '<img src="' + esc(img) + '" alt="' + esc(s.title) + '" loading="lazy" referrerpolicy="no-referrer">' : '') +
      '</a>' +
      '<span class="bsr-badge tier-' + tier(s.bsr) + '" title="Best Sellers Rank">#' + num(s.bsr) + '</span>' +
      (age !== null && age <= cfg.newDays ? '<span class="bsr-new">NEW</span>' : '') +
      '<div class="bsr-body">' +
        '<div class="bsr-brandline">' + esc(s.brand || 'Unknown brand') + '</div>' +
        '<h3 class="bsr-name"><a href="' + esc(url) + '" target="_blank" rel="noopener nofollow" title="' + esc(s.title) + '">' + esc(s.title || s.asin) + '</a></h3>' +
        '<div class="bsr-rankline"><b>#' + num(s.bsr) + '</b> in ' + esc(s.bsr_category || 'Amazon') + (sub ? '<br>' + sub : '') + '</div>' +
        '<div class="bsr-meta"><span class="bsr-price">' + esc(s.price || '-') + '</span>' +
          '<span title="Date First Available">' + fmtDay(s.date_first_available) + (age !== null ? ' &#183; ' + age + 'd' : '') + '</span></div>' +
        '<div class="bsr-trend">' + trendHtml + '</div>' +
        '<div class="bsr-actions">' +
          '<button class="bsr-btn" type="button" data-copy="' + esc(s.asin) + '">Copy ASIN</button>' +
          '<a class="bsr-btn bsr-btn-primary" href="' + esc(url) + '" target="_blank" rel="noopener nofollow">Amazon &#8599;</a>' +
        '</div>' +
      '</div></article>';
  }

  function render() {
    var list = visible();
    var total = shirts().length;
    var grid = $('bsr-grid'), empty = $('bsr-empty');
    renderStats();
    grid.innerHTML = list.map(card).join('');
    empty.hidden = list.length > 0;
    if (!list.length) {
      empty.textContent = total
        ? 'No shirts match these filters.'
        : 'No shirts with a BSR yet - the next scrape will fill this in.';
    }
    $('bsr-count').textContent = total ? 'Showing ' + list.length + ' of ' + total + ' shirts' : '';
    syncSort();
  }

  function syncSort() {
    Array.prototype.forEach.call($('bsr-sort').querySelectorAll('button'), function (b) {
      b.setAttribute('aria-pressed', String(b.getAttribute('data-sort') === state.sort));
    });
  }

  function renderClock() {
    var upd = $('bsr-updated'), cd = $('bsr-countdown');
    if (state.stamp) {
      var t = Date.parse(state.stamp);
      var next = t + cfg.scrapeHours * 3600000;
      upd.textContent = 'Updated ' + ago(t);
      upd.title = new Date(t).toUTCString() + ' | next scrape due ' + new Date(next).toUTCString();
    }
    cd.textContent = state.auto && !state.loading && state.nextPoll ? 'next check ' + clock(state.nextPoll - Date.now()) : '';
  }

  // ---------- fetching ----------
  function load(manual) {
    if (state.loading) return;
    if (!cfg.dataUrl || /YOUR_GITHUB_USER/.test(cfg.dataUrl)) {
      setDot('error');
      $('bsr-updated').textContent = 'Not configured';
      notice('Set BSR_CONFIG.dataUrl to the raw GitHub URL of data/bsr_shirts.json.');
      $('bsr-grid').innerHTML = '';
      return;
    }
    state.loading = true;
    setDot('loading');
    $('bsr-refresh').disabled = true;
    var sep = cfg.dataUrl.indexOf('?') === -1 ? '?' : '&';
    fetch(cfg.dataUrl + sep + '_=' + Date.now(), { cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        var changed = data.last_updated !== state.stamp;
        var hadData = !!state.stamp;
        state.data = data;
        state.stamp = data.last_updated || null;
        state.failures = 0;
        notice('');
        setDot('ok');
        if (changed || manual) render();
        if (changed && hadData) toast('New scrape loaded - ' + shirts().length + ' shirts');
        else if (manual) toast(changed ? 'Data loaded' : 'Already up to date');
      })
      .catch(function (err) {
        state.failures++;
        setDot('error');
        if (!state.data) {
          $('bsr-grid').innerHTML = '';
          $('bsr-updated').textContent = 'Could not load data';
        }
        notice('Could not load BSR data (' + err.message + '). ' + (state.data ? 'Showing the last loaded copy; ' : '') + 'retrying automatically.');
      })
      .then(function () {
        state.loading = false;
        $('bsr-refresh').disabled = false;
        // Back off up to 4x the poll interval after repeated failures.
        state.nextPoll = Date.now() + cfg.pollMinutes * 60000 * Math.min(4, Math.pow(2, state.failures ? state.failures - 1 : 0));
        renderClock();
      });
  }

  // ---------- events ----------
  $('bsr-sort').addEventListener('click', function (e) {
    var b = e.target.closest('button[data-sort]');
    if (!b) return;
    state.sort = b.getAttribute('data-sort');
    savePrefs();
    render();
  });
  var qTimer;
  $('bsr-q').addEventListener('input', function (e) {
    clearTimeout(qTimer);
    qTimer = setTimeout(function () { state.q = e.target.value; render(); }, 150);
  });
  $('bsr-max').addEventListener('change', function (e) { state.maxBsr = +e.target.value; savePrefs(); render(); });
  $('bsr-age').addEventListener('change', function (e) { state.maxAge = +e.target.value; savePrefs(); render(); });
  $('bsr-auto').addEventListener('change', function (e) { state.auto = e.target.checked; savePrefs(); renderClock(); });
  $('bsr-refresh').addEventListener('click', function () { load(true); });
  $('bsr-grid').addEventListener('click', function (e) {
    var b = e.target.closest('[data-copy]');
    if (!b) return;
    var asin = b.getAttribute('data-copy');
    var done = function () { toast('Copied ' + asin); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(asin).then(done, function () { window.prompt('Copy ASIN', asin); });
    } else {
      window.prompt('Copy ASIN', asin);
    }
  });
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && state.auto && Date.now() >= state.nextPoll) load(false);
  });

  // Restore controls from prefs, then start.
  $('bsr-max').value = String(state.maxBsr);
  $('bsr-age').value = String(state.maxAge);
  $('bsr-auto').checked = !!state.auto;
  syncSort();

  setInterval(function () {
    if (state.auto && !document.hidden && !state.loading && state.nextPoll && Date.now() >= state.nextPoll) load(false);
    renderClock();
  }, 1000);
  load(false);
})();
