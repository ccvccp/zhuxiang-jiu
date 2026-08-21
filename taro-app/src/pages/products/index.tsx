import React from 'react';
import { View, Text } from '@tarojs/components';
import styles from './index.module.scss';

const ProductsPage: React.FC = () => {
  return (
    <View className={styles.page}>
      <View className={styles.title}>商品中心</View>
      <View className={styles.desc}>功能正在开发中...</View>
    </View>
  );
};

export default ProductsPage;
