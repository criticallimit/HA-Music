const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function cardHarness(config, width, height) {
  const card = {clientWidth:width,style:{}};
  const context = vm.createContext({HTMLElement:class {},customElements:{get:()=>true},window:{customCards:[],innerHeight:516},CustomEvent:class {constructor(name,options){this.detail=options.detail;}}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../ha_music/lovelace/ha-music-card.js'),'utf8'),context);
  const instance = vm.runInContext('Object.create(HAMusicCard.prototype)',context);
  Object.assign(instance,{_config:config,_measuredHeight:height,style:{},getBoundingClientRect:()=>({top:100}),shadowRoot:{querySelector:()=>card},
    _iframe:{style:{},attributes:{},setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];}}});
  return {instance,card,iframe:instance._iframe,context};
}

test('Host sends available height once per change and for a replacement iframe', () => {
  const h = cardHarness({},600,1000), messages=[];
  h.context.window.location={origin:'http://ha.test'};
  h.iframe.contentWindow={postMessage(message,origin){messages.push({message,origin});}};
  h.instance._applyDimensions();h.instance._applyDimensions();
  assert.equal(messages.length,1);
  assert.equal(messages[0].message.height,400);
  assert.equal(messages[0].message.type,'ha-music-available-height');
  assert.equal(messages[0].origin,'http://ha.test');
  h.context.window.innerHeight=616;h.instance._applyDimensions();
  assert.equal(messages[1].message.height,500);
  h.instance._iframe={...h.iframe};h.instance._applyDimensions();
  assert.equal(messages.length,3);
});

test('automatic height fits content below the card on mobile and desktop without scrolling', () => {
  for (const width of [390,1200]) {
    const {instance,card,iframe} = cardHarness({},width,1000);
    instance._applyDimensions();
    assert.equal(card.style.height,'400px');
    assert.equal(iframe.style.width,width+'px');
    assert.equal(iframe.style.height,'1000px');
    assert.equal(iframe.style.transform,'scale(0.4)');
    assert.equal(iframe.attributes.scrolling,'no');
    instance._measuredHeight=1600;
    instance._applyDimensions();
    assert.equal(iframe.style.transform,'scale(0.25)');
  }
});

test('viewport resize recomputes automatic height and old dimensions are ignored', () => {
  const {instance,card,iframe,context} = cardHarness({height:2000,width:1200},600,800);
  instance._applyDimensions();
  assert.equal(instance.style.width,'100%');
  assert.equal(card.style.height,'400px');
  assert.equal(iframe.style.width,'600px');
  assert.equal(iframe.style.transform,'scale(0.5)');
  context.window.innerHeight=916;
  instance._applyDimensions();
  assert.equal(card.style.height,'800px');
  assert.equal(iframe.style.transform,'scale(1)');
  assert.equal(iframe.style.height,'800px');
  assert.equal(iframe.attributes.scrolling,'no');
  context.window.visualViewport={height:500,offsetTop:100};
  card.clientWidth=390;
  instance._applyDimensions();
  assert.equal(card.style.height,'484px');
  assert.equal(iframe.style.width,'390px');
});

test('theme edits remove obsolete width and height while preserving other card settings', () => {
  const {context}=cardHarness({},600,800);
  const editor=vm.runInContext('Object.create(HAMusicCardEditor.prototype)',context);
  let changed;
  editor._config={type:'custom:ha-music-card',width:800,height:600,grid_options:{columns:12}};
  editor.dispatchEvent=event=>{changed=event.detail.config;};
  editor._changed({theme:'My theme'});
  assert.equal(changed.width,undefined);
  assert.equal(changed.height,undefined);
  assert.equal(changed.theme,'My theme');
  assert.equal(changed.grid_options.columns,12);
});

function harness() {
  const elements = new Map();
  const timers = [];
  function element() {
    const classes = new Set();
    return {hidden:false, disabled:false, textContent:'', value:'', children:[], attributes:{}, listeners:{},
      get src(){return this.attributes.src;},set src(value){this.attributes.src=value;if(this.onload)this.onload();},
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
    createElement:element, hidden:false}, URLSearchParams, URL, AbortSignal, TextDecoder, console,
    setTimeout(callback, delay){timers.push({callback,delay});}, setInterval(){}, fetch:()=>new Promise(()=>{})});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../ha_music/web/app.js'),'utf8'),context);
  return {context, get, timers, run: code => vm.runInContext(code,context)};
}

function albumHarness() {
  const h = harness();
  h.run('radioReadyForViews=true; renderAppleSelection({items:[{id:"a",kind:"Album",name:"Album",album_id:12,artwork:{image:"api/album-art/12"}}],available:true})');
  return h;
}

function sortingHarness() {
  const h=harness();
  const selection={available:true,revision:'original',items:[
    {id:'p1',kind:'Playlist',name:'One',tracks:[{id:1,name:'Song'}]},
    {id:'a1',kind:'Album',name:'Album One',album_id:12},
    {id:'p2',kind:'Playlist',name:'Two'},
    {id:'a2',kind:'Album',name:'Album Two',album_id:13},
    {id:'p3',kind:'Playlist',name:'Three'}]};
  h.context.selection=selection;
  h.run('radioReadyForViews=true; renderAppleSelection(selection); api=(action,body)=>reply(action,body)');
  return {...h,selection};
}

test('sorting saves only existing IDs and retains the new order across reloads without playback', async () => {
  const h=sortingHarness(), calls=[];
  h.context.reply=async(action,body)=>{
    calls.push({action,body});
    return {selection:{...h.selection,revision:'saved',items:[h.selection.items[2],h.selection.items[1],h.selection.items[4],h.selection.items[3],h.selection.items[0]]}};
  };
  h.get('apple-playlists-sort').listeners.click();
  await h.get('apple-playlist-list').children[0].listeners.click();
  assert.equal(calls.length,0,'Sorting clicks must not start playback or open a track list');
  await h.run('reorderAppleFavorite("Playlist","p1","p3")');
  assert.equal(calls[0].action,'apple-library-order');
  assert.deepEqual(Array.from(calls[0].body.order),['p2','p3','p1']);
  assert.equal(calls[0].body.revision,'original');
  assert.equal(calls[0].body.items,undefined);
  assert.deepEqual(h.get('apple-playlist-list').children.map(c=>c.attributes['data-favorite-id']),['p2','p3','p1']);
  assert.deepEqual(h.get('apple-album-list').children.map(c=>c.attributes['data-favorite-id']),['a1','a2']);
  h.run('appleItemsSignature=""; renderAppleSelection(appleSelection)');
  assert.deepEqual(h.get('apple-playlist-list').children.map(c=>c.attributes['data-favorite-id']),['p2','p3','p1']);
  assert.equal(h.get('apple-sort-feedback').textContent,'Reihenfolge gespeichert.');
});

