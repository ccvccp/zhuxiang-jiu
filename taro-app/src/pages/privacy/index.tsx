/**
 * privacy.ts · 隐私政策页面
 * 竹香酒小程序用户信息收集与使用规则
 */

import React from 'react';
import { View, Text, ScrollView } from '@tarojs/components';
import styles from './privacy.module.scss';

const PrivacyPage: React.FC = () => {
  return (
    <View className={styles.page}>
      <ScrollView scrollY className={styles.content}>
        <View className={styles.title}>竹香酒小程序隐私政策</View>
        <View className={styles.updateTime}>最后更新：2026年8月18日</View>

        <View className={styles.section}>
          <View className={styles.sectionTitle}>一、信息收集</View>
          <View className={styles.paragraph}>
            竹香酒小程序在您使用过程中会收集以下信息：
          </View>
          <View className={styles.list}>
            <View className={styles.listItem}>1. 手机号码（用于会员注册与登录）</View>
            <View className={styles.listItem}>2. 收货地址（用于订单发货）</View>
            <View className={styles.listItem}>3. 微信支付信息（由微信支付安全处理）</View>
            <View className={styles.listItem}>4. 会员等级与积分信息（用于权益计算）</View>
          </View>
        </View>

        <View className={styles.section}>
          <View className={styles.sectionTitle}>二、信息使用</View>
          <View className={styles.paragraph}>
            收集的信息仅用于以下目的：
          </View>
          <View className={styles.list}>
            <View className={styles.listItem}>1. 订单处理与发货（代理商区域路由）</View>
            <View className={styles.listItem}>2. 会员权益计算（等级加成/积分抵扣）</View>
            <View className={styles.listItem}>3. 分润计算（厂家→代理商5%同品分润）</View>
            <View className={styles.listItem}>4. 客户服务与售后支持</View>
          </View>
        </View>

        <View className={styles.section}>
          <View className={styles.sectionTitle}>三、信息存储</View>
          <View className={styles.paragraph}>
            您的信息通过 EnvAdapter 多端适配层安全存储，支持微信小程序本地存储（Taro.getStorageSync）和服务端数据库隔离存储，不会与第三方共享。
          </View>
        </View>

        <View className={styles.section}>
          <View className={styles.sectionTitle}>四、用户权利</View>
          <View className={styles.list}>
            <View className={styles.listItem}>1. 您有权查看和修改个人信息</View>
            <View className={styles.listItem}>2. 您有权注销账户并删除个人信息</View>
            <View className={styles.listItem}>3. 您有权撤回信息收集授权</View>
          </View>
        </View>

        <View className={styles.section}>
          <View className={styles.sectionTitle}>五、联系方式</View>
          <View className={styles.paragraph}>
            如您对隐私政策有任何疑问，请联系客服。
          </View>
        </View>

        <View className={styles.warning}>
          过量饮酒有害健康
        </View>
      </ScrollView>
    </View>
  );
};

export default PrivacyPage;
