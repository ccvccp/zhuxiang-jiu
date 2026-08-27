/**
 * 纯前端二维码生成器(无第三方依赖)
 * 支持: 字节模式 / M级纠错 / 版本1-7(内容最长122字节)
 * 算法参考 ISO/IEC 18004 标准
 */

// ---------- GF(256) 域运算(本原多项式 0x11D) ----------
const EXP = new Uint8Array(512);
const LOG = new Uint8Array(256);
(() => {
  let x = 1;
  for (let i = 0; i < 255; i++) {
    EXP[i] = x;
    LOG[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i++) EXP[i] = EXP[i - 255];
})();

function gmul(a: number, b: number): number {
  if (a === 0 || b === 0) return 0;
  return EXP[LOG[a] + LOG[b]];
}

// ---------- Reed-Solomon 纠错 ----------
function rsGenPoly(ecLen: number): number[] {
  let poly = [1];
  for (let i = 0; i < ecLen; i++) {
    const next = new Array<number>(poly.length + 1).fill(0);
    for (let j = 0; j < poly.length; j++) {
      next[j] ^= poly[j];
      next[j + 1] ^= gmul(poly[j], EXP[i]);
    }
    poly = next;
  }
  return poly;
}

function rsEncode(data: number[], ecLen: number): number[] {
  const gen = rsGenPoly(ecLen);
  const buf = data.concat(new Array<number>(ecLen).fill(0));
  for (let i = 0; i < data.length; i++) {
    const coef = buf[i];
    if (coef !== 0) {
      for (let j = 0; j < gen.length; j++) buf[i + j] ^= gmul(gen[j], coef);
    }
  }
  return buf.slice(data.length);
}

// ---------- 版本表(M级): [数据码字总数, 纠错码字/块, 块数, 数据码字/块] ----------
const VERSION_M: number[][] = [
  [16, 10, 1, 16],
  [28, 16, 1, 28],
  [44, 26, 1, 44],
  [64, 18, 2, 32],
  [86, 24, 2, 43],
  [108, 16, 4, 27],
  [124, 18, 4, 31],
];

// 校正图案中心坐标(版本1无)
const ALIGN: number[][] = [
  [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34], [6, 22, 38],
];

// ---------- UTF-8 编码 ----------
function toUtf8(text: string): number[] {
  const out: number[] = [];
  for (const ch of text) {
    const cp = ch.codePointAt(0) as number;
    if (cp < 0x80) out.push(cp);
    else if (cp < 0x800) out.push(0xc0 | (cp >> 6), 0x80 | (cp & 0x3f));
    else if (cp < 0x10000) out.push(0xe0 | (cp >> 12), 0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f));
    else out.push(0xf0 | (cp >> 18), 0x80 | ((cp >> 12) & 0x3f), 0x80 | ((cp >> 6) & 0x3f), 0x80 | (cp & 0x3f));
  }
  return out;
}

// ---------- 格式信息 BCH(15,5), M级=0 ----------
function formatBits(mask: number): number {
  const data = mask; // M级纠错位为 0b00
  let rem = data;
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  return ((data << 10) | rem) ^ 0x5412;
}

// ---------- 版本信息 BCH(18,6), v>=7 ----------
function versionBits(version: number): number {
  let rem = version;
  for (let i = 0; i < 12; i++) rem = (rem << 1) ^ ((rem >>> 11) * 0x1f25);
  return (version << 12) | rem;
}