test('pending reorder keeps its preview through polling and serializes further moves', async () => {
  const h=sortingHarness();let resolve;
  h.context.reply=()=>new Promise(r=>{resolve=r;});
  const pending=h.run('reorderAppleFavorite("Playlist","p1","p2")');
  h.run('renderAppleSelection(selection)');
  assert.equal(h.get('apple-playlist-list').children[0].attributes['data-favorite-id'],'p2');
  assert.equal(h.get('apple-playlists-sort').disabled,true);
  await h.run('reorderAppleFavorite("Playlist","p3","p2")');
  const saved=h.run('appleSelection');resolve({selection:{...saved,revision:'saved'}});await pending;
  assert.equal(h.get('apple-playlists-sort').disabled,false);
  assert.equal(h.run('appleSelection.revision'),'saved');
});

test('failed reorder reloads a concurrent import instead of overwriting it', async () => {
  const h=sortingHarness(),calls=[];
  const latest={...h.selection,revision:'imported',items:[...h.selection.items,{id:'p4',name:'Imported',kind:'Playlist'}]};
  h.context.reply=async action=>{calls.push(action);if(action==='apple-library-order')throw new Error('Liste inzwischen geändert');return {apple_music:latest};};
  h.get('apple-playlists-sort').listeners.click();
  await h.run('reorderAppleFavorite("Playlist","p1","p2")');
  assert.deepEqual(calls,['apple-library-order','radio-state']);
  assert.equal(h.run('appleSelection.revision'),'imported');
  assert.equal(h.get('apple-playlist-list').children.length,4);
  assert.match(h.get('apple-sort-feedback').textContent,/nicht gespeichert/);
  assert.equal(h.run('librarySortBusy'),false);
});

test('album keyboard sorting moves the complete cover entry and preserves playlists', async () => {
  const h=sortingHarness();
  h.context.reply=async(action,body)=>({selection:{...h.selection,revision:'saved',items:[h.selection.items[0],h.selection.items[3],h.selection.items[2],h.selection.items[1],h.selection.items[4]]}});
  h.get('apple-albums-sort').listeners.click();
  let prevented=false;
  await h.get('apple-album-list').children[0].children[0].listeners.keydown({key:'ArrowRight',preventDefault(){prevented=true;}});
  assert.equal(prevented,true);
  assert.deepEqual(h.get('apple-album-list').children.map(c=>c.attributes['data-favorite-id']),['a2','a1']);
  assert.deepEqual(h.get('apple-playlist-list').children.map(c=>c.attributes['data-favorite-id']),['p1','p2','p3']);
});

test('pointer drag cancellation is inert and dropping saves a move only within the same section', async () => {
  const h=sortingHarness(),calls=[];
  h.context.reply=async(action,body)=>{calls.push({action,body});return {selection:h.run('appleSelection')};};
  h.get('apple-playlists-sort').listeners.click();
  let button=h.get('apple-playlist-list').children[0];
  button.setPointerCapture=()=>{};button.hasPointerCapture=()=>true;button.releasePointerCapture=()=>{};
  const event={pointerId:1,button:0,clientX:50,clientY:50,preventDefault(){}};
  h.get('apple-playlist-list').getBoundingClientRect=()=>({top:0,bottom:100});
  let target=h.get('apple-album-list').children[0];
  h.context.document.elementFromPoint=()=>({closest:()=>target});
  button.listeners.pointerdown(event);button.listeners.pointermove(event);await button.listeners.pointerup(event);
  assert.equal(calls.length,0);
  target=h.get('apple-playlist-list').children[2];
  button.listeners.pointerdown(event);button.listeners.pointermove(event);button.listeners.pointercancel(event);
  assert.equal(calls.length,0);assert.equal(h.run('libraryDrag'),null);
  button.listeners.pointerdown(event);button.listeners.pointermove(event);await button.listeners.pointerup(event);
  assert.equal(calls.length,1);
  assert.deepEqual(Array.from(calls[0].body.order),['p2','p3','p1']);
});

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

test('playlist tile opens title selection with artist and preserves playback payload', async () => {
  const h=harness(), calls=[];
  h.context.reply=async(action,body)=>{calls.push({action,body});return {artist:'Importierte Playlist',tracks:[{id:91,number:1,name:'Song',artist:'Singer'}]};};
  h.run('api=reply; radioReadyForViews=true; loadRadioState=async()=>{};updateSong=()=>{};renderAppleSelection({available:true,items:[{id:"p",kind:"Playlist",name:"My list"}]})');
  await h.get('apple-playlist-list').children[0].listeners.click();
  assert.equal(calls.length,1);
  assert.equal(calls[0].action,'playlist-tracks');
  assert.match(h.get('album-play-all').textContent,/Ganze Playlist/);
  const row=h.get('album-tracks-list').children[0];
  assert.equal(row.children[2].children[0].textContent,'Singer');
  assert.equal(h.get('playlist-import-open').hidden,false);
  await row.listeners.click();
  assert.equal(calls[1].action,'apple_playlist_track');
  assert.equal(calls[1].body.favorite,'p');
  assert.equal(calls[1].body.track_id,91);
});

