/* 竹香酒品牌资质馆·保存证书图到相册
 * 容器端能力: window.xhs.miniTool.saveImageToPhotosAlbum(容器自动注入,
 * 无需随包携带 SDK)。证书图为包内资源, 经 canvas 同源导出为 data:
 * base64 直传保存(45KB 级小图无需 writeTempFile 中转)。
 * 降级路径: 未注入 SDK 的环境(普通浏览器预览)提示后再操作;
 * canvas 导出失败(污损等)给出明确提示, 不白屏不静默。
 * 兼容基线: Chrome 61 / ES2017, 经典脚本无 import。 */
(function () {
  'use strict';

  function getCertDataUrl(img) {
    var canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    if (!canvas.width || !canvas.height) {
      throw new Error('证书图未就绪');
    }
    var ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0);
    return canvas.toDataURL('image/jpeg', 0.92);
  }

  function init() {
    var btn = document.getElementById('save-cert-btn');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var miniTool = window.xhs && window.xhs.miniTool;
      if (!miniTool ||
          typeof miniTool.saveImageToPhotosAlbum !== 'function') {
        alert('请在小红书小工具中打开后再保存');
        return;
      }
      var img = document.getElementById('cert-img');
      if (!img) return;
      var dataUrl;
      try {
        dataUrl = getCertDataUrl(img);
      } catch (err) {
        alert('证书图读取失败, 请稍后再试');
        return;
      }
      miniTool.saveImageToPhotosAlbum({ filePath: dataUrl })
        .then(function () {
          alert('已保存到相册');
        })
        .catch(function (err) {
          var msg = (err && err.errMsg) || '未知原因';
          alert('保存失败: ' + msg);
        });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
