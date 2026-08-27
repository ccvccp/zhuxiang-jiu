import { useEffect } from 'react';
import Taro, { useDidShow, useDidHide, useLaunch } from '@tarojs/taro';
import { applyActiveTheme } from '@/services/theme-service';
// 全局样式
import './app.scss';

// 全局系统信息类型
interface SystemInfo {
  statusBarHeight?: number;
  navBarHeight?: number;
  safeAreaInsets?: { top: number; bottom: number; left: number; right: number };
  platform?: string;
  system?: string;
  pixelRatio?: number;
  windowWidth?: number;
  windowHeight?: number;
}

// 多端通用系统信息注入（H5/微信小程序/支付宝小程序/抖音小程序/APP）
function initSystemInfo(): SystemInfo {
  try {
    const info = Taro.getSystemInfoSync();
    const result: SystemInfo = {
      statusBarHeight: info.statusBarHeight,
      platform: info.platform,
      system: info.system,
      pixelRatio: info.pixelRatio,
      windowWidth: info.windowWidth,
      windowHeight: info.windowHeight,
      safeAreaInsets: (info as any).safeAreaInsets || { top: 0, bottom: 0, left: 0, right: 0 }
    };
    // 计算导航栏高度（仅小程序端有原生导航栏，H5/APP 自定义导航时使用）
    if (info.statusBarHeight !== undefined) {
      // 微信小程序: 胶囊按钮高度约 32px + 上下间距 8px*2 = 48px，加上 statusBarHeight
      const navBarH = info.statusBarHeight + 44;
      result.navBarHeight = navBarH;
    }
    // 全局注入供页面组件使用
    (Taro as any).$systemInfo = result;
    return result;
  } catch (e) {
    console.warn('[App] getSystemInfoSync failed:', e);
    return {};
  }
}

function App(props) {
  // 可以使用所有的 React Hooks
  useEffect(() => {
    initSystemInfo();
  }, []);

  // 小程序 onLaunch
  useLaunch(() => {
    initSystemInfo();
    // 主题运行时引擎: 拉取激活主题并应用到导航栏/tabBar(失败静默降级)
    applyActiveTheme();
  });

  // 对应 onShow
  useDidShow(() => {});

  // 对应 onHide
  useDidHide(() => {});

  return props.children;
}

export default App;
