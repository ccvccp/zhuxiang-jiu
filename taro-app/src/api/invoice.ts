/**
 * 发票 API · 对接后端 /api/invoice/*
 * AI 无感开票: 抬头簿管理 + 手动补开 + 红冲申诉
 */
import { request } from './request';

export interface InvoiceTitleVO {
  id: number;
  titleType: string;    // personal | company
  title: string;
  taxNo: string;        // 企业必填
  isDefault: boolean;
  useCount: number;
}

export interface InvoiceVO {
  invoiceNo: string;
  orderId: string;
  amount: number;
  status: string;
  title: string;
  issueType: string;    // auto | manual
  issuedAt: string;
}

export const InvoiceAPI = {
  /** 抬头簿列表 */
  async titles(): Promise<InvoiceTitleVO[]> {
    const res = await request<any>({ url: '/api/invoice/titles' });
    return (res.titles || []).map((t: any) => ({
      id: Number(t.titleId ?? t.id ?? 0),
      titleType: t.titleType || 'personal',
      title: t.title || '',
      taxNo: t.taxNo || '',
      isDefault: Boolean(t.isDefault),
      useCount: t.useCount || 0,
    }));
  },

  /** 新增抬头(首个自动成为默认) */
  async addTitle(data: {
    titleType: string; title: string; taxNo?: string; isDefault?: boolean;
  }): Promise<InvoiceTitleVO[]> {
    const res = await request<any>({
      url: '/api/invoice/titles',
      method: 'POST',
      data: {
        titleType: data.titleType,
        title: data.title,
        taxNo: data.taxNo || '',
        isDefault: data.isDefault ?? false,
      },
    });
    return (res.titles || []).map((t: any) => ({
      id: Number(t.titleId ?? t.id ?? 0),
      titleType: t.titleType || 'personal',
      title: t.title || '',
      taxNo: t.taxNo || '',
      isDefault: Boolean(t.isDefault),
      useCount: t.useCount || 0,
    }));
  },

  /** 设为默认抬头 */
  async setDefault(titleId: number): Promise<void> {
    await request<any>({
      url: `/api/invoice/titles/${titleId}/default`,
      method: 'POST',
    });
  },

  /** 删除抬头 */
  async removeTitle(titleId: number): Promise<void> {
    await request<any>({
      url: `/api/invoice/titles/${titleId}`,
      method: 'DELETE',
    });
  },

  /** 我的发票列表(自动+手动, 含红冲票) */
  async mine(): Promise<InvoiceVO[]> {
    const res = await request<any>({ url: '/api/invoice/mine' });
    return (res.invoices || []).map((i: any) => ({
      invoiceNo: i.invoiceNo || i.invoice_no || '',
      orderId: i.orderId || i.order_id || '',
      amount: i.amount || 0,
      status: i.status || '',
      title: i.title || '',
      issueType: i.issueType || i.issue_type || 'auto',
      issuedAt: i.issuedAt || i.issued_at || '',
    }));
  },

  /** 手动触发开票(无感漏网兜底; titleId 缺省用默认抬头) */
  async requestInvoice(orderId: string, titleId?: number): Promise<any> {
    return await request<any>({
      url: `/api/invoice/orders/${orderId}/request`,
      method: 'POST',
      data: titleId ? { titleId } : {},
    });
  },

  /** 红冲申诉(发票被拦/误拦时) */
  async appeal(orderId: string, reason: string): Promise<any> {
    return await request<any>({
      url: `/api/invoice/orders/${orderId}/appeal`,
      method: 'POST',
      data: { reason },
    });
  },
};
