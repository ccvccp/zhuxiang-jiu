import React, { useState, useEffect, useMemo } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import CheckoutService from '@/services/checkout-service';
import { ProductAPI, ProductVO } from '@/api/product';

// 兜底: API 失败时用 mock 数据(保证页面可用)
function loadMockProducts(): ProductVO[] {
  const db = CheckoutService.getMockDB();
  return (db.products || []).map((p: any) => ({
    id: String(p.id),
    name: p.name,
    price: p.price,
    stock: p.stock,
    spec: p.spec || '500ml',
    abv: p.abv || '42%vol',
    category: p.category || '经典',
  }));
}

const ProductsPage: React.FC = () => {
  const [products, setProducts] = useState<ProductVO[]>([]);
  const [activeCategory, setActiveCategory] = useState('全部');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        // 优先调真实后端 API
        const res = await ProductAPI.list({ page: 1, page_size: 50 });
        setProducts(res.products);
      } catch (e) {
        console.warn('[products] API 调用失败,降级 mock:', e);
        // 降级 mock 数据
        setProducts(loadMockProducts());
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  // 分类列表(动态生成)
  const categories = useMemo(() => {
    const set = new Set<string>();
    products.forEach(p => set.add(p.category));
    return ['全部', ...Array.from(set)];
  }, [products]);

  // 过滤后的商品
  const filtered = useMemo(() => {
    if (activeCategory === '全部') return products;
    return products.filter(p => p.category === activeCategory);
  }, [products, activeCategory]);

  const handleBuy = (product: ProductVO) => {
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
        {loading ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>⏳</View>
            <View className={styles.emptyText}>加载中...</View>
          </View>
        ) : filtered.length === 0 ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>🍶</View>
            <View className={styles.emptyText}>暂无该分类商品</View>
          </View>
        ) : (
          filtered.map(p => (
            <View key={p.id} className={styles.productCard} onClick={() => handleBuy(p)}>
              <View className={styles.productThumb}>
                <View className={styles.productEmoji}>🍶</View>
                <View className={styles.specTag}>{p.spec}</View>
              </View>
              <View className={styles.productInfo}>
                <View className={styles.productName}>{p.name}</View>
                <View className={styles.productMeta}>
                  <View className={styles.categoryTag}>{p.category}</View>
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
