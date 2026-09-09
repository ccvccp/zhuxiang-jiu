/**
 * 全局配置 · 环境地址与业务常量统一管理
 * ============================================================
 * 修改后端地址 / 客服电话 / 签到奖励等, 只需改这一个文件
 */

// ============================================================
// 后端地址
// ============================================================
// 三级主机判定(H5 按页面域名自动选择, 换环境无需改码重新构建):
//   1. 生产域名 zxjiu.com(含子域) → https://zxjiu.com(nginx 同源反代 /api)
//   2. 本机调试(localhost/127.0.0.1) → http://<同hostname>:8000(后端与本机同host)
//   3. 局域网 IP 访问(真机调试) → http://<页面host>:8000(同网段开发机后端)
//   4. 其余主机(公网 IP 直访/任意部署主机) → 页面同源(window.location.origin,
//      nginx 已同源反代 /api, 证书未就绪的 http 阶段同样可用)
// weapp 无 window, 固定生产域名(小程序合法域名要求 https)
const API_PORT = '8000';

/** 生产域名(DNS 解析指向服务器, nginx 同源反代 /api) */
export const PROD_DOMAIN = 'zxjiu.com';

/** 是否生产域名主机(含 www 等子域) */
const isProdHost = (host: string) =>
  host === PROD_DOMAIN || host.endsWith(`.${PROD_DOMAIN}`);

/** 是否本机调试主机(dev 后端挂在开发机 LAN IP) */
const isLocalDebugHost = (host: string) =>
  ['localhost', '127.0.0.1'].includes(host);

/** 是否私网 IP(局域网调试: 后端与静态服务同机, API 走 <host>:8000) */
const isPrivateIpHost = (host: string) =>
  /^10\./.test(host)
  || /^192\.168\./.test(host)
  || /^172\.(1[6-9]|2\d|3[01])\./.test(host);

const resolveApiBase = (): string => {
  if (process.env.TARO_ENV !== 'h5') {
    return `https://${PROD_DOMAIN}`; // weapp: 固定生产域名
  }
  if (typeof window === 'undefined') {
    return `http://localhost:${API_PORT}`; // SSR 边界安全默认
  }
  const { hostname, origin } = window.location;
  if (isProdHost(hostname)) return `https://${PROD_DOMAIN}`;       // 生产域名
  if (isLocalDebugHost(hostname)) return `http://${hostname}:${API_PORT}`; // 本机调试(后端同机)
  if (isPrivateIpHost(hostname)) return `http://${hostname}:${API_PORT}`;  // 局域网真机调试
  return origin; // 其余(公网 IP 直访等): 同源直连
};

export const API_BASE = resolveApiBase();

// ============================================================
// 会员身份(测试用, 接入真实登录后改为动态获取)
// ============================================================
// 当前登录会员: 李四(member_id=2, L5 钻石会员)
export const CURRENT_MEMBER_ID = '2';

// ============================================================
// 客服联系
// ============================================================
export const SERVICE_PHONE = '400-888-XXXX';

// ============================================================
// 每日签到
// ============================================================
/** 签到一次奖励积分 */
export const SIGN_IN_REWARD_POINTS = 5;
/** 签到日期本地存储 key(存储当日日期字符串, 如 2026-08-27) */
export const SIGN_IN_STORAGE_KEY = 'last_signin_date';

// ============================================================
// 首页公告轮播
// ============================================================
/** 公告切换间隔(毫秒) */
export const NOTICE_INTERVAL_MS = 3000;

// ============================================================
// 商品字段缺省值(后端字段缺失时兜底)
// ============================================================
export const PRODUCT_DEFAULTS = {
  spec: '500ml',
  abv: '42%vol',
  category: '经典',
} as const;

// ============================================================
// 会员等级
// ============================================================
/** 等级 → 显示名称 */
export const LEVEL_NAME: Record<string, string> = {
  L1: '普通会员', L2: '银卡会员', L3: '金卡会员', L4: '铂金会员', L5: '钻石会员',
};

/** 等级 → 升下一级所需成长值(简化估算) */
export const NEXT_LEVEL_POINTS: Record<string, number> = {
  L1: 1000, L2: 3000, L3: 6000, L4: 10000, L5: 10000,
};

/** 危险操作确认按钮颜色(与 theme.scss $color-error 对齐) */
export const DANGER_COLOR = '#f53f3f';

// ============================================================
// 订单状态颜色(与 theme.scss 主题色对齐)
// ============================================================
const ORDER_STATUS_COLOR: Record<string, string> = {
  '已付款': '#00b42a',   // $color-success
  '待付款': '#ff7d00',   // $color-warning
  '已取消': '#86909c',   // $color-text-tertiary
  default: '#355c44',    // $color-primary
};

/** 按订单状态取显示颜色 */
export function statusColor(status: string): string {
  return ORDER_STATUS_COLOR[status] || ORDER_STATUS_COLOR.default;
}
