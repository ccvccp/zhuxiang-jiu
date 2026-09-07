/**
 * 局域网 H5 静态服务 · 供手机真机预览
 * ============================================================
 * 用途: 将 taro build:h5 产物(dist/)以 0.0.0.0:8080 对局域网开放,
 *       手机与电脑连同一 WiFi 后浏览器访问 http://<电脑IP>:8080 即可。
 *
 * 运行: node scripts/serve-lan.js   (零依赖, 纯 Node 内置模块)
 *
 * 特性:
 *   - SPA 回退: 非文件路径统一回退 index.html(Taro H5 前端路由)
 *   - 正确的 MIME 类型与缓存禁用(便于改码后刷新即生效)
 *   - 目录穿越防护
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const os = require('os');

const PORT = Number(process.env.PORT || 8080);
const ROOT = path.resolve(__dirname, '..', 'dist');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.map': 'application/json',
};

function safeJoin(root, urlPath) {
  const decoded = decodeURIComponent(urlPath.split('?')[0]);
  const full = path.join(root, decoded);
  if (!full.startsWith(root)) return null; // 目录穿越防护
  return full;
}

const server = http.createServer((req, res) => {
  let filePath = safeJoin(ROOT, req.url || '/');
  if (!filePath) {
    res.writeHead(403);
    return res.end('Forbidden');
  }

  // 目录或无扩展名路径 → SPA 回退 index.html
  let stat = null;
  try { stat = fs.statSync(filePath); } catch { /* not found */ }
  if (!stat || stat.isDirectory()) {
    filePath = path.join(ROOT, 'index.html');
  }

  const ext = path.extname(filePath).toLowerCase();
  const mime = MIME[ext] || 'application/octet-stream';
  fs.readFile(filePath, (err, data) => {
    if (err) {
      console.error('[serve-lan] 读取失败:', filePath, err.message);
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      return res.end('404 Not Found');
    }
    res.writeHead(200, {
      'Content-Type': mime,
      'Cache-Control': 'no-store', // 预览场景禁缓存
      'Access-Control-Allow-Origin': '*',
    });
    res.end(data);
  });
});

server.listen(PORT, '0.0.0.0', () => {
  const nets = os.networkInterfaces();
  const lan = [];
  Object.values(nets).forEach(list => (list || []).forEach(n => {
    if (n.family === 'IPv4' && !n.internal) lan.push(n.address);
  }));
  console.log(`[serve-lan] H5 产物目录: ${ROOT}`);
  console.log(`[serve-lan] 局域网访问: http://${lan[0] || '<本机IP>'}:${PORT}`);
  console.log(`[serve-lan] 本机访问:   http://127.0.0.1:${PORT}`);
});
