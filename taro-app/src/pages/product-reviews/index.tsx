import React, { useState, useEffect } from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import { useRouter } from '@tarojs/taro';
import styles from './index.module.scss';
import NavBar from '@/components/NavBar';
import { ProductAPI, ReviewVO } from '@/api/product';

const PAGE_SIZE = 10;

/**
 * 商品全量评价页 · 对接 GET /api/product/{id}/reviews
 * 分页加载 + 评分汇总 + 空态
 */
const ProductReviewsPage: React.FC = () => {
  const router = useRouter();
  const productId = router.params.id || '0';
  const productName = decodeURIComponent(router.params.name || '');

  const [reviews, setReviews] = useState<ReviewVO[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [totalPages, setTotalPages] = useState(1);
  const [ratingAvg, setRatingAvg] = useState(0);
  const [ratingCount, setRatingCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  const loadReviews = async (targetPage: number) => {
    if (targetPage > 1) {
      setLoadingMore(true);
    } else {
      setLoading(true);
    }
    try {
      const res = await ProductAPI.reviews(productId, targetPage, PAGE_SIZE);
      setReviews(prev => targetPage === 1 ? res.reviews : [...prev, ...res.reviews]);
      setPage(res.page || targetPage);
      setTotal(res.total);
      setTotalPages(res.totalPages);
      setRatingAvg(res.ratingAvg);
      setRatingCount(res.ratingCount);
    } catch (e) {
      console.warn('[product-reviews] 评价加载失败:', e);
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  };

  useEffect(() => {
    loadReviews(1);
  }, [productId]);

  const hasMore = page < totalPages;

  return (
    <View className={styles.page}>
      <NavBar title="全部评价" />
      <ScrollView scrollY className={styles.scrollView}>
        {/* 评分汇总卡 */}
        <View className={styles.scoreCard}>
          <View className={styles.scoreLeft}>
            <View className={styles.scoreNum}>{ratingAvg}</View>
            <View className={styles.scoreLabel}>综合评分</View>
          </View>
          <View className={styles.scoreRight}>
            <View className={styles.scoreStars}>
              {'★★★★★'.slice(0, Math.round(ratingAvg))}
              <Text className={styles.starsDim}>
                {'★★★★★'.slice(0, 5 - Math.round(ratingAvg))}
              </Text>
            </View>
            <View className={styles.scoreCount}>{ratingCount} 条评价 · 好评率展示</View>
          </View>
        </View>

        {productName ? (
          <View className={styles.productTip}>商品: {productName}</View>
        ) : null}

        {/* 评价列表 */}
        {loading ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>⏳</View>
            <View className={styles.emptyText}>加载中...</View>
          </View>
        ) : reviews.length === 0 ? (
          <View className={styles.empty}>
            <View className={styles.emptyIcon}>💬</View>
            <View className={styles.emptyText}>暂无评价</View>
          </View>
        ) : (
          reviews.map(r => (
            <View key={r.id} className={styles.reviewCard}>
              <View className={styles.reviewTop}>
                <View className={styles.reviewAvatar}>{r.nickname.slice(0, 1)}</View>
                <View className={styles.reviewNickname}>{r.nickname}</View>
                <View className={styles.reviewStars}>{'★'.repeat(r.rating)}</View>
              </View>
              <View className={styles.reviewContent}>{r.content}</View>
              <View className={styles.reviewDate}>{(r.createdAt || '').slice(0, 10)}</View>
            </View>
          ))
        )}

        {/* 分页加载 */}
        {hasMore && !loading && (
          <View className={styles.loadMore} onClick={() => loadReviews(page + 1)}>
            {loadingMore ? '加载中...' : `加载更多 (${reviews.length}/${total})`}
          </View>
        )}
        {!hasMore && reviews.length > 0 && (
          <View className={styles.noMore}>— 已展示全部评价 —</View>
        )}
        <View className={styles.bottomSpacer} />
      </ScrollView>
    </View>
  );
};

export default ProductReviewsPage;
