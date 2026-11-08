/**
 * dsh-engineering-ui Host 插件 · 兼容性与契约回归测试。
 *
 * ── 【P1-7 修复】本文件此前完全失效，原因有三 ──────────────────────────
 *  1. 依赖 createRequire 解析 @deepseek-ai/* 到【安装体】。0.2.0 把这些包
 *     打进 app.asar，磁盘上 require.resolve 必然 MODULE_NOT_FOUND ——
 *     测试根本跑不起来（Cannot find module '@deepseek-ai/dsh-llm'）。
 *  2. 断言 h.routes.size === 9，而插件实际注册 13 条路由（多了 4 条
 *     /defense/*）。断言与实际长期不符。
 *  3. 用 sessionPersistence.inspect() —— 该 API 在 0.2.0 已被移除
 *     （改为 open/read/close），且断言 source.plugin 字段，与
 *     repeat-output.test.mjs 的"不得存在 plugin 字段"断言直接矛盾。
 *
 * 现改为【自包含】：把两个外部依赖替换为内联 shim（与
 * repeat-output.test.mjs 同一套做法），因此无需 DSH 安装体即可运行。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync, mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { Readable } from 'node:stream';
import fs from 'node:fs';
import crypto from 'node:crypto';

const SHIM_LLM = `
export function createUserMessage(input) {
  return { role: 'user', content: input.content, source: input.source };
}
`;

const SHIM_REGISTRY = `
export function resolveSessionPreset({ header, events }) {
  const evs = Array.isArray(events) ? events : [];
  for (let i = evs.length - 1; i >= 0; i--) {
    const ev = evs[i];
    if (ev && ev.type === 'agent-preset/selected' && ev.data && ev.data.agentPreset) {
      return String(ev.data.agentPreset);
    }
  }
  return (header && header.agentPreset) ?? null;
}
export const agentPresetProjectionDefinition = {
  init: (header) => (header && header.agentPreset) ?? null,
  apply: (state, event) => {
    if (event && event.type === 'agent-preset/selected' && event.data && event.data.agentPreset) {
      return String(event.data.agentPreset);
    }
    return state;
  },
};
`;

const LLM_URL = 'data:text/javascript;base64,' + Buffer.from(SHIM_LLM).toString('base64');
const REG_URL = 'data:text/javascript;base64,' + Buffer.from(SHIM_REGISTRY).toString('base64');
const source = readFileSync(new URL('../lib/index.js', import.meta.url), 'utf8');
const signSource = readFileSync(new URL('../lib/defense-sign.js', import.meta.url), 'utf8');
const SIGN_URL = 'data:text/javascript;base64,' + Buffer.from(signSource).toString('base64');
// 【卡死修复】job-wait-guard.js 也必须内联（data: URL 加载无法解析相对路径）
const guardSource = readFileSync(new URL('../lib/job-wait-guard.js', import.meta.url), 'utf8');
const GUARD_URL = 'data:text/javascript;base64,' + Buffer.from(guardSource).toString('base64');
// 【Webots 接入】webots.js 同样是相对 import，data: URL 解析不了 → 一并内联。
const webotsSource = readFileSync(new URL('../lib/webots.js', import.meta.url), 'utf8');
const WEBOTS_URL = 'data:text/javascript;base64,' + Buffer.from(webotsSource).toString('base64');
// 【内存守卫】mem-guard.js 同样是相对 import，一并内联。
const memGuardSource = readFileSync(new URL('../lib/mem-guard.js', import.meta.url), 'utf8');
const MEMGUARD_URL = 'data:text/javascript;base64,' + Buffer.from(memGuardSource).toString('base64');

const rewritten = source
  .replace("from '@deepseek-ai/dsh-llm'", "from '" + LLM_URL + "'")
  .replace("from '@deepseek-ai/dsh-agent-preset-registry'", "from '" + REG_URL + "'")
  .replace("from './defense-sign.js'", "from '" + SIGN_URL + "'")
  .replace("from './job-wait-guard.js'", "from '" + GUARD_URL + "'")
  .replace("from './webots.js'", "from '" + WEBOTS_URL + "'")
  .replace("from './mem-guard.js'", "from '" + MEMGUARD_URL + "'");
assert.ok(rewritten.includes('data:text/javascript'), '依赖 shim 必须替换成功');

const { apply } = await import('data:text/javascript;base64,' + Buffer.from(rewritten).toString('base64'));

/**
 * 构造一个可控的插件宿主上下文。
 *
 * sessionPersistence 用 0.2.0 合同：open(id,'read') → { header, read(), close() }，
 * 并保留 stat() 供 parentOf() 回落使用（inspect() 已移除）。
 */
