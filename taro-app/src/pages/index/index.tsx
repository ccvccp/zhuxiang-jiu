import React, { useState, useEffect } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import CheckoutService from '@/services/checkout-service';

const IndexPage: React.FC = () => {
  const [products, setProducts] = useState<any[]>([]);

  useEffect(() => {
    // 初始化 mock DB
    CheckoutService.resetMock();
    const db = CheckoutService.getMockDB();
    setProducts(db.products || []);
  }, []);

  const handleBuy = (product: any) => {
    // 代理商认领山东泰安(测试用)
    CheckoutService.claim(1, '山东泰安');

    // 跳转结算页,传递商品信息
    Taro.navigateTo({
      url: `/pages/checkout/index?productId=${product.id}&productName=${encodeURIComponent(product.name)}&price=${product.price}`
    });
  };

  return (
    <View className={styles.page}>
      <View className={styles.header}>
        <View className={styles.brandTitle}>竹香酒</View>
        <View className={styles.brandDesc}>竹韵佳酿 · 雅致生活</View>
        <View className={styles.warning}>过量饮酒有害健康</View>
      </View>
      <ScrollView className={styles.productList} scrollY>
        {products.map(p => (
          <View key={p.id} className={styles.productCard} onClick={() => handleBuy(p)}>
            <View className={styles.productInfo}>
              <View className={styles.productName}>{p.name}</View>
              <View className={styles.productPrice}>¥{p.price}</View>
              <View className={styles.productStock}>库存 {p.stock} 瓶</View>
            </View>
            <View className={styles.buyButton} onClick={(e) => { e.stopPropagation(); handleBuy(p); }}>
              立即购买
            </View>
          </View>
        ))}
      </ScrollView>
    </View>
  );
};

export default IndexPage;
