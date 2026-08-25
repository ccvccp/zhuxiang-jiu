import React from 'react';
import { View, Button } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import CheckoutService from '@/services/checkout-service';

const MinePage: React.FC = () => {
  const handleClearData = () => {
    Taro.showModal({
      title: '删除我的数据',
      content: '根据《个人信息保护法》第47条，您有权删除个人信息。此操作将清除所有订单、积分、会员数据，不可恢复。确认删除？',
      confirmColor: '#e74c3c',
      success: (res) => {
        if (res.confirm) {
          CheckoutService.resetMock();
          Taro.showToast({ title: '数据已清除', icon: 'success' });
          setTimeout(() => {
            Taro.switchTab({ url: '/pages/index/index' });
          }, 1500);
        }
      }
    });
  };

  return (
    <View className={styles.page}>
      <View className={styles.title}>会员中心</View>
      <View className={styles.desc}>功能正在开发中...</View>
      <View className={styles.section}>
        <View className={styles.sectionTitle}>个人信息管理</View>
        <View className={styles.desc}>根据《个人信息保护法》，您有权查看、修改和删除个人信息。</View>
        <Button className={styles.dangerButton} onClick={handleClearData}>
          删除我的数据
        </Button>
      </View>
    </View>
  );
};

export default MinePage;
