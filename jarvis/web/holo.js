/* The 3D card: a hologram-style view of a validated scene spec (see jarvis/holo.py) or of a real model file (glb/gltf/stl, loaded by three.js loaders only).
   Loaded on demand (dynamic import), so the page costs nothing until a 3D card opens. three.js r160 (MIT) is vendored in /static/vendor/three, no CDN.
   Budget for this CPU: 30 fps cap, pixel ratio <= 1.5, no shadows, no post-processing, renders only when something changes (or while it spins), stops when the card is hidden
   or closed and disposes everything; if WebGL fails or the frame rate stays very low it falls back to a flat labelled SVG diagram with a plain line saying why.
   No keyboard shortcuts live here. Nothing from the brain is ever executed: parts are drawn from a fixed set of shapes. */
import * as THREE from 'three';
import { OrbitControls } from '/static/vendor/three/jsm/controls/OrbitControls.js';

const PALETTE = { cyan: 0x4fd8ff, blue: 0x4a7dff, red: 0xff4b5c, orange: 0xff9a3c, yellow: 0xffd84a, green: 0x4be39a, white: 0xeaf2ff, grey: 0x9aa8bd, purple: 0xa77bff, pink: 0xff7bc8 };
const MAX_TRIS = 100000, TARGET_FPS = 30, MIN_FPS = 7;