test('playlist rows show cached album covers and album names without changing the track playback ID', async () => {
  const h=harness(),calls=[];
  h.context.reply=async(action,body)=>{calls.push({action,body});return {tracks:[{id:91,number:1,name:'Song',artist:'Singer',album:'The Album',image:'https://a.mzstatic.com/cover.jpg'}]};};
  h.run('api=reply;radioReadyForViews=true;loadRadioState=async()=>{};updateSong=()=>{};renderAppleSelection({available:true,items:[{id:"p",kind:"Playlist",name:"List"}]})');
  await h.run('openAlbumTracks("p")');
  const row=h.get('album-tracks-list').children[0];
  assert.equal(row.children[1].children[0].src,'https://a.mzstatic.com/cover.jpg');
  assert.equal(row.children[2].children[1].textContent,'The Album');
  row.children[1].children[0].onerror();
  assert.equal(row.children[1].children[0].hidden,true);
  assert.equal(row.children[1].children[1].hidden,false);
  await row.listeners.click();
  assert.equal(calls[1].action,'apple_playlist_track');
  assert.equal(calls[1].body.track_id,91);
});

test('playlist cover loading coalesces albums and stops when the dialog closes or standby begins', async () => {
  for(const stop of ['closeAlbumTracks()','strictStandby=true']){
    const h=harness(),calls=[];
    h.context.reply=async(action,body)=>{
      calls.push(action);
      if(action==='playlist-cover')return {image:'https://a.mzstatic.com/cover.jpg'};
      return {tracks:[{id:1,number:1,name:'One',artist:'Singer',album:'Same'},
        {id:2,number:2,name:'Two',artist:'Singer',album:'Same'},
        {id:3,number:3,name:'Three',artist:'Singer',album:'Other'}]};
    };
    h.run('api=reply;radioReadyForViews=true;renderAppleSelection({available:true,items:[{id:"p",kind:"Playlist",name:"List"}]})');
    await h.run('openAlbumTracks("p")');
    const first=h.timers.find(t=>t.delay===0);await first.callback();
    assert.equal(calls.filter(c=>c==='playlist-cover').length,1);
    for(const row of h.get('album-tracks-list').children.slice(0,2))assert.equal(row.children[1].children[0].src,'https://a.mzstatic.com/cover.jpg');
    const next=h.timers.filter(t=>t.delay===4500).at(-1);
    h.run(stop);await next.callback();
    assert.equal(calls.filter(c=>c==='playlist-cover').length,1);
  }
});

test('untrusted playlist artwork is rejected and a delayed cover cannot fill a closed dialog', async () => {
  const h=harness();let resolve;
  h.context.reply=async action=>action==='playlist-cover' ? new Promise(r=>{resolve=r;}) :
    {tracks:[{id:1,number:1,name:'Song',artist:'Singer',album:'Album',image:'https://evil.invalid/cover.jpg'}]};
  h.run('api=reply;radioReadyForViews=true;renderAppleSelection({available:true,items:[{id:"p",kind:"Playlist",name:"List"}]})');
  await h.run('openAlbumTracks("p")');
  const art=h.get('album-tracks-list').children[0].children[1];
  assert.equal(art.children[0].src,undefined);
  const pending=h.timers.find(t=>t.delay===0).callback();
  h.run('closeAlbumTracks()');resolve({image:'https://a.mzstatic.com/cover.jpg'});await pending;
  assert.equal(art.children[0].src,undefined);
  assert.equal(h.run('playlistCoverResults.size'),0);
});

test('uploaded playlist covers render locally and only missing images request a fallback', async () => {
  const h=harness(),calls=[], cover='api/playlist-art/'+'a'.repeat(64);
  h.context.reply=async(action,body)=>{
    calls.push({action,body});
    if(action==='playlist-cover')return {image:'api/album-art/12'};
    return {tracks:[{id:1,number:1,name:'One',artist:'Singer',image:cover,local_covers:true},
      {id:2,number:2,name:'Two',artist:'Singer',local_covers:true},
      {id:3,number:3,name:'Three',artist:'Singer',local_covers:true}]};
  };
  h.run('api=reply;radioReadyForViews=true;renderAppleSelection({available:true,items:[{id:"p",kind:"Playlist",name:"List"}]})');
  await h.run('openAlbumTracks("p")');
  assert.equal(h.get('album-tracks-list').children[0].children[1].children[0].src,cover);
  await h.timers.find(t=>t.delay===0).callback();
  assert.equal(calls.at(-1).body.track_id,2);
  assert.equal(h.get('album-tracks-list').children[1].children[1].children[0].src,'api/album-art/12');
  await h.timers.filter(t=>t.delay===4500).at(-1).callback();
  assert.equal(calls.at(-1).body.track_id,3); // Same artist without album must not merge different songs.
  assert.equal(h.run('validAppleImage("api/playlist-art/../options")'),'');
  assert.equal(h.run('validAppleImage("api/playlist-art/"+"A".repeat(64))'),'');
});

test('an album without a search result retains a placeholder and retries after the cooldown', async () => {
  const h=harness(),calls=[];
  h.context.reply=async action=>{calls.push(action);return {selected:null,items:[]};};
  h.run('api=reply;radioReadyForViews=true;renderAppleSelection({available:true,items:[{id:"a",kind:"Album",name:"Unknown"}]})');
  await h.timers.find(t=>t.delay===4500).callback();
  assert.deepEqual(calls,['album-covers']);
  h.run('queueAlbumCovers()');
  assert.equal(h.timers.filter(t=>t.delay===4500).length,1);
  h.run('albumCoverResults.get("a:auto").retryAt=0;queueAlbumCovers()');
  await h.timers.filter(t=>t.delay===4500).at(-1).callback();
  assert.deepEqual(calls,['album-covers','album-covers']);
});

test('a missing local picture is searched again even if an old browser result remains cached', async () => {
  const h=harness(),calls=[];
  h.context.reply=async action=>{calls.push(action);return action==='playlist-cover' ? {image:'api/album-art/13'} :
    {tracks:[{id:1,number:1,name:'Song',artist:'Singer',album:'Album'}]};};
  h.run('api=reply;radioReadyForViews=true;playlistCoverResults.set(playlistCoverKey({name:"Song",artist:"Singer",album:"Album"}),{image:"api/album-art/12",expires:Date.now()+86400000});renderAppleSelection({available:true,items:[{id:"p",kind:"Playlist",name:"List"}]})');
  await h.run('openAlbumTracks("p")');
  await h.timers.find(t=>t.delay===0).callback();
  assert.deepEqual(calls,['playlist-tracks','playlist-cover']);
  assert.equal(h.get('album-tracks-list').children[0].children[1].children[0].src,'api/album-art/13');
});

