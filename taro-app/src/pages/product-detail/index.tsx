import React, { useState, useEffect } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro, { useRouter } from '@tarojs/taro';
import styles from './index.module.scss';
import CheckoutService from '@/services/checkout-service';

const ProductDetailPage: React.FC = () => {
  const router = useRouter();
  const [product, setProduct] = useState<any>(null);
  const [qty, setQty] = useState(1);

  const productId = Number(router.params.id || '0');

  useEffect(() => {
    const db = CheckoutService.getMockDB();
    const p = (db.products || []).find((x: any) => x.id === productId);
    if (p) {
      setProduct(p);
    } else {
      Taro.showToast({ title: '商品不存在', icon: 'none' });
      setTimeout(() => Taro.navigateBack(), 1500);
    }
  }, [productId]);

  const handleQtyMinus = () => setQty(q => Math.max(1, q - 1));
  const handleQtyPlus = () => setQty(q => Math.min(product?.stock || 1, q + 1));

  const handleBuyNow = () => {
    if (!product) return;
    // 代理商认领山东泰安(测试用,与首页一致)
    CheckoutService.claim(1, '山东泰安');
    Taro.navigateTo({
      url: `/pages/checkout/index?productId=${product.id}&productName=${encodeURIComponent(product.name)}&price=${product.price}&qty=${qty}`
    });
  };

  const handleContact = () => {
    Taro.showToast({ title: '客服功能开发中', icon: 'none' });
  };

  if (!product) {
    return (
      <View className={styles.page}>
        <View className={styles.loading}>
          <View className={styles.loadingIcon}>🍶</View>
          <View className={styles.loadingText}>加载中...</View>
        </View>
      </View>
    );
  }

  const stockLabel = product.stock > 10
    ? `库存充足 · ${product.stock} 瓶`
    : `仅剩 ${product.stock} 瓶`;

  return (
    <View className={styles.page}>
      <ScrollView scrollY className={styles.scrollView}>
        {/* 商品大图区 */}
        <View className={styles.heroBox}>
          <View className={styles.heroEmoji}>🍶</View>
          <View className={styles.heroTag}>{product.category}</View>
        </View>

        {/* 价格与名称 */}
        <View className={styles.priceSection}>
          <View className={styles.priceRow}>
            <Text className={styles.priceSymbol}>¥</Text>
            <Text className={styles.priceValue}>{product.price}</Text>
            <View className={styles.specBadge}>{product.spec}</View>
          </View>
          <View className={styles.productName}>{product.name}</View>
          <View className={styles.stockInfo}>{stockLabel}</View>
        </View>

        {/* 规格参数 */}
        <View className={styles.specSection}>
          <View className={styles.sectionTitle}>规格参数</View>
          <View className={styles.specGrid}>
            <View className={styles.specItem}>
              <View className={styles.specLabel}>净含量</View>
              <View className={styles.specValue}>{product.spec}</View>
            </View>
            <View className={styles.specItem}>
              <View className={styles.specLabel}>酒精度</View>
              <View className={styles.specValue}>{product.abv}</View>
            </View>
            <View className={styles.specItem}>
              <View className={styles.specLabel}>产地</View>
              <View className={styles.specValue}>{product.origin}</View>
            </View>
            <View className={styles.specItem}>
              <View className={styles.specLabel}>分类</View>
              <View className={styles.specValue}>{product.category}</View>
            </View>
          </View>
        </View>

        {/* 商品描述 */}
        <View className={styles.descSection}>
          <View className={styles.sectionTitle}>商品介绍</View>
          <View className={styles.descText}>{product.description}</View>
        </View>

        {/* 温馨提示 */}
        <View className={styles.tipSection}>
          <View className={styles.sectionTitle}>温馨提示</View>
          <View className={styles.tipList}>
            <View className={styles.tipItem}>· 过量饮酒有害健康，未成年人禁止饮酒</View>
            <View className={styles.tipItem}>· 孕妇及饮酒后驾驶人员禁止饮酒</View>
            <View className={styles.tipItem}>· 请存放于阴凉干燥处，避免阳光直射</View>
          </View>
        </View>

        <View className={styles.bottomSpacer} />
      </ScrollView>

      {/* 底部操作栏 */}
      <View className={styles.actionBar}>
        <View className={styles.actionIcon} onClick={handleContact}>
          <View className={styles.iconEmoji}>📞</View>
          <View className={styles.iconText}>客服</View>
        </View>
        <View className={styles.qtyBox}>
          <View className={styles.qtyBtn} onClick={handleQtyMinus}>-</View>
          <View className={styles.qtyValue}>{qty}</View>
          <View className={styles.qtyBtn} onClick={handleQtyPlus}>+</View>
        </View>
        <View className={styles.buyBtn} onClick={handleBuyNow}>
          立即购买
        </View>
      </View>
    </View>
  );
};

export default ProductDetailPage;
