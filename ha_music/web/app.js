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
const STATIONS = [["wdr2","WDR 2"],["1live","1LIVE"],["wdr4","WDR 4"],["80s80s","80s80s"],["ndr2","NDR 2"],["radiobob","Radio BOB!"]];
async function api(path, data) {
  const response = await fetch("api/" + path, data ? {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)} : {});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Anfrage fehlgeschlagen");
  return body;
}
let ready = false;
let selectedStation = "";
$("test-wdr2").addEventListener("click", async () => {
  const button = $("test-wdr2");
  if (!confirm("WDR 2 jetzt auf der Alexa-Gruppe Wohnung starten? Eine laufende Wiedergabe kann unterbrochen werden.")) return;
  button.disabled = true;
  try {
    await api("test_wdr2", {});
    $("test-info").textContent = "Testbefehl gesendet. Bitte prüfen, ob alle Gruppenlautsprecher synchron Radio spielen.";
  } catch(e) {
    $("test-info").textContent = "Test fehlgeschlagen: " + e.message;
  } finally { await checkPlayback(); }
});
async function checkPlayback() {
  try {
    const result = await api("playback-check");
    $("test-wdr2").disabled = !result.available;
    $("test-info").textContent = result.reason + (result.entity_id ? " (" + result.entity_id + ")" : "");
  } catch (e) {
    $("test-wdr2").disabled = true;
    $("test-info").textContent = "Fähigkeitsprüfung fehlgeschlagen: " + e.message;
  }
}
async function updateSong() {
  if (!selectedStation) return;
  try {
    const info = await api("now-playing?station=" + encodeURIComponent(selectedStation));
    $("current-title").textContent = info.title || info.station || "Titel nicht verfügbar";
    $("current-artist").textContent = info.artist || "Keine aktuellen Metadaten";
    const cover = $("current-cover");
    cover.hidden = !info.cover;
    if (info.cover) cover.src = info.cover;
  } catch { $("current-artist").textContent = "Metadaten derzeit nicht verfügbar"; }
}
setInterval(updateSong, 60000);
const previous = new Map();
function status(text) { $("message").textContent = text; }
for (const [id,name] of STATIONS) {
  const button = document.createElement("button");
  button.textContent = name;
  button.className = "station";
  button.addEventListener("click", async () => {
    selectedStation=id; $("current-title").textContent = name;
    updateSong(); status("Titelanzeige ausgewählt. Direkte Alexa-Gruppenwiedergabe ist noch nicht verfügbar.");
  });
  $("station-list").appendChild(button);
}
async function refresh() {
  try {
    const config = await api("status"); ready = config.backend === "connected";
    status(ready ? "Geräteerkennung aktiv. Radio-Senderwahl zeigt derzeit Metadaten; direkter Multiroom-Start folgt." : "Home-Assistant-Verbindung nicht verfügbar.");
  } catch(e) { status(e.message); }
  try {
    const {players,remembered,groups,excluded,diagnostics} = await api("players");
    await checkPlayback();
    $("groups").textContent = groups.length ? "Gruppe: " + groups.map(p => p.name + " (" + p.state + ")").join(", ") + " · Die direkte Wiedergabe wird noch geprüft." : "Multiroom-Gruppe Wohnung derzeit nicht erkannt.";
    $("excluded").textContent = excluded?.length ? "Weitere Alexa-Geräte (nicht als Raumlautsprecher): " + excluded.map(p => p.name).join(", ") : "";
    const wrap = $("players"); wrap.replaceChildren();
    if (!players.length) { const d = diagnostics || {};
      const show = key => (d[key]?.entities ?? 0) + " Entitäten, " + (d[key]?.media_players ?? 0) + " Media Player";
      wrap.textContent = "Keine Alexa-Media-Player gefunden. Alexa Devices: " + show("alexa_devices") + "; Alexa Media Player: " + show("alexa_media") + ".";
      return; }
    for (const p of players) {
      const row = document.createElement("div"); row.className = "player-row";
      const title = document.createElement("span"); title.textContent = p.name + " (" + p.state + ")";
      const slider = document.createElement("input"); slider.type="range"; slider.min=0; slider.max=100; slider.step=5;
      slider.value = Math.round((p.volume ?? 0) * 100);
      const label = document.createElement("span"); label.textContent=slider.value+"%";
      const mute = document.createElement("button"); mute.type="button"; mute.textContent="Stumm";
      slider.disabled = p.volume === null || p.volume === undefined;
      mute.disabled = slider.disabled;
      slider.addEventListener("change", async () => {
        const volume = Number(slider.value)/100;
        try { await api("volume",{entity_id:p.entity_id,volume}); if(volume>0)previous.set(p.entity_id,volume);label.textContent=slider.value+"%"; }
        catch(e){status(e.message);}
      });
      mute.addEventListener("click",async () => {
        const current = Number(slider.value)/100;
        const next = current > 0 ? 0 : (previous.get(p.entity_id) || remembered[p.entity_id] || 0.3);
        if(current>0) previous.set(p.entity_id,current);
        try { await api("volume",{entity_id:p.entity_id,volume:next});slider.value=Math.round(next*100);label.textContent=slider.value+"%";mute.textContent=next?"Stumm":"Ein"; }
        catch(e){status(e.message);}
      });
      row.append(title,slider,label,mute); wrap.append(row);
    }
  } catch(e) { $("players").textContent = "Geräte konnten nicht geladen werden: "+e.message; }
}
refresh();
