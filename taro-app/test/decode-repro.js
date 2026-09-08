/**
 * decode-repro.js · 扫码识别率复现诊断(纯 Node, 零外部依赖)
 * ============================================================
 * 目的: 量化定位"拍照识别率低"的根因环节
 *   1. 用真实源码(src/utils/qrcode.ts, M级纠错)生成瓶码/打卡码
 *   2. 模拟手机拍照劣化: 距离(码占比) / 模糊 / 反光 / 倾斜 / 低对比
 *   3. 对比两种解码策略的失败边界:
 *        OLD: 单遍 1280 降采样(调优前)
 *        NEW: 多尺度+对比度增强+中心裁剪+attemptBoth(调优后)
 *
 * 运行: node test/decode-repro.js
 */
const fs = require('fs');
const path = require('path');
const Module = require('module');
const ts = require('typescript');
const jsQR = require('jsqr');

// ---------- 加载真实 qrcode.ts ----------
const QR_SRC = path.resolve(__dirname, '..', 'src', 'utils', 'qrcode.ts');
const { outputText } = ts.transpileModule(fs.readFileSync(QR_SRC, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 },
});
const m = { exports: {} };
new Function('module', 'exports', 'require', outputText)(m, m.exports, require);
const { qrMatrix } = m.exports;

// ---------- 像素工具(RGBA) ----------
function makeBuf(w, h, fill = 255) {
  const b = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < b.length; i += 4) {
    b[i] = b[i + 1] = b[i + 2] = fill; b[i + 3] = 255;
  }
  return b;
}

/** 矩阵 → RGBA(带 4 模块静区), pxPerModule 每模块像素 */
function rasterMatrix(matrix, pxPerModule) {
  const n = matrix.length;
  const size = (n + 8) * pxPerModule;
  const buf = makeBuf(size, size, 255);
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      if (!matrix[y][x]) continue;
      for (let dy = 0; dy < pxPerModule; dy++) {
        for (let dx = 0; dx < pxPerModule; dx++) {
          const px = ((y + 4) * pxPerModule + dy) * size + (x + 4) * pxPerModule + dx;
          buf[px * 4] = buf[px * 4 + 1] = buf[px * 4 + 2] = 0;
        }
      }
    }
  }
  return { buf, size };
}

/** 双线性把 QR 贴进大画布(码占边长 frac, 模拟距离), 背景带轻噪声 */
function composePhoto(qrBuf, qrSize, W, H, frac) {
  const out = makeBuf(W, H, 245);
  // 背景噪声(模拟瓶身/桌面纹理)
  for (let i = 0; i < out.length; i += 4) {
    const nz = (Math.random() * 16 - 8) | 0;
    out[i] += nz; out[i + 1] += nz; out[i + 2] += nz;
  }
  const dstSize = Math.round(Math.min(W, H) * frac);
  const ox = ((W - dstSize) / 2) | 0, oy = ((H - dstSize) / 2) | 0;
  for (let y = 0; y < dstSize; y++) {
    for (let x = 0; x < dstSize; x++) {
      const sx = Math.min(qrSize - 1, ((x * qrSize / dstSize) | 0));
      const sy = Math.min(qrSize - 1, ((y * qrSize / dstSize) | 0));
      const s = (sy * qrSize + sx) * 4, d = ((oy + y) * W + (ox + x)) * 4;
      out[d] = qrBuf[s]; out[d + 1] = qrBuf[s + 1]; out[d + 2] = qrBuf[s + 2];
    }
  }
  return { buf: out, w: W, h: H };
}

/** 盒式模糊(半径 r, 模拟失焦) */
function blur(buf, w, h, r) {
  const src = new Uint8ClampedArray(buf);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let sr = 0, sg = 0, sb = 0, cnt = 0;
      for (let dy = -r; dy <= r; dy++) {
        for (let dx = -r; dx <= r; dx++) {
          const ny = y + dy, nx = x + dx;
          if (ny < 0 || ny >= h || nx < 0 || nx >= w) continue;
          const i = (ny * w + nx) * 4;
          sr += src[i]; sg += src[i + 1]; sb += src[i + 2]; cnt++;
        }
      }
      const i = (y * w + x) * 4;
      buf[i] = sr / cnt; buf[i + 1] = sg / cnt; buf[i + 2] = sb / cnt;
    }
  }
  return buf;
}

/** 反光斑(白色径向渐变覆盖码区, 模拟瓶身玻璃反光) */
function glare(buf, w, h, cx, cy, radius, strength) {
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const d = Math.hypot(x - cx, y - cy) / radius;
      if (d >= 1) continue;
      const add = strength * (1 - d * d);
      const i = (y * w + x) * 4;
      buf[i] += add; buf[i + 1] += add; buf[i + 2] += add;
    }
  }
  return buf;
}

