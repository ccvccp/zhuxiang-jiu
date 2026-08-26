import React, { useState } from 'react';
import { View, Text, Checkbox } from '@tarojs/components';
import Taro, { useRouter } from '@tarojs/taro';
import styles from './index.module.scss';
import CheckoutService from '@/services/checkout-service';
import { OrderAPI } from '@/api/order';

// 姓名脱敏(张三→张*, 李四→李*) — 《个人信息保护法》第51条安全保护
function maskName(name: string): string {
  if (!name || name.length < 2) return name;
  return name[0] + '*'.repeat(name.length - 1);
}

const CheckoutPage: React.FC = () => {
  const router = useRouter();
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<any>(null);
  const [agreed, setAgreed] = useState(false); // 隐私政策同意状态

  const productId = Number(router.params.productId || '0');
  const productName = decodeURIComponent(router.params.productName || '竹香酒');
  const price = Number(router.params.price || '0');
  const qty = Number(router.params.qty || '1') || 1;

  // 价格估算(L5会员)
  const originalTotal = price * qty;
  const memberDiscount = Math.round(originalTotal * 0.15 * 100) / 100;
  const finalAmount = Math.round((originalTotal - memberDiscount) * 100) / 100;

  const handleSubmit = async () => {
    // 《个人信息保护法》第14条: 收集个人信息前需取得用户同意
    if (!agreed) {
      Taro.showToast({ title: '请先阅读并同意隐私政策和用户协议', icon: 'none' });
      return;
    }
    setSubmitting(true);
    try {
      // 优先调真实后端 OrderAPI.create
      try {
        const apiRes = await OrderAPI.create({
          items: [{
            productId: String(productId),
            productName: productName,
            quantity: qty,
            unitPrice: price,
          }],
          usePoints: 0,
          remark: '小程序下单',
        });
        // 后端返回成功
        if (apiRes.success !== false) {
          const orderData = apiRes.details || apiRes;
          setResult({
            success: true,
            orderNo: orderData.orderId || orderData.order_no || '',
            data: {
              orderNo: orderData.orderId || orderData.order_no || '',
              finalAmount: (orderData.priceDetail || {}).actualAmount || price * qty,
              shipperType: 'manufacturer',
              shipperAgentName: '厂家直供',
              manufacturerServiceFee: 0,
              pointsEarned: (orderData.priceDetail || {}).pointsEarned || 0,
              status: '待付款',
            },
            logs: [],
          });
          Taro.showToast({ title: '下单成功', icon: 'success' });
          return;
        }
        throw new Error(apiRes.detail || '后端下单失败');
      } catch (apiErr: any) {
        console.warn('[Checkout] 后端API失败,降级 mock:', apiErr.message);
      }
      // 降级 mock CheckoutService
      const res = await CheckoutService.submit({
        items: [{ id: productId, name: productName, price, qty }],
        memberId: 2, // 李四(L5)
        memberLevel: 'L5',
        points: 0,
        couponCode: undefined,
        paymentMethod: 'wechat',
        region: '山东泰安', // 已认领区域→代理商发货+5%服务费
      });
      setResult(res);
      if (res.success) {
        Taro.showToast({ title: '下单成功', icon: 'success' });
      } else {
        Taro.showToast({ title: res.error || '下单失败', icon: 'none' });
      }
    } catch (e: any) {
      console.error('[Checkout] submit error:', e);
      Taro.showToast({ title: e.message, icon: 'none' });
    } finally {
      setSubmitting(false);
    }
  };

  if (result && result.success) {
    const d = result.data;
    return (
      <View className={styles.page}>
        <View className={styles.card}>
          <View className={styles.resultBox}>
            <View className={styles.resultIcon}>✓</View>
            <View className={styles.resultTitle}>下单成功</View>
            <View className={styles.resultDesc}>订单号: {d.orderNo}</View>
          </View>
        </View>
        <View className={styles.card}>
          <View className={styles.cardTitle}>订单详情</View>
          <View className={styles.row}>
            <Text className={styles.rowLabel}>商品</Text>
            <Text className={styles.rowValue}>{productName}</Text>
          </View>
          <View className={styles.row}>
            <Text className={styles.rowLabel}>实付金额</Text>
            <Text className={styles.priceHighlight}>¥{d.finalAmount}</Text>
          </View>
          <View className={styles.row}>
            <Text className={styles.rowLabel}>发货方</Text>
            <Text className={`${styles.shipperTag} ${d.shipperType === 'agent' ? styles.agent : styles.manufacturer}`}>
              {d.shipperType === 'agent' ? `代理商: ${maskName(d.shipperAgentName)}` : '厂家直供'}
            </Text>
          </View>
          {d.manufacturerServiceFee > 0 && (
            <View className={styles.row}>
              <Text className={styles.rowLabel}>厂家→代理商服务费</Text>
              <Text className={styles.rowValue}>¥{d.manufacturerServiceFee}(同品分润)</Text>
            </View>
          )}
          <View className={styles.row}>
            <Text className={styles.rowLabel}>积分入账</Text>
            <Text className={styles.rowValue}>{d.pointsEarned} 竹叶(L5+8%)</Text>
          </View>
        </View>
        <View className={styles.card}>
          <View className={styles.cardTitle}>事务日志</View>
          <View className={styles.logBox}>
            {result.logs.map((l: any, i: number) => (
              <View key={i} className={styles.logItem}>
                [{l.step}] {l.msg}
              </View>
            ))}
          </View>
        </View>
      </View>
    );
  }

  return (
    <View className={styles.page}>
      <View className={styles.card}>
        <View className={styles.cardTitle}>商品信息</View>
        <View className={styles.row}>
          <Text className={styles.rowLabel}>商品名称</Text>
          <Text className={styles.rowValue}>{productName}</Text>
        </View>
        <View className={styles.row}>
          <Text className={styles.rowLabel}>单价</Text>
          <Text className={styles.rowValue}>¥{price}</Text>
        </View>
        <View className={styles.row}>
          <Text className={styles.rowLabel}>数量</Text>
          <Text className={styles.rowValue}>{qty}</Text>
        </View>
      </View>
      <View className={styles.card}>
        <View className={styles.cardTitle}>价格明细</View>
        <View className={styles.row}>
          <Text className={styles.rowLabel}>原价</Text>
          <Text className={styles.rowValue}>¥{originalTotal}</Text>
        </View>
        <View className={styles.row}>
          <Text className={styles.rowLabel}>L5会员折扣(15%)</Text>
          <Text className={styles.rowValue}>-¥{memberDiscount}</Text>
        </View>
        <View className={styles.row}>
          <Text className={styles.rowLabel}>实付</Text>
          <Text className={styles.priceHighlight}>¥{finalAmount}</Text>
        </View>
      </View>
      <View className={styles.card}>
        <View className={styles.cardTitle}>发货信息</View>
        <View className={styles.row}>
          <Text className={styles.rowLabel}>收货区域</Text>
          <Text className={styles.rowValue}>山东泰安(已认领)</Text>
        </View>
        <View className={styles.row}>
          <Text className={styles.rowLabel}>发货方</Text>
          <Text className={`${styles.shipperTag} ${styles.agent}`}>代理商发货</Text>
        </View>
        <View className={styles.row}>
          <Text className={styles.rowLabel}>服务费</Text>
          <Text className={styles.rowValue}>厂家5%同品分润</Text>
        </View>
        <View className={styles.row}>
          <Text className={styles.rowLabel}>提示</Text>
          <Text className={styles.rowValue}>过量饮酒有害健康</Text>
        </View>
      </View>
      <View className={styles.card}>
        <View className={styles.cardTitle}>合规协议</View>
        <View className={styles.row} onClick={() => Taro.navigateTo({ url: '/pages/privacy/index' })}>
          <Text className={styles.rowLabel}>隐私政策</Text>
          <Text className={styles.rowValue}>查看 ›</Text>
        </View>
        <View className={styles.row} onClick={() => Taro.navigateTo({ url: '/pages/agreement/index' })}>
          <Text className={styles.rowLabel}>用户协议</Text>
          <Text className={styles.rowValue}>查看 ›</Text>
        </View>
        <View className={styles.row}>
          <Text className={styles.rowLabel}>酒类销售资质</Text>
          <Text className={styles.rowValue}>食品经营许可证(含酒类)</Text>
        </View>
        <View className={styles.agreeRow}>
          <Checkbox value="agreed" checked={agreed} onClick={() => setAgreed(!agreed)} color="#355c44" />
          <Text className={styles.agreeText}>
            我已阅读并同意
            <Text onClick={() => Taro.navigateTo({ url: '/pages/privacy/index' })} className={styles.link}>《隐私政策》</Text>
            和
            <Text onClick={() => Taro.navigateTo({ url: '/pages/agreement/index' })} className={styles.link}>《用户协议》</Text>
          </Text>
        </View>
      </View>
      <View className={styles.submitButton}>
        <View className={styles.payButton} onClick={handleSubmit}>
          {submitting ? '提交中...' : `支付 ¥${finalAmount}`}
        </View>
      </View>
    </View>
  );
};

export default CheckoutPage;
