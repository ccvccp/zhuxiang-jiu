// 小红书草稿 IndexedDB 删除逻辑(2026-09-30 第 21 轮 CDP 失效修复抽出)
// 背景: UI 删除 handler 被 xhs 安全盾拦截(shield/webprofile 风控
// 调用后 handler 链中止)——CDP 坐标点击与 dispatchEvent 全序列均
// 绕不过, 改走 IndexedDB draft-database-v1 直接删记录。
//
// 本模块导出 deleteDraftsInPage: 自包含页面上下文函数(无外部闭包
// 依赖, 仅用全局 indexedDB/Promise/JSON)——
//   · xhs-bot.js 里 page.evaluate(deleteDraftsInPage, match) 直接
//     序列化注入页面执行(puppeteer 会 fn.toString() 传递)
//   · node:test 单测里配 fake-indexeddb 全局即可同一份代码验证
// 结构(12:58 diag 实证): draft-database-v1 四 store
// (article-draft/audio-draft/image-draft/video-draft),
// keyPath=draftId; match 空=全删, 非空=只删 JSON 含 match 的记录
function deleteDraftsInPage(match) {
  return new Promise((resolve) => {
    const req = indexedDB.open('draft-database-v1');
    req.onsuccess = () => {
      const db = req.result;
      const storeNames = Array.from(db.objectStoreNames);
      const out = { deleted: 0, perStore: {} };
      let pending = storeNames.length;
      if (!pending) { db.close(); return resolve(out); }
      const finish = () => {
        if (--pending === 0) { db.close(); resolve(out); }
      };
      for (const name of storeNames) {
        const tx = db.transaction(name, 'readwrite');
        const st = tx.objectStore(name);
        out.perStore[name] = { before: 0, deleted: 0 };
        const c = st.count();
        c.onsuccess = () => {
          out.perStore[name].before = c.result;
          const cur = st.openCursor();
          cur.onsuccess = () => {
            const cursor = cur.result;
            if (cursor) {
              const raw = JSON.stringify(cursor.value || {});
              if (!match || raw.includes(match)) {
                cursor.delete();
                out.deleted++;
                out.perStore[name].deleted++;
              }
              cursor.continue();
            }
          };
          tx.oncomplete = finish;
          tx.onerror = finish;
        };
        c.onerror = finish;
      }
    };
    req.onerror = () => resolve({ err: String(req.error) });
  });
}

module.exports = { deleteDraftsInPage };
