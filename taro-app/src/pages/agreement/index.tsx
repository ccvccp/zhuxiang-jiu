/**
 * agreement.ts · 用户协议页面
 * 竹香酒小程序用户服务协议
 */

import React from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import styles from './agreement.module.scss';

const AgreementPage: React.FC = () => {
  return (
    <View className={styles.page}>
      <ScrollView scrollY className={styles.content}>
        <View className={styles.title}>竹香酒小程序用户协议</View>
        <View className={styles.updateTime}>最后更新：2026年8月18日</View>

        <View className={styles.section}>
          <View className={styles.sectionTitle}>一、服务说明</View>
          <View className={styles.paragraph}>
            竹香酒小程序（以下简称"本服务"）由竹香酒官方运营，提供竹香酒系列商品的展示、购买、结算与售后服务。本服务基于 Taro 多端架构，支持微信小程序、支付宝小程序、H5 等多端运行。
          </View>
        </View>

        <View className={styles.section}>
          <View className={styles.sectionTitle}>二、会员体系</View>
          <View className={styles.list}>
            <View className={styles.listItem}>1. 会员等级：L1-L5，L5 为 SVIP 超级会员</View>
            <View className={styles.listItem}>2. 积分：竹叶（100 竹叶 = ¥1），24 个月滚动过期</View>
            <View className={styles.listItem}>3. 积分抵扣上限：订单金额的 30%</View>
            <View className={styles.listItem}>4. 等级加成：L3 +2%，L4 +5%，L5 +8%（积分入账）</View>
            <View className={styles.listItem}>5. SVIP 升级条件：累计消费 ≥ ¥9999 或付费 ¥99/年</View>
          </View>
        </View>

        <View className={styles.section}>
          <View className={styles.sectionTitle}>三、订单与发货</View>
          <View className={styles.list}>
            <View className={styles.listItem}>1. 订单结算：9 阶段事务（预检→订单→库存→券→积分→分润→支付→提交）</View>
            <View className={styles.listItem}>2. 发货方路由：认领区域→该代理商发货+售后；未认领区域→厂家直供</View>
            <View className={styles.listItem}>3. 运费规则：购买两瓶免运费，否则 ¥12 运费</View>
            <View className={styles.listItem}>4. 厂家→代理商服务费：订单金额 5% 同品分润（认领区域）</View>
          </View>
        </View>

        <View className={styles.section}>
          <View className={styles.sectionTitle}>四、分润规则</View>
          <View className={styles.list}>
            <View className={styles.listItem}>1. 无代理商区域：平台 80% + 酒店 20%</View>
            <View className={styles.listItem}>2. 认领代理商区域：平台 80% + 酒店 20% + 厂家 5% 同品分润给代理商</View>
          </View>
        </View>

        <View className={styles.section}>
          <View className={styles.sectionTitle}>五、合规声明</View>
          <View className={styles.list}>
            <View className={styles.listItem}>1. 本服务销售的酒类商品，已取得《食品经营许可证》（含酒类销售许可）</View>
            <View className={styles.listItem}>2. 酒类广告已标注"广告"标识，并包含"过量饮酒有害健康"健康警示语</View>
            <View className={styles.listItem}>3. 本服务不向未成年人销售酒类商品</View>
            <View className={styles.listItem}>4. 酒类广告不含极端词（最/第一/唯一）、饮酒动作、医疗暗示等违规内容</View>
          </View>
        </View>

        <View className={styles.section}>
          <View className={styles.sectionTitle}>六、退换货政策</View>
          <View className={styles.paragraph}>
            认领区域的售后服务由对应代理商提供；未认领区域由厂家提供售后服务。退换货政策遵循《消费者权益保护法》。
          </View>
        </View>

        <View className={styles.section}>
          <View className={styles.sectionTitle}>七、免责条款</View>
          <View className={styles.paragraph}>
            本服务不对因不可抗力导致的订单延迟或损失承担责任。酒类商品为特殊商品，请理性消费。
          </View>
        </View>

        <View className={styles.warning}>
          过量饮酒有害健康 · 未成年人禁止饮酒
        </View>
      </ScrollView>
    </View>
  );
};

export default AgreementPage;
