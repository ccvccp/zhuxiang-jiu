import React, { useState, useEffect, useMemo } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import CheckoutService from '@/services/checkout-service';

// 商品分类映射(按名称关键词推断)
function classify(name: string): string {
  if (name.includes('珍藏')) return '珍藏';
  if (name.includes('佳酿')) return '佳酿';
  return '经典';
}

// 解析规格(如 "竹奕·竹香经典 500ml" → "500ml")
function parseSpec(name: string): string {
  const m = name.match(/(\d+ml)/i);
  return m ? m[1] : '500ml';
}

const ProductsPage: React.FC = () => {
  const [products, setProducts] = useState<any[]>([]);
  const [activeCategory, setActiveCategory] = useState('全部');

  useEffect(() => {
    const db = CheckoutService.getMockDB();
    setProducts(db.products || []);
  }, []);

  // 分类列表(动态生成)
  const categories = useMemo(() => {
    const set = new Set<string>();
    products.forEach(p => set.add(classify(p.name)));
    return ['全部', ...Array.from(set)];
  }, [products]);

  // 过滤后的商品
  const filtered = useMemo(() => {
    if (activeCategory === '全部') return products;
    return products.filter(p => classify(p.name) === activeCategory);
  }, [products, activeCategory]);

  const handleBuy = (product: any) => {
    // 跳转商品详情页
    Taro.navigateTo({
      url: `/pages/product-detail/index?id=${product.id}`
    });
  };

  return (
    <View className={styles.page}>
      <View className={styles.header}>
        <View className={styles.title}>竹香佳酿</View>
        <View className={styles.subtitle}>竹韵佳酿 · 雅致生活</View>
      </View>

      {/* 分类筛选栏 */}
      <ScrollView className={styles.categoryBar} scrollX>
        {categories.map(cat => (
          <View
            key={cat}
            className={`${styles.categoryItem} ${activeCategory === cat ? styles.categoryActive : ''}`}
            onClick={() => setActiveCategory(cat)}
          >
            {cat}
          </View>
        ))}
      </ScrollView>

      {/* 商品列表 */}
      <ScrollView className={styles.productList} scrollY>
        {filtered.length === 0 ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>🍶</View>
            <View className={styles.emptyText}>暂无该分类商品</View>
          </View>
        ) : (
          filtered.map(p => (
            <View key={p.id} className={styles.productCard} onClick={() => handleBuy(p)}>
              <View className={styles.productThumb}>
                <View className={styles.productEmoji}>🍶</View>
                <View className={styles.specTag}>{parseSpec(p.name)}</View>
              </View>
              <View className={styles.productInfo}>
                <View className={styles.productName}>{p.name}</View>
                <View className={styles.productMeta}>
                  <View className={styles.categoryTag}>{classify(p.name)}</View>
                  <View className={styles.productStock}>
                    {p.stock > 10 ? `库存 ${p.stock} 瓶` : `仅剩 ${p.stock} 瓶`}
                  </View>
                </View>
                <View className={styles.productBottom}>
                  <View className={styles.productPrice}>
                    <Text className={styles.priceSymbol}>¥</Text>
                    <Text className={styles.priceValue}>{p.price}</Text>
                  </View>
                  <View
                    className={styles.buyButton}
                    onClick={(e) => { e.stopPropagation(); handleBuy(p); }}
                  >
                    立即购买
                  </View>
                </View>
              </View>
            </View>
          ))
        )}
      </ScrollView>
    </View>
  );
};

export default ProductsPage;
