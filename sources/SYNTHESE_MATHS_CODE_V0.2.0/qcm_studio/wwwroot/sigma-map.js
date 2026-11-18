(() => {
  'use strict';

  const city = document.getElementById('sigma-city');
  const map = city.closest('.map-area');
  const pauseButton = document.getElementById('sigma-pause');
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const svg = 'http://www.w3.org/2000/svg';
  const openingArrival = 6.7;
  const openingTime = 7;
  const dayDuration = 103;
  const playbackRate = 1.12;

  function route(description) {
    const path = document.createElementNS(svg, 'path');
    path.setAttribute('d', description);
    return { path, length: path.getTotalLength() };
  }

  function detail(parent, type, attributes) {
    const element = document.createElementNS(svg, type);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
    parent.append(element);
    return element;
  }

  function wallSize(face) {
    const [a, b, , d] = Array.from(face.points);
    return { width: Math.hypot(b.x - a.x, b.y - a.y), height: d.y - a.y, span: Math.abs(b.x - a.x) };
  }

  function plasterEntrances() {
    const entrances = new Set();
    for (const { element } of buildings) {
      let best = null;
      let bestSpan = 0;
      for (const plane of element.querySelectorAll('[data-sigma-wall="plaster"]')) {
        const { width, height, span } = wallSize(plane);
        if (height >= 12 && width >= 21 && span > bestSpan) {
          best = plane;
          bestSpan = span;
        }
      }
      if (best) entrances.add(best);
    }
    return entrances;
  }

  function dressPlaster(group, width, height, entrance, addStep) {
    if (height < 10 || width < 17) return;
    const bays = Math.max(1, Math.min(5, Math.floor((width - 6) / 13)));
    const spacing = (width - 6) / bays;
    const entranceBay = Math.floor((bays - 1) / 2);
    for (let bay = 0; bay < bays; bay++) {
      const center = 3 + (bay + .5) * spacing;
      const door = entrance && bay === entranceBay;
      const openingWidth = Math.min(door ? 9 : 11, spacing - 3);
      const openingHeight = Math.min(door ? 15 : 9, height * (door ? .69 : .47));
      const y = door ? height - openingHeight - .8 : (height - openingHeight) * .43;
      const x = center - openingWidth / 2;
      detail(group, 'rect', { x: x - .8, y: y - .7, width: openingWidth + 1.6,
        height: openingHeight + 1.5, fill: '#513d35', opacity: '.34' });
      detail(group, 'rect', { x, y, width: openingWidth, height: openingHeight, rx: '.6',
        fill: door ? '#695549' : 'url(#sigma-glass)', stroke: '#765845', 'stroke-width': 1 });
      detail(group, 'path', { d: `M${x - 1} ${y - .8}h${openingWidth + 2}m0 ${openingHeight + 1.4}h-${openingWidth + 2}`,
        fill: 'none', stroke: '#efd3af', 'stroke-width': '.9' });
      if (door) {
        detail(group, 'circle', { cx: x + openingWidth - 2, cy: y + openingHeight * .56, r: '.7', fill: '#eac993' });
        addStep(center, openingWidth + 2, 4.5);
      } else {
        detail(group, 'path', { d: `M${center} ${y + .5}v${openingHeight - 1}`, stroke: '#c7ab8b', 'stroke-width': '.7' });
      }
    }
  }

  function addRoofVent(surface, corners, origin, end, length) {
    const footprint = corners.map(({ x, y }) => [x, y]);
    const ux = (end.x - origin.x) / length;
    const uy = (end.y - origin.y) / length;
    const local = corners.map(point => {
      const x = point.x - origin.x;
      const y = point.y - origin.y;
      return [x * ux + y * uy, -x * uy + y * ux];
    });
    const centerX = local.reduce((sum, [x]) => sum + x, 0) / local.length;
    const centerY = local.reduce((sum, [, y]) => sum + y, 0) / local.length;
    const candidates = [[centerX + 12, centerY + 6], [centerX - 12, centerY + 6], [centerX, centerY]];
    const site = candidates.find(([x, y]) => [[-9, -7], [11, -7], [11, 9], [-9, 9]].every(([dx, dy]) =>
      inside([origin.x + (x + dx) * ux - (y + dy) * uy, origin.y + (x + dx) * uy + (y + dy) * ux], footprint)));
    if (!site) return;
    const [x, y] = site;
    const vent = detail(surface, 'g', { transform: `translate(${x} ${y})`, class: 'sigma-roof-vent' });
    detail(vent, 'path', { d: 'M-6-1 7-1 10 7-3 7Z', fill: '#302d30', opacity: '.55' });
    detail(vent, 'path', { d: 'M-7-2 6-2 6 5-7 5Z', fill: '#627074', stroke: '#383f44', 'stroke-width': '.7' });
    detail(vent, 'path', { d: 'M-7-2-5-5H8L6-2Z', fill: '#b5aaa0', stroke: '#4e5153', 'stroke-width': '.7' });
    detail(vent, 'path', { d: 'M6-2 8-5V2L6 5Z', fill: '#494b51' });
    detail(vent, 'path', { d: 'M-5 0H4M-5 2H4', stroke: '#35444b', 'stroke-width': '.7' });
  }

  function addRoofEdges(group, corners, compact) {
    const area = corners.reduce((sum, a, index) => {
      const b = corners[(index + 1) % corners.length];
      return sum + a.x * b.y - b.x * a.y;
    }, 0);
    const trim = compact ? 1.3 : 2.6;
    for (const [index, a] of corners.entries()) {
      const b = corners[(index + 1) % corners.length];
      if ((b.x - a.x) * area >= 0 || Math.hypot(b.x - a.x, b.y - a.y) < 4) continue;
      detail(group, 'path', { d: `M${a.x} ${a.y}L${b.x} ${b.y}v${trim}L${a.x} ${a.y + trim}Z`,
        fill: '#4c4040', opacity: '.84' });
      detail(group, 'path', { d: `M${a.x} ${a.y + .55}L${b.x} ${b.y + .55}`, stroke: '#dec6a4',
        'stroke-width': '.7', opacity: '.86' });
      if (!compact && Math.hypot(b.x - a.x, b.y - a.y) > 32) {
        detail(group, 'path', { d: `M${a.x} ${a.y + 2.15}L${b.x} ${b.y + 2.15}`,
          stroke: '#767b78', 'stroke-width': '1', 'stroke-linecap': 'round' });
      }
    }
  }

  function addFrontage(face, width, center, span, depth) {
    const [a, b, , base] = Array.from(face.points);
    const tangent = { x: (b.x - a.x) / width, y: (b.y - a.y) / width };
    const sign = tangent.x < 0 ? -1 : 1;
    const normal = { x: -tangent.y * sign, y: Math.abs(tangent.x) * .78 };
    const point = (along, out) => [base.x + tangent.x * along + normal.x * out, base.y + tangent.y * along + normal.y * out];
    const edge = Math.min(span / 2, center - 1, width - center - 1);
    if (edge < 2.5) return;
    const outline = [point(center - edge, .35), point(center + edge, .35),
      point(center + edge + .7, depth), point(center - edge - .7, depth)];
    if (buildings.some(building => building.element !== face.parentNode && overlap(outline, building.footprint))) return;
    const ground = document.getElementById('sigma-frontages');
    detail(ground, 'polygon', { points: outline.map(p => p.join(',')).join(' '),
      fill: depth >= 5 ? 'url(#sigma-concrete-pavers)' : '#c7b89f',
      stroke: '#91816d', 'stroke-width': '.65', class: 'sigma-frontage-step' });
    if (depth >= 5) {
      detail(ground, 'polyline', { points: [point(center - edge, depth * .48), point(center + edge, depth * .48)].map(p => p.join(',')).join(' '),
        fill: 'none', stroke: '#e7d5b7', 'stroke-width': '.7' });
    }
  }

  function dressBuildings() {
    const entrances = plasterEntrances();
    const profiles = new Map(buildings.map(building => [building.element, building]));
    const colors = {
      brick: [['#b57251', '#8f4c38'], ['#b46a51', '#8a483a'], ['#b77a56', '#96563c']],
      plaster: [['#c5a78b', '#9b806b'], ['#cbb699', '#a18a73'], ['#b7aa95', '#8f8173']],
      metal: [['#829498', '#66777d'], ['#98a29e', '#707f80'], ['#879099', '#636d7a']]
    };
    for (const [index, face] of Array.from(city.querySelectorAll('[data-sigma-wall]')).entries()) {
      const [a, b] = Array.from(face.points);
      const { width, height } = wallSize(face);
      if (width < 1 || height < 4) continue;
      const facade = document.createElementNS(svg, 'g');
      facade.setAttribute('aria-hidden', 'true');
      facade.setAttribute('transform', `matrix(${(b.x - a.x) / width} ${(b.y - a.y) / width} 0 1 ${a.x} ${a.y})`);
      face.after(facade);
      const clipId = `sigma-wall-clip-${index}`;
      detail(detail(facade, 'clipPath', { id: clipId }), 'rect', { width, height });
      const group = detail(facade, 'g', { 'clip-path': `url(#${clipId})` });

      const material = face.dataset.sigmaWall;
      const brick = material === 'brick';
      const plaster = material === 'plaster';
      const loading = face.hasAttribute('data-sigma-loading');
      const signed = face.hasAttribute('data-sigma-signed');
      const shade = Math.abs(b.y - a.y) / width > .55;
      const profile = profiles.get(face.parentNode);
      const variant = Math.abs(profile.minX * 5 + profile.maxY * 3) % 3;
      face.setAttribute('fill', colors[material][variant][shade ? 1 : 0]);
      const small = profile.compact;
      if (!plaster) detail(group, 'rect', { width, height, fill: `url(#sigma-${brick ? 'brickwork' : 'cladding'})`, opacity: small ? '.55' : '1' });
      detail(group, 'rect', { y: height - 3, width, height: 3, fill: '#5b473b', opacity: '.26' });
      if (brick && width > 28) {
        detail(group, 'path', { d: `M0 4H${width}`, stroke: '#f3b681', 'stroke-width': '1.2', opacity: '.56' });
      }
      if (plaster && width > 20) {
        detail(group, 'rect', { x: 1, y: 3, width: width - 2, height: height - 6, fill: 'url(#sigma-plaster)' });
      }
      detail(group, 'path', { d: `M0 2H${width}`, stroke: '#533b35', 'stroke-width': '2', opacity: '.42' });
      if (!small && width > 27) {
        detail(group, 'path', { d: `M3 2l.9 6m2-6 1 3M${width - 4} 2l-.8 5`,
          stroke: '#645041', 'stroke-width': '.65', opacity: '.34' });
      }
      detail(group, 'path', { d: `M0 ${height - 2}H${width}`, stroke: '#e7be91', 'stroke-width': '1.2', opacity: '.62' });
      if (small) {
        if (entrances.has(face)) {
          const doorHeight = Math.min(10, height - 4);
          detail(group, 'rect', { x: width / 2 - 3, y: height - doorHeight, width: 6, height: doorHeight,
            fill: '#695549', stroke: '#c7ab8b', 'stroke-width': '.65' });
          addFrontage(face, width, width / 2, 7, 2.5);
        }
        continue;
      }
      if (plaster) {
        dressPlaster(group, width, height, entrances.has(face),
          (center, span, depth) => addFrontage(face, width, center, span, depth));
        continue;
      }
      if (signed || face.hasAttribute('data-sigma-solid')) continue;

      const count = Math.floor(width / 23);
      const spacing = width / (count + 1);
      if (brick && count >= 3) {
        for (let index = 1; index < count; index++) {
          const x = spacing * (index + .5);
          detail(group, 'rect', { x: x - 1.5, y: 3.5, width: 3, height: height - 6.5, fill: '#6b453a', opacity: '.24' });
          detail(group, 'path', { d: `M${x - 1.5} 4V${height - 3}`, stroke: '#e1a176', 'stroke-width': '.7', opacity: '.7' });
        }
      }
      for (let index = 1; index <= count; index++) {
        const x = spacing * index;
        if (loading && index === Math.ceil(count / 2)) {
          const y = Math.max(5, height * .28);
          detail(group, 'rect', { x: x - 9, y, width: 18, height: height - y, fill: 'url(#sigma-shutter)', stroke: '#4a3c36', 'stroke-width': 1 });
          detail(group, 'path', { d: `M${x - 5} ${y - 2}h10`, stroke: '#ffe0a1', 'stroke-width': 2 });
          detail(group, 'path', { d: `M${x - 10.5} ${height - 1}v-5m21 5v-5`, stroke: '#cf9a52', 'stroke-width': '1.2' });
          addFrontage(face, width, x, 22, 8);
          continue;
        }
        const y = height * .29;
        const h = Math.min(brick ? 14 : 10, height * .5);
        const w = brick ? 10 : 14;
        const left = x - w / 2;
        const right = x + w / 2;
        const opening = brick && h >= 10
          ? `M${left} ${y + 3}Q${x} ${y - 3} ${right} ${y + 3}V${y + h}H${left}Z`
          : `M${left} ${y}h${w}v${h}h-${w}Z`;
        detail(group, 'path', { d: opening, fill: 'url(#sigma-glass)', stroke: brick ? '#6e4435' : '#384951', 'stroke-width': 1.2 });
        detail(group, 'path', { d: `M${left + .6} ${y + (brick && h >= 10 ? 3.3 : .7)}V${y + h - .4}`,
          fill: 'none', stroke: '#262f34', 'stroke-width': '1.1', opacity: '.66' });
        detail(group, 'path', { d: `M${left - 1.5} ${y + h + .6}H${right + 1.5}`,
          stroke: '#d5af86', 'stroke-width': '1.4' });
        detail(group, 'path', { d: `M${left - 1} ${y + h}H${right + 1}M${x} ${y + 2}V${y + h}M${left + 1} ${y + h - 2}l3-3`,
          fill: 'none', stroke: '#e2c9a7', 'stroke-width': '.7', opacity: '.76' });
      }
    }

    for (const [index, roof] of Array.from(city.querySelectorAll('[data-sigma-roof]')).entries()) {
      const group = document.createElementNS(svg, 'g');
      group.setAttribute('aria-hidden', 'true');
      roof.after(group);
      const corners = Array.from(roof.points);
      const points = roof.getAttribute('points');
      if (profiles.get(roof.parentNode).compact) {
        detail(group, 'polygon', { points, fill: 'none', stroke: '#d1b291', 'stroke-width': '.6', 'stroke-linejoin': 'round' });
        addRoofEdges(group, corners, true);
        continue;
      }
      const clipId = `sigma-roof-clip-${index}`;
      detail(detail(group, 'clipPath', { id: clipId }), 'polygon', { points });
      const panels = detail(group, 'g', { 'clip-path': `url(#${clipId})` });
      const [origin, end, length] = corners.reduce((longest, start, i) => {
        const next = corners[(i + 1) % corners.length];
        const edge = Math.hypot(next.x - start.x, next.y - start.y);
        return edge > longest[2] ? [start, next, edge] : longest;
      }, [null, null, 0]);
      const radius = Math.ceil(Math.max(...corners.map(point => Math.hypot(point.x - origin.x, point.y - origin.y))));
      const angle = Math.atan2(end.y - origin.y, end.x - origin.x) * 180 / Math.PI;
      const tile = roof.getAttribute('fill') === '#885e50';
      if (tile && corners.length === 4) {
        const [a, b, c, d] = corners;
        const ridgeStart = `${(a.x + d.x) / 2},${(a.y + d.y) / 2}`;
        const ridgeEnd = `${(b.x + c.x) / 2},${(b.y + c.y) / 2}`;
        detail(panels, 'polygon', { points: `${a.x},${a.y} ${b.x},${b.y} ${ridgeEnd} ${ridgeStart}`, fill: '#392a39', opacity: '.36' });
        detail(panels, 'polyline', { points: `${ridgeStart} ${ridgeEnd}`, stroke: '#ebac7d', 'stroke-width': '1.5', opacity: '.7' });
      }
      const surface = detail(panels, 'g', { transform: `translate(${origin.x} ${origin.y}) rotate(${angle})` });
      detail(surface, 'rect', { x: -radius, y: -radius, width: radius * 2, height: radius * 2,
        fill: `url(#sigma-roof-${tile ? 'tiles' : radius < 45 ? 'seams' : 'panels'})` });
      const industrial = roof.parentNode.querySelector('[data-sigma-wall="brick"], [data-sigma-wall="metal"]');
      if (industrial && !tile && length >= 60 && !roof.hasAttribute('data-sigma-skylights')) {
        addRoofVent(surface, corners, origin, end, length);
      }
      detail(panels, 'polygon', { points, fill: 'none', stroke: '#272a30', 'stroke-width': '4', opacity: '.58', 'stroke-linejoin': 'round' });
      detail(group, 'polygon', { points, fill: 'none', stroke: '#d1b291', 'stroke-width': '.75', 'stroke-linejoin': 'round' });
      addRoofEdges(group, corners, false);
      if (length < 30 || corners.length !== 4 || !roof.hasAttribute('data-sigma-skylights')) continue;
      const [a, b, , d] = corners;
      const project = (u, v) => `${a.x + (b.x - a.x) * u + (d.x - a.x) * v},${a.y + (b.y - a.y) * u + (d.y - a.y) * v}`;
      const longSide = Math.hypot(b.x - a.x, b.y - a.y) >= 65;
      const glazing = longSide ? [[.22, .18], [.56, .18]] : [[.28, .4]];
      for (const [start, width] of glazing) {
        const end = start + width;
        detail(panels, 'polygon', { points: [project(start, .36), project(end, .36), project(end, .58), project(start, .58)].join(' '),
          fill: 'url(#sigma-glass)', stroke: '#bc956e', 'stroke-width': '1.6' });
        detail(panels, 'polyline', { points: [project(start + width / 2, .36), project(start + width / 2, .58)].join(' '),
          stroke: '#d4ae86', 'stroke-width': '.8' });
      }
    }
  }

  function person(coat, label) {
    const figure = document.createElementNS(svg, 'g');
    figure.setAttribute('class', 'sigma-person');
    figure.setAttribute('data-sigma-track', '');
    figure.setAttribute('tabindex', '0');
    figure.setAttribute('role', 'button');
    figure.setAttribute('aria-label', label);
    figure.setAttribute('aria-pressed', 'false');
    figure.style.setProperty('--coat', coat);
    figure.innerHTML =
      '<rect class="sigma-actor-target" x="-16" y="-33" width="34" height="43" rx="11"/>' +
      '<g class="sigma-person-art" transform="scale(.77)">' +
      '<ellipse class="sigma-person-shadow" cx="4" cy="1" rx="10" ry="4"/>' +
      '<g class="sigma-person-body"><path class="sigma-legs" d="M-3-8l-2 8m8-8 2 8"/>' +
      '<path class="sigma-coat" d="M-5-8v-8a5 5 0 0 1 10 0v8Z"/>' +
      '<path class="sigma-arm" d="m3-16 6 5"/><circle class="sigma-head" cy="-23" r="4.5"/>' +
      '<rect class="sigma-case" x="6" y="-10" width="9" height="7" rx="2"/></g></g>';
    document.getElementById('sigma-people').append(figure);
    return figure;
  }

  function pointAt(track, progress) {
    return track.path.getPointAtLength(track.length * Math.max(0, Math.min(1, progress)));
  }

  const buildings = Array.from(city.querySelectorAll('[data-sigma-footprint]'), element => {
    const footprint = element.dataset.sigmaFootprint.split(' ').map(pair => pair.split(',').map(Number));
    let perimeter = 0;
    const area = Math.abs(footprint.reduce((sum, [x, y], index) => {
      const [u, v] = footprint[(index + 1) % footprint.length];
      perimeter += Math.hypot(u - x, v - y);
      return sum + x * v - u * y;
    }, 0)) / 2;
    const surfaces = Array.from(element.querySelectorAll('[data-sigma-wall], [data-sigma-roof]'),
      polygon => Array.from(polygon.points, point => [point.x, point.y]));
    const cover = surfaces.map(polygon => {
      const area = polygon.reduce((sum, [x, y], index) => {
        const [u, v] = polygon[(index + 1) % polygon.length];
        return sum + x * v - u * y;
      }, 0);
      const ordered = area < 0 ? polygon.slice().reverse() : polygon;
      return 'M' + ordered.map(point => point.join(' ')).join('L') + 'Z';
    }).join('');
    return {
      element, footprint, surfaces, cover, compact: area < 420 || 2 * area / perimeter < 10,
      minX: Math.min(...footprint.map(([x]) => x)),
      maxX: Math.max(...footprint.map(([x]) => x)),
      minY: Math.min(...footprint.map(([, y]) => y)),
      maxY: Math.max(...footprint.map(([, y]) => y)),
      top: Math.min(...surfaces.flat().map(([, y]) => y))
    };
  });
  const vanSize = { halfLength: 13, halfWidth: 8 };
  const carSize = { halfLength: 13, halfWidth: 7 };

  function vehiclePose(track, progress, reverse = false) {
    const position = Math.max(0, Math.min(1, progress));
    const point = pointAt(track, position);
    const before = pointAt(track, position - .004);
    const after = pointAt(track, position + .004);
    return { x: point.x, y: point.y, angle: Math.atan2(after.y - before.y, after.x - before.x) + (reverse ? Math.PI : 0) };
  }

  function vehicleOutline({ x, y, angle }, { halfLength, halfWidth }) {
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    return [[-halfLength, -halfWidth], [halfLength, -halfWidth], [halfLength, halfWidth], [-halfLength, halfWidth]]
      .map(([u, v]) => [x + u * cos - v * sin, y + u * sin + v * cos]);
  }

  function inside([x, y], polygon) {
    return polygon.reduce((result, [a, b], index) => {
      const [c, d] = polygon[(index + 1) % polygon.length];
      return (b > y) !== (d > y) && x < a + (c - a) * (y - b) / (d - b) ? !result : result;
    }, false);
  }

  function edgesCross(a, b, c, d) {
    const side = ([x, y], [u, v], [i, j]) => (u - x) * (j - y) - (v - y) * (i - x);
    return Math.max(a[0], b[0]) >= Math.min(c[0], d[0]) && Math.max(c[0], d[0]) >= Math.min(a[0], b[0]) &&
      Math.max(a[1], b[1]) >= Math.min(c[1], d[1]) && Math.max(c[1], d[1]) >= Math.min(a[1], b[1]) &&
      side(a, b, c) * side(a, b, d) <= 0 && side(c, d, a) * side(c, d, b) <= 0;
  }

  function overlap(first, second) {
    return first.some(point => inside(point, second)) || second.some(point => inside(point, first)) ||
      first.some((a, i) => second.some((c, j) => edgesCross(a, first[(i + 1) % first.length], c, second[(j + 1) % second.length])));
  }

  function clearsBuildings(pose, size) {
    const radius = Math.hypot(size.halfLength, size.halfWidth);
    const nearby = buildings.filter(b => pose.x + radius >= b.minX && pose.x - radius <= b.maxX &&
      pose.y + radius >= b.minY && pose.y - radius <= b.maxY);
    if (!nearby.length) return true;
    const outline = vehicleOutline(pose, size);
    return nearby.every(building => !overlap(outline, building.footprint));
  }

  function routeSamples(track, reverse) {
    const count = Math.max(1, Math.ceil(track.length / 1.5));
    return Array.from({ length: count + 1 }, (_, index) => vehiclePose(track, index / count, reverse));
  }

  function reservedVehicleSpace() {
    const routes = [[firstTrip, vanSize], [secondTrip, vanSize],
      ...cars.filter(car => car.clear).map(car => [car.legs, carSize])];
    return routes.flatMap(([legs, size]) => legs.flatMap(({ track, reverse }) =>
      routeSamples(track, reverse).map(pose => ({
        x: pose.x, y: pose.y,
        outline: vehicleOutline(pose, { halfLength: size.halfLength + 1, halfWidth: size.halfWidth + 1 })
      }))));
  }

  const treeOccluders = [];

  function treeOccluder({ x, y }, scale, leaves) {
    const canopy = leaves.map(([cx, cy, r]) => [x + cx * scale, y + cy * scale, r * scale]);
    const cover = canopy.map(([cx, cy, r]) =>
      `M${cx - r} ${cy}a${r} ${r} 0 1 1 ${2 * r} 0a${r} ${r} 0 1 1 ${-2 * r} 0Z`).join('');
    const trunk = `M${x - 4.5 * scale} ${y - 24 * scale}H${x + .5 * scale}V${y + 4 * scale}H${x - 4.5 * scale}Z`;
    return {
      depth: y + 2,
      minX: Math.min(...canopy.map(([cx, , r]) => cx - r)),
      maxX: Math.max(...canopy.map(([cx, , r]) => cx + r)),
      top: Math.min(...canopy.map(([, cy, r]) => cy - r)),
      cover: cover + trunk,
      hidesPoint: (a, b) => canopy.some(([cx, cy, r]) => Math.hypot(a - cx, b - cy) < r)
    };
  }

  function layerScenery(parkingGround) {
    const trees = Array.from(city.querySelectorAll('use[href="#sigma-tree"]'));
    if (!trees.length) return;
    const layer = document.createElementNS(svg, 'g');
    layer.setAttribute('id', 'sigma-scenery');
    trees[0].before(layer);
    const objects = buildings.map(building => ({ element: building.element, depth: building.maxY }));
    const placed = [];
    const traffic = reservedVehicleSpace();
    const leaves = Array.from(city.querySelectorAll('#sigma-tree > circle'),
      leaf => ['cx', 'cy', 'r'].map(attribute => Number(leaf.getAttribute(attribute))));
    const offsets = [[0, 0], [0, 7], [-7, 0], [7, 0], [0, -7], [-9, 9], [9, 9],
      [-14, 0], [14, 0], [0, 14], [0, -14], [-14, 14], [14, 14]];
    const trunkSize = { halfLength: 3, halfWidth: 3 };
    for (const tree of trees) {
      const translation = tree.transform.baseVal.getItem(0);
      const { e: x, f: y } = translation.matrix;
      const spot = offsets.map(([dx, dy]) => ({ x: x + dx, y: y + dy, angle: 0 })).find(point => {
        if (!clearsBuildings(point, trunkSize) ||
            placed.some(other => Math.hypot(point.x - other.x, point.y - other.y) < 7)) return false;
        const trunk = vehicleOutline(point, trunkSize);
        if (parkingGround.some(surface => overlap(trunk, surface))) return false;
        return traffic.every(sample => Math.abs(sample.x - point.x) > 22 ||
          Math.abs(sample.y - point.y) > 22 || !overlap(trunk, sample.outline));
      });
      if (!spot) {
        tree.remove();
        continue;
      }
      translation.setTranslate(spot.x, spot.y);
      placed.push(spot);
      const occluder = treeOccluder(spot, tree.transform.baseVal.getItem(1).matrix.a, leaves);
      treeOccluders.push(occluder);
      objects.push({ element: tree, depth: occluder.depth });
    }
    objects.sort((a, b) => a.depth - b.depth);
    layer.append(...objects.map(object => object.element));
  }

  function clearTrip(legs, size) {
    return legs.every(({ track, reverse }) =>
      routeSamples(track, reverse).every(pose => clearsBuildings(pose, size)));
  }

  function drivingLeg(start, end, path, reverse = false) {
    return { start, end, track: route(path), reverse };
  }

  function drivePose(legs, seconds) {
    const leg = legs.findLast(item => seconds >= item.start) ?? legs[0];
    const time = Math.max(0, Math.min(1, (seconds - leg.start) / (leg.end - leg.start)));
    return vehiclePose(leg.track, time * time * (3 - 2 * time), leg.reverse);
  }

  function drive(element, legs, seconds) {
    const pose = drivePose(legs, seconds);
    element.setAttribute('transform', `translate(${pose.x} ${pose.y}) rotate(${pose.angle * 180 / Math.PI})`);
    maskBehindScenery(element, pose);
  }

  const sceneryMasks = new Map();
  let nextSceneryMask = 0;

  function maskBehindScenery(vehicle, { x, y }) {
    const radius = 16;
    const occluders = buildings.filter(b => x + radius >= b.minX && x - radius <= b.maxX &&
      y + radius >= b.top && y < b.maxY &&
      b.footprint.some(([a, z], index) => {
        const [c, d] = b.footprint[(index + 1) % b.footprint.length];
        const scan = Math.max(b.minX + .01, Math.min(b.maxX - .01, x));
        return (a > scan) !== (c > scan) && z + (d - z) * (scan - a) / (c - a) > y;
      }));
    const trees = treeOccluders.filter(tree => y < tree.depth && y + radius >= tree.top &&
      x + radius >= tree.minX && x - radius <= tree.maxX);
    const cover = [...occluders, ...trees].map(object => object.cover).join('');
    if (!cover && !sceneryMasks.has(vehicle)) return;
    if (!sceneryMasks.has(vehicle)) {
      const id = `sigma-vehicle-mask-${nextSceneryMask++}`;
      const layer = document.createElementNS(svg, 'g');
      vehicle.before(layer);
      layer.append(vehicle);
      layer.setAttribute('mask', `url(#${id})`);
      const mask = detail(city.querySelector('defs'), 'mask', {
        id, maskUnits: 'userSpaceOnUse', x: -100, y: -100, width: 1200, height: 1000
      });
      detail(mask, 'rect', { x: -100, y: -100, width: 1200, height: 1000, fill: 'white' });
      sceneryMasks.set(vehicle, detail(mask, 'path', { fill: 'black' }));
    }
    const path = sceneryMasks.get(vehicle);
    if (path.getAttribute('d') !== cover) path.setAttribute('d', cover);
    const hiddenBehindScenery = occluders.some(b => b.surfaces.some(surface => inside([x, y], surface))) ||
      trees.some(tree => tree.hidesPoint(x, y));
    vehicle.style.pointerEvents = hiddenBehindScenery ? 'none' : '';
  }

  function car(color, label) {
    const element = document.createElementNS(svg, 'g');
    element.setAttribute('data-sigma-track', '');
    element.setAttribute('tabindex', '0');
    element.setAttribute('role', 'button');
    element.setAttribute('aria-label', label);
    element.setAttribute('aria-pressed', 'false');
    element.style.color = color;
    element.innerHTML = '<rect class="sigma-actor-target" x="-16" y="-10" width="32" height="20" rx="5"/>' +
      '<use href="#sigma-car" transform="scale(.88 .74)"/>';
    document.getElementById('sigma-cars').append(element);
    return element;
  }

  function walker(element, track, seconds, start, end, reverse = false) {
    const visible = seconds >= start && seconds < end;
    element.style.display = visible ? '' : 'none';
    if (!visible) return;

    const progress = (seconds - start) / (end - start);
    const point = pointAt(track, reverse ? 1 - progress : progress);
    const stride = Math.sin(seconds * 15);
    element.setAttribute('opacity', String(Math.min(1, (seconds - start) * 3, (end - seconds) * 3)));
    element.setAttribute('transform', `translate(${point.x} ${point.y}) scale(.58)`);
    element.querySelector('.sigma-legs').setAttribute('d',
      `M-3-8l${-2 + stride * 3} 8m${8 - stride * 3}-8 ${2 - stride * 3} 8`);
    element.querySelector('.sigma-person-body').setAttribute('transform', `translate(0 ${-Math.abs(stride) * 1.4})`);
    return point;
  }

  const firstVan = document.getElementById('sigma-first-van');
  const secondVan = document.getElementById('sigma-second-van');
  function westWorkshopTrip(x, wait = 0) {
    return [
      drivingLeg(9.5, 13, 'M502 478L518 513'),
      drivingLeg(13.5 + wait, 25 + wait, `M518 513C523 524 515 525 511 526L359 559L333 563C315 566 320 568 322 579L${x} 635`),
      drivingLeg(43, 46.4, `M${x} 635L322 579`, true),
      drivingLeg(47, 57, 'M322 579C323 585 335 575 341 573L361 568L506 536C516 534 516 527 511 516L491 480C483 465 494 458 502 478')
    ];
  }
  const eastTrip = [
    drivingLeg(14.5, 17, 'M539 469L553 505'),
    drivingLeg(17.5, 26, 'M553 505C557 515 559 523 573 524L586 525C597 526 598 531 597 538L593 583L590 601'),
    drivingLeg(42, 45, 'M590 601L593 583L595 558C596 549 598 541 595 536C592 530 588 535 584 537', true),
    drivingLeg(45.5, 56.5, 'M584 537C596 531 598 521 582 516C568 512 563 500 558 487C552 469 546 458 539 456C531 454 534 457 539 469')
  ];
  const parkingBays = [[576, 474], [595, 469], [614, 465]];
  const parkingDirection = [6, 24];

  function distanceToSegment([x, y], [ax, ay], [bx, by]) {
    const dx = bx - ax;
    const dy = by - ay;
    const length = dx * dx + dy * dy;
    const fraction = length ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / length)) : 0;
    return Math.hypot(x - ax - fraction * dx, y - ay - fraction * dy);
  }

  function clearsRoads(surface, roads) {
    return roads.every(({ points, radius }) => points.slice(1).every((b, index) => {
      const a = points[index];
      if (inside(a, surface) || inside(b, surface)) return false;
      return surface.every((c, side) => {
        const d = surface[(side + 1) % surface.length];
        return !edgesCross(a, b, c, d) && Math.min(
          distanceToSegment(c, a, b), distanceToSegment(d, a, b),
          distanceToSegment(a, c, d), distanceToSegment(b, c, d)
        ) > radius;
      });
    }));
  }

  function planParking() {
    const centers = [[557, 478], ...parkingBays];
    const halfPitch = [9.5, -2.25];
    const boundaries = centers.map(([x, y]) => [x - halfPitch[0], y - halfPitch[1]]);
    const [lastX, lastY] = centers.at(-1);
    boundaries.push([lastX + halfPitch[0], lastY + halfPitch[1]]);
    const shifted = ([x, y], amount) => [x + parkingDirection[0] * amount, y + parkingDirection[1] * amount];
    const first = boundaries[0];
    const last = boundaries.at(-1);
    const strip = (start, end) => [shifted(first, start), shifted(last, start), shifted(last, end), shifted(first, end)];
    const roads = Array.from(city.querySelectorAll('[data-sigma-road-edge]'), road => ({
      points: Array.from(road.points, ({ x, y }) => [x, y]),
      radius: Number(road.getAttribute('stroke-width')) / 2 + 1.7
    }));
    const canLay = surface => clearsRoads(surface, roads) &&
      buildings.every(building => !overlap(surface, building.footprint));
    const pedestrian = strip(-.96, -.65);
    if (!canLay(pedestrian)) return null;
    const driveEdge = [1.38, 1.25, 1.12, 1, .88].find(depth => canLay(strip(-.65, depth)));
    if (driveEdge === undefined) return null;
    return { asphalt: strip(-.65, driveEdge), pedestrian, first, last, boundaries, shifted };
  }

  const parkingLayout = planParking();

  function drawParking() {
    if (!parkingLayout) return [];
    const parking = document.getElementById('sigma-parking');
    const { asphalt, pedestrian, first, last, boundaries, shifted } = parkingLayout;
    const polygon = points => points.map(point => point.join(',')).join(' ');
    const sidewalkEnd = shifted(first, -.8);
    drawPedestrianAccess(parking, `M519 442Q534 451 ${sidewalkEnd.join(' ')}`, 6);
    detail(parking, 'polygon', { points: polygon(asphalt), 'data-sigma-parking-surface': '',
      fill: '#736762', stroke: '#cfbea2', 'stroke-width': '1.6' });
    detail(parking, 'polygon', { points: polygon(pedestrian), 'data-sigma-parking-surface': '',
      fill: '#cdbfa5', stroke: '#a29076', 'stroke-width': '.7' });
    const marking = detail(parking, 'g', { fill: 'none', stroke: '#e9d9b8', 'stroke-width': '1.15' });
    for (const boundary of boundaries) {
      detail(marking, 'polyline', { points: polygon([shifted(boundary, -.63), shifted(boundary, .57)]) });
    }
    detail(marking, 'polyline', { points: polygon([shifted(first, -.63), shifted(last, -.63)]) });
    return [asphalt, pedestrian];
  }

  function drawPedestrianAccess(parent, path, width) {
    const edge = { d: path, fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' };
    detail(parent, 'path', { ...edge, stroke: '#978770', 'stroke-width': width + 1.7 });
    detail(parent, 'path', { ...edge, stroke: 'url(#sigma-concrete-pavers)', 'stroke-width': width });
  }

  const arrivals = [
    [person('#ca7255', 'Suivre l’employée au manteau corail'), route(`M${parkingBays[0].join(' ')}Q555 459 534 454L518 440`), 16.5, 23],
    [person('#5377a0', 'Suivre l’employé au manteau bleu'), route(`M${parkingBays[1].join(' ')}Q560 455 538 454L518 440`), 26.5, 33]
  ];
  const dispatcher = person('#a66b99', 'Suivre la personne qui ouvre le site SIGMA');
  const openingWalk = route('M476 468Q485 458 504 452L518 440');
  const technician = person('#497182', 'Suivre le technicien devant l’atelier');
  const client = person('#d17d54', 'Suivre le client');
  const secondTechnician = person('#158992', 'Suivre le technicien du deuxième atelier');
  const firstBoarding = route('M518 440Q513 457 505 461L497 478');
  const secondBoarding = route('M518 440Q531 445 536 456L544 469');
  const clientWalk = route('M360 664Q339 670 335 663Q331 653 341 654Q350 655 360 664');
  const secondWalk = route('M596 607L596 634Q594 645 576 643');
  const carEntrance = 'Q538 532 536 521L533 511Q532 506 544 504L';
  const fromWest = 'M-24 220L25 225C61 229 72 234 89 248C97 255 103 261 108 264C118 272 122 281 125 297C130 325 136 369 145 389L197 502C219 550 250 566 301 571C322 575 340 575 360 571L516 537';
  const toWest = 'M535 517C537 525 527 523 517 525L358 559C332 565 312 564 296 560L279 556C241 547 218 529 207 504L145 367C137 350 131 337 132 321C135 283 144 268 155 259C169 250 172 245 171 234C169 219 158 211 146 208C129 203 117 207 101 213C79 221 49 219 25 217L-24 214';

  function carJourney(start, park, depart, x, y) {
    const aisle = `${x + parkingDirection[0]} ${y + parkingDirection[1]}`;
    const bay = `${x} ${y}`;
    const pullOut = `${x + 11} ${y + 25}`;
    return [
      drivingLeg(start, park - 2.4, fromWest + carEntrance +
        `${x - 13} ${y + 28}C${x + 10} ${y + 27} ${x + 8.5} ${y + 34} ${aisle}`),
      drivingLeg(park - 2.4, park, `M${aisle}L${bay}`),
      drivingLeg(depart, depart + 3, `M${bay}L${x + 4} ${y + 16}Q${x + 7} ${y + 26} ${pullOut}`, true),
      drivingLeg(depart + 3, depart + 5, `M${pullOut}Q${x + 3} ${y + 27} 549 502Q532 505 534 513L535 517`),
      drivingLeg(depart + 6, depart + 22, toWest)
    ];
  }

  const cars = [
    {
      element: car('#c97554', 'Suivre la voiture de l’employée'),
      legs: carJourney(0, 16, 63, ...parkingBays[0])
    },
    {
      element: car('#427685', 'Suivre la voiture de l’employé'),
      legs: carJourney(9, 26, 71, ...parkingBays[1])
    },
    {
      element: car('#acaa70', 'Suivre la voiture du client'),
      legs: carJourney(18, 37, 55, ...parkingBays[2]),
      visitOnly: true
    }
  ];
  for (const car of cars) car.clear = Boolean(parkingLayout) && clearTrip(car.legs, carSize);
  const eastClear = clearTrip(eastTrip, vanSize);
  const secondTrip = eastClear ? eastTrip : eastTrip.slice(0, 1);

  function clearsTraffic(legs) {
    const size = { halfLength: 11.4, halfWidth: 5.3 };
    const traffic = [...cars.filter(car => car.clear).map(car => car.legs), secondTrip];
    for (let frame = 0; frame <= 960; frame++) {
      const time = frame / 10;
      const pose = drivePose(legs, time);
      const outline = vehicleOutline(pose, size);
      for (const other of traffic) {
        if (other !== secondTrip && (time < other[0].start || time >= other.at(-1).end)) continue;
        const otherPose = drivePose(other, eastClear || other !== secondTrip ? time : 0);
        if (Math.hypot(pose.x - otherPose.x, pose.y - otherPose.y) < 26 &&
            overlap(outline, vehicleOutline(otherPose, size))) return false;
      }
    }
    return true;
  }

  let westTrip = null;
  for (const x of [350, 344, 338, 332, 326, 320]) {
    if (!clearTrip(westWorkshopTrip(x), vanSize)) continue;
    for (const wait of [0, .4, .8, 1.2]) {
      const legs = westWorkshopTrip(x, wait);
      if (clearsTraffic(legs)) {
        westTrip = { x, wait, legs };
        break;
      }
    }
    if (westTrip) break;
  }
  const firstTrip = westTrip?.legs ?? westWorkshopTrip(332).slice(0, 1);
  const technicianWalk = route(`M${(westTrip?.x ?? 332) + 8} 640L345 655Q347 660 360 664`);
  const visitor = person('#b09862', 'Suivre le client devant SIGMA');
  const visitorArrival = route(`M${parkingBays[2].join(' ')}Q567 461 542 453L518 440`);
  const employeeExits = [
    [arrivals[0][0], arrivals[0][1], 56, 62.5, true],
    [arrivals[1][0], arrivals[1][1], 64, 70.5, true]
  ];
  const alert = document.getElementById('sigma-alert');
  const incidentTriangle = document.getElementById('sigma-incident-triangle');
  const issueBadge = document.getElementById('sigma-issue-badge');
  const workProgress = document.getElementById('sigma-work-progress');
  const workDots = Array.from(workProgress.querySelectorAll('circle'));
  const issueIcons = {
    conveyor: document.getElementById('sigma-issue-conveyor'),
    robot: document.getElementById('sigma-issue-robot'),
    vision: document.getElementById('sigma-issue-vision'),
    supervision: document.getElementById('sigma-issue-supervision')
  };
  const emotion = document.getElementById('sigma-emotion');
  const emotionFace = document.getElementById('sigma-emotion-face');
  const agreement = document.getElementById('sigma-agreement');
  const resolved = document.getElementById('sigma-resolved');
  const communicationLink = document.getElementById('sigma-communication-link');
  const phoneSignal = document.getElementById('sigma-phone-signal');
  const mailSignal = document.getElementById('sigma-mail-signal');
  const dusk = document.getElementById('sigma-dusk');
  const lampGlows = document.getElementById('sigma-lamp-glows');
  const workshops = [
    { x: 408, y: 586, bend: '337 430' },
    { x: 555, y: 571, bend: '677 441' }
  ].map(({ x, y, bend }) => ({
    x, y,
    marker: `translate(${x} ${y})`,
    incoming: route(`M${x} ${y - 6}Q${bend} 511 410`),
    reply: route(`M511 410Q${bend} ${x} ${y - 6}`)
  }));
  const scenarios = [
    { workshop: 0, issue: 'conveyor', visit: true },
    { workshop: 1, issue: 'robot', visit: true },
    { workshop: 0, issue: 'vision', planned: true, visit: true },
    { workshop: 1, issue: 'supervision', planned: true, visit: true },
    { workshop: 0, issue: 'conveyor', visit: false },
    { workshop: 1, issue: 'robot', planned: true, visit: false }
  ];
  let scenario = scenarios[0];
  let remainingScenarios = scenarios.slice(1);
  const teams = [
    { van: firstVan, technician, boarding: firstBoarding, walk: technicianWalk, legs: westTrip?.legs ?? westWorkshopTrip(332), available: Boolean(westTrip), boardTime: 3.6, walkTime: 4.3 },
    { van: secondVan, technician: secondTechnician, boarding: secondBoarding, walk: secondWalk, legs: eastTrip, available: eastClear, boardTime: 4.6, walkTime: 4.8 }
  ];

  function startIntervention() {
    const team = teams[scenario.workshop];
    const byPhone = cycle % 2 === 0;
    const [departure, arrival, returnTrip, home] = team.legs;
    const checklist = [
      { key: 'opening', duration: 0, notBefore: .8, readyAt: () => siteOpenedAt },
      { key: 'incoming', duration: byPhone ? 3.2 : 2.4 },
      ...(!byPhone ? [{ key: 'reply', duration: 1.6 }] : []),
      ...(scenario.planned ? [{ key: 'agreement', duration: 1.1 }] : []),
      { key: 'readyForBoarding', duration: 0, notBefore: departure.start - team.boardTime, requires: () => team.available },
      { key: 'boarding', duration: team.boardTime },
      { key: 'departure', duration: 0, notBefore: departure.start, latest: departure.start, requires: () => team.available },
      { key: 'outbound', duration: arrival.end - departure.start },
      { key: 'parking', duration: .35 },
      { key: 'enter', duration: team.walkTime },
      { key: 'work', duration: scenario.planned ? 4.7 : 5.3 },
      { key: 'exit', duration: team.walkTime - .4 },
      { key: 'returnReady', duration: 0, notBefore: returnTrip.start, latest: returnTrip.start },
      { key: 'inbound', duration: home.end - returnTrip.start },
      { key: 'closed' }
    ];
    return { team, byPhone, checklist, index: 0, startedAt: .8, completed: new Map() };
  }

  function advanceIntervention(intervention, seconds) {
    const { checklist, completed } = intervention;
    while (intervention.index < checklist.length - 1) {
      const step = checklist[intervention.index];
      const endsAt = Math.max(intervention.startedAt + step.duration, step.notBefore ?? -Infinity, step.readyAt?.() ?? -Infinity);
      if (seconds < endsAt || endsAt > (step.latest ?? Infinity) + 1e-6 || (step.requires && !step.requires())) break;
      completed.set(step.key, endsAt);
      intervention.index++;
      intervention.startedAt = endsAt;
    }
    return checklist[intervention.index];
  }

  function nextScenario() {
    if (!remainingScenarios.length) {
      remainingScenarios = scenarios.filter(candidate => candidate !== scenario);
    }
    const index = Math.floor(Math.random() * remainingScenarios.length);
    [scenario] = remainingScenarios.splice(index, 1);
  }

  function drawCommunications(seconds, clientPosition, intervention, step) {
    const workshop = workshops[scenario.workshop];
    const { byPhone, startedAt, completed } = intervention;
    const incoming = step.key === 'incoming';
    const reply = step.key === 'reply';
    phoneSignal.setAttribute('visibility', byPhone && incoming ? 'visible' : 'hidden');
    mailSignal.setAttribute('visibility', !byPhone && (incoming || reply) ? 'visible' : 'hidden');
    communicationLink.setAttribute('visibility', incoming || reply ? 'visible' : 'hidden');
    if (incoming || reply) {
      const track = incoming ? workshop.incoming : workshop.reply;
      const progress = (seconds - startedAt) / step.duration;
      const point = pointAt(track, progress);
      const signal = byPhone ? phoneSignal : mailSignal;
      signal.setAttribute('transform', `translate(${point.x} ${point.y})`);
      communicationLink.setAttribute('d', track.path.getAttribute('d'));
      communicationLink.setAttribute('stroke', byPhone ? '#205977' : '#754784');
    }
    alert.setAttribute('transform', workshop.marker);
    const planned = scenario.planned;
    const showAlert = completed.has('opening') && !completed.has('work');
    const working = step.key === 'work';
    const finishedAt = completed.get('work');
    alert.setAttribute('display', showAlert ? 'inline' : 'none');
    incidentTriangle.setAttribute('visibility', showAlert && !planned && !working ? 'visible' : 'hidden');
    issueBadge.setAttribute('transform', planned || working ? 'translate(0 0)' : 'translate(24 -9) scale(.8)');
    alert.setAttribute('fill', working ? '#365c62' : planned ? '#816735' : '#aa5341');
    workProgress.setAttribute('display', working ? 'inline' : 'none');
    if (working) {
      const lit = Math.floor((seconds - startedAt) * 4) % workDots.length;
      workDots.forEach((dot, index) => dot.setAttribute('opacity', reducedMotion.matches || index === lit ? '1' : '.36'));
    }
    for (const [issue, icon] of Object.entries(issueIcons)) {
      icon.setAttribute('visibility', showAlert && issue === scenario.issue ? 'visible' : 'hidden');
    }
    resolved.setAttribute('transform', workshop.marker);
    alert.setAttribute('opacity', planned || working ? '1' : String(0.82 + 0.18 * Math.sin(seconds * 5)));
    resolved.setAttribute('visibility', seconds - finishedAt < 6 ? 'visible' : 'hidden');
    const worried = !planned && seconds - completed.get('incoming') < 2.3;
    const relieved = !planned && seconds - finishedAt < 3;
    const anchor = worried && clientPosition
      ? { x: clientPosition.x + 11, y: clientPosition.y - 24 }
      : { x: workshop.x + (worried ? -24 : 23), y: workshop.y - 9 };
    emotion.setAttribute('visibility', worried || relieved ? 'visible' : 'hidden');
    emotion.setAttribute('transform', `translate(${anchor.x} ${anchor.y})`);
    const face = worried ? '😰' : relieved ? '😄' : '';
    if (emotionFace.textContent !== face) emotionFace.textContent = face;
    agreement.setAttribute('visibility', step.key === 'agreement' ? 'visible' : 'hidden');
  }

  function drawTeam(team, step, seconds) {
    if (team !== intervention.team) {
      drive(team.van, team.legs, 0);
      team.technician.style.display = 'none';
      return;
    }

    const { startedAt, completed } = intervention;
    const [departure, arrival, returnTrip, home] = team.legs;
    const duration = seconds - startedAt;
    let driveTime = completed.has('outbound') ? arrival.end : 0;
    if (step.key === 'outbound') driveTime = departure.start + duration;
    if (step.key === 'inbound') driveTime = returnTrip.start + duration;
    if (step.key === 'closed') driveTime = home.end;
    drive(team.van, team.legs, driveTime);

    if (step.key === 'boarding') walker(team.technician, team.boarding, seconds, startedAt, startedAt + step.duration);
    else if (step.key === 'enter' || step.key === 'exit') {
      walker(team.technician, team.walk, seconds, startedAt, startedAt + step.duration, step.key === 'exit');
    } else team.technician.style.display = 'none';
  }

  const rain = document.getElementById('sigma-rain');
  const wetGround = document.getElementById('sigma-wet-ground');
  const afterRain = document.getElementById('sigma-after-rain');
  const asphaltColor = document.getElementById('sigma-asphalt-color');
  const nearRain = document.getElementById('sigma-rain-near');
  const farRain = document.getElementById('sigma-rain-far');
  const rainRings = Array.from(wetGround.querySelectorAll('.sigma-rain-ring'));
  const storm = document.getElementById('sigma-storm');
  const lightning = document.getElementById('sigma-lightning');

  const animals = document.getElementById('sigma-animals');
  const shinyOdds = 8192;
  const species = {
    cat: { color: '#cc8752', shiny: '#d6c9ff', scale: .40, bounce: .4 },
    sparrow: { color: '#998171', shiny: '#b0d9ee', scale: .32, bounce: .9 },
    squirrel: { color: '#ae6444', shiny: '#f2d393', scale: .39, bounce: .85 },
    deer: { color: '#b68b62', shiny: '#e3dec5', scale: .68, bounce: .45, rarity: 'rare' },
    hare: { color: '#b59b76', shiny: '#d8e7c4', scale: .46, bounce: 1.2 },
    pheasant: { color: '#b67a4d', shiny: '#e5a1ba', scale: .43, bounce: .55, rarity: 'uncommon' }
  };
  const wildlifeSpots = [
    { habitat: 'buildings', common: 'cat', start: 1, end: 16, path: 'M476 460Q478 466 490 468Q505 471 516 462' },
    { habitat: 'buildings', common: 'sparrow', start: 37, end: 55, path: 'M375 324Q389 321 402 321L426 318' },
    { habitat: 'woods', common: 'squirrel', rare: 'deer', rarePercent: .06, start: 36, end: 66,
      path: 'M677 612Q697 621 704 636Q718 658 746 668' },
    { habitat: 'field', common: 'hare', rare: 'pheasant', rarePercent: .19, start: 32, end: 63,
      path: 'M190 704Q207 675 226 679Q245 681 274 711' }
  ].map(spot => ({ ...spot, track: route(spot.path) }));
  let wildlife = [];

  function spawnWildlife() {
    for (const animal of wildlife) {
      sceneryMasks.get(animal.element)?.parentNode.remove();
      sceneryMasks.delete(animal.element);
    }
    animals.replaceChildren();
    wildlife = wildlifeSpots.map(spot => {
      const kind = spot.rare && Math.random() < spot.rarePercent ? spot.rare : spot.common;
      const shiny = Math.random() < 1 / shinyOdds;
      const look = species[kind];
      const element = detail(animals, 'g', { 'data-habitat': spot.habitat, 'data-species': kind,
        'data-rarity': shiny ? 'shiny' : look.rarity ?? 'common', display: 'none' });
      detail(element, 'ellipse', { cx: 0, cy: 1, rx: 7 * look.scale, ry: 2.3 * look.scale, fill: '#342b2a', opacity: '.28' });
      const sprite = detail(element, 'use', { href: `#sigma-animal-${kind}`, color: shiny ? look.shiny : look.color });
      const spark = shiny ? detail(element, 'path', {
        d: 'M10-18l1.1 3.2 3.3 1.1-3.3 1.1-1.1 3.2-1.1-3.2-3.3-1.1 3.3-1.1Z M-9-15l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7Z',
        fill: '#fff2ae', stroke: '#d3a85a', 'stroke-width': '.7', opacity: 0
      }) : null;
      return { ...spot, element, sprite, spark, look };
    });
  }

  function drawWildlife(dayTime) {
    for (const { start, end, track, element, sprite, spark, look } of wildlife) {
      const age = dayTime - start;
      const duration = end - start;
      const visible = age >= 0 && age < duration;
      element.setAttribute('display', visible ? 'inline' : 'none');
      if (!visible) continue;
      const time = age / duration;
      const phase = time * 3;
      const pause = phase - Math.floor(phase);
      const travel = (Math.floor(phase) + Math.min(1, pause / .75)) / 3;
      const point = pointAt(track, travel);
      const ahead = pointAt(track, Math.min(1, travel + .025));
      const before = pointAt(track, Math.max(0, travel - .025));
      const heading = ahead.x < before.x ? -1 : 1;
      const bob = pause < .75 && !reducedMotion.matches ? Math.abs(Math.sin(age * 7)) * look.bounce : 0;
      element.setAttribute('transform', `translate(${point.x} ${point.y})`);
      element.setAttribute('opacity', Math.min(1, age * 2, (duration - age) * 2));
      sprite.setAttribute('transform', `translate(0 ${-bob}) scale(${heading * look.scale} ${look.scale})`);
      if (spark) spark.setAttribute('opacity', age < 1.6 ? 1 - age / 1.6 : 0);
      maskBehindScenery(element, point);
    }
  }

  function drawLightning(dayTime) {
    const age = dayTime - 4.4;
    const active = !reducedMotion.matches && age >= 0 && age < .95;
    const pulse = active ? Math.min(1, age / .045) * (1 - age / .95) : 0;
    storm.setAttribute('opacity', pulse);
    lightning.setAttribute('opacity', active ? Math.max(0, 1 - age / .28) : 0);
  }

  function drawRain(dayTime) {
    const intensity = Math.max(0, Math.min(1, (34 - dayTime) / 9));
    const wetness = Math.max(0, Math.min(1, (47 - dayTime) / 15));
    rain.setAttribute('opacity', intensity);
    wetGround.setAttribute('opacity', wetness);
    const sunlight = Math.max(0, Math.min(1, (dayTime - 31) / 17, (74 - dayTime) / 17));
    afterRain.setAttribute('opacity', sunlight * .32);
    const roadColor = `rgb(${Math.round(85 - wetness * 20)},${Math.round(79 - wetness * 13)},${Math.round(77 - wetness * 7)})`;
    if (asphaltColor.getAttribute('fill') !== roadColor) asphaltColor.setAttribute('fill', roadColor);
    city.style.setProperty('--sigma-shadow-opacity', String(.19 + (1 - wetness) * .08 + sunlight * .13));
    if (!intensity && !wetness) return;
    const time = reducedMotion.matches ? 0 : dayTime;
    nearRain.setAttribute('patternTransform', `translate(${time * -124 % 88} ${time * 310 % 72})`);
    farRain.setAttribute('patternTransform', `translate(${time * -79 % 104} ${time * 196 % 89})`);
    rainRings.forEach((ring, index) => {
      const progress = (time * 2.2 + index / rainRings.length) % 1;
      ring.setAttribute('rx', 1 + progress * 5);
      ring.setAttribute('ry', .45 + progress * 1.8);
      ring.setAttribute('opacity', intensity * (1 - progress));
    });
  }

  function draw(dayTime) {
    walker(dispatcher, openingWalk, dayTime, .3, openingArrival);
    if (dayTime >= openingArrival) siteOpenedAt = openingArrival - openingTime;
    const seconds = dayTime - openingTime;
    const step = advanceIntervention(intervention, seconds);
    if (city.dataset.sigmaCheckpoint !== step.key) city.dataset.sigmaCheckpoint = step.key;
    for (const team of teams) drawTeam(team, step, seconds);
    for (const { element, legs, visitOnly, clear } of cars) {
      const shown = clear && (!visitOnly || scenario.visit) && seconds >= legs[0].start && seconds < legs.at(-1).end;
      element.style.display = shown ? '' : 'none';
      if (shown) drive(element, legs, seconds);
    }
    const pedestrians = seconds < 52 ? arrivals : employeeExits;
    pedestrians.forEach(([figure, track, start, end, reverse], index) =>
      walker(figure, track, cars[index].clear ? seconds : -1, start, end, reverse));
    const visitTime = scenario.visit && cars[2].clear ? seconds : -1;
    const clientPosition = walker(client, clientWalk, scenario.workshop === 0 ? seconds : -1, 1, 8);
    if (visitTime < 43) walker(visitor, visitorArrival, visitTime, 37.5, 42);
    else walker(visitor, visitorArrival, visitTime, 49, 54.5, true);
    drawCommunications(seconds, clientPosition, intervention, step);
    drawWildlife(dayTime);
    drawRain(dayTime);
    drawLightning(dayTime);
    const evening = Math.max(0, Math.min(1, (seconds - 70) / 17));
    const night = evening * Math.min(1, (dayDuration - dayTime) / 3);
    dusk.setAttribute('opacity', String(night * .38));
    lampGlows.setAttribute('opacity', String(night));
  }

  let paused = reducedMotion.matches;
  let elapsed = paused ? 5 : 0;
  let cycle = 0;
  let siteOpenedAt = Infinity;
  let intervention = startIntervention();
  let frame = null;
  let lastTick = null;

  function tick(timestamp) {
    if (lastTick !== null) {
      elapsed += (timestamp - lastTick) * playbackRate / 1000;
      const daysPassed = Math.floor(elapsed / dayDuration);
      if (daysPassed) {
        cycle += daysPassed;
        nextScenario();
        siteOpenedAt = Infinity;
        intervention = startIntervention();
        spawnWildlife();
      }
      elapsed %= dayDuration;
    }
    lastTick = timestamp;
    draw(elapsed);
    frame = requestAnimationFrame(tick);
  }

  function syncPlayback() {
    const playing = !paused && !document.hidden && map.classList.contains('sigma-map-active');
    if (playing && frame === null) frame = requestAnimationFrame(tick);
    if (!playing && frame !== null) {
      cancelAnimationFrame(frame);
      frame = null;
      lastTick = null;
    }
    pauseButton.classList.toggle('sigma-paused', paused);
    pauseButton.setAttribute('aria-label', paused ? 'Animer la ville' : 'Mettre la ville en pause');
  }

  pauseButton.addEventListener('click', () => {
    paused = !paused;
    syncPlayback();
  });
  reducedMotion.addEventListener('change', event => {
    if (event.matches) paused = true;
    syncPlayback();
  });
  document.addEventListener('visibilitychange', syncPlayback);
  new MutationObserver(syncPlayback).observe(map, { attributes: true, attributeFilter: ['class'] });
  dressBuildings();
  const frontages = document.getElementById('sigma-frontages');
  for (const track of [technicianWalk, secondWalk]) {
    drawPedestrianAccess(frontages, track.path.getAttribute('d'), 4.5);
  }
  layerScenery(drawParking());
  const stormRoofs = document.getElementById('sigma-storm-roofs');
  for (const roof of city.querySelectorAll('[data-sigma-roof]')) {
    detail(stormRoofs, 'polygon', { points: roof.getAttribute('points'), 'stroke-width': 5, opacity: '.24' });
    detail(stormRoofs, 'polygon', { points: roof.getAttribute('points'), 'stroke-width': 1.3, opacity: '.9' });
  }
  spawnWildlife();
  draw(elapsed);
  syncPlayback();
})();