function harness(handles = {}, hostApply = apply) {
  const routes = new Map(), asks = [], effects = [];
  // ── 【测试脚手架修复】同一钩子名可以被注册多次（完全合法）──────────────
  // 原实现 hooks 是 Map<name, fn>：第二个同名注册直接【覆盖】第一个。
  // 本插件确实注册了两个 'agent/turn-stopping'：
  //   ① 收尾守卫（先注册）  ② 平台子代理存活登记（后注册）
  // 于是 Map 里只剩②，测试拿到的根本不是守卫，'未达终态必须 steer' 恒失败。
  // 生产环境按注册顺序依次调用【全部】钩子，所以脚手架也必须保留列表。
  // hooks 仍保持 Map 语义（size/has/get 均按"钩子名"计），额外提供
  // hooks.all(name) 取该名字下的全部处理函数。
  const hooks = new Map();          // name -> [fn, ...]
  const root = { id: 'root' };
  const ctx = {
    effect(setup) { const cleanup = setup(); effects.push(cleanup); return cleanup; },
    // 【Webots 接入】index.js 已声明 inject=['...','tools'] 并调用 ctx.tools.guard。
    //   这里有守卫的话，mcp__webots__* 的归属校验才会生效；测试不需要它生效。
    tools: { guard() { return () => {}; } },
    on(name, fn) {
      const list = hooks.get(name) || [];
      list.push(fn);
      hooks.set(name, list);
      return () => {
        const cur = hooks.get(name) || [];
        const i = cur.indexOf(fn);
        if (i >= 0) cur.splice(i, 1);
        if (!cur.length) hooks.delete(name);
      };
    },
    webServer: { register(route) { routes.set(route.path, route.handler); return () => routes.delete(route.path); } },
    sessionPersistence: {
      async open(id) {
        const h = handles[id];
        if (!h) throw new Error('no such session');
        return {
          header: h.header || {},
          inheritedEventCount: h.inheritedEventCount || 0,
          async read() { return { events: h.events || [] }; },
          async close() {},
        };
      },
      async stat(id) {
        const h = handles[id];
        if (!h) return null;
        return { header: h.header || {} };
      },
    },
    sessions: {},
    agents: { get(id) { return id === root.id ? root : undefined; }, roots() { return [root]; } },
    subagents: { async listDescendants() { return [{ id: 'child', label: 'child' }]; } },
    userQuestions: { ask(request) { return new Promise((resolve, reject) => asks.push({ request, resolve, reject })); } },
  };
  const returnedDispose = hostApply(ctx);
  const dispose = () => { for (const cleanup of effects.splice(0).reverse()) cleanup(); };
  async function request(route, payload, method = payload ? 'POST' : 'GET', headers = {}, remoteAddress = '127.0.0.1') {
    const req = Readable.from(payload ? [JSON.stringify(payload)] : []);
    req.url = '/dsh-engineering-ui' + route;
    req.method = method;
    req.headers = headers;
    req.socket = { remoteAddress };
    let body;
    const res = { statusCode: 200, setHeader() {}, end(text) { body = JSON.parse(text); } };
    await routes.get(req.url.split('?')[0])(req, res);
    return { status: res.statusCode, ...body };
  }
  hooks.all = (name) => hooks.get(name) || [];
  return { ctx, root, routes, hooks, asks, dispose, returnedDispose, request };
}

test('npm升级可更新只读判定文件并维护既有基线，失败恢复只读状态', async () => {
  const temp = mkdtempSync(path.join(tmpdir(), 'engineering-runtime-upgrade-'));
  const keys = ['DSH_HOME', 'DSH_STATE_DIR', 'USERPROFILE'];
  const previous = Object.fromEntries(keys.map(key => [key, process.env[key]]));
  const engineering = path.join(temp, 'package', 'engineering');
  const home = path.join(temp, 'home');
  const tools = path.join(home, '.agent-presets', 'engineering', 'tools');
  const state = path.join(temp, 'state');
  const target = path.join(tools, 'defense_gate.py');
  const baselinePath = path.join(state, '.defense', 'gate_baseline.json');
  mkdirSync(path.join(engineering, 'tools'), { recursive: true });
  mkdirSync(path.join(engineering, 'skills', 'mode-selection'), { recursive: true });
  mkdirSync(path.dirname(baselinePath), { recursive: true });
  mkdirSync(tools, { recursive: true });
  writeFileSync(path.join(engineering, 'tools', 'mode_gate.py'), 'new mode gate');
  writeFileSync(path.join(engineering, 'tools', 'defense_gate.py'), 'trusted new gate');
  writeFileSync(path.join(engineering, 'skills', 'mode-selection', 'SKILL.md'), 'packaged skill');
  writeFileSync(target, 'protected old gate');
  fs.chmodSync(target, 0o444);
  writeFileSync(path.join(tools, 'settings.json'), '{"keep":"settings"}');
  writeFileSync(path.join(state, 'workflow_state.json'), '{"keep":"task"}');
  const untouched = { sha256: 'user-owned-record', size: 123, mtime: 456 };
  writeFileSync(baselinePath, JSON.stringify({ schema: 'dsh-engineering/gate-baseline@1',
    reason: 'manual', files: { 'defense_gate.py': { sha256: 'old' }, 'custom.py': untouched } }));
  process.env.DSH_HOME = home;
  process.env.DSH_STATE_DIR = state;
  process.env.USERPROFILE = path.join(temp, 'user');
  const hostSource = rewritten.replaceAll('import.meta.dirname',
    JSON.stringify(path.join(engineering, 'plugins', 'dsh-engineering-ui', 'lib')));
  const { apply: installedApply } = await import('data:text/javascript;base64,' + Buffer.from(hostSource).toString('base64'));
  try {
    harness({}, installedApply).dispose();
    assert.equal(readFileSync(target, 'utf8'), 'trusted new gate');
    assert.equal(fs.statSync(target).mode & 0o200, 0, '合法升级后继续只读');
    const baseline = JSON.parse(readFileSync(baselinePath, 'utf8'));
    assert.equal(baseline.files['defense_gate.py'].sha256,
      crypto.createHash('sha256').update(readFileSync(target)).digest('hex'));
    assert.deepEqual(baseline.files['custom.py'], untouched);
    assert.equal(baseline.reason, 'manual');
    assert.equal(readFileSync(path.join(tools, 'settings.json'), 'utf8'), '{"keep":"settings"}');
    assert.equal(readFileSync(path.join(state, 'workflow_state.json'), 'utf8'), '{"keep":"task"}');
    assert.equal(readFileSync(path.join(home, 'skills', 'mode-selection', 'SKILL.md'), 'utf8'), 'packaged skill');
    const originalRename = fs.renameSync;
    fs.renameSync = (from, to) => { if (to === target) throw new Error('fixture rename failed'); return originalRename(from, to); };
    fs.chmodSync(target, 0o644);
    writeFileSync(target, 'previous after interrupted update');
    fs.chmodSync(target, 0o444);
    try { assert.throws(() => harness({}, installedApply), /fixture rename failed/); }
    finally { fs.renameSync = originalRename; }
    assert.equal(readFileSync(target, 'utf8'), 'previous after interrupted update');
    assert.equal(fs.statSync(target).mode & 0o200, 0, '失败也必须恢复只读');
    assert.equal(fs.readdirSync(tools).some(name => name.includes('.tmp-')), false);
    harness({}, installedApply).dispose();
    assert.equal(readFileSync(target, 'utf8'), 'trusted new gate', '再次加载可恢复被中断的升级');
    fs.rmSync(baselinePath);
    harness({}, installedApply).dispose();
    assert.equal(fs.existsSync(baselinePath), false, '原来没有基线时不能自动创建');
  } finally {
    fs.chmodSync(target, 0o644);
    for (const key of keys) {
      if (previous[key] === undefined) delete process.env[key]; else process.env[key] = previous[key];
    }
    rmSync(temp, { recursive: true, force: true });
  }
});

