export default defineAppConfig({
  pages: [
    'pages/index/index',
    'pages/products/index',
    'pages/product-detail/index',
    'pages/mine/index',
    'pages/checkout/index',
    'pages/orders/index',
    'pages/order-detail/index',
    'pages/promotion/index',
    'pages/pocket/index',
    'pages/activity/index',
    'pages/wallet/index',
    'pages/login/index',
    'pages/theme-admin/index',
    'pages/perm-center/index',
    'pages/trace-punch/index',
    'pages/trace-view/index',
    'pages/privacy/index',
    'pages/agreement/index'
  ],
  window: {
    backgroundTextStyle: 'light',
    navigationBarBackgroundColor: '#355c44',
    navigationBarTitleText: '竹香酒',
    navigationBarTextStyle: 'white'
  },
  tabBar: {
    color: '#999999',
    selectedColor: '#355c44',
    backgroundColor: '#ffffff',
    borderStyle: 'black',
    list: [
      {
        pagePath: 'pages/index/index',
        text: '首页',
        iconPath: 'assets/tabbar/home.png',
        selectedIconPath: 'assets/tabbar/home-selected.png'
      },
      {
        pagePath: 'pages/products/index',
        text: '商品',
        iconPath: 'assets/tabbar/products.png',
        selectedIconPath: 'assets/tabbar/products-selected.png'
      },
      {
        pagePath: 'pages/mine/index',
        text: '我的',
        iconPath: 'assets/tabbar/mine.png',
        selectedIconPath: 'assets/tabbar/mine-selected.png'
      }
    ]
  }
})
