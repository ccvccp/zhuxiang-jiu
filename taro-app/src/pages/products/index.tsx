import React, { useState, useEffect, useMemo } from 'react';
import { View, Text, ScrollView, Input } from '@tarojs/components';
import Taro from '@tarojs/taro';
import styles from './index.module.scss';
import CheckoutService from '@/services/checkout-service';
import { ProductAPI, ProductVO } from '@/api/product';
import { PRODUCT_DEFAULTS } from '@/config';

// 兜底: API 失败时用 mock 数据(保证页面可用)
function loadMockProducts(): ProductVO[] {
  const db = CheckoutService.getMockDB();
  return (db.products || []).map((p: any) => ({
    id: String(p.id),
    name: p.name,
    price: p.price,
    stock: p.stock,
    spec: p.spec || PRODUCT_DEFAULTS.spec,
    abv: p.abv || PRODUCT_DEFAULTS.abv,
    category: p.category || PRODUCT_DEFAULTS.category,
  }));
}

const ProductsPage: React.FC = () => {
  const [products, setProducts] = useState<ProductVO[]>([]);
  const [activeCategory, setActiveCategory] = useState('全部');
  const [loading, setLoading] = useState(true);
  // 搜索态: keyword 输入值 / searched 已提交关键词(null 表示非搜索模式)
  const [keyword, setKeyword] = useState('');
  const [searched, setSearched] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);

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

  // 提交搜索(空关键词拦截, 防后端 409)
  const handleSearch = async () => {
    const kw = keyword.trim();
    if (!kw) {
      Taro.showToast({ title: '请输入搜索关键词', icon: 'none' });
      return;
    }
    setSearching(true);
    try {
      const res = await ProductAPI.search(kw, 1, 50);
      setSearched(kw);
      setProducts(res.products);
    } catch (e) {
      console.warn('[products] 搜索失败:', e);
      Taro.showToast({ title: '搜索失败,请稍后再试', icon: 'none' });
    } finally {
      setSearching(false);
    }
  };

  // 清除搜索 → 恢复分类列表模式
  const handleClearSearch = async () => {
    setKeyword('');
    setSearched(null);
    setLoading(true);
    try {
      const res = await ProductAPI.list({ page: 1, page_size: 50 });
      setProducts(res.products);
    } catch (e) {
      console.warn('[products] 恢复列表失败,降级 mock:', e);
      setProducts(loadMockProducts());
    } finally {
      setLoading(false);
    }
  };

  // 分类列表(动态生成, 搜索模式下隐藏分类栏)
  const categories = useMemo(() => {
    const set = new Set<string>();
    products.forEach(p => set.add(p.category));
    return ['全部', ...Array.from(set)];
  }, [products]);

  // 过滤后的商品(搜索模式下展示原始结果, 不再按分类过滤)
  const filtered = useMemo(() => {
    if (searched !== null) return products;
    if (activeCategory === '全部') return products;
    return products.filter(p => p.category === activeCategory);
  }, [products, activeCategory, searched]);

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

      {/* 搜索栏 */}
      <View className={styles.searchBar}>
        <View className={styles.searchInputWrap}>
          <Text className={styles.searchIcon}>🔍</Text>
          <Input
            className={styles.searchInput}
            type="text"
            placeholder="搜索酒品 / 系列 / 场景"
            placeholderClass={styles.searchPlaceholder}
            value={keyword}
            onInput={(e) => setKeyword(e.detail.value)}
            onConfirm={handleSearch}
            confirmType="search"
          />
          {keyword ? (
            <Text className={styles.searchClear} onClick={() => setKeyword('')}>×</Text>
          ) : null}
        </View>
        <View
          className={styles.searchBtn}
          onClick={searched !== null ? handleClearSearch : handleSearch}
        >
          {searching ? '搜索中' : searched !== null ? '取消' : '搜索'}
        </View>
      </View>

      {/* 分类筛选栏(搜索结果模式下隐藏) */}
      {searched === null && (
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
      )}

      {/* 搜索结果提示 */}
      {searched !== null && (
        <View className={styles.searchResultTip}>
          <Text>“{searched}” 的搜索结果</Text>
          <Text className={styles.searchResultCount}>{products.length} 件商品</Text>
        </View>
      )}

      {/* 商品列表 */}
      <ScrollView className={styles.productList} scrollY>
        {loading || searching ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>⏳</View>
            <View className={styles.emptyText}>加载中...</View>
          </View>
        ) : filtered.length === 0 ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>{searched !== null ? '🔍' : '🍶'}</View>
            <View className={styles.emptyText}>
              {searched !== null ? `未找到“${searched}”相关商品` : '暂无该分类商品'}
            </View>
            {searched !== null && (
              <View className={styles.searchResetBtn} onClick={handleClearSearch}>
                浏览全部商品
              </View>
            )}
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