test('文件令牌按当前状态目录生效，环境令牌优先，普通轮询不被限速', async () => {
  const temp = mkdtempSync(path.join(tmpdir(), 'engineering-route-auth-'));
  const keys = ['DSH_HOME', 'DSH_STATE_DIR', 'DSH_ENGINEERING_TOKEN'];
  const previous = Object.fromEntries(keys.map(key => [key, process.env[key]]));
  const tools = path.join(temp, '.agent-presets', 'engineering', 'tools');
  const state = path.join(temp, 'state');
  mkdirSync(tools, { recursive: true });
  mkdirSync(path.join(state, '.defense'), { recursive: true });
  process.env.DSH_HOME = temp;
  process.env.DSH_STATE_DIR = state;
  delete process.env.DSH_ENGINEERING_TOKEN;
  const h = harness();
  try {
    const payload = { text: 'isolated fixture' };
    assert.equal((await h.request('/persona', payload)).status, 200, '未配置令牌时保留本机调用');
    const tokenFile = path.join(state, '.defense', 'local.token');
    writeFileSync(tokenFile, 'fixture-file-token\n');
    for (const route of ['/persona', '/settings', '/conn-launch', '/conn-diagnose', '/defense/sign', '/side-chat']) {
      assert.equal((await h.request(route, payload)).status, 403, route + ' 必须拒绝缺失令牌');
    }
    const headers = { 'x-dsh-engineering-token': 'fixture-file-token' };
    const denied = await h.request('/persona', payload, 'POST', { 'x-dsh-engineering-token': 'wrong' });
    assert.equal(denied.status, 403);
    assert.equal(denied.code, 'ENGINEERING_TOKEN_REQUIRED');
    assert.equal((await h.request('/persona', payload, 'POST', headers)).status, 200);
    assert.equal((await h.request('/persona', payload, 'POST', { ...headers, origin: 'https://example.invalid' })).status, 403);
    assert.equal((await h.request('/persona', payload, 'POST', headers, '192.0.2.1')).status, 403);
    for (const content of ['', ' \n\t']) {
      writeFileSync(tokenFile, content);
      assert.equal((await h.request('/persona', payload)).status, 403, '空令牌文件不能退化为未配置');
    }
    rmSync(tokenFile);
    mkdirSync(tokenFile);
    assert.equal((await h.request('/persona', payload)).status, 403, '令牌路径不可读时不得放行');
    rmSync(tokenFile, { recursive: true });
    writeFileSync(tokenFile, 'fixture-file-token');
    process.env.DSH_ENGINEERING_TOKEN = 'fixture-env-token';
    assert.equal((await h.request('/persona', payload, 'POST', headers)).status, 403, '环境令牌覆盖文件令牌');
    assert.equal((await h.request('/persona', payload, 'POST', { 'x-dsh-engineering-token': 'fixture-env-token' })).status, 200);
    delete process.env.DSH_ENGINEERING_TOKEN;
    rmSync(tokenFile);
    assert.equal((await h.request('/persona', payload)).status, 200);
    for (let i = 0; i < 125; i++) {
      assert.equal((await h.request('/defense/info')).status, 200, '普通轮询不应占用防线限速桶');
    }
  } finally {
    h.dispose();
    for (const key of keys) {
      if (previous[key] === undefined) delete process.env[key]; else process.env[key] = previous[key];
    }
    rmSync(temp, { recursive: true, force: true });
  }
});

