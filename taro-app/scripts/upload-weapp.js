/**
 * 微信小程序自动化上传(miniprogram-ci)
 *
 * 前置(仅首次, 需管理员微信):
 *   1. mp.weixin.qq.com 登录 → 开发管理 → 开发设置 → 「小程序代码上传」
 *      → 生成密钥 → 下载 private.wxafcff83a4c2a2b7f.key
 *   2. 将密钥放到 taro-app/ 根目录(或用 --key 指定路径; 已 gitignore)
 *   3. 若后台开启了「IP 白名单」, 需将本机公网 IP 加入(或临时关闭)
 *
 * 用法:
 *   node scripts/upload-weapp.js                        # 默认版本 1.1.0
 *   node scripts/upload-weapp.js --version 1.2.0 --desc "描述"
 *   node scripts/upload-weapp.js --key D:\path\to\private.xxx.key
 *   npm run upload:weapp
 *
 * 产物: dist-weapp/(48 页, project.config.json miniprogramRoot 已指向)
 */
const path = require('path');
const fs = require('fs');

const ROOT = path.resolve(__dirname, '..');
const APPID = 'wxafcff83a4c2a2b7f';

function parseArgs() {
  const args = { version: '1.1.0', desc: '全功能版: 消费主链+六大AI管理工作台', key: '' };
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i += 2) {
    const k = String(argv[i] || '').replace(/^--/, '');
    const v = argv[i + 1];
    if (k === 'version') args.version = v;
    else if (k === 'desc') args.desc = v;
    else if (k === 'key') args.key = v;
  }
  return args;
}

function preflight(distDir, keyPath) {
  const problems = [];
  const appJson = path.join(distDir, 'app.json');
  if (!fs.existsSync(appJson)) {
    problems.push(`未找到 ${appJson} — 先构建: $env:TARO_OUTPUT_DIR="dist-weapp"; npm run build:weapp`);
  } else {
    const pages = JSON.parse(fs.readFileSync(appJson, 'utf8')).pages || [];
    if (pages.length < 40) problems.push(`页面数异常(${pages.length} < 40), 疑似旧产物`);
  }
  if (!fs.existsSync(keyPath)) {
    problems.push([
      `未找到上传密钥 ${keyPath}`,
      '  获取: mp.weixin.qq.com → 开发管理 → 开发设置 → 小程序代码上传 → 生成密钥(下载)',
      `  放置: 拷贝到 ${path.join(ROOT, `private.${APPID}.key`)}`,
      '  注意: 若后台开启了 IP 白名单, 需将本机公网 IP 加入或临时关闭',
    ].join('\n'));
  }
  return problems;
}

async function main() {
  const args = parseArgs();
  const keyPath = args.key
    ? path.resolve(args.key)
    : path.join(ROOT, `private.${APPID}.key`);
  const distDir = path.join(ROOT, 'dist-weapp');

  const problems = preflight(distDir, keyPath);
  if (problems.length) {
    console.error('[preflight] 未满足前置条件:\n');
    problems.forEach((p) => console.error('  - ' + p + '\n'));
    process.exit(1);
  }

  const ci = require('miniprogram-ci');
  const project = new ci.Project({
    appid: APPID,
    type: 'miniProgram',
    projectPath: ROOT,
    privateKeyPath: keyPath,
    ignores: ['node_modules/**/*', 'dist/**/*'],
  });

  console.log(`[upload] appid=${APPID} version=${args.version}`);
  console.log(`[upload] desc=${args.desc}`);
  const result = await ci.upload({
    project,
    version: args.version,
    desc: args.desc,
    setting: { es6: false, minify: false },
    onProgressUpdate: () => {},
  });
  console.log('[upload] 完成:', JSON.stringify(result || {}));
}

main().catch((err) => {
  console.error('[upload] 失败:', err && err.message ? err.message : err);
  process.exit(1);
});
