/**
 * 跨端图片读取工具 · chooseImage 结果 → data URL(base64)
 *
 * 背景: Taro.getFileSystemManager 在 H5 端为 temporarilyNotSupport 桩
 * (调用返回 Promise 而非对象, fsm.readFile 直接 TypeError)——
 * 生产实证: pocket/theme-admin 图标上传在电脑端(H5)静默失效。
 *
 * 分端策略:
 *   H5 端:  chooseImage 返回 tempFiles[0].originalFileObj(原生 File)
 *           → FileReader.readAsDataURL
 *   小程序端: tempFilePaths[0] → getFileSystemManager().readFile(base64)
 */
import Taro from '@tarojs/taro';

/** 读取失败错误 */
export class ImageReadError extends Error {
  constructor(msg: string) {
    super(msg);
    this.name = 'ImageReadError';
  }
}

/**
 * 将 chooseImage 结果读为 data URL
 * @param res Taro.chooseImage 的 success 回调参数
 * @returns data:image/xxx;base64,...
 */
export const chooseImageAsDataUrl = (res: Taro.chooseImage.SuccessCallbackResult): Promise<string> =>
  new Promise((resolve, reject) => {
    const file: any = (res.tempFiles || [])[0];
    const path: string = (res.tempFilePaths || [])[0] || file?.path || '';

    // ---- H5 端: originalFileObj 为原生 File 对象 ----
    if (typeof FileReader !== 'undefined' && file?.originalFileObj) {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result || ''));
      fr.onerror = () => reject(new ImageReadError('读取图片失败'));
      fr.readAsDataURL(file.originalFileObj);
      return;
    }

    // ---- 小程序端: FileSystemManager 读 base64 ----
    if (!path) {
      reject(new ImageReadError('未选择图片'));
      return;
    }
    try {
      const fsm = Taro.getFileSystemManager();
      if (typeof fsm?.readFile !== 'function') {
        reject(new ImageReadError('当前环境不支持读取本地文件'));
        return;
      }
      fsm.readFile({
        filePath: path,
        encoding: 'base64',
        success: (r: any) => {
          const ext = (path.split('.').pop() || 'jpg').toLowerCase();
          const mime = ext === 'jpg' ? 'jpeg' : ext;
          resolve(`data:image/${mime};base64,${r.data}`);
        },
        fail: () => reject(new ImageReadError('读取图片失败')),
      });
    } catch (e) {
      reject(new ImageReadError('读取图片失败'));
    }
  });