test('UI 用户输入令牌后重试，令牌仅用于同源工程请求和当前标签会话', async () => {
  const client = readFileSync(new URL('../lib/client.js', import.meta.url), 'utf8');
  const helper = client.slice(client.indexOf("var engSessionToken = ''"), client.indexOf('// ── 原始事件'));
  const calls = [];
  let prompts = 0, answer = 'fixture-ui-token', genericDenial = false;
  const window = {
    location: new URL('http://127.0.0.1:9000/'),
  };
  const fetch = async (url, options = {}) => {
    calls.push({ url, options });
    const authorized = new Headers(options.headers).get('x-dsh-engineering-token') === 'fixture-ui-token';
    return Response.json(authorized && !genericDenial ? { ok: true } :
      (genericDenial ? { ok: false } : { code: 'ENGINEERING_TOKEN_REQUIRED' }),
      { status: authorized && !genericDenial ? 200 : 403 });
  };
  const { engFetch, clearToken } = new Function('window', 'fetch', 'Headers', 'URL', 'ask', helper
    + ';engRequestToken=async function(){engSessionToken=(await ask())||"";return engSessionToken;};'
    + 'return {engFetch,clearToken:()=>{engSessionToken="";}};')(
    window, fetch, Headers, URL, () => { prompts++; return answer; });
  const options = { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"text":"fixture"}' };
  assert.equal((await engFetch('/dsh-engineering-ui/persona', options)).status, 200);
  assert.equal(prompts, 1);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].options.body, options.body, '授权重试必须保留原 POST 内容');
  assert.equal(calls[1].options.headers.get('content-type'), 'application/json');
  assert.equal(calls[1].options.redirect, 'error', '不得向跳转目标泄漏令牌');
  assert.equal((await engFetch('/dsh-engineering-ui/persona')).status, 200);
  assert.equal(prompts, 1, '当前标签的后续请求直接使用已输入令牌');
  await engFetch('//example.invalid/dsh-engineering-ui/persona');
  assert.equal(new Headers(calls.at(-1).options.headers).has('x-dsh-engineering-token'), false);
  genericDenial = true;
  assert.equal((await engFetch('/dsh-engineering-ui/persona')).status, 403);
  assert.equal(prompts, 1, '跨站或无效令牌文件的拒绝不得误弹输入框');
  genericDenial = false;
  clearToken(); answer = null;
  const beforeCancel = calls.length;
  assert.equal((await engFetch('/dsh-engineering-ui/persona')).status, 403);
  assert.equal(calls.length, beforeCancel + 1, '用户取消后不得重试');
  answer = 'wrong';
  assert.equal((await engFetch('/dsh-engineering-ui/persona')).status, 403);
  answer = 'fixture-ui-token';
  assert.equal((await engFetch('/dsh-engineering-ui/persona')).status, 200, '旧令牌失效时允许用户更新');
  for (const route of ['persona', 'settings', 'conn-launch', 'conn-diagnose', 'side-chat']) {
    assert.equal(client.includes("fetch('/dsh-engineering-ui/" + route + "'"), false, route + ' 必须走授权请求入口');
  }
});

