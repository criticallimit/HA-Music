"use strict";
// Dashboard card inherits theme variables from Home Assistant, as in TV Guide.
if (window.parent !== window) {
  const applyTheme = (data) => {
    if (!data || data.type !== "ha-music-theme" || !data.vars) return;
    for (const [name, value] of Object.entries(data.vars)) {
      if (name.startsWith("--") && typeof value === "string")
        document.documentElement.style.setProperty(name, value);
    }
    document.documentElement.dataset.haTheme = data.darkMode ? "dark" : "light";
    document.documentElement.classList.add("ha-dashboard-card");
    window.parent.postMessage({type:"ha-music-theme-ready"}, window.location.origin);
  };
  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin || event.source !== window.parent) return;
    applyTheme(event.data);
  });
  window.parent.postMessage({type:"ha-music-theme-request"}, window.location.origin);
}

// Report actual visible content height to the Lovelace host.
// ResizeObserver reacts when the radio powers on/off or speaker controls change.
if (new URLSearchParams(window.location.search).get("ha_music_card") === "1" && window.parent !== window) {
  document.documentElement.classList.add("ha-dashboard-fit");
  let pendingHeightFrame = 0;
  let lastReportedHeight = 0;
  const setAvailableHeight = height => {
    if (Number.isFinite(height) && height > 0 && height <= 10000)
      document.documentElement.style.setProperty("--ha-card-available-height", height + "px");
  };
  const fitAppleLibrary = () => {
    const library = document.getElementById("apple-library");
    const right = document.querySelector(".dashboard-right");
    const artwork = document.querySelector(".now");
    const stations = document.getElementById("station-list");
    if (!library || library.hidden || !right || right.hidden || !artwork || artwork.hidden || !stations) return;
    const gap = parseFloat(getComputedStyle(right).rowGap) || 0;
    const controls = [...right.children].filter(child => child !== library && child !== stations && !child.hidden &&
      getComputedStyle(child).display !== "none" && !["absolute", "fixed"].includes(getComputedStyle(child).position));
    const controlsHeight = controls.reduce((height, child) => height + child.getBoundingClientRect().height, 0) + gap * controls.length;
    // Measure the same radio footer at this width without showing it, moving
    // live controls, duplicating listeners, or changing the active view.
    const probe = stations.cloneNode(true);
    probe.removeAttribute("id");
    probe.querySelectorAll("[id]").forEach(child => child.removeAttribute("id"));
    probe.hidden = false;
    probe.inert = true;
    probe.setAttribute("aria-hidden", "true");
    Object.assign(probe.style, {position:"fixed", visibility:"hidden", pointerEvents:"none", left:"0", top:"0",
      width:right.getBoundingClientRect().width + "px", margin:"0"});
    document.body.appendChild(probe);
    const stationHeight = Math.max(72, probe.getBoundingClientRect().height);
    probe.remove();
    const art = artwork.getBoundingClientRect(), panel = right.getBoundingClientRect();
    const sameRow = Math.abs(art.top - panel.top) < 1;
    const referenceHeight = sameRow ? Math.max(art.height, controlsHeight + stationHeight) : controlsHeight + stationHeight;
    const budget = Math.max(72, Math.floor(referenceHeight - controlsHeight));
    library.style.setProperty("--ha-library-height", budget + "px");
    library.classList.toggle("ha-library-compact", budget < 200);
    for (const grid of library.querySelectorAll(".stations")) {
      grid.style.setProperty("--ha-library-tile-height", Math.max(32, Math.min(120, grid.clientHeight)) + "px");
    }
  };
  const reportHeight = () => {
    pendingHeightFrame = 0;
    setAvailableHeight(window.frameElement?.parentElement?.clientHeight);
    fitAppleLibrary();
    const shell = document.querySelector(".shell");
    if (!shell) return;
    const height = Math.ceil(shell.getBoundingClientRect().height);
    if (!Number.isFinite(height) || height < 1 || height === lastReportedHeight) return;
    lastReportedHeight = height;
    window.parent.postMessage({type:"ha-music-content-height", height}, window.location.origin);
  };
  const scheduleHeight = () => {
    if (!pendingHeightFrame) pendingHeightFrame = requestAnimationFrame(reportHeight);
  };
  if (window.ResizeObserver) {
    const observer = new ResizeObserver(scheduleHeight);
    observer.observe(document.querySelector(".shell"));
  }
  if (window.MutationObserver) {
    // Switching views and importing favorites can leave the shell's size
    // unchanged. Observe those changes too; sizing styles are deliberately
    // excluded to prevent a measurement loop.
    new MutationObserver(scheduleHeight).observe(document.querySelector(".shell"),
      {subtree:true, childList:true, attributes:true, attributeFilter:["hidden"]});
  }
  window.addEventListener("load", scheduleHeight);
  window.addEventListener("resize", scheduleHeight);
  window.addEventListener("message", event => {
    if (event.source !== window.parent || event.origin !== window.location.origin || event.data?.type !== "ha-music-available-height") return;
    setAvailableHeight(Number(event.data.height));
    scheduleHeight();
  });
  scheduleHeight();
}

const $ = id => document.getElementById(id);
const isDashboardCard = new URLSearchParams(window.location.search).get("ha_music_card") === "1";
let showDashboardSetup = false;
let dashboardCardReported = false;
function applyCardSetupVisibility(installed) {
  $("settings-open").hidden = !showDashboardSetup && (isDashboardCard || installed);
}
async function reportDashboardCardLoaded() {
  if (!isDashboardCard || dashboardCardReported) return;
  try {
    await api("dashboard_card_installed", {});
    dashboardCardReported = true;
    applyCardSetupVisibility(true);
  } catch (err) {
    console.warn("[HA Music] Could not record dashboard card installation", err);
  }
}

let radioReadyForViews = false;
let mediaPreparing = false;
let preferredView = "radio";
let countdownEndsAt = null;
let strictStandby = false;
let uiGeneration = 0;
let stateRequestRunning = false;
let playersRequestRunning = false;
let stationPending = false;
let viewPending = false;
let powerPending = false;
let volumeRequests = 0;
let volumeRevision = 0;
let masterRoomLevels = {};
const roomVolumeRows = new Map();
const requestedRoomVolumes = new Map();
const volumeEditing = new Map();
const ROOM_VOLUME_PREVIEW_MS = 60000;
let transportPending = false;
let transportEpoch = 0;
let groupTransport = null;
let trackTransport = null;
let transportIntent = {};
let transportError = "";
let transportPreview = null;
let masterTransportButton = null;
function shuffleIntent() { return transportIntent.shuffle ?? trackTransport?.shuffle; }
function groupPlaybackState() { return transportIntent.state ?? groupTransport?.state; }
function canControlGroup(command) {
  if (!groupTransport) return false;
  const support = groupTransport["supports_" + command];
  if (typeof support === "boolean") return groupTransport.available && support &&
    groupPlaybackState() === (command === "play" ? "paused" : "playing");
  return Boolean(groupTransport["can_" + command]);
}
function renderTrackTransport() {
  for (const [command,label] of [["previous","Vorheriger Titel"],["shuffle","Shuffle"],["next","Nächster Titel"]]) {
    const button = $("track-" + command);
    button.disabled = strictStandby || !radioReadyForViews || mediaPreparing || stationPending || transportPending || !trackTransport?.["can_" + command];
    const shuffle = command === "shuffle";
    const confirmed = shuffle && shuffleIntent() === true;
    if (shuffle) {
      const state = typeof shuffleIntent() === "boolean" ? (confirmed ? "on" : "off") : "unknown";
      button.setAttribute("data-shuffle-state", state);
      button.setAttribute("aria-pressed", String(confirmed));
      button.classList.toggle("active", confirmed);
    }
    const description = transportPending ? "Befehl wird gesendet …" :
      button.disabled ? label + " derzeit nicht verfügbar" :
      shuffle ? (confirmed ? "Zufällige Wiedergabe aktiv – auf Reihenfolge umschalten" : "Wiedergabe in Reihenfolge – Shuffle einschalten") : label;
    button.title = description;
    button.setAttribute("aria-label", description);
  }
}
async function controlTrack(command) {
  if (strictStandby || !radioReadyForViews || mediaPreparing || stationPending || transportPending || !trackTransport?.["can_" + command]) return;
  const body = {command, entity_id:trackTransport.entity_id};
  if (command === "shuffle") body.shuffle = !shuffleIntent();
  const generation = uiGeneration;
  const previousIntent = {...transportIntent};
  transportError = "";
  if (command === "shuffle") {
    transportPreview = {generation, previous:previousIntent};
    transportIntent.shuffle = body.shuffle;
  }
  transportPending = true;
  transportEpoch++;
  renderGroupTransport();
  for (const button of stationButtons.values()) button.disabled = true;
  renderAppleSelection(appleSelection);
  try {
    await api("track_transport", body);
    if (generation !== uiGeneration || !radioReadyForViews) return;
    if (command !== "shuffle") { trackTransport = null; groupTransport = null; }
  } catch (e) {
    if (generation === uiGeneration) {
      transportIntent = previousIntent;
      transportError = "Titelsteuerung fehlgeschlagen: " + e.message;
      $("playback-state").textContent = transportError;
      reportError(transportError);
    }
  } finally {
    if (transportPreview?.generation === generation) transportPreview = null;
    transportPending = false;
    renderGroupTransport();
    if (generation === uiGeneration && radioReadyForViews) {
      await loadRadioState();
      await updateSong();
    }
  }
}
for (const command of ["previous","shuffle","next"])
  $("track-" + command).addEventListener("click", () => controlTrack(command));
