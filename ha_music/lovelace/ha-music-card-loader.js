// Reload HA Music card without stale browser assets.
import("/local/ha-music-card.js?t=" + Date.now()).catch((err) => {
  console.error("[HA Music] Lovelace card could not be loaded", err);
});
