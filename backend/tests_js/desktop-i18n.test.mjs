import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync('app/static/desktop-i18n.js', 'utf8');
const dictionary = JSON.parse(fs.readFileSync('app/static/i18n-en.json', 'utf8'));

function load(language) {
  const window = { __expenseDesktopLanguage: language, __expenseDesktopI18n: dictionary, alert() {}, confirm() {}, prompt() {} };
  const observed = [];
  const document = { readyState: 'loading', documentElement: { lang: 'zh-CN' }, addEventListener() {}, createTreeWalker() { return { nextNode: () => null }; } };
  vm.runInNewContext(source, { window, document, NodeFilter: { SHOW_ELEMENT: 1, SHOW_TEXT: 4 },
    MutationObserver: class { observe(target, options) { observed.push(options); } } });
  return { i18n: window.LedgerI18n, document, observed };
}

test('English swaps interface text and keeps surrounding spaces', () => {
  const { i18n, document, observed } = load('en');
  assert.equal(i18n.language, 'en');
  assert.equal(i18n.t('更新账单'), 'Update bills');
  assert.equal(i18n.t('  查看详情 '), '  View details ');
  assert.equal(document.documentElement.lang, 'en');
  assert.equal(observed.length, 1, 'watches the page for new text');
});

test('patterns fill in values, reorder them and translate captured labels', () => {
  const { i18n } = load('en');
  assert.equal(i18n.t('最近操作 11/15 20:17'), 'Last activity 11/15 20:17');
  assert.equal(i18n.t('2026年11月'), '11/2026');
  assert.equal(i18n.t('第 1–50 / 120 笔'), '1–50 of 120');
  assert.equal(i18n.t('连击 ×3'), 'Combo ×3');
});

test('composite labels are translated part by part', () => {
  const { i18n } = load('en');
  assert.equal(i18n.t('已送达 · 含退款'), 'Delivered · includes refund');
});

test('ledger data and categories stay as recorded', () => {
  const { i18n } = load('en');
  for (const value of ['餐饮', '外卖', 'Walmart Supercentre', '给妈妈的礼物', '珍珠奶茶 x2']) assert.equal(i18n.t(value), value);
});

test('Chinese mode changes nothing and does not watch the page', () => {
  const { i18n, observed } = load('zh');
  assert.equal(i18n.language, 'zh');
  assert.equal(i18n.t('更新账单'), '更新账单');
  assert.equal(observed.length, 0);
});

test('the dictionary is consistent: every English placeholder exists in its key, and no spending category is a key', () => {
  const categories = ['餐饮', '外卖', '咖啡', '食品杂货', '家居日用', '个护美容', '出行', '通讯', '订阅', '软件订阅', '数字娱乐', '数字服务', '日常零售', '综合购物', '杂货配送', '餐厨用品', '银行费用', '外卖会员', '购物会员', '未分类', '其他'];
  for (const category of categories) assert.equal(dictionary[category], undefined, category);
  for (const [zh, en] of Object.entries(dictionary)) {
    const keySlots = new Set(zh.match(/\{\d+\}/g) ?? []);
    for (const slot of en.match(/\{\d+\}/g) ?? []) assert.ok(keySlots.has(slot) || zh.endsWith('：'), `${zh} -> ${en}`);
    assert.ok(!/[㐀-鿿]/.test(en) || zh.includes('Choose language'), `English text left in Chinese: ${zh}`);
  }
});
