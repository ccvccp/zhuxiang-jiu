/**
 * 通用导航栏 · 二级页面返回入口
 * ============================================================
 * 作用: H5 端(手机浏览器)无原生导航栏, 二级页面需要可见的
 *       "返回 + 标题"入口; weapp 端原生导航栏自带返回, 不渲染
 *       (避免双重导航)。
 *
 * 返回逻辑:
 *   页面栈 > 1 → Taro.navigateBack() 回上一页
 *   页面栈 = 1 (刷新/直达进入, 无上级) → 兜底 switchTab 回首页
 *
 * 自带等高占位(spacer), 页面无需额外 padding-top 适配。
 */
import React from 'react';
import { View, Text } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';

interface NavBarProps {
  /** 导航栏标题 */
  title: string;
  /** 自定义返回回调(如页内视图切换); 缺省走页面栈返回 */
  onBack?: () => void;
}

const NavBar: React.FC<NavBarProps> = ({ title, onBack }) => {
  // weapp 端: 原生导航栏自带返回按钮, 不渲染自定义导航
  if (process.env.TARO_ENV !== 'h5') {
    return null;
  }

  const handleBack = () => {
    if (onBack) {
      onBack();
      return;
    }
    const pages = Taro.getCurrentPages();
    if (pages.length > 1) {
      Taro.navigateBack();
    } else {
      // 栈空兜底: 直接进入本页(如刷新后), 回首页
      Taro.switchTab({ url: '/pages/index/index' });
    }
  };

  return (
    <View className={styles.wrapper}>
      {/* 占位: 高度与 fixed 导航栏一致, 防内容被遮挡 */}
      <View className={styles.spacer} />
      <View className={styles.navbar}>
        <View
          className={styles.backBtn}
          onClick={handleBack}
          hoverClass={styles.backBtnActive}
        >
          <View className={styles.arrow} />
          <Text className={styles.backText}>返回</Text>
        </View>
        <Text className={styles.title}>{title}</Text>
      </View>
    </View>
  );
};

export default NavBar;
