/**
 * 全局配置 · 环境地址与业务常量统一管理
 * ============================================================
 * 修改后端地址 / 客服电话 / 签到奖励等, 只需改这一个文件
 */

// ============================================================
// 后端地址
// ============================================================
// 生产域名(zxjiu.com): DNS 解析 → 服务器, nginx 同源部署
//   (H5 静态产物 + /api 反代后端:8000), H5/weapp 统一走 https://zxjiu.com
// 本地调试: 局域网 IP(手机与电脑连同一 WiFi), H5 端按页面域名自动判定,
//   换环境无需改码重新构建
const LAN_HOST = '192.168.0.107';
const API_PORT = '8000';

/** 生产域名(DNS 解析指向服务器, nginx 同源反代 /api) */
export const PROD_DOMAIN = 'zxjiu.com';

/** 是否生产域名主机(含 www 等子域) */
const isProdHost = (host: string) =>
  host === PROD_DOMAIN || host.endsWith(`.${PROD_DOMAIN}`);

// weapp 无 window, 构建产物面向生产域名(小程序合法域名要求 https);
// H5 按当前页面域名判定——局域网/localhost 调试走本地后端
const onProd = process.env.TARO_ENV === 'h5'
  ? (typeof window !== 'undefined' && isProdHost(window.location.hostname))
  : true;

export const API_BASE = onProd
  ? `https://${PROD_DOMAIN}`
  : `http://${LAN_HOST}:${API_PORT}`;

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
