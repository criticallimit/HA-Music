const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const vm = require('vm');
const path = require('path');

function embeddedLayout(embedded = true) {
  const app = fs.readFileSync(path.join(__dirname,'../ha_music/web/app.js'),'utf8');
  const reporter = app.slice(app.indexOf('// Fit both libraries'),app.indexOf('const $ ='));
  const styles = () => ({values:{},setProperty(k,v){this.values[k]=v;}});
  const classes = () => ({values:new Set(),add(k){this.values.add(k);},toggle(k,on){if(on)this.values.add(k);else this.values.delete(k);}});
  const rect = (height,top=80,width=600) => ({height,top,width});
  const grids = [150,150].map(clientHeight=>({clientHeight,style:styles()}));
  const library = {hidden:false,style:styles(),classList:classes(),querySelectorAll:()=>grids};
  const artwork = {hidden:false,getBoundingClientRect:()=>rect(720)};
  const master = {hidden:false,getBoundingClientRect:()=>rect(90)};
  const players = {hidden:false,getBoundingClientRect:()=>rect(230)};
  const absolute = {hidden:false,position:'absolute',getBoundingClientRect:()=>rect(1)};
  const probes = [];
  const stations = {hidden:true,cloneNode(){
    const probe = {hidden:true,style:{},attributes:{id:'station-list'},querySelectorAll:()=>[],
      removeAttribute(k){delete this.attributes[k];},setAttribute(k,v){this.attributes[k]=v;},
      getBoundingClientRect:()=>rect(80),remove(){this.removed=true;}};
    probes.push(probe);return probe;
  }};
  const right = {hidden:false,children:[master,players,absolute,stations,library],getBoundingClientRect:()=>rect(720)};
  const shell = {getBoundingClientRect:()=>rect(806)};
  const root = {style:styles(),classList:classes()};
  const messages = [],frames = [],events = {},observations = [];
  const parent = {postMessage(message){messages.push(message);}};
  const window = {parent,location:{search:'?ha_music_card=1',origin:'http://ha.test'},frameElement:null,
    addEventListener(name,handler){events[name]=handler;}};
  if (!embedded) { window.parent = window; window.location.search = ""; }
  class ResizeObserver {constructor(callback){this.callback=callback;}observe(node){observations.push({type:'resize',node,callback:this.callback});}}
  class MutationObserver {constructor(callback){this.callback=callback;}observe(node,options){observations.push({type:'mutation',node,options,callback:this.callback});}}
  window.ResizeObserver=ResizeObserver;window.MutationObserver=MutationObserver;
  const context=vm.createContext({window,URLSearchParams,ResizeObserver,MutationObserver,
    requestAnimationFrame(callback){frames.push(callback);return frames.length;},
    getComputedStyle(node){return {rowGap:'8px',display:node.hidden?'none':'flex',position:node.position||'static'};},
    document:{documentElement:root,body:{appendChild(probe){probe.appended=true;}},
      getElementById:id=>({'apple-library':library,'station-list':stations}[id]),
      querySelector:selector=>({'.shell':shell,'.dashboard-right':right,'.now':artwork}[selector])}});
  vm.runInContext(reporter,context);
  const flush=()=>{const pending=frames.splice(0);pending.forEach(callback=>callback());};
  return {library,artwork,right,shell,master,players,stations,probes,root,messages,frames,events,observations,parent,flush};
}

test('Apple library uses the radio geometry without changing live controls or hidden stations',()=>{
  const h=embeddedLayout();h.flush();
  assert.equal(h.library.style.values['--ha-library-height'],'384px');
  assert.equal(h.library.classList.values.has('ha-library-compact'),false);
  assert.equal(h.stations.hidden,true);
  assert.equal(h.right.children.length,5);
  assert.equal(h.probes.length,1);
  assert.equal(h.probes[0].attributes.id,undefined);
  assert.equal(h.probes[0].attributes['aria-hidden'],'true');
  assert.equal(h.probes[0].inert,true);
  assert.equal(h.probes[0].removed,true);
  assert.equal(h.messages[0].height,806);
});

