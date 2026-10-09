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
  let pendingHeightFrame = 0;
  let lastReportedHeight = 0;
  const reportHeight = () => {
    pendingHeightFrame = 0;
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
  window.addEventListener("load", scheduleHeight);
  window.addEventListener("resize", scheduleHeight);
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
let transportPending = false;
let transportEpoch = 0;
let groupTransport = null;
let trackTransport = null;
let masterTransportButton = null;
function renderTrackTransport() {
  for (const [command,label] of [["previous","Vorheriger Titel"],["shuffle","Shuffle"],["next","Nächster Titel"]]) {
    const button = $("track-" + command);
    button.disabled = strictStandby || !radioReadyForViews || mediaPreparing || stationPending || transportPending || !trackTransport?.["can_" + command];
    const shuffle = command === "shuffle";
    const confirmed = shuffle && trackTransport?.shuffle === true;
    if (shuffle) {
      const state = typeof trackTransport?.shuffle === "boolean" ? (confirmed ? "on" : "off") : "unknown";
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
  if (command === "shuffle") body.shuffle = !trackTransport.shuffle;
  const generation = uiGeneration;
  transportPending = true;
  transportEpoch++;
  renderGroupTransport();
  for (const button of stationButtons.values()) button.disabled = true;
  renderAppleSelection(appleSelection);
  try {
    await api("track_transport", body);
    if (generation !== uiGeneration || !radioReadyForViews) return;
    // Do not optimistically flip shuffle: display HA's reported state only.
    trackTransport = null;
    groupTransport = null;
  } catch (e) {
    if (generation === uiGeneration) reportError("Titelsteuerung fehlgeschlagen: " + e.message);
  } finally {
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
  const pause = groupTransport?.state === "playing";
  masterTransportButton.disabled = strictStandby || !radioReadyForViews || mediaPreparing || stationPending || transportPending || !(pause ? groupTransport?.can_pause : groupTransport?.can_play);
  const label = transportPending ? "Gruppenbefehl wird gesendet …" :
    masterTransportButton.disabled ? "Gruppensteuerung derzeit nicht verfügbar" :
    pause ? "Gesamte Gruppe pausieren" : "Gesamte Gruppe fortsetzen";
  masterTransportButton.setAttribute("aria-label", label);
  masterTransportButton.title = label;
  masterTransportButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round">' +
    (pause ? '<path d="M8 5v14M16 5v14" stroke-linecap="round"/>' : '<path d="m8 5 11 7-11 7Z"/>') + '</svg>';
}
async function controlGroup(command) {
  if (strictStandby || !radioReadyForViews || mediaPreparing || stationPending || transportPending || !groupTransport?.["can_" + command]) return;
  transportPending = true;
  transportEpoch++;
  const generation = uiGeneration;
  renderGroupTransport();
  try {
    await api("group_transport", {command});
    if (generation !== uiGeneration || !radioReadyForViews) return;
    groupTransport = null;
  } catch (e) {
    if (generation === uiGeneration) reportError("Gruppensteuerung fehlgeschlagen: " + e.message);
  } finally {
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
    $("playback-state").textContent = info.source_restore_error || (unconfirmedVolumes.length
      ? "Lautstärke von " + unconfirmedVolumes.length + " Echo-Gerät(en) noch nicht bestätigt. Hörbare Wiedergabe nicht bestätigt."
      : info.playing ? "Alexa meldet Wiedergabe" : "Alexa meldet derzeit keine aktive Wiedergabe");
  } catch(e) {
    if (generation === uiGeneration && stationEpoch === epoch && transportRequestEpoch === transportEpoch) {
      $("playback-state").textContent = "Wiedergabestatus nicht verfügbar: " + e.message;
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
function renderAppleSelection(selection) {
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
        button.className = "station apple-favorite";
        const icon = document.createElement("span"); icon.className = "station-icon"; icon.setAttribute("aria-hidden","true");
        icon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>';
        const label = document.createElement("span"); label.textContent = favorite.name;
        button.title = favorite.name;
        button.append(icon,label);
        button.addEventListener("click", () => startAppleFavorite(favorite.id));
        appleButtons.set(favorite.id,button); container.appendChild(button);
      }
    }
  }
  for (const [id,button] of appleButtons) {
    button.disabled = stationPending || transportPending || !radioReadyForViews || mediaPreparing || strictStandby || !appleSelection.available;
    button.classList.toggle("active", activeApple?.id === id);
    button.setAttribute("aria-pressed", String(activeApple?.id === id));
  }
  $("apple-library-note").textContent = items.length && !appleSelection.available && radioReadyForViews && !mediaPreparing
    ? "Apple-Music-Steuergerät unter Add-on → Konfiguration auswählen und unter Alexa-Geräte auf Aktiv setzen."
    : "Playlists und Alben unter Add-on → Konfiguration → Apple-Music-Favoriten hinzufügen. Dein Apple-Music-Konto muss in Alexa verknüpft sein.";
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
  if (!isReady) { groupTransport = null; trackTransport = null; }
  renderGroupTransport();
  document.querySelector(".now").hidden = !isReady;
  document.querySelector(".dashboard-right").hidden = !isReady;
  $("radio-standby").hidden = isReady;
}
async function loadRadioState() {
  if (stateRequestRunning || powerPending) return;
  stateRequestRunning = true;
  const generation = uiGeneration;
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
    renderAppleSelection(data.apple_music);
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
  const volume = saved?.["media_player.wohnung"] ?? group.volume ?? 0;
  return {...group, volume};
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
      renderAudioButton();
      slider.addEventListener("input", () => {
        label.textContent = slider.value + "%";
      });
      slider.addEventListener("change", async () => {
        if (!radioReadyForViews || mediaPreparing || strictStandby) return;
        const generation = uiGeneration;
        const volume = Number(slider.value)/100;
        volumeRequests++;
        slider.disabled = mute.disabled = true;
        try { await api("volume",{entity_id:p.entity_id,volume}); if(generation !== uiGeneration) return; if(volume>0)previous.set(p.entity_id,volume);label.textContent=slider.value+"%";renderAudioButton(); }
        catch(e){if(generation === uiGeneration)reportError(e.message);}
        finally { volumeRequests--; slider.disabled = mute.disabled = !radioReadyForViews || mediaPreparing; refreshPlayers(); }
      });
      mute.addEventListener("click",async () => {
        if (!radioReadyForViews || mediaPreparing || strictStandby) return;
        const generation = uiGeneration;
        const current = Number(slider.value)/100;
        const next = current > 0 ? 0 : (previous.get(p.entity_id) || remembered[p.entity_id] || 0.3);
        if(current>0) previous.set(p.entity_id,current);
        volumeRequests++;
        slider.disabled = mute.disabled = true;
        try {
          const response = await api(master ? "volume" : "room_audio", master ? {entity_id:p.entity_id,volume:next} : {entity_id:p.entity_id,on:current===0});
          if (generation !== uiGeneration) return;
          slider.value = Math.round((master ? next : response.volume)*100);
          label.textContent=slider.value+"%";
          renderAudioButton();
        }
        catch(e){if(generation === uiGeneration)reportError(e.message);}
        finally { volumeRequests--; slider.disabled = mute.disabled = !radioReadyForViews || mediaPreparing; refreshPlayers(); }
      });

  if (master) {
    row.classList.add("master-row");
    const controls = document.createElement("div"); controls.className = "master-controls";
    masterTransportButton = document.createElement("button");
    masterTransportButton.type = "button";
    masterTransportButton.className = "master-transport";
    masterTransportButton.addEventListener("click", () => controlGroup(groupTransport?.state === "playing" ? "pause" : "play"));
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
  if (strictStandby || !radioReadyForViews || playersRequestRunning || volumeRequests) return;
  playersRequestRunning = true;
  const generation = uiGeneration;
  try {
    const {players,groups,remembered,saved_levels} = await api("players");
    if (generation !== uiGeneration || !radioReadyForViews || volumeRequests) return;
    const master = masterView(groups, saved_levels);
    masterTransportButton = null;
    $("master-volume").replaceChildren();
    if (master) $("master-volume").appendChild(volumeRow(master, remembered, true));
    else $("master-volume").textContent = "Master-Lautstärke nicht verfügbar";
    $("groups").textContent = groups.length ? "Gruppe: " + groups.map(p => p.name).join(", ") + " · Alexa-Multiroom" : "Master-Gruppe Wohnung ist nicht aktiviert oder nicht verfügbar.";
    const wrap = $("players"); wrap.replaceChildren();
    if (!players.length) wrap.textContent = "Keine Raumgeräte aktiviert. Bitte Geräte in der Add-on-Konfiguration auswählen.";
    for (const p of players) wrap.appendChild(volumeRow(p, remembered, false));
  } catch (e) {
    if (generation === uiGeneration) {
      $("players").textContent = "Lautsprecher derzeit nicht verfügbar.";
      $("master-volume").textContent = "Master-Lautstärke nicht verfügbar";
      reportError("Lautsprecher konnten nicht geladen werden: " + e.message);
    }
  } finally { playersRequestRunning = false; }
}
refresh();
setInterval(() => { if (!strictStandby && radioReadyForViews && !document.querySelector("input[type=range]:active")) refreshPlayers(); }, 30000);

