const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness() {
  const elements = new Map();
  const timers = [];
  function element() {
    const classes = new Set();
    return {hidden:false, disabled:false, textContent:'', value:'', children:[], attributes:{}, listeners:{},
      open:false, showModal(){this.open=true;}, close(){this.open=false;}, remove(){this.removed=true;},
      classList:{toggle(name,on){if(on) classes.add(name); else classes.delete(name);},add(name){classes.add(name);},remove(name){classes.delete(name);},contains(name){return classes.has(name);}}, style:{setProperty(){}},
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
    setTimeout(callback, delay){timers.push({callback,delay});}, setInterval(){}, fetch:()=>new Promise(()=>{})});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../ha_music/web/app.js'),'utf8'),context);
  return {context, get, timers, run: code => vm.runInContext(code,context)};
}

function albumHarness() {
  const h = harness();
  h.run('radioReadyForViews=true; renderAppleSelection({items:[{id:"a",kind:"Album",name:"Album",album_id:12,artwork:{image:"api/album-art/12"}}],available:true})');
  return h;
}

test('album dialog uses saved cover immediately and renders numbered accessible play rows', async () => {
  const h = albumHarness();
  h.context.reply = async () => ({artist:'Singer',image:'api/album-art/12',tracks:[{id:9,number:1,name:'Song <safe>'}]});
  h.run('api=reply');
  const opened = h.run('openAlbumTracks("a")');
  assert.equal(h.get('album-tracks-cover').src,'api/album-art/12');
  await opened;
  assert.equal(h.get('album-tracks-artist').textContent,'Singer');
  const row = h.get('album-tracks-list').children[0];
  assert.deepEqual(row.children.map(x=>x.textContent),['1.','Song <safe>','▶']);
  assert.match(row.attributes['aria-label'],/Song <safe> abspielen/);
  assert.equal(row.children[2].attributes['aria-hidden'],'true');
  h.get('album-tracks-cover').onerror();
  assert.equal(h.get('album-tracks-cover').hidden,true);
  assert.equal(h.get('album-tracks-cover-placeholder').hidden,false);
});

test('album dialog preserves album and individual track playback payloads', async () => {
  const h = albumHarness(), calls=[];
  h.context.reply = async (action,payload) => {calls.push([action,payload]);return {tracks:[{id:9,number:1,name:'Song'}]};};
  h.run('api=reply; loadRadioState=async()=>{};updateSong=()=>{}');
  await h.run('openAlbumTracks("a")');
  await h.get('album-tracks-list').children[0].listeners.click();
  assert.equal(calls[1][0],'apple_album_track');
  assert.equal(calls[1][1].favorite,'a');
  assert.equal(calls[1][1].track_id,9);
  assert.equal(h.get('album-tracks-dialog').open,false);
  h.context.start = async id => calls.push(['whole',id]);
  h.run('startAppleFavorite=start');
  await h.run('openAlbumTracks("a")');
  await h.get('album-play-all').listeners.click();
  assert.deepEqual(calls.at(-1),['whole','a']);
});

test('closed album dialog ignores late metadata and standby prevents opening', async () => {
  const h = albumHarness();
  let resolve;
  h.context.reply = () => new Promise(r=>{resolve=r;});
  h.run('api=reply');
  const pending = h.run('openAlbumTracks("a")');
  h.run('closeAlbumTracks()');
  resolve({artist:'Stale',image:'api/album-art/99',tracks:[{id:9,number:1,name:'Old'}]});
  await pending;
  assert.equal(h.get('album-tracks-list').children.length,0);
  assert.equal(h.get('album-tracks-cover').src,'api/album-art/12');
  h.run('strictStandby=true');
  await h.run('openAlbumTracks("a")');
  assert.equal(h.get('album-tracks-dialog').open,false);
});

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

test('normal radio start overrides the previous Apple tab without a playback request', async () => {
  const h = harness();
  const calls=[];
  h.context.reply=async(action)=>{
    calls.push(action);
    return {stations:[{id:'wdr2',available:true}],power:'on',ready:'on',selected_view:'radio',last_station:'wdr2',apple_music:{items:[],active:null}};
  };
  h.run('api=reply; preferredView="apple"; show("apple"); refreshPlayers=async()=>{}; updateSong=async()=>{}');
  await h.run('loadRadioState()');
  assert.equal(h.get('radio-page').hidden,false);
  assert.equal(h.get('apple-page').hidden,true);
  assert.equal(h.get('radio-tab').classList.contains('active'),true);
  assert.equal(h.get('apple-tab').classList.contains('active'),false);
  assert.equal(h.run('selectedStation'),'wdr2');
  assert.deepEqual(calls,['radio-state']);
});

test('normal Apple start overrides the previous Radio tab without a playback request', async () => {
  const h = harness();
  const calls=[];
  h.context.reply=async(action)=>{
    calls.push(action);
    return {stations:[],power:'on',ready:'on',selected_view:'apple',last_station:'wdr2',apple_music:{items:[],active:{id:'favorite',name:'Dirk'}}};
  };
  h.run('api=reply; preferredView="radio"; refreshPlayers=async()=>{}; updateSong=async()=>{}');
  await h.run('loadRadioState()');
  assert.equal(h.get('apple-page').hidden,false);
  assert.equal(h.get('radio-page').hidden,true);
  assert.equal(h.get('apple-tab').classList.contains('active'),true);
  assert.equal(h.run('selectedStation'),'');
  assert.deepEqual(calls,['radio-state']);
});