// ---------- 掩码条件 ----------
const MASK_FNS: Array<(x: number, y: number) => boolean> = [
  (x, y) => (x + y) % 2 === 0,
  (_x, y) => y % 2 === 0,
  (x, _y) => x % 3 === 0,
  (x, y) => (x + y) % 3 === 0,
  (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0,
  (x, y) => (x * y) % 2 + ((x * y) % 3) === 0,
  (x, y) => ((x * y) % 2 + ((x * y) % 3)) % 2 === 0,
  (x, y) => ((x + y) % 2 + ((x * y) % 3)) % 2 === 0,
];

// ---------- 罚分评估(4条规则) ----------
function penalty(m: boolean[][]): number {
  const size = m.length;
  let score = 0;

  // 规则1: 行/列连续同色 >=5
  const runPenalty = (get: (i: number) => boolean) => {
    let s = 0, run = 1;
    for (let i = 1; i < size; i++) {
      if (get(i) === get(i - 1)) run++;
      else {
        if (run >= 5) s += run - 2;
        run = 1;
      }
    }
    if (run >= 5) s += run - 2;
    return s;
  };
  for (let y = 0; y < size; y++) score += runPenalty(i => m[y][i]);
  for (let x = 0; x < size; x++) score += runPenalty(i => m[i][x]);

  // 规则2: 2x2 同色块
  for (let y = 0; y < size - 1; y++) {
    for (let x = 0; x < size - 1; x++) {
      const c = m[y][x];
      if (m[y][x + 1] === c && m[y + 1][x] === c && m[y + 1][x + 1] === c) score += 3;
    }
  }

  // 规则3: 类定位图案 1011101 + 4个浅色
  const pat1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0];
  const pat2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1];
  const linePenalty = (line: boolean[]) => {
    let s = 0;
    for (let i = 0; i + 11 <= line.length; i++) {
      let m1 = true, m2 = true;
      for (let j = 0; j < 11; j++) {
        const v = line[i + j] ? 1 : 0;
        if (v !== pat1[j]) m1 = false;
        if (v !== pat2[j]) m2 = false;
      }
      if (m1) s += 40;
      if (m2) s += 40;
    }
    return s;
  };
  for (let y = 0; y < size; y++) {
    const line = [false, false, false, false, ...m[y], false, false, false, false];
    score += linePenalty(line);
  }
  for (let x = 0; x < size; x++) {
    const line = [false, false, false, false];
    for (let y = 0; y < size; y++) line.push(m[y][x]);
    line.push(false, false, false, false);
    score += linePenalty(line);
  }

  // 规则4: 黑白比例偏差
  let dark = 0;
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) if (m[y][x]) dark++;
  const k = Math.floor(Math.abs((dark * 100) / (size * size) - 50) / 5);
  score += k * 10;

  return score;
}

// ---------- 构造矩阵(功能图案+数据+掩码+格式信息) ----------
function buildMatrix(version: number, codewords: number[], mask: number): boolean[][] {
  const size = version * 4 + 17;
  const modules: boolean[][] = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));
  const isFunc: boolean[][] = Array.from({ length: size }, () => new Array<boolean>(size).fill(false));

  const setFn = (x: number, y: number, dark: boolean) => {
    modules[y][x] = dark;
    isFunc[y][x] = true;
  };

  // 定位图案(含分隔符)
  const drawFinder = (cx: number, cy: number) => {
    for (let dy = -4; dy <= 4; dy++) {
      for (let dx = -4; dx <= 4; dx++) {
        const x = cx + dx, y = cy + dy;
        if (x < 0 || x >= size || y < 0 || y >= size) continue;
        const dist = Math.max(Math.abs(dx), Math.abs(dy));
        setFn(x, y, dist !== 2 && dist !== 4);
      }
    }
  };
  drawFinder(3, 3);
  drawFinder(size - 4, 3);
  drawFinder(3, size - 4);

  // 校正图案(跳过与定位图案重叠处)
  const centers = ALIGN[version - 1];
  for (const cy of centers) {
    for (const cx of centers) {
      if ((cx === 6 && cy === 6) || (cx === 6 && cy === size - 7) || (cx === size - 7 && cy === 6)) continue;
      for (let dy = -2; dy <= 2; dy++) {
        for (let dx = -2; dx <= 2; dx++) {
          setFn(cx + dx, cy + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
        }
      }
    }
  }

  // 时序图案
  for (let i = 8; i < size - 8; i++) {
    setFn(6, i, i % 2 === 0);
    setFn(i, 6, i % 2 === 0);
  }

  // 预留格式信息区
  for (let i = 0; i < 9; i++) {
    if (i !== 6) {
      setFn(8, i, false);
      setFn(i, 8, false);
    }
  }
  for (let i = 0; i < 8; i++) {
    setFn(8, size - 1 - i, false);
    setFn(size - 1 - i, 8, false);
  }
  // 暗模块
  setFn(8, size - 8, true);

  // 预留版本信息区(v>=7)
  if (version >= 7) {
    for (let i = 0; i < 18; i++) {
      const a = size - 11 + (i % 3);
      const b = Math.floor(i / 3);
      isFunc[b][a] = true;
      isFunc[a][b] = true;
    }
  }

  // 数据码字蛇形放置
  let bitIndex = 0;
  const totalBits = codewords.length * 8;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let vert = 0; vert < size; vert++) {
      for (let j = 0; j < 2; j++) {
        const x = right - j;
        const upward = ((right + 1) & 2) === 0;
        const y = upward ? size - 1 - vert : vert;
        if (!isFunc[y][x] && bitIndex < totalBits) {
          modules[y][x] = ((codewords[bitIndex >>> 3] >>> (7 - (bitIndex & 7))) & 1) !== 0;
          bitIndex++;
        }
      }
    }
  }

  // 施加掩码(仅数据区)
  const maskFn = MASK_FNS[mask];
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      if (!isFunc[y][x] && maskFn(x, y)) modules[y][x] = !modules[y][x];
    }
  }

  // 绘制格式信息
  const fmt = formatBits(mask);
  const getBit = (v: number, i: number) => ((v >>> i) & 1) !== 0;
  for (let i = 0; i <= 5; i++) setFn(8, i, getBit(fmt, i));
  setFn(8, 7, getBit(fmt, 6));
  setFn(8, 8, getBit(fmt, 7));
  setFn(7, 8, getBit(fmt, 8));
  for (let i = 9; i < 15; i++) setFn(14 - i, 8, getBit(fmt, i));
  for (let i = 0; i < 8; i++) setFn(size - 1 - i, 8, getBit(fmt, i));
  for (let i = 8; i < 15; i++) setFn(8, size - 15 + i, getBit(fmt, i));
  setFn(8, size - 8, true);

  // 绘制版本信息(v>=7)
  if (version >= 7) {
    const vb = versionBits(version);
    for (let i = 0; i < 18; i++) {
      const bit = getBit(vb, i);
      setFn(size - 11 + (i % 3), Math.floor(i / 3), bit);
      setFn(Math.floor(i / 3), size - 11 + (i % 3), bit);
    }
  }

  return modules;
}

