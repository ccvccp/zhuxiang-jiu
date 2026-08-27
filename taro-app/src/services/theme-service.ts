/**
 * 主题运行时引擎 · 拉取激活主题并即时应用
 * ============================================================
 * 基于微信原生 API(Taro.setNavigationBarColor / setTabBarStyle)
 * 实现免发版换肤: 管理端激活新主题后 C 端下次启动即生效。
 * 拉取失败静默降级(保留编译期默认竹绿主题), 不影响使用。
 */
import Taro from '@tarojs/taro';
import { SiteThemeAPI, ThemeColorsVO } from '@/api/siteTheme';

// 内存缓存(供页面读取, 如金刚区图标覆盖)
let activeTheme: {
  themeId: number;
  name: string;
  colors: ThemeColorsVO;
  icons: Record<string, any>;
} | null = null;

/** 获取当前激活主题(已拉取则用缓存) */
export function getActiveTheme() {
  return activeTheme;
}

/** 获取金刚区图标覆盖(quickGrid key → emoji) */
export function getQuickGridIcon(key: string, fallback: string): string {
  const grid = activeTheme?.icons?.quickGrid;
  return (grid && grid[key]) || fallback;
}

/**
 * 冷启动兼容: useLaunch 时首页尚未渲染, 原生导航栏/tabBar API 会失败,
 * 失败后延迟 1.2s 重试一次(此时首页已就绪)。
 */
const withLaunchRetry = async (fn: () => Promise<any>): Promise<void> => {
  try {
    await fn();
  } catch (_) {
    await new Promise(resolve => setTimeout(resolve, 1200));
    await fn().catch(() => undefined);
  }
};

/**
 * 应用激活主题到原生导航栏 + tabBar
 * 在 app 启动(useLaunch)与主题管理页激活成功后调用。
 */
export async function applyActiveTheme(): Promise<void> {
  try {
    const theme = await SiteThemeAPI.active();
    activeTheme = theme;
    const c = theme.colors || {};
    // 导航栏(frontColor 仅支持 #ffffff / #000000)
    if (c.navBar) {
      const front = c.textOnPrimary === '#000000' ? '#000000' : '#ffffff';
      await withLaunchRetry(() => Taro.setNavigationBarColor({
        frontColor: front,
        backgroundColor: c.navBar,
      }));
    }
    // tabBar
    if (c.tabSelected || c.tabColor || c.tabBg) {
      await withLaunchRetry(() => Taro.setTabBarStyle({
        selectedColor: c.tabSelected || '#355c44',
        color: c.tabColor || '#999999',
        backgroundColor: c.tabBg || '#ffffff',
        borderStyle: 'black',
      }));
    }
    console.info('[theme] 运行时主题已应用:', theme.name || theme.themeId);
  } catch (e) {
    // 静默降级: 保留编译期默认主题
    console.info('[theme] 拉取激活主题失败, 使用默认主题');
  }
}
