// 登录态保活调度器 (2026-09-29 立项)
// 目的: 扫码一次 + 长期保活 —— 周期跑 probe 访问创作后台刷新 Cookie 活性,
//       掉线优先自动恢复、恢复不了 toast 告警人工扫码, 让智能推广自动化
//       流水线不因登录态过期断流(人工只在掉线告警时介入一次)。
// 用法: node keepalive.js <config_keepalive.json>   (常驻, Ctrl+C 退出)
// 恢复链(channels): probe 撞登录页时 bot.js ensureLogin 一键登录优先(设备已绑定),
//   微信客户端授权弹窗由 allow_enter.ps1 自动点「允许」(触摸压力通道)——全自动;
//   注意: 该链依赖 PC 微信客户端(Weixin.exe)在线, 不在线则超时告警。
//   douyin 无一键登录, 掉线即告警人工扫码。
// 退出码语义(bot): 0=存活(含一键自动恢复)  2/4=掉线需人工  1/3=基础设施错(Chrome占用等)
//   基础设施错不立即告警(下轮重试), 连续 2 次才 toast, 防发布流水线占用 profile 误报。
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const CFG = JSON.parse(fs.readFileSync(process.argv[2] || 'config_keepalive.json', 'utf-8'));
const NAME = CFG.name || 'bot';
const INTERVAL_MS = (CFG.intervalHours || 24) * 3600 * 1000;
const WAIT_LOGIN_MIN = CFG.waitLoginMinutes || 3;
const RUN_TIMEOUT_MS = (WAIT_LOGIN_MIN + 8) * 60000; // 强杀兜底防挂死(正常 1-2 分钟完)
const LOCK = path.join(__dirname, `keepalive_${NAME}.lock`);
const STATUS = path.join(__dirname, `keepalive_status_${NAME}.json`);
const LOGF = path.join(__dirname, `keepalive_${NAME}.log`);
const RUN_CFG = path.join(__dirname, `config_ka_run_${NAME}.json`);

const fmt = (t) => {
  const d = new Date(t), p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
};
const LOG = (m) => {
  const line = `[${fmt(Date.now())}] ${m}`;
  console.log(line);
  fs.appendFileSync(LOGF, line + '\n');
};
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

// ---- 锁(防双开): pid 探活, 陈旧锁自动接管 ----
function acquireLock() {
  try {
    const pid = +fs.readFileSync(LOCK, 'utf8').trim();
    if (pid && pid !== process.pid) {
      process.kill(pid, 0); // 活着会抛错; 不抛 = 已有实例
      LOG(`already running (pid ${pid}), exit`);
      return false;
    }
  } catch (e) { /* 无锁或旧进程已死 */ }
  fs.writeFileSync(LOCK, String(process.pid));
  return true;
}
const releaseLock = () => { try { fs.unlinkSync(LOCK); } catch (e) {} };
process.on('SIGINT', () => { releaseLock(); LOG('keepalive stop'); process.exit(0); });
process.on('exit', releaseLock);

// ---- toast 通知(best-effort, 失败仅留日志) ----
// 铁律(2026-09-29 实证): detached:true 的 powershell spawn 会秒死(toast/弹窗静默丢失)
//   —— 普通 spawn + windowsHide 即后台运行, unref 不拖 keepalive 退出
function toast(title, body) {
  try {
    const p = spawn('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass',
      '-File', path.join(__dirname, 'toast_notify.ps1'), '-Title', title, '-Body', body || ''],
      { cwd: __dirname, stdio: 'ignore', windowsHide: true });
    p.unref();
    LOG(`toast: ${title} | ${body || ''}`);
  } catch (e) { LOG('toast err ' + e.message); }
}

function loadState() {
  try { return JSON.parse(fs.readFileSync(STATUS, 'utf8')); } catch (e) {}
  return { name: NAME, alive: null, lastResult: '', lastRunAt: '', lastOkAt: '', lastReason: '', consecutiveLoggedOut: 0, consecutiveInfra: 0, nextRunAt: '', history: [] };
}
const saveState = (s) => fs.writeFileSync(STATUS, JSON.stringify(s, null, 2));