function renderGroupTransport() {
  renderTrackTransport();
  if (!masterTransportButton) return;
  const pause = groupPlaybackState() === "playing";
  masterTransportButton.disabled = strictStandby || !radioReadyForViews || mediaPreparing || stationPending || transportPending || !canControlGroup(pause ? "pause" : "play");
  const label = transportPending ? "Gruppenbefehl wird gesendet …" :
    masterTransportButton.disabled ? "Gruppensteuerung derzeit nicht verfügbar" :
    pause ? "Gesamte Gruppe pausieren" : "Gesamte Gruppe fortsetzen";
  masterTransportButton.setAttribute("aria-label", label);
  masterTransportButton.title = label;
  masterTransportButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round">' +
    (pause ? '<path d="M8 5v14M16 5v14" stroke-linecap="round"/>' : '<path d="m8 5 11 7-11 7Z"/>') + '</svg>';
}
async function controlGroup(command) {
  if (strictStandby || !radioReadyForViews || mediaPreparing || stationPending || transportPending || !canControlGroup(command)) return;
  const previousIntent = {...transportIntent};
  const generation = uiGeneration;
  transportPreview = {generation, previous:previousIntent};
  transportError = "";
  transportIntent.state = command === "play" ? "playing" : "paused";
  transportPending = true;
  transportEpoch++;
  renderGroupTransport();
  try {
    await api("group_transport", {command});
    if (generation !== uiGeneration || !radioReadyForViews) return;
  } catch (e) {
    if (generation === uiGeneration) {
      transportIntent = previousIntent;
      transportError = "Gruppensteuerung fehlgeschlagen: " + e.message;
      $("playback-state").textContent = transportError;
      reportError(transportError);
    }
  } finally {
    if (transportPreview?.generation === generation) transportPreview = null;
    transportPending = false;
    renderGroupTransport();
    if (generation === uiGeneration && radioReadyForViews) updateSong();
  }
}
let radioEventSource = null;
function show(page) {
  const radio = page === "radio";
  // Move the live elements rather than clone them: both views share controls,
  // current artwork, listeners and the existing polling/event subscriptions.
  const dashboard = $(radio ? "radio-page" : "apple-page");
  for (const selector of [".now", ".dashboard-right"]) {
    const panel = document.querySelector(selector);
    if (panel.parentElement !== dashboard) dashboard.appendChild(panel);
  }
  $("station-list").hidden = !radio;
  $("apple-library").hidden = radio;
  $("radio-page").hidden = !radio;
  $("apple-page").hidden = radio;
  $("radio-tab").classList.toggle("active", radio);
  $("apple-tab").classList.toggle("active", !radio);
}
async function selectView(page) {
  if (!radioReadyForViews || viewPending) return;
  viewPending = true;
  const generation = ++uiGeneration;
  try {
    await api("selected_view", {view:page});
    if (generation !== uiGeneration || !radioReadyForViews) return;
    preferredView = page;
    show(page);
  } catch (e) {
    if (generation === uiGeneration && radioReadyForViews) reportError("Ansicht konnte nicht gespeichert werden: " + e.message);
  }
  finally { viewPending = false; }
}
$("radio-tab").addEventListener("click", () => selectView("radio"));
$("apple-tab").addEventListener("click", () => selectView("apple"));
function renderCountdown() {
  if (radioReadyForViews || countdownEndsAt === null) return;
  const seconds = Math.max(0, Math.ceil((countdownEndsAt - Date.now()) / 1000));
  $("radio-standby-text").textContent = seconds > 0 ? "Radio startet … " + seconds + " s" : "Startbefehle werden abgeschlossen …";
}
setInterval(renderCountdown, 1000);
// Lovelace dashboard setup is independent of radio power/readiness.
const settingsDialog = $("dashboard-settings");
$("settings-open").addEventListener("click", () => settingsDialog.showModal());
$("settings-close").addEventListener("click", () => settingsDialog.close());
settingsDialog.addEventListener("click", (event) => {
  if (event.target === settingsDialog) settingsDialog.close();
});
async function copySettingsText(value) {
  try {
    await navigator.clipboard.writeText(value);
    $("settings-feedback").textContent = "In Zwischenablage kopiert.";
  } catch (_) {
    $("settings-feedback").textContent = "Bitte den Text im Feld markieren und kopieren.";
  }
}
$("copy-resource").addEventListener("click", () => copySettingsText($("resource-url").value));
$("copy-card-yaml").addEventListener("click", () => copySettingsText($("card-yaml").value));
$("open-resources").addEventListener("click", () => {
  const url = new URL("/config/lovelace/resources", window.location.origin);
  const tab = window.open(url.href, "_blank", "noopener,noreferrer");
  if (!tab) {
    $("settings-feedback").textContent = "Home Assistant hat den neuen Tab blockiert. Einstellungen → Dashboards → Ressourcen öffnen.";
  }
});
const STATIONS = [["1live","1LIVE"],["wdr2","WDR 2"],["swr3","SWR3"],["sommerhits","Sommerhits"],["charts","Charts"],["80s","80er"],["90s","90er"]];
async function api(path, data) {
  const response = await fetch("api/" + path, {signal:AbortSignal.timeout(20000), ...(data ? {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)} : {})});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Anfrage fehlgeschlagen");
  return body;
}
// Logos are selected by station ID, never read from Alexa's delayed media state.
const STATION_LOGOS = {
  "1live": "1live.svg",
  "wdr2": "wdr2.svg",
  "swr3": "swr3.svg"
};
function updateStationLogo(station) {
  document.querySelector(".now").classList.toggle("radio-selected", Boolean(STATION_LOGOS[station]));
  const cover = $("current-cover");
  const logo = STATION_LOGOS[station];
  if (logo) {
    if (cover.getAttribute("src") !== logo) cover.src = logo;
    cover.alt = STATIONS.find(item => item[0] === station)?.[1] || station;
    cover.hidden = false;
  } else {
    cover.hidden = true;
    cover.removeAttribute("src");
  }
}
function setActiveStation(station) {
  for (const [id, button] of stationButtons) {
    const active = id === station;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
}
let selectedStation = "";
let activeApple = null;
let appleSelection = {items:[], available:false};
let appleItemsSignature = "";
const appleButtons = new Map();
let stationEpoch = 0;
let songRequestRunning = false;
const lastStationMetadata = new Map();
const METADATA_GRACE_MS = 90000;
function renderRadioMetadata(station, external) {
  if (station !== selectedStation) return;
  const line = external?.title && external?.artist
    ? external.artist + " – " + external.title : (external?.show || "");
  const ticker = $("now-ticker");
  const text = $("now-ticker-text");
  if (line) {
    if (text.textContent !== line) text.textContent = line;
    text.title = line;
    ticker.hidden = false;
    $("current-artist").textContent = "Aktueller Radiotext";
  } else {
    ticker.hidden = true;
    text.textContent = "";
    $("current-artist").textContent = "Aktueller Radiotext";
  }
}
async function updateSong() {
  if (strictStandby || !radioReadyForViews || songRequestRunning || transportPending) return;
  songRequestRunning = true;
  const station = selectedStation;
  const epoch = stationEpoch;
  const transportRequestEpoch = transportEpoch;
  const generation = uiGeneration;
  try {
    const info = await api("playback-status");
    if (generation !== uiGeneration || !radioReadyForViews || stationEpoch !== epoch || station !== selectedStation || transportRequestEpoch !== transportEpoch) return;
    if (!transportPending) {
      if (info.control_intent?.state === "playing" || info.control_intent?.state === "paused") transportIntent.state = info.control_intent.state;
      if (typeof info.control_intent?.shuffle === "boolean") transportIntent.shuffle = info.control_intent.shuffle;
      groupTransport = info.transport || null;
      trackTransport = info.track_transport || null;
      renderGroupTransport();
    }
    let details = info.details;
    if (activeApple && (Object.values(STATION_LOGOS).some(logo => details?.image === logo || details?.image === "/" + logo) || ["radio", "channel"].includes(details?.content_type))) details = null;
    const isRadio = !activeApple && ["wdr2","1live","swr3"].includes(station);
    if (isRadio && info.radio_metadata) applyRadioMetadata(info.radio_metadata);
    $("current-title").textContent = activeApple ? (details?.title || activeApple.name) : STATIONS.find(s => s[0] === station)?.[1] || details?.title || "Kein Sender ausgewählt";
    if (!isRadio) {
      const line = details?.artist && details?.title ? details.artist + " – " + details.title : "";
      $("now-ticker-text").textContent = line;
      $("now-ticker").hidden = !line;
      $("current-artist").textContent = line ? "Jetzt läuft" : "Aktuelle Programminformation nicht verfügbar";
    }
    // Fixed branding is exclusively for broadcast stations.
    // Amazon presets use the artwork supplied by Alexa.
    if (!isRadio) {
      const cover = $("current-cover");
      const image = details?.image;
      if (typeof image === "string" && ((image.startsWith("/") && !image.startsWith("//")) || image.startsWith("https://"))) {
        if (cover.getAttribute("src") !== image) cover.src = image;
        cover.alt = details?.album || details?.title || (activeApple ? "Apple Music" : "Amazon Music");
        cover.hidden = false;
      } else {
        cover.hidden = true;
        cover.removeAttribute("src");
      }
    }
    const unconfirmedVolumes = Object.keys(info.volume_confirmation || {});
    $("playback-state").textContent = transportError || info.source_restore_error || (unconfirmedVolumes.length
      ? "Lautstärke von " + unconfirmedVolumes.length + " Echo-Gerät(en) noch nicht bestätigt. Hörbare Wiedergabe nicht bestätigt."
      : info.playing ? "Alexa meldet Wiedergabe" : "Alexa meldet derzeit keine aktive Wiedergabe");
    if (activeApple?.kind === "Album" && info.apple_verification && !transportError && !info.source_restore_error) {
      const verification = info.apple_verification;
      const prefix = {confirmed:"Album bestätigt", pending:"Albumprüfung läuft", mismatch:"Falsches Album", unverifiable:"Album nicht überprüfbar"}[verification.status];
      if (prefix) $("playback-state").textContent = prefix + ": " + verification.reason;
    }
  } catch(e) {
    if (generation === uiGeneration && stationEpoch === epoch && transportRequestEpoch === transportEpoch) {
      $("playback-state").textContent = transportError || "Wiedergabestatus nicht verfügbar: " + e.message;
      groupTransport = null;
      trackTransport = null;
      renderGroupTransport();
    }
  } finally { songRequestRunning = false; }
}
// One backend monitor polls radio metadata, all open clients receive changes.
function applyRadioMetadata(update) {
  if (!radioReadyForViews || update.station !== selectedStation) return;
  const item = update.metadata;
  if (item?.status === "available" && (item.title || item.show)) {
    lastStationMetadata.set(selectedStation, {value:item, at:Date.now()});
    renderRadioMetadata(selectedStation, item);
  } else {
    const prior = lastStationMetadata.get(selectedStation);
    if (prior && Date.now() - prior.at < METADATA_GRACE_MS) {
      const station = selectedStation;
      const generation = uiGeneration;
      renderRadioMetadata(station, prior.value);
      setTimeout(() => {
        if (generation === uiGeneration && radioReadyForViews && selectedStation === station &&
            lastStationMetadata.get(station) === prior && Date.now() - prior.at >= METADATA_GRACE_MS)
          renderRadioMetadata(station, null);
      }, METADATA_GRACE_MS - (Date.now() - prior.at));
    } else renderRadioMetadata(selectedStation, null);
  }
}
function connectRadioEvents() {
  if (!window.EventSource) return;
  if (strictStandby || !radioReadyForViews || radioEventSource) return;
  const source = new EventSource("api/events");
  radioEventSource = source;
  source.onmessage = event => {
    if (radioEventSource !== source || !radioReadyForViews) return;
    try {
      const update = JSON.parse(event.data);
      if (!selectedStation && !activeApple && STATION_LOGOS[update.station]) {
        selectedStation = update.station;
        stationEpoch++;
        setActiveStation(selectedStation);
        updateStationLogo(selectedStation);
        $("current-title").textContent = STATIONS.find(s => s[0] === selectedStation)?.[1] || selectedStation;
      }
      applyRadioMetadata(update);
    } catch (_) { /* Ignore invalid event payloads. */ }
  };
}
function closeRadioEvents() {
  if (radioEventSource) radioEventSource.close();
  radioEventSource = null;
}
 // The server provides live ICY changes over the event connection.

// Alexa is the authoritative source for Amazon song and cover changes.
setInterval(updateSong, 6000);
const previous = new Map();
function reportError(text) {
  if (text) console.warn("[HA Music] " + text);
}
const stationButtons = new Map();
let libraryEpoch = 0;
let libraryEditorKind = "Playlist";
let libraryEditorSnapshot = null;
let libraryEditorRows = [];
let libraryEditorBusy = false;
let librarySortKind = null;
let librarySortBusy = false;
let libraryDrag = null;
const albumCoverResults = new Map();
let albumCoverTimer = false;
let albumCoverPending = false;
function validAppleImage(value) {
  if (typeof value !== "string") return "";
  if (/^api\/album-art\/[1-9][0-9]{0,15}$/.test(value)) return value;
  if (/^api\/playlist-art\/[a-f0-9]{64}$/.test(value)) return value;
  try { const url = new URL(value); return url.protocol === "https:" && !url.username && !url.password && (!url.port || url.port === "443") && (url.hostname === "mzstatic.com" || url.hostname.endsWith(".mzstatic.com")) ? url.href : ""; }
  catch (_) { return ""; }
}
function coverKey(item) { return item.id + ":" + (item.album_id || "auto"); }
function savedAlbumCover(item) {
  return /^api\/album-art\/[1-9][0-9]{0,15}$/.test(item?.artwork?.image || "") ? item.artwork : null;
}
function applyAlbumCover(button, album) {
  const imageUrl = validAppleImage(album?.image);
  if (!imageUrl || !button || button.coverApplied === imageUrl) return;
  button.coverApplied = imageUrl;
  if (button.coverImage) button.coverImage.remove();
  const image = document.createElement("img"); image.className = "apple-album-cover";
  button.coverImage = image;
  image.alt = ""; image.loading = imageUrl.startsWith("api/album-art/") ? "eager" : "lazy"; image.referrerPolicy = "no-referrer";
  const fallback = validAppleImage(album.fallback_image);
  let retried = false;
  image.addEventListener("error", () => {
    if (!retried && fallback && fallback !== imageUrl && radioReadyForViews && !strictStandby) {
      retried = true; image.src = fallback; return;
    }
    image.remove(); button.classList.remove("has-cover"); button.artworkLink.hidden = true;
  });
  image.src = imageUrl; button.appendChild(image); button.classList.add("has-cover");
  // Link stays outside the playback button, so opening the store cannot start music.
  try {
    const url = new URL(album.store_url);
    if (url.protocol === "https:" && !url.username && !url.password && ["music.apple.com", "itunes.apple.com"].includes(url.hostname)) {
      button.artworkLink.href = url.href; button.artworkLink.hidden = false;
    }
  } catch (_) { /* Missing store links do not affect playback. */ }
}
function queueAlbumCovers() {
  if (albumCoverTimer || albumCoverPending || !radioReadyForViews || mediaPreparing || strictStandby) return;
  const candidate = appleSelection?.items?.find(item => item.kind === "Album" && !savedAlbumCover(item) &&
    (!albumCoverResults.has(coverKey(item)) || albumCoverResults.get(coverKey(item)).retryAt <= Date.now()));
  if (!candidate) return;
  albumCoverTimer = true;
  setTimeout(async () => {
    albumCoverTimer = false;
    if (!radioReadyForViews || mediaPreparing || strictStandby || !appleButtons.has(candidate.id)) return;
    if (appleSelection.items.some(item => item.id === candidate.id && savedAlbumCover(item))) { queueAlbumCovers(); return; }
    albumCoverPending = true;
    const generation = uiGeneration, key = coverKey(candidate);
    try {
      const result = await api("album-covers", {search:candidate.search || candidate.name, ...(candidate.album_id ? {album_id:candidate.album_id} : {})});
      albumCoverResults.set(key, {album:result.selected, retryAt:Date.now()+(result.selected ? 60000 : 86400000)});
      if (generation === uiGeneration && radioReadyForViews && !strictStandby &&
          appleSelection.items.some(item => coverKey(item) === key)) applyAlbumCover(appleButtons.get(candidate.id), result.selected);
    } catch (_) { albumCoverResults.set(key, {album:null, retryAt:Date.now()+60000}); }
    finally { albumCoverPending = false; queueAlbumCovers(); }
  }, 4500);
}
function libraryEditorControls(busy) {
  libraryEditorBusy = busy;
  renderLibrarySortControls();
  for (const id of ["library-editor-save", "library-editor-add", "library-editor-close", "library-editor-cancel"])
    $(id).disabled = busy;
  for (const row of libraryEditorRows) {
    row.name.disabled = busy; row.search.disabled = busy; row.command.disabled = busy; row.remove.disabled = busy;
    if (row.coverButton) row.coverButton.disabled = busy;
    if (row.importFile) row.importFile.disabled = busy;
  }
}
function addLibraryEditorRow(item = {}) {
  const container = document.createElement("div"); container.className = "library-editor-row";
  const row = {container, albumId:item.album_id, tracks:item.tracks};
  for (const [field, text] of [["name", "Anzeigename"], ["command", "Text an Alexa"], ["search", "Cover-Suchname"]]) {
    const label = document.createElement("label"); label.textContent = text;
    const input = document.createElement("input"); input.type = "text"; input.maxLength = field === "command" ? 500 : 200;
    input.required = field !== "search";
    input.value = field === "command" ? (item.command ?? (item.name ? (libraryEditorKind === "Playlist" ? "spiel playlist " : "spiel album ") + item.name : "")) : (item[field] || "");
    if (field === "command") input.placeholder = libraryEditorKind === "Playlist" ? "spiel playlist Dirk" : "spiel album Albumtitel";
    label.appendChild(input);
    if (field !== "search") container.appendChild(label);
    row[field] = input;
  }
  row.remove = document.createElement("button"); row.remove.type = "button"; row.remove.textContent = "Entfernen";
  row.remove.addEventListener("click", () => {
    if (libraryEditorBusy) return;
    libraryEditorRows = libraryEditorRows.filter(existing => existing !== row);
    container.remove();
  });
  container.appendChild(row.remove); libraryEditorRows.push(row); $("library-editor-rows").appendChild(container);
  if (libraryEditorKind === "Playlist") {
    const label = document.createElement("label"); label.className = "playlist-import";
    label.textContent = "Titelliste importieren (Apple-Music-XML, Text oder CSV)";
    row.importFile = document.createElement("input"); row.importFile.type = "file";
    row.importFile.accept = ".xml,.txt,.tsv,.csv";
    row.importStatus = document.createElement("span"); row.importStatus.className = "playlist-import-status";
    row.importStatus.setAttribute("role", "status");
    row.importStatus.textContent = item.tracks ? item.tracks.length + " Titel importiert" : "Noch keine Titelliste importiert";
    row.importFile.addEventListener("change", () => importPlaylistFile(row));
    label.append(row.importFile, row.importStatus); container.appendChild(label);
  }
  if (libraryEditorKind === "Album") {
    row.coverButton = document.createElement("button"); row.coverButton.type = "button"; row.coverButton.textContent = "Cover suchen";
    row.coverResults = document.createElement("div"); row.coverResults.className = "album-cover-results";
    row.coverButton.addEventListener("click", () => searchEditorAlbumCover(row));
    for (const input of [row.name,row.search]) input.addEventListener("input", () => {
      row.albumId = undefined; row.album = null; row.coverResults.replaceChildren();
    });
    container.append(row.coverButton,row.coverResults);
  }
  return row;
}
async function importPlaylistFile(row) {
  const file = row.importFile.files?.[0];
  if (!file || libraryEditorBusy) return;
  libraryEditorControls(true);
  row.importStatus.textContent = "Importiere …";
  try {
    if (file.size > 1048576) throw new Error("Bitte eine Datei bis 1 MB auswählen");
    const bytes = new Uint8Array(await file.arrayBuffer());
    const encoding = bytes[0] === 255 && bytes[1] === 254 ? "utf-16le" : bytes[0] === 254 && bytes[1] === 255 ? "utf-16be" : "utf-8";
    let content;
    try { content = new TextDecoder(encoding, {fatal:true}).decode(bytes); }
    catch (error) {
      if (encoding !== "utf-8") throw error;
      content = new TextDecoder("windows-1252").decode(bytes);
    }
    const result = await api("playlist-import", {content});
    row.tracks = result.tracks;
    row.importStatus.textContent = row.tracks.length + " Titel importiert. Mit Übernehmen speichern.";
  } catch (error) {
    row.importStatus.textContent = "Import fehlgeschlagen: " + error.message + (row.tracks ? " – bisherige Titelliste bleibt erhalten." : "");
  } finally {
    row.importFile.value = "";
    libraryEditorControls(false);
  }
}
async function searchEditorAlbumCover(row) {
  if (libraryEditorBusy) return;
  if (!radioReadyForViews || strictStandby || mediaPreparing) {
    row.coverResults.textContent = "Albumsuche erst verfügbar, wenn HA Music bereit ist."; return;
  }
  const generation = uiGeneration;
  libraryEditorControls(true); row.coverResults.replaceChildren();
  row.coverResults.textContent = "Suche Albumcover …";
  try {
    const result = await api("album-covers", {search:row.search.value.trim() || row.name.value.trim()});
    if (generation !== uiGeneration || !radioReadyForViews || strictStandby) throw new Error("Albumsuche unterbrochen");
    row.coverResults.textContent = "";
    if (!result.items.length) row.coverResults.textContent = "Kein Album gefunden. Bitte Albumtitel und Interpret prüfen.";
    for (const album of result.items) {
      const choice = document.createElement("button"); choice.type = "button"; choice.className = "album-cover-choice";
      const image = document.createElement("img"); image.alt = ""; image.loading = "lazy";
      image.src = validAppleImage(album.image);
      const text = document.createElement("span"); text.textContent = album.artist + " – " + album.name;
      choice.append(image,text); choice.setAttribute("aria-pressed", String(row.albumId === album.album_id));
      choice.addEventListener("click", () => {
        if (libraryEditorBusy) return;
        row.albumId = album.album_id;
        row.album = album;
        for (const child of row.coverResults.children) child.setAttribute("aria-pressed", String(child === choice));
        $("library-editor-feedback").textContent = "Cover ausgewählt. Mit Übernehmen speichern.";
      });
      row.coverResults.appendChild(choice);
    }
  } catch (e) { row.coverResults.textContent = "Albumsuche fehlgeschlagen: " + e.message; }
  finally { libraryEditorControls(false); }
}
async function openLibraryEditor(kind) {
  if (librarySortBusy || libraryDrag) return;
  librarySortKind = null;
  renderAppleSelection(appleSelection);
  if (libraryEditorBusy || $("library-editor").open) return;
  libraryEditorKind = kind; libraryEditorSnapshot = null; libraryEditorRows = [];
  $("library-editor-rows").replaceChildren();
  $("library-editor-title").textContent = kind === "Playlist" ? "Playlists verwalten" : "Alben verwalten";
  $("library-editor-feedback").textContent = "Lade gespeicherte Einträge …";
  $("library-editor").showModal(); libraryEditorControls(true);
  try {
    libraryEditorSnapshot = await api("apple-library");
    for (const item of libraryEditorSnapshot.items.filter(item => item.kind === kind)) addLibraryEditorRow(item);
    if (!libraryEditorRows.length) addLibraryEditorRow();
    $("library-editor-feedback").textContent = "";
  } catch (e) {
    $("library-editor-feedback").textContent = "Einträge konnten nicht geladen werden: " + e.message;
  } finally {
    libraryEditorControls(false);
    if (!libraryEditorSnapshot) { $("library-editor-save").disabled = true; $("library-editor-add").disabled = true; }
  }
}
function closeLibraryEditor() {
  if (!libraryEditorBusy) $("library-editor").close();
}
async function submitLibraryEditor(event) {
  event.preventDefault();
  if (libraryEditorBusy || !libraryEditorSnapshot) return;
  const items = libraryEditorSnapshot.items.filter(item => item.kind !== libraryEditorKind);
  for (const row of libraryEditorRows)
    items.push({kind:libraryEditorKind, name:row.name.value.trim(), command:row.command.value, search:row.search.value.trim(), ...(row.albumId ? {album_id:row.albumId} : {}), ...(row.tracks !== undefined ? {tracks:row.tracks} : {})});
  libraryEditorControls(true); $("library-editor-feedback").textContent = "Speichere …";
  try {
    const result = await api("apple-library", {items, revision:libraryEditorSnapshot.revision});
    for (const row of libraryEditorRows) {
      if (!row.album) continue;
      const item = result.selection.items.find(item => item.kind === "Album" && item.album_id === row.albumId && item.name === row.name.value.trim());
      if (item) albumCoverResults.set(coverKey(item), {album:row.album, retryAt:0});
    }
    libraryEpoch++;
    renderAppleSelection(result.selection);
    $("library-editor").close();
  } catch (e) {
    $("library-editor-feedback").textContent = "Speichern fehlgeschlagen: " + e.message;
  } finally { libraryEditorControls(false); }
}
$("apple-playlists-edit").addEventListener("click", () => openLibraryEditor("Playlist"));
$("apple-albums-edit").addEventListener("click", () => openLibraryEditor("Album"));
$("library-editor-add").addEventListener("click", () => { if (!libraryEditorBusy && libraryEditorSnapshot) addLibraryEditorRow(); });
$("library-editor-close").addEventListener("click", closeLibraryEditor);
$("library-editor-cancel").addEventListener("click", closeLibraryEditor);
$("library-editor").addEventListener("cancel", event => { event.preventDefault(); closeLibraryEditor(); });
$("library-editor-form").addEventListener("submit", submitLibraryEditor);
function renderLibrarySortControls() {
  for (const [kind, id] of [["Playlist","apple-playlists-sort"],["Album","apple-albums-sort"]]) {
    const button = $(id), active = librarySortKind === kind;
    button.textContent = active ? "✓" : "↕";
    button.disabled = librarySortBusy || libraryEditorBusy;
    button.setAttribute("aria-pressed", String(active));
    button.setAttribute("aria-label", active ? "Sortieren beenden" : (kind === "Playlist" ? "Playlists" : "Alben") + " sortieren");
    button.title = active ? "Fertig – Reihenfolge wird automatisch gespeichert" : "Sortieren: Kacheln ziehen oder mit Pfeiltasten verschieben";
    $(kind === "Playlist" ? "apple-playlists-edit" : "apple-albums-edit").disabled = librarySortBusy || libraryEditorBusy;
  }
}
for (const [kind, id] of [["Playlist","apple-playlists-sort"],["Album","apple-albums-sort"]]) {
  $(id).addEventListener("click", () => {
    if (librarySortBusy || libraryDrag || libraryEditorBusy) return;
    librarySortKind = librarySortKind === kind ? null : kind;
    $("apple-sort-feedback").textContent = librarySortKind ? "Kacheln ziehen oder mit Pfeiltasten verschieben. Die Reihenfolge wird automatisch gespeichert." : "Sortieren beendet.";
    renderAppleSelection(appleSelection);
  });
}
async function reorderAppleFavorite(kind, source, target) {
  if (librarySortBusy || source === target) return;
  const before = appleSelection;
  const favorites = before.items.filter(item => item.kind === kind);
  const from = favorites.findIndex(item => item.id === source), to = favorites.findIndex(item => item.id === target);
  if (from < 0 || to < 0 || !before.revision) return;
  favorites.splice(to, 0, favorites.splice(from, 1)[0]);
  let index = 0;
  const items = before.items.map(item => item.kind === kind ? favorites[index++] : item);
  librarySortBusy = true;
  libraryEpoch++;
  $("apple-sort-feedback").textContent = "Reihenfolge wird gespeichert …";
  renderAppleSelection({...before, items}, true);
  try {
    const result = await api("apple-library-order", {kind, order:favorites.map(item => item.id), revision:before.revision});
    libraryEpoch++;
    $("apple-sort-feedback").textContent = "Reihenfolge gespeichert.";
    renderAppleSelection(result.selection, true);
  } catch (e) {
    libraryEpoch++;
    $("apple-sort-feedback").textContent = "Sortierung nicht gespeichert: " + e.message;
    renderAppleSelection(before, true);
    // Read the current library after a conflicting edit/import; never overwrite it.
    try {
      // radio-state supplies artwork, IDs and the matching revision together.
      const state = await api("radio-state");
      if (state.apple_music) renderAppleSelection(state.apple_music, true);
    } catch (_) { /* Keep the visible error and last known list. */ }
  } finally {
    librarySortBusy = false;
    renderAppleSelection(appleSelection, true);
    appleButtons.get(source)?.focus?.();
  }
}
function attachFavoriteSorting(button, item, favorite) {
  item.setAttribute("data-favorite-id", favorite.id);
  item.setAttribute("data-favorite-kind", favorite.kind);
  button.addEventListener("pointerdown", event => {
    if (librarySortKind !== favorite.kind || librarySortBusy || event.button !== 0) return;
    event.preventDefault();
    button.setPointerCapture(event.pointerId);
    libraryDrag = {pointer:event.pointerId, source:favorite.id, kind:favorite.kind, target:favorite.id, item, targetItem:null};
    item.classList.add("favorite-dragging");
  });
  const move = event => {
    const drag = libraryDrag;
    if (!drag || drag.pointer !== event.pointerId || drag.source !== favorite.id) return;
    const target = document.elementFromPoint(event.clientX,event.clientY)?.closest("[data-favorite-id]");
    drag.targetItem?.classList.remove("favorite-drop-target");
    drag.targetItem = null; drag.target = drag.source;
    if (target?.getAttribute("data-favorite-kind") === drag.kind) {
      drag.target = target.getAttribute("data-favorite-id");
      drag.targetItem = target;
      if (drag.target !== drag.source) target.classList.add("favorite-drop-target");
    }
    const container = $(drag.kind === "Album" ? "apple-album-list" : "apple-playlist-list");
    const rect = container.getBoundingClientRect();
    const before = container.scrollTop;
    if (event.clientY < rect.top+28) container.scrollTop -= 20;
    else if (event.clientY > rect.bottom-28) container.scrollTop += 20;
    if (container.scrollTop !== before && !drag.scrollFrame) {
      const point = {pointerId:event.pointerId,clientX:event.clientX,clientY:event.clientY};
      drag.scrollFrame = requestAnimationFrame(() => {
        drag.scrollFrame = 0;
        if (libraryDrag === drag) move(point);
      });
    }
  };
  button.addEventListener("pointermove", event => {
    if (libraryDrag?.scrollFrame) {
      cancelAnimationFrame(libraryDrag.scrollFrame);
      libraryDrag.scrollFrame = 0;
    }
    move(event);
  });
  const finish = (event, cancel = false) => {
    const drag = libraryDrag;
    if (!drag || drag.pointer !== event.pointerId || drag.source !== favorite.id) return;
    libraryDrag = null;
    if (drag.scrollFrame) cancelAnimationFrame(drag.scrollFrame);
    drag.item.classList.remove("favorite-dragging");
    drag.targetItem?.classList.remove("favorite-drop-target");
    if (button.hasPointerCapture(event.pointerId)) button.releasePointerCapture(event.pointerId);
    if (!cancel) return reorderAppleFavorite(drag.kind,drag.source,drag.target);
  };
  button.addEventListener("pointerup", event => finish(event));
  button.addEventListener("pointercancel", event => finish(event,true));
  button.addEventListener("lostpointercapture", event => finish(event,true));
  button.addEventListener("keydown", event => {
    if (librarySortKind !== favorite.kind || librarySortBusy || !["ArrowLeft","ArrowRight","ArrowUp","ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const favorites = appleSelection.items.filter(item => item.kind === favorite.kind);
    const index = favorites.findIndex(item => item.id === favorite.id);
    const target = favorites[index + (["ArrowLeft","ArrowUp"].includes(event.key) ? -1 : 1)];
    if (target) return reorderAppleFavorite(favorite.kind,favorite.id,target.id);
  });
}
function renderAppleSelection(selection, force = false) {
  if (!force && (librarySortBusy || libraryDrag)) return;
  appleSelection = selection || {items:[], available:false};
  const items = Array.isArray(appleSelection.items) ? appleSelection.items : [];
  const signature = JSON.stringify(items);
  if (signature !== appleItemsSignature) {
    appleItemsSignature = signature;
    appleButtons.clear();
    for (const [kind, id] of [["Playlist","apple-playlist-list"],["Album","apple-album-list"]]) {
      const container = $(id);
      container.replaceChildren();
      const favorites = items.filter(item => item.kind === kind);
      if (!favorites.length) {
        const empty = document.createElement("p"); empty.className = "apple-empty";
        empty.textContent = kind === "Playlist" ? "Noch keine Playlists hinterlegt." : "Noch keine Alben hinterlegt.";
        container.appendChild(empty);
      }
      for (const favorite of favorites) {
        const button = document.createElement("button"); button.type = "button";
        button.className = "station apple-favorite " + (kind === "Playlist" ? "apple-playlist" : "apple-album");
        const icon = document.createElement("span"); icon.className = "station-icon"; icon.setAttribute("aria-hidden","true");
        icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>';
        const label = document.createElement("span"); label.className = "apple-favorite-label"; label.textContent = favorite.name;
        if (kind === "Playlist") label.style.setProperty("--playlist-font-size", (favorite.name.length > 60 ? 11 : favorite.name.length > 30 ? 13 : 17) + "px");
        button.title = favorite.name; button.setAttribute("aria-label", favorite.name);
        button.append(icon,label);
        button.addEventListener("click", () => {
          if (!librarySortKind && !librarySortBusy) return openAlbumTracks(favorite.id);
        });
        appleButtons.set(favorite.id,button);
        if (kind === "Album") {
          const entry = document.createElement("div"); entry.className = "apple-album-entry"; entry.title = favorite.name;
          const link = document.createElement("a"); link.className = "album-store-link"; link.textContent = "Apple Music ↗";
          link.hidden = true; link.target = "_blank"; link.rel = "noopener noreferrer";
          button.artworkLink = link; entry.append(button,link); container.appendChild(entry);
          attachFavoriteSorting(button, entry, favorite);
          if (radioReadyForViews && !strictStandby) applyAlbumCover(button, savedAlbumCover(favorite) || albumCoverResults.get(coverKey(favorite))?.album);
        } else {
          container.appendChild(button);
          attachFavoriteSorting(button, button, favorite);
        }
      }
    }
  }
  for (const [id,button] of appleButtons) {
    const sorting = librarySortKind === items.find(item => item.id === id)?.kind;
    button.classList.toggle("favorite-sortable", sorting);
    button.disabled = librarySortBusy || (!sorting && (Boolean(librarySortKind) || stationPending || transportPending || !radioReadyForViews || mediaPreparing || strictStandby || !appleSelection.available));
    button.classList.toggle("active", activeApple?.id === id);
    button.setAttribute("aria-pressed", String(activeApple?.id === id));
    const item = items.find(item => item.id === id);
    if (item?.kind === "Album" && radioReadyForViews && !strictStandby && !mediaPreparing)
      applyAlbumCover(button, savedAlbumCover(item) || albumCoverResults.get(coverKey(item))?.album);
  }
  $("apple-library-note").textContent = librarySortKind ? $("apple-sort-feedback").textContent : items.length && !appleSelection.available && radioReadyForViews && !mediaPreparing
    ? "Apple-Music-Steuergerät unter Add-on → Konfiguration auswählen und unter Alexa-Geräte auf Aktiv setzen."
    : "Playlists und Alben über das Plus neben der Überschrift verwalten. Dein Apple-Music-Konto muss in Alexa verknüpft sein.";
  renderLibrarySortControls();
  queueAlbumCovers();
}
let albumDialogFavorite = null;
let albumDialogGeneration = 0;
let albumDialogTrackPending = false;
const playlistCoverResults = new Map();
function playlistCoverKey(track) {
  return JSON.stringify([track.album ? "album" : "song", track.album || track.name, track.album_artist || track.artist]);
}
function setPlaylistTrackCover(art, url) {
  const [image, placeholder] = art.children;
  const valid = validAppleImage(url);
  image.hidden = !valid; placeholder.hidden = !!valid;
  image.removeAttribute("src");
  image.onerror = () => { image.hidden = true; placeholder.hidden = false; };
  image.referrerPolicy = "no-referrer";
  if (valid) image.src = valid;
}
function loadPlaylistCovers(jobs, favorite, generation) {
  const queue = Array.from(jobs.values());
  const ui = uiGeneration;
  const current = () => generation === albumDialogGeneration && albumDialogFavorite?.id === favorite &&
    ui === uiGeneration && !strictStandby && radioReadyForViews && !mediaPreparing;
  const next = async () => {
    if (!queue.length || !current()) return;
    const job = queue.shift();
    try {
      const result = await api("playlist-cover", {favorite,track_id:job.track.id});
      if (!current()) return;
      const image = validAppleImage(result.image);
      playlistCoverResults.set(job.key, {image, expires:Date.now()+(image ? 86400000 : 60000)});
      while (playlistCoverResults.size > 200) playlistCoverResults.delete(playlistCoverResults.keys().next().value);
      for (const art of job.art) setPlaylistTrackCover(art, image);
    } catch (_) { /* Missing artwork does not prevent selecting or playing a song. */ }
    if (current() && queue.length) setTimeout(next, 4500);
  };
  if (queue.length) setTimeout(next, 0);
}
function setAlbumDialogCover(album) {
  const image = $("album-tracks-cover");
  const url = validAppleImage(album?.image);
  image.hidden = !url;
  $("album-tracks-cover-placeholder").hidden = !!url;
  image.removeAttribute("src");
  image.onerror = () => {
    image.hidden = true;
    $("album-tracks-cover-placeholder").hidden = false;
  };
  image.referrerPolicy = "no-referrer";
  if (url) image.src = url;
}
function closeAlbumTracks() {
  albumDialogGeneration++;
  albumDialogFavorite = null;
  albumDialogTrackPending = false;
  $("album-play-all").disabled = false;
  $("album-tracks-dialog").close();
}
$("album-tracks-close").addEventListener("click", closeAlbumTracks);
$("album-tracks-dialog").addEventListener("cancel", event => { event.preventDefault(); closeAlbumTracks(); });
$("playlist-import-open").addEventListener("click", () => { closeAlbumTracks(); openLibraryEditor("Playlist"); });
$("album-play-all").addEventListener("click", async () => {
  const favorite = albumDialogFavorite;
  if (!favorite || albumDialogTrackPending) return;
  closeAlbumTracks();
  await startAppleFavorite(favorite.id);
});
async function openAlbumTracks(id) {
  if (strictStandby || !radioReadyForViews || mediaPreparing || stationPending || transportPending || !appleSelection.available) return;
  const favorite = appleSelection.items.find(item => item.id === id && (item.kind === "Album" || item.kind === "Playlist"));
  if (!favorite) return;
  albumDialogFavorite = favorite;
  const generation = ++albumDialogGeneration;
  $("album-tracks-title").textContent = favorite.name;
  $("album-tracks-artist").textContent = "";
  $("album-play-all").textContent = favorite.kind === "Playlist" ? "▶ Ganze Playlist abspielen" : "▶ Ganzes Album abspielen";
  $("playlist-import-open").hidden = favorite.kind !== "Playlist";
  setAlbumDialogCover(savedAlbumCover(favorite) || albumCoverResults.get(coverKey(favorite))?.album);
  $("album-tracks-feedback").textContent = "Titelliste wird geladen …";
  $("album-tracks-list").replaceChildren();
  $("album-tracks-dialog").showModal();
  try {
    const result = await api(favorite.kind === "Playlist" ? "playlist-tracks" : "album-tracks", {favorite:id});
    if (generation !== albumDialogGeneration || albumDialogFavorite?.id !== id) return;
    $("album-tracks-artist").textContent = result.artist || "";
    if (result.image) setAlbumDialogCover(result);
    $("album-tracks-feedback").textContent = result.tracks?.length ? "" : favorite.kind === "Playlist" ? "Die importierte Playlist enthält keine Titel." : "Keine Titel im Apple-Katalog gefunden";
    const coverJobs = new Map();
    for (const track of result.tracks || []) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "album-track-choice";
      const number = document.createElement("span"); number.className = "album-track-number"; number.textContent = track.number + ".";
      const name = document.createElement("span"); name.className = "album-track-name"; name.textContent = track.name;
      if (favorite.kind === "Playlist") {
        const artist = document.createElement("small"); artist.className = "playlist-track-artist"; artist.textContent = track.artist;
        name.appendChild(artist);
        if (track.album) {
          const album = document.createElement("small"); album.className = "playlist-track-album"; album.textContent = track.album;
          name.appendChild(album);
        }
      }
      const play = document.createElement("span"); play.className = "album-track-play"; play.textContent = "▶"; play.setAttribute("aria-hidden", "true");
      if (favorite.kind === "Playlist") {
        button.classList.add("playlist-track-choice");
        const art = document.createElement("span"); art.className = "playlist-track-art"; art.setAttribute("aria-hidden","true");
        const image = document.createElement("img"); image.alt = ""; image.loading = "lazy";
        const placeholder = document.createElement("span"); placeholder.className = "playlist-track-art-placeholder"; placeholder.textContent = "♫";
        art.append(image,placeholder);
        const key = playlistCoverKey(track), cached = playlistCoverResults.get(key);
        const cover = validAppleImage(track.image);
        setPlaylistTrackCover(art,cover);
        if (!cover && !(cached?.expires > Date.now() && !cached.image)) {
          if (!coverJobs.has(key)) coverJobs.set(key,{key,track,art:[]});
          coverJobs.get(key).art.push(art);
        }
        button.append(number,art,name,play);
      } else button.append(number, name, play);
      button.setAttribute("aria-label", "Titel " + track.number + ": " + track.name + (favorite.kind === "Playlist" ? " von " + track.artist : "") + " abspielen");
      button.title = "Titel auf Apple Music abspielen: " + track.name;
      button.addEventListener("click", async () => {
        if (button.disabled || albumDialogTrackPending || albumDialogFavorite?.id !== id || generation !== albumDialogGeneration || stationPending || transportPending || strictStandby || !radioReadyForViews || mediaPreparing) return;
        albumDialogTrackPending = true;
        transportEpoch++;
        for (const choice of $("album-tracks-list").children) choice.disabled = true;
        $("album-play-all").disabled = true;
        $("album-tracks-feedback").textContent = "Titel wird gestartet …";
        try {
          await api(favorite.kind === "Playlist" ? "apple_playlist_track" : "apple_album_track", {favorite:id,track_id:track.id});
          if (generation !== albumDialogGeneration) return;
          transportIntent.state = "playing";
          transportError = "";
          closeAlbumTracks();
          await loadRadioState();
          updateSong();
        } catch (error) {
          if (generation !== albumDialogGeneration) return;
          $("album-tracks-feedback").textContent = "Wiedergabe fehlgeschlagen: " + error.message;
          albumDialogTrackPending = false;
          for (const choice of $("album-tracks-list").children) choice.disabled = false;
          $("album-play-all").disabled = false;
        }
      });
      $("album-tracks-list").appendChild(button);
    }
    if (favorite.kind === "Playlist") {
      loadPlaylistCovers(coverJobs,id,generation);
      if (result.tracks?.length && !result.tracks.some(track => track.album || track.local_covers))
        $("album-tracks-feedback").textContent = "Fehlende Cover werden gesucht und lokal gespeichert. Die aktuelle Mac-App überträgt Cover und Albumnamen direkt.";
    }
  } catch (error) {
    if (generation === albumDialogGeneration) $("album-tracks-feedback").textContent = "Titelliste nicht verfügbar: " + error.message;
  }
}
async function startAppleFavorite(id) {
  if (stationPending || transportPending || !radioReadyForViews || mediaPreparing || strictStandby || !appleSelection.available || !appleButtons.has(id)) return;
  const favorite = appleSelection.items.find(item => item.id === id);
  if (!favorite) return;
  stationPending = true;
  trackTransport = null;
  renderGroupTransport();
  const generation = ++uiGeneration;
  renderAppleSelection(appleSelection);
  for (const button of stationButtons.values()) button.disabled = true;
  try {
    await api("apple_music", {favorite:id});
    if (generation !== uiGeneration) return;
    transportIntent.state = "playing";
    transportError = "";
    activeApple = favorite;
    selectedStation = "";
    stationEpoch++;
    updateStationLogo("");
    setActiveStation("");
    $("now-ticker").hidden = true;
    $("now-ticker-text").textContent = "";
    $("current-title").textContent = activeApple.name;
    $("current-artist").textContent = "Apple-Music-Befehl gesendet; Rückmeldung ausstehend";
    // Backend state is shared by all open clients; the next poll selects Apple
    // metadata and stops restoring the previous radio logo.
    groupTransport = null; trackTransport = null; transportEpoch++;
    renderGroupTransport();
  } catch(e) {
    if (generation === uiGeneration) reportError("Apple-Music-Wiedergabe fehlgeschlagen: " + e.message);
  } finally {
    stationPending = false;
    await loadRadioState();
    if (generation === uiGeneration) updateSong();
  }
}
for (const [id,name] of STATIONS) {
  const button = document.createElement("button");
  button.className = "station";
  const pictogram = document.createElement("span");
  pictogram.className = "station-icon";
  pictogram.setAttribute("aria-hidden", "true");
  pictogram.innerHTML = ["1live","wdr2","swr3"].includes(id)
    ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="9" width="18" height="12" rx="2"/><path d="M5 9 18 3"/><circle cx="9" cy="15" r="2"/><path d="M15 14h3m-3 3h3"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>';
  const label = document.createElement("span");
  label.textContent = name;
  button.append(pictogram, label);
  button.setAttribute("aria-pressed", "false");
  button.disabled = true;
  button.addEventListener("click", async () => {
    if (stationPending || transportPending || !radioReadyForViews || mediaPreparing) return;
    stationPending = true;
    trackTransport = null;
    renderGroupTransport();
    const generation = ++uiGeneration;
    for (const item of stationButtons.values()) item.disabled = true;
    try {
      await api("radio_direct", {station:id});
      if (generation !== uiGeneration) return;
      transportIntent.state = "playing";
      transportError = "";
      selectedStation = id;
      activeApple = null;
      updateStationLogo(id);
      setActiveStation(id);
      stationEpoch++;
      $("current-artist").textContent = ["1live","wdr2","swr3"].includes(id) ? "Aktueller Radiotext" : "Jetzt läuft";
      $("current-title").textContent = name;
      $("now-ticker").hidden = true;
      $("now-ticker-text").textContent = "";
      updateSong();
      setTimeout(updateSong, 2500);
      if (!["wdr2","1live","swr3"].includes(id)) {
        for (const delay of [5000, 9000, 15000, 22000]) setTimeout(updateSong, delay);
      }
    } catch(e) {
      if (generation === uiGeneration) reportError("Direkte Wiedergabe fehlgeschlagen: " + e.message);
    }
    finally { stationPending = false; await loadRadioState(); }
  });
  stationButtons.set(id, button);
  $("station-list").appendChild(button);
}
function displayRadioReadiness(isReady) {
  if (!isReady) {
    if (transportPreview) transportIntent = transportPreview.previous;
    transportPreview = null;
    groupTransport = null; trackTransport = null;
  }
  renderGroupTransport();
  document.querySelector(".now").hidden = !isReady;
  document.querySelector(".dashboard-right").hidden = !isReady;
  $("radio-standby").hidden = isReady;
}
async function loadRadioState() {
  if (stateRequestRunning || powerPending) return;
  stateRequestRunning = true;
  const generation = uiGeneration;
  const requestedLibraryEpoch = libraryEpoch;
  try {
    const data = await api("radio-state");
    if (generation !== uiGeneration) return;
    const state = new Map(data.stations.map(s => [s.id,s]));
    showDashboardSetup = data.show_dashboard_setup === true;
    applyCardSetupVisibility(Boolean(data.dashboard_card_installed));
    if (isDashboardCard) reportDashboardCardLoaded();
    strictStandby = data.standby === true;
    const radioReady = data.power === "on" && data.ready === "on";
    const becameReady = radioReady && !radioReadyForViews;
    const controlsBecameReady = radioReady && !data.preparing && mediaPreparing;
    mediaPreparing = data.preparing === true;
    if (!radioReady) trackTransport = null;
    if (!radioReady) {
      closeRadioEvents();
      if (radioReadyForViews) uiGeneration++;
    }
    radioReadyForViews = radioReady;
    if (data.recovered_session && (selectedStation || activeApple)) {
      selectedStation = "";
      activeApple = null;
      stationEpoch++;
      updateStationLogo("");
      setActiveStation("");
      $("now-ticker").hidden = true;
      $("now-ticker-text").textContent = "";
    }
    const nextApple = data.apple_music?.active || null;
    if (activeApple?.id !== nextApple?.id) {
      activeApple = nextApple;
      selectedStation = "";
      stationEpoch++;
      updateStationLogo("");
      setActiveStation("");
      $("now-ticker").hidden = true;
      $("now-ticker-text").textContent = "";
      if (activeApple) {
        $("current-title").textContent = activeApple.name;
        $("current-artist").textContent = "Jetzt läuft";
      }
    }
    if (requestedLibraryEpoch === libraryEpoch) renderAppleSelection(data.apple_music);
    if (radioReady) connectRadioEvents();
    if (becameReady || controlsBecameReady) refreshPlayers();
    if (!viewPending) preferredView = data.selected_view === "apple" ? "apple" : "radio";
    $("radio-tab").disabled = !radioReady;
    $("apple-tab").disabled = !radioReady;
    show(radioReady ? preferredView : "radio");
    // The room controls are refreshed immediately at the ready transition,
    // rather than waiting for the next 30-second background interval.
    displayRadioReadiness(radioReady);
    countdownEndsAt = data.power === "on" && !radioReady && data.startup_remaining != null
      ? Date.now() + data.startup_remaining * 1000 : null;
    // Restore the actual HA Music preset after a power cycle, not merely its artwork.
    const restored = data.last_station;
    if (radioReady && !stationPending && !activeApple && selectedStation !== restored && state.has(restored)) {
      selectedStation = restored;
      stationEpoch++;
      setActiveStation(restored);
      updateStationLogo(restored);
      $("current-title").textContent = STATIONS.find(item => item[0] === restored)?.[1] || restored;
      updateSong();
    }

    for (const [key,button] of stationButtons) button.disabled = stationPending || transportPending || mediaPreparing || !radioReady || !state.get(key)?.available;
    const label = data.startup_error ? "Radio nicht verfügbar" : data.power === "on" && data.ready !== "on" ? "Radio startet …" :
      data.power === "on" ? "Radio eingeschaltet" :
      data.power === "off" ? "Radio ausgeschaltet" : "Radio nicht verfügbar";
    $("radio-standby-text").textContent = label;
    if (data.recovering || data.recovery_message) {
      countdownEndsAt = null;
      $("radio-standby-text").textContent = data.recovery_message || "Bestehenden Wiedergabestatus prüfen …";
    }
    renderCountdown();
    $("power-on").disabled = data.recovering === true || data.power === "on" || data.power === "unavailable";
    $("power-off").disabled = data.recovering === true || data.power === "off" || data.power === "unavailable";
    if (becameReady && !selectedStation) {
      $("current-title").textContent = "Bestehende Wiedergabe";
      updateSong();
    }
  } catch(e) {
    if (generation !== uiGeneration) return;
    countdownEndsAt = null;
    uiGeneration++;
    radioReadyForViews = false;
    renderAppleSelection(appleSelection);
    closeRadioEvents();
    $("radio-tab").disabled = true;
    $("apple-tab").disabled = true;
    show("radio");
    displayRadioReadiness(false);
    $("radio-standby-text").textContent = "Radio nicht verfügbar";
    for (const button of stationButtons.values()) button.disabled = true;
  } finally { stateRequestRunning = false; }
}
for (const [id,on] of [["power-on",true],["power-off",false]]) {
  $(id).addEventListener("click", async () => {
    if (powerPending) return;
    powerPending = true;
    uiGeneration++;
    radioReadyForViews = false;
    closeRadioEvents();
    displayRadioReadiness(false);
    $("power-on").disabled = true;
    $("power-off").disabled = true;
    if (on) {
      countdownEndsAt = Date.now() + 70000;
      $("radio-standby-text").textContent = "Radio startet … 70 s";
    } else {
      countdownEndsAt = null;
      $("radio-standby-text").textContent = "Radio ausgeschaltet";
    }
    try { await api("radio_power",{on});
      if (on) strictStandby = false; }
    catch(e){countdownEndsAt = null; reportError(e.message);}
    finally{powerPending = false; await loadRadioState();}
  });
}
setInterval(() => {
  if (!strictStandby || !document.hidden) loadRadioState();
}, 3000);
function masterView(groups, saved) {
  const group = groups.find(p => p.entity_id === "media_player.wohnung");
  if (!group) return null;
  // The master setting is independent; individual room changes must not move it.
  const volume = saved?.["media_player.wohnung"] ?? 0.3;
  return {...group, volume:requestedRoomLevel({...group, volume})};
}
function previewVolume(entity, level, revision, masterPreview = false) {
  requestedRoomVolumes.set(entity, {level, revision, generation:uiGeneration,
    expires:Date.now()+ROOM_VOLUME_PREVIEW_MS, settleUntil:Date.now()+3000, masterPreview});
}
function previewMasterVolume(level) {
  const revision = ++volumeRevision;
  previewVolume("media_player.wohnung", level, revision);
  for (const [entity, room] of roomVolumeRows) {
    if (!(masterRoomLevels[entity] > 0)) continue;
    previewVolume(entity, level, revision, true);
    room.render(level);
  }
  return revision;
}
function discardMasterPreview(revision) {
  for (const [entity, request] of requestedRoomVolumes) {
    if (request.revision !== revision) continue;
    requestedRoomVolumes.delete(entity);
    const room = roomVolumeRows.get(entity);
    if (room) room.render(room.observed);
  }
}
function settleVolumePreview(revision) {
  // Start the bounded wait after the command returns, including slow groups.
  for (const request of requestedRoomVolumes.values()) {
    if (request.revision !== revision) continue;
    request.expires = Date.now()+ROOM_VOLUME_PREVIEW_MS;
    request.settleUntil = Date.now()+3000;
  }
}
function requestedRoomLevel(p) {
  const request = requestedRoomVolumes.get(p.entity_id);
  if (!request) return p.volume;
  if (request.generation !== uiGeneration || Date.now() >= request.expires ||
      (Date.now() >= request.settleUntil && typeof p.volume === "number" && Math.abs(p.volume-request.level) < 0.005)) {
    requestedRoomVolumes.delete(p.entity_id);
    return p.volume;
  }
  return request.level;
}
function volumeRow(p, remembered, master) {
  const row = document.createElement("div"); row.className = "player-row";
  const title = document.createElement("span"); title.textContent = (master ? "Master Volume" : p.name + " (" + p.state + ")");
  const slider = document.createElement("input"); slider.type="range"; slider.min=0; slider.max=100; slider.step=1;
      slider.value = Math.round((p.volume ?? 0) * 100);
      const label = document.createElement("span"); label.textContent=typeof p.volume === "number" ? slider.value+"%" : "–";
      const mute = document.createElement("button"); mute.type="button"; mute.textContent="Stumm";
      slider.setAttribute("aria-label", (master ? "Master" : p.name) + " Lautstärke");
      slider.disabled = mediaPreparing;
      mute.disabled = slider.disabled;
      function renderAudioButton() {
        const audible = Number(slider.value) > 0;
        mute.textContent = master ? (audible ? "Stumm" : "Ein") : (audible ? "Hörbar" : "Stumm");
        mute.classList.toggle("is-muted", !audible);
        mute.setAttribute("aria-pressed", String(audible));
        mute.setAttribute("aria-label", (master ? "Master" : p.name) + (audible ? " stummschalten" : " hörbar schalten"));
        mute.title = master ? "Master-Lautstärke umschalten" : "Raum hörbar/stumm schalten. Die Gruppenwiedergabe läuft weiter.";
      }
      function renderLevel(level) {
        slider.value = Math.round((level ?? 0)*100);
        label.textContent = typeof level === "number" ? slider.value+"%" : "–";
        renderAudioButton();
      }
      if (!master) {
        roomVolumeRows.set(p.entity_id, {render:renderLevel, observed:p.volume});
        renderLevel(requestedRoomLevel(p));
      }
      renderAudioButton();
      slider.addEventListener("input", () => {
        label.textContent = slider.value + "%";
        renderAudioButton();
        if (!radioReadyForViews || mediaPreparing || strictStandby) return;
        volumeEditing.set(p.entity_id, uiGeneration);
        if (master) previewMasterVolume(Number(slider.value)/100);
        else {
          previewVolume(p.entity_id, Number(slider.value)/100, ++volumeRevision);
          masterRoomLevels[p.entity_id] = Number(slider.value)/100;
        }
      });
      slider.addEventListener("blur", () => {
        volumeEditing.delete(p.entity_id);
        refreshPlayers();
      });
      slider.addEventListener("change", async () => {
        volumeEditing.delete(p.entity_id);
        if (!radioReadyForViews || mediaPreparing || strictStandby) return;
        const generation = uiGeneration;
        const volume = Number(slider.value)/100;
        const revision = master ? previewMasterVolume(volume) : ++volumeRevision;
        if (!master) {
          previewVolume(p.entity_id, volume, revision);
          masterRoomLevels[p.entity_id] = volume;
        }
        volumeRequests++;
        slider.disabled = mute.disabled = true;
        try { await api("volume",{entity_id:p.entity_id,volume}); if(generation !== uiGeneration) return; settleVolumePreview(revision); if(volume>0)previous.set(p.entity_id,volume);label.textContent=slider.value+"%";renderAudioButton(); }
        catch(e){if(generation === uiGeneration){discardMasterPreview(revision);renderLevel(p.volume);reportError(e.message);}}
        finally { volumeRequests--; slider.disabled = mute.disabled = !radioReadyForViews || mediaPreparing; refreshPlayers();
          setTimeout(()=>{if(generation === uiGeneration)refreshPlayers();},ROOM_VOLUME_PREVIEW_MS); }
      });
      mute.addEventListener("click",async () => {
        if (!radioReadyForViews || mediaPreparing || strictStandby) return;
        const generation = uiGeneration;
        const current = Number(slider.value)/100;
        const next = current > 0 ? 0 : (previous.get(p.entity_id) || remembered[p.entity_id] || 0.3);
        if(current>0) previous.set(p.entity_id,current);
        const revision = master ? previewMasterVolume(next) : ++volumeRevision;
        renderLevel(next);
        if (!master) {
          previewVolume(p.entity_id, next, revision);
          masterRoomLevels[p.entity_id] = next;
        }
        volumeRequests++;
        slider.disabled = mute.disabled = true;
        try {
          // Send the exact displayed level; stale Alexa state must not turn a
          // quick unmute into a no-op or restore an older percentage.
          await api("volume", {entity_id:p.entity_id,volume:next});
          if (generation !== uiGeneration) return;
          settleVolumePreview(revision);
          slider.value = Math.round(next*100);
          label.textContent=slider.value+"%";
          renderAudioButton();
        }
        catch(e){if(generation === uiGeneration){discardMasterPreview(revision);renderLevel(p.volume);reportError(e.message);}}
        finally { volumeRequests--; slider.disabled = mute.disabled = !radioReadyForViews || mediaPreparing; refreshPlayers();
          setTimeout(()=>{if(generation === uiGeneration)refreshPlayers();},ROOM_VOLUME_PREVIEW_MS); }
      });

  if (master) {
    row.classList.add("master-row");
    const controls = document.createElement("div"); controls.className = "master-controls";
    masterTransportButton = document.createElement("button");
    masterTransportButton.type = "button";
    masterTransportButton.className = "master-transport";
    masterTransportButton.addEventListener("click", () => controlGroup(groupPlaybackState() === "playing" ? "pause" : "play"));
    controls.append(mute, masterTransportButton);
    row.append(title,slider,label,controls);
    renderGroupTransport();
  } else {
    row.append(title,slider,label,mute);
  }
  return row;
}
async function refresh() {
  try {
    const config = await api("status");
    await loadRadioState();
    reportError(config.backend === "connected" ? "" : "Home-Assistant-Verbindung nicht verfügbar.");
  } catch(e) { reportError(e.message); }
  await refreshPlayers();
  await updateSong();
}

async function refreshPlayers() {
  for (const [entity, generation] of volumeEditing) {
    if (generation !== uiGeneration) volumeEditing.delete(entity);
  }
  if (strictStandby || !radioReadyForViews || playersRequestRunning || volumeRequests || volumeEditing.size) return;
  playersRequestRunning = true;
  const generation = uiGeneration;
  const revision = volumeRevision;
  try {
    const {players,groups,remembered,saved_levels,master_room_levels} = await api("players");
    if (generation !== uiGeneration || revision !== volumeRevision || !radioReadyForViews || volumeRequests || volumeEditing.size) return;
    masterRoomLevels = master_room_levels || Object.fromEntries(players.map(p=>[p.entity_id,saved_levels?.[p.entity_id] ?? 0.3]));
    for (const [entity, request] of requestedRoomVolumes) {
      if (!request.masterPreview && entity !== "media_player.wohnung" &&
          request.generation === uiGeneration && Date.now() < request.expires) {
        masterRoomLevels[entity] = request.level;
      }
    }
    roomVolumeRows.clear();
    const master = masterView(groups, saved_levels);
    masterTransportButton = null;
    $("master-volume").replaceChildren();
    if (master) $("master-volume").appendChild(volumeRow(master, remembered, true));
    else $("master-volume").textContent = "Master-Lautstärke nicht verfügbar";
    $("groups").textContent = groups.length ? "Gruppe: " + groups.map(p => p.name).join(", ") + " · Alexa-Multiroom" : "Master-Gruppe Wohnung ist nicht aktiviert oder nicht verfügbar.";
    const wrap = $("players"); wrap.replaceChildren();
    if (!players.length) wrap.textContent = "Keine Raumgeräte aktiviert. Bitte Geräte in der Add-on-Konfiguration auswählen.";
    for (const p of players) wrap.appendChild(volumeRow({...p,volume:saved_levels?.[p.entity_id] ?? 0.3}, remembered, false));
  } catch (e) {
    if (generation === uiGeneration && revision === volumeRevision) {
      $("players").textContent = "Lautsprecher derzeit nicht verfügbar.";
      $("master-volume").textContent = "Master-Lautstärke nicht verfügbar";
      reportError("Lautsprecher konnten nicht geladen werden: " + e.message);
    }
  } finally { playersRequestRunning = false; }
}
refresh();
setInterval(() => { if (!strictStandby && radioReadyForViews && !document.querySelector("input[type=range]:active")) refreshPlayers(); }, 30000);
