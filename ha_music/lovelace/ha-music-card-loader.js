// Stable Home Assistant resource loader for HA Music.
// The card itself opens the add-on through Home Assistant's Supervisor Ingress.
// A changing query string prevents browser caches from keeping an old card build.
import("/local/ha-music-card.js?t=" + Date.now()).catch((err) => {
  console.error("[HA Music] Lovelace card could not be loaded", err);
});
