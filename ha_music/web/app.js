"use strict";
const $ = id => document.getElementById(id);
function show(page) {
  const radio = page === "radio";
  $("radio-page").hidden = !radio; $("apple-page").hidden = radio;
  $("radio-tab").classList.toggle("active", radio);
  $("apple-tab").classList.toggle("active", !radio);
}
$("radio-tab").addEventListener("click", () => show("radio"));
$("apple-tab").addEventListener("click", () => show("apple"));
const STATIONS = [["1live","1LIVE"],["wdr2","WDR 2"],["swr3","SWR3"],["sommerhits","Sommerhits"],["charts","Charts"],["80s","80er"],["90s","90er"]];
async function api(path, data) {
  const response = await fetch("api/" + path, data ? {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)} : {});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Anfrage fehlgeschlagen");
  return body;
}
let ready = false;
let selectedStation = "";
async function updateSong() {
  try {
    const info = await api("playback-status");
    const details = info.details;
    const title = details?.title;
    const artist = details?.artist;
    const isRadio = ["wdr2","1live","swr3"].includes(selectedStation);
    let external = null;
    if (isRadio) {
      try { const item = await api("now-playing?station=" + encodeURIComponent(selectedStation));
        if (item.status === "available") external = item;
      } catch (_) { /* Keep Alexa media attributes when station lookup fails. */ }
    }
    const stationName = selectedStation ? STATIONS.find(s => s[0] === selectedStation)?.[1] : null;
    $("current-title").textContent = stationName || title || "Kein Sender ausgewählt";
    const hasSong = Boolean(external?.title && external?.artist);
    const alexaSong = !isRadio && title && artist;
    const songLine = hasSong ? external.artist + " – " + external.title :
      external?.show ? external.show : alexaSong ? artist + " – " + title : "";
    const ticker = $("now-ticker");
    const tickerText = $("now-ticker-text");
    if (songLine) {
      if (tickerText.textContent !== songLine) tickerText.textContent = songLine;
      ticker.hidden = false;
      ticker.classList.toggle("scrolling", songLine.length > 36);
    } else {
      ticker.hidden = true;
      ticker.classList.remove("scrolling");
      tickerText.textContent = "";
    }
    $("current-artist").textContent = hasSong ? "Jetzt läuft" : external?.show ? "Aktuelle Sendung" : songLine ? "Jetzt läuft" : "Aktuelle Programminformation nicht verfügbar";
    const cover = $("current-cover");
    // Keep Alexa's station logo in the radio panel, independently of track data.
    const picture = isRadio ? details?.image : (external?.cover || details?.image);
    if (picture && (picture.startsWith("/") || picture.startsWith("https://"))) {
      cover.src = picture; cover.hidden = false;
    } else { cover.hidden = true; cover.removeAttribute("src"); }
    $("playback-state").textContent = info.playing ? "Wiedergabe aktiv" :
      "Alexa meldet derzeit keine aktive Wiedergabe" ;
  } catch(e) { $("playback-state").textContent = "Wiedergabestatus nicht verfügbar: " + e.message; }
}
setInterval(updateSong, 30000);
const previous = new Map();
function status(text) { $("message").textContent = text; }
const stationButtons = new Map();
for (const [id,name] of STATIONS) {
  const button = document.createElement("button");
  button.textContent = name;
  button.className = "station";
  button.disabled = true;
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await api("radio_direct", {station:id});
      selectedStation = id;
      $("current-title").textContent = name;
      $("now-ticker").hidden = true;
      $("now-ticker-text").textContent = "";
      setTimeout(updateSong, 2500);
      if (["wdr2","1live","swr3"].includes(id)) setTimeout(updateSong, 10000);
      status(name + " direkt über den Alexa-Media-Player angefordert. Bitte Wiedergabe prüfen.");
    } catch(e) { status("Direkte Wiedergabe fehlgeschlagen: " + e.message); }
    finally { await loadRadioState(); }
  });
  stationButtons.set(id, button);
  $("station-list").appendChild(button);
}
async function loadRadioState() {
  try {
    const data = await api("radio-state");
    const state = new Map(data.stations.map(s => [s.id,s]));

    for (const [key,button] of stationButtons) button.disabled = !state.get(key)?.available;
    const label = data.power === "on" && data.ready !== "on" ? "Radio startet …" :
      data.power === "on" ? "Radio eingeschaltet" :
      data.power === "off" ? "Radio ausgeschaltet" : "Radio nicht verfügbar";
    $("radio-state").textContent = label;
    $("power-on").disabled = data.power === "on" || data.power === "unavailable";
    $("power-off").disabled = data.power === "off" || data.power === "unavailable";
  } catch(e) {
    $("radio-state").textContent = "Radiozustand nicht verfügbar: " + e.message;
    for (const button of stationButtons.values()) button.disabled = true;
  }
}
for (const [id,on] of [["power-on",true],["power-off",false]]) {
  $(id).addEventListener("click", async () => {
    $(id).disabled = true;
    try { await api("radio_power",{on}); status(on ? "Radio-Einschaltbefehl gesendet." : "Radio-Ausschaltbefehl gesendet."); }
    catch(e){status(e.message);}
    finally{await loadRadioState();}
  });
}
setInterval(loadRadioState, 20000);
let volumeReconcileGeneration = 0;
function scheduleVolumeReconciliation(value) {
  const generation = ++volumeReconcileGeneration;
  const room = $("players");
  for (const row of room.querySelectorAll(".player-row")) {
    const slider = row.querySelector("input[type=range]");
    const label = row.querySelectorAll("span")[1];
    const name = row.querySelector("span")?.textContent || "";
    if (!slider || slider.disabled || /unavailable/i.test(name)) continue;
    slider.value = Math.round(value * 100);
    if (label) label.textContent = Math.round(value * 100) + "%*";
  }
  status("Master-Lautstärke gesendet. Raumwerte vorläufig angezeigt (*); Bestätigung durch Alexa steht aus.");
  setTimeout(async () => {
    if (generation !== volumeReconcileGeneration) return;
    await refreshPlayers();
    status("Lautsprecherwerte erneut aus Home Assistant eingelesen.");
  }, 3000);
  setTimeout(async () => {
    if (generation !== volumeReconcileGeneration) return;
    await refreshPlayers();
  }, 15000);
}
function volumeRow(p, remembered, master) {
  const row = document.createElement("div"); row.className = "player-row";
  const title = document.createElement("span"); title.textContent = (master ? "Master Volume" : p.name + " (" + p.state + ")");
  const slider = document.createElement("input"); slider.type="range"; slider.min=0; slider.max=100; slider.step=5;
      slider.value = Math.round((p.volume ?? 0) * 100);
      const label = document.createElement("span"); label.textContent=slider.value+"%";
      const mute = document.createElement("button"); mute.type="button"; mute.textContent="Stumm";
      slider.disabled = p.state === "unavailable" || p.volume === null || p.volume === undefined;
      mute.disabled = slider.disabled;
      mute.textContent = Number(slider.value) === 0 ? "Ein" : "Stumm";
      slider.addEventListener("change", async () => {
        const volume = Number(slider.value)/100;
        try { await api("volume",{entity_id:p.entity_id,volume}); if(volume>0)previous.set(p.entity_id,volume);label.textContent=slider.value+"%";mute.textContent=volume?"Stumm":"Ein"; if (master) scheduleVolumeReconciliation(volume); }
        catch(e){status(e.message);}
      });
      mute.addEventListener("click",async () => {
        const current = Number(slider.value)/100;
        const next = current > 0 ? 0 : (previous.get(p.entity_id) || remembered[p.entity_id] || 0.3);
        if(current>0) previous.set(p.entity_id,current);
        try { await api("volume",{entity_id:p.entity_id,volume:next});slider.value=Math.round(next*100);label.textContent=slider.value+"%";mute.textContent=next?"Stumm":"Ein";if(master) scheduleVolumeReconciliation(next); }
        catch(e){status(e.message);}
      });

  row.append(title,slider,label,mute);
  return row;
}
async function refresh() {
  try {
    const config = await api("status"); ready = config.backend === "connected";
    await loadRadioState();
    status(ready ? "Sender werden direkt über Home Assistant abgespielt, ohne externe Skripte." : "Home-Assistant-Verbindung nicht verfügbar.");
  } catch(e) { status(e.message); }
  try {
    const {players,remembered,groups,excluded,diagnostics} = await api("players");
    await updateSong();
    $("groups").textContent = groups.length ? "Gruppe: " + groups.map(p => p.name).join(", ") + " · Alexa-Multiroom" : "Multiroom-Gruppe Wohnung derzeit nicht erkannt.";
    const master = groups.find(p => p.entity_id === "media_player.wohnung");
    $("master-volume").replaceChildren();
    if (master) $("master-volume").appendChild(volumeRow(master, remembered, true));
    else $("master-volume").textContent = "Master-Lautstärke nicht verfügbar";
    $("excluded").textContent = excluded?.length ? "Weitere Alexa-Geräte (nicht als Raumlautsprecher): " + excluded.map(p => p.name).join(", ") : "";
    const wrap = $("players"); wrap.replaceChildren();
    if (!players.length) { const d = diagnostics || {};
      const show = key => (d[key]?.entities ?? 0) + " Entitäten, " + (d[key]?.media_players ?? 0) + " Media Player";
      wrap.textContent = "Keine Alexa-Media-Player gefunden. Alexa Devices: " + show("alexa_devices") + "; Alexa Media Player: " + show("alexa_media") + ".";
      return; }
    for (const p of players) wrap.appendChild(volumeRow(p, remembered, false));
  } catch(e) { $("players").textContent = "Geräte konnten nicht geladen werden: "+e.message; }
}
async function refreshPlayers() {
  try {
    const {players,groups,remembered} = await api("players");
    const master = groups.find(p => p.entity_id === "media_player.wohnung");
    $("master-volume").replaceChildren();
    if (master) $("master-volume").appendChild(volumeRow(master, remembered, true));
    else $("master-volume").textContent = "Master-Lautstärke nicht verfügbar";
    $("groups").textContent = groups.length ? "Gruppe: " + groups.map(p => p.name).join(", ") + " · Alexa-Multiroom" : "Multiroom-Gruppe Wohnung derzeit nicht erkannt.";
    const wrap = $("players"); wrap.replaceChildren();
    for (const p of players) wrap.appendChild(volumeRow(p, remembered, false));
  } catch (e) { status("Lautsprecherstatus nicht aktualisiert: " + e.message); }
}
refresh();
setInterval(() => { if (!document.querySelector("input[type=range]:active")) refreshPlayers(); }, 30000);