test('Mobile and tall speaker stacks keep both libraries inside the radio footer budget',()=>{
  const h=embeddedLayout();h.right.getBoundingClientRect=()=>({top:400,width:390,height:410});h.flush();
  assert.equal(h.library.style.values['--ha-library-height'],'80px');
  assert.equal(h.library.classList.values.has('ha-library-compact'),true);
  h.right.getBoundingClientRect=()=>({top:80,width:600,height:1100});
  h.players.getBoundingClientRect=()=>({height:900});
  h.events.resize();h.flush();
  assert.equal(h.library.style.values['--ha-library-height'],'80px');
});

test('View switches and imported rows refit even when shell height is unchanged',()=>{
  const h=embeddedLayout();h.library.hidden=true;h.flush();
  assert.equal(h.probes.length,0);
  const mutation=h.observations.find(item=>item.type==='mutation');
  assert.equal(mutation.options.childList,true);
  assert.deepEqual(Array.from(mutation.options.attributeFilter),['hidden']);
  h.library.hidden=false;mutation.callback();mutation.callback();h.flush();
  assert.equal(h.probes.length,1);
  assert.equal(h.library.style.values['--ha-library-height'],'384px');
  assert.equal(h.messages.length,1,'Height notifications stay deduplicated');
  h.artwork.hidden=true;mutation.callback();h.flush();
  assert.equal(h.probes.length,1,'Standby must not size hidden panels');
});

test('Available height messages accept only the parent and matching origin',()=>{
  const h=embeddedLayout();h.flush();
  const message=(source,origin,height)=>h.events.message({source,origin,data:{type:'ha-music-available-height',height}});
  message({},'http://ha.test',400);message(h.parent,'http://evil.test',400);
  assert.equal(h.frames.length,0);
  message(h.parent,'http://ha.test',400);h.flush();
  assert.equal(h.root.style.values['--ha-card-available-height'],'400px');
  message(h.parent,'http://ha.test',Infinity);h.flush();
  assert.equal(h.root.style.values['--ha-card-available-height'],'400px');
  message(h.parent,'http://ha.test',10001);h.flush();
  assert.equal(h.root.style.values['--ha-card-available-height'],'400px');
});


test('embedded album art and radio logos scale without cropping or padding',()=>{
 const css=fs.readFileSync(path.join(__dirname,'../ha_music/web/style.css'),'utf8');
 const declarations=selector=>Array.from(css.matchAll(/([^{}]+)\{([^{}]+)\}/g)).filter(match=>match[1].trim()===selector).at(-1)?.[2];
 assert.match(declarations('html.ha-dashboard-fit .now:not(.radio-selected) .art img:not([hidden])'),/object-fit:contain;padding:0/);
 assert.match(declarations('.now.radio-selected .art img:not([hidden])'),/object-fit:contain;padding:0/);
});


test('short libraries scroll without introducing another tile size',()=>{
 const h=embeddedLayout();h.flush();
 h.right.getBoundingClientRect=()=>({top:400,width:390,height:410});h.events.resize();h.flush();
 assert.equal(h.library.style.values['--ha-library-height'],'80px');
 assert.equal(h.library.style.values['--ha-library-square-size'],undefined);
 const css=fs.readFileSync(path.join(__dirname,'../ha_music/web/style.css'),'utf8');
 assert.equal(css.includes('--ha-library-tile-height'),false);
 assert.match(css,/#apple-playlist-list \.station\.apple-playlist,#apple-album-list \.station\.apple-album\{aspect-ratio:1 \/ 1;height:120px;width:120px/);
 assert.match(css,/#apple-album-list \.apple-album-cover\{width:100%;height:100%;object-fit:contain/);
});


test('Ingress uses the same library budget without Lovelace-only styling or parent messages',()=>{
 const h=embeddedLayout(false);h.flush();
 assert.equal(h.library.style.values['--ha-library-height'],'384px');
 assert.equal(h.root.classList.values.has('ha-dashboard-fit'),false);
 assert.equal(h.messages.length,0);
});