test('全部 32 条 HTTP 路由与守卫钩子注册，且可完整卸载', () => {
  const h = harness();
  try {
    // 9 条基础/提问/通知路由
    //   + 5 条【三大防线】签名路由（sign / verify / verify-gate / info / judge）
    //   + 5 条【连接区】路由（conn-state / conn-probe
    //     / conn-probe-process-only / conn-launch / conn-diagnose）
    //     ※ 第 5 条是【10 分钟自动刷新】新增的纯进程端点：
    //       用户要求"只做检测就行"，它【绝不发 COM】，因此必须与
    //       conn-probe 分开（后者可能把 SW 拉起来）。
    //   + 1 条【划词详情】侧边分析路由（/side-chat）
    //   + 1 条【工程人设】读写路由（/persona）
    //   + 1 条【第4项·SW 健康看护】ctx.jobs 版路由（/sw-guard-job）= 22
    // 注：/defense/verify-gate 是宿主侧"不消费 nonce"的只读验签入口
    //   （门禁 verify-gate 用），原先漏计，已补入清单。
    // ── 【工程环境检查（用户需求 3）】新增 3 条 → 22 + 3 = 25 ────────────
    //   /env-check  —— 跑一次 tools/env_check.py probe（只读，不装东西）
    //   /env-prompt —— 生成可发给新对话的修复提示词
    //   /settings   —— 本机设置（交付目录 / 各软件路径）读写
    // 判定逻辑全部在 Python 侧，JS 只做 HTTP 转接，保证"设置页看到的"
    //   与"Agent 跑出来的"是同一套结论。
    // ── 【模型自动接替】新增 2 条 → 25 + 2 = 27 ────────────────────────
    //   /model-catalog —— 收集各 provider 的可用模型目录（只读，给设置页用）
    //   /model-chain   —— 读写"备用模型链"配置（GET 读 / POST 存）
    // 旧断言写死 27 且清单里没有这两条 —— 代码早就是 29 条，
    //   于是这条断言长期失败，而它恰恰是"路由注册完整性"的唯一守门人。
    //   断言数量与清单必须同时维护：加路由时两处一起改。
    // ── 【Webots 接入】+4 → 【内存守卫】+1(mem-guard) = 32 ──────────────────────────────
    //   /webots-context / -report / -evidence / -attribute（全部只读 GET）
    assert.equal(h.routes.size, 32, '路由数量应为 32（含 4 条 Webots 只读路由）');
    // ── 清单必须与 lib/index.js 的 ctx.webServer.register 一一对应 ──────
    // 漏写一条 → 路由存在但没被这条断言保护；多写一条 → 直接失败。
    for (const p of [
      '/dsh-engineering-ui/agents', '/dsh-engineering-ui/log',
      '/dsh-engineering-ui/ask', '/dsh-engineering-ui/ask-child',
      '/dsh-engineering-ui/pending-child', '/dsh-engineering-ui/result-child',
      '/dsh-engineering-ui/answer', '/dsh-engineering-ui/verify-subagent',
      '/dsh-engineering-ui/notify-room',
      '/dsh-engineering-ui/sw-guard-job',
      '/dsh-engineering-ui/defense/sign', '/dsh-engineering-ui/defense/verify',
      '/dsh-engineering-ui/defense/verify-gate',
      '/dsh-engineering-ui/defense/info', '/dsh-engineering-ui/defense/judge',
      '/dsh-engineering-ui/conn-state', '/dsh-engineering-ui/conn-probe',
      '/dsh-engineering-ui/conn-probe-process-only',
      '/dsh-engineering-ui/conn-launch', '/dsh-engineering-ui/conn-diagnose',
      '/dsh-engineering-ui/env-check', '/dsh-engineering-ui/env-prompt',
      '/dsh-engineering-ui/settings',
      '/dsh-engineering-ui/side-chat',
      '/dsh-engineering-ui/model-catalog', '/dsh-engineering-ui/model-chain',
      '/dsh-engineering-ui/persona',
      '/dsh-engineering-ui/mem-guard',
      '/dsh-engineering-ui/webots-context', '/dsh-engineering-ui/webots-report',
      '/dsh-engineering-ui/webots-evidence', '/dsh-engineering-ui/webots-attribute',
    ]) {
      assert.ok(h.routes.has(p), 'missing route ' + p);
    }
    // 反向校验：清单长度必须等于实际注册数，防止"数量对了但清单漏了"
    assert.equal(new Set([
      '/dsh-engineering-ui/agents', '/dsh-engineering-ui/log',
      '/dsh-engineering-ui/ask', '/dsh-engineering-ui/ask-child',
      '/dsh-engineering-ui/pending-child', '/dsh-engineering-ui/result-child',
      '/dsh-engineering-ui/answer', '/dsh-engineering-ui/verify-subagent',
      '/dsh-engineering-ui/notify-room', '/dsh-engineering-ui/sw-guard-job',
      '/dsh-engineering-ui/defense/sign', '/dsh-engineering-ui/defense/verify',
      '/dsh-engineering-ui/defense/verify-gate',
      '/dsh-engineering-ui/defense/info', '/dsh-engineering-ui/defense/judge',
      '/dsh-engineering-ui/conn-state', '/dsh-engineering-ui/conn-probe',
      '/dsh-engineering-ui/conn-probe-process-only',
      '/dsh-engineering-ui/conn-launch', '/dsh-engineering-ui/conn-diagnose',
      '/dsh-engineering-ui/env-check', '/dsh-engineering-ui/env-prompt',
      '/dsh-engineering-ui/settings',
      '/dsh-engineering-ui/side-chat',
      '/dsh-engineering-ui/model-catalog', '/dsh-engineering-ui/model-chain',
      '/dsh-engineering-ui/persona',
      '/dsh-engineering-ui/mem-guard',
      '/dsh-engineering-ui/webots-context', '/dsh-engineering-ui/webots-report',
      '/dsh-engineering-ui/webots-evidence', '/dsh-engineering-ui/webots-attribute',
    ]).size, h.routes.size, '路由清单与注册数必须一致（清单有重复或漏项）');
    assert.equal(h.hooks.has('agent/turn-stopping'), true);
  } finally { h.dispose(); }
  assert.equal(h.routes.size, 0, 'dispose 后路由必须清空');
  assert.equal(h.hooks.size, 0, 'dispose 后钩子必须清空');
});

test('宿主停用释放路由，重复启用后旧清理不能删除新路由', () => {
  const h = harness();
  const count = h.routes.size;
  h.dispose();
  assert.equal(h.routes.size, 0, '只触发原生 effect 清理也必须释放路由');
  apply(h.ctx);
  assert.equal(h.routes.size, count);
  h.returnedDispose();
  assert.equal(h.routes.size, count, '已消费的旧清理不能误删新实例路由');
  h.dispose();
  assert.equal(h.routes.size, 0);
});

test('收尾守卫：非终态时 steer，达到 finished 后放行，且 source 为 producer-owned', () => {
  const oldHome = process.env.DSH_HOME;
  const temp = mkdtempSync(path.join(tmpdir(), 'dsh-eng-compat-'));
  const tools = path.join(temp, '.agent-presets', 'engineering', 'tools');
  mkdirSync(tools, { recursive: true });
  writeFileSync(path.join(tools, 'workflow_state.json'), JSON.stringify({ step: 'design' }));
  process.env.DSH_HOME = temp;
  const h = harness();
  try {
    const steered = [];
    const agent = {
      id: 'guard-1',
      session: {
        header: { agentPreset: 'engineering' },
        snapshotEvents() { return [{ type: 'agent-preset/selected', data: { agentPreset: 'engineering' } }]; },
      },
      steer(message) { steered.push(message); },
    };
    // 真实运行时按注册顺序调用【全部】turn-stopping 钩子，这里保持一致
    const stop = () => {
      for (const fn of h.hooks.all('agent/turn-stopping')) {
        fn({ agent, signal: new AbortController().signal });
      }
    };
    stop();
    assert.equal(steered.length, 1, '进行中的工程流程没有房间记录时也必须 steer');
    // 【V4】steer 的消息 source 必须是生产者自有 kind，不得是通用 plugin
    assert.equal(steered[0].source.kind, 'plugin:dsh-engineering-ui');
    assert.equal(Object.hasOwn(steered[0].source, 'plugin'), false,
      '不得保留已退休的 plugin 字段（与 repeat-output.test.mjs 一致）');
    // 用户取消 → 放行
    for (const fn of h.hooks.all('agent/turn-stopping')) {
      fn({ agent, signal: AbortSignal.abort() });
    }
    assert.equal(steered.length, 1, 'aborted 信号必须放行');
    // 门禁进入终态 → 放行
    writeFileSync(path.join(tools, 'workflow_state.json'), JSON.stringify({ step: 'finished' }));
    stop();
    assert.equal(steered.length, 1, 'workflow step=finished 必须放行');
  } finally {
    h.dispose();
    if (oldHome === undefined) delete process.env.DSH_HOME; else process.env.DSH_HOME = oldHome;
    rmSync(temp, { recursive: true, force: true });
  }
});

