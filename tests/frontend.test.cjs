const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness() {
  const elements = new Map();
  function element() {
    return {hidden:false, disabled:false, textContent:'', value:'', children:[], attributes:{}, listeners:{},
      classList:{toggle(){},add(){}}, style:{setProperty(){}},
      addEventListener(name,handler){this.listeners[name]=handler;}, appendChild(e){this.children.push(e);},
      append(...items){this.children.push(...items);}, replaceChildren(...items){this.children = items;},
      setAttribute(k,v){this.attributes[k]=v;}, getAttribute(k){return this.attributes[k];},
      removeAttribute(k){delete this.attributes[k];}};
  }
  const get = id => {if(!elements.has(id)) elements.set(id,element()); return elements.get(id);};
  const window = {location:{origin:'http://localhost',search:''}, addEventListener(){}};
  window.parent = window;
  const context = vm.createContext({window, document:{getElementById:get,querySelector:get,
    createElement:element, hidden:false}, URLSearchParams, URL, AbortSignal, console,
    setTimeout(){}, setInterval(){}, fetch:()=>new Promise(()=>{})});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../ha_music/web/app.js'),'utf8'),context);
  return {context, get, run: code => vm.runInContext(code,context)};
}

test('countdown expiry remains starting, never claims ready', () => {
  const h = harness();
  h.run('countdownEndsAt = Date.now()-1; renderCountdown()');
  assert.match(h.get('radio-standby-text').textContent,/abgeschlossen/);
  assert.equal(h.run('radioReadyForViews'),false);
});

test('failed state clears countdown and renders unavailable', async () => {
  const h = harness();
  h.run('countdownEndsAt = Date.now()+50000; api=async()=>{throw new Error("offline")}');
  await h.run('loadRadioState()');
  h.run('renderCountdown()');
  assert.equal(h.get('radio-standby-text').textContent,'Radio nicht verfügbar');
  assert.equal(h.run('countdownEndsAt'),null);
});

test('overlapping state polls coalesce', async () => {
  const h = harness();
  let resolve, count = 0;
  h.context.reply = ()=>{ count++; return new Promise(r=>{resolve=r;}); };
  h.run('api=reply');
  const first = h.run('loadRadioState()');
  await h.run('loadRadioState()');
  assert.equal(count,1);
  resolve({stations:[],power:'off',ready:'off',standby:true});
  await first;
});

test('old playback reply cannot update UI after power generation changes', async () => {
  const h = harness();
  let resolve;
  h.context.reply=()=>new Promise(r=>{resolve=r;});
  h.run('api=reply; radioReadyForViews=true; selectedStation="charts"');
  const request = h.run('updateSong()');
  h.run('uiGeneration++; radioReadyForViews=false');
  resolve({playing:true,details:{title:'Old title',artist:'Old artist'}});
  await request;
  assert.equal(h.get('current-title').textContent,'');
});

test('saved Apple view survives off state and is restored when ready', async () => {
  const h = harness();
  h.context.reply = async()=>({stations:[],power:'off',ready:'off',standby:true,selected_view:'apple'});
  h.run('api=reply');
  await h.run('loadRadioState()');
  assert.equal(h.run('preferredView'),'apple');
  assert.equal(h.get('apple-page').hidden,true);
  h.context.reply=async()=>({stations:[],power:'on',ready:'on',selected_view:'apple'});
  h.run('refreshPlayers=async()=>{}; api=reply');
  await h.run('loadRadioState()');
  assert.equal(h.get('apple-page').hidden,false);
});

test('device errors replace perpetual loading placeholders', async () => {
  const h = harness();
  h.run('radioReadyForViews=true; api=async()=>{throw new Error("offline")}');
  await h.run('refreshPlayers()');
  assert.match(h.get('players').textContent,/nicht verfügbar/);
  assert.match(h.get('master-volume').textContent,/nicht verfügbar/);
});