/**
 * 生成二维码矩阵
 * @param text 内容文本(最长122字节)
 * @returns 二维布尔矩阵, true=黑
 */
export function qrMatrix(text: string): boolean[][] {
  const bytes = toUtf8(text);
  if (bytes.length === 0) throw new Error('QR content is empty');
  if (bytes.length > 122) throw new Error('QR content too long (max 122 bytes)');

  // 选择版本
  let version = 1;
  while (version <= 7 && VERSION_M[version - 1][0] * 8 < 12 + bytes.length * 8) version++;
  if (version > 7) throw new Error('QR content too long');

  const [dataLen, ecLen, numBlocks, blockLen] = VERSION_M[version - 1];

  // 编码位流: 模式(4bit) + 长度(8bit) + 数据
  const bits: number[] = [];
  const push = (val: number, len: number) => {
    for (let i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1);
  };
  push(0b0100, 4);
  push(bytes.length, 8);
  for (const b of bytes) push(b, 8);

  // 终止符 + 补齐到字节
  const capBits = dataLen * 8;
  const termLen = Math.min(4, capBits - bits.length);
  for (let i = 0; i < termLen; i++) bits.push(0);
  while (bits.length % 8 !== 0) bits.push(0);

  const data: number[] = [];
  for (let i = 0; i < bits.length; i += 8) {
    let b = 0;
    for (let j = 0; j < 8; j++) b = (b << 1) | bits[i + j];
    data.push(b);
  }
  // 填充字节 0xEC 0x11
  let padIdx = 0;
  while (data.length < dataLen) data.push(padIdx++ % 2 === 0 ? 0xec : 0x11);

  // 分块纠错 + 交织
  const blocks: number[][] = [];
  const ecs: number[][] = [];
  for (let i = 0; i < numBlocks; i++) {
    const blk = data.slice(i * blockLen, (i + 1) * blockLen);
    blocks.push(blk);
    ecs.push(rsEncode(blk, ecLen));
  }
  const codewords: number[] = [];
  for (let i = 0; i < blockLen; i++) for (const b of blocks) codewords.push(b[i]);
  for (let i = 0; i < ecLen; i++) for (const e of ecs) codewords.push(e[i]);

  // 尝试全部8种掩码, 罚分最低者胜出
  let best: boolean[][] | null = null;
  let bestScore = Infinity;
  for (let mask = 0; mask < 8; mask++) {
    const m = buildMatrix(version, codewords, mask);
    const s = penalty(m);
    if (s < bestScore) {
      bestScore = s;
      best = m;
    }
  }
  return best as boolean[][];
}

/**
 * 将二维码矩阵绘制到 Canvas 上下文
 * 兼容旧版 canvasContext(setFillStyle) 与 2d 节点(fillStyle)
 * @param ctx canvas 上下文(旧版 createCanvasContext 或 2d getContext)
 * @param matrix qrMatrix 生成的矩阵
 * @param sizePx 画布边长(px)
 */
export function renderQrMatrix(ctx: any, matrix: boolean[][], sizePx: number): void {
  const n = matrix.length;
  const quiet = 4; // 静区边距
  const cell = sizePx / (n + quiet * 2);
  const setFill = (color: string) => {
    if (typeof ctx.setFillStyle === 'function') ctx.setFillStyle(color);
    else ctx.fillStyle = color;
  };
  setFill('#ffffff');
  ctx.fillRect(0, 0, sizePx, sizePx);
  setFill('#000000');
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      if (matrix[y][x]) {
        const px = Math.floor((x + quiet) * cell);
        const py = Math.floor((y + quiet) * cell);
        const sz = Math.ceil(cell);
        ctx.fillRect(px, py, sz, sz);
      }
    }
  }
}
