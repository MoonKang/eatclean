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
  var month = '';          // 'yyyy-mm' or '' for any
  var historyCache = {};   // bucket number -> promise of {facilityId: [inspections]}
  var active = { A: true, B: true, C: true, U: true, '': true };
  var popup = null;

  var map = new maplibregl.Map({
    container: 'map',
    style: 'https://tiles.openfreemap.org/styles/positron',
    center: [-83.6, 32.9],
    zoom: 6.3
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');
  map.addControl(new maplibregl.GeolocateControl({ trackUserLocation: false }), 'bottom-right');

  var colorExpr = ['match', ['get', 'grade'],
    'A', COLOR.A, 'B', COLOR.B, 'C', COLOR.C, 'U', COLOR.U, COLOR['']];

  function visible() {
    return all.filter(function (f) {
      var p = f.properties;
      if (!active[p.grade]) return false;
      return !month || (p.date || '').slice(0, 7) === month;
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
      var opt = el('option', null, label + ' (' + counts[m].toLocaleString() + ')');
      opt.value = m;
      select.appendChild(opt);
    });
    select.addEventListener('change', function () {
      month = select.value;
      if (popup) popup.remove();
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
    details.appendChild(el('summary', null,
      v.length + (v.length === 1 ? ' violation' : ' violations') + (points ? ' · ' + points + ' points' : '')));
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

  function showPopup(feature) {
    var p = feature.properties;
    var box = el('div', 'popup');
    box.appendChild(el('h2', null, p.name));
    box.appendChild(el('div', 'addr', p.address));

    var row = el('div', 'score');
    row.appendChild(scoreBadge(p.score));
    row.appendChild(el('span', null, p.grade ? 'Grade ' + p.grade : 'No score'));
    row.appendChild(el('span', 'when', '· ' + formatDate(p.date)));
    box.appendChild(row);

    var more = el('button', 'more', 'More history');
    more.type = 'button';
    more.addEventListener('click', function () { openHistory(feature); });
    box.appendChild(more);

    if (popup) popup.remove();
    popup = new maplibregl.Popup({ offset: 12, maxWidth: '300px' })
      .setLngLat(feature.geometry.coordinates)
      .setDOMContent(box)
      .addTo(map);
  }

  function buildGradeButtons() {
    var counts = {};
    all.forEach(function (f) { counts[f.properties.grade] = (counts[f.properties.grade] || 0) + 1; });
    var wrap = document.getElementById('grades');
    wrap.textContent = '';
    GRADES.forEach(function (g) {
      if (!counts[g.key]) return;
      var b = el('button');
      b.type = 'button';
      b.setAttribute('aria-pressed', 'true');
      b.title = g.range ? 'Score ' + g.range : 'No score on record';
      var dot = el('span', 'dot');
      dot.style.background = g.color;
      b.appendChild(dot);
      b.appendChild(el('span', null, g.label));
      b.appendChild(el('span', 'count', counts[g.key].toLocaleString()));
      b.addEventListener('click', function () {
        active[g.key] = !active[g.key];
        b.setAttribute('aria-pressed', String(active[g.key]));
        refresh();
      });
      wrap.appendChild(b);
    });
  }

  function setupSearch() {
    var input = document.getElementById('search');
    var list = document.getElementById('results');

    function pick(f) {
      list.hidden = true;
      input.value = f.properties.name;
      map.flyTo({ center: f.geometry.coordinates, zoom: 16 });
      showPopup(f);
    }

    input.addEventListener('input', function () {
      var q = input.value.trim().toLowerCase();
      list.textContent = '';
      if (q.length < 2) { list.hidden = true; return; }
      var hits = [];
      for (var i = 0; i < all.length && hits.length < 12; i++) {
        if (all[i].properties.name.toLowerCase().indexOf(q) !== -1) hits.push(all[i]);
      }
      if (!hits.length) {
        list.appendChild(el('li', 'empty', 'No restaurants match'));
      }
      hits.forEach(function (f) {
        var li = el('li', null, f.properties.name);
        li.tabIndex = 0;
        li.appendChild(el('span', 'sub', f.properties.address));
        li.addEventListener('click', function () { pick(f); });
        li.addEventListener('keydown', function (e) { if (e.key === 'Enter') pick(f); });
        list.appendChild(li);
      });
      list.hidden = false;
    });

    document.addEventListener('click', function (e) {
      if (!e.target.closest('.search')) list.hidden = true;
    });
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
    if (!all.length) return;
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

  Promise.all([dataReady, mapReady]).then(function (res) {
    all = res[0].features;
    buildGradeButtons();
    buildMonthSelect();
    setupSearch();
    addLayers();
    fitToData();
    return fetch('data/meta.json').then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
  }).then(function (meta) {
    var text = all.length.toLocaleString() + ' food service establishments';
    if (meta && meta.newest_inspection) text += ' · newest inspection ' + formatDate(meta.newest_inspection);
    metaEl.textContent = text + '. Source: Georgia Department of Public Health.';
  }).catch(function (err) {
    metaEl.textContent = 'Could not load inspection data (' + err.message + ').';
  });
})();
