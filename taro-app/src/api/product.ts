/**
 * 商品 API · 对接后端 /api/product/*
 * 字段映射: 后端 product → 前端 ProductVO
 */
import { request } from './request';

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

// 后端 product 字段 → 前端 VO
function mapProduct(p: any): ProductVO {
  return {
    id: p.product_id,
    name: p.name,
    price: p.price,
    originalPrice: p.original_price,
    memberPrice: p.member_price,
    stock: p.stock,
    spec: p.volume || '500ml',
    abv: p.alcohol ? `${p.alcohol}%vol` : '42%vol',
    category: p.series || '经典',
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
};
