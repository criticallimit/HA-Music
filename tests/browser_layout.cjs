const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const web=path.join(__dirname,'../ha_music/web');
const artwork='<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300"><rect width="300" height="300" fill="#cda5b4"/><rect x="4" y="4" width="292" height="292" fill="none" stroke="#243746" stroke-width="8"/></svg>';
(async()=>{
 const server=http.createServer((req,res)=>{
  const file=new URL(req.url,'http://localhost').pathname;
  if(file==='/card-test'){
   res.writeHead(200,{'Content-Type':'text/html'});
   res.end(`<style>body{margin:0}ha-music-card{display:block}</style><script src="/card.js"></script><ha-music-card></ha-music-card><script>const card=document.querySelector('ha-music-card');card.shadowRoot.innerHTML='<ha-card style="display:block;overflow:hidden"><iframe style="border:0" src="/?ha_music_card=1"></iframe></ha-card>';card._iframe=card.shadowRoot.querySelector('iframe');card._applyDimensions();card._iframe.addEventListener('load',()=>{card._lastFitIframe=null;card._applyDimensions()});</script>`);return;
  }
  if(file==='/card.js'){res.writeHead(200,{'Content-Type':'application/javascript'});res.end(fs.readFileSync(path.join(web,'../lovelace/ha-music-card.js')));return;}
  if(file.startsWith('/api/album-art/')){res.writeHead(200,{'Content-Type':'image/svg+xml'});res.end(artwork);return;}
  const name=file==='/'?'index.html':file.slice(1);
  if(!['index.html','style.css','app.js','1live.svg','wdr2.svg','swr3.svg'].includes(name)){res.writeHead(404);res.end();return;}
  let source=fs.readFileSync(path.join(web,name));
  if(name==='app.js')source=Buffer.from(source.toString().replace('\nrefresh();','\n'));
  res.writeHead(200,{'Content-Type':name.endsWith('.css')?'text/css':name.endsWith('.js')?'application/javascript':name.endsWith('.svg')?'image/svg+xml':'text/html'});res.end(source);
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 let browser;
 try{
  browser=await chromium.launch({headless:true,...(process.env.HA_MUSIC_BROWSER?{executablePath:process.env.HA_MUSIC_BROWSER}:{})});
  for(const mode of ["ingress","card","embedded-card"]) for(const width of [1200,390]){
   const page=await browser.newPage({viewport:{width,height:900}});
   const errors=[];page.on('pageerror',error=>errors.push(error.message));
   await page.goto('http://127.0.0.1:'+server.address().port+(mode==='embedded-card'?'/card-test':''));
   if(mode==='embedded-card')await page.waitForFunction(()=>document.querySelector('ha-music-card')._iframe.contentDocument?.getElementById('apple-album-list'));
   const surface=mode==='embedded-card'?page.frames().find(frame=>frame.parentFrame()):page;
   await surface.evaluate(mode=>{
    api=async()=>({});radioReadyForViews=true;
    document.documentElement.classList.toggle('ha-dashboard-fit',mode!=='ingress');
    document.getElementById('radio-standby').hidden=true;
    const items=Array.from({length:20},(_,i)=>({id:'p'+i,kind:'Playlist',name:'Playlist '+i})).concat(Array.from({length:20},(_,i)=>({id:'a'+i,kind:'Album',name:'Album '+i,album_id:i+1,artwork:{image:'api/album-art/'+(i+1)}})));
    renderAppleSelection({available:true,items});show('apple');displayRadioReadiness(true);
    activeApple=items[20];showAppleArtwork();
   },mode);
   await surface.waitForFunction(()=>!document.getElementById('current-cover').hidden);
   for(const height of [360,160,80]){
    await surface.evaluate(height=>{const library=document.getElementById('apple-library');library.style.height=height+'px';library.classList.toggle('ha-library-compact',height<200);},height);
    const result=await surface.evaluate(()=>{
     const lists=['apple-playlist-list','apple-album-list'].map(id=>{const list=document.getElementById(id);return {id,height:list.clientHeight,scroll:list.scrollHeight,tiles:Array.from(list.querySelectorAll('.apple-favorite')).map(tile=>{const r=tile.getBoundingClientRect();return {width:r.width,height:r.height};})};});
     const image=document.getElementById('current-cover'),r=image.getBoundingClientRect(),art=document.querySelector('.art').getBoundingClientRect(),style=getComputedStyle(image);
     return {lists,art:{width:art.width,height:art.height},image:{width:r.width,height:r.height,padding:style.padding,fit:style.objectFit},albumFits:Array.from(document.querySelectorAll('.apple-album-cover')).map(img=>getComputedStyle(img).objectFit)};
    });
    for(const list of result.lists){assert.equal(list.tiles.length,20);assert.ok(list.scroll>list.height);for(const tile of list.tiles){assert.equal(tile.width,120);assert.equal(tile.height,120);}}
    assert.equal(result.image.width,result.art.width);assert.equal(result.image.height,result.art.height);assert.equal(result.image.padding,'0px');assert.equal(result.image.fit,'cover');assert.ok(result.albumFits.every(fit=>fit==='contain'));
    for(const id of ['apple-playlist-list','apple-album-list']){await surface.locator('#'+id).evaluate(list=>{list.scrollTop=list.scrollHeight;});assert.ok(await surface.locator('#'+id).evaluate(list=>list.scrollTop>0));}
    console.log('PASS: '+mode+', viewport '+width+', library height '+height+', equal 120px squares, independent scrolling, full artwork');
   }
   await surface.evaluate(async()=>{
    activeApple=null;selectedStation='wdr2';updateStationLogo(selectedStation);show('radio');
    api=async()=>({details:{title:'Old programme',artist:'Presenter',image:'/api/album-art/98',content_type:'music'}});
    await updateSong();
    window.frameBefore=document.querySelector('.art').getBoundingClientRect().toJSON();
    show('apple');show('radio');
    selectedStation='charts';updateStationLogo(selectedStation);
    api=async()=>({details:{title:'WDR 2',image:'/api/hassio_ingress/session/wdr2.svg?old=1',content_type:'music'}});
    await updateSong();
   });
   assert.ok(await surface.locator('#current-cover').evaluate(image=>image.hidden));
   await surface.evaluate(async()=>{api=async()=>({details:{title:'Old programme',artist:'Presenter',image:'/api/album-art/98',content_type:'music'}});await updateSong();await updateSong();});
   assert.ok(await surface.locator('#current-cover').evaluate(image=>image.hidden));
   await surface.evaluate(async()=>{api=async()=>({details:{title:'Lush Life',artist:'Zara Larsson',image:'/api/album-art/99',content_type:'music'}});await updateSong();});
   await surface.waitForFunction(()=>!document.getElementById('current-cover').hidden);
   const geometry=await surface.evaluate(()=>{const image=document.getElementById('current-cover'),art=document.querySelector('.art');return {before:window.frameBefore,after:art.getBoundingClientRect().toJSON(),image:image.getBoundingClientRect().toJSON(),padding:getComputedStyle(image).padding};});
   assert.equal(geometry.after.width,geometry.before.width);assert.equal(geometry.after.height,geometry.before.height);
   assert.equal(geometry.image.width,geometry.after.width);assert.equal(geometry.image.height,geometry.after.height);assert.equal(geometry.padding,'0px');
   console.log('PASS: '+mode+', WDR2 → Charts, stale logo rejected and artwork geometry preserved across views');
   assert.deepEqual(errors,[]);await page.close();
  }
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
