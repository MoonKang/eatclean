(function () {
  'use strict';

  var GRADES = [
    { key: 'A', label: 'A', range: '90–100', color: '#1a8f4c' },
    { key: 'B', label: 'B', range: '80–89', color: '#c79a00' },
    { key: 'C', label: 'C', range: '70–79', color: '#e0701a' },
    { key: 'U', label: 'U', range: 'below 70', color: '#c92a2a' },
    { key: '', label: 'No score', range: '', color: '#7a828a' }
  ];
  var COLOR = {};
  GRADES.forEach(function (g) { COLOR[g.key] = g.color; });

  var all = [];
  var month = '';          // 'yyyy-mm': inspected in that month or later; '' for any time
  var showStale = false;   // places not inspected in STALE_MONTHS are probably closed
  var STALE_MONTHS = 18;
  var staleBefore = (function () {
    var d = new Date(); d.setMonth(d.getMonth() - STALE_MONTHS);
    return d.toISOString().slice(0, 10);
  })();
  function isStale(p) { return !!p.date && p.date < staleBefore; }
  var category = '';       // e.g. 'Korean', or '' for any (from OpenStreetMap, see collector/osm.py)
  var historyCache = {};   // bucket number -> promise of {facilityId: [inspections]}
  var active = { A: true, B: true, C: true, U: true, '': true };
  var popup = null;

  var map = new maplibregl.Map({
    container: 'map',
    style: 'https://tiles.openfreemap.org/styles/positron',
    center: [-83.6, 32.9],
    zoom: 6.3,
    hash: true   // keeps #zoom/lat/lng in the URL so a view can be bookmarked or shared
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
  map.addControl(new maplibregl.GeolocateControl({ trackUserLocation: false }), 'bottom-right');

  var colorExpr = ['match', ['get', 'grade'],
    'A', COLOR.A, 'B', COLOR.B, 'C', COLOR.C, 'U', COLOR.U, COLOR['']];

  function visible() {
    return all.filter(function (f) {
      var p = f.properties;
      if (!active[p.grade]) return false;
      if (category && (p.cat || '').split(', ').indexOf(category) === -1) return false;
      if (!showStale && !month && isStale(p)) return false;
      return !month || (p.date || '') >= month;   // '2026-11-03' >= '2026-11'
    });
  }

  function gradeOf(score) {
    if (score === null || score === undefined) return '';
    return score >= 90 ? 'A' : score >= 80 ? 'B' : score >= 70 ? 'C' : 'U';
  }

  function scoreBadge(score) {
    var has = score !== null && score !== undefined && score !== '';
    var badge = el('span', 'badge', has ? String(score) : '–');
    badge.style.background = COLOR[has ? gradeOf(Number(score)) : ''];
    return badge;
  }

  function setupStaleToggle() {
    var box = document.getElementById('show-stale');
    var n = all.filter(function (f) { return isStale(f.properties); }).length;
    document.getElementById('stale-count').textContent = n.toLocaleString();
    box.addEventListener('change', function () { showStale = box.checked; updateMenuDot(); refresh(); });
  }

  function buildMonthSelect() {
    var counts = {};
    all.forEach(function (f) {
      var m = (f.properties.date || '').slice(0, 7);
      if (m) counts[m] = (counts[m] || 0) + 1;
    });
    var select = document.getElementById('month');
    Object.keys(counts).sort().reverse().forEach(function (m) {
      var p = m.split('-');
      var label = new Date(Number(p[0]), Number(p[1]) - 1, 1)
        .toLocaleDateString(undefined, { year: 'numeric', month: 'long' });
      var opt = el('option', null, label);
      opt.value = m;
      select.appendChild(opt);
    });
    select.addEventListener('change', function () {
      month = select.value;
      if (popup) popup.remove();
      updateMenuDot();
      refresh();
    });
  }

  function loadHistory(p) {
    if (!historyCache[p.b]) {
      historyCache[p.b] = fetch('data/history/' + p.b + '.json').then(function (r) {
        if (r.status === 404) return {};
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
      historyCache[p.b].catch(function () { delete historyCache[p.b]; });
    }
    return historyCache[p.b].then(function (shard) { return shard[p.id] || []; });
  }

  function renderInspection(insp) {
    var item = el('li', 'insp');
    var head = el('div', 'insp-head');
    head.appendChild(scoreBadge(insp.score));
    var text = el('div');
    text.appendChild(el('div', 'insp-date', formatDate(insp.date)));
    text.appendChild(el('div', 'insp-purpose', insp.purpose || 'Inspection'));
    head.appendChild(text);
    if (insp.report) {
      var a = el('a', 'insp-report', 'Official report');
      a.href = insp.report;
      a.target = '_blank';
      a.rel = 'noopener';
      head.appendChild(a);
    }
    item.appendChild(head);

    var v = insp.violations || [];
    if (!v.length) {
      item.appendChild(el('div', 'insp-none', 'No violations recorded'));
      return item;
    }
    var points = v.reduce(function (sum, x) { return sum + (x.points || 0); }, 0);
    var details = el('details');
    var summary = el('summary', null,
      v.length + (v.length === 1 ? ' violation' : ' violations') + (points ? ' · ' + points + ' points' : ''));
    var repeats = v.filter(function (x) { return x.repeat; }).length;
    if (repeats) summary.appendChild(el('span', 'tag tag-repeat', repeats + ' repeat'));
    details.appendChild(summary);
    var ul = el('ul', 'viols');
    v.forEach(function (x) {
      var li = el('li');
      var title = el('div', 'viol-title', x.item || x.code || 'Violation');
      if (x.points) title.appendChild(el('span', 'tag', '−' + x.points));
      if (x.repeat) title.appendChild(el('span', 'tag tag-repeat', 'Repeat'));
      if (x.corrected) title.appendChild(el('span', 'tag', 'Corrected on site'));
      li.appendChild(title);
      if (x.notes) li.appendChild(el('div', 'viol-notes', x.notes));
      ul.appendChild(li);
    });
    details.appendChild(ul);
    item.appendChild(details);
    return item;
  }

  function openHistory(feature) {
    if (isPhone()) setPanel(false);  // the history drawer needs the screen
    var p = feature.properties;
    var drawer = document.getElementById('drawer');
    var body = document.getElementById('drawer-body');
    body.textContent = '';
    body.appendChild(el('h2', null, p.name));
    body.appendChild(el('div', 'addr', p.address));
    var status = el('p', 'meta', 'Loading history…');
    body.appendChild(status);
    drawer.hidden = false;
    drawer.dataset.id = p.id;

    loadHistory(p).then(function (list) {
      if (drawer.dataset.id !== p.id) return;   // another restaurant was opened meanwhile
      if (!list.length) {
        status.textContent = 'Past inspections have not been collected for this restaurant yet.';
        return;
      }
      status.textContent = list.length + (list.length === 1 ? ' inspection on record' : ' inspections on record');
      var ul = el('ul', 'insp-list');
      list.forEach(function (insp) { ul.appendChild(renderInspection(insp)); });
      body.appendChild(ul);
    }).catch(function (err) {
      if (drawer.dataset.id === p.id) status.textContent = 'Could not load history (' + err.message + ').';
    });
  }

  document.getElementById('drawer-close').addEventListener('click', function () {
    document.getElementById('drawer').hidden = true;
  });

  function refresh() {
    var src = map.getSource('facilities');
    if (src) src.setData({ type: 'FeatureCollection', features: visible() });
    var drawer = document.getElementById('drawer');
    if (!drawer.hidden && drawer.dataset.id === 'nearby' && nearby.center) showNearby();  // follow the filters
  }

  function formatDate(iso) {
    if (!iso) return 'date unknown';
    var p = iso.split('-');
    var d = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function showPopup(feature, opts) {
    if (isPhone()) setPanel(false);  // keep the popup from opening under the panel
    var p = feature.properties;
    var box = el('div', 'popup');
    box.appendChild(el('h2', null, p.name));
    box.appendChild(el('div', 'addr', p.address));
    if (p.cat) {
      // From OpenStreetMap (matched by name and location) or the name itself; see collector/osm.py.
      var cat = el('div', 'cat', p.cat);
      cat.title = 'Category from OpenStreetMap or the restaurant\'s name';
      box.appendChild(cat);
    }

    var row = el('div', 'score');
    row.appendChild(scoreBadge(p.score));
    row.appendChild(el('span', null, p.grade ? 'Grade ' + p.grade : 'No score'));
    row.appendChild(el('span', 'when', '· ' + formatDate(p.date)));
    box.appendChild(row);
    if (p.prev != null && p.score != null && p.score !== '') {
      var d = Number(p.score) - Number(p.prev);
      var trend = el('div', 'trend ' + (d > 0 ? 'up' : d < 0 ? 'down' : 'same'),
        (d > 0 ? '↑ Up from ' : d < 0 ? '↓ Down from ' : '= Same as ') + p.prev + ' on ' + formatDate(p.prev_date));
      box.appendChild(trend);
    }
    if (p.src === 'gnr') {
      box.appendChild(el('div', 'src-note', 'From GNR Public Health, copied by hand. May be out of date.'));
    }

    var more = el('button', 'more', 'More history');
    more.type = 'button';
    more.addEventListener('click', function () { openHistory(feature); });
    var actions = el('div', 'actions');
    if (opts && opts.backToList && document.getElementById('drawer').hidden) {
      var back = el('button', 'more', '◀ List');
      back.type = 'button';
      back.title = 'Back to the list of places near you';
      back.addEventListener('click', function () { if (popup) popup.remove(); showNearby(); });
      actions.appendChild(back);
    }
    actions.appendChild(more);
    // A plain Google Maps search (no API): reviews, photos and hours are one click away.
    var g = el('a', 'more gmaps', 'Google Maps ↗');
    g.href = 'https://www.google.com/maps/search/?api=1&query=' +
      encodeURIComponent(p.name + ', ' + p.address + ', GA');
    g.target = '_blank';
    g.rel = 'noopener';
    g.title = 'Reviews, photos and hours on Google Maps';
    actions.appendChild(g);
    var share = el('button', 'more share', 'Share');
    share.type = 'button';
    share.addEventListener('click', function () { shareRestaurant(feature, share); });
    actions.appendChild(share);
    box.appendChild(actions);

    if (popup) popup.remove();
    popup = new maplibregl.Popup({ offset: 12, maxWidth: '340px' })
      .setLngLat(feature.geometry.coordinates)
      .setDOMContent(box)
      .addTo(map);
  }

  function buildGradeButtons() {
    var counts = {};
    all.forEach(function (f) { counts[f.properties.grade] = (counts[f.properties.grade] || 0) + 1; });
    var wrap = document.getElementById('grades');
    wrap.textContent = '';
    // Clicking a grade shows only that grade; clicking more adds them.
    // With none selected, every grade is shown.
    var selected = {};
    var buttons = [];
    function apply() {
      var any = GRADES.some(function (g) { return selected[g.key]; });
      GRADES.forEach(function (g) { active[g.key] = !any || !!selected[g.key]; });
      buttons.forEach(function (x) { x.b.setAttribute('aria-pressed', String(!!selected[x.key])); });
      wrap.classList.toggle('filtering', any);
      updateMenuDot();
      refresh();
    }
    GRADES.forEach(function (g) {
      if (!counts[g.key]) return;
      var b = el('button');
      b.type = 'button';
      b.setAttribute('aria-pressed', 'false');
      buttons.push({ b: b, key: g.key });
      b.title = g.range ? 'Score ' + g.range : 'No score on record';
      var dot = el('span', 'dot');
      dot.style.background = g.color;
      b.appendChild(dot);
      b.appendChild(el('span', null, g.label));
      b.addEventListener('click', function () {
        selected[g.key] = !selected[g.key];
        apply();
      });
      wrap.appendChild(b);
    });
  }

  // --- Google Maps links -----------------------------------------------------
  // Reads a pasted Google Maps URL. Place links carry the place's exact
  // position (!3d<lat>!4d<lon>) and its name (/place/<name>/); other links have
  // the map centre (@lat,lon) or a ?q= query. Short links redirect elsewhere and
  // can't be followed from the page, so they are only recognised.
  function parseMapsLink(text) {
    if (!/^https?:\/\//i.test(text) || !/google\.[a-z.]+\/maps|maps\.google\.|goo\.gl/i.test(text)) return null;
    if (/maps\.app\.goo\.gl|goo\.gl\/maps/i.test(text)) return { shortLink: true };
    var out = { lat: null, lon: null, name: null };
    var m = text.match(/!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)/) ||
            text.match(/[?&](?:q|query|ll|destination)=(-?\d+\.\d+)(?:,|%2C)(-?\d+\.\d+)/i) ||
            text.match(/@(-?\d+\.\d+),(-?\d+\.\d+)/);
    if (m) { out.lat = parseFloat(m[1]); out.lon = parseFloat(m[2]); }
    var n = text.match(/\/place\/([^/@?]+)/) || text.match(/[?&](?:q|query)=([^&]+)/i);
    if (n) {
      var parts = decodeURIComponent(n[1].replace(/\+/g, ' ')).split(',');
      var name = parts[0].trim();
      if (name && !/^-?\d+\.\d+$/.test(name)) {
        out.name = name;
        out.hint = words(parts.slice(1).join(' '));  // town or street, if the link has one
      }
    }
    return out.lat != null || out.name ? out : null;
  }

  var STOP = { the: 1, restaurant: 1, inc: 1, llc: 1, and: 1, of: 1, at: 1, bar: 1, grill: 1, cafe: 1 };
  function words(s) {
    return (s || '').normalize('NFD').replace(/[̀-ͯ]/g, '')  // Chão -> Chao
      .toLowerCase().replace(/&/g, ' and ').replace(/['\u2019]/g, '').replace(/[^a-z0-9 ]/g, ' ').split(/\s+/)
      .filter(function (w) { return w.length > 1 && !STOP[w]; });
  }
  function nameScore(a, b) {
    var wa = words(a), wb = words(b);
    if (!wa.length || !wb.length) return 0;
    var common = wa.filter(function (w) { return wb.indexOf(w) !== -1; }).length;
    return common / Math.min(wa.length, wb.length);
  }
  function metres(lat1, lon1, lat2, lon2) {
    var x = (lon2 - lon1) * Math.cos((lat1 + lat2) * Math.PI / 360), y = lat2 - lat1;
    return Math.sqrt(x * x + y * y) * 111320;
  }

  // Restaurants for a Google Maps link, best first. With a position: places
  // within 250 m, a matching name ranked first (a match within 150 m alone is
  // treated as the answer). With only a name: name matches statewide.
  function matchMapsLink(link) {
    var scored = [];
    all.forEach(function (f) {
      var s = link.name ? nameScore(link.name, f.properties.name) : 0;
      if (link.lat != null) {
        var c = f.geometry.coordinates, d = metres(link.lat, link.lon, c[1], c[0]);
        if (d <= 250) scored.push({ f: f, d: d, s: s });
      } else if (s >= 0.5) {
        // Without a position, an address that contains the link's town ranks first.
        var addr = words(f.properties.address);
        var near = (link.hint || []).some(function (w) { return addr.indexOf(w) !== -1; });
        scored.push({ f: f, d: null, s: s + (near ? 1 : 0) });
      }
    });
    scored.sort(function (a, b) { return (b.s - a.s) || ((a.d || 0) - (b.d || 0)); });
    var best = scored.filter(function (x) { return x.s >= 0.5 && (x.d == null || x.d <= 150); });
    if (best.length === 1 || (best.length > 1 && best[0].s > best[1].s)) scored = [best[0]];
    return scored.slice(0, 10).map(function (x) {
      return { type: 'Feature', geometry: x.f.geometry, properties: x.f.properties, dist: x.d };
    });
  }

  // Centre a point in the part of the map the side panel doesn't cover.
  function flyToVisible(lngLat, instant, zoom) {
    var panel = document.querySelector('.panel');
    var drawer = document.getElementById('drawer');
    var left = panel && panel.offsetWidth && window.innerWidth - panel.offsetWidth > 360 ? panel.offsetWidth + 12 : 0;
    var right = 0, bottom = 0;
    if (!drawer.hidden) {
      if (isPhone()) bottom = drawer.offsetHeight; else right = drawer.offsetWidth;
    }
    var opts = { center: lngLat, zoom: zoom || Math.max(map.getZoom(), 16),
                 offset: [(left - right) / 2, bottom ? -bottom / 2 + 40 : 40] };
    if (instant) map.easeTo(Object.assign(opts, { duration: 0 })); else map.flyTo(opts);
  }

  // --- phone menu -------------------------------------------------------------
  var menuButton = document.getElementById('menu-toggle');
  function isPhone() { return window.matchMedia('(max-width: 560px)').matches; }
  function setPanel(open) {
    document.body.classList.toggle('panel-open', open);
    menuButton.setAttribute('aria-expanded', String(open));
  }
  menuButton.addEventListener('click', function () { setPanel(true); });
  document.getElementById('panel-close').addEventListener('click', function () { setPanel(false); });
  // A dot on the menu button says some filter is on while the panel is closed.
  function updateMenuDot() {
    var filtering = document.getElementById('grades').classList.contains('filtering') ||
                    !!document.getElementById('month').value || !!category;
    document.querySelector('.menu-dot').hidden = !filtering;
  }

  // --- category filter --------------------------------------------------------
  var catCounts = null;  // {label: count}, built on first search
  function categoryCounts() {
    if (!catCounts) {
      catCounts = {};
      all.forEach(function (f) {
        (f.properties.cat || '').split(', ').forEach(function (c) {
          if (c) catCounts[c] = (catCounts[c] || 0) + 1;
        });
      });
    }
    return catCounts;
  }
  // Categories whose name has a word starting with the query: "kor" -> Korean.
  function matchCategories(q) {
    var counts = categoryCounts();
    return Object.keys(counts).filter(function (c) {
      return c.toLowerCase().split(/[\s-]+/).some(function (w) { return w.indexOf(q) === 0; }) ||
             c.toLowerCase().indexOf(q) === 0;
    }).sort(function (a, b) { return counts[b] - counts[a]; }).slice(0, 4);
  }
  function setCategory(c) {
    category = c;
    var chip = document.getElementById('cat-filter');
    chip.hidden = !c;
    if (c) {
      chip.querySelector('.cat-name').textContent = c;
      chip.querySelector('.cat-count').textContent = categoryCounts()[c].toLocaleString();
    }
    if (popup) popup.remove();
    updateMenuDot();
    refresh();
  }
  document.querySelector('#cat-filter button').addEventListener('click', function () { setCategory(''); });

  // --- shareable links -------------------------------------------------------
  // ?r=<facility id> opens the map on that restaurant's popup.
  function linkFor(f) {
    var c = f.geometry.coordinates;
    return location.origin + location.pathname + '?r=' + encodeURIComponent(f.properties.id) +
      '#16/' + c[1].toFixed(5) + '/' + c[0].toFixed(5);
  }
  function shareRestaurant(f, button) {
    var url = linkFor(f);
    var done = function (msg) {
      button.textContent = msg;
      setTimeout(function () { button.textContent = 'Share'; }, 2000);
    };
    if (navigator.share && isPhone()) {
      navigator.share({ title: f.properties.name, text: f.properties.name + ' – inspection score', url: url })
        .catch(function () {});
    } else if (navigator.clipboard) {
      navigator.clipboard.writeText(url).then(function () { done('Link copied'); },
                                              function () { window.prompt('Copy this link:', url); });
    } else {
      window.prompt('Copy this link:', url);
    }
  }
  function openFromLink() {
    var id = new URLSearchParams(location.search).get('r');
    if (!id) return;
    var f = all.filter(function (x) { return x.properties.id === id; })[0];
    if (!f) return;
    revealIfHidden(f);
    flyToVisible(f.geometry.coordinates, true);
    showPopup(f);
  }
  // A restaurant opened from search or a link is shown even if a filter hid it.
  function revealIfHidden(f) {
    if (isStale(f.properties) && !showStale) {
      showStale = true;
      document.getElementById('show-stale').checked = true;
      refresh();
    }
  }

  // --- near me -----------------------------------------------------------------
  // A list of the shown (filtered) places nearest to you, or to the map centre
  // when location isn't available, in the side drawer.
  var nearby = { center: null, label: '', sort: 'distance' };
  function miles(m) {
    var mi = m / 1609.34;
    return mi < 0.1 ? Math.round(m * 3.281 / 10) * 10 + ' ft' : (mi < 10 ? mi.toFixed(1) : Math.round(mi)) + ' mi';
  }
  function findNearMe() {
    if (isPhone()) setPanel(false);
    if (!navigator.geolocation) return askForLocation('unsupported');
    showNearby(null, '');  // shows "Finding your location…" while the browser asks
    navigator.geolocation.getCurrentPosition(function (pos) {
      var here = [pos.coords.longitude, pos.coords.latitude];
      map.easeTo({ center: here, zoom: Math.max(map.getZoom(), 13) });
      showNearby(here, 'you');
    }, function (err) {
      askForLocation(err && err.code === 1 ? 'denied' : 'unavailable');
    }, { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 });
  }
  // Shown instead of a list when we don't have your location: explain, and let
  // you try again or deliberately pick the map area.
  function askForLocation(reason) {
    var drawer = document.getElementById('drawer');
    var body = document.getElementById('drawer-body');
    body.textContent = '';
    drawer.dataset.id = 'nearby-ask';
    drawer.hidden = false;
    body.appendChild(el('h2', null, 'Places near you'));
    body.appendChild(el('p', 'near-ask', {
      denied: 'Location is blocked for this site. To see places near you, allow location for this site in your browser or phone settings, then try again.',
      unavailable: 'Your location couldn\'t be found right now. Check that location services are on, then try again.',
      unsupported: 'This browser can\'t share your location.'
    }[reason]));
    var tools = el('div', 'near-tools');
    if (reason !== 'unsupported') {
      var again = el('button', 'more primary', '📍 Share my location');
      again.type = 'button';
      again.addEventListener('click', findNearMe);
      tools.appendChild(again);
    }
    var area = el('button', 'more', 'Use the map area instead');
    area.type = 'button';
    area.addEventListener('click', function () {
      var c = map.getCenter();
      showNearby([c.lng, c.lat], 'the map centre');
    });
    tools.appendChild(area);
    body.appendChild(tools);
  }
  function showNearby(center, label) {
    if (center) { nearby.center = center; nearby.label = label; }
    var drawer = document.getElementById('drawer');
    var body = document.getElementById('drawer-body');
    body.textContent = '';
    drawer.dataset.id = 'nearby';
    drawer.hidden = false;
    body.appendChild(el('h2', null, 'Near ' + (nearby.center ? nearby.label : 'you')));
    if (!nearby.center) { body.appendChild(el('p', 'meta', 'Finding your location…')); return; }

    var tools = el('div', 'near-tools');
    var sort = el('select');
    sort.setAttribute('aria-label', 'Sort');
    [['distance', 'Closest first'], ['best', 'Best score first'], ['worst', 'Lowest score first']].forEach(function (o) {
      var opt = el('option', null, o[1]); opt.value = o[0]; sort.appendChild(opt);
    });
    sort.value = nearby.sort;
    sort.addEventListener('change', function () { nearby.sort = sort.value; showNearby(); });
    tools.appendChild(sort);
    var here = el('button', 'more', 'Use map area');
    here.type = 'button';
    here.title = 'List places around the centre of the map';
    here.addEventListener('click', function () { var c = map.getCenter(); showNearby([c.lng, c.lat], 'the map centre'); });
    tools.appendChild(here);
    body.appendChild(tools);

    var c = nearby.center;
    var rows = visible().map(function (f) {
      var g = f.geometry.coordinates;
      return { f: f, d: metres(c[1], c[0], g[1], g[0]) };
    }).filter(function (r) { return r.d <= 16093; });   // 10 miles
    var score = function (r) { var s = r.f.properties.score; return s === null || s === undefined || s === '' ? null : Number(s); };
    rows.sort(function (a, b) {
      if (nearby.sort === 'distance') return a.d - b.d;
      var sa = score(a), sb = score(b);
      if (sa === null || sb === null) return (sa === null) - (sb === null) || a.d - b.d;
      return nearby.sort === 'best' ? (sb - sa) || (a.d - b.d) : (sa - sb) || (a.d - b.d);
    });
    if (nearby.sort !== 'distance') {
      // "best/worst nearby" means within a short drive, not the whole 10 miles
      rows = rows.filter(function (r) { return r.d <= 4828; });   // 3 miles
    }
    var shown = rows.slice(0, 50);
    body.appendChild(el('p', 'meta', shown.length ? 'Showing ' + shown.length + ' of ' + rows.length +
      ' places within ' + (nearby.sort === 'distance' ? '10' : '3') + ' miles, with your filters applied.' :
      'No places within 10 miles match your filters.'));
    var ul = el('ul', 'near-list');
    shown.forEach(function (r) {
      var p = r.f.properties;
      var li = el('li');
      li.tabIndex = 0;
      li.appendChild(scoreBadge(p.score));
      var text = el('div', 'near-text');
      text.appendChild(el('div', 'near-name', p.name));
      text.appendChild(el('div', 'near-sub', miles(r.d) + (p.cat ? ' · ' + p.cat : '') + ' · ' + formatDate(p.date)));
      li.appendChild(text);
      var go = function () {
        // Step aside when there isn't room for the popup beside the list (always on phones).
        var drawer = document.getElementById('drawer');
        var panel = document.querySelector('.panel');
        var gap = window.innerWidth - drawer.offsetWidth - (panel.offsetWidth || 0) - 24;
        if (isPhone() || gap < 360) drawer.hidden = true;
        flyToVisible(r.f.geometry.coordinates);
        showPopup(r.f, { backToList: true });
      };
      li.addEventListener('click', go);
      li.addEventListener('keydown', function (e) { if (e.key === 'Enter') go(); });
      ul.appendChild(li);
    });
    body.appendChild(ul);
  }
  document.getElementById('nearby-btn').addEventListener('click', findNearMe);

  function setupSearch() {
    var input = document.getElementById('search');
    var list = document.getElementById('results');

    function pick(f) {
      list.hidden = true;
      revealIfHidden(f);
      if (isPhone()) setPanel(false);
      input.value = f.properties.name;
      flyToVisible(f.geometry.coordinates);
      showPopup(f);
    }

    input.addEventListener('input', function () {
      var raw = input.value.trim();
      var q = raw.toLowerCase();
      list.textContent = '';
      if (q.length < 2) { list.hidden = true; return; }
      var hits = [];
      var link = parseMapsLink(raw);
      if (link && link.shortLink) {
        list.appendChild(el('li', 'empty', 'Short links (maps.app.goo.gl) can\'t be read here. Open the link, then copy the full address from the browser\'s address bar.'));
        list.hidden = false;
        return;
      } else if (link) {
        hits = matchMapsLink(link);
        if (link.lat != null) flyToVisible([link.lon, link.lat]);
        if (!hits.length) list.appendChild(el('li', 'empty', 'No inspected restaurant found at that Google Maps place'));
      } else {
        var cats = matchCategories(q);
        cats.forEach(function (c) {
          var li = el('li', 'cat-hit', '🍽 ' + c);
          li.tabIndex = 0;
          li.appendChild(el('span', 'sub', 'Show all ' + categoryCounts()[c].toLocaleString() + ' with this category'));
          function choose() { list.hidden = true; input.value = ''; setCategory(c); if (isPhone()) setPanel(false); }
          li.addEventListener('click', choose);
          li.addEventListener('keydown', function (e) { if (e.key === 'Enter') choose(); });
          list.appendChild(li);
        });
        for (var i = 0; i < all.length && hits.length < 12; i++) {
          if (all[i].properties.name.toLowerCase().indexOf(q) !== -1) hits.push(all[i]);
        }
        if (!hits.length && !cats.length) list.appendChild(el('li', 'empty', 'No restaurants or categories match'));
      }
      if (link && hits.length === 1) { pick(hits[0]); return; }
      hits.forEach(function (f) {
        var li = el('li', null, f.properties.name);
        li.tabIndex = 0;
        li.appendChild(el('span', 'sub', f.properties.address + (f.dist != null ? ' · ' + Math.round(f.dist) + ' m away' : '')));
        li.addEventListener('click', function () { pick(f); });
        li.addEventListener('keydown', function (e) { if (e.key === 'Enter') pick(f); });
        list.appendChild(li);
      });
      list.hidden = false;
    });

    document.addEventListener('click', function (e) {
      if (!e.target.closest('.search')) list.hidden = true;
    });
    // Anything typed or pasted while the data was still loading.
    if (input.value.trim()) input.dispatchEvent(new Event('input'));
  }

  // Diagonal hatch for counties the state portal has no data for.
  function addHatchImage() {
    var size = 12;
    var canvas = document.createElement('canvas');
    canvas.width = canvas.height = size;
    var ctx = canvas.getContext('2d');
    ctx.fillStyle = 'rgba(120, 128, 138, 0.18)';
    ctx.fillRect(0, 0, size, size);
    ctx.strokeStyle = 'rgba(95, 103, 112, 0.55)';
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(0, size); ctx.lineTo(size, 0);
    ctx.moveTo(-size / 2, size / 2); ctx.lineTo(size / 2, -size / 2);
    ctx.moveTo(size / 2, size * 1.5); ctx.lineTo(size * 1.5, size / 2);
    ctx.stroke();
    map.addImage('no-data-hatch', ctx.getImageData(0, 0, size, size));
  }

  function addCountyLayers(counties) {
    addHatchImage();
    map.addSource('counties', { type: 'geojson', data: counties });
    var uncovered = ['==', ['get', 'covered'], false];
    map.addLayer({
      id: 'uncovered-fill',
      type: 'fill',
      source: 'counties',
      filter: uncovered,
      paint: { 'fill-pattern': 'no-data-hatch' }
    });
    map.addLayer({
      id: 'uncovered-outline',
      type: 'line',
      source: 'counties',
      filter: uncovered,
      paint: { 'line-color': '#5f6770', 'line-width': 1, 'line-opacity': 0.6 }
    });
    map.addLayer({
      id: 'uncovered-label',
      type: 'symbol',
      source: 'counties',
      filter: uncovered,
      minzoom: 7.5,
      layout: {
        'text-field': ['concat', ['get', 'name'], ' County\nno data'],
        'text-font': ['Noto Sans Regular'],
        'text-size': 11
      },
      paint: { 'text-color': '#4a5058', 'text-halo-color': '#ffffff', 'text-halo-width': 1.5 }
    });

    map.on('click', 'uncovered-fill', function (e) {
      // A restaurant pin on top of the county wins.
      if (map.queryRenderedFeatures(e.point, { layers: ['points', 'clusters'] }).length) return;
      showCountyPopup(e.features[0], e.lngLat);
    });
    map.on('mouseenter', 'uncovered-fill', function () { map.getCanvas().style.cursor = 'help'; });
    map.on('mouseleave', 'uncovered-fill', function () { map.getCanvas().style.cursor = ''; });
  }

  function showCountyPopup(feature, lngLat) {
    var p = feature.properties;
    var box = el('div', 'popup');
    box.appendChild(el('h2', null, p.name + ' County'));
    box.appendChild(el('div', 'addr', p.imported ? 'Partly included on this map' : 'Not included on this map'));
    var why = p.source
      ? p.name + ' County publishes its inspections through ' + p.source + ', not the state portal this map uses.'
      : p.facilities
        ? 'The state inspection portal this map uses lists ' + p.facilities + ' permits here but has inspection scores for only ' + p.inspected + '.'
        : 'The state inspection portal this map uses has no results for this county.';
    box.appendChild(el('p', 'county-note', why));
    if (p.imported) {
      box.appendChild(el('p', 'county-note', 'The ' + p.imported + ' pins here were copied by hand from GNR\'s site and are not a complete list.'));
    }
    if (p.source_url) {
      var a = el('a', null, 'Look up ' + p.name + ' County inspections');
      a.href = p.source_url;
      a.target = '_blank';
      a.rel = 'noopener';
      box.appendChild(a);
    }
    if (popup) popup.remove();
    popup = new maplibregl.Popup({ maxWidth: '280px' }).setLngLat(lngLat).setDOMContent(box).addTo(map);
  }

  function addLayers() {
    map.addSource('facilities', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: visible() },
      cluster: true,
      clusterMaxZoom: 12,
      clusterRadius: 45
    });

    map.addLayer({
      id: 'clusters',
      type: 'circle',
      source: 'facilities',
      filter: ['has', 'point_count'],
      paint: {
        'circle-color': '#3b5b7a',
        'circle-opacity': 0.85,
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 2,
        'circle-radius': ['step', ['get', 'point_count'], 14, 25, 18, 100, 23, 500, 29]
      }
    });
    map.addLayer({
      id: 'cluster-count',
      type: 'symbol',
      source: 'facilities',
      filter: ['has', 'point_count'],
      layout: {
        'text-field': ['get', 'point_count_abbreviated'],
        'text-font': ['Noto Sans Bold'],
        'text-size': 12
      },
      paint: { 'text-color': '#ffffff' }
    });
    map.addLayer({
      id: 'points',
      type: 'circle',
      source: 'facilities',
      filter: ['!', ['has', 'point_count']],
      paint: {
        'circle-color': colorExpr,
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 5, 14, 9, 17, 13],
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 1.5
      }
    });
    map.addLayer({
      id: 'point-scores',
      type: 'symbol',
      source: 'facilities',
      minzoom: 14,
      filter: ['all', ['!', ['has', 'point_count']], ['has', 'score']],
      layout: {
        'text-field': ['to-string', ['get', 'score']],
        'text-font': ['Noto Sans Bold'],
        'text-size': 10,
        'text-allow-overlap': true
      },
      paint: { 'text-color': '#ffffff' }
    });

    map.on('click', 'clusters', function (e) {
      var f = e.features[0];
      map.getSource('facilities').getClusterExpansionZoom(f.properties.cluster_id).then(function (zoom) {
        map.easeTo({ center: f.geometry.coordinates, zoom: zoom });
      });
    });
    map.on('click', 'points', function (e) { showPopup(e.features[0]); });
    ['clusters', 'points'].forEach(function (id) {
      map.on('mouseenter', id, function () { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', id, function () { map.getCanvas().style.cursor = ''; });
    });
  }

  function fitToData() {
    if (!all.length || location.hash.length > 1) return;   // a shared link already set the view
    var b = new maplibregl.LngLatBounds();
    all.forEach(function (f) { b.extend(f.geometry.coordinates); });
    map.fitBounds(b, { padding: { top: 60, bottom: 40, left: 40, right: 40 }, maxZoom: 13, duration: 0 });
  }

  var metaEl = document.getElementById('meta');
  var mapReady = new Promise(function (resolve) { map.on('load', resolve); });
  var dataReady = fetch('data/facilities.geojson').then(function (r) {
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  });

  // Optional: older data exports have no county file, and the map works without it.
  var countiesReady = fetch('data/counties.geojson')
    .then(function (r) { return r.ok ? r.json() : null; })
    .catch(function () { return null; });

  Promise.all([dataReady, mapReady, countiesReady]).then(function (res) {
    all = res[0].features;
    buildGradeButtons();
    buildMonthSelect();
    setupStaleToggle();
    setupSearch();
    if (res[2]) addCountyLayers(res[2]);   // below the pins, so it is added first
    addLayers();
    fitToData();
    openFromLink();
    return fetch('data/meta.json').then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
  }).then(function (meta) {
    var text = all.length.toLocaleString() + ' food service establishments';
    if (meta && meta.newest_inspection) text += ' · newest inspection ' + formatDate(meta.newest_inspection);
    metaEl.textContent = text + '. Source: Georgia Department of Public Health' +
      (meta && meta.gnr_mapped ? ', plus ' + meta.gnr_mapped.toLocaleString() + ' hand-copied from GNR Public Health.' : '.');
    var missing = meta && meta.counties_uncovered;
    if (missing && missing.length) {
      var note = el('p', 'meta coverage');
      note.appendChild(el('span', 'swatch'));
      note.appendChild(document.createTextNode(missing.length <= 4
        ? 'No data for ' + missing.join(', ') + (missing.length === 1 ? ' County.' : ' counties.')
        : 'No data for ' + missing.length + ' of 159 counties (shaded).'));
      metaEl.parentNode.insertBefore(note, metaEl.nextSibling);
    }
  }).catch(function (err) {
    metaEl.textContent = 'Could not load inspection data (' + err.message + ').';
  });
})();
