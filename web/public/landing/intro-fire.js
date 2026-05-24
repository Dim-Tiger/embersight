/* ============================================================
   EmberSight intro — red embers → multi-color logo
   Red sparks streak in from every direction with contrails,
   then transition to true brand logo colors at impact.
   ============================================================ */
(function () {
  const canvas = document.getElementById('introCanvas');
  const skip   = document.getElementById('skipIntro');
  const ctx    = canvas.getContext('2d', { alpha: false });

  let W = 0, H = 0;
  const DPR = Math.min(window.devicePixelRatio || 1, 2);
  function resize() {
    W = window.innerWidth;
    H = window.innerHeight;
    canvas.width  = W * DPR;
    canvas.height = H * DPR;
    canvas.style.width  = W + 'px';
    canvas.style.height = H + 'px';
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }
  resize();
  window.addEventListener('resize', resize);

  // Starting ember palette
  const RED_CORE = [220, 38, 38];
  const RED_HOT  = [255, 90, 60];
  const RED_DEEP = [160, 18, 18];

  const CONVERGE_END   = 900;   // particles arrive at logo
  const HOLD_END       = 1400;  // logo holds before reveal
  const FADE_OUT_START = 1360;
  const TRAIL_LEN      = 8;     // history points per particle

  let particles = [];
  let started = false;
  let revealed = false;
  let startTime = 0;

  function startAnimation() {
    if (started) return;
    started = true;
    window.__embersight.revealSite();
    startTime = performance.now();
    requestAnimationFrame(frame);
  }

  const img = new Image();
  img.onload = function () {
    const SAMPLE = 260;
    const off  = document.createElement('canvas');
    off.width  = SAMPLE;
    off.height = SAMPLE;
    const octx = off.getContext('2d');
    octx.drawImage(img, 0, 0, SAMPLE, SAMPLE);
    const data = octx.getImageData(0, 0, SAMPLE, SAMPLE).data;

    const cx = W / 2;
    const cy = H * 0.5;
    const logoSize = Math.min(W, H) * 0.46;
    const scale = logoSize / SAMPLE;

    const stride = 2;
    for (let y = 0; y < SAMPLE; y += stride) {
      for (let x = 0; x < SAMPLE; x += stride) {
        const i = (y * SAMPLE + x) * 4;
        const a = data[i + 3];
        if (a < 80) continue;
        const lr = data[i], lg = data[i + 1], lb = data[i + 2];
        if (lr > 248 && lg > 248 && lb > 248) continue;

        const tx = cx + (x - SAMPLE / 2) * scale;
        const ty = cy + (y - SAMPLE / 2) * scale;

        // origin: random angle, far off-screen
        const ang  = Math.random() * Math.PI * 2;
        const dist = Math.max(W, H) * (0.75 + Math.random() * 0.7);
        const ox = cx + Math.cos(ang) * dist;
        const oy = cy + Math.sin(ang) * dist;

        const mix = Math.random();
        const ember = mix < 0.5 ? RED_CORE : mix < 0.85 ? RED_HOT : RED_DEEP;

        // trail history (initialized to origin)
        const trail = new Float32Array(TRAIL_LEN * 2);
        for (let k = 0; k < TRAIL_LEN; k++) {
          trail[k * 2]     = ox;
          trail[k * 2 + 1] = oy;
        }

        particles.push({
          ox, oy,
          x: ox, y: oy,
          tx, ty,
          // ember color (start)
          er: ember[0], eg: ember[1], eb: ember[2],
          // logo color (end)
          lr, lg, lb,
          size: 1.7 + Math.random() * 1.5,
          delay: Math.random() * 0.20,
          trail,
        });
      }
    }
    startAnimation();
  };
  img.onerror = startAnimation;
  img.src = 'assets/logo.png';
  setTimeout(startAnimation, 600);

  function easeOutQuart(t) { return 1 - Math.pow(1 - t, 4); }
  function clamp01(v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
  function lerp(a, b, t) { return a + (b - a) * t; }

  function frame() {
    const elapsed = performance.now() - startTime;
    const T = elapsed / CONVERGE_END;

    ctx.fillStyle = '#fbf6ee';
    ctx.fillRect(0, 0, W, H);

    // ----- pass 1: contrails (additive, multi-segment with alpha falloff) -----
    ctx.globalCompositeOperation = 'lighter';
    ctx.lineCap = 'round';
    for (const p of particles) {
      const local = clamp01((T - p.delay) / (1 - p.delay));
      const e = easeOutQuart(local);
      const x = p.ox + (p.tx - p.ox) * e;
      const y = p.oy + (p.ty - p.oy) * e;

      // shift trail history
      for (let k = TRAIL_LEN - 1; k > 0; k--) {
        p.trail[k * 2]     = p.trail[(k - 1) * 2];
        p.trail[k * 2 + 1] = p.trail[(k - 1) * 2 + 1];
      }
      p.trail[0] = x;
      p.trail[1] = y;
      p.x = x; p.y = y;

      // color: red during flight, fade to logo color near impact
      const colorMix = clamp01((local - 0.75) / 0.25); // last 25% of flight
      const cr = Math.round(lerp(p.er, p.lr, colorMix));
      const cg = Math.round(lerp(p.eg, p.lg, colorMix));
      const cb = Math.round(lerp(p.eb, p.lb, colorMix));

      // contrail intensity — strong mid-flight, fades after arrival
      const flightI = local < 1 ? 0.6 + 0.4 * local : Math.max(0, 1 - (elapsed - CONVERGE_END) / 400);
      if (flightI > 0.02) {
        for (let k = 0; k < TRAIL_LEN - 1; k++) {
          const x1 = p.trail[k * 2],     y1 = p.trail[k * 2 + 1];
          const x2 = p.trail[(k + 1) * 2], y2 = p.trail[(k + 1) * 2 + 1];
          const segA = (1 - k / TRAIL_LEN) * 0.5 * flightI;
          if (segA < 0.01) continue;
          ctx.strokeStyle = `rgba(${cr},${cg},${cb},${segA})`;
          ctx.lineWidth = p.size * (1.1 - k * 0.09);
          ctx.beginPath();
          ctx.moveTo(x1, y1);
          ctx.lineTo(x2, y2);
          ctx.stroke();
        }
      }

      // glow halo at head
      const gr = p.size * 4.2;
      const glow = ctx.createRadialGradient(x, y, 0, x, y, gr);
      glow.addColorStop(0, `rgba(${cr},${cg},${cb},${0.6 * (0.4 + e * 0.6)})`);
      glow.addColorStop(1, `rgba(${cr},${cg},${cb},0)`);
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(x, y, gr, 0, Math.PI * 2);
      ctx.fill();
    }

    // ----- pass 2: crisp cores (non-additive so multi-color logo reads) -----
    ctx.globalCompositeOperation = 'source-over';
    for (const p of particles) {
      const local = clamp01((T - p.delay) / (1 - p.delay));
      const colorMix = clamp01((local - 0.75) / 0.25);
      const cr = Math.round(lerp(p.er, p.lr, colorMix));
      const cg = Math.round(lerp(p.eg, p.lg, colorMix));
      const cb = Math.round(lerp(p.eb, p.lb, colorMix));
      const a = 0.55 + 0.45 * local;
      ctx.fillStyle = `rgba(${cr},${cg},${cb},${a})`;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
      ctx.fill();
    }

    if (!revealed && elapsed >= FADE_OUT_START) {
      revealed = true;
      window.__embersight.hideIntro();
    }
    if (elapsed < HOLD_END + 200) requestAnimationFrame(frame);
  }

  function skipNow() {
    if (revealed) return;
    revealed = true;
    window.__embersight.revealSite();
    window.__embersight.hideIntro();
  }
  skip.addEventListener('click', skipNow);
  window.addEventListener('keydown', e => { if (e.key === 'Escape' || e.key === ' ') skipNow(); });
  setTimeout(() => { if (!revealed) skipNow(); }, 2500);
})();
