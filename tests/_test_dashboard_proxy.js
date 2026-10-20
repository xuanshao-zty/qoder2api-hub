/* 全局出口代理面板的 VM 回归：真实 dashboard 函数 + 最小 DOM/请求桩。
 *
 *   node tests/_test_dashboard_proxy.js
 *
 * 不发网络请求。覆盖 GET /settings -> render，保存/测试/清除路由、全局 payload、
 * 面板 > 环境 > 直连、存储密码不回显、真实失败状态不画成功以及按钮复原。
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');
const ROOT = process.env.QD_DASH_ROOT || path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'dashboard.html'), 'utf8');
const SUITE_MD5 = crypto.createHash('md5').update(fs.readFileSync(__filename)).digest('hex');
let PASS = 0, FAIL = 0;
function check(name, cond, detail){
  if(cond){ PASS++; console.log('  ok   ' + name); }
  else { FAIL++; console.log('  FAIL ' + name + (detail ? '  ' + detail : '')); }
}
function finish(){
  console.log('SUITE: ' + path.basename(__filename) + ' md5=' + SUITE_MD5);
  console.log('FILE: dashboard.html md5=' + crypto.createHash('md5').update(html).digest('hex')
    + ' bytes=' + Buffer.byteLength(html, 'utf8') + ' codeunits=' + html.length
    + ' as-of=' + new Date().toISOString());
  console.log('');
  console.log('SUMMARY: TOTAL ' + (PASS + FAIL) + ' checks, ' + PASS + ' passed, ' + FAIL + ' failed');
  console.log('RESULT: ' + (FAIL ? 'RED' : 'GREEN') + ' (exit ' + (FAIL ? 1 : 0) + ')');
  process.exitCode = FAIL ? 1 : 0;
}
function slice(from, to){
  const a = html.indexOf(from);
  const b = a < 0 ? -1 : html.indexOf(to, a + from.length);
  return a >= 0 && b > a ? html.slice(a, b) : '';
}
const proxyJs = slice('let OUTBOUND_PROXY =', '/* ---- API key list:');
const loadJs = slice('async function loadSettings(', 'async function savePanelPassword(');
const PASSWORD = 'stored-password-never-render';
const PANEL_MASK = 'http://panel-user:***@127.0.0.1:31280';
const ENV_MASK = 'socks5://env-user:***@127.0.0.1:31281';
const PANEL = {
  outbound_proxy: {
    set: true, valid: true, has_auth: true, masked: PANEL_MASK,
    // 即便对象误带原始字段，页面也只能取 masked，不能把明文回填到输入框。
    url: 'http://panel-user:' + PASSWORD + '@127.0.0.1:31280', password: PASSWORD,
  },
  outbound_proxy_env: { set: true, masked: ENV_MASK }, api_keys: [], limits: null,
};
const ENV = { outbound_proxy: { set: false }, outbound_proxy_env: { set: true, masked: ENV_MASK } };
const DIRECT = { outbound_proxy: { set: false }, outbound_proxy_env: { set: false } };

function makeEnv(){
  const elements = {};
  ['setProxyState', 'setProxyStateText', 'setProxyUrl', 'setProxyResult'].forEach(id => {
    elements[id] = { value: '', placeholder: '', textContent: '', style: {} };
  });
  const st = { elements, posted: [], got: [], toasts: [], replies: [], settings: PANEL,
    renderedKeys: 0, limits: [], button: { disabled: false } };
  const sandbox = {
    document: { getElementById: id => elements[id] || null },
    window: {}, API_KEY_ROWS: [],
    getJSON: async url => { st.got.push(url); return st.settings; },
    postJSON: async (url, body) => {
      st.posted.push({ url, body: JSON.parse(JSON.stringify(body)), disabled: st.button.disabled });
      if(!st.replies.length) throw new Error('unexpected request without queued response');
      const reply = st.replies.shift();
      if(reply instanceof Error) throw reply;
      return reply;
    },
    toast: (msg, kind) => st.toasts.push({ msg, kind }),
    renderKeyRows: () => { st.renderedKeys++; },
    updateUI: () => {},
    renderLimits: data => st.limits.push(data),
  };
  vm.createContext(sandbox);
  vm.runInContext(loadJs + '\n' + proxyJs + '\nthis.api = { loadSettings, renderProxyState,'
    + ' saveOutboundProxy, clearOutboundProxy, testOutboundProxy };', sandbox,
    { filename: 'dashboard-proxy-functions.js' });
  return { st, api: sandbox.api };
}
function visible(st){
  return Object.values(st.elements).map(el => [el.value, el.placeholder, el.textContent].join(' ')).join('\n');
}
function globalPayload(call, key, value){
  return !!call && Object.keys(call.body).length === 1 && call.body[key] === value;
}

async function main(){
  check('锚点：代理函数和 loadSettings 都真实抽到，漂移不能静默假绿',
    proxyJs.length > 2000 && loadJs.length > 500);
  check('页面输入与保存/测试/清除三个按钮均接到真实代理函数',
    /id="setProxyUrl"/.test(html)
    && /onclick="saveOutboundProxy\(this\)"/.test(html)
    && /onclick="testOutboundProxy\(this\)"/.test(html)
    && /onclick="clearOutboundProxy\(this\)"/.test(html));
  let { st, api } = makeEnv();
  await api.loadSettings(false);
  check('loadSettings 通过正确 GET /settings 路由读取配置',
    st.got.length === 1 && st.got[0] === '/settings');
  check('loadSettings 把代理状态接到 render，而非只声明未调用',
    st.elements.setProxyStateText.textContent.includes(PANEL_MASK)
    && st.elements.setProxyState.textContent.includes('已设置'));
  check('面板代理优先环境代理，使用安全文本而非其它原始字段',
    st.elements.setProxyStateText.textContent.includes('面板')
    && !st.elements.setProxyStateText.textContent.includes(ENV_MASK)
    && st.elements.setProxyState.style.color === 'var(--accent2)');
  check('存储值不回填：input 为空、placeholder 仅有 masked、不含明文密码',
    st.elements.setProxyUrl.value === ''
    && st.elements.setProxyUrl.placeholder.includes(PANEL_MASK)
    && !visible(st).includes(PASSWORD));

  st.elements.setProxyUrl.value = 'stale-editor-value';
  api.renderProxyState(ENV);
  check('清空面板配置后渲染环境代理，输入框旧值被清掉',
    st.elements.setProxyUrl.value === ''
    && st.elements.setProxyStateText.textContent.includes(ENV_MASK)
    && st.elements.setProxyStateText.textContent.includes('环境变量')
    && st.elements.setProxyState.textContent.includes('环境变量'));
  api.renderProxyState(DIRECT);
  check('无任何代理时只展示直连状态，不渲染成代理已设置',
    st.elements.setProxyState.textContent.includes('直连')
    && st.elements.setProxyStateText.textContent.includes('直连')
    && st.elements.setProxyState.style.color === 'var(--dim)'
    && st.elements.setProxyUrl.value === '');
  api.renderProxyState(null);
  check('空配置渲染不会抛异常或残留旧代理',
    st.elements.setProxyStateText.textContent.includes('直连')
    && !st.elements.setProxyStateText.textContent.includes(PANEL_MASK));

  ({ st, api } = makeEnv());
  const raw = 'http://new-user:new-secret@127.0.0.1:31282';
  st.elements.setProxyUrl.value = '  ' + raw + '  ';
  st.replies.push(PANEL);
  await api.saveOutboundProxy(st.button);
  let call = st.posted[0];
  check('保存走 POST /settings/save，payload 只含全局 outbound_proxy',
    call && call.url === '/settings/save' && globalPayload(call, 'outbound_proxy', raw));
  check('保存等待时禁用按钮，结束后恢复，避免按钮留在忙状态',
    call && call.disabled === true && st.button.disabled === false);
  check('保存成功清空输入，回显 masked 而非刚提交的密码',
    st.elements.setProxyUrl.value === '' && visible(st).includes(PANEL_MASK)
    && !visible(st).includes('new-secret') && !visible(st).includes(PASSWORD));
  check('保存成功给成功反馈', st.toasts.at(-1).kind === 'ok');
  const count = st.posted.length;
  st.elements.setProxyUrl.value = '   ';
  await api.saveOutboundProxy(st.button);
  check('空串保存不发送覆盖请求，提示使用显式清除',
    st.posted.length === count && st.toasts.at(-1).kind === 'warn'
    && !st.button.disabled);

  st.elements.setProxyUrl.value = raw;
  st.replies.push(new Error('invalid proxy rejected'));
  await api.saveOutboundProxy(st.button);
  check('保存被拒绝时不报成功，显示错误并恢复按钮',
    st.toasts.at(-1).kind === 'bad' && st.toasts.at(-1).msg.includes('invalid proxy rejected')
    && !st.button.disabled);

  ({ st, api } = makeEnv());
  api.renderProxyState(PANEL);
  st.elements.setProxyResult.textContent = '旧的测试结果';
  st.replies.push(ENV);
  await api.clearOutboundProxy(st.button);
  call = st.posted[0];
  check('清除走 POST /settings/save，payload 只含 outbound_proxy 空串',
    call && call.url === '/settings/save' && globalPayload(call, 'outbound_proxy', ''));
  check('清除正确显示环境回落，不保留旧代理、密码或旧测试结果',
    st.elements.setProxyStateText.textContent.includes(ENV_MASK)
    && !st.elements.setProxyStateText.textContent.includes(PANEL_MASK)
    && !visible(st).includes(PASSWORD) && st.elements.setProxyResult.textContent === ''
    && st.elements.setProxyUrl.value === '');
  check('清除的按钮禁用/恢复与成功反馈正确',
    call && call.disabled === true && !st.button.disabled && st.toasts.at(-1).kind === 'ok');
  st.replies.push(DIRECT);
  await api.clearOutboundProxy(st.button);
  check('环境也为空时清除回落直连，而非渲染假代理',
    st.elements.setProxyStateText.textContent.includes('直连'));
  st.replies.push(new Error('clear denied'));
  await api.clearOutboundProxy(st.button);
  check('清除失败正确反馈并恢复按钮',
    st.toasts.at(-1).kind === 'bad' && st.toasts.at(-1).msg.includes('clear denied')
    && !st.button.disabled);

  ({ st, api } = makeEnv());
  st.elements.setProxyUrl.value = '  ' + raw + '  ';
  st.replies.push({ ok: true, source: 'input', masked: PANEL_MASK, ms: 12, status: 200 });
  await api.testOutboundProxy(st.button);
  call = st.posted[0];
  check('测试走 POST /settings/proxy-test，只发 trim 后 proxy，不误调用保存',
    call && call.url === '/settings/proxy-test' && globalPayload(call, 'proxy', raw)
    && st.posted.every(p => p.url !== '/settings/save'));
  check('代理探测成功显示连通性、耗时与 HTTP 状态',
    st.elements.setProxyResult.textContent.includes('可达')
    && st.elements.setProxyResult.textContent.includes('12')
    && st.elements.setProxyResult.textContent.includes('200')
    && st.elements.setProxyResult.style.color === 'var(--accent2)');
  check('测试结果只展示 masked，不显示输入或存储密码',
    st.elements.setProxyResult.textContent.includes(PANEL_MASK)
    && !st.elements.setProxyResult.textContent.includes('new-secret')
    && !st.elements.setProxyResult.textContent.includes(PASSWORD));
  check('测试按钮等待时禁用，完成后恢复且成功反馈正确',
    call && call.disabled === true && !st.button.disabled && st.toasts.at(-1).kind === 'ok');
  st.elements.setProxyUrl.value = '';
  st.replies.push({ ok: true, source: 'panel', masked: PANEL_MASK, ms: 4, status: 200 });
  await api.testOutboundProxy(st.button);
  check('测试输入留空仍调用后端，让后端选已保存配置/环境而非前端伪成功',
    st.posted.length === 2 && globalPayload(st.posted[1], 'proxy', '')
    && st.posted[1].url === '/settings/proxy-test');
  st.replies.push({ ok: false, source: 'input', error: '连接被拒绝：代理端口没在监听', ms: 1 });
  await api.testOutboundProxy(st.button);
  check('死端口 ok=false 必须显示失败，不残留上一次绿色成功',
    st.elements.setProxyResult.textContent.includes('失败')
    && st.elements.setProxyResult.textContent.includes('连接被拒绝')
    && st.elements.setProxyResult.style.color === 'var(--warn)'
    && st.toasts.at(-1).kind === 'bad' && !st.button.disabled);
  st.replies.push({ ok: true, direct: true, source: 'direct', masked: '', ms: 8, status: 200 });
  await api.testOutboundProxy(st.button);
  check('后端真实直连成功时正确区别为直连探测成功',
    st.elements.setProxyResult.textContent.includes('直连探测成功')
    && !st.elements.setProxyResult.textContent.includes('代理链路可达'));
  st.replies.push({ ok: false, direct: true, source: 'direct', error: 'direct probe failed' });
  await api.testOutboundProxy(st.button);
  check('无代理但真实直连失败时也不误画成功',
    st.elements.setProxyResult.textContent.includes('direct probe failed')
    && !st.elements.setProxyResult.textContent.includes('成功')
    && st.toasts.at(-1).kind === 'bad');
  st.replies.push(new Error('panel password required'));
  await api.testOutboundProxy(st.button);
  check('测试 HTTP/鉴权异常按失败展示并恢复按钮',
    st.elements.setProxyResult.textContent.includes('panel password required')
    && st.elements.setProxyResult.style.color === 'var(--warn)'
    && st.toasts.at(-1).kind === 'bad' && !st.button.disabled);
  st.replies.push({ ok: true, source: 'panel', realm: 'intl', ms: 250, status: 401,
    probe: 'https://openapi.qoder.sh/api/v1/userinfo',
    message: '探测未携带账号 Token；不是代理密码错误', masked: PANEL_MASK });
  await api.testOutboundProxy(st.button);
  check('401 连通响应显示区域、真实业务探测地址和无账号 Token 的说明',
    st.elements.setProxyResult.textContent.includes('国际版')
    && st.elements.setProxyResult.textContent.includes('HTTP 401')
    && st.elements.setProxyResult.textContent.includes('/api/v1/userinfo')
    && st.elements.setProxyResult.textContent.includes('不是代理密码错误')
    && st.elements.setProxyResult.style.color === 'var(--accent2)');
  st.replies.push({ ok: false, realm: 'cn', status: 503,
    probe: 'https://openapi.qoder.com.cn/api/v1/userinfo',
    error: '探测站返回 HTTP 503' });
  await api.testOutboundProxy(st.button);
  check('真正的 503 不改成绿色通过，失败同时显示目标区域和探测地址',
    st.elements.setProxyResult.textContent.includes('失败')
    && st.elements.setProxyResult.textContent.includes('国内版')
    && st.elements.setProxyResult.textContent.includes('503')
    && st.elements.setProxyResult.textContent.includes('https://openapi.qoder.com.cn/api/v1/userinfo')
    && st.elements.setProxyResult.style.color === 'var(--warn)'
    && st.toasts.at(-1).kind === 'bad');
  check('所有前端写请求只包含单一全局字段，不引入账号或 realm 代理设置',
    st.posted.every(p => Object.keys(p.body).join(',') === 'proxy'));
}

main().catch(error => {
  check('套件执行不应有未处理异常', false, String(error && error.stack || error));
}).finally(finish);
