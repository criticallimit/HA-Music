// Compatible with both JavaScript and JavaScript Module resource types.
// Load the actual HA Music card as an ES module and report registration errors.
import("/local/ha-music-card.js?t=" + Date.now())
  .then(() => {
    if (!customElements.get("ha-music-card") ||
        !(window.customCards || []).some(card => card.type === "ha-music-card")) {
      throw new Error("Card module loaded but HA Music registration is missing");
    }
    console.info("[HA Music] Lovelace card registered");
  })
  .catch(err => console.error("[HA Music] Lovelace card could not be loaded", err));
