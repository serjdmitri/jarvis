/* Start-up and wake animation (about 1.2 s): the screen swipes away to the left (250 ms ease-in), a thin blue bar scans left to right (400 ms) clearing to the dark grid, the orb fades in and grows
   (350 ms ease-out), then the notch frame, corner brackets, tick marks, ring arcs and the dock icons pop in one by one (50 ms apart). Only transform / translate / scale / opacity are animated
   (compositor friendly). Lite mode or reduced motion: a plain fade. Setting "start-up animation" turns it off. */
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  let playing = false;
  const anim = (el, kf, opt) => { try { return el.animate(kf, { fill: 'both', ...opt }).finished.catch(() => {}); } catch (_) { return Promise.resolve(); } };
  const wait = ms => new Promise(r => setTimeout(r, ms));
  function svgEl(tag, attrs, parent) { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); parent.appendChild(e); return e; }
  function buildPieces(root) {                                                       // the HUD pieces as separate elements, only for the length of the animation
    const w = innerWidth, h = innerHeight, svg = svgEl('svg', { width: w, height: h, viewBox: `0 0 ${w} ${h}`, style: 'position:absolute;inset:0' }, root), P = [];
    const st = { fill: 'none', stroke: 'rgba(210,228,255,.9)', 'stroke-width': 1.2, 'vector-effect': 'non-scaling-stroke' };
    const grp = () => { const g = svgEl('g', {}, svg); g.style.opacity = 0; g.style.transformOrigin = '50% 50%'; P.push(g); return g; };
    { const g = grp(); svgEl('path', { ...st, d: [[.36, .055], [.405, .055], [.425, .08], [.575, .08], [.595, .055], [.64, .055]].map(([a, b], i) => (i ? 'L' : 'M') + a * w + ' ' + b * h).join(' ') }, g); }     // the notch
    for (const [x, y, sx, sy] of [[14, 14, 1, 1], [w - 14, 14, -1, 1], [14, h - 14, 1, -1], [w - 14, h - 14, -1, -1]]) { const g = grp(); svgEl('path', { ...st, d: `M${x} ${y + sy * 16} L${x} ${y} L${x + sx * 16} ${y}` }, g); }   // corner brackets
    { const g = grp(); let d = ''; const ty = h * .085; for (let i = 24; i < w - 12; i += 28) { d += `M${i} ${ty}l0 ${(i / 28 | 0) % 5 ? 3 : 6}`; } svgEl('path', { ...st, 'stroke-width': 1, d }, g); }                       // tick marks
    { const g = grp(), r = Math.min(w, h) * .2, cx = (typeof ring !== 'undefined' ? ring.x : w / 2) || w / 2, cy = (typeof ring !== 'undefined' ? ring.y : h / 2) || h / 2;
      for (const [a0, a1] of [[200, 290], [20, 110]]) { const p = a => [cx + r * Math.cos(a * Math.PI / 180), cy + r * Math.sin(a * Math.PI / 180)]; const [x0, y0] = p(a0), [x1, y1] = p(a1); svgEl('path', { ...st, 'stroke-width': 2, d: `M${x0} ${y0} A${r} ${r} 0 0 1 ${x1} ${y1}` }, g); } }       // ring arcs
    return P;
  }
  async function play(kind) {
    if (playing || document.documentElement.classList.contains('dock')) return;
    const pref = (typeof PREFS !== 'undefined' && PREFS.boot_anim !== false);
    const html = document.documentElement, simple = !pref || html.classList.contains('lite') || matchMedia('(prefers-reduced-motion: reduce)').matches;
    playing = true;
    try {
      if (simple) { if (pref) await anim(document.body, [{ opacity: 0 }, { opacity: 1 }], { duration: 220, easing: 'ease-out' }); return; }
      const kids = [...document.body.children].filter(e => e.id !== 'boot' && !/^(SCRIPT|STYLE)$/.test(e.tagName));
      let boot = document.getElementById('boot'); if (!boot) { boot = document.createElement('div'); boot.id = 'boot'; document.body.appendChild(boot); }
      boot.innerHTML = '<div id="bootgrid"></div><div id="bootbar"></div><div id="bootpieces"></div>'; boot.style.display = 'block';
      const grid = boot.querySelector('#bootgrid'), bar = boot.querySelector('#bootbar'), pieces = buildPieces(boot.querySelector('#bootpieces'));
      // 1) the current screen swipes away to the left
      await Promise.all(kids.map(e => anim(e, [{ translate: '0 0' }, { translate: '-100vw 0' }], { duration: 250, easing: 'ease-in' })));
      kids.forEach(e => { e.style.visibility = 'hidden'; e.getAnimations().forEach(a => a.cancel()); });                  // (nothing is shown until the orb comes in)
      // 2) the blue bar scans left to right and clears to the dark grid
      anim(grid, [{ transform: 'scaleX(0)' }, { transform: 'scaleX(1)' }], { duration: 400, easing: 'cubic-bezier(.5,0,.3,1)' });
      await anim(bar, [{ transform: 'translateX(0)' }, { transform: `translateX(${innerWidth}px)` }], { duration: 400, easing: 'cubic-bezier(.5,0,.3,1)' });
      bar.style.opacity = 0;
      // 3) the orb fades in and grows, then the pieces pop in one by one
      const orb = kids.filter(e => ['fx', 'center', 'orbhit'].includes(e.id)); orb.forEach(e => { e.style.visibility = ''; });
      const dockIcons = [...document.querySelectorAll('#dock .ico')];
      const steps = [...pieces, ...dockIcons];
      kids.forEach(e => { if (!orb.includes(e)) e.style.visibility = ''; });
      dockIcons.forEach(e => { e.style.opacity = 0; });
      await Promise.all([...orb.map(e => anim(e, [{ opacity: 0, scale: '.6' }, { opacity: 1, scale: '1' }], { duration: 350, easing: 'cubic-bezier(.2,.8,.2,1)' })),      // the pieces pop in while the orb is still growing, one every 45 ms
        ...steps.map((e, i) => wait(100 + i * 45).then(() => e.ownerSVGElement ? anim(e, [{ opacity: 0, transform: 'scale(.7)' }, { opacity: 1, transform: 'scale(1)' }], { duration: 200, easing: 'ease-out' })
          : anim(e, [{ opacity: 0, scale: '.5' }, { opacity: 1, scale: '1' }], { duration: 200, easing: 'ease-out' })))]);
      dockIcons.forEach(e => { e.style.opacity = ''; e.getAnimations().forEach(a => a.cancel()); });
      orb.forEach(e => e.getAnimations().forEach(a => a.cancel()));
      boot.style.display = 'none'; boot.innerHTML = '';
    } catch (_) {
    } finally {
      [...document.body.children].forEach(e => { if (e.id !== 'boot') { e.style.visibility = ''; } });
      const boot = document.getElementById('boot'); if (boot) { boot.style.display = 'none'; boot.innerHTML = ''; }
      document.querySelectorAll('#dock .ico').forEach(e => { e.style.opacity = ''; });
      playing = false;
    }
  }
  window.playBoot = play;
  if (!new URLSearchParams(location.search).has('noboot')) addEventListener('load', () => setTimeout(() => play('start'), 300));
  window.__bootBusy = () => playing;
})();
