// Optional local companion to Playwright: npm install jsdom@26; node tests/dashboard_dom.cjs PATCHED_SOURCE
// Executes the production HTML/JS, not a reimplementation. Layout/scroll need the real browser suite.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync(process.argv[2]+'/mobile/src/main/java/be/mygod/vpnhotspot/DashboardPage.kt','utf8');
const html=source.split('"""')[1].trim();
const fixture=fs.readFileSync(__dirname+'/dashboard_browser.cjs','utf8');
const context={};vm.createContext(context);
vm.runInContext(fixture.slice(fixture.indexOf('const client ='),fixture.indexOf("let mode ="))+'\nglobalThis.fixture=status;',context);
const status=JSON.parse(JSON.stringify(context.fixture));
let current=status,mode='valid';
const dom=new JSDOM(html,{url:'http://localhost/',runScripts:'dangerously',pretendToBeVisual:true,beforeParse(window){
 window.setTimeout=()=>0;window.clearTimeout=()=>{};
 window.fetch=async()=>{if(mode==='network')throw Error('offline');return {ok:mode!=='http',status:503,text:async()=>mode==='json'?'{invalid':JSON.stringify(current)}};
}});
const w=dom.window,d=w.document,$=s=>d.querySelector(s),all=s=>Array.from(d.querySelectorAll(s));
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const refresh=async()=>{await w.eval('refresh()');await flush()};
const visible=()=>all('#clients .client-card').filter(n=>!n.hidden);
(async()=>{
 await flush();
 assert.equal(all('#clients .client-card').length,2);
 assert.equal(visible().length,2);assert.equal(all('#clients details[open]').length,0);
 const first=$('[data-select="02:00:00:00:00:01"]'),second=$('[data-select="02:00:00:00:00:02"]');
 first.click();assert.equal(visible().length,1);
 const row=visible()[0],detail=row.querySelector('details'),summary=detail.querySelector('summary');
 detail.open=true;summary.focus();await refresh();
 assert.equal(d.activeElement,summary);assert.equal(visible()[0],row);assert.equal(detail.open,true);
 assert.equal(first.getAttribute('aria-pressed'),'true');
 current={...status,clients:[status.clients[1],{...status.clients[0],name:'renamed system host'}]};await refresh();
 assert.equal(d.activeElement,summary);assert.equal(visible()[0],row);assert.equal(detail.open,true);
 assert.equal(first.textContent,'renamed system host');
 current=status;await refresh();
 second.click();await refresh();assert.equal(visible()[0].dataset.mac,'02:00:00:00:00:02');
 assert.equal(all('#clients details[open]').length,1);
 $('[data-select=""]').click();assert.equal(visible().length,2);
 $('[data-expand="true"]').click();await refresh();assert.equal(all('#clients details[open]').length,2);
 assert.equal(all('#clients img,#clients script').length,0);
 assert.ok($('#clients').textContent.includes('<script>alert(3)</script>'));
 $('[data-expand="false"]').click();await refresh();assert.equal(all('#clients details[open]').length,0);
 for(const failure of ['http','json','network','partial']){
  mode=failure;current=failure==='partial'?{...status,clients:[status.clients[0],{mac:'bad'}]}:status;
  await refresh();assert.ok($('#refreshState').textContent.includes('刷新失败'));
  assert.equal(all('#clients .client-card').length,2);assert.equal(all('#clients .client-card')[0],row);
  mode='valid';current=status;await refresh();assert.ok($('#refreshState').textContent.startsWith('更新成功'));
 }
 current={...status,neighboursAvailable:false,trafficAvailable:false};await refresh();
 assert.equal(all('#clients .client-card').length,2);assert.ok($('#refreshState').textContent.includes('邻居来源不可用'));
 current=status;await refresh();first.click();first.focus();
 current={...status,clients:[status.clients[1]]};await refresh();
 assert.equal(visible().length,1);assert.equal($('[data-select=""]').getAttribute('aria-pressed'),'true');
 assert.equal(d.activeElement,$('[data-select=""]'));
 current=status;await refresh();assert.equal(all('#clients details[open]').length,0);
 current={...status,clients:[]};await refresh();assert.equal(visible().length,0);
 assert.ok($('#clients').parentElement.textContent.includes('当前没有观测到设备'));
 console.log('PASS production DOM: capsule/all, keyed focus, expansion/polling, removal/fallback, hostile hostname, failure retention, empty state');
 dom.window.close();
})().catch(error=>{console.error(error);dom.window.close();process.exitCode=1});
