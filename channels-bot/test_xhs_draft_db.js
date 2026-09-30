// xhs_draft_db 删除逻辑单测(2026-09-30, 21 轮 CDP 失效修复配套)
// 被测体: deleteDraftsInPage —— xhs-bot.js 页面上下文函数, 与
// 本测试共用同一份实现(bot require 同一模块, 测试不漂移)。
// 环境: fake-indexeddb 注入全局 indexedDB(页面与测试同构);
// 草稿库结构按 12:58 diag 实证: draft-database-v1 v10 四 store
// (article/audio/image/video-draft), keyPath=draftId。
// 用例: 全删/标题过滤/空库/嵌套结构匹配/返回结构完整性
require('fake-indexeddb/auto');
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { deleteDraftsInPage } = require('./xhs_draft_db');

const DB_NAME = 'draft-database-v1';
const STORES = ['article-draft', 'audio-draft', 'image-draft', 'video-draft'];

// 草稿记录真实形态(12:58 diag 样本: 标题嵌在 editor 深层)
function draft(id, title) {
  return {
    draftId: id,
    content: { contextStore: { liveContext: { time: 0, title: '' } } },
    editor: { title, desc: '家人们...' },
  };
}

function freshDb(records) {
  return new Promise((resolve, reject) => {
    const del = indexedDB.deleteDatabase(DB_NAME);
    const afterDel = () => {
      const req = indexedDB.open(DB_NAME, 10);
      req.onupgradeneeded = () => {
        const db = req.result;
        for (const name of STORES) {
          db.createObjectStore(name, { keyPath: 'draftId' });
        }
      };
      req.onsuccess = () => {
        const db = req.result;
        const names = Object.keys(records);
        if (!names.length) { db.close(); return resolve(); }
        const tx = db.transaction(names, 'readwrite');
        for (const [name, items] of Object.entries(records)) {
          const st = tx.objectStore(name);
          for (const it of items) st.put(it);
        }
        tx.oncomplete = () => { db.close(); resolve(); };
        tx.onerror = () => reject(tx.error);
      };
      req.onerror = () => reject(req.error);
    };
    del.onsuccess = del.onerror = del.onblocked = afterDel;
  });
}

function countAll() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME);
    req.onsuccess = () => {
      const db = req.result;
      const out = {};
      let pending = db.objectStoreNames.length;
      if (!pending) { db.close(); return resolve(out); }
      for (const name of db.objectStoreNames) {
        const tx = db.transaction(name, 'readonly');
        const c = tx.objectStore(name).count();
        c.onsuccess = () => {
          out[name] = c.result;
          if (--pending === 0) { db.close(); resolve(out); }
        };
        c.onerror = () => reject(c.error);
      }
    };
    req.onerror = () => reject(req.error);
  });
}

test('match 空 = 全删: 四 store 所有记录删除, perStore 计数正确', async () => {
  await freshDb({
    'video-draft': [draft('v1', '国庆家宴白酒怎么选'), draft('v2', '中秋团圆宴')],
    'article-draft': [draft('a1', '图文草稿')],
  });
  const r = await deleteDraftsInPage('');
  assert.equal(r.err, undefined);
  assert.equal(r.deleted, 3);
  assert.equal(r.perStore['video-draft'].before, 2);
  assert.equal(r.perStore['video-draft'].deleted, 2);
  assert.equal(r.perStore['article-draft'].before, 1);
  assert.equal(r.perStore['article-draft'].deleted, 1);
  const after = await countAll();
  assert.equal(after['video-draft'], 0);
  assert.equal(after['article-draft'], 0);
});

test('match 非空 = 按标题过滤: 只删 JSON 含 match 的记录, 其余保留', async () => {
  await freshDb({
    'video-draft': [
      draft('v1', '国庆家宴白酒怎么选'),
      draft('v2', '中秋团圆宴白酒清单'),
    ],
  });
  const r = await deleteDraftsInPage('国庆家宴白酒怎么选');
  assert.equal(r.deleted, 1);
  assert.equal(r.perStore['video-draft'].deleted, 1);
  const after = await countAll();
  assert.equal(after['video-draft'], 1);
  // 保留的是不匹配那条
  const left = await new Promise((resolve) => {
    const req = indexedDB.open(DB_NAME);
    req.onsuccess = () => {
      const db = req.result;
      const tx = db.transaction('video-draft', 'readonly');
      const cur = tx.objectStore('video-draft').openCursor();
      cur.onsuccess = () => {
        if (cur.result) { resolve(cur.result.value.editor.title); }
        else { resolve(null); }
        db.close();
      };
    };
  });
  assert.equal(left, '中秋团圆宴白酒清单');
});

test('空库: 四 store 全 0, 正常返回不报错', async () => {
  await freshDb({});
  const r = await deleteDraftsInPage('');
  assert.equal(r.err, undefined);
  assert.equal(r.deleted, 0);
  assert.equal(r.perStore['image-draft'].before, 0);
  assert.equal(r.perStore['image-draft'].deleted, 0);
});

test('嵌套结构匹配: 标题在 content.contextStore 深层也能命中(JSON 全序列化匹配)', async () => {
  await freshDb({
    'video-draft': [{
      draftId: 'deep-1',
      content: {
        contextStore: {
          nested: { deeper: { title: '国庆家宴白酒怎么选' } },
        },
      },
      editor: { title: '' },
    }],
  });
  const r = await deleteDraftsInPage('国庆家宴白酒怎么选');
  assert.equal(r.deleted, 1);
  const after = await countAll();
  assert.equal(after['video-draft'], 0);
});

test('返回结构完整性: perStore 覆盖全部四 store 且字段齐备', async () => {
  await freshDb({ 'video-draft': [draft('v1', '国庆家宴白酒怎么选')] });
  const r = await deleteDraftsInPage('');
  const keys = Object.keys(r.perStore);
  for (const name of STORES) {
    assert.ok(keys.includes(name), 'perStore 缺 ' + name);
    assert.ok('before' in r.perStore[name], '缺 before');
    assert.ok('deleted' in r.perStore[name], '缺 deleted');
  }
  assert.equal(typeof r.deleted, 'number');
});