test('protocol-relative artwork is rejected', async () => {
  const h = harness();
  h.context.reply=async()=>({playing:true,details:{image:'//external.invalid/image'}});
  h.run('api=reply; radioReadyForViews=true; selectedStation="charts"');
  await h.run('updateSong()');
  assert.equal(h.get('current-cover').hidden,true);
});

test('stale HA volume does not move desired room slider to 50 percent', () => {
  const h = harness();
  const room = h.run('roomView({entity_id:"media_player.wohnzimmer",volume:0.5,state:"idle"},{"media_player.wohnzimmer":0.2})');
  assert.equal(room.volume,0.2);
  assert.equal(room.observed,0.5);
  assert.equal(room.pending,true);
});

test('room target supplied by backend renders startup mute', () => {
  const h = harness();
  assert.equal(h.run('roomView({entity_id:"media_player.wohnzimmer",volume:0.4},{"media_player.wohnung":0,"media_player.wohnzimmer":0}).volume'),0);
});

test('individual volume remains independent of master zero after startup', () => {
  const h = harness();
  assert.equal(h.run('roomView({entity_id:"media_player.wohnzimmer",volume:0.3},{"media_player.wohnung":0,"media_player.wohnzimmer":0.3}).volume'),0.3);
});

test('room audio switch uses room action and server restored volume', async () => {
  const h = harness();
  const calls = [];
  h.context.reply = async (action, body) => { calls.push({action,body}); return {volume:0.45}; };
  h.run('api=reply; refreshPlayers=async()=>{}');
  const row = h.run('volumeRow({entity_id:"media_player.kueche",name:"Küche",state:"playing",volume:0},{},false)');
  assert.equal(row.children[3].textContent,'Stumm');
  await row.children[3].listeners.click();
  assert.equal(calls[0].action,'room_audio');
  assert.equal(calls[0].body.on,true);
  assert.equal(row.children[1].value,45);
  assert.equal(row.children[3].textContent,'Hörbar');
  assert.equal(row.children[3].attributes['aria-pressed'],'true');
});

test('group controls reflect confirmed group state and feature availability', () => {
  const h = harness();
  const row = h.run('volumeRow({entity_id:"media_player.wohnung",volume:0.4,state:"playing"},{},true)');
  const button = row.children[3].children[1];
  h.run('radioReadyForViews=true; groupTransport={state:"playing",can_pause:true,can_play:false}; renderGroupTransport()');
  assert.equal(button.disabled,false);
  assert.equal(button.attributes['aria-label'],'Gesamte Gruppe pausieren');
  h.run('groupTransport={state:"paused",can_pause:false,can_play:true}; renderGroupTransport()');
  assert.equal(button.disabled,false);
  assert.equal(button.attributes['aria-label'],'Gesamte Gruppe fortsetzen');
  h.run('radioReadyForViews=false; displayRadioReadiness(false)');
  assert.equal(button.disabled,true);
});

test('group commands coalesce and cannot wake standby', async () => {
  const h = harness();
  let resolve, count = 0;
  h.context.reply = () => { count++; return new Promise(r => {resolve=r;}); };
  const row = h.run('volumeRow({entity_id:"media_player.wohnung",volume:0.4,state:"playing"},{},true)');
  const button = row.children[3].children[1];
  h.run('api=reply; updateSong=async()=>{}; radioReadyForViews=true; groupTransport={can_pause:true}; strictStandby=true');
  await h.run('controlGroup("pause")');
  assert.equal(count,0);
  h.run('strictStandby=false');
  const first = h.run('controlGroup("pause")');
  await h.run('controlGroup("pause")');
  assert.equal(count,1);
  assert.equal(button.disabled,true);
  resolve({});
  await first;
  assert.equal(button.disabled,true);
});

test('playback reply from before group command cannot restore old control state', async () => {
  const h = harness();
  let resolve;
  h.context.reply = () => new Promise(r => {resolve=r;});
  h.run('api=reply; radioReadyForViews=true');
  const request = h.run('updateSong()');
  h.run('transportEpoch++; groupTransport=null');
  resolve({playing:true,transport:{state:'playing',can_pause:true}});
  await request;
  assert.equal(h.run('groupTransport'),null);
});