test('automatic recovery shows a check in progress without permitting a premature power command', async () => {
  const h = harness();
  h.context.reply=async()=>({stations:[],power:'off',ready:'off',standby:true,recovering:true,
    recovery_message:'Bestehenden Wiedergabestatus prüfen …'});
  h.run('api=reply');
  await h.run('loadRadioState()');
  assert.match(h.get('radio-standby-text').textContent,/prüfen/);
  assert.equal(h.get('power-on').disabled,true);
  assert.equal(h.get('power-off').disabled,true);
  assert.equal(h.run('radioReadyForViews'),false);
});

test('reattached Apple session clears old radio branding and immediately reads playback', async () => {
  const h = harness();
  let reads=0;
  h.context.reply=async()=>({stations:[{id:'wdr2',available:true}],power:'on',ready:'on',
    recovered_session:true,last_station:'',selected_view:'apple',apple_music:{items:[],available:true,active:null}});
  h.context.readPlayback=()=>{reads++;};
  h.run('api=reply; selectedStation="wdr2"; updateSong=readPlayback; refreshPlayers=async()=>{}');
  await h.run('loadRadioState()');
  assert.equal(h.get('apple-page').hidden,false);
  assert.equal(h.run('selectedStation'),'');
  assert.equal(h.get('current-cover').hidden,true);
  assert.equal(h.run('radioReadyForViews'),true);
  assert.equal(reads,1);
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

test('room slider and percentage show only the current HA volume', async () => {
  const h = harness();
  h.context.reply=async()=>({players:[{entity_id:'media_player.wohnzimmer',volume:.5,state:'idle'}],groups:[],remembered:{},saved_levels:{'media_player.wohnzimmer':.2}});
  h.run('api=reply; radioReadyForViews=true');
  await h.run('refreshPlayers()');
  const row = h.get('players').children[0];
  assert.equal(row.children[1].value,50);
  assert.equal(row.children[2].textContent,'50%');
  assert.equal(row.children[2].title,undefined);
});

test('saved mute does not replace the HA room volume', async () => {
  const h = harness();
  h.context.reply=async()=>({players:[{entity_id:'media_player.wohnzimmer',volume:.4,state:'playing'}],groups:[],remembered:{},saved_levels:{'media_player.wohnung':0,'media_player.wohnzimmer':0}});
  h.run('api=reply; radioReadyForViews=true');
  await h.run('refreshPlayers()');
  assert.equal(h.get('players').children[0].children[2].textContent,'40%');
});

test('individual volume remains independent of master zero after startup', async () => {
  const h = harness();
  h.context.reply=async()=>({players:[{entity_id:'media_player.wohnzimmer',volume:.3,state:'playing'}],groups:[{entity_id:'media_player.wohnung',volume:.7}],remembered:{},saved_levels:{'media_player.wohnung':0,'media_player.wohnzimmer':.3}});
  h.run('api=reply; radioReadyForViews=true');
  await h.run('refreshPlayers()');
  assert.equal(h.get('players').children[0].children[2].textContent,'30%');
  assert.equal(h.get('master-volume').children[0].children[2].textContent,'0%');
});

test('room audio switch uses room action and server restored volume', async () => {
  const h = harness();
  const calls = [];
  h.context.reply = async (action, body) => { calls.push({action,body}); return {volume:0.45}; };
  h.run('api=reply; radioReadyForViews=true; refreshPlayers=async()=>{}');
  const row = h.run('volumeRow({entity_id:"media_player.kueche",name:"Küche",state:"playing",volume:0},{},false)');
  assert.equal(row.children[3].textContent,'Stumm');
  await row.children[3].listeners.click();
  assert.equal(calls[0].action,'room_audio');
  assert.equal(calls[0].body.on,true);
  assert.equal(row.children[1].value,45);
  assert.equal(row.children[3].textContent,'Hörbar');
  assert.equal(row.children[3].attributes['aria-pressed'],'true');
});

async function masterHarness() {
  const h = harness();
  const data = {players:[
    {entity_id:'media_player.wohnzimmer',name:'Wohnzimmer',volume:.3,state:'playing'},
    {entity_id:'media_player.kueche',name:'Küche',volume:0,state:'playing'}],
    groups:[{entity_id:'media_player.wohnung',volume:.3}],remembered:{},
    saved_levels:{'media_player.wohnung':.3},
    master_room_levels:{'media_player.wohnzimmer':.3,'media_player.kueche':0}};
  h.context.reply=async()=>data;
  h.run('api=(action,body)=>reply(action,body); radioReadyForViews=true');
  await h.run('refreshPlayers()');
  return {...h,data};
}

test('master drag updates active room slider percentage and icon before any request', async () => {
  const h = await masterHarness();
  const calls=[];
  h.context.reply=async(action)=>{calls.push(action);return h.data;};
  const slider=h.get('master-volume').children[0].children[1];
  slider.value=65;
  slider.listeners.input();
  assert.equal(h.get('players').children[0].children[1].value,65);
  assert.equal(h.get('players').children[0].children[2].textContent,'65%');
  assert.equal(h.get('players').children[0].children[3].attributes['aria-pressed'],'true');
  assert.equal(h.get('players').children[1].children[1].value,0);
  assert.deepEqual(calls,[]);
});

test('master preview survives delayed HA values then follows confirmed and external values', async () => {
  const h=await masterHarness();
  let resolve;
  h.context.reply=(action)=> action==='volume' ? new Promise(r=>{resolve=r;}) : Promise.resolve(h.data);
  const slider=h.get('master-volume').children[0].children[1];
  slider.value=60;
  const pending=slider.listeners.change();
  assert.equal(h.get('players').children[0].children[1].value,60);
  resolve({ok:true});
  await pending;
  await h.run('refreshPlayers()');
  assert.equal(h.get('players').children[0].children[1].value,60);
  h.data.players[0].volume=.6;
  await h.run('refreshPlayers()');
  assert.equal(h.run('requestedRoomVolumes.size'),0);
  h.data.players[0].volume=.45;
  await h.run('refreshPlayers()');
  assert.equal(h.get('players').children[0].children[1].value,45);
});

test('master mute preview keeps room intent and never unmutes an individually muted room', async () => {
  const h=await masterHarness();
  h.run('previewMasterVolume(0)');
  assert.equal(h.get('players').children[0].children[1].value,0);
  assert.equal(h.get('players').children[0].children[3].attributes['aria-pressed'],'false');
  h.data.players[0].volume=0;
  h.data.saved_levels['media_player.wohnung']=0;
  await h.run('refreshPlayers()');
  h.run('previewMasterVolume(.7)');
  assert.equal(h.get('players').children[0].children[1].value,70);
  assert.equal(h.get('players').children[1].children[1].value,0);
});

test('master mute button immediately previews mute and unmute before the command returns', async () => {
  const h=await masterHarness();
  let resolve;
  h.context.reply=(action)=>action==='volume' ? new Promise(r=>{resolve=r;}) : Promise.resolve(h.data);
  let button=h.get('master-volume').children[0].children[3].children[0];
  const mute=button.listeners.click();
  assert.equal(h.get('players').children[0].children[1].value,0);
  h.data.saved_levels['media_player.wohnung']=0;
  resolve({ok:true});
  await mute;
  await h.run('refreshPlayers()');
  button=h.get('master-volume').children[0].children[3].children[0];
  const unmute=button.listeners.click();
  assert.equal(h.get('players').children[0].children[1].value,30);
  assert.equal(h.get('players').children[1].children[1].value,0);
  h.data.saved_levels['media_player.wohnung']=.3;
  resolve({ok:true});
  await unmute;
});

test('player reply started before master drag cannot replace the new preview', async () => {
  for(const fail of [false,true]) {
    const h=await masterHarness();
    let resolve,reject;
    h.context.reply=()=>new Promise((r,j)=>{resolve=r;reject=j;});
    const pending=h.run('refreshPlayers()');
    h.run('previewMasterVolume(.75)');
    if(fail)reject(new Error('old offline reply'));else resolve(h.data);
    await pending;
    assert.equal(h.get('players').children[0].children[1].value,75);
    assert.equal(h.get('players').textContent,'');
  }
});

test('failed master request rolls room preview back to observed values', async () => {
  const h=await masterHarness();
  h.context.reply=async(action)=>{if(action==='volume')throw new Error('offline');return h.data;};
  const slider=h.get('master-volume').children[0].children[1];
  slider.value=90;
  await slider.listeners.change();
  assert.equal(h.get('players').children[0].children[1].value,30);
  assert.equal(h.run('requestedRoomVolumes.size'),0);
});

test('newer individual room input wins over the pending master preview', async () => {
  const h=await masterHarness();
  let resolve;
  h.context.reply=()=>new Promise(r=>{resolve=r;});
  h.run('refreshPlayers=async()=>{}');
  const master=h.get('master-volume').children[0].children[1];
  master.value=80;
  const pending=master.listeners.change();
  const room=h.get('players').children[0].children[1];
  room.value=35;
  room.listeners.input();
  resolve({ok:true});
  await pending;
  assert.equal(room.value,35);
  assert.equal(h.run('requestedRoomVolumes.has("media_player.wohnzimmer")'),false);
});

test('unconfirmed master preview expires and cannot cross a power generation', async () => {
  const h=await masterHarness();
  h.run('previewMasterVolume(.8); requestedRoomVolumes.get("media_player.wohnzimmer").expires=0');
  await h.run('refreshPlayers()');
  assert.equal(h.get('players').children[0].children[1].value,30);
  h.run('previewMasterVolume(.9); uiGeneration++');
  await h.run('refreshPlayers()');
  assert.equal(h.get('players').children[0].children[1].value,30);
  assert.equal(h.run('requestedRoomVolumes.size'),0);
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

test('cover controls reflect reported capabilities and confirmed shuffle state', () => {
  const h = harness();
  h.run('radioReadyForViews=true; trackTransport={entity_id:"media_player.wohnung",can_previous:true,can_next:true,can_shuffle:false,shuffle:null}; renderTrackTransport()');
  assert.equal(h.get('track-previous').disabled,false);
  assert.equal(h.get('track-next').disabled,false);
  assert.equal(h.get('track-shuffle').disabled,true);
  h.run('trackTransport.can_shuffle=true; trackTransport.shuffle=true; renderTrackTransport()');
  assert.equal(h.get('track-shuffle').attributes['aria-pressed'],'true');
  assert.equal(h.get('track-shuffle').attributes['aria-label'],'Zufällige Wiedergabe aktiv – auf Reihenfolge umschalten');
  assert.equal(h.get('track-shuffle').attributes['data-shuffle-state'],'on');
  assert.equal(h.get('track-shuffle').classList.contains('active'),true);
  h.run('trackTransport.shuffle=false; renderTrackTransport()');
  assert.equal(h.get('track-shuffle').disabled,false);
  assert.equal(h.get('track-shuffle').attributes['aria-pressed'],'false');
  assert.equal(h.get('track-shuffle').attributes['data-shuffle-state'],'off');
  assert.equal(h.get('track-shuffle').classList.contains('active'),false);
  assert.equal(h.get('track-shuffle').attributes['aria-label'],'Wiedergabe in Reihenfolge – Shuffle einschalten');
  h.run('radioReadyForViews=false; displayRadioReadiness(false)');
  assert.equal(h.get('track-next').disabled,true);
  assert.equal(h.get('track-shuffle').attributes['aria-pressed'],'false');
  assert.equal(h.get('track-shuffle').attributes['data-shuffle-state'],'unknown');
});

test('track commands coalesce, include selected target and cannot wake standby', async () => {
  const h = harness();
  let resolve;
  const calls=[];
  h.context.reply=(action,body)=>{calls.push({action,body});return new Promise(r=>{resolve=r;});};
  h.run('api=reply; radioReadyForViews=true; strictStandby=true; trackTransport={entity_id:"media_player.wohnung",can_next:true}; loadRadioState=async()=>{}; updateSong=async()=>{}');
  await h.run('controlTrack("next")');
  assert.equal(calls.length,0);
  h.run('strictStandby=false');
  const first=h.run('controlTrack("next")');
  await h.run('controlTrack("next")');
  assert.equal(calls.length,1);
  assert.equal(calls[0].action,'track_transport');
  assert.equal(calls[0].body.entity_id,'media_player.wohnung');
  assert.equal(calls[0].body.command,'next');
  assert.equal(h.get('track-next').disabled,true);
  resolve({ok:true});
  await first;
  assert.equal(h.run('trackTransport'),null);
});

test('shuffle click sends explicit desired state and waits for HA confirmation', async () => {
  const h = harness();
  let resolve;
  let desired;
  h.context.reply=(action,body)=>{desired=body.shuffle;return new Promise(r=>{resolve=r;});};
  h.run('api=reply; radioReadyForViews=true; trackTransport={entity_id:"media_player.wohnung",can_shuffle:true,shuffle:false}; renderTrackTransport(); loadRadioState=async()=>{}; updateSong=async()=>{}');
  const request=h.get('track-shuffle').listeners.click();
  assert.equal(desired,true);
  assert.equal(h.get('track-shuffle').attributes['aria-pressed'],'false');
  resolve({ok:true});
  await request;
  assert.equal(h.get('track-shuffle').attributes['aria-pressed'],'false');
});

test('old playback reply cannot re-enable cover controls after power off', async () => {
  const h = harness();
  let resolve;
  h.context.reply=()=>new Promise(r=>{resolve=r;});
  h.run('api=reply; radioReadyForViews=true');
  const request=h.run('updateSong()');
  h.run('uiGeneration++; radioReadyForViews=false; displayRadioReadiness(false)');
  resolve({playing:true,track_transport:{entity_id:'media_player.wohnung',can_next:true}});
  await request;
  assert.equal(h.get('track-next').disabled,true);
  assert.equal(h.run('trackTransport'),null);
});

test('active shuffle sends false and only becomes dim when HA reports ordered playback', async () => {
  const h = harness();
  const calls=[];
  h.context.reply=async(action,body)=>{calls.push({action,body});return {ok:true};};
  h.context.playback=async()=>({playing:true,track_transport:{entity_id:'media_player.wohnung',can_shuffle:true,shuffle:false}});
  h.run('const originalUpdateSong = updateSong');
  h.run('api=reply; radioReadyForViews=true; trackTransport={entity_id:"media_player.wohnung",can_shuffle:true,shuffle:true}; renderTrackTransport(); loadRadioState=async()=>{}; updateSong=async()=>{}');
  assert.equal(h.get('track-shuffle').classList.contains('active'),true);
  await h.run('controlTrack("shuffle")');
  assert.equal(calls.length,1);
  assert.equal(calls[0].action,'track_transport');
  assert.equal(calls[0].body.shuffle,false);
  assert.equal(h.get('track-shuffle').attributes['data-shuffle-state'],'unknown');
  h.run('api=playback');
  // Call the original polling function again after the request has settled.
  h.run('updateSong = originalUpdateSong');
  await h.run('updateSong()');
  assert.equal(h.get('track-shuffle').attributes['data-shuffle-state'],'off');
  assert.equal(h.get('track-shuffle').classList.contains('active'),false);
  assert.equal(h.get('track-shuffle').disabled,false);
});

test('playback polling renders cover controls for an active queue', async () => {
  const h = harness();
  h.context.reply=async()=>({playing:true,details:{title:'Titel'},track_transport:{entity_id:'media_player.wohnung',can_next:true,can_shuffle:true,shuffle:true}});
  h.run('api=reply; radioReadyForViews=true');
  await h.run('updateSong()');
  assert.equal(h.get('track-next').disabled,false);
  assert.equal(h.get('track-previous').disabled,true);
  assert.equal(h.get('track-shuffle').attributes['aria-pressed'],'true');
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

test('pending view reply cannot reopen Apple view after power off', async () => {
  const h = harness();
  let resolve;
  h.context.reply = () => new Promise(r => {resolve=r;});
  h.run('api=reply; radioReadyForViews=true; show("radio")');
  const request = h.run('selectView("apple")');
  h.run('uiGeneration++; radioReadyForViews=false; show("radio")');
  resolve({});
  await request;
  assert.equal(h.get('apple-page').hidden,true);
  assert.equal(h.run('preferredView'),'radio');
});

test('timed ready shows interface while startup commands still prepare devices', async () => {
  const h = harness();
  h.context.reply = async () => ({stations:[], power:'on', ready:'on', preparing:true});
  h.run('api=reply; refreshPlayers=async()=>{}');
  await h.run('loadRadioState()');
  assert.equal(h.get('.now').hidden,false);
  assert.equal(h.get('radio-standby').hidden,true);
  assert.equal(h.run('mediaPreparing'),true);
  assert.equal(h.run('[...stationButtons.values()].every(button=>button.disabled)'),true);
  h.context.reply = async () => ({stations:[], power:'on', ready:'on', preparing:false});
  h.run('api=reply');
  await h.run('loadRadioState()');
  assert.equal(h.run('mediaPreparing'),false);
});

test('unavailable metadata expires without another stream event', () => {
  const h = harness();
  h.context.EventSource = class {close(){}};
  h.run('window.EventSource=EventSource; radioReadyForViews=true; selectedStation="wdr2"; Date.now=()=>100000; lastStationMetadata.set("wdr2",{value:{title:"Old",artist:"Artist"},at:90000}); connectRadioEvents(); radioEventSource.onmessage({data:JSON.stringify({station:"wdr2",metadata:{status:"unavailable"}})})');
  assert.equal(h.get('now-ticker').hidden,false);
  const expiry = h.timers.find(t => t.delay === 80000);
  assert.ok(expiry);
  h.run('Date.now=()=>180000');
  expiry.callback();
  assert.equal(h.get('now-ticker').hidden,true);
});

test('metadata expiry cannot remove a newer song', () => {
  const h = harness();
  h.context.EventSource = class {close(){}};
  h.run('window.EventSource=EventSource; radioReadyForViews=true; selectedStation="wdr2"; Date.now=()=>100000; lastStationMetadata.set("wdr2",{value:{title:"Old",artist:"Artist"},at:90000}); connectRadioEvents(); radioEventSource.onmessage({data:JSON.stringify({station:"wdr2",metadata:{status:"unavailable"}})})');
  const expiry = h.timers.find(t => t.delay === 80000);
  h.run('Date.now=()=>180000; radioEventSource.onmessage({data:JSON.stringify({station:"wdr2",metadata:{status:"available",title:"New",artist:"Artist"}})})');
  expiry.callback();
  assert.equal(h.get('now-ticker-text').textContent,'Artist – New');
});

test('Lovelace errors are rendered as text rather than HTML', () => {
  let Card;
  const context = vm.createContext({HTMLElement:class {}, window:{}, customElements:{get(){},define(name,value){if(name==='ha-music-card') Card=value;}}, console});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../ha_music/lovelace/ha-music-card.js'),'utf8'),context);
  const loading = {};
  const shadowRoot = {innerHTML:'', querySelector(){return loading;}};
  const message = '<img src=x onerror=alert(1)>';
  Card.prototype._renderShell.call({shadowRoot,_isCardPicker:()=>false},message);
  assert.equal(loading.textContent,message);
  assert.equal(shadowRoot.innerHTML.includes(message),false);
});

test('saved favorites appear and disappear on state polls without restarting playback', async () => {
  const h=harness();
  const calls=[];
  const active={id:'playing',name:'Current playlist',target:'media_player.wohnzimmer'};
  let items=[{id:'playing',name:'Current playlist',kind:'Playlist'}];
  h.context.reply=async(action)=>{calls.push(action);return {stations:[],power:'on',ready:'on',selected_view:'apple',apple_music:{items,available:true,active}};};
  h.context.active=active;
  h.run('api=reply; activeApple=active; radioReadyForViews=true; refreshPlayers=async()=>{}; updateSong=async()=>{}');
  await h.run('loadRadioState()');
  items=[{id:'new',name:'New playlist',kind:'Playlist'},{id:'album',name:'New album',kind:'Album'}];
  await h.run('loadRadioState()');
  assert.equal(h.get('apple-playlist-list').children[0].title,'New playlist');
  assert.equal(h.get('apple-album-list').children[0].title,'New album');
  assert.equal(h.run('activeApple.id'),'playing');
  items=[];
  await h.run('loadRadioState()');
  assert.match(h.get('apple-playlist-list').children[0].textContent,/Noch keine Playlists/);
  assert.equal(h.run('activeApple.id'),'playing');
  assert.deepEqual(calls,['radio-state','radio-state','radio-state']);
});

test('Apple favorites are plain text tiles and disabled during standby', () => {
  const h = harness();
  h.run('radioReadyForViews=true; renderAppleSelection({available:true,items:[{id:"one",name:"<img src=x>",kind:"Playlist"}]})');
  const button = h.get('apple-playlist-list').children[0];
  assert.equal(button.children[1].textContent,'<img src=x>');
  assert.equal(button.disabled,false);
  h.run('strictStandby=true; renderAppleSelection(appleSelection)');
  assert.equal(button.disabled,true);
});

test('Apple playback sends configured favorite id and suppresses old radio logo restoration', async () => {
  const h = harness();
  const calls = [];
  h.context.reply = async (action,body) => {
    calls.push({action,body});
    if (action === 'apple_music') return {ok:true};
    return {stations:[{id:'wdr2',available:true}],power:'on',ready:'on',last_station:'wdr2',selected_view:'apple',
      apple_music:{available:true,items:[{id:'one',name:'Abendmusik',kind:'Playlist'}],active:{id:'one',name:'Abendmusik',kind:'Playlist'}}};
  };
  h.run('radioReadyForViews=true; selectedStation="wdr2"; api=reply; refreshPlayers=async()=>{}; updateSong=async()=>{}; renderAppleSelection({available:true,items:[{id:"one",name:"Abendmusik",kind:"Playlist"}]})');
  await h.get('apple-playlist-list').children[0].listeners.click();
  assert.equal(calls[0].action,'apple_music');
  assert.equal(calls[0].body.favorite,'one');
  assert.equal(h.run('activeApple.id'),'one');
  assert.equal(h.run('selectedStation'),'');
  assert.equal(h.get('current-title').textContent,'Abendmusik');
  assert.equal(h.get('current-cover').hidden,true);
});

test('Apple favorite commands coalesce and cannot wake standby', async () => {
  const h = harness();
  let resolve, count=0;
  h.context.reply=()=>{count++;return new Promise(r=>{resolve=r;});};
  h.run('radioReadyForViews=true; api=reply; renderAppleSelection({available:true,items:[{id:"one",name:"Test",kind:"Album"}]}); strictStandby=true');
  await h.run('startAppleFavorite("one")');
  assert.equal(count,0);
  h.run('strictStandby=false; loadRadioState=async()=>{}; updateSong=async()=>{}');
  const first=h.run('startAppleFavorite("one")');
  await h.run('startAppleFavorite("one")');
  assert.equal(count,1);
  h.run('uiGeneration++; radioReadyForViews=false');
  resolve({ok:true});
  await first;
  assert.equal(h.run('activeApple'),null);
});

test('group pause is disabled during a pending source selection', async () => {
  const h = harness();
  let calls=0;
  h.context.reply=async()=>{calls++;return {ok:true};};
  const row=h.run('volumeRow({entity_id:"media_player.wohnung",volume:.4,state:"playing"},{},true)');
  h.run('api=reply; radioReadyForViews=true; stationPending=true; groupTransport={state:"playing",can_pause:true}; renderGroupTransport()');
  assert.equal(row.children[3].children[1].disabled,true);
  await h.run('controlGroup("pause")');
  assert.equal(calls,0);
});

test('accepted Apple click removes radio branding even when state polling is already running', async () => {
  const h = harness();
  h.context.reply=async()=>({ok:true});
  h.run('api=reply; radioReadyForViews=true; selectedStation="1live"; updateStationLogo("1live"); stateRequestRunning=true; updateSong=async()=>{}; renderAppleSelection({available:true,items:[{id:"one",name:"Abendmusik",kind:"Playlist"}]})');
  await h.run('startAppleFavorite("one")');
  assert.equal(h.run('selectedStation'),'');
  assert.equal(h.run('activeApple.id'),'one');
  assert.equal(h.get('current-cover').hidden,true);
  assert.equal(h.get('current-title').textContent,'Abendmusik');
  assert.match(h.get('current-artist').textContent,/Rückmeldung ausstehend/);
});

test('unconfirmed echo volumes do not claim audible playback', async () => {
  const h = harness();
  h.context.reply=async()=>({playing:true,volume_confirmation:{'media_player.bad':{expected:.4,observed:.01}}});
  h.run('api=reply; radioReadyForViews=true');
  await h.run('updateSong()');
  assert.match(h.get('playback-state').textContent,/Hörbare Wiedergabe nicht bestätigt/);
  const row=h.run('volumeRow({entity_id:"media_player.bad",name:"Bad",state:"playing",volume:.01},{},false)');
  assert.equal(row.children[2].textContent,'1%');
});

test('missing HA room volume does not pretend a saved percentage is current', () => {
  const h = harness();
  const row=h.run('volumeRow({entity_id:"media_player.bad",name:"Bad",state:"unknown",volume:null},{},false)');
  assert.equal(row.children[2].textContent,'–');
});

test('invalid artwork does not discard valid playback controls', async () => {
  const h = harness();
  h.context.reply=async()=>({playing:true,details:{title:'Titel',image:42},track_transport:{entity_id:'media_player.wohnung',can_next:true}});
  h.run('api=reply; radioReadyForViews=true');
  await h.run('updateSong()');
  assert.equal(h.get('current-cover').hidden,true);
  assert.equal(h.get('track-next').disabled,false);
  assert.equal(h.get('current-title').textContent,'Titel');
});

test('failed saved source restoration is visible even if Alexa reports playing', async () => {
  const h = harness();
  h.context.reply=async()=>({playing:true,source_restore_error:'Gespeicherte Wiedergabe konnte nicht wiederhergestellt werden'});
  h.run('api=reply; radioReadyForViews=true');
  await h.run('updateSong()');
  assert.equal(h.get('playback-state').textContent,'Gespeicherte Wiedergabe konnte nicht wiederhergestellt werden');
});

test('stale 1LIVE artwork cannot return after an accepted Apple selection', async () => {
  const h = harness();
  h.context.reply=async()=>({playing:true,details:{title:'1LIVE',image:'/1live.svg',content_type:'radio'}});
  h.run('api=reply; radioReadyForViews=true; activeApple={id:"one",name:"Abendmusik"}; selectedStation=""');
  await h.run('updateSong()');
  assert.equal(h.get('current-cover').hidden,true);
  assert.equal(h.get('current-title').textContent,'Abendmusik');
});

test('Apple title and cover use Alexa metadata without changing radio selection', async () => {
  const h = harness();
  h.context.reply=async()=>({playing:true,details:{title:'Mein Titel',artist:'Mein Interpret',image:'https://example.com/cover.jpg'}});
  h.run('api=reply; radioReadyForViews=true; activeApple={id:"one",name:"Abendmusik"}; selectedStation=""');
  await h.run('updateSong()');
  assert.equal(h.get('current-title').textContent,'Mein Titel');
  assert.equal(h.get('now-ticker-text').textContent,'Mein Interpret – Mein Titel');
  assert.equal(h.get('current-cover').src,'https://example.com/cover.jpg');
  assert.equal(h.get('current-cover').hidden,false);
});

test('next track replaces Apple title artist and artwork together from new playback reply', async () => {
  const h = harness();
  let next=false;
  h.context.reply=async(action)=>{
    if(action==='track_transport'){next=true;return {ok:true};}
    return {playing:true,track_transport:{entity_id:'media_player.wohnung',can_next:true},details:next
      ? {entity_id:'media_player.wohnung',title:'Better Together',artist:'Jack Johnson',image:'https://example.com/new.jpg'}
      : {entity_id:'media_player.wohnung',title:'Alter Titel',artist:'Alter Künstler',image:'https://example.com/old.jpg'}};
  };
  h.run('api=reply; radioReadyForViews=true; activeApple={id:"one",name:"Playlist"}; loadRadioState=async()=>{}');
  await h.run('updateSong()');
  assert.equal(h.get('now-ticker-text').textContent,'Alter Künstler – Alter Titel');
  await h.run('controlTrack("next")');
  assert.equal(h.get('current-title').textContent,'Better Together');
  assert.equal(h.get('now-ticker-text').textContent,'Jack Johnson – Better Together');
  assert.equal(h.get('current-cover').src,'https://example.com/new.jpg');
});

test('regular playback reply restores radiotext after missing event delivery', async () => {
  const h = harness();
  h.context.reply=async()=>({playing:true,radio_metadata:{station:'wdr2',metadata:{status:'available',title:'Track',artist:'Artist'}}});
  h.run('api=reply; radioReadyForViews=true; selectedStation="wdr2"');
  await h.run('updateSong()');
  assert.equal(h.get('now-ticker-text').textContent,'Artist – Track');
  assert.equal(h.get('current-artist').textContent,'Aktueller Radiotext');
});

test('cached radio snapshot cannot overwrite another station or wake standby', async () => {
  const h = harness();
  h.context.reply=async()=>({playing:true,radio_metadata:{station:'swr3',metadata:{status:'available',title:'Wrong',artist:'Artist'}}});
  h.run('api=reply; radioReadyForViews=true; selectedStation="wdr2"');
  await h.run('updateSong()');
  assert.equal(h.get('now-ticker-text').textContent,'');
  h.run('radioReadyForViews=false; applyRadioMetadata({station:"wdr2",metadata:{status:"available",title:"Wrong",artist:"Artist"}})');
  assert.equal(h.get('now-ticker-text').textContent,'');
});

test('library editor saves current category and preserves albums without playback commands', async () => {
  const h=harness(), calls=[];
  h.context.reply=async(action,body)=>{
    calls.push({action,body});
    if (!body) return {revision:'r',items:[{name:'Old',kind:'Playlist'},{name:'Album',kind:'Album'}]};
    return {selection:{items:[{id:'new',name:'New',kind:'Playlist'}],available:true}};
  };
  h.run('api=reply; radioReadyForViews=true');
  await h.run('openLibraryEditor("Playlist")');
  assert.equal(h.get('library-editor').open,true);
  assert.equal(h.run('libraryEditorRows[0].command.value'),'spiel playlist Old');
  h.run('libraryEditorRows[0].name.value="New"');
  h.run('libraryEditorRows[0].command.value="  spiele meine Playlist Dirk auf Apple Music auf Wohnung  "');
  await h.run('submitLibraryEditor({preventDefault(){}})');
  assert.equal(h.get('library-editor').open,false);
  assert.equal(calls[1].body.revision,'r');
  assert.equal(calls[1].body.items.find(i=>i.kind==='Album').name,'Album');
  assert.equal(calls[1].body.items.find(i=>i.kind==='Playlist').name,'New');
  assert.equal(calls[1].body.items.find(i=>i.kind==='Playlist').command,'  spiele meine Playlist Dirk auf Apple Music auf Wohnung  ');
  assert.equal(h.run('appleButtons.has("new")'),true);
  assert.ok(calls.every(c=>c.action==='apple-library'));
});

test('library cancel discards edits and failed save retains editable draft', async () => {
  const h=harness(), calls=[];
  h.context.reply=async(action,body)=>{calls.push(body);if(body) throw new Error('disk full');return {revision:'r',items:[]};};
  h.run('api=reply');await h.run('openLibraryEditor("Album")');
  h.run('libraryEditorRows[0].name.value="My Album"');
  await h.run('submitLibraryEditor({preventDefault(){}})');
  assert.equal(h.get('library-editor').open,true);
  assert.match(h.get('library-editor-feedback').textContent,/disk full/);
  assert.equal(h.run('libraryEditorRows[0].name.value'),'My Album');
  assert.equal(h.get('library-editor-save').disabled,false);
  h.run('closeLibraryEditor()');assert.equal(calls.length,2);
});

test('library removes entries and renders untrusted labels as text', async () => {
  const h=harness();let posted;
  h.context.reply=async(action,body)=>{if(!body)return {revision:'r',items:[{name:'<img src=x>',kind:'Album'}]};posted=body;return {selection:{items:[],available:false}};};
  h.run('api=reply');await h.run('openLibraryEditor("Album")');
  assert.equal(h.run('libraryEditorRows[0].name.value'),'<img src=x>');
  h.run('libraryEditorRows[0].remove.listeners.click()');
  await h.run('submitLibraryEditor({preventDefault(){}})');
  assert.equal(posted.items.length,0);
  assert.equal(h.get('library-editor').open,false);
});

test('failed library load prevents replacing existing entries with an empty draft', async () => {
  const h=harness();h.context.reply=async()=>{throw new Error('offline');};
  h.run('api=reply');await h.run('openLibraryEditor("Playlist")');
  assert.equal(h.get('library-editor-save').disabled,true);
  assert.equal(h.get('library-editor-add').disabled,true);
  assert.match(h.get('library-editor-feedback').textContent,/offline/);
});

test('old state poll cannot remove newly saved library tiles', async () => {
  const h=harness(); let resolve;
  h.context.reply=()=>new Promise(r=>{resolve=r;});
  h.run('api=reply; refreshPlayers=async()=>{}');
  const pending=h.run('loadRadioState()');
  h.run('libraryEpoch++; renderAppleSelection({items:[{id:"new",kind:"Album",name:"New"}],available:true})');
  resolve({stations:[],power:'on',ready:'on',apple_music:{items:[],available:true}});
  await pending;
  assert.equal(h.run('appleButtons.has("new")'),true);
});

test('album artwork loads before playback and playlists retain their entered labels', async () => {
  const h=harness(), calls=[];
  h.context.reply=async(action)=>{calls.push(action);return {selected:{image:'https://is1.mzstatic.com/cover.jpg',store_url:'https://music.apple.com/de/album/12'}};};
  h.run('api=reply; radioReadyForViews=true; renderAppleSelection({items:[{id:"a",kind:"Album",name:"Album",search:"Album"},{id:"p",kind:"Playlist",name:"Dirks Favoriten"}],available:true})');
  await h.timers.find(timer=>timer.delay===4500).callback();
  assert.equal(h.run('appleButtons.get("a").classList.contains("has-cover")'),true);
  assert.equal(h.run('appleButtons.get("a").children.at(-1).src'),'https://is1.mzstatic.com/cover.jpg');
  assert.equal(h.run('appleButtons.get("p").children[1].textContent'),'Dirks Favoriten');
  assert.deepEqual(calls,['album-covers']);
});

test('album cover selection is saved with library without starting music', async () => {
  const h=harness();let posted;
  h.context.reply=async(action,body)=>{
    if(action==='album-covers')return {items:[{album_id:12,name:'Album',artist:'Singer',image:'https://is1.mzstatic.com/a.jpg'}]};
    if(!body)return {revision:'r',items:[{kind:'Album',name:'Album'}]};
    posted=body;return {selection:{items:[],available:true}};
  };
  h.run('api=reply; radioReadyForViews=true');await h.run('openLibraryEditor("Album")');
  await h.run('searchEditorAlbumCover(libraryEditorRows[0])');
  h.run('libraryEditorRows[0].coverResults.children[0].listeners.click()');
  await h.run('submitLibraryEditor({preventDefault(){}})');
  assert.equal(posted.items[0].album_id,12);
});

test('untrusted artwork and cover searches in standby cannot issue image requests', async () => {
  const h=harness();h.run('radioReadyForViews=false; renderAppleSelection({items:[{id:"a",kind:"Album",name:"Album"}],available:false})');
  assert.equal(h.timers.some(timer=>timer.delay===4500),false);
  h.run('applyAlbumCover(appleButtons.get("a"),{image:"https://mzstatic.com.evil.test/a.jpg"})');
  assert.equal(h.run('appleButtons.get("a").classList.contains("has-cover")'),false);
});

test('missing album image leaves a usable labeled playback button', () => {
  const h=harness();h.run('radioReadyForViews=true; renderAppleSelection({items:[{id:"a",kind:"Album",name:"Album"}],available:true}); applyAlbumCover(appleButtons.get("a"),{image:"https://is1.mzstatic.com/a.jpg",store_url:"https://music.apple.com/de/album/12"})');
  h.run('appleButtons.get("a").children.at(-1).listeners.error()');
  assert.equal(h.run('appleButtons.get("a").classList.contains("has-cover")'),false);
  assert.equal(h.run('appleButtons.get("a").children[1].textContent'),'Album');
});

test('confirmed album image accepts only the local numeric artwork route', () => {
  const h=harness();
  assert.equal(h.run('validAppleImage("api/album-art/12")'),'api/album-art/12');
  assert.equal(h.run('validAppleImage("api/album-art/../../options.json")'),'');
  assert.equal(h.run('validAppleImage("https://evil.test/api/album-art/12")'),'');
  h.run('radioReadyForViews=true;renderAppleSelection({items:[{id:"a",kind:"Album",name:"Album"}],available:true});applyAlbumCover(appleButtons.get("a"),{image:"api/album-art/12"})');
  assert.equal(h.run('appleButtons.get("a").coverImage.src'),'api/album-art/12');
});

test('saved album pictures render immediately without timers or cover API calls', () => {
  const h=harness();
  h.run('radioReadyForViews=true;renderAppleSelection({items:[{id:"a",kind:"Album",album_id:12,name:"Album",artwork:{image:"api/album-art/12"}},{id:"b",kind:"Album",album_id:13,name:"Album 2",artwork:{image:"api/album-art/13"}}],available:true})');
  assert.equal(h.run('appleButtons.get("a").coverImage.src'),'api/album-art/12');
  assert.equal(h.run('appleButtons.get("b").coverImage.src'),'api/album-art/13');
  assert.equal(h.run('appleButtons.get("a").coverImage.loading'),'eager');
  assert.equal(h.timers.some(timer=>timer.delay===4500),false);
});

test('saved covers take priority over old remote browser results', () => {
  const h=harness();
  h.run('radioReadyForViews=true;albumCoverResults.set("a:12",{album:{image:"https://is1.mzstatic.com/old.jpg"}});renderAppleSelection({items:[{id:"a",kind:"Album",album_id:12,name:"Album",artwork:{image:"api/album-art/12"}}],available:true})');
  assert.equal(h.run('appleButtons.get("a").coverImage.src'),'api/album-art/12');
  assert.equal(h.timers.some(timer=>timer.delay===4500),false);
});
