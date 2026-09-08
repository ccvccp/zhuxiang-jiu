/**
 * 商品 API · 对接后端 /api/product/*
 * 字段映射: 后端 product → 前端 ProductVO
 */
import { request } from './request';
import { PRODUCT_DEFAULTS } from '@/config';

export interface ProductVO {
  id: string;
  name: string;
  price: number;
  originalPrice?: number;
  memberPrice?: number;
  stock: number;
  spec: string;       // volume
  abv: string;        // alcohol + '%vol'
  category: string;   // series
  subtitle?: string;
  brand?: string;
  salesMonthly?: number;
}

/** 商品评价 VO(后端 reviews 存储结构直映射) */
export interface ReviewVO {
  id: string;
  nickname: string;
  rating: number;      // 1-5
  content: string;
  createdAt: string;
}

// 后端 product 字段 → 前端 VO
function mapProduct(p: any): ProductVO {
  return {
    id: p.product_id,
    name: p.name,
    price: p.price,
    originalPrice: p.original_price,
    memberPrice: p.member_price,
    stock: p.stock,
    spec: p.volume || PRODUCT_DEFAULTS.spec,
    abv: p.alcohol ? `${p.alcohol}%vol` : PRODUCT_DEFAULTS.abv,
    category: p.series || PRODUCT_DEFAULTS.category,
    subtitle: p.subtitle,
    brand: p.brand,
    salesMonthly: p.sales_monthly,
  };
}

export const ProductAPI = {
  /** 商品列表(筛选+排序+分页) */
  async list(params?: {
    series?: string;
    sort?: string;
    page?: number;
    page_size?: number;
  }): Promise<{ products: ProductVO[]; total: number; page: number; totalPages: number }> {
    const query: string[] = [];
    if (params?.series) query.push(`series=${encodeURIComponent(params.series)}`);
    if (params?.sort) query.push(`sort=${params.sort}`);
    query.push(`page=${params?.page || 1}`);
    query.push(`page_size=${params?.page_size || 20}`);
    const qs = query.length ? `?${query.join('&')}` : '';

    const res = await request<any>({ url: `/api/product/list${qs}` });
    return {
      products: (res.products || []).map(mapProduct),
      total: res.total || 0,
      page: res.page || 1,
      totalPages: res.totalPages || 1,
    };
  },

  /** 商品详情 */
  async detail(productId: string): Promise<ProductVO & { description?: string }> {
    const res = await request<any>({ url: `/api/product/${productId}` });
    const mapped = mapProduct(res.product || res);
    return {
      ...mapped,
      description: (res.product || res).description || (res.product || res).subtitle || '',
    };
  },

  /** 分类导航 */
  async categories(): Promise<any> {
    return await request<any>({ url: '/api/product/categories' });
  },

  /** 热销推荐 */
  async hot(limit = 6): Promise<ProductVO[]> {
    const res = await request<any>({ url: `/api/product/hot?limit=${limit}` });
    return (res.products || []).map(mapProduct);
  },

  /** 关键词搜索(匹配 name/subtitle/series/tags/scenes; 空关键词由调用方拦截) */
  async search(keyword: string, page = 1, pageSize = 20): Promise<{
    products: ProductVO[]; total: number; page: number; totalPages: number;
  }> {
    const qs = `?keyword=${encodeURIComponent(keyword)}&page=${page}&page_size=${pageSize}`;
    const res = await request<any>({ url: `/api/product/search${qs}` });
    return {
      products: (res.products || []).map(mapProduct),
      total: res.total || 0,
      page: res.page || 1,
      totalPages: res.totalPages || 1,
    };
  },

  /** 商品评价列表(分页, 附评分汇总) */
  async reviews(productId: string, page = 1, pageSize = 10): Promise<{
    reviews: ReviewVO[]; total: number; totalPages: number;
    ratingAvg: number; ratingCount: number; productName: string;
  }> {
    const res = await request<any>({
      url: `/api/product/${productId}/reviews?page=${page}&page_size=${pageSize}`,
    });
    return {
      reviews: (res.reviews || []).map((r: any) => ({
        id: r.review_id,
        nickname: r.member_nickname || '匿名会员',
        rating: r.rating || 5,
        content: r.content || '',
        createdAt: r.created_at || '',
      })),
      total: res.total || 0,
      totalPages: res.totalPages || 1,
      ratingAvg: res.ratingAvg || 0,
      ratingCount: res.ratingCount || 0,
      productName: res.productName || '',
    };
  },
};
