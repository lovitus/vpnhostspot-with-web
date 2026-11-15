// Browser integration over the compiled production page. All status values below are test fixtures.
const fs = require('node:fs');
const http = require('node:http');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const html = fs.readFileSync(process.argv[2], 'utf8');
const output = process.argv[3];
const client = (mac, ip) => ({
  mac, name: '测试设备 <img src=x onerror=alert(1)>', nameSource: 'ClientRecord', interface: 'p2p-test',
  hostnames: ['<img src=x onerror=alert(2)>'], hostnameSource: 'TetheringManagerCompat.ClientsChanged',
  hostnameStatus: 'available', hostnameChangedAt: 1234567890000,
  hostnameAddresses: [{ip, hostname: '<script>alert(3)</script>', type: 'WIFI_P2P', downstream: 'p2p-test'}],
  interfaces: ['p2p-test'], type: 'WIFI_P2P', ips: [ip, 'fe80::2'], ipv4: [ip], ipv6: ['fe80::2'],
  neighbourStates: [{ip, state: 2}], online: true, state: 'associated',
  sendBytesPerSecond: 1200, receiveBytesPerSecond: 2400, sendBytes: 1234567, receiveBytes: 7654321,
  upstreamDnsSentPackets: 10, upstreamDnsReceivedPackets: 9, dnsCountersAvailable: true,
  dnsSuccessCount: null, dnsFailureCount: null, activeTcpFlows: null, activeUdpFlows: null,
  currentEgress: null, lastActivityAt: null, lastTrafficChangeAgoMillis: 1000,
});
const upstream = {network: 1234, interface: 'tun-test', interfaces: ['tun-test'], type: ['VPN'], validated: true,
  capabilitiesObservedAt: 1234567890000, addresses: ['10.0.0.2/32'], dnsServers: ['1.1.1.1'],
  routes: [{destination: '0.0.0.0/0', gateway: null, interface: 'tun-test', type: 1}]};