test('无工程状态的识图回合可结束；活动房间及未收尾的流程仍须继续', () => {
  const temp = mkdtempSync(path.join(tmpdir(), 'dsh-eng-readonly-'));
  const oldState = process.env.DSH_STATE_DIR;
  process.env.DSH_STATE_DIR = temp;
  const h = harness();
  try {
    const steered = [];
    const agent = { id: 'readonly-drawing',
      session: { header: { agentPreset: 'engineering' }, snapshotEvents: () => [] },
      steer: (message) => steered.push(message) };
    const stop = () => h.hooks.all('agent/turn-stopping').forEach(fn => fn({ agent }));
    stop();
    assert.equal(steered.length, 0, '只读图没有登记工程任务，无需完成标记也可结束');
    writeFileSync(path.join(temp, 'mode_state.json'), JSON.stringify({ rooms: { part: { active: true } } }));
    stop();
    assert.equal(steered.length, 1, '仅有活动房间也不能提前结束');
    writeFileSync(path.join(temp, 'workflow_state.json'), JSON.stringify({ task_id: 'current', step: 'design' }));
    writeFileSync(path.join(temp, 'mode_state.json'), JSON.stringify({
      rooms: { part: { task_id: 'current', active: false, ended_at: 'now' } },
    }));
    stop();
    assert.equal(steered.length, 2, '房间结束不代表整个工程流程已经收尾');
    writeFileSync(path.join(temp, 'workflow_state.json'), JSON.stringify({ task_id: 'current', step: 'idle' }));
    writeFileSync(path.join(temp, 'mode_state.json'), JSON.stringify({
      rooms: { old: { task_id: 'previous', active: true } },
    }));
    stop();
    assert.equal(steered.length, 2, '其他任务的旧房间不催办当前回合');
  } finally {
    h.dispose();
    if (oldState === undefined) delete process.env.DSH_STATE_DIR; else process.env.DSH_STATE_DIR = oldState;
    rmSync(temp, { recursive: true, force: true });
  }
});

test('材料失败等用户时放行，成功或真人纠正后恢复守卫；普通失败照常', () => {
  const temp = mkdtempSync(path.join(tmpdir(), 'dsh-eng-material-tools-'));
  const oldState = process.env.DSH_STATE_DIR;
  process.env.DSH_STATE_DIR = temp;
  const workflow = { task_id: 'current', step: 'design' };
  writeFileSync(path.join(temp, 'workflow_state.json'), JSON.stringify(workflow));
  writeFileSync(path.join(temp, 'mode_state.json'), JSON.stringify({
    rooms: { part: { task_id: 'current', active: true } },
  }));
  const h = harness();
  try {
    const now = Date.now();
    let clock = now;
    const events = [{ type: 'user/message', time: now, data: { source: { kind: 'user' }, content: [] } }];
    const steered = [];
    const agent = { id: 'material-tools',
      session: { header: { agentPreset: 'engineering' }, snapshotEvents: () => events },
      steer: (message) => steered.push(message) };
    const stop = () => h.hooks.all('agent/turn-stopping').forEach(fn => fn({ agent }));
    const output = (result, name = 'pwsh', command = `& "${path.join(temp, 'python.exe')}" "${path.join(temp, 'sw_bridge.py')}" run "part.py"`) => {
      const callId = 'call-' + events.length;
      if (name) events.push({ type: 'tool/call', time: ++clock,
        data: { callId, name, arguments: JSON.stringify({ command }) } });
      events.push({ type: 'tool/result', time: ++clock,
        data: { message: { toolCallId: callId, content: [{ type: 'text', text: '[swapi][WARN] failed\n'
          + JSON.stringify(result, null, 2) + '\n[exit code: 1]' }] } } });
    };
    output({ ok: false, retryable: false, error: 'ordinary command failure' });
    stop();
    assert.equal(steered.length, 1, '普通失败不会新增等待用户的放行条件');
    const needsUser = { ok: false, requires_user_action: true, retryable: false };
    output(needsUser, 'read_file');
    stop();
    assert.equal(steered.length, 2, '读取 JSON 文件回显等待字段不会放行');
    output(needsUser, 'pwsh', 'Get-Content material-status.json');
    stop();
    assert.equal(steered.length, 3, '普通 pwsh 文件读取也不会放行');
    output(needsUser, 'pwsh', 'Write-Output material-status');
    stop();
    assert.equal(steered.length, 4, '无关命令回显 JSON 不会放行');
    output(needsUser, null);
    stop();
    assert.equal(steered.length, 5, '没有匹配 callId 的工具结果不会放行');
    output({ ok: false, requires_user_action: true, retryable: true });
    stop();
    assert.equal(steered.length, 6, '可以自动重试的结果不放行');
    output({ ok: false, requires_user_action: true, retryable: false });
    stop();
    assert.equal(steered.length, 6, '明确需要用户且不可重试时不再拉回');
    events.push({ type: 'user/message', time: now + 100,
      data: { source: { kind: 'plugin:dsh-engineering-ui' }, content: [] } });
    stop();
    assert.equal(steered.length, 6, '插件补充消息不会冒充用户纠正');
    output({ ok: true, requires_user_action: false, retryable: false });
    stop();
    assert.equal(steered.length, 7, '新的成功结果取代同一会话旧失败');
    output({ ok: false, requires_user_action: true, retryable: false });
    clock = now + 200;
    events.push({ type: 'user/message', time: clock,
      data: { source: { kind: 'user' }, content: [{ type: 'text', text: '改用用户确认的准确材料' }] } });
    stop();
    assert.equal(steered.length, 8, '用户纠正后旧工具结果不再阻止正常继续');
    output({ ok: false, task_id: 'previous', requires_user_action: true, retryable: false });
    stop();
    assert.equal(steered.length, 9, '其他任务的结果不能放行');
    output({ ok: false, requires_user_action: true, retryable: false });
    stop();
    assert.equal(steered.length, 9, '纠正后新的真实失败仍可以停下等用户');
    output({ ok: true, material_defense: [{ ok: true, applicable: true, level: 'PASS' }] });
    stop();
    assert.equal(steered.length, 10, '新的桥接材料检查成功不继承旧工具失败');
    assert.deepEqual(JSON.parse(readFileSync(path.join(temp, 'workflow_state.json'), 'utf8')), workflow,
      '等待人工不写 finished 或修改任务状态');
  } finally {
    h.dispose();
    if (oldState === undefined) delete process.env.DSH_STATE_DIR; else process.env.DSH_STATE_DIR = oldState;
    rmSync(temp, { recursive: true, force: true });
  }
});

