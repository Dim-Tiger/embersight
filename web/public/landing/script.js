(function () {
  const intro = document.getElementById('intro');
  const video = document.getElementById('introVideo');
  const skip = document.getElementById('skipIntro');
  const site = document.getElementById('site');

  // reveal site UNDER the intro so the fade-out feels seamless
  site.setAttribute('aria-hidden', 'false');
  site.classList.add('is-visible');

  const TRIM_START = parseFloat(video.dataset.trimStart || '0');
  const FADE_BEFORE_END = 0.35;       // start fading this many seconds before video ends
  const FALLBACK_MS = 12000;          // hard cutoff

  let revealed = false;
  function reveal() {
    if (revealed) return;
    revealed = true;
    intro.classList.add('is-hidden');
    setTimeout(() => intro.remove(), 800);
  }

  function startPlayback() {
    try { video.currentTime = TRIM_START; } catch (_) {}
    const p = video.play();
    if (p && typeof p.catch === 'function') p.catch(reveal);
  }

  if (video.readyState >= 1) startPlayback();
  else video.addEventListener('loadedmetadata', startPlayback, { once: true });

  // start fade before the video ends so there's no visible cut
  video.addEventListener('timeupdate', () => {
    if (!video.duration) return;
    if (video.currentTime >= video.duration - FADE_BEFORE_END) {
      intro.classList.add('is-hidden');
    }
  });
  video.addEventListener('ended', reveal);
  video.addEventListener('error', reveal);
  setTimeout(reveal, FALLBACK_MS);

  skip.addEventListener('click', reveal);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' || e.key === ' ') reveal();
  });
})();