/** 低对比(亮度整体压进 [90,160], 模拟弱光/蒙尘) */
function wash(buf) {
  for (let i = 0; i < buf.length; i += 4) {
    buf[i] = 90 + (buf[i] / 255) * 70;
    buf[i + 1] = 90 + (buf[i + 1] / 255) * 70;
    buf[i + 2] = 90 + (buf[i + 2] / 255) * 70;
  }
  return buf;
}

/** 旋转(最近邻, 模拟瓶子倾斜拍摄) */
function rotate(buf, w, h, deg) {
  const rad = (deg * Math.PI) / 180;
  const out = makeBuf(w, h, 245);
  const cx = w / 2, cy = h / 2;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const dx = x - cx, dy = y - cy;
      const sx = Math.round(cx + dx * Math.cos(rad) + dy * Math.sin(rad));
      const sy = Math.round(cy - dx * Math.sin(rad) + dy * Math.cos(rad));
      if (sx < 0 || sx >= w || sy < 0 || sy >= h) continue;
      const s = (sy * w + sx) * 4, d = (y * w + x) * 4;
      out[d] = buf[s]; out[d + 1] = buf[s + 1]; out[d + 2] = buf[s + 2];
    }
  }
  return out;
}

/** 降采样(盒式平均, 模拟 canvas drawImage) */
function downscale(buf, w, h, target) {
  const r = target / Math.max(w, h);
  const dw = Math.max(1, Math.round(w * r));
  const dh = Math.max(1, Math.round(h * r));
  const out = makeBuf(dw, dh);
  for (let y = 0; y < dh; y++) {
    for (let x = 0; x < dw; x++) {
      const x0 = Math.floor(x / r), x1 = Math.max(x0 + 1, Math.floor((x + 1) / r));
      const y0 = Math.floor(y / r), y1 = Math.max(y0 + 1, Math.floor((y + 1) / r));
      let sr = 0, sg = 0, sb = 0, cnt = 0;
      for (let yy = y0; yy < y1 && yy < h; yy++) {
        for (let xx = x0; xx < x1 && xx < w; xx++) {
          const i = (yy * w + xx) * 4;
          sr += buf[i]; sg += buf[i + 1]; sb += buf[i + 2]; cnt++;
        }
      }
      const d = (y * dw + x) * 4;
      out[d] = sr / cnt; out[d + 1] = sg / cnt; out[d + 2] = sb / cnt;
    }
  }
  return { buf: out, w: dw, h: dh };
}

// ---------- 两种解码策略 ----------
/** OLD: 单遍 1280(调优前实现) */
function decodeOld(buf, w, h) {
  if (Math.max(w, h) > 1280) {
    const d = downscale(buf, w, h, 1280);
    return jsQR(d.buf, d.w, d.h)?.data ?? null;
  }
  return jsQR(buf, w, h)?.data ?? null;
}

/** NEW: 组件内新策略(多尺度+增强+裁剪)的等价复刻 */
function decodeNew(buf, w, h) {
  const cap = Math.min(Math.max(w, h), 2000);
  const long = Math.max(w, h);
  for (const target of [cap, Math.round(cap * 0.6), Math.round(cap * 0.35)]) {
    const d = downscale(buf, w, h, target);
    let r = jsQR(d.buf, d.w, d.h, { inversionAttempts: 'attemptBoth' });
    if (r?.data) return r.data;
    r = jsQR(wash(new Uint8ClampedArray(d.buf)), d.w, d.h, { inversionAttempts: 'attemptBoth' });
    if (r?.data) return r.data;
  }
  // 中心裁剪
  for (const ratio of [0.5, 0.3]) {
    const sw = Math.round(w * ratio), sh = Math.round(h * ratio);
    if (sw < 80 || sh < 80) continue;
    const sx = (w - sw) >> 1, sy = (h - sh) >> 1;
    const crop = makeBuf(sw, sh);
    for (let y = 0; y < sh; y++) {
      for (let x = 0; x < sw; x++) {
        const s = ((sy + y) * w + sx + x) * 4, d = (y * sw + x) * 4;
        crop[d] = buf[s]; crop[d + 1] = buf[s + 1]; crop[d + 2] = buf[s + 2];
      }
    }
    const r = jsQR(crop, sw, sh, { inversionAttempts: 'attemptBoth' });
    if (r?.data) return r.data;
  }
  // 小图抢救: 2x/3x 最近邻放大(边缘锐化效应, 挽救边界像素密度)
  if (Math.max(w, h) <= 1280) {
    for (const k of [2, 3]) {
      const uw = w * k, uh = h * k;
      const up = makeBuf(uw, uh);
      for (let y = 0; y < uh; y++) {
        for (let x = 0; x < uw; x++) {
          const s = ((y / k | 0) * w + (x / k | 0)) * 4, d = (y * uw + x) * 4;
          up[d] = buf[s]; up[d + 1] = buf[s + 1]; up[d + 2] = buf[s + 2];
        }
      }
      const r = jsQR(up, uw, uh, { inversionAttempts: 'attemptBoth' });
      if (r?.data) return r.data;
    }
  }
  return null;
}