// ---- 一轮保活: 先挂 allow_enter watcher(等 bot 的 flag), 再 spawn bot probe ----
async function runOnce(state) {
  if (CFG.allowEnter) {
    const maxWait = WAIT_LOGIN_MIN * 60 + 180;
    try {
      // 同 toast 铁律: 不用 detached(会秒死), 普通 spawn + windowsHide
      const w = spawn('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', path.join(__dirname, 'allow_enter.ps1'), String(maxWait)],
        { cwd: __dirname, stdio: 'ignore', windowsHide: true });
      w.unref();
      LOG(`allow_enter watcher detached (maxWait ${maxWait}s, pid ${w.pid})`);
    } catch (e) { LOG('allow_enter spawn err ' + e.message); }
  }
  // bot config 每轮刷新(单文件事实源, 与 keepalive 配置不漂移)
  fs.writeFileSync(RUN_CFG, JSON.stringify({ action: CFG.botAction || 'probe', waitLoginMinutes: WAIT_LOGIN_MIN }, null, 2));

  LOG(`round start: node ${CFG.botScript} ${path.basename(RUN_CFG)} (timeout ${Math.round(RUN_TIMEOUT_MS / 60000)}min)`);
  const exit = await new Promise((resolve) => {
    const bot = spawn(process.execPath, [CFG.botScript, RUN_CFG], { cwd: __dirname, stdio: 'ignore' });
    const killer = setTimeout(() => {
      LOG('BOT_KILLED_TIMEOUT(挂死强杀)');
      try { bot.kill('SIGKILL'); } catch (e) {}
      resolve('timeout');
    }, RUN_TIMEOUT_MS);
    bot.on('exit', (c) => { clearTimeout(killer); resolve(c); });
    bot.on('error', (e) => { clearTimeout(killer); LOG('bot spawn err ' + e.message); resolve('spawn_err'); });
  });

  // 判定: ok / logged_out(掉线需人工) / infra(基础设施错, 重试容忍)
  let kind, reason = '';
  if (exit === 0) {
    let st = {};
    try { st = JSON.parse(fs.readFileSync(path.join(__dirname, CFG.loginStateFile), 'utf8')); } catch (e) {}
    if (st.ok) kind = 'ok';
    else { kind = 'logged_out'; reason = 'login_state.ok=false'; }
  } else if (exit === 2 || exit === 4) {
    kind = 'logged_out';
    reason = exit === 2 ? 'LOGIN_TIMEOUT(掉线, 一键恢复失败或扫码未确认)' : 'RELOGIN_TIMEOUT(会话过期重登失败)';
  } else {
    kind = 'infra';
    reason = `infra_error(exit=${exit}) Chrome profile 占用/启动失败等, 下轮重试`;
  }

  const prevAlive = state.alive;
  const at = fmt(Date.now());
  if (kind === 'ok') {
    state.alive = true; state.lastOkAt = at;
    state.consecutiveLoggedOut = 0; state.consecutiveInfra = 0;
    if (prevAlive === false) toast(`${NAME}-bot 登录态已恢复`, '保活 probe 通过, 可继续自动发布');
  } else if (kind === 'logged_out') {
    state.alive = false;
    state.consecutiveLoggedOut++; state.consecutiveInfra = 0;
    if (prevAlive !== false) toast(`${NAME}-bot 登录态掉线`, '自动恢复失败, 请打开 Chrome 窗口扫码登录');
  } else {
    state.consecutiveInfra++;
    if (state.consecutiveInfra === 2) toast(`${NAME}-bot 保活连续异常`, 'probe 连续 2 次基础设施错误, 详见 keepalive 日志');
  }
  state.lastResult = kind; state.lastRunAt = at; state.lastReason = reason;
  state.history.unshift({ at, kind, exit: typeof exit === 'number' ? exit : String(exit), reason });
  state.history = state.history.slice(0, 20);
  LOG(`round result: ${kind}${reason ? ' — ' + reason : ''} (consec loggedOut=${state.consecutiveLoggedOut} infra=${state.consecutiveInfra})`);
}

(async () => {
  if (!acquireLock()) process.exit(3);
  const WAKE = path.join(__dirname, `keepalive_${NAME}.wake`);
  LOG(`keepalive start: name=${NAME} bot=${CFG.botScript} interval=${(INTERVAL_MS / 3600000)}h allowEnter=${!!CFG.allowEnter}`);
  const state = loadState();
  while (true) {
    const t0 = Date.now();
    // 异常自愈: 单轮任何未预期错误不杀常驻进程(计入状态, 下轮重试)
    try {
      await runOnce(state);
    } catch (e) {
      LOG(`round crashed: ${e && e.message || e}`);
      state.lastResult = 'infra'; state.lastRunAt = fmt(Date.now()); state.lastReason = 'uncaught: ' + String(e && e.message || e);
    }
    state.nextRunAt = fmt(Date.now() + INTERVAL_MS);
    saveState(state);
    LOG(`round finished in ${Math.round((Date.now() - t0) / 1000)}s, next run ${state.nextRunAt}`);
    // wake-able sleep: publish_guard 发布前写 wake 文件可催立即跑一轮
    // (掉线恢复不必等满 24h; 30s 分片轮询, 文件即信号一次性消费)
    const deadline = Date.now() + INTERVAL_MS;
    while (Date.now() < deadline) {
      let woken = false;
      try { woken = fs.existsSync(WAKE); if (woken) fs.unlinkSync(WAKE); } catch (e) {}
      if (woken) { LOG('wake signal — running next round immediately'); break; }
      await sleep(30000);
    }
  }
})();