const status = {
  schema: 3, downstream: 'p2p-test', gateway: '192.0.2.1', port: 8080, uptimeMillis: 65000,
  sampledAt: 1234567890000, trafficAvailable: true, neighboursAvailable: true, neighboursChangedAt: 1234567890000,
  trafficRateWindowMillis: 60000,
  observedClientCount: 2, associatedClientCount: 2, associationSource: 'RepeaterService.group',
  clients: [client('02:00:00:00:00:01', '192.0.2.2'), client('02:00:00:00:00:02', '192.0.2.3')],
  traffic: {sendBytes: 1234567, receiveBytes: 7654321, sendBytesPerSecond: 1200, receiveBytesPerSecond: 2400},
  vpn: upstream, primary: upstream, fallback: null, selectedUpstream: upstream, fallbackInUse: null, currentEgress: null,
  diagnostics: {daemon: null, counterObservation: 'recorder_reply_observed', counterSampledAt: 1234567890000, counterAgeMillis: 1000,
    recentErrors: [], appVersion: '3.0.8-p4', versionCode: 2011004, patchRelease: '0005-dashboard-schema3',
    android: '16', androidApi: 36, kernel: 'test-kernel', deviceUptimeMillis: 999999},
};
let mode = 'valid', requests = 0;
// Deliberately preserve a JSON integer beyond JavaScript's exact number range during export.
const originalRaw = JSON.stringify(status).replace('1234567', '9007199254740993');
const server = http.createServer((req,res) => {
 if(req.url === '/') {res.writeHead(200, {'content-type':'text/html;charset=utf-8'});res.end(html);return;}
 if(req.url !== '/api/status') {res.writeHead(404);res.end();return;}
 requests++;
 if(mode === 'network') {req.socket.destroy();return;}
 if(mode === 'http') {res.writeHead(503);res.end('{}');return;}
 res.writeHead(200, {'content-type':'application/json;charset=utf-8','cache-control':'no-store'});
 if(mode === 'json') res.end('{invalid');
 else if(mode === 'partial') res.end(JSON.stringify({...status, clients:[status.clients[0], {mac:'bad'}]}));
 else if(mode === 'monitor') res.end(JSON.stringify({...status,neighboursAvailable:false,trafficAvailable:false}));
 else if(mode === 'removed') res.end(JSON.stringify({...status,clients:[status.clients[1]],observedClientCount:1,associatedClientCount:1}));
 else if(mode === 'empty') res.end(JSON.stringify({...status,clients:[],observedClientCount:0,associatedClientCount:0}));
 else res.end(originalRaw);
});
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser = await chromium.launch({headless:true});
 try {
  const page = await browser.newPage({viewport:{width:1440,height:1100},acceptDownloads:true});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port+'/');
  await page.waitForFunction(()=>document.querySelectorAll('#clients .client-card').length===2);
  assert.match(await page.locator('#dashboard').innerText(),/主上游.*程序选择/s);
  assert.equal(await page.locator('#dashboard img').count(),0); // identity text cannot become markup
  assert.match(await page.locator('#dashboard').innerText(),/区间均值.*不是每秒实时采样/s);
  assert.match(await page.locator('#dashboard').innerText(),/部分计数回复不能证明所有源健康/);
  await page.waitForTimeout(3500);
  assert.equal(await page.locator('#clients .client-card').count(),2);
  assert.ok(requests>=3);
  const firstCapsule=page.locator('[data-select="02:00:00:00:00:01"]');
  const secondCapsule=page.locator('[data-select="02:00:00:00:00:02"]');
  assert.equal(await page.locator('#clients details[open]').count(),0);
  assert.equal(await page.locator('#clients .client-card:visible').count(),2);
  await firstCapsule.click();
  assert.equal(await page.locator('#clients .client-card:visible').count(),1);
  await page.locator('details[data-key="client-02:00:00:00:00:01"] summary').click();
  await page.evaluate(()=>{window.savedSummary=document.activeElement;window.savedCard=document.querySelector('#clients .client-card');window.savedScrollY=scrollY});
  await page.waitForTimeout(1200);
  assert.ok(await page.evaluate(()=>document.activeElement===window.savedSummary&&document.querySelector('#clients .client-card')===window.savedCard&&scrollY===window.savedScrollY));
  assert.equal(await firstCapsule.getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('details[data-key="client-02:00:00:00:00:01"]').getAttribute('open'),'');
  await secondCapsule.click();
  assert.equal(await page.locator('#clients .client-card:visible').getAttribute('data-mac'),'02:00:00:00:00:02');
  await page.waitForTimeout(1200);
  assert.equal(await secondCapsule.getAttribute('aria-pressed'),'true');
  await page.locator('[data-select=""]').click();
  assert.equal(await page.locator('#clients .client-card:visible').count(),2);
  assert.equal(await page.locator('#clients details[open]').count(),1);
  await page.locator('[data-expand="true"]').click();await page.waitForTimeout(1200);
  assert.equal(await page.locator('#clients details[open]').count(),2);
  assert.equal(await page.locator('#clients img,#clients script').count(),0);
  assert.match(await page.locator('#clients').innerText(),/<script>alert\(3\)<\/script>/);
  await page.locator('[data-expand="false"]').click();await page.waitForTimeout(1200);
  assert.equal(await page.locator('#clients details[open]').count(),0);
  for(const failure of ['http','json','partial','network']) {
   mode=failure;
   await page.waitForFunction(()=>document.querySelector('#refreshState').textContent.includes('刷新失败'));
   assert.equal(await page.locator('#clients .client-card').count(),2, failure+' erased retained client rows');
   assert.match(await page.locator('#refreshState').innerText(),/保留上次成功数据/);
   mode='valid';
   await page.waitForFunction(()=>document.querySelector('#refreshState').textContent.startsWith('更新成功'));
  }
  mode='monitor';
  await page.waitForFunction(()=>document.querySelector('#refreshState').textContent.includes('邻居来源不可用'));
  assert.equal(await page.locator('#clients .client-card').count(),2);
  mode='valid';
  await page.waitForFunction(()=>!document.querySelector('#refreshState').textContent.includes('不可用'));
  const downloadPromise=page.waitForEvent('download');await page.locator('#export').click();const download=await downloadPromise;
  assert.equal(fs.readFileSync(await download.path(),'utf8'),originalRaw);
  await page.screenshot({path:output+'/dashboard-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
  await page.locator('[data-expand="true"]').click();
  const layout = await page.locator('#clients .client-card').first().evaluate(row => {
    const rect=row.getBoundingClientRect();return {left:rect.left,right:rect.right,width:row.clientWidth,scrollWidth:row.scrollWidth};
  });
  assert.ok(layout.scrollWidth<=layout.width,'mobile client fields require horizontal scrolling');
  assert.ok(layout.left>=0&&layout.right<=390,'mobile card lies outside viewport');
  await page.locator('#client-capsules').evaluate(n=>{n.scrollLeft=n.scrollWidth;window.capsuleScroll=n.scrollLeft});
  await page.waitForTimeout(1200);
  assert.ok(await page.locator('#client-capsules').evaluate(n=>n.scrollLeft===window.capsuleScroll));
  await page.screenshot({path:output+'/dashboard-mobile.png',fullPage:true});
  await firstCapsule.click();mode='removed';
  await page.waitForFunction(()=>document.querySelectorAll('#clients .client-card').length===1);
  assert.equal(await page.locator('[data-select=""]').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('#clients .client-card:visible').count(),1);
  assert.equal(await page.locator('#clients details[open]').count(),1);
  mode='valid';await page.waitForFunction(()=>document.querySelectorAll('#clients .client-card').length===2);
  assert.equal(await page.locator('details[data-key="client-02:00:00:00:00:01"]').getAttribute('open'),null);
  mode='empty';await page.waitForFunction(()=>document.querySelectorAll('#clients .client-card').length===0);
  assert.match(await page.locator('#dashboard').innerText(),/当前没有观测到设备/);
  assert.deepEqual(errors,[]);
  console.log('PASS production page: stable clients >3s; HTTP/JSON/partial/network failures retain rows; source failure, recovery, explicit empty state, text safety, details, exact JSON export and mobile layout');
 } finally {await browser.close();server.close();}
})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
