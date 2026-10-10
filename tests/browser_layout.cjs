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
  for(const mode of ["ingress","card"]) for(const width of [1200,390]){
   const page=await browser.newPage({viewport:{width,height:900}});
   const errors=[];page.on('pageerror',error=>errors.push(error.message));
   await page.goto('http://127.0.0.1:'+server.address().port);
   await page.evaluate(mode=>{
    api=async()=>({});radioReadyForViews=true;
    document.documentElement.classList.toggle('ha-dashboard-fit',mode==='card');
    document.getElementById('radio-standby').hidden=true;
    const items=Array.from({length:20},(_,i)=>({id:'p'+i,kind:'Playlist',name:'Playlist '+i})).concat(Array.from({length:20},(_,i)=>({id:'a'+i,kind:'Album',name:'Album '+i,album_id:i+1,artwork:{image:'api/album-art/'+(i+1)}})));
    renderAppleSelection({available:true,items});show('apple');displayRadioReadiness(true);
    activeApple=items[20];showAppleArtwork();
   },mode);
   await page.waitForFunction(()=>!document.getElementById('current-cover').hidden);
   for(const height of [360,160,80]){
    await page.evaluate(height=>{const library=document.getElementById('apple-library');library.style.height=height+'px';library.classList.toggle('ha-library-compact',height<200);},height);
    const result=await page.evaluate(()=>{
     const lists=['apple-playlist-list','apple-album-list'].map(id=>{const list=document.getElementById(id);return {id,height:list.clientHeight,scroll:list.scrollHeight,tiles:Array.from(list.querySelectorAll('.apple-favorite')).map(tile=>{const r=tile.getBoundingClientRect();return {width:r.width,height:r.height};})};});
     const image=document.getElementById('current-cover'),r=image.getBoundingClientRect(),art=document.querySelector('.art').getBoundingClientRect(),style=getComputedStyle(image);
     return {lists,art:{width:art.width,height:art.height},image:{width:r.width,height:r.height,padding:style.padding,fit:style.objectFit},albumFits:Array.from(document.querySelectorAll('.apple-album-cover')).map(img=>getComputedStyle(img).objectFit)};
    });
    for(const list of result.lists){assert.equal(list.tiles.length,20);assert.ok(list.scroll>list.height);for(const tile of list.tiles){assert.equal(tile.width,120);assert.equal(tile.height,120);}}
    assert.equal(result.image.width,result.art.width);assert.equal(result.image.height,result.art.height);assert.equal(result.image.padding,'0px');assert.equal(result.image.fit,'cover');assert.ok(result.albumFits.every(fit=>fit==='contain'));
    for(const id of ['apple-playlist-list','apple-album-list']){await page.locator('#'+id).evaluate(list=>{list.scrollTop=list.scrollHeight;});assert.ok(await page.locator('#'+id).evaluate(list=>list.scrollTop>0));}
    console.log('PASS: '+mode+', viewport '+width+', library height '+height+', equal 120px squares, independent scrolling, full artwork');
   }
   assert.deepEqual(errors,[]);await page.close();
  }
 }finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
