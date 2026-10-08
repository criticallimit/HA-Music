"use strict";
const radioTab = document.getElementById("radio-tab");
const appleTab = document.getElementById("apple-tab");
const radioPage = document.getElementById("radio-page");
const applePage = document.getElementById("apple-page");
function show(page) {
  const radio = page === "radio";
  radioPage.hidden = !radio;
  applePage.hidden = radio;
  radioTab.classList.toggle("active", radio);
  appleTab.classList.toggle("active", !radio);
  radioTab.setAttribute("aria-pressed", String(radio));
  appleTab.setAttribute("aria-pressed", String(!radio));
}
radioTab.addEventListener("click", () => show("radio"));
appleTab.addEventListener("click", () => show("apple"));
const stations = ["WDR 2", "1LIVE", "WDR 4", "80s80s", "NDR 2", "Radio BOB!"];
for (const name of stations) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "station";
  button.disabled = true;
  button.textContent = name;
  button.title = "Sendersteuerung noch nicht eingerichtet";
  document.getElementById("station-list").appendChild(button);
}
show("radio");