test('材料房间报告只认当前任务、所属房间和最新记录，用户纠正后恢复守卫', () => {
  const temp = mkdtempSync(path.join(tmpdir(), 'dsh-eng-material-report-'));
  const oldState = process.env.DSH_STATE_DIR;
  process.env.DSH_STATE_DIR = temp;
  const taskId = 'current';
  const reportDir = path.join(temp, 'reports', taskId);
  mkdirSync(reportDir, { recursive: true });
  writeFileSync(path.join(temp, 'workflow_state.json'), JSON.stringify({ task_id: taskId, step: 'design' }));
  const mode = { rooms: { '材料 房': { active: true, task_id: taskId },
    old: { active: true, task_id: 'previous' } }, subagents: { '材料 房': { id: 'child-a' } } };
  writeFileSync(path.join(temp, 'mode_state.json'), JSON.stringify(mode));
  const h = harness();
  try {
    const now = Date.now();
    const events = [{ type: 'user/message', time: now, data: { source: { kind: 'user' }, content: [] } }];
    const steered = [];
    const agent = { id: 'material-reports',
      session: { header: { agentPreset: 'engineering' }, snapshotEvents: () => events },
      steer: (message) => steered.push(message) };
    const stop = () => h.hooks.all('agent/turn-stopping').forEach(fn => fn({ agent }));
    const failed = { room: '材料 房', task_id: taskId, stage: 'failed', ts: (now + 1000) / 1000,
      requires_user_action: true, retryable: false };
    const report = (latest) => writeFileSync(path.join(reportDir, '材料 房.report.json'),
      JSON.stringify({ room: '材料 房', task_id: taskId, latest, history: [failed] }));
    writeFileSync(path.join(reportDir, 'old.report.json'), JSON.stringify({ room: 'old',
      latest: { ...failed, room: 'old' } }));
    stop();
    assert.equal(steered.length, 1, '其他任务房间的报告不放行');
    report(failed);
    stop();
    assert.equal(steered.length, 1, '当前任务最新报告明确等待用户时不拉回');
    report({ ...failed, task_id: 'previous' });
    stop();
    assert.equal(steered.length, 2, '同名文件内任务身份也必须匹配');
    report({ room: '材料 房', task_id: taskId, stage: 'validating', ts: (now + 2000) / 1000 });
    stop();
    assert.equal(steered.length, 3, '新报告恢复工作，history 的旧失败不再放行');
    report(failed);
    agent.id = 'child-b';
    agent.delegationDepth = 1;
    stop();
    assert.equal(steered.length, 4, '子代理不能借其他房间的材料失败放行');
    agent.id = 'child-a';
    stop();
    assert.equal(steered.length, 4, '所属房间的子代理可以等待用户');
    events.push({ type: 'user/message', time: now + 3000,
      data: { source: { kind: 'user' }, content: [] } });
    stop();
    assert.equal(steered.length, 5, '真人纠正后旧报告不再放行');
    assert.deepEqual(JSON.parse(readFileSync(path.join(temp, 'mode_state.json'), 'utf8')), mode,
      '等待人工不会结束房间或修改活动标记');
  } finally {
    h.dispose();
    if (oldState === undefined) delete process.env.DSH_STATE_DIR; else process.env.DSH_STATE_DIR = oldState;
    rmSync(temp, { recursive: true, force: true });
  }
});