test('playlist without export keeps whole-playlist playback and offers import', async () => {
  const h=harness();let started;
  h.context.reply=async()=>{throw Error('Bitte eine Titelliste importieren');};
  h.context.start=async id=>{started=id;};
  h.run('api=reply;startAppleFavorite=start;radioReadyForViews=true;renderAppleSelection({available:true,items:[{id:"p",kind:"Playlist",name:"My list"}]})');
  await h.run('openAlbumTracks("p")');
  assert.match(h.get('album-tracks-feedback').textContent,/importieren/);
  assert.equal(h.get('album-play-all').disabled,false);
  await h.get('album-play-all').listeners.click();
  assert.equal(started,'p');
});

test('playlist imports Unicode text into an editable draft and preserves it on failure', async () => {
  const h=harness(), calls=[];
  const imported=[{id:17,number:1,name:'Jóga',artist:'Björk'}];
  h.context.reply=async(action,body)=>{
    calls.push({action,body});
    if(action==='playlist-import')return {tracks:imported};
    if(body)return {selection:{items:[],available:false}};
    return {revision:'r',items:[{kind:'Playlist',name:'My list',command:'spiel playlist My list',tracks:[{name:'Old',artist:'Singer'}]}]};
  };
  h.run('api=reply');await h.run('openLibraryEditor("Playlist")');
  const row=h.run('libraryEditorRows[0]');
  const text='\ufeffName\tArtist\nJóga\tBjörk\n';
  const buffer=Buffer.from(text,'utf16le');
  row.importFile.files=[{size:buffer.length,arrayBuffer:async()=>buffer.buffer.slice(buffer.byteOffset,buffer.byteOffset+buffer.length)}];
  await row.importFile.listeners.change();
  assert.equal(calls[1].action,'playlist-import');
  assert.match(calls[1].body.content,/Jóga\tBjörk/);
  assert.equal(row.tracks,imported);
  assert.equal(h.get('library-editor').open,true);
  h.context.reply=async()=>{throw Error('Bad file');};
  h.run('api=reply');
  await row.importFile.listeners.change();
  assert.equal(row.tracks,imported);
  assert.match(row.importStatus.textContent,/bisherige Titelliste bleibt/);
  h.context.reply=async(action,body)=>{calls.push({action,body});return {selection:{items:[],available:false}};};
  h.run('api=reply');
  await h.run('submitLibraryEditor({preventDefault(){}})');
  assert.equal(calls.at(-1).body.items[0].tracks,imported);
  assert.equal(calls.at(-1).body.items[0].command,'spiel playlist My list');
});

