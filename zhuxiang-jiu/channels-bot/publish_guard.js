// 发布链登录态守卫 (2026-09-29 立项)
// 目的: 把"智能推广自动化"补成真闭环——RPA 发布(bot.js/douyin-bot.js)
//       执行前先验登录态: 健康放行(可直接包发布命令透传退出码);
//       掉线→写 wake 催保活调度器立即跑恢复轮(channels 一键登录链)→
//       轮询等待恢复; 超时仍掉线→拒绝发布(toast 告警人工扫码), 防止
//       发布流水线撞掉线态白跑一程(Chrome 开窗→登录页→超时)。
// 用法:
//   node publish_guard.js <channels|douyin> [--wait-min N] [-- node bot.js cfg.json]
//   --wait-min: 掉线等待恢复上限(默认 15 分钟)
//   -- 后跟发布命令: 守卫通过后 spawn 并透传其退出码(无 -- 则守卫即退出)
// 退出码: 0=登录态健康(或发布命令成功)  2=等待超时仍掉线(勿发布)  3=参数错
// 铁律: 只读状态不写业务——wake 文件是 keepalive 约定信号, 恢复/告警
//       全部由 keepalive 调度器完成, 守卫零副作用。
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const NAME = process.argv[2];
const WAIT_MIN = (() => {
  const i = process.argv.indexOf('--wait-min');
  return (i > 0 && process.argv[i + 1]) ? Math.max(1, +process.argv[i + 1] || 15) : 15;
})();
const CMD_SEP = process.argv.indexOf('--');
const PUBLISH_CMD = CMD_SEP > 0 ? process.argv.slice(CMD_SEP + 1) : null;

if (!/^(channels|douyin|xhs)$/.test(NAME || '')) {
  console.error('用法: node publish_guard.js <channels|douyin|xhs> [--wait-min N] [-- 发布命令]');
  process.exit(3);
}

const STATUS = path.join(__dirname, `keepalive_status_${NAME}.json`);
const WAKE = path.join(__dirname, `keepalive_${NAME}.wake`);
// 状态新鲜度: 保活默认 24h 一轮, 超 26h 未更新=调度器疑似停摆
const FRESH_MS = 26 * 3600 * 1000;

const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const readStatus = () => {
  try { return JSON.parse(fs.readFileSync(STATUS, 'utf8')); } catch (e) { return null; }
};
const tsOf = (s) => {
  // "2026-09-29 12:09:47" 本地时间 → epoch
  const m = /(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})/.exec(s || '');
  return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime() : 0;
};

function toast(title, body) {
  try {
    const p = spawn('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass',
      '-File', path.join(__dirname, 'toast_notify.ps1'), '-Title', title, '-Body', body || ''],
      { cwd: __dirname, stdio: 'ignore', windowsHide: true });
    p.unref();
  } catch (e) { /* best-effort */ }
}

(async () => {
  const deadline = Date.now() + WAIT_MIN * 60000;
  let round = 0;
  while (Date.now() < deadline) {
    round++;
    const st = readStatus();
    const fresh = st && st.lastRunAt && (Date.now() - tsOf(st.lastRunAt) < FRESH_MS);
    if (st && st.alive === true && fresh) {
      console.log(`[guard] ${NAME} 登录态健康(上次 ${st.lastRunAt} ${st.lastResult}) — 放行`);
      break;
    }
    // 非健康三态统一动作: 写 wake 催保活立即跑一轮
    if (!st || !fresh) {
      console.log(`[guard] ${NAME} 保活状态缺失或停摆(lastRunAt=${st ? st.lastRunAt : '无'}) — 写 wake 催醒并 toast 告警`);
      toast(`${NAME}-bot 保活疑似停摆`, `发布守卫发现状态过期(${st ? st.lastRunAt : '无'}), 已催醒, 请检查计划任务`);
    } else {
      console.log(`[guard] ${NAME} 登录态掉线(${st.lastReason || st.lastResult}) — 写 wake 催保活恢复(一键登录链), 等待中...`);
    }
    try { fs.writeFileSync(WAKE, String(Date.now())); } catch (e) {}
    if (round === 1) toast(`${NAME}-bot 发布前发现掉线`, `已触发自动恢复, 守卫等待中(上限${WAIT_MIN}分钟)`);
    await sleep(20000);
    if (Date.now() >= deadline) break;
    // wake 后保活一轮约 0.5-3 分钟(掉线恢复链 45s-3min), 20s 粒度轮询
  }

  const st = readStatus();
  if (!(st && st.alive === true)) {
    console.error(`[guard] ${NAME} 等待 ${WAIT_MIN} 分钟后仍掉线 — 拒绝发布(请人工扫码, 状态见 keepalive_status_${NAME}.json)`);
    toast(`${NAME}-bot 发布被守卫拒绝`, `自动恢复超时, 请打开 Chrome 扫码登录后重试发布`);
    process.exit(2);
  }

  if (!PUBLISH_CMD || !PUBLISH_CMD.length) {
    console.log('[guard] 守卫通过(未附发布命令)');
    process.exit(0);
  }
  console.log(`[guard] spawn 发布命令: ${PUBLISH_CMD.join(' ')}`);
  // 'node' 自动映射当前解释器全路径(本机 node 未入 PATH, 直跑会 ENOENT)
  const exe = PUBLISH_CMD[0] === 'node' ? process.execPath : PUBLISH_CMD[0];
  const child = spawn(exe, PUBLISH_CMD.slice(1), {
    cwd: __dirname, stdio: 'inherit', shell: false,
  });
  child.on('exit', (c) => process.exit(c === null ? 1 : c));
  child.on('error', (e) => { console.error('[guard] 发布命令启动失败: ' + e.message); process.exit(1); });
})();