test('日志端点走 0.2.0 的 open/read/close，过滤继承事件与高频 chunk', async () => {
  const h = harness({ child: {
    header: { parentSession: 'root', createdAt: 10 },
    inheritedEventCount: 2,
    events: [
      { seq: 0, type: 'assistant/message', data: {} },
      { seq: 1, type: 'tool/result', data: {} },
      { seq: 2, type: 'assistant/chunk', data: {} },
      { seq: 3, type: 'tool/call', data: { name: 'cad' } },
      { seq: 4, type: 'tool/result', data: { message: { content: [] } } },
    ],
  } });
  try {
    const log = await h.request('/log?sessionId=child');
    // 继承的 2 条被排除；assistant/chunk 属于 RAW_DROP
    assert.deepEqual(log.events.map(e => e.seq), [3, 4]);
    assert.equal(log.latestSeq, 4);
    assert.deepEqual((await h.request('/log?sessionId=child&since=3')).events.map(e => e.seq), [4]);
    assert.equal((await h.request('/log?sessionId=unknown')).status, 404);
    assert.equal((await h.request('/log?sessionId=../invalid')).status, 400);
  } finally { h.dispose(); }
});

const questions = [{ id: 'q1', question: 'Confirm part 10?', options: [{ label: 'Continue' }, { label: 'Revise' }] }];

test('子代理提问路由到父会话、可去重、答案以原生 tool_result 返回', async () => {
  const h = harness({ child: { header: { parentSession: 'root' }, events: [] } });
  try {
    const payload = { childSessionId: 'child', questions };
    const first = await h.request('/ask-child', payload);
    assert.equal(first.ok, true);
    assert.equal(first.rootSessionId, 'root', '提问必须挂到 root 会话（真人所在处）');
    assert.equal(h.asks[0].request.agent, h.root);
    // 同一题重复登记 → 复用同一张卡
    const second = await h.request('/ask-child', payload);
    assert.equal(second.pendingId, first.pendingId);
    assert.equal(h.asks.length, 1, '同题不得重复登记');
    assert.equal((await h.request('/pending-child?childSessionId=child')).pending.length, 1);
    const answers = [{ id: 'q1', selected: ['Continue'], custom: 'Use steel' }];
    h.asks[0].resolve({ answers });
    await new Promise(setImmediate);
    assert.deepEqual((await h.request('/result-child?pendingId=' + first.pendingId)).answers, answers);
    assert.equal((await h.request('/pending-child?rootSessionId=root')).pending.length, 0);
  } finally { h.dispose(); }
});

test('面板作答与主对话登记各自沿用既有协议', async () => {
  const h = harness();
  try {
    const first = await h.request('/ask-child', { childSessionId: 'child', rootSessionId: 'root', questions });
    const result = await h.request('/answer', { pendingId: first.pendingId, selected: [{ qid: 'q1', label: 'Revise' }], custom: '20 mm' });
    assert.equal(result.bound, 'child-tool-result');
    assert.deepEqual((await h.request('/result-child?pendingId=' + first.pendingId)).answers,
      [{ id: 'q1', selected: ['Revise'], custom: '20 mm' }]);
    // 重复提交必须被拒绝
    assert.equal((await h.request('/answer', { pendingId: first.pendingId })).ok, false);
    // 主对话提问
    const rootAsk = await h.request('/ask', { sessionId: 'root', questions });
    assert.equal(rootAsk.ok, true);
    h.asks[1].resolve({ answers: [{ id: 'q1', selected: ['Continue'] }] });
    await new Promise(setImmediate);
    assert.equal((await h.request('/ask', { sessionId: 'missing', questions })).ok, false);
  } finally { h.dispose(); }
});

test('client 半导出既有组件与渲染器（横幅已随问题2 移除）', () => {
  // 用最小 React shim 加载 client.js：只需要能求值出 exports，
  // 不依赖安装体的 react（那个在 app.asar 里，磁盘上取不到）。
  const shim = {
    createElement: () => null, Fragment: 'Fragment',
    useState: (v) => [typeof v === 'function' ? v() : v, () => {}],
    useRef: (v) => ({ current: v }), useEffect: () => {}, useMemo: (f) => f(),
  };
  let client;
  const mod = readFileSync(new URL('../lib/client.js', import.meta.url), 'utf8');
  // client.js 是浏览器侧的 __ModuleLoader__ 包装，用一个假 window 接住它
  const fn = new Function('window', 'console', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', mod);
  fn({ __ModuleLoader__: { load(def) { client = def.factory(() => shim); } } },
     console, setTimeout, clearTimeout, setInterval, clearInterval);
  for (const key of ['apply', 'SubagentConsole', 'EngineeringSettingsSection', 'AskCard', 'formatMessage', 'renderEvent', 'parseQuestions']) {
    assert.equal(typeof client[key], 'function', key);
  }
  assert.equal(client.SubagentDock, client.SubagentConsole);
  // 【问题2 修复】配套横幅已随 SW单行模式 一起删除
  assert.equal('EngineeringBanner' in client, false, 'EngineeringBanner must be removed');
  // 事件分类契约
  for (const [kind, type] of [['thinking', 'think'], ['message', 'text'], ['tool-call', 'tool'], ['tool-result', 'result'], ['error', 'error']]) {
    assert.equal(client.formatMessage({ kind, text: 'CAD result', tool: 'sw_bridge' }).t, type);
  }
  assert.equal(client.formatMessage({ kind: 'tool-call', tool: 'ask_user_question', args: JSON.stringify({ questions }) }).t, 'ask');
});