test('track selections coalesce and a late failure cannot overwrite another dialog', async () => {
  const h=albumHarness();let reject,count=0;
  h.context.reply=async(action)=>{
    if(action==='album-tracks')return {tracks:[{id:1,number:1,name:'One'},{id:2,number:2,name:'Two'}]};
    count++;return new Promise((resolve,r)=>{reject=r;});
  };
  h.run('api=reply');await h.run('openAlbumTracks("a")');
  const first=h.get('album-tracks-list').children[0].listeners.click();
  await h.get('album-tracks-list').children[1].listeners.click();
  assert.equal(count,1);
  assert.equal(h.get('album-play-all').disabled,true);
  h.run('closeAlbumTracks()');await h.run('openAlbumTracks("a")');
  reject(Error('Old request'));await first;
  assert.equal(h.get('album-tracks-feedback').textContent,'');
  assert.equal(h.get('album-play-all').disabled,false);
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

test('room slider and percentage show the saved choice despite different HA volume', async () => {
  const h = harness();
  h.context.reply=async()=>({players:[{entity_id:'media_player.wohnzimmer',volume:.5,state:'idle'}],groups:[],remembered:{},saved_levels:{'media_player.wohnzimmer':.2}});
  h.run('api=reply; radioReadyForViews=true');
  await h.run('refreshPlayers()');
  const row = h.get('players').children[0];
  assert.equal(row.children[1].value,20);
  assert.equal(row.children[2].textContent,'20%');
  assert.equal(row.children[2].title,undefined);
});

test('saved mute remains authoritative despite external HA room volume', async () => {
  const h = harness();
  h.context.reply=async()=>({players:[{entity_id:'media_player.wohnzimmer',volume:.4,state:'playing'}],groups:[],remembered:{},saved_levels:{'media_player.wohnung':0,'media_player.wohnzimmer':0}});
  h.run('api=reply; radioReadyForViews=true');
  await h.run('refreshPlayers()');
  assert.equal(h.get('players').children[0].children[2].textContent,'0%');
});

test('individual volume remains independent of master zero after startup', async () => {
  const h = harness();
  h.context.reply=async()=>({players:[{entity_id:'media_player.wohnzimmer',volume:.3,state:'playing'}],groups:[{entity_id:'media_player.wohnung',volume:.7}],remembered:{},saved_levels:{'media_player.wohnung':0,'media_player.wohnzimmer':.3}});
  h.run('api=reply; radioReadyForViews=true');
  await h.run('refreshPlayers()');
  assert.equal(h.get('players').children[0].children[2].textContent,'30%');
  assert.equal(h.get('master-volume').children[0].children[2].textContent,'0%');
});

test('room audio switch sends the exact remembered level rather than stale server state', async () => {
  const h = harness();
  const calls = [];
  h.context.reply = async (action, body) => { calls.push({action,body}); return {volume:0.45}; };
  h.run('api=reply; radioReadyForViews=true; refreshPlayers=async()=>{}');
  const row = h.run('volumeRow({entity_id:"media_player.kueche",name:"Küche",state:"playing",volume:0},{"media_player.kueche":.45},false)');
  assert.equal(row.children[3].textContent,'Stumm');
  await row.children[3].listeners.click();
  assert.equal(calls[0].action,'volume');
  assert.equal(calls[0].body.volume,.45);
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
    saved_levels:{'media_player.wohnung':.3,'media_player.wohnzimmer':.3,'media_player.kueche':0},
    master_room_levels:{'media_player.wohnzimmer':.3,'media_player.kueche':0}};
  h.context.reply=async()=>data;
  h.run('api=(action,body)=>reply(action,body); radioReadyForViews=true');
  await h.run('refreshPlayers()');
  return {...h,data};
}

test('changing Bad only persists its slider and icon while other rooms ignore shared Alexa reports', async () => {
  const h = await masterHarness();
  h.data.players.push({entity_id:'media_player.bad',name:'Bad',volume:.5,state:'playing'});
  h.data.saved_levels['media_player.bad']=.5;
  h.data.saved_levels['media_player.kueche']=.25;
  h.data.master_room_levels['media_player.bad']=.5;
  h.data.master_room_levels['media_player.kueche']=.25;
  await h.run('refreshPlayers()');
  const calls=[];
  h.context.reply=async(action,body)=>{
    if(action==='volume') {
      calls.push(body);
      h.data.saved_levels[body.entity_id]=body.volume;
      h.data.master_room_levels[body.entity_id]=body.volume;
    }
    return h.data;
  };
  const slider=h.get('players').children[2].children[1];
  slider.value=10;await slider.listeners.change();
  assert.equal(calls.length,1);
  assert.equal(calls[0].entity_id,'media_player.bad');
  assert.equal(calls[0].volume,.1);
  for(const external of [.1,.5,0,.8]) {
    h.data.players.forEach(player=>player.volume=external);
    h.run('for (const request of requestedRoomVolumes.values()) request.expires=0');
    await h.run('refreshPlayers()');
    assert.deepEqual(h.get('players').children.map(row=>row.children[1].value),[30,25,10]);
    assert.deepEqual(h.get('players').children.map(row=>row.children[3].attributes['aria-pressed']),['true','true','true']);
    assert.equal(h.get('master-volume').children[0].children[1].value,30);
  }
  h.run('uiGeneration++');await h.run('refreshPlayers()');
  assert.equal(h.get('players').children[2].children[1].value,10);
});

test('Apple library explanatory footer remains hidden', () => {
  const html=fs.readFileSync(path.join(__dirname,'../ha_music/web/index.html'),'utf8');
  assert.match(html, /<p id="apple-library-note" hidden><\/p>/);
});

test('room slider and icon retain the chosen value through delayed and briefly regressing HA replies', async () => {
  const h=await masterHarness();
  let resolve;
  h.context.reply=action=>action==='volume' ? new Promise(r=>{resolve=r;}) : Promise.resolve(h.data);
  const slider=h.get('players').children[0].children[1];
  slider.value=72;
  slider.listeners.input();
  const pending=slider.listeners.change();
  assert.equal(h.get('players').children[0].children[2].textContent,'72%');
  resolve({ok:true});await pending;
  await h.run('refreshPlayers()');
  assert.equal(h.get('players').children[0].children[1].value,72);
  h.data.players[0].volume=.72;
  await h.run('refreshPlayers()');
  h.data.players[0].volume=.3;
  await h.run('refreshPlayers()');
  assert.equal(h.get('players').children[0].children[1].value,72);
  assert.equal(h.get('players').children[0].children[3].attributes['aria-pressed'],'true');
  h.data.players[0].volume=.72;
  h.data.saved_levels['media_player.wohnzimmer']=.72;
  h.run('requestedRoomVolumes.get("media_player.wohnzimmer").settleUntil=0');
  await h.run('refreshPlayers()');
  h.data.players[0].volume=.55;
  await h.run('refreshPlayers()');
  assert.equal(h.get('players').children[0].children[1].value,72);
});

test('room mute and unmute react before the request and keep the exact level despite stale HA state', async () => {
  const h=await masterHarness(), calls=[];
  let resolve;
  h.context.reply=(action,body)=>{
    if(action==='volume') {calls.push(body);return new Promise(r=>{resolve=r;});}
    return Promise.resolve(h.data);
  };
  let row=h.get('players').children[0];
  const mute=row.children[3].listeners.click();
  assert.equal(row.children[1].value,0);
  assert.equal(row.children[2].textContent,'0%');
  assert.equal(row.children[3].attributes['aria-pressed'],'false');
  resolve({ok:true});await mute;await h.run('refreshPlayers()');
  row=h.get('players').children[0];
  assert.equal(row.children[1].value,0);
  assert.equal(h.run('masterRoomLevels["media_player.wohnzimmer"]'),0);
  const unmute=row.children[3].listeners.click();
  assert.equal(row.children[1].value,30);
  assert.equal(row.children[3].attributes['aria-pressed'],'true');
  assert.deepEqual(calls.map(c=>c.volume),[0,.3]);
  h.data.players[0].volume=0;
  resolve({ok:true});await unmute;await h.run('refreshPlayers()');
  assert.equal(h.get('players').children[0].children[1].value,30);
});

test('master slider itself stays at the requested level while saved master reports lag', async () => {
  const h=await masterHarness();
  h.context.reply=async()=>h.data;
  const slider=h.get('master-volume').children[0].children[1];
  slider.value=81;await slider.listeners.change();await h.run('refreshPlayers()');
  assert.equal(h.get('master-volume').children[0].children[1].value,81);
  assert.equal(h.get('players').children[0].children[1].value,81);
  assert.equal(h.get('players').children[1].children[1].value,0);
});

test('polling cannot rebuild a slider while it is being dragged and old edits do not block a new generation', async () => {
  const h=await masterHarness();
  let resolve;
  h.context.reply=()=>new Promise(r=>{resolve=r;});
  const pending=h.run('refreshPlayers()');
  const row=h.get('players').children[0];
  row.children[1].value=44;row.children[1].listeners.input();
  resolve(h.data);await pending;
  assert.equal(h.get('players').children[0],row);
  assert.equal(row.children[2].textContent,'44%');
  h.context.reply=async()=>h.data;
  await h.run('refreshPlayers()');
  assert.equal(h.get('players').children[0],row);
  h.run('uiGeneration++');await h.run('refreshPlayers()');
  assert.equal(h.run('volumeEditing.size'),0);
  assert.equal(h.get('players').children[0].children[1].value,30);
});

test('failed room mute rolls back both icon and slider rather than pretending success', async () => {
  const h=await masterHarness();
  h.context.reply=async action=>{if(action==='volume')throw new Error('offline');return h.data;};
  await h.get('players').children[0].children[3].listeners.click();
  await h.run('refreshPlayers()');
  assert.equal(h.get('players').children[0].children[1].value,30);
  assert.equal(h.get('players').children[0].children[3].attributes['aria-pressed'],'true');
  assert.equal(h.run('requestedRoomVolumes.size'),0);
});

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

test('master preview settles on saved values and ignores later external volume', async () => {
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
  h.data.saved_levels['media_player.wohnung']=.6;
  h.data.saved_levels['media_player.wohnzimmer']=.6;
  h.run('for (const request of requestedRoomVolumes.values()) request.settleUntil=0');
  await h.run('refreshPlayers()');
  assert.equal(h.run('requestedRoomVolumes.size'),0);
  h.data.players[0].volume=.45;
  await h.run('refreshPlayers()');
  assert.equal(h.get('players').children[0].children[1].value,60);
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

test('failed master request rolls room preview back to saved values', async () => {
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
  assert.equal(h.run('requestedRoomVolumes.get("media_player.wohnzimmer").level'),.35);
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

test('shuffle click immediately shows explicit desired state without waiting for player confirmation', async () => {
  const h = harness();
  let resolve;
  let desired;
  h.context.reply=(action,body)=>{desired=body.shuffle;return new Promise(r=>{resolve=r;});};
  h.run('api=reply; radioReadyForViews=true; trackTransport={entity_id:"media_player.wohnung",can_shuffle:true,shuffle:false}; renderTrackTransport(); loadRadioState=async()=>{}; updateSong=async()=>{}');
  const request=h.get('track-shuffle').listeners.click();
  assert.equal(desired,true);
  assert.equal(h.get('track-shuffle').attributes['aria-pressed'],'true');
  resolve({ok:true});
  await request;
  assert.equal(h.get('track-shuffle').attributes['aria-pressed'],'true');
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

test('active shuffle immediately becomes dim and ignores contrary player reports', async () => {
  const h = harness();
  const calls=[];
  h.context.reply=async(action,body)=>{calls.push({action,body});return {ok:true};};
  h.context.playback=async()=>({playing:true,track_transport:{entity_id:'media_player.wohnung',can_shuffle:true,shuffle:true}});
  h.run('const originalUpdateSong = updateSong');
  h.run('api=reply; radioReadyForViews=true; trackTransport={entity_id:"media_player.wohnung",can_shuffle:true,shuffle:true}; renderTrackTransport(); loadRadioState=async()=>{}; updateSong=async()=>{}');
  assert.equal(h.get('track-shuffle').classList.contains('active'),true);
  await h.run('controlTrack("shuffle")');
  assert.equal(calls.length,1);
  assert.equal(calls[0].action,'track_transport');
  assert.equal(calls[0].body.shuffle,false);
  assert.equal(h.get('track-shuffle').attributes['data-shuffle-state'],'off');
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

test('play pause icon flips before request completion and ignores stale player state', async () => {
  const h=harness();
  let resolve;
  const row=h.run('volumeRow({entity_id:"media_player.wohnung",volume:.3},{},true)');
  const button=row.children[3].children[1];
  h.context.reply=action=>action==='group_transport' ? new Promise(r=>{resolve=r;}) : Promise.resolve({transport:{state:'playing',available:true,supports_play:true,supports_pause:true,can_pause:true,can_play:false}});
  h.run('api=reply; radioReadyForViews=true; groupTransport={state:"playing",available:true,supports_play:true,supports_pause:true,can_pause:true}; renderGroupTransport()');
  const pause=button.listeners.click();
  assert.match(button.innerHTML,/m8 5 11 7-11 7Z/);
  assert.equal(h.run('transportIntent.state'),'paused');
  resolve({ok:true});await pause;await h.run('updateSong()');
  assert.match(button.innerHTML,/m8 5 11 7-11 7Z/);
  assert.equal(button.disabled,false);
  const play=button.listeners.click();
  assert.match(button.innerHTML,/M8 5v14M16 5v14/);
  resolve({ok:true});await play;
  assert.equal(h.run('transportIntent.state'),'playing');
});

test('saved transport choices survive fresh browser load while player reports the opposite', async () => {
  const h=harness();
  const row=h.run('volumeRow({entity_id:"media_player.wohnung",volume:.3},{},true)');
  h.context.reply=async()=>({control_intent:{state:'paused',shuffle:true},transport:{state:'playing',available:true,supports_play:true,supports_pause:true},track_transport:{entity_id:'media_player.wohnung',can_shuffle:true,shuffle:false}});
  h.run('api=reply; radioReadyForViews=true');
  await h.run('updateSong()');
  assert.match(row.children[3].children[1].innerHTML,/m8 5 11 7-11 7Z/);
  assert.equal(h.get('track-shuffle').attributes['aria-pressed'],'true');
  h.context.reply=async()=>({transport:{state:'playing',available:true,supports_play:true,supports_pause:true},track_transport:{entity_id:'media_player.wohnung',can_shuffle:true,shuffle:false}});
  await h.run('updateSong()');
  assert.equal(h.run('transportIntent.state'),'paused');
  assert.equal(h.get('track-shuffle').attributes['aria-pressed'],'true');
});

test('failed play pause or shuffle rolls back the immediate icon and reports the failure', async () => {
  const h=harness();
  const row=h.run('volumeRow({entity_id:"media_player.wohnung",volume:.3},{},true)');
  h.context.reply=async()=>{throw new Error('offline');};
  h.run('api=reply; radioReadyForViews=true; groupTransport={state:"playing",can_pause:true}; trackTransport={entity_id:"media_player.wohnung",can_shuffle:true,shuffle:false}; updateSong=async()=>{}; loadRadioState=async()=>{}; renderGroupTransport()');
  await h.run('controlGroup("pause")');
  assert.match(row.children[3].children[1].innerHTML,/M8 5v14M16 5v14/);
  assert.equal(h.run('transportIntent.state'),undefined);
  await h.run('controlTrack("shuffle")');
  assert.equal(h.get('track-shuffle').attributes['aria-pressed'],'false');
  assert.equal(h.run('transportIntent.shuffle'),undefined);
  assert.match(h.get('playback-state').textContent,/offline/);
});

test('standby removes unaccepted transport preview and late failure cannot restore it', async () => {
  const h=harness();
  let reject;
  h.context.reply=()=>new Promise((_,r)=>{reject=r;});
  h.run('api=reply; radioReadyForViews=true; transportIntent={state:"playing",shuffle:false}; groupTransport={state:"playing",can_pause:true}; updateSong=async()=>{}');
  const request=h.run('controlGroup("pause")');
  assert.equal(h.run('transportIntent.state'),'paused');
  h.run('uiGeneration++; radioReadyForViews=false; displayRadioReadiness(false)');
  assert.equal(h.run('transportIntent.state'),'playing');
  reject(new Error('cancelled'));await request;
  assert.equal(h.run('transportIntent.state'),'playing');
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
  assert.equal(calls[0].action,'playlist-tracks');
  assert.equal(h.get('album-tracks-dialog').open,true);
  await h.get('album-play-all').listeners.click();
  assert.equal(calls[1].action,'apple_music');
  assert.equal(calls[1].body.favorite,'one');
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
  assert.equal(h.get('current-artist').textContent,'Apple Music');
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


test('accepted album uses saved artwork before any Alexa status and retains it through delayed radio metadata', async()=>{
 const h=albumHarness();
 h.context.reply=async()=>({ok:true});
 h.run('api=reply;loadRadioState=async()=>{};updateSongOriginal=updateSong;updateSong=async()=>{};selectedStation="wdr2";updateStationLogo("wdr2")');
 await h.run('startAppleFavorite("a")');
 assert.equal(h.get('current-cover').src,'api/album-art/12');
 assert.equal(h.get('current-cover').hidden,false);
 assert.equal(h.get('.now').classList.contains('radio-selected'),false);
 h.context.reply=async()=>({details:{image:'wdr2.svg',content_type:'radio'}});
 h.run('updateSong=updateSongOriginal');await h.run('updateSong()');
 assert.equal(h.get('current-cover').src,'api/album-art/12');
 assert.equal(h.get('current-cover').hidden,false);
 h.context.reply=async()=>({details:{image:'https://example.test/old.jpg'},apple_verification:{status:'pending',reason:'Waiting'}});
 await h.run('updateSong()');assert.equal(h.get('current-cover').src,'api/album-art/12');
});

test('same playlist track change replaces its artwork and never inherits the previous album',async()=>{
 const h=harness();let active={id:'p',kind:'Track',name:'First',now_artwork:{image:'api/playlist-art/'+'a'.repeat(64)}};
 h.context.reply=async()=>({stations:[],power:'on',ready:'on',apple_music:{available:true,items:[],active}});
 h.run('api=reply;radioReadyForViews=true;refreshPlayers=async()=>{};updateStationLogo("wdr2")');
 await h.run('loadRadioState()');assert.equal(h.get('current-cover').src,active.now_artwork.image);
 active={...active,name:'Second',now_artwork:{image:'api/album-art/42'}};
 await h.run('loadRadioState()');assert.equal(h.get('current-title').textContent,'Second');assert.equal(h.get('current-cover').src,'api/album-art/42');
 active={...active,name:'Missing',now_artwork:{image:''},artwork:{image:'api/album-art/9'}};
 await h.run('loadRadioState()');assert.equal(h.get('current-cover').hidden,true);
 assert.equal(h.get('.now').classList.contains('radio-selected'),false);
});

test('cached cover fallback preserves Alexa artwork for Amazon and unverified albums without covers',async()=>{
 const h=harness();h.context.reply=async()=>({details:{title:'Song',image:'https://example.test/current.jpg'},apple_verification:{status:'pending'}});
 h.run('api=reply;radioReadyForViews=true;activeApple={id:"a",kind:"Album",name:"Album"}');
 await h.run('updateSong()');assert.equal(h.get('current-cover').src,'https://example.test/current.jpg');
 h.run('activeApple=null;selectedStation="charts"');await h.run('updateSong()');assert.equal(h.get('current-cover').src,'https://example.test/current.jpg');
});

test('Charts rejects delayed broadcast metadata including ingress and absolute station logos',async()=>{
 const h=harness();h.run('radioReadyForViews=true;selectedStation="charts";activeApple=null');
 for(const details of [
  {title:'WDR 2',image:'wdr2.svg'},
  {title:'WDR 2',image:'/api/hassio_ingress/session/wdr2.svg?cache=1'},
  {title:'SWR3',image:'https://ha.test/api/hassio_ingress/session/swr3.svg'},
  {title:'Old radio',image:'https://example.test/station.jpg',content_type:'radio'},
  {title:'Old channel',image:'https://example.test/station.jpg',content_type:'channel'}
 ]){
  h.context.reply=async()=>({details});h.run('api=reply');await h.run('updateSong()');
  assert.equal(h.get('current-cover').hidden,true);assert.equal(h.get('now-ticker').hidden,true);
 }
 h.context.reply=async()=>({details:{title:'Song',artist:'Artist',image:'https://example.test/music.jpg',content_type:'music'}});
 h.run('api=reply');await h.run('updateSong()');assert.equal(h.get('current-cover').src,'https://example.test/music.jpg');
});


test('browsing Apple and Radio keeps the playing station branding and clears leftover artwork padding',()=>{
 const h=harness();
 h.run('radioReadyForViews=true;selectedStation="1live";updateStationLogo(selectedStation)');
 const image=h.get('current-cover');
 image.style.padding='28px';image.style.objectFit='cover';
 h.get('.now').classList.remove('radio-selected');
 for(const view of ['apple','radio','apple']) {
   h.run(`show("${view}")`);
   assert.equal(image.src,'1live.svg');assert.equal(image.hidden,false);
   assert.equal(image.style.padding,'0px');assert.equal(image.style.objectFit,'contain');
   assert.equal(h.get('.now').classList.contains('radio-selected'),true);
   assert.equal(h.run('selectedStation'),'1live');assert.equal(h.run('activeApple'),null);
 }
});

test('radio to album transition replaces source geometry and browsing does not restore a radio logo',()=>{
 const h=albumHarness();
 h.run('selectedStation="wdr2";updateStationLogo(selectedStation);activeApple=appleSelection.items[0];selectedStation="";showAppleArtwork();show("radio");show("apple")');
 assert.equal(h.get('current-cover').src,'api/album-art/12');
 assert.equal(h.get('current-cover').style.objectFit,'cover');
 assert.equal(h.get('current-cover').style.padding,'0px');
 assert.equal(h.get('.now').classList.contains('radio-selected'),false);
});


test('album artwork previews at click and stale state or Alexa mismatch cannot restore the old cover',async()=>{
 const h=albumHarness();let accept,stateReply;
 const old={id:'old',kind:'Album',name:'Old',album_id:9,artwork:{image:'api/album-art/9'}};
 h.context.old=old;
 h.context.reply=action=> action==='apple_music' ? new Promise(r=>{accept=r;}) : action==='radio-state' ? new Promise(r=>{stateReply=r;}) : Promise.resolve({details:{image:'https://example.test/old.jpg'},apple_verification:{status:'mismatch',reason:'Old metadata'}});
 h.run('api=reply;activeApple=old;showAppleArtwork();refreshPlayers=async()=>{}');
 const request=h.run('startAppleFavorite("a")');
 assert.equal(h.get('current-cover').src,'api/album-art/12');assert.equal(h.get('current-title').textContent,'Album');
 const state=h.run('loadRadioState()');
 await h.run('updateSong()');assert.equal(h.get('current-cover').src,'api/album-art/12');
 accept({ok:true});await request;
 stateReply({stations:[],power:'on',ready:'on',selected_view:'apple',apple_music:{items:[],available:true,active:old}});await state;
 assert.equal(h.run('activeApple.id'),'a');assert.equal(h.get('current-cover').src,'api/album-art/12');
 await h.run('updateSong()');assert.equal(h.get('current-cover').src,'api/album-art/12');
 assert.equal(h.get('current-cover').style.padding,'0px');assert.equal(h.get('current-cover').style.objectFit,'cover');
});

test('failed album request restores prior artwork rather than leaving its preview',async()=>{
 const h=albumHarness();let reject;
 h.context.reply=()=>new Promise((_,r)=>{reject=r;});
 h.run('api=reply;loadRadioState=async()=>{};updateSong=async()=>{};selectedStation="1live";updateStationLogo(selectedStation)');
 h.get('current-cover').setAttribute('src','1live.svg');
 const pending=h.run('startAppleFavorite("a")');assert.equal(h.get('current-cover').src,'api/album-art/12');
 reject(new Error('offline'));await pending;
 assert.equal(h.get('current-cover').src,'1live.svg');assert.equal(h.get('current-cover').style.objectFit,'contain');assert.equal(h.run('activeApple'),null);
});


test('new cover stays hidden until loaded and delayed previous image cannot flash back',()=>{
 const h=harness(),cover=h.get('current-cover');
 Object.defineProperty(cover,'src',{get(){return this.attributes.src;},set(value){this.attributes.src=value;}});
 h.run('setNowArtwork("api/album-art/1","First")');const firstLoad=cover.onload;
 h.run('setNowArtwork("api/album-art/2","Second")');
 assert.equal(cover.hidden,true);firstLoad();assert.equal(cover.hidden,true);
 h.run('setNowArtwork("api/album-art/2","Second")');assert.equal(cover.hidden,true);
 cover.onload();assert.equal(cover.hidden,false);assert.equal(cover.src,'api/album-art/2');
});


test('changing views while an album starts does not discard the accepted source',async()=>{
 const h=albumHarness();let accept;
 h.context.reply=action=>action==='apple_music'?new Promise(r=>{accept=r;}):Promise.resolve({ok:true});
 h.run('api=reply;loadRadioState=async()=>{};updateSong=async()=>{}');
 const pending=h.run('startAppleFavorite("a")');await h.run('selectView("radio")');accept({ok:true});await pending;
 assert.equal(h.run('activeApple.id'),'a');assert.equal(h.get('current-cover').src,'api/album-art/12');
});

test('changing views does not suppress a failed transport rollback',async()=>{
 const h=harness();let reject;
 h.context.reply=action=>action==='group_transport'?new Promise((_,r)=>{reject=r;}):Promise.resolve({ok:true});
 h.run('api=reply;radioReadyForViews=true;groupTransport={state:"playing",can_pause:true};loadRadioState=async()=>{};updateSong=async()=>{}');
 const pending=h.run('controlGroup("pause")');await h.run('selectView("apple")');reject(new Error('offline'));await pending;
 assert.equal(h.run('transportIntent.state'),undefined);assert.match(h.get('playback-state').textContent,/offline/);
});

test('state requested before view change cannot move the view back afterward',async()=>{
 const h=harness();let finish;
 h.context.reply=action=>action==='radio-state'?new Promise(r=>{finish=r;}):Promise.resolve({ok:true});
 h.run('api=reply;radioReadyForViews=true;refreshPlayers=async()=>{};updateSong=async()=>{}');
 const state=h.run('loadRadioState()');await h.run('selectView("apple")');
 finish({stations:[],power:'on',ready:'on',selected_view:'radio',apple_music:{items:[],available:true}});await state;
 assert.equal(h.run('preferredView'),'apple');assert.equal(h.get('apple-page').hidden,false);
});

test('standby closes pending title dialog and late acceptance cannot change local play state',async()=>{
 const h=albumHarness();let accept;
 h.context.reply=action=>action==='album-tracks'?Promise.resolve({tracks:[{id:9,number:1,name:'Song'}]}):new Promise(r=>{accept=r;});
 h.run('api=reply;loadRadioState=async()=>{};updateSong=async()=>{};transportIntent.state="paused"');await h.run('openAlbumTracks("a")');
 const pending=h.get('album-tracks-list').children[0].listeners.click();
 h.run('uiGeneration++;radioReadyForViews=false;strictStandby=true;displayRadioReadiness(false)');
 accept({ok:true});await pending;
 assert.equal(h.get('album-tracks-dialog').open,false);assert.equal(h.run('transportIntent.state'),'paused');
});


test('failed artwork stays a placeholder across repeated renders',()=>{
 const h=harness(),cover=h.get('current-cover');
 h.run('setNowArtwork("api/album-art/1","Album")');assert.equal(cover.hidden,false);
 cover.onerror();h.run('setNowArtwork("api/album-art/1","Album")');assert.equal(cover.hidden,true);
});
