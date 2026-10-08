// Register the HA Music custom card before this resource module finishes loading.
// The card picker requires window.customCards and customElements to be populated.
try {
  await import("/local/ha-music-card.js?t=" + Date.now());
  if (!customElements.get("ha-music-card") ||
      !(window.customCards || []).some(card => card.type === "ha-music-card")) {
    throw new Error("HA Music card module loaded but card registration is missing");
  }
  console.info("[HA Music] Lovelace card registered");
} catch (err) {
  console.error("[HA Music] Lovelace card could not be loaded", err);
}
