'use strict';
const $ = id => document.getElementById(id);
const labels = {waiting:'Waiting for system', starting:'Starting monitoring…', monitoring:'Monitoring', reconnecting:'Connection lost — reconnecting…', camera_unavailable:'Camera unavailable', interrupted:'Detection interrupted'};
const phaseLabels = {DETECT_UPPER:'Scanning upper',DETECT_LOWER:'Scanning lower',TRANSIT_TO_LOWER:'Moving down',TRANSIT_TO_UPPER:'Moving up',MOVING_TO_STATION:'Moving to station',SETTLING:'Settling',IDLE:'Waiting for scan'};
const smsLabels = {idle:'No message',queued:'Queued',sending:'Sending',sent:'Submitted',failed:'Failed',unknown:'Unconfirmed'};
let current = null, everConnected = false, instance = null, offset = null, previousServerTime = null;
let scan = null, streamWanted = false, streamStarted = null, nextStreamAttempt = 0, inFlight = false, lastResponse = null;
const camera = $('camera');
function text(id, value) { $(id).textContent = value; }
function timeLabel(value) {
  if (!value) return 'Not checked';
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? 'Not checked' : date.toLocaleTimeString('en', {hour:'2-digit',minute:'2-digit'});
}
function stopStream() {
  camera.removeAttribute('src'); camera.hidden = true; streamStarted = null;
  $('camera-label').hidden = true;
}
function startStream() {
  if (!streamWanted || streamStarted !== null || performance.now() < nextStreamAttempt) return;
  streamStarted = performance.now(); camera.hidden = false;
  camera.src = '/stream?t=' + Date.now();
}
camera.addEventListener('error', () => {
  stopStream(); nextStreamAttempt = performance.now() + 3000;
  $('camera-cover').hidden = false; text('camera-message', 'Reconnecting camera…');
});
function renderStations(rows, live) {
  $('stations').replaceChildren();
  for (let id = 1; id <= 4; id++) {
    const s = rows.find(row => Number(row.id ?? row.station_id) === id) || {};
    const checked = s.last_scanned || null;
    const status = checked ? (['clear','pest','fungus','both'].includes(s.status) ? s.status : 'unknown') : 'unknown';
    const names = {clear:'No threats detected',pest:'Pests detected',fungus:'Fungus detected',both:'Pests & fungus',unknown:'Not scanned yet'};
    const article = document.createElement('article');
    article.className = 'station' + (live && Number(current.scan?.station) === id ? ' active' : '');
    const top = document.createElement('div'); top.className = 'station-top';
    const name = document.createElement('span'); name.className = 'station-name'; name.textContent = 'Lettuce ' + id;
    const condition = document.createElement('span'); condition.className = 'station-status ' + status; condition.textContent = names[status];
    top.append(name, condition);
    const bottom = document.createElement('div'); bottom.className = 'station-bottom';
    const counts = document.createElement('span');
    const pestCount = Number(s.pest_count || 0);
    counts.textContent = checked ? `${pestCount} ${pestCount === 1 ? 'pest' : 'pests'} · ${Number(s.fungus_coverage_pct || 0).toFixed(1)}% fungus` : 'Awaiting first completed scan';
    const stamp = document.createElement('time'); stamp.textContent = checked ? timeLabel(checked) : '—';
    bottom.append(counts, stamp); article.append(top, bottom); $('stations').append(article);
  }
  $('stations').classList.toggle('stale', !live);
}
function renderLogs(data) {
  const rows = Array.isArray(data.logs) ? data.logs : [];
  const available = data.logs_available && data.connected;
  $('logs').replaceChildren();
  for (const row of rows.slice(0,5)) {
    const item = document.createElement('div'); item.className = 'log-row';
    const stamp = document.createElement('time'); stamp.textContent = timeLabel(row.first_seen);
    const station = document.createElement('span'); station.textContent = 'Lettuce ' + row.station;
    const detection = document.createElement('span'); detection.textContent = ({whitefly:'Whiteflies',whiteflies:'Whiteflies',worm:'Worm',fungus:'Fungus'})[row.class] || String(row.class || 'Detection');
    const status = document.createElement('span'); status.className = row.status === 'resolved' ? 'resolved' : 'active'; status.textContent = row.status === 'resolved' ? 'Resolved' : 'Active';
    item.append(stamp, station, detection, status); $('logs').append(item);
  }
  $('history-heading').parentElement.parentElement.classList.toggle('stale', !available);
  $('logs-message').hidden = rows.length > 0;
  text('logs-message', available ? 'No detections recorded' : everConnected ? 'Detection history unavailable' : 'Waiting for detection history');
  text('log-freshness', available ? 'Confirmed records' : rows.length ? 'History unavailable · last known records' : '');
}
function synchronize(data, sent, received) {
  const next = data.scan;
  if (!data.connected || instance !== data.instance_id || (next && previousServerTime !== null && next.server_time < previousServerTime)) {
    offset = null; previousServerTime = null;
  }
  instance = data.instance_id;
  if (data.connected && next && Number.isFinite(next.server_time) && Number.isFinite(next.phase_started_at) && Number.isFinite(next.phase_duration_s)) {
    const estimate = next.server_time - (sent + received) / 2000;
    offset = offset === null ? estimate : .2 * estimate + .8 * offset;
    previousServerTime = next.server_time; scan = next;
  } else scan = null;
}
function render(data, sent, received) {
  const restart = instance !== null && data.instance_id !== instance;
  current = data; everConnected ||= data.connected;
  synchronize(data, sent, received);
  text('status', labels[data.status] || labels.waiting);
  $('connection').className = 'connection ' + (data.status === 'monitoring' ? 'good' : everConnected ? 'warning' : '');
  text('freshness', data.connected ? 'Last completed scans' : data.updated_at ? 'Last known · ' + timeLabel(data.updated_at) : 'Awaiting data');
  text('update-time', data.connected ? 'Local monitoring' : data.updated_at ? 'Data paused · ' + timeLabel(data.updated_at) : 'Local monitoring');
  renderStations(Array.isArray(data.stations) ? data.stations : [], data.connected);
  renderLogs(data);
  text('pump', data.pump_active === null || !data.connected ? 'Unavailable' : data.pump_active ? 'Running' : 'Idle');
  text('buzzer', data.buzzer_muted === null || !data.connected ? 'Unavailable' : data.buzzer_muted ? 'Muted' : 'On');
  text('sms', data.connected ? smsLabels[data.sms_status] || 'Unavailable' : 'Unavailable');
  streamWanted = Boolean(data.connected && data.camera_available);
  if (restart) {stopStream(); nextStreamAttempt = performance.now() + 500;}
  if (!streamWanted) stopStream();
  const streamStalled = streamStarted !== null && performance.now() - streamStarted > 6000 && (!data.stream_active || data.stream_age_s === null || data.stream_age_s > 3);
  if (streamStalled) {stopStream(); nextStreamAttempt = performance.now() + 1000;}
  const flowing = streamWanted && data.stream_active && data.stream_age_s !== null && data.stream_age_s <= 3;
  $('camera-cover').hidden = flowing;
  $('camera-label').hidden = !flowing;
  text('camera-message', data.status === 'waiting' ? 'Waiting for the system to start…' : data.status === 'reconnecting' ? labels.reconnecting : data.status === 'starting' ? labels.starting : !data.camera_available ? 'Camera unavailable' : 'Connecting camera…');
  startStream(); tick();
}
function tick() {
  text('clock', new Date().toLocaleTimeString('en', {hour:'2-digit',minute:'2-digit'}));
  const live = current?.connected && lastResponse !== null && performance.now() - lastResponse < 5000;
  if (scan && live && offset !== null) {
    const remaining = Math.max(0, Math.min(scan.phase_duration_s, scan.phase_started_at + scan.phase_duration_s - (performance.now()/1000 + offset)));
    text('scan-label', `${phaseLabels[scan.phase] || 'Scanning'} · Lettuce ${scan.station}`);
    text('countdown', `${Math.ceil(remaining)}s`);
    $('scan-progress').value = scan.phase_duration_s > 0 ? 1 - remaining / scan.phase_duration_s : 0;
  } else {
    text('scan-label', everConnected ? 'Scan updates paused' : 'Waiting for scan'); text('countdown', '—'); $('scan-progress').value = 0;
  }
  if (!live && current?.connected) disconnected();
  if (live && current.camera_available && Number.isFinite(current.camera_frame_age_s) && current.camera_frame_age_s + (performance.now() - lastResponse)/1000 > 3) {
    render({...current,camera_available:false,status:current.status==='monitoring'?'camera_unavailable':current.status},performance.now(),performance.now());
  }
  startStream();
}
function disconnected() {
  const fallback = {...(current || {}), connected:false, camera_available:false, logs_available:false, status:everConnected?'reconnecting':'waiting'};
  render(fallback, performance.now(), performance.now());
}
async function poll() {
  if (inFlight) return;
  inFlight = true;
  const sent = performance.now(); const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3000);
  try {
    const response = await fetch('/api/state', {signal:controller.signal,cache:'no-store'});
    if (!response.ok) throw Error('Display unavailable');
    const data = await response.json();
    if (typeof data.connected !== 'boolean' || typeof data.status !== 'string') throw Error('Invalid display state');
    lastResponse = performance.now(); render(data, sent, lastResponse);
  } catch (_) {disconnected();}
  finally {clearTimeout(timer); inFlight = false; setTimeout(poll, 2000);}
}
renderStations([], false); tick(); poll(); setInterval(tick, 250);

let wakeLock = null;
async function keepScreenAwake() {
  if (!navigator.wakeLock || wakeLock || document.visibilityState !== 'visible') return;
  try {
    wakeLock = await navigator.wakeLock.request('screen');
    wakeLock.addEventListener('release', () => {wakeLock = null;});
  } catch (_) { /* Retry after compositor/session startup. */ }
}
document.addEventListener('visibilitychange', keepScreenAwake);
keepScreenAwake(); setInterval(keepScreenAwake, 5000);