export function mount(host, card, api) {
  const look = () => api.look();                              // {accent: '#rrggbb', lite: bool, base: 'jarvis'|'rose'}
  const el = (tag, cls, txt) => { const e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };
  const spec = card.spec, ctl = { state: {}, apply: () => {}, dispose: () => {} };
  host.classList.add('holo'); host.textContent = '';
  const stage = el('div', 'hstage'), cv = el('canvas', 'hcv'), over = el('div', 'hover'), svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('class', 'hlines');
  const info = el('div', 'hinfo'), bar = el('div', 'hbar'), credit = el('div', 'hcredit'), more = el('div', 'hmore');
  stage.append(cv, svg, over, info); host.append(stage, bar, credit, more);
  credit.textContent = (card.source && card.source.line) || '';
  if (card.bullets && card.bullets.length) { const ul = el('ul', 'hbul'); card.bullets.forEach(b => ul.append(el('li', null, b))); more.append(ul); }
  if (card.more) { const b = el('button', 'tiny box', 'MORE'), t = el('div', 'hmoretxt', card.more); t.style.display = 'none'; b.onclick = () => { t.style.display = t.style.display ? '' : 'none'; b.textContent = t.style.display ? 'MORE' : 'LESS'; }; more.append(b, t); }

  // ------------------------------------------------------------------ WebGL, or the flat diagram
  let renderer = null, failReason = '';
  try {
    renderer = new THREE.WebGLRenderer({ canvas: cv, antialias: !look().lite, alpha: true, powerPreference: 'low-power', preserveDrawingBuffer: false });
    if (!renderer.getContext()) throw new Error('no context');
  } catch (e) { failReason = 'WebGL is not available on this screen'; renderer = null; }
  if (!renderer) { flat(failReason); return ctl; }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5)); renderer.localClippingEnabled = true; renderer.setClearColor(0x000000, 0);

  const scene = new THREE.Scene(), cam = new THREE.PerspectiveCamera(45, 1, 0.05, 2000), root = new THREE.Group(); scene.add(root);
  const controls = new OrbitControls(cam, cv); controls.enableDamping = true; controls.dampingFactor = 0.12; controls.autoRotate = true; controls.autoRotateSpeed = 1.4; controls.enablePan = true;
  controls.touches = { ONE: THREE.TOUCH.ROTATE, TWO: THREE.TOUCH.DOLLY_PAN };
  const parts = new Map(), order = [], geos = [], mats = [];
  const st = ctl.state = { sel: null, hidden: new Set(), wire: false, labels: true, explode: 0, explodeT: 0, spin: true, mode: null, cut: null, measure: [], notes: (card.notes || []).slice() };
  const plane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 1e6);                // the cut plane lives in the MODEL's space, so it turns with the model when you rotate the view
  const accent = () => new THREE.Color(look().accent || '#3d8bff');
  const tint = name => { if (name === 'cyan' || !PALETTE[name]) return accent().lerp(new THREE.Color(0xffffff), 0.35); return new THREE.Color(PALETTE[name]); };

  function geom(p) {
    const [x, y, z] = p.size;
    switch (p.shape) {
      case 'box': return new THREE.BoxGeometry(x, y, z);
      case 'sphere': { const g = new THREE.SphereGeometry(0.5, 20, 14); g.scale(x, y, z); return g; }
      case 'cylinder': return new THREE.CylinderGeometry(x / 2, x / 2, y, 22);
      case 'cone': return new THREE.ConeGeometry(x / 2, y, 22);
      case 'torus': return new THREE.TorusGeometry(Math.max(x, 0.02) / 2, Math.max(Math.min(y, x) / 2, 0.01), 12, 30);
      case 'capsule': return new THREE.CapsuleGeometry(x / 2, Math.max(0.01, y - x), 4, 12);
      case 'plane': { const g = new THREE.PlaneGeometry(x, y); g.rotateX(-Math.PI / 2); return g; }
      case 'ring': { const g = new THREE.RingGeometry(Math.min(y, x) / 2, x / 2, 72); g.rotateX(-Math.PI / 2); return g; }
      case 'extrude': { const sh = new THREE.Shape(); p.points.forEach((q, i) => i ? sh.lineTo(q[0], q[1]) : sh.moveTo(q[0], q[1])); const g = new THREE.ExtrudeGeometry(sh, { depth: Math.max(z, 0.01), bevelEnabled: false }); g.translate(0, 0, -z / 2); return g; }
      case 'tube': { const c = new THREE.CatmullRomCurve3(p.points.map(q => new THREE.Vector3(q[0], q[1], q[2]))); return new THREE.TubeGeometry(c, Math.min(60, p.points.length * 6), Math.max(x, 0.02) / 2, 8, false); }
    }
    return new THREE.BoxGeometry(1, 1, 1);
  }

  let tris = 0;
  function build(list) {
    for (const p of list) {
      const g = geom(p); geos.push(g); tris += (g.index ? g.index.count : g.attributes.position.count) / 3;
      const col = tint(p.color);
      const m = new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.26, depthWrite: false, side: THREE.DoubleSide, clippingPlanes: [plane] }); mats.push(m);
      const mesh = new THREE.Mesh(g, m); mesh.userData.id = p.id;
      const eg = new THREE.EdgesGeometry(g, look().lite ? 60 : 28), em = new THREE.LineBasicMaterial({ color: col.clone().lerp(new THREE.Color(0xffffff), 0.3), transparent: true, opacity: 0.95, clippingPlanes: [plane], blending: look().lite ? THREE.NormalBlending : THREE.AdditiveBlending, depthWrite: false });
      geos.push(eg); mats.push(em); const edges = new THREE.LineSegments(eg, em);
      const grp = new THREE.Group(); grp.add(mesh, edges);
      grp.position.set(...p.pos); grp.rotation.set(...p.rot.map(d => d * Math.PI / 180));
      parts.set(p.id, { p, grp, mesh, edges, m, em, col, base: grp.position.clone(), ex: new THREE.Vector3(...(p.explode || [0, 0, 0])) }); order.push(p.id);
    }
    for (const id of order) { const e = parts.get(id), par = e.p.parent && parts.get(e.p.parent); (par ? par.grp : root).add(e.grp); }
  }

  function loadModel() {                                                         // a real model: three.js loaders only, never executed, parts = the file's own named nodes
    const m = card.model;
    const fin = obj => {
      let n = 0; obj.traverse(o => { if (o.isMesh && o.geometry) { const gg = o.geometry; n += (gg.index ? gg.index.count : gg.attributes.position.count) / 3; } });
      if (n > MAX_TRIS) { failNote('This model has ' + Math.round(n / 1000) + 'k triangles: too heavy for this computer, shown as points.'); }
      const box0 = new THREE.Box3().setFromObject(obj), sz = box0.getSize(new THREE.Vector3()), k = 10 / Math.max(sz.x, sz.y, sz.z || 1); obj.scale.multiplyScalar(k);
      const box = new THREE.Box3().setFromObject(obj), c = box.getCenter(new THREE.Vector3()); obj.position.sub(c);
      let i = 0; const named = [];
      obj.traverse(o => { if (o.isMesh) { const id = 'm' + (i++); const col = tint('cyan'); const heavy = n > MAX_TRIS;
        const mat = new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.26, depthWrite: false, side: THREE.DoubleSide, clippingPlanes: [plane] }); mats.push(mat);
        const eg = new THREE.EdgesGeometry(o.geometry, 35), em = new THREE.LineBasicMaterial({ color: col.clone().lerp(new THREE.Color(0xffffff), 0.3), transparent: true, opacity: 0.9, clippingPlanes: [plane] }); geos.push(eg); mats.push(em);
        if (heavy) { const pts = new THREE.Points(o.geometry, new THREE.PointsMaterial({ color: col, size: 0.03 })); o.parent.add(pts); o.visible = false; }
        o.material = mat; o.userData.id = id; const edges = heavy ? new THREE.Object3D() : new THREE.LineSegments(eg, em); if (!heavy) o.add(edges);
        const label = (o.name || o.parent && o.parent.name || ('part ' + i)).replace(/[_-]+/g, ' ').slice(0, 28);
        parts.set(id, { p: { id, label, desc: 'A part of the model (' + label + ').', shape: 'mesh', pos: [0, 0, 0], size: [1, 1, 1], color: 'cyan' }, grp: o, mesh: o, edges, m: mat, em, col, base: o.position.clone(), ex: new THREE.Vector3() }); order.push(id); named.push(id); } });
      root.add(obj); finish();
    };
    const url = m.url; const done = fin;
    if (m.kind === 'stl') import('/static/vendor/three/jsm/loaders/STLLoader.js').then(({ STLLoader }) => new STLLoader().load(url, g => { g.computeVertexNormals(); const mesh = new THREE.Mesh(g, new THREE.MeshBasicMaterial()); const gr = new THREE.Group(); gr.add(mesh); mesh.name = card.title || 'model'; done(gr); }, null, () => flat('The model file could not be read')));
    else import('/static/vendor/three/jsm/loaders/GLTFLoader.js').then(({ GLTFLoader }) => new GLTFLoader().load(url, g => done(g.scene), null, () => flat('The model file could not be read')));
  }
  function failNote(t) { credit.textContent = t + '  ' + credit.textContent; }

  // ------------------------------------------------------------------ framing, labels, interaction
  let slow = 0, frameR = 10, needs = true, last = 0, alive = true, fpsT0 = performance.now(), fpsN = 0, glide = null;
  const lab = new Map(), pins = [], tmpV = new THREE.Vector3(), ray = new THREE.Raycaster();
  function finish() {
    const box = new THREE.Box3().setFromObject(root), sz = box.getSize(new THREE.Vector3()), c = box.getCenter(new THREE.Vector3());
    frameR = Math.max(sz.x, sz.y, sz.z, 1) * 0.8; controls.target.copy(c);
    const sph = box.getBoundingSphere(new THREE.Sphere()), asp = Math.max(0.3, (stage.clientWidth || 300) / (stage.clientHeight || 200)), vf = cam.fov * Math.PI / 180, hf = 2 * Math.atan(Math.tan(vf / 2) * asp);
    const fit = Math.max(0.3, sph.radius) / Math.sin(Math.min(vf, hf) / 2) * 1.08, dir = new THREE.Vector3(1.3, 0.9, 1.9).normalize();               // the whole model fills the stage, whatever its shape (a wide scene in a short stage was tiny)
    let dist = Math.min(fit, frameR * 2.1 * 1.6); cam.aspect = asp; cam.updateProjectionMatrix();
    for (let it = 0; it < 4; it++) {                                                                      // tighten: project the 8 corners of the box and scale the distance until they just fill 86% of the stage
      cam.position.copy(c).addScaledVector(dir, dist); cam.lookAt(c); cam.updateMatrixWorld(true); let m = 0.01;
      for (let q = 0; q < 8; q++) { tmpV.set(q & 1 ? box.max.x : box.min.x, q & 2 ? box.max.y : box.min.y, q & 4 ? box.max.z : box.min.z).project(cam); m = Math.max(m, Math.abs(tmpV.x), Math.abs(tmpV.y)); }
      dist = Math.max(sph.radius * 0.6, dist * Math.min(1.6, Math.max(0.5, m / 0.86)));
    }
    st.home = { target: c.clone(), dist };
    cam.position.copy(c).addScaledVector(dir, dist); controls.minDistance = frameR * 0.3; controls.maxDistance = frameR * 6; controls.update();
    const floorY = box.min.y - 0.05, grid = new THREE.GridHelper(Math.ceil(frameR * 4), look().lite ? 8 : 20, 0x335577, 0x1b2a40); grid.position.set(c.x, floorY, c.z); grid.material.transparent = true; grid.material.opacity = 0.35; grid.material.clippingPlanes = [plane]; mats.push(grid.material); geos.push(grid.geometry); scene.add(grid); st.grid = grid;
    cutRange = Math.max(sz.x, sz.y, sz.z) * 0.6; st.center = c.clone();
    for (const id of order) { const p = parts.get(id).p, d = el('div', 'hlab', p.label.toUpperCase()); d.dataset.id = id; d.onclick = e => { e.stopPropagation(); select(id); }; over.append(d); const ln = document.createElementNS('http://www.w3.org/2000/svg', 'line'); svg.append(ln); lab.set(id, { d, ln }); }
    (st.notes || []).forEach(addPinEl); buildBar(); resize(); needs = true; requestAnimationFrame(loop);
    info.style.display = 'none';
  }
  let cutRange = 5;
  function resize() { const w = stage.clientWidth || 300, h = stage.clientHeight || 200; renderer.setSize(w, h, false); cv.style.width = '100%'; cv.style.height = '100%'; cam.aspect = w / h; cam.updateProjectionMatrix(); svg.setAttribute('viewBox', `0 0 ${w} ${h}`); needs = true; }
  const ro = new ResizeObserver(resize); ro.observe(stage);
  controls.addEventListener('change', () => { needs = true; }); controls.addEventListener('start', () => { glide = null; if (controls.autoRotate) { controls.autoRotate = false; } });

  function center(id) { const e = parts.get(id); const b = new THREE.Box3().setFromObject(e.grp); return b.getCenter(new THREE.Vector3()); }
  function project(v) { tmpV.copy(v).project(cam); return { x: (tmpV.x * 0.5 + 0.5) * stage.clientWidth, y: (-tmpV.y * 0.5 + 0.5) * stage.clientHeight, z: tmpV.z }; }
  function layout() {
    const w = stage.clientWidth, h = stage.clientHeight, k = order.length;
    const items = [];
    order.forEach((id, i) => {
      const L = lab.get(id), e = parts.get(id);
      const sub = k > 6 && e.p.parent && st.sel !== id && st.sel !== e.p.parent && !L.show;              // small sub-parts (colour bands, pins) only get a label when they or their parent are selected: no pile-up
      const hide = !st.labels || st.hidden.has(id) || sub || (k > 16 && st.sel !== id && !L.show);
      if (hide) { L.d.style.display = 'none'; L.ln.style.display = 'none'; return; }
      const c = project(center(id)); if (c.z > 1 || c.x < -20 || c.x > w + 20 || c.y < -20 || c.y > h + 20) { L.d.style.display = 'none'; L.ln.style.display = 'none'; return; }
      const side = (i % 2 ? 1 : -1), lx = Math.max(6, Math.min(w - 6, c.x + side * (46 + (i % 3) * 14))), ly = Math.max(10, Math.min(h - 10, c.y - 26 - (i % 3) * 10));
      items.push({ id, L, c, side, lx, ly });
    });
    [-1, 1].forEach(sd => {                                                                              // labels on the same side keep at least 20 px apart (top to bottom), so none sits on another
      const g = items.filter(o => o.side === sd).sort((a, b) => a.ly - b.ly); let prev = -1e9;
      g.forEach(o => { o.ly = Math.max(o.ly, prev + 20); prev = o.ly; }); for (let n = g.length - 1, lim = h - 10; n >= 0; n--) { g[n].ly = Math.min(g[n].ly, lim); lim = g[n].ly - 20; }
    });
    items.forEach(o => { const { id, L, c, side, lx, ly } = o;
      L.d.style.display = ''; L.d.style.left = lx + 'px'; L.d.style.top = ly + 'px'; L.d.style.transform = side > 0 ? 'translate(0,-50%)' : 'translate(-100%,-50%)'; L.d.classList.toggle('sel', st.sel === id);
      L.ln.style.display = ''; L.ln.setAttribute('x1', c.x); L.ln.setAttribute('y1', c.y); L.ln.setAttribute('x2', lx); L.ln.setAttribute('y2', ly); L.ln.setAttribute('class', st.sel === id ? 'sel' : '');
    });
    if (st.sel && info.style.display !== 'none') { const c = project(center(st.sel)); info.style.left = Math.max(6, Math.min(w - 230, c.x + 24)) + 'px'; info.style.top = Math.max(6, Math.min(h - info.offsetHeight - 6, c.y - 20)) + 'px'; }
    pins.forEach(pn => { const e = parts.get(pn.n.part), w3 = e ? e.grp.localToWorld(new THREE.Vector3(...pn.n.pos)) : new THREE.Vector3(...pn.n.pos), c = project(w3);
      let vis = c.z < 1; if (vis && pn.tick++ % 6 === 0) { const d = w3.distanceTo(cam.position); ray.set(cam.position, w3.clone().sub(cam.position).normalize()); const hit = ray.intersectObjects([...parts.values()].filter(p => p.grp.visible).map(p => p.mesh), false)[0]; pn.occ = !!(hit && hit.distance < d - Math.max(0.15, frameR * 0.02)); }
      pn.d.style.display = vis && !pn.occ ? '' : 'none'; pn.d.style.left = c.x + 'px'; pn.d.style.top = c.y + 'px'; });
    st.measure.forEach(mm => { if (mm.line) { const a = project(mm.a), b = project(mm.b); mm.d.style.left = (a.x + b.x) / 2 + 'px'; mm.d.style.top = (a.y + b.y) / 2 + 'px'; } });
  }

  function style() {
    for (const [id, e] of parts) {
      const dim = st.sel && st.sel !== id, on = st.sel === id; e.grp.visible = !st.hidden.has(id);
      e.m.opacity = st.wire ? 0.5 : on ? 0.55 : dim ? 0.07 : 0.26; e.m.wireframe = st.wire; e.edges.visible = !st.wire; e.em.opacity = dim ? 0.25 : 1;
      e.m.color.copy(on ? e.col.clone().lerp(new THREE.Color(0xffffff), 0.35) : e.col); e.em.color.copy(on ? new THREE.Color(0xffffff) : e.col.clone().lerp(new THREE.Color(0xffffff), 0.3));
    }
    needs = true;
  }
  function select(id) {
    if (id && !parts.has(id)) return;
    st.sel = id; style(); api.send({ t: 'holo_sel', id: id || '' });
    if (!id) { info.style.display = 'none'; return; }
    const p = parts.get(id).p; info.textContent = ''; info.append(el('b', null, p.label), el('p', null, p.desc)); const ul = el('ul'); info.append(ul); info.style.display = ''; info.dataset.id = id;
    const c = center(id), dist = Math.max(frameR * 0.9, cam.position.distanceTo(controls.target) * 0.7); glide = { t0: performance.now(), from: controls.target.clone(), to: c, d0: cam.position.distanceTo(controls.target), d1: dist }; needs = true;
  }
  ctl.setBullets = (id, list) => { if (info.dataset.id === id) { const ul = info.querySelector('ul'); if (ul) { ul.textContent = ''; list.forEach(b => ul.append(el('li', null, b))); } } };

  // pick a part: a tap (not a drag). Measure and note modes pick a POINT on the surface instead.
  let down = null;
  cv.addEventListener('pointerdown', e => { down = { x: e.clientX, y: e.clientY, t: performance.now() }; });
  cv.addEventListener('pointerup', e => {
    if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 7 || performance.now() - down.t > 600) { down = null; return; } down = null;
    const r = cv.getBoundingClientRect(); ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), cam);
    const meshes = [...parts.values()].filter(p => p.grp.visible).map(p => p.mesh); const hit = ray.intersectObjects(meshes, false)[0];
    if (st.mode === 'measure' && hit) return measureTap(hit.point);
    if (st.mode === 'note' && hit) return noteTap(hit);
    if (hit) select(hit.object.userData.id); else select(null);
  });

  // ------------------------------------------------------------------ the toolbox
  function setExplode(v) { st.explodeT = Math.max(0, Math.min(1, v)); needs = true; }
  function measureTap(pt) {
    if (st.measure.length && st.measure[st.measure.length - 1].b === undefined) { /* second point of an open measurement */ }
    let cur = st.measure[st.measure.length - 1];
    if (!cur || cur.b) { st.measure.forEach(m => { m.marks.forEach(o => scene.remove(o)); m.d && m.d.remove(); }); st.measure = []; cur = { a: pt.clone(), marks: [mark(pt)] }; st.measure.push(cur); toast('First point set. Tap the second.'); return; }
    cur.b = pt.clone(); cur.marks.push(mark(pt)); const geo = new THREE.BufferGeometry().setFromPoints([cur.a, cur.b]); geos.push(geo);
    const ln = new THREE.Line(geo, new THREE.LineBasicMaterial({ color: 0xffd84a })); scene.add(ln); cur.marks.push(ln); cur.line = ln;
    const d = cur.a.distanceTo(cur.b), unit = spec.unit || ''; cur.d = el('div', 'hmeas', d.toFixed(d < 10 ? 2 : 1) + ' ' + unit); over.append(cur.d); toast('Distance ' + cur.d.textContent); needs = true;
  }
  function mark(p) { const m = new THREE.Mesh(new THREE.SphereGeometry(frameR * 0.015, 8, 6), new THREE.MeshBasicMaterial({ color: 0xffd84a })); m.position.copy(p); scene.add(m); geos.push(m.geometry); mats.push(m.material); return m; }
  function noteTap(hit) {
    const e = parts.get(hit.object.userData.id), local = e.grp.worldToLocal(hit.point.clone());
    const box = el('div', 'hnotebox'), inp = el('input'); inp.placeholder = 'Note for this spot'; inp.maxLength = 120; const ok = el('button', 'tiny box', 'PIN'), no = el('button', 'tiny box', 'CANCEL'); box.append(inp, ok, no); stage.append(box);
    const c = project(hit.point); box.style.left = Math.max(6, Math.min(stage.clientWidth - 230, c.x)) + 'px'; box.style.top = Math.max(6, c.y - 40) + 'px'; inp.focus();
    const done = save => { if (save && inp.value.trim()) { const n = { pos: local.toArray().map(v => +v.toFixed(3)), text: inp.value.trim(), part: e.p.id }; st.notes.push(n); addPinEl(n); api.send({ t: 'holo_notes', title: spec.title, notes: st.notes }); } box.remove(); setMode(null); };
    ok.onclick = () => done(true); no.onclick = () => done(false); inp.onkeydown = ev => { ev.stopPropagation(); if (ev.key === 'Enter') done(true); if (ev.key === 'Escape') done(false); };
  }
  function addPinEl(n) { const d = el('div', 'hpin'); d.title = n.text; const t = el('span', null, n.text); d.append(t); over.append(d); pins.push({ n, d, tick: 0, occ: false }); needs = true; }
  function setMode(m) { st.mode = st.mode === m ? null : m; host.classList.toggle('picking', !!st.mode); cv.style.cursor = st.mode ? 'crosshair' : ''; bar.querySelectorAll('[data-mode]').forEach(b => b.classList.toggle('on', b.dataset.mode === st.mode)); if (st.mode === 'measure') toast('Tap two points to measure'); if (st.mode === 'note') toast('Tap a spot to pin a note'); }
  function setCut(axis, v) {                                                    // a clipping plane in the MODEL's space (the camera orbits, the model stays), so it turns with the model; the part on the + side is kept
    if (!axis) { plane.constant = 1e6; st.cut = null; needs = true; return; }
    const idx = axis === 'x' ? 0 : axis === 'y' ? 1 : 2, n = new THREE.Vector3(); n.setComponent(idx, 1);
    plane.normal.copy(n); plane.constant = -(st.center.getComponent(idx) + v * cutRange); st.cut = { axis, v }; needs = true;
  }
  function toast(t) { api.toast(t); }

  function buildBar() {
    const mk = (txt, fn, mode) => { const b = el('button', 'tiny box hb', txt); if (mode) b.dataset.mode = mode; b.onclick = fn; bar.append(b); return b; };
    mk('RESET', () => { st.sel = null; select(null); st.hidden.clear(); style(); controls.target.copy(st.home.target); cam.position.set(st.home.target.x + frameR * 1.3, st.home.target.y + frameR * 0.9, st.home.target.z + frameR * 1.9); controls.update(); needs = true; });
    const ex = mk('EXPLODE', () => { setExplode(st.explodeT > 0.5 ? 0 : 1); slider.value = st.explodeT * 100; });
    const slider = el('input', 'hslide'); slider.type = 'range'; slider.min = 0; slider.max = 100; slider.value = 0; slider.setAttribute('aria-label', 'explode'); slider.oninput = () => setExplode(slider.value / 100); bar.append(slider);
    const wf = mk('WIREFRAME', () => { st.wire = !st.wire; wf.textContent = st.wire ? 'SOLID' : 'WIREFRAME'; style(); });
    const lb = mk('LABELS', () => { st.labels = !st.labels; lb.classList.toggle('off', !st.labels); needs = true; });
    const sp = mk('SPIN', () => { controls.autoRotate = !controls.autoRotate; sp.classList.toggle('off', !controls.autoRotate); needs = true; });
    const cutb = mk('CUT', () => { const on = !cutRow.style.display; cutRow.style.display = on ? 'none' : ''; if (on) setCut(null); else setCut(cutAxis, +cutS.value / 100); cutb.classList.toggle('on', !on); });
    const cutRow = el('span', 'hcut'); cutRow.style.display = 'none'; let cutAxis = 'y'; const cutS = el('input', 'hslide'); cutS.type = 'range'; cutS.min = -100; cutS.max = 100; cutS.value = 0; cutS.oninput = () => setCut(cutAxis, cutS.value / 100);
    ['x', 'y', 'z'].forEach(a => { const b = el('button', 'tiny box hb', a.toUpperCase()); b.onclick = () => { cutAxis = a; setCut(a, cutS.value / 100); }; cutRow.append(b); }); cutRow.append(cutS); bar.append(cutRow);
    mk('MEASURE', () => setMode('measure'), 'measure'); mk('NOTE', () => setMode('note'), 'note');
    mk('SAVE', () => { api.send({ t: 'holo_save_ask' }); }); mk('PNG', () => exportPng()); mk('VIDEO', () => exportVideo());
    const nx = mk('TOUR', () => api.send({ t: 'holo_tour' }));
  }

  // ------------------------------------------------------------------ export (a picture, or a few seconds of video, into ~/jarvis/exports)
  function upload(blob, kind) { return fetch('/api/holo/export?kind=' + kind + '&title=' + encodeURIComponent(spec.title), { method: 'POST', body: blob }).then(r => r.json()).then(j => toast(j.ok ? 'Saved ' + j.name : (j.error || 'Could not save'))).catch(() => toast('Could not save')); }
  function exportPng() { renderer.render(scene, cam); cv.toBlob(b => b && b.size < 6e6 ? upload(b, 'png') : toast('Too big'), 'image/png'); }
  function exportVideo() {
    if (!window.MediaRecorder || !cv.captureStream) return toast('Video recording is not available here');
    const was = { s: controls.autoRotate, e: st.explodeT }; controls.autoRotate = true; setExplode(was.e || 1); toast('Recording 4 seconds…'); needs = true;
    const rec = new MediaRecorder(cv.captureStream(20), { mimeType: 'video/webm;codecs=vp8', videoBitsPerSecond: 700000 }), chunks = [];
    rec.ondataavailable = e => e.data.size && chunks.push(e.data); rec.onstop = () => { controls.autoRotate = was.s; setExplode(was.e); const b = new Blob(chunks, { type: 'video/webm' }); b.size < 8e6 ? upload(b, 'webm') : toast('Too big'); };
    rec.start(); setTimeout(() => rec.state !== 'inactive' && rec.stop(), 4000);
  }

  // ------------------------------------------------------------------ the loop: 30 fps at most, only when something changes, stopped when hidden or closed
  function loop(now) {
    if (!alive) return; if (!host.isConnected) { dispose(); return; }
    requestAnimationFrame(loop);
    if (document.hidden || !stage.offsetParent) return;
    const spinning = controls.autoRotate; if (now - last < 1000 / TARGET_FPS) return;
    if (glide) { const k = Math.min(1, (now - glide.t0) / 600), e = k * k * (3 - 2 * k); controls.target.lerpVectors(glide.from, glide.to, e); const dir = cam.position.clone().sub(controls.target).normalize(); cam.position.copy(controls.target).addScaledVector(dir, glide.d0 + (glide.d1 - glide.d0) * e); if (k >= 1) glide = null; needs = true; }
    if (Math.abs(st.explode - st.explodeT) > 0.002) { st.explode += (st.explodeT - st.explode) * 0.18; for (const e of parts.values()) e.grp.position.copy(e.base).addScaledVector(e.ex, st.explode); needs = true; } else if (st.explode !== st.explodeT) { st.explode = st.explodeT; for (const e of parts.values()) e.grp.position.copy(e.base).addScaledVector(e.ex, st.explode); needs = true; }
    if (!(spinning || needs || controls.enableDamping)) return;
    last = now; const moved = controls.update(); if (!spinning && !needs && !moved) return; needs = false;
    layout(); renderer.render(scene, cam);
    if (spinning) { fpsN++; if (now - fpsT0 > 4000) { const f = fpsN / ((now - fpsT0) / 1000); if (f < MIN_FPS && ++slow >= 2) { dispose(); ctl.flat = true; flat('This screen draws 3D too slowly (' + f.toFixed(0) + ' frames a second)'); return; } if (f >= MIN_FPS) slow = 0; fpsN = 0; fpsT0 = now; } } else { fpsN = 0; fpsT0 = now; }
  }
  function dispose() {
    if (!alive) return; alive = false; try { ro.disconnect(); controls.dispose(); geos.forEach(g => g.dispose()); mats.forEach(m => m.dispose()); renderer.dispose(); renderer.forceContextLoss(); } catch (_) {}
  }
  ctl.dispose = dispose;

  // ------------------------------------------------------------------ voice and tour control from the server
  ctl.apply = (op, id) => {
    switch (op) {
      case 'select': select(id); break; case 'hide': st.hidden.add(id); if (st.sel === id) select(null); style(); break;
      case 'isolate': order.forEach(o => o === id ? st.hidden.delete(o) : st.hidden.add(o)); select(id); style(); break;
      case 'all': st.hidden.clear(); select(null); style(); break;
      case 'next': case 'prev': { const vis = order.filter(o => !st.hidden.has(o)); const i = vis.indexOf(st.sel); select(vis[(i + (op === 'next' ? 1 : -1) + vis.length) % vis.length] || vis[0]); break; }
      case 'explode': setExplode(1); break; case 'implode': setExplode(0); break;
      case 'spin_on': controls.autoRotate = true; needs = true; break; case 'spin_off': controls.autoRotate = false; break;
      case 'wire': st.wire = true; style(); break; case 'solid': st.wire = false; style(); break;
      case 'reset': bar.firstChild.click(); break;
    }
  };
  ctl.screenOf = id => { const c = project(center(id)), r = cv.getBoundingClientRect(); return { x: r.left + c.x, y: r.top + c.y }; };           // for tests: where a part is on the screen
  ctl.pick = (x, y) => { const r = cv.getBoundingClientRect(); ray.setFromCamera(new THREE.Vector2(((x - r.left) / r.width) * 2 - 1, -((y - r.top) / r.height) * 2 + 1), cam); const h = ray.intersectObjects([...parts.values()].filter(p => p.grp.visible).map(p => p.mesh), false)[0]; return h ? h.object.userData.id : null; };
  ctl.stats = () => ({ tris: Math.round(tris), spin: controls.autoRotate, plane: [plane.normal.x, plane.normal.y, plane.normal.z, plane.constant], cam: cam.position.toArray(), measure: st.measure.filter(m => m.b).length, notes: st.notes.length, explode: st.explodeT, wire: st.wire, sel: st.sel, hidden: [...st.hidden], renderCalls: renderer.info.render.calls, geometries: renderer.info.memory.geometries });
  ctl.parts = () => order.map(i => parts.get(i).p);
  ctl.selected = () => st.sel;

  // ------------------------------------------------------------------ the flat diagram (no WebGL, or too slow): the model seen from the front, labelled
  function flat(reason) {
    host.textContent = ''; host.classList.add('holo', 'flat');
    const ns = 'http://www.w3.org/2000/svg', s = document.createElementNS(ns, 'svg'); s.setAttribute('class', 'hflat');
    const ps = (spec && spec.parts) || [], xs = ps.flatMap(p => [p.pos[0] - p.size[0] / 2, p.pos[0] + p.size[0] / 2]), ys = ps.flatMap(p => [p.pos[1] - p.size[1] / 2, p.pos[1] + p.size[1] / 2]);
    const x0 = Math.min(...xs, 0), x1 = Math.max(...xs, 1), y0 = Math.min(...ys, 0), y1 = Math.max(...ys, 1), W = 560, H = 380, k = Math.min((W - 160) / (x1 - x0 || 1), (H - 40) / (y1 - y0 || 1)), ox = 80 - x0 * k, oy = H - 20 + y0 * k;
    s.setAttribute('viewBox', `0 0 ${W} ${H}`);
    ps.forEach((p, i) => { const cx = ox + p.pos[0] * k, cy = oy - p.pos[1] * k, w = Math.max(6, p.size[0] * k), h = Math.max(6, (p.shape === 'sphere' ? p.size[1] : p.size[1]) * k);
      const sh = document.createElementNS(ns, p.shape === 'sphere' || p.shape === 'torus' || p.shape === 'ring' ? 'ellipse' : 'rect');
      if (sh.tagName === 'ellipse') { sh.setAttribute('cx', cx); sh.setAttribute('cy', cy); sh.setAttribute('rx', w / 2); sh.setAttribute('ry', h / 2); } else { sh.setAttribute('x', cx - w / 2); sh.setAttribute('y', cy - h / 2); sh.setAttribute('width', w); sh.setAttribute('height', h); }
      sh.setAttribute('class', 'fpart'); sh.style.stroke = '#' + (PALETTE[p.color] || PALETTE.cyan).toString(16).padStart(6, '0'); s.append(sh);
      const lx = i % 2 ? W - 10 : 10, ly = 20 + (i * 17) % (H - 30), ln = document.createElementNS(ns, 'line'); ln.setAttribute('x1', cx); ln.setAttribute('y1', cy); ln.setAttribute('x2', lx + (i % 2 ? -4 : 4)); ln.setAttribute('y2', ly); ln.setAttribute('class', 'fline'); s.append(ln);
      const t = document.createElementNS(ns, 'text'); t.setAttribute('x', lx); t.setAttribute('y', ly + 3); t.setAttribute('text-anchor', i % 2 ? 'end' : 'start'); t.setAttribute('class', 'flab'); t.textContent = p.label.toUpperCase(); s.append(t); });
    const note = el('div', 'hcredit', (reason ? reason + '. ' : '') + 'Showing a flat labelled diagram instead.'), cr = el('div', 'hcredit', (card.source && card.source.line) || '');
    host.append(s, note, cr);
  }
  if (card.model) loadModel(); else { build(spec.parts); finish(); }                     // start only now: every variable above is initialised
  return ctl;
}
