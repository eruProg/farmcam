// Run with Playwright installed and display_server.py listening on localhost:8090.
const {chromium, expect} = require('playwright/test');
const fs = require('fs');
const path = require('path');
const output = path.resolve('.verification/display');
fs.mkdirSync(output, {recursive:true});
const cameraFixture = `<svg xmlns="http://www.w3.org/2000/svg" width="960" height="720" viewBox="0 0 960 720"><rect width="960" height="720" fill="#3e4938"/><g fill="#72914e" stroke="#92ad6d" stroke-width="3"><ellipse cx="330" cy="330" rx="130" ry="180" transform="rotate(-25 330 330)"/><ellipse cx="540" cy="350" rx="145" ry="195" transform="rotate(28 540 350)"/><ellipse cx="440" cy="410" rx="100" ry="170"/></g><path d="M440 570L430 210M440 480L310 280M465 470L600 250" stroke="#a4bd7d" stroke-width="8" fill="none"/><rect x="20" y="650" width="420" height="45" rx="5" fill="#17241bcc"/><text x="38" y="681" font-size="24" fill="white" font-family="sans-serif">Camera preview · synthetic test fixture</text></svg>`;
const now = Date.now()/1000;
const fixture = {
  status:'monitoring',connected:true,instance_id:'a',updated_at:now,camera_available:true,
  scan:{phase:'DETECT_UPPER',station:2,phase_started_at:90,phase_duration_s:30,server_time:100},
  stations:[{id:1,status:'clear',last_scanned:now-120,pest_count:0,fungus_coverage_pct:0},
    {id:2,status:'both',last_scanned:now-80,pest_count:3,fungus_coverage_pct:22.9},
    {id:3,status:'pest',last_scanned:now-40,pest_count:1,fungus_coverage_pct:0},
    {id:4,status:'clear',last_check:new Date().toISOString()}],
  pump_active:false,buzzer_muted:true,sms_status:'sent',logs_available:true,logs_updated_at:now,
  logs:Array.from({length:5},(_,i)=>({station:i%4+1,class:i%2?'worm':'fungus',first_seen:now-i*45,status:i%2?'resolved':'active'})),
  stream_active:true,stream_age_s:.1,
};
(async()=>{
  const browser=await chromium.launch({channel:process.env.BROWSER_CHANNEL||'msedge',headless:true});
  const errors=[];
  for(const [width,height] of [[1024,600],[1280,800],[1920,1080]]) {
    const page=await browser.newPage({viewport:{width,height}});
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto('http://127.0.0.1:8090');
    await expect(page.locator('#camera-message')).toHaveText('Waiting for the system to start…');
    await page.screenshot({path:path.join(output,`waiting-${width}.png`)});
    let data=structuredClone(fixture);
    let calls=0,concurrent=0,peak=0,streamCalls=0;
    await page.route('**/api/state',async route=>{
      calls++;concurrent++;peak=Math.max(peak,concurrent);
      await new Promise(r=>setTimeout(r,100));
      await route.fulfill({json:data});concurrent--;
    });
    await page.route('**/stream?*',async route=>{streamCalls++;await route.fulfill({contentType:'image/svg+xml',body:cameraFixture});});
    await expect(page.locator('#status')).toHaveText('Monitoring');
    await expect(page.locator('#camera-cover')).toBeHidden();
    await expect(page.locator('.station').last()).toContainText('Not scanned yet');
    await expect(page.locator('.station.active')).toContainText('Lettuce 2');
    await page.screenshot({path:path.join(output,`monitoring-${width}.png`)});
    const overflow=await page.evaluate(()=>({x:document.documentElement.scrollWidth>innerWidth,y:document.documentElement.scrollHeight>innerHeight}));
    if(overflow.x||overflow.y)throw Error('Viewport overflow '+width);
    await page.evaluate(()=>document.documentElement.style.fontSize='20px');
    await page.screenshot({path:path.join(output,`enlarged-${width}.png`)});
    const bad=await page.locator('.station,.equipment,.header,.scan-strip').evaluateAll(elements=>elements.filter(e=>e.scrollHeight>e.clientHeight+2||e.scrollWidth>e.clientWidth+2).map(e=>e.className));
    if(bad.length)throw Error('Enlarged text overflow: '+bad.join(',')+' at '+width);
    const clipped=await page.locator('.log-row').evaluateAll(rows=>rows.filter(row=>getComputedStyle(row).display!=='none' && row.getBoundingClientRect().bottom>row.parentElement.getBoundingClientRect().bottom+1).length);
    if(clipped)throw Error('Clipped log rows at '+width);
    data={...data,status:'camera_unavailable',camera_available:false};
    await expect(page.locator('#camera-message')).toHaveText('Camera unavailable');
    await expect(page.locator('#camera')).not.toHaveAttribute('src',/stream/);
    data={...data,status:'interrupted',camera_available:true};
    await expect(page.locator('#status')).toHaveText('Detection interrupted');
    data={...data,status:'reconnecting',connected:false,camera_available:false};
    await expect(page.locator('#status')).toHaveText('Connection lost — reconnecting…');
    await expect(page.locator('#stations')).toHaveClass('station-list stale');
    await expect(page.locator('#countdown')).toHaveText('—');
    await page.screenshot({path:path.join(output,`disconnected-${width}.png`)});
    data={...structuredClone(fixture),instance_id:'b',logs_available:false};
    await expect(page.locator('#status')).toHaveText('Monitoring');
    await expect(page.locator('#log-freshness')).toContainText('History unavailable');
    data={...data,logs_available:true,logs:[]};
    await expect(page.locator('#logs-message')).toHaveText('No detections recorded');
    await page.screenshot({path:path.join(output,`empty-${width}.png`)});
    if(peak!==1)throw Error('Overlapping status requests');
    if(streamCalls>5)throw Error('Excess stream reconnects');
    console.log(JSON.stringify({width,height,calls,peak,streamCalls,status:'passed'}));
    await page.close();
  }
  await browser.close();
  if(errors.length)throw Error(errors.join('\n'));
})().catch(e=>{console.error(e);process.exitCode=1});