// ---------- 主流程 ----------
const CONTENTS = {
  '生命码(30字符)': 'BLC-ZX52L08-20260908-000001-3F2A',
  '工段打卡码(20字符)': 'ZXBJ-TRACE:STAGE-07:v1',
};

// 场景维度: 照片分辨率(全幅拍照 vs capture 低分辨率抓帧 vs JPEG 压缩)
const CAPTURES = [
  { label: '全幅拍照 3264x2448', W: 3264, H: 2448, jpeg: 0 },
  { label: '全幅拍照+JPEG压缩', W: 3264, H: 2448, jpeg: 25 },
  { label: '中幅拍照 1920x1440', W: 1920, H: 1440, jpeg: 0 },
  { label: '中幅拍照+JPEG压缩', W: 1920, H: 1440, jpeg: 25 },
  { label: 'capture抓帧 1280x960', W: 1280, H: 960, jpeg: 0 },
  { label: 'capture抓帧 1280x960+JPEG', W: 1280, H: 960, jpeg: 25 },
  { label: 'capture低分帧 640x480', W: 640, H: 480, jpeg: 0 },
  { label: 'capture低分帧+JPEG', W: 640, H: 480, jpeg: 25 },
];

const scenarios = [
  { name: '近距离(码占30%)', frac: 0.30, degrade: null },
  { name: '中距离(码占15%)', frac: 0.15, degrade: null },
  { name: '远距离(码占8%)',  frac: 0.08, degrade: null },
  { name: '远距离+轻微失焦(码占15%,blur2)', frac: 0.15, degrade: 'blur2' },
  { name: '中距离+反光(码占30%,斑覆1/4)', frac: 0.30, degrade: 'glare' },
  { name: '中距离+倾斜15°(码占30%)', frac: 0.30, degrade: 'rot15' },
  { name: '远距离+倾斜15°(码占8%)', frac: 0.08, degrade: 'rot15' },
  { name: '中距离+低对比(码占30%)', frac: 0.30, degrade: 'wash' },
];

/** JPEG 块效应模拟(8x8 块内高频噪声 + 量化感) */
function jpegify(buf, quality) {
  for (let by = 0; by * 8 * 4 < buf.length; ) {
    for (let i = by; i < Math.min(by + 8 * 4, buf.length); i += 4) {
      if (Math.random() < 0.3) {
        const nz = (Math.random() * 2 - 1) * quality;
        buf[i] += nz; buf[i + 1] += nz; buf[i + 2] += nz;
      }
    }
    by += 8 * 4 * 8; // 粗略跳块
  }
  return buf;
}

console.log('解码管线对照: 分辨率 × 劣化场景\n');

let oldWin = 0, newWin = 0, total = 0;
for (const [label, content] of Object.entries(CONTENTS)) {
  const matrix = qrMatrix(content);
  const { buf: qrBuf, size: qrSize } = rasterMatrix(matrix, 12);
  for (const cap of CAPTURES) {
    let capOld = 0, capNew = 0;
    for (const sc of scenarios) {
      const photo = composePhoto(qrBuf, qrSize, cap.W, cap.H, sc.frac);
      let buf = photo.buf;
      if (sc.degrade === 'blur2') buf = blur(buf, cap.W, cap.H, 2);
      if (sc.degrade === 'glare') buf = glare(buf, cap.W, cap.H, cap.W / 2 - 200, cap.H / 2 - 150, Math.min(cap.W, cap.H) * 0.075, 200);
      if (sc.degrade === 'rot15') buf = rotate(buf, cap.W, cap.H, 15);
      if (sc.degrade === 'wash') buf = wash(buf);
      if (cap.jpeg) buf = jpegify(buf, cap.jpeg);
      const old = decodeOld(buf, cap.W, cap.H);
      const neu = decodeNew(buf, cap.W, cap.H);
      total++;
      if (old) { oldWin++; capOld++; }
      if (neu) { newWin++; capNew++; }
    }
    console.log(`${label.slice(0, 5)} | ${cap.label.padEnd(20)} | OLD ${capOld}/${scenarios.length} | NEW ${capNew}/${scenarios.length}`);
  }
}
console.log(`\n汇总: OLD ${oldWin}/${total}, NEW ${newWin}/${total}`);
