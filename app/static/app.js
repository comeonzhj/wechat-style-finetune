/* 语料工坊 · 前端（无依赖） */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const view = $('#view');

const state = {
  config: null,
  styles: {},
  articles: [],
  minYear: 2023,
  filter: { q: '', year: 'scope', status: 'all' },
  selected: new Set(),
  job: null,
  jobTimer: null,
  view: 'articles',
  detailTab: 'brief',
};

const VOICES = { impersonal: '无人称', flat_first: '平实第一人称' };
const STYLE_ORDER = ['generic', 'news', 'academic', 'consult', 'marketing'];

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (n) => (n == null ? '—' : Number(n).toLocaleString('zh-CN'));
const pct = (n) => (n == null ? '—' : (n * 100).toFixed(0) + '%');

async function api(path, body, method) {
  const opt = { method: method || (body ? 'POST' : 'GET'), headers: {} };
  if (body) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  let j;
  try { j = await r.json(); } catch (e) { throw new Error('响应不是 JSON（HTTP ' + r.status + '）'); }
  if (!j.ok) throw new Error(j.error || 'HTTP ' + r.status);
  return j;
}

let toastTimer = null;
function toast(msg, type = '') {
  const t = $('#toast');
  t.className = 'toast ' + type + (type === 'bad' ? '' : '');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), type === 'bad' ? 6000 : 3200);
}

function badge(text, kind = 'line') {
  return `<span class="pill pill-${kind}">${esc(text)}</span>`;
}

function scoreClass(v) { return v >= 5 ? 'ok' : v >= 4 ? '' : v >= 3 ? 'warn' : 'bad'; }

/* ================= 路由 ================= */

function route() {
  const h = location.hash.replace(/^#\/?/, '') || 'home';
  // 产品层页面交给 studio.js，未加载时退回文章列表
  const simple = h.split('/')[0];
  if (['home', 'import', 'clean', 'train'].includes(simple) && window.STUDIO) {
    $$('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.nav === simple));
    state.view = simple;
    window.STUDIO.render(simple);
    return;
  }
  const activeNav = h.startsWith('a/') ? 'articles' : h.split('/')[0];
  $$('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.nav === activeNav));
  if (h.startsWith('a/')) {
    state.view = 'detail';
    viewDetail(h.slice(2));
  } else if (h.startsWith('config')) {
    state.view = 'config';
    viewConfig();
  } else {
    state.view = 'articles';
    viewArticles();
  }
}

/* ================= 文章列表 ================= */

async function loadArticles(force) {
  const r = await api('/api/articles' + (force ? '?force=1' : ''));
  state.articles = r.items;
  state.minYear = r.min_year;
}

function filteredArticles() {
  const { q, year, status } = state.filter;
  return state.articles.filter((a) => {
    if (year === 'scope' && !a.in_scope) return false;
    if (year === 'all') { /* 全部 */ }
    if (q && !a.title.toLowerCase().includes(q.toLowerCase())) return false;
    const s = a.status || {};
    if (status === 'todo' && (s.classified || s.brief || s.drafts)) return false;
    if (status === 'brief' && !s.brief) return false;
    if (status === 'draft' && !s.drafts) return false;
    if (status === 'accepted' && !s.accepted) return false;
    return true;
  });
}

function statusChips(s) {
  s = s || {};
  const out = [];
  if (s.classified) out.push(badge('分类', 'ok'));
  if (s.brief) out.push(badge('Brief', 'ok'));
  if (s.destyle) out.push(badge('去味', 'ok'));
  if (s.drafts) out.push(badge(`稿 ${s.drafts}`, 'accent'));
  if (s.accepted) out.push(badge(`通过 ${s.accepted}`, 'ok'));
  return out.join(' ') || '<span class="muted small">未处理</span>';
}

async function viewArticles() {
  if (!state.articles.length) {
    try { await loadArticles(true); } catch (e) { view.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  }
  const scope = state.articles.filter((a) => a.in_scope);
  const stats = {
    total: state.articles.length,
    scope: scope.length,
    brief: scope.filter((a) => a.status && a.status.brief).length,
    draft: scope.filter((a) => a.status && a.status.drafts).length,
    accepted: scope.reduce((n, a) => n + ((a.status && a.status.accepted) || 0), 0),
  };
  const noKey = !state.config.providers.some((p) => p.has_key || p.base_url.startsWith('mock://'));

  view.innerHTML = `
    <div class="page-head">
      <div class="grow">
        <h1 class="page-title">文章</h1>
        <div class="page-desc">共 ${fmt(stats.total)} 篇，其中 ${state.minYear} 年及以后 ${fmt(stats.scope)} 篇在训练范围内。所有产物落在 data/work/。</div>
      </div>
      <button class="btn" id="refresh">↻ 重新扫描</button>
    </div>

    ${noKey ? `<div class="callout warn">还没有可用的 API Key。先到<a href="#/config">配置</a>里填 DeepSeek 的 Key，或者把提供方切到 Mock 先跑通流程。</div>` : ''}

    <div class="stats">
      <div class="stat"><div class="stat-num">${fmt(stats.total)}</div><div class="stat-label">全部文章</div></div>
      <div class="stat"><div class="stat-num">${fmt(stats.scope)}</div><div class="stat-label">${state.minYear}+ 在范围</div></div>
      <div class="stat"><div class="stat-num">${fmt(stats.brief)}</div><div class="stat-label">已抽取 Brief</div></div>
      <div class="stat"><div class="stat-num">${fmt(stats.draft)}</div><div class="stat-label">已生成 AI 稿</div></div>
      <div class="stat"><div class="stat-num">${fmt(stats.accepted)}</div><div class="stat-label">质检通过</div></div>
    </div>

    <div class="toolbar">
      <input type="text" id="q" autocomplete="off" placeholder="搜索标题…" value="${esc(state.filter.q)}">
      <select id="f-year">
        <option value="scope">${state.minYear}+ 在范围</option>
        <option value="all">全部年份</option>
      </select>
      <select id="f-status">
        <option value="all">全部状态</option>
        <option value="todo">未处理</option>
        <option value="brief">有 Brief</option>
        <option value="draft">有 AI 稿</option>
        <option value="accepted">已通过</option>
      </select>
      <span class="sep"></span>
      <button class="btn btn-sm" id="sel-all">全选当前</button>
      <button class="btn btn-sm" id="sel-none">清空</button>
      <span class="pill pill-mute" id="sel-count">已选 0</span>
      <span class="sep"></span>
      <button class="btn btn-sm" data-run="brief">① 抽取 Brief</button>
      <button class="btn btn-sm" data-run="destyle">② 去风格化</button>
      <button class="btn btn-sm" data-run="draft">③ 生成 AI 稿 + 质检</button>
      <button class="btn btn-sm btn-primary" data-run="full">一键全流程</button>
      <span class="sep"></span>
      <button class="btn btn-sm" id="export">⤓ 导出训练集</button>
    </div>

    <div class="table-wrap">
      <table>
        <thead><tr>
          <th style="width:34px"><input type="checkbox" id="chk-head"></th>
          <th>标题</th><th style="width:64px">年份</th><th style="width:74px">字数</th>
          <th style="width:220px">状态</th><th style="width:90px"></th>
        </tr></thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
  `;

  $('#q').value = state.filter.q;
  $('#f-year').value = state.filter.year;
  $('#f-status').value = state.filter.status;

  renderRows();

  $('#q').addEventListener('input', (e) => { state.filter.q = e.target.value; renderRows(); });
  $('#f-year').addEventListener('change', (e) => { state.filter.year = e.target.value; renderRows(); });
  $('#f-status').addEventListener('change', (e) => { state.filter.status = e.target.value; renderRows(); });
  $('#refresh').addEventListener('click', async () => {
    try { await loadArticles(true); toast('已重新扫描', 'ok'); route(); } catch (e) { toast(e.message, 'bad'); }
  });
  $('#sel-all').addEventListener('click', () => {
    filteredArticles().forEach((a) => state.selected.add(a.id));
    renderRows();
  });
  $('#sel-none').addEventListener('click', () => { state.selected.clear(); renderRows(); });
  $('#chk-head').addEventListener('change', (e) => {
    const list = filteredArticles();
    if (e.target.checked) list.forEach((a) => state.selected.add(a.id));
    else list.forEach((a) => state.selected.delete(a.id));
    renderRows();
  });
  $$('[data-run]').forEach((b) => b.addEventListener('click', () => runFromButton(b.dataset.run)));
  $('#export').addEventListener('click', doExport);
}

function renderRows() {
  const list = filteredArticles();
  const tbody = $('#rows');
  if (!tbody) return;
  if (!list.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty">没有匹配的文章</td></tr>`;
  } else {
    tbody.innerHTML = list.map((a) => `
      <tr data-id="${a.id}">
        <td><input type="checkbox" class="rowchk" data-id="${a.id}" ${state.selected.has(a.id) ? 'checked' : ''}></td>
        <td class="title"><a href="#/a/${a.id}" title="${esc(a.title)}">${esc(a.title)}</a></td>
        <td class="num">${a.year || '—'}</td>
        <td class="num">${fmt(a.chars)}</td>
        <td class="chips">${statusChips(a.status)}</td>
        <td><a class="btn btn-sm" href="#/a/${a.id}">查看</a></td>
      </tr>`).join('');
  }
  $$('.rowchk').forEach((c) => c.addEventListener('change', (e) => {
    const id = e.target.dataset.id;
    if (e.target.checked) state.selected.add(id); else state.selected.delete(id);
    updateSelCount();
  }));
  updateSelCount();
  const head = $('#chk-head');
  if (head) head.checked = list.length > 0 && list.every((a) => state.selected.has(a.id));
}

function updateSelCount() {
  const el = $('#sel-count');
  if (el) el.textContent = `已选 ${state.selected.size}`;
}

const RUN_PRESETS = {
  brief: { stages: ['classify', 'brief'], limit: 20 },
  destyle: { stages: ['destyle'], limit: 20 },
  draft: { stages: ['draft', 'judge'], limit: 10 },
  full: { stages: ['classify', 'brief', 'destyle', 'draft', 'judge'], all: true },
};

async function runFromButton(kind) {
  const preset = RUN_PRESETS[kind];
  const ids = [...state.selected];
  const body = { stages: preset.stages };
  if (ids.length) body.article_ids = ids;
  else if (preset.all) {
    const todo = state.articles.filter((a) => a.in_scope && a.status && a.status.drafts === 0);
    if (!confirm(`将排队处理 ${todo.length} 篇范围内还没生成 AI 稿的文章，确定？`)) return;
    body.all = true;
  } else body.limit = preset.limit;
  try {
    const r = await api('/api/run', body);
    startPolling(r.job_id);
    toast(`已启动：${r.total} 篇`, 'ok');
  } catch (e) { toast(e.message, 'bad'); }
}

async function doExport() {
  try {
    const r = await api('/api/export', {});
    toast(`导出完成：${r.total} 对。train ${r.paths.train.count} / eval ${r.paths.eval.count}`, 'ok');
  } catch (e) { toast(e.message, 'bad'); }
}

/* ================= 运行抽屉 ================= */

function startPolling(jobId) {
  state.job = jobId;
  $('#drawer').classList.remove('hidden');
  clearInterval(state.jobTimer);
  const tick = async () => {
    try {
      const r = await api('/api/run/' + jobId);
      renderJob(r.job);
      if (r.job.status !== 'running') {
        clearInterval(state.jobTimer);
        state.jobTimer = null;
        loadArticles(true).then(() => {
          if (state.view === 'articles') viewArticles();
          if (state.view === 'detail') viewDetail(currentDetailId);
        });
        toast(`运行结束：完成 ${r.job.done}/${r.job.total}，失败 ${r.job.failed}`, r.job.failed ? 'bad' : 'ok');
      }
    } catch (e) { clearInterval(state.jobTimer); state.jobTimer = null; toast(e.message, 'bad'); }
  };
  tick();
  state.jobTimer = setInterval(tick, 1200);
  $('#job-stop').onclick = async () => {
    try { await api(`/api/run/${jobId}/stop`, {}); toast('已请求停止'); } catch (e) { toast(e.message, 'bad'); }
  };
  $('#job-close').onclick = () => $('#drawer').classList.add('hidden');
}

function renderJob(j) {
  const ratio = j.total ? Math.round((j.done / j.total) * 100) : 0;
  $('#job-ratio').textContent = `${j.done}/${j.total}`;
  $('#job-sub').textContent = j.status === 'running'
    ? `并发处理中${j.current.length ? '：' + j.current.slice(0, 2).map((t) => t.slice(0, 16)).join(' / ') : ''}`
    : j.status === 'stopping'
      ? '正在停止：不再派发新任务，进行中的调用返回后丢弃'
      : `已结束 · 失败 ${j.failed} · ${j.ended_at || ''}`;
  const stopBtn = $('#job-stop');
  if (stopBtn) { stopBtn.disabled = j.status !== 'running'; stopBtn.textContent = j.status === 'stopping' ? '停止中…' : '停止'; }
  $('#job-bar').style.width = ratio + '%';
  const log = $('#job-log');
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
  log.innerHTML = j.logs.map((l) => `<div><span class="t">${l.t}</span>${esc(l.title ? '「' + l.title + '」' : '')}${esc(l.msg)}</div>`).join('');
  if (atBottom) log.scrollTop = log.scrollHeight;
}

/* ================= 详情 ================= */

let currentDetailId = null;

async function viewDetail(id) {
  currentDetailId = id;
  try {
    const r = await api('/api/articles/' + id);
    renderDetail(r.article, r.work);
  } catch (e) {
    view.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

function renderDetail(a, w) {
  const t = (w && w._target_body) || a.body;
  const flags = (w && w.flags) || {};
  const drafts = (w && w.drafts) || [];
  const tabs = [
    ['original', '原文'], ['classify', '分类'], ['brief', 'Brief'],
    ['destyle', '去风格化'], ['drafts', `AI 稿 ${drafts.length || ''}`],
  ];
  if (!state.detailTab) state.detailTab = 'brief';

  view.innerHTML = `
    <div class="row" style="margin-bottom:10px">
      <a href="#/articles" class="btn btn-sm">← 返回</a>
      <span class="pill pill-mute">${esc(a.file)}</span>
      <span class="pill pill-line">${esc(a.published_at || a.year || '')}</span>
      <span class="pill pill-line">${fmt(a.chars)} 字</span>
      ${a.image_slots ? `<span class="pill pill-line">图位 ${a.image_slots}</span>` : ''}
    </div>
    <div class="detail-head">
      <div class="grow">
        <h1 class="detail-title">${esc(a.title)}</h1>
        ${a.source ? `<a class="small" href="${esc(a.source)}" target="_blank" rel="noreferrer">原文链接</a>` : ''}
      </div>
    </div>

    <div class="flag-row">
      ${['eval:评测集', 'cut_promo:切掉推广', 'skip:跳过此篇'].map((x) => {
        const [k, label] = x.split(':');
        return `<label class="check"><input type="checkbox" class="flag" data-flag="${k}" ${flags[k] ? 'checked' : ''}>${label}</label>`;
      }).join('')}
      <span class="small muted">改 flag 立即保存</span>
    </div>

    <div class="tabs">
      ${tabs.map(([k, label]) => `<button class="tab ${state.detailTab === k ? 'active' : ''}" data-tab="${k}">${esc(label)}</button>`).join('')}
    </div>
    <div id="tab-body"></div>
  `;

  $$('.tab').forEach((b) => b.addEventListener('click', () => {
    state.detailTab = b.dataset.tab;
    $$('.tab').forEach((x) => x.classList.toggle('active', x === b));
    renderTab(a, w);
  }));
  $$('.flag').forEach((c) => c.addEventListener('change', async () => {
    const f = {};
    f[c.dataset.flag] = c.checked;
    try {
      const r = await api(`/api/articles/${a.id}/flags`, { flags: f });
      toast('已保存', 'ok');
      loadArticles(true);
      if (c.dataset.flag === 'cut_promo') viewDetail(a.id);
    } catch (e) { toast(e.message, 'bad'); }
  }));

  renderTab(a, w);
}

function renderTab(a, w) {
  const el = $('#tab-body');
  const tab = state.detailTab;
  const body = (w && w._target_body) || a.body;
  const drafts = (w && w.drafts) || [];

  if (tab === 'original') {
    el.innerHTML = `<div class="callout">这是参与训练的目标文本（已去 frontmatter / 配图占位 / H1）。${(w && w.flags && w.flags.cut_promo) ? '已按分类结果切掉文末推广。' : ''}</div>
      <div class="text-body">${esc(body)}</div>`;
    return;
  }

  if (tab === 'classify') {
    el.innerHTML = w && w.classify
      ? `<pre class="json">${esc(JSON.stringify(w.classify, null, 2))}</pre>`
      : `<div class="empty">还没有分类结果。在文章列表里选中后点「① 抽取 Brief」。</div>`;
    return;
  }

  if (tab === 'brief') {
    if (!w || !w.brief) { el.innerHTML = `<div class="empty">还没有 Brief。</div>`; return; }
    const m = w.brief_metrics || {};
    const b = w.brief;
    const thin = (b.sections || []).filter((s) => s.thin).length;
    el.innerHTML = `
      <div class="metric-grid">
        ${metric('表达层重合', (m.lcs || 0) + ' 字', (m.lcs || 0) > 25 ? 'bad' : (m.lcs || 0) > 15 ? 'warn' : 'ok')}
        ${metric('4-gram 重合', m.jaccard4 ?? '—', (m.jaccard4 || 0) > 0.15 ? 'warn' : 'ok')}
        ${metric('节数', (b.sections || []).length)}
        ${metric('薄节 thin', thin, thin ? 'warn' : 'ok')}
        ${metric('目标字数', fmt(b.length_target))}
        ${metric('结构', b.structure_mode || '—')}
      </div>
      <div class="card" style="margin-bottom:12px">
        <div class="kv"><b>主题</b><span>${esc(b.topic || '')}</span></div>
        <div class="kv"><b>读者</b><span>${esc(b.target_reader || '')}</span></div>
        <div class="kv"><b>身份</b><span>${esc((b.speaker || {}).position || '')}</span></div>
        <div class="kv"><b>岗位</b><span>CTA ${(b.cta || {}).exists ? esc((b.cta || {}).kind || '有') : '无'} · 保留词 ${(b.keep_terms || []).length}</span></div>
      </div>
      ${(b.sections || []).map((s) => `
        <div class="section-card">
          <h4>${s.order}. ${esc(s.function || '')} <span class="muted small">· 约 ${fmt(s.min_chars)} 字${s.thin ? ' · <span style="color:var(--warn)">素材偏薄</span>' : ''}</span></h4>
          <div class="kv"><b>讲什么</b><span>${esc(s.topic_line || '')}</span></div>
          <div class="kv"><b>论点</b><span>${(s.claims || []).map((c) => '· ' + esc(c)).join('<br>')}</span></div>
          ${(s.facts || []).length ? `<div class="kv"><b>事实</b><span>${(s.facts || []).map((f) => '· ' + esc(f.content) + ` <span class="muted">[${esc(f.kind)}]</span>`).join('<br>')}</span></div>` : ''}
          ${s.punchline_intent ? `<div class="kv"><b>要记住</b><span>${esc(s.punchline_intent)}</span></div>` : ''}
        </div>`).join('')}
      <details style="margin-top:8px"><summary class="muted small" style="cursor:pointer">原始 JSON</summary>
        <pre class="json" style="margin-top:8px">${esc(JSON.stringify(b, null, 2))}</pre></details>
    `;
    return;
  }

  if (tab === 'destyle') {
    const d = w && w.brief_destyle;
    if (!d) { el.innerHTML = `<div class="empty">还没跑去风格化检查。</div>`; return; }
    const leaks = d.leaks || [];
    el.innerHTML = `
      <div class="metric-grid">
        ${metric('残留分', d.style_residue_score ?? '—', (d.style_residue_score || 0) >= 4 ? 'bad' : (d.style_residue_score || 0) >= 3 ? 'warn' : 'ok')}
        ${metric('剩余泄漏', leaks.length, leaks.length ? 'warn' : 'ok')}
        ${metric('开始泄漏', d.leaks_before ?? '—')}
        ${metric('改写轮数', (d.rounds || []).length)}
      </div>
      ${leaks.length ? leaks.map((l) => `<div class="leak"><b>${esc(l.type || '泄漏')}</b> · ${esc(l.field || '')}<br>
        「${esc(l.text || '')}」<br><span class="muted">${esc(l.reason || '')}</span>
        ${l.fix ? `<br>→ ${esc(l.fix)}` : ''}</div>`).join('') : '<div class="callout">没有发现残留的"声音"。这份 Brief 可以交给写手了。</div>'}
      <div class="row" style="margin-top:10px"><button class="btn btn-sm" id="copy-brief">复制修订后 Brief</button></div>
      <pre class="json" style="margin-top:10px">${esc(JSON.stringify(d.revised_brief || w.brief, null, 2))}</pre>
    `;
    const btn = $('#copy-brief');
    if (btn) btn.onclick = () => {
      navigator.clipboard.writeText(JSON.stringify(d.revised_brief || w.brief, null, 2));
      toast('已复制', 'ok');
    };
    return;
  }

  if (tab === 'drafts') {
    if (!drafts.length) {
      el.innerHTML = `<div class="empty">还没有 AI 稿。选中文章后点「③ 生成 AI 稿 + 质检」。</div>`;
      return;
    }
    state._targetBody = body;
    el.innerHTML = drafts.map((d, i) => draftCard(d, i)).join('');
    $$('[data-act="judge"]').forEach((b) => b.addEventListener('click', async () => {
      b.disabled = true; b.textContent = '质检中…';
      try {
        await api(`/api/articles/${a.id}/judge`, { key: b.dataset.key });
        toast('质检完成', 'ok');
        viewDetail(a.id);
      } catch (e) { toast(e.message, 'bad'); b.disabled = false; b.textContent = '单独质检'; }
    }));
    return;
  }
}

function metric(k, v, cls = '') {
  return `<div class="metric"><div class="metric-k">${esc(k)}</div><div class="metric-v ${cls}">${esc(v)}</div></div>`;
}

function scoreChip(k, v) {
  return `<span class="score">${esc(k)} <b class="${v >= 4 ? 'ok' : v >= 3 ? 'warn' : 'bad'}">${v ?? '—'}</b></span>`;
}

function draftCard(d, i) {
  const m = d.metrics || {};
  const j = d.judge || null;
  const sc = (j && j.scores) || {};
  const verdictKind = j ? (j.verdict === 'accept' ? 'ok' : j.verdict === 'repair' ? 'warn' : 'bad') : 'mute';
  const lcsCls = (m.lcs || 0) > 25 ? 'bad' : (m.lcs || 0) > 15 ? 'warn' : 'ok';
  const ratioCls = (m.length_ratio || 0) < 0.7 || (m.length_ratio || 0) > 1.35 ? 'bad' : 'ok';
  return `
  <div class="draft">
    <div class="draft-head">
      <b>#${i + 1}</b>
      ${badge(d.style, 'accent')} ${badge(VOICES[d.voice] || d.voice, 'line')}
      ${badge(d.provider_label || d.provider, 'line')} <span class="mono small">${esc(d.model || '')}</span>
      ${d.structure_override !== 'none' ? badge('结构重排:' + d.structure_override, 'warn') : ''}
      <span class="grow"></span>
      ${j ? badge((j.verdict || '?'), verdictKind) : badge('未质检', 'mute')}
      <button class="btn btn-sm" data-act="judge" data-key="${esc(d.key)}">单独质检</button>
    </div>
    <div class="draft-body" style="padding:12px 14px">
      <div class="metric-grid">
        ${metric('字数', fmt(m.chars_draft))}
        ${metric('长度比', m.length_ratio ?? '—', ratioCls)}
        ${metric('最长重合', (m.lcs || 0) + ' 字', lcsCls)}
        ${metric('4-gram', m.jaccard4 ?? '—', (m.jaccard4 || 0) > 0.2 ? 'bad' : (m.jaccard4 || 0) > 0.12 ? 'warn' : 'ok')}
        ${metric('多出数字', (m.numbers_extra || []).length, (m.numbers_extra || []).length ? 'bad' : 'ok')}
        ${metric('缺保留词', (m.keep_terms_missing || []).length, (m.keep_terms_missing || []).length ? 'warn' : 'ok')}
        ${metric('闸门', d.gate && d.gate.ok ? '通过' : '未过', d.gate && d.gate.ok ? 'ok' : 'warn')}
      </div>
      ${(d.gate && d.gate.reasons || []).length ? `<div class="callout warn small">${d.gate.reasons.map(esc).join('；')}</div>` : ''}
      ${j ? `
        <div class="scores">
          ${scoreChip('同题', sc.same_topic)}${scoreChip('保真', sc.faithfulness)}${scoreChip('距离', sc.style_distance)}
          ${scoreChip('重合', sc.leak)}${scoreChip('篇幅', sc.length_fit)}${scoreChip('可发', sc.publishable)}
        </div>
        ${renderJudgeDetail(j)}
      ` : ''}
      ${(m.numbers_extra || []).length ? `<div class="small muted">多出的数字：${m.numbers_extra.slice(0, 12).map(esc).join('、')}</div>` : ''}
    </div>
    <details class="draft-body">
      <summary>展开全文与对照 ↓</summary>
      <div class="cols" style="padding:0 12px 12px">
        <div><div class="small muted" style="margin-bottom:6px">AI 稿</div><div class="text-body" style="max-height:52vh;padding:14px 16px">${esc(d.text)}</div></div>
        <div><div class="small muted" style="margin-bottom:6px">你的原文（目标）</div><div class="text-body" style="max-height:52vh;padding:14px 16px">${esc((state._targetBody || ''))}</div></div>
      </div>
    </details>
  </div>`;
}

function renderJudgeDetail(j) {
  const ev = j.evidence || {};
  const arr = (x) => (Array.isArray(x) ? x : x ? [x] : []);
  const blocks = [];
  if (arr(ev.fabrications).length) blocks.push(`<div class="leak"><b>疑似编造</b><br>${arr(ev.fabrications).map(esc).join('<br>')}</div>`);
  if (arr(ev.number_mismatch).length) blocks.push(`<div class="leak"><b>数字不一致</b><br>${arr(ev.number_mismatch).map(esc).join('<br>')}</div>`);
  if (arr(ev.omissions).length) blocks.push(`<div class="callout warn small"><b>遗漏</b>：${arr(ev.omissions).map(esc).join('；')}</div>`);
  if (arr(ev.style_leaks).length) blocks.push(`<div class="callout warn small"><b>像原文</b>：${arr(ev.style_leaks).map(esc).join('；')}</div>`);
  if (arr(j.issues).length) blocks.push(`<div class="small muted">问题：${arr(j.issues).map(esc).join('；')}</div>`);
  if (j.repair_instructions) blocks.push(`<div class="callout small"><b>修改要求</b><br>${esc(j.repair_instructions)}</div>`);
  return blocks.join('');
}

/* ================= 配置 ================= */

function providerSelect(name, selected) {
  return `<select class="${name}">${state.config.providers.map((p) =>
    `<option value="${esc(p.id)}" ${p.id === selected ? 'selected' : ''}>${esc(p.label)}${p.has_key ? '' : ' (无 Key)'}</option>`).join('')}</select>`;
}

function viewConfig() {
  const c = state.config;
  const s = c.settings || {};
  view.innerHTML = `
    <div class="page-head">
      <div class="grow">
        <h1 class="page-title">配置</h1>
        <div class="page-desc">Key 只存在本机 <code>data/config.json</code>（权限 600），不会上传。改完记得保存。</div>
      </div>
    </div>

    <div id="cfg-warn"></div>

    <div class="card">
      <div class="row" style="justify-content:space-between;align-items:flex-start">
        <div>
          <div class="card-title">模型提供方</div>
          <div class="card-desc">任何 OpenAI 兼容的 <code>/chat/completions</code> 都能接。豆包填火山方舟 <code>api/v3</code> 地址，模型填推理接入点 ID。</div>
        </div>
        <button class="btn btn-sm" id="add-provider">＋ 添加提供方</button>
      </div>
      <div id="providers">
        ${c.providers.map((p) => providerCard(p)).join('')}
      </div>
    </div>

    <div class="card">
      <div class="card-title">角色分配</div>
      <div class="card-desc">抽取 / 去风格化 / 裁判。裁判默认走「交叉」策略：不用生成该稿的同一家模型当裁判。</div>
      <div class="role-grid">
        ${['classify:分类', 'brief:抽取 Brief', 'destyle:去风格化', 'judge:质检裁判'].map((x) => {
          const [k, label] = x.split(':');
          const r = (c.roles || {})[k] || {};
          return `<div data-role="${k}">
            <label class="field"><span class="field-label">${label}</span>
              <div class="row" style="gap:6px">
                ${providerSelect('r-provider', r.provider)}
                <input type="text" autocomplete="off" spellcheck="false" class="r-model" style="flex:1;min-width:120px" value="${esc(r.model || '')}" placeholder="模型 ID">
                <input type="number" class="r-temp" step="0.1" min="0" max="2" style="width:72px" value="${r.temperature ?? 0.2}" title="temperature">
              </div>
            </label>
          </div>`;
        }).join('')}
      </div>
      <label class="field" style="margin-top:6px">
        <span class="field-label">裁判策略</span>
        <div class="row">
          <select id="judge-policy" style="width:auto">
            <option value="cross" ${c.judge_policy === 'cross' ? 'selected' : ''}>交叉（推荐）— 裁判尽量不同于写手</option>
            <option value="fixed" ${c.judge_policy === 'fixed' ? 'selected' : ''}>固定 — 永远用上面指定的裁判</option>
          </select>
          <span class="hint">写手是 DeepSeek 时，裁判最好用豆包/MiMo，否则它会觉得自己的腔调很自然。</span>
        </div>
      </label>
    </div>

    <div class="card">
      <div class="row" style="justify-content:space-between;align-items:flex-start">
        <div>
          <div class="card-title">写手矩阵</div>
          <div class="card-desc">每篇按「跨提供方」优先挑 ${s.drafts_per_article || 2} 个写手。generic 腔最接近真实输入，建议占比最大。</div>
        </div>
        <div class="row">
          <button class="btn btn-sm" id="reset-writers">恢复默认</button>
          <button class="btn btn-sm" id="add-writer">＋ 写手</button>
        </div>
      </div>
      <div id="writers">
        <div class="writer-row writer-head">
          <span>提供方</span><span>模型</span><span>腔调</span><span>人称</span><span>温度</span><span>启用</span><span></span>
        </div>
        ${(c.writers || []).map((w, i) => writerRow(w, i)).join('')}
      </div>
    </div>

    <div class="card">
      <div class="card-title">全局设置</div>
      <div class="grid grid-3">
        <label class="field"><span class="field-label">起始年份（之前的文章不进范围）</span>
          <input type="number" id="s-minyear" value="${s.min_year ?? 2023}"></label>
        <label class="field"><span class="field-label">并发数</span>
          <input type="number" id="s-conc" min="1" max="16" value="${s.concurrency ?? 3}"></label>
        <label class="field"><span class="field-label">每篇生成几份 AI 稿</span>
          <input type="number" id="s-drafts" min="1" max="4" value="${s.drafts_per_article ?? 2}"></label>
        <label class="field"><span class="field-label">结构重排样本比例（0–1）</span>
          <input type="number" id="s-override" step="0.01" min="0" max="1" value="${s.structure_override_rate ?? 0.12}"></label>
        <label class="field"><span class="field-label">单次请求超时（秒）</span>
          <input type="number" id="s-timeout" min="30" max="900" value="${s.request_timeout ?? 300}"></label>
        <label class="field"><span class="field-label">单次输出 token 上限</span>
          <input type="number" id="s-tokcap" value="${s.max_tokens_cap ?? 32000}"></label>
        <label class="field"><span class="field-label">质检不过自动回炉轮数（0 = 不回炉）</span>
          <input type="number" id="s-repair" min="0" max="3" value="${s.auto_repair_rounds ?? 1}"></label>
      </div>
    </div>

    <div class="card">
      <div class="card-title">导出训练集</div>
      <div class="card-desc">导出 ChatML JSONL，loss 只算 assistant 段。指令会在下面几句里随机轮换，避免模型依赖单一魔法短语。</div>
      <label class="field"><span class="field-label">system prompt</span>
        <textarea id="e-system" class="mono" rows="6">${esc(c.export.system_prompt || '')}</textarea></label>
      <label class="field"><span class="field-label">用户指令池（每行一句）</span>
        <textarea id="e-instr" rows="6">${esc((c.export.instructions || []).join('\n'))}</textarea></label>
      <label class="check"><input type="checkbox" id="e-accepted" ${c.export.only_accepted ? 'checked' : ''}> 只导出质检 accept 的样本</label>
      <label class="check" style="margin-top:6px"><input type="checkbox" id="e-strip" ${c.export.strip_promo !== false ? 'checked' : ''}> 导出时清掉结尾的公众号导流（推荐）</label>
      <div class="hint" style="margin-left:22px">41% 的文章结尾是「赞赏、点赞、转发、在看 / 扫码领券加入」，不清掉模型会把它当固定收尾反复生成。同时会把 <code>&lt;tool_call&gt;</code> 这类特殊 token 标记降级成普通文字。</div>
      <div class="divider"></div>
      <div class="row" style="gap:14px;align-items:flex-end">
        <label class="check"><input type="checkbox" id="e-seg" ${c.export.segment !== false ? 'checked' : ''}> 长文切段导出</label>
        <label class="field" style="margin:0;width:110px"><span class="field-label">每段最小</span>
          <input type="number" id="e-segmin" value="${c.export.segment_min ?? 2000}"></label>
        <label class="field" style="margin:0;width:110px"><span class="field-label">每段最大</span>
          <input type="number" id="e-segmax" value="${c.export.segment_max ?? 3000}"></label>
        <span class="hint" style="flex:1">超过上限的文章会按段落切成 2000–3000 字，AI 稿用比例对齐到同一处边界；不超过的整篇导出。</span>
      </div>
      <div class="divider"></div>
      <div class="row" style="gap:10px;align-items:flex-end">
        <label class="field" style="margin:0;width:110px"><span class="field-label">评测集篇数</span>
          <input type="number" id="e-evaln" value="40"></label>
        <button class="btn" id="sample-eval">随机划分评测集</button>
        <span class="hint" style="flex:1">按字数分层随机抽（种子固定=可复现），抽中的文章不进 train，只进 eval.jsonl。</span>
      </div>
    </div>

    <div class="sticky-save">
      <button class="btn" id="reload-config">放弃修改</button>
      <button class="btn btn-primary" id="save-config">保存配置</button>
    </div>
  `;

  $$('#providers .provider-card').forEach((card) => {
    const pid = card.dataset.pid;
    const testBtn = $('.p-test', card);
    testBtn.addEventListener('click', async () => {
      const msg = $('.test-msg', card);
      const old = testBtn.textContent;
      testBtn.disabled = true;
      testBtn.textContent = '测试中…';
      msg.className = 'test-msg show';
      msg.innerHTML = '<span class="muted">正在用表单里的地址试连…</span>';
      const payload = {
        provider: pid,
        label: $('.p-label', card).value.trim(),
        base_url: $('.p-base', card).value.trim(),
        api_key: $('.p-key', card).value.trim(),
        models: $('.p-models', card).value.split(/[,，\n]/).map((x) => x.trim()).filter(Boolean),
        json_mode: $('.p-json', card).checked,
        reasoning: $('.p-reasoning', card).checked,
        disable_thinking: $('.p-nothink', card).checked,
        model: $('.p-models', card).value.split(/[,，]/)[0].trim(),
      };
      try {
        const r = await api('/api/providers/test', payload);
        testBtn.textContent = old;
        testBtn.disabled = false;
        const url = `<div class="mono small">POST ${esc(r.url || payload.base_url || '(空)')}</div>`;
        if (r.ok) {
          msg.className = 'test-msg show ok';
          msg.innerHTML = `<b>✓ 可用</b> · ${r.ms}ms · ${esc(r.reply || '')}${url}`;
          toast('连接正常', 'ok');
        } else {
          msg.className = 'test-msg show bad';
          msg.innerHTML = `<b>✗ 失败</b><div class="mono small" style="margin:4px 0">${esc(r.error || '')}</div>${url}<div class="small">${esc(hintFor(r.error || ''))}</div>`;
        }
      } catch (e) {
        testBtn.textContent = old;
        testBtn.disabled = false;
        msg.className = 'test-msg show bad';
        msg.innerHTML = `<b>✗ 失败</b> ${esc(e.message)}`;
      }
    });
    $('.p-del', card)?.addEventListener('click', () => {
      card.remove();
      toast('已从列表移除，保存后生效');
    });
  });

  $('#add-writer').addEventListener('click', () => {
    const firstP = state.config.providers[0];
    const w = { id: 'w_' + Math.random().toString(36).slice(2, 7), provider: firstP.id, model: '', style: 'generic', voice: 'impersonal', temperature: 0.8, enabled: true };
    $('#writers').insertAdjacentHTML('beforeend', writerRow(w, 999));
  });
  $('#reset-writers').addEventListener('click', () => {
    if (!confirm('恢复默认写手矩阵？当前未保存的写手配置会丢失。')) return;
    state._defaultWriters = true;
    $('#writers').innerHTML = `<div class="writer-row writer-head"><span>提供方</span><span>模型</span><span>腔调</span><span>人称</span><span>温度</span><span>启用</span><span></span></div>`;
    DEFAULTS.writers.forEach((w) => $('#writers').insertAdjacentHTML('beforeend', writerRow(w, 999)));
  });
  $('#add-provider').addEventListener('click', () => {
    const id = 'p_' + Math.random().toString(36).slice(2, 7);
    const p = { id, label: '新提供方', base_url: '', api_key: '', models: [''], json_mode: false, enabled: true, has_key: false };
    $('#providers').insertAdjacentHTML('beforeend', providerCard(p));
    toast('已添加，保存后生效');
  });
  $('#save-config').addEventListener('click', saveConfigFromForm);
  $('#sample-eval').addEventListener('click', async () => {
    const n = parseInt($('#e-evaln').value || '40', 10);
    if (!confirm(`按字数分层随机抽 ${n} 篇作为评测集？会清除之前的评测集标记。`)) return;
    try {
      const r = await api('/api/eval/sample', { n, seed: 42, reset: true });
      toast(`已标记 ${r.count} 篇（分层：${JSON.stringify(r.tiers)}）`, 'ok');
      loadArticles(true);
    } catch (e) { toast(e.message, 'bad'); }
  });
  renderConfigWarnings();
  view.addEventListener('input', () => renderConfigWarnings());
  view.addEventListener('change', (e) => {
    if (e.target.classList.contains('p-base')) {
      const card = e.target.closest('.provider-card');
      const el = $('.p-effective', card);
      if (el) el.textContent = effectiveUrl(e.target.value);
    }
    renderConfigWarnings();
  });
  $('#reload-config').addEventListener('click', () => { api('/api/config').then((r) => { state.config = r.config; viewConfig(); }); });
}

const DEFAULTS = { writers: [] };

function effectiveUrl(base) {
  const b = (base || '').trim().replace(/\/+$/, '');
  if (!b) return '（未填 Base URL）';
  if (b.endsWith('/chat/completions')) return b;
  return b + '/chat/completions';
}

function providerMap() {
  const out = {};
  $$('#providers .provider-card').forEach((card) => {
    out[card.dataset.pid] = {
      label: $('.p-label', card).value.trim() || card.dataset.pid,
      base: $('.p-base', card).value.trim(),
      models: $('.p-models', card).value.split(/[,，\n]/).map((x) => x.trim()).filter(Boolean),
    };
  });
  return out;
}

function collectIssues() {
  const prov = providerMap();
  const issues = [];
  const check = (where, pid, model, setter) => {
    const p = prov[pid];
    if (!p) { issues.push({ where, msg: `「${where}」指向的提供方不存在` }); return; }
    const models = (p.models || []).map((m) => m.toLowerCase());
    if (!model) {
      if (!models.length) issues.push({ where, msg: `「${where}」没填模型 ID，且 ${p.label} 也没有可用模型`, setter });
      return;
    }
    if (models.length && !models.includes(model.toLowerCase())) {
      issues.push({ where, msg: `「${where}」用的 ${model} 不在 ${p.label} 的模型列表（${p.models.join(' / ')}）里`, setter });
    }
  };
  ['classify', 'brief', 'destyle', 'judge'].forEach((k) => {
    const el = $(`[data-role="${k}"]`);
    if (!el) return;
    check(k, $('.r-provider', el).value, $('.r-model', el).value.trim(), () => {
      const pid = $('.r-provider', el).value;
      const m = (prov[pid] && prov[pid].models[0]) || '';
      $('.r-model', el).value = m;
    });
  });
  $$('#writers .writer-row[data-writer]').forEach((row) => {
    const pid = $('.w-provider', row).value;
    const model = $('.w-model', row).value.trim();
    check('写手 ' + pid, pid, model, () => {
      $('.w-model', row).value = (prov[pid] && prov[pid].models[0]) || '';
    });
  });
  return issues;
}

function renderConfigWarnings() {
  const box = $('#cfg-warn');
  if (!box) return;
  const issues = collectIssues();
  // 标红
  $$('#providers .provider-card .p-models, [data-role] .r-model, #writers .w-model').forEach((i) => i.classList.remove('invalid'));
  if (!issues.length) { box.innerHTML = ''; return; }
  box.innerHTML = `<div class="callout warn">
    <b>有 ${issues.length} 处模型 ID 对不上</b>（照这个跑，调用会直接报 model not found）
    <div class="small" style="margin:6px 0">${issues.slice(0, 6).map((i) => '· ' + esc(i.msg)).join('<br>')}</div>
    <button class="btn btn-sm" id="fix-models">一键对齐到各提供方的第一个模型</button>
  </div>`;
  const btn = $('#fix-models');
  if (btn) btn.onclick = () => {
    issues.forEach((i) => i.setter && i.setter());
    renderConfigWarnings();
    toast('已对齐，记得保存', 'ok');
  };
}

function hintFor(err) {
  const e = (err || '').toLowerCase();
  if (/base url 为空|base url 不合法/.test(err)) return '这里要填到 /v1 或 /api/v3 这一级，程序会自己拼 /chat/completions。';
  if (/unknown url type/.test(e)) return '地址没填或格式不对（多半是没保存就点了测试）。';
  if (/authentication|invalid.*key|api key.*(invalid|missing)|401|unauthorized|permission/.test(e)) {
    return 'Key 收到了但被拒。最常见原因：Key 是「中转站/Token Plan」发的，而 Base URL 还指向官方地址——两边必须配套。';
  }
  if (/404|not found/.test(e)) return '路径或模型 ID 不对：Base URL 别带 /chat/completions，模型 ID 用服务商给的准确名字。';
  if (/model/.test(e) && /(exist|found|support)/.test(e)) return '模型 ID 不被这个地址接受，检查是否拼错或该端点不支持此模型。';
  if (/timeout|timed out/.test(e)) return '网络超时：检查是否需要代理，或该中转站响应慢。';
  return '把上面这段原始报错发我，多半是 Base URL 与 Key 的配套关系问题。';
}

function providerCard(p) {
  return `
  <div class="provider-card" data-pid="${esc(p.id)}">
    <div class="head">
      <input type="text" autocomplete="off" class="p-label pname" value="${esc(p.label || '')}" style="border:none;padding:2px 4px;width:180px;font-weight:620">
      <span class="tag">${esc(p.id)}</span>
      ${p.has_key ? '<span class="pill pill-ok">有 Key</span>' : '<span class="pill pill-warn">无 Key</span>'}
      <span class="grow"></span>
      <label class="check"><input type="checkbox" class="p-enabled" ${p.enabled ? 'checked' : ''}>启用</label>
      <button class="btn btn-sm p-test">测试连接</button>
      <button class="btn btn-sm btn-danger p-del">移除</button>
    </div>
    <div class="grid grid-2">
      <label class="field"><span class="field-label">Base URL</span>
        <input type="text" autocomplete="off" spellcheck="false" class="p-base" value="${esc(p.base_url || '')}" placeholder="以 /v1 或 /api/v3 结尾"></label>
      <label class="field"><span class="field-label">API Key ${p.api_key_masked ? '· 当前 ' + esc(p.api_key_masked) : ''}</span>
        <input type="password" autocomplete="new-password" class="p-key" value="" placeholder="${p.has_key ? '已保存，留空表示不修改' : '粘贴 API Key'}"></label>
    </div>
    <label class="field"><span class="field-label">模型 ID（逗号分隔，第一个为默认）</span>
      <input type="text" autocomplete="off" spellcheck="false" class="p-models" value="${esc((p.models || []).join(', '))}" placeholder="模型 ID，多个用逗号分隔"></label>
    <div class="row" style="gap:18px">
      <label class="check"><input type="checkbox" class="p-json" ${p.json_mode ? 'checked' : ''}>支持 response_format=json_object</label>
      <label class="check"><input type="checkbox" class="p-nothink" ${p.disable_thinking !== false ? 'checked' : ''}>关闭 Thinking（thinking.type=disabled）</label>
      <label class="check"><input type="checkbox" class="p-reasoning" ${p.reasoning ? 'checked' : ''}>推理模型（仅未关闭 Thinking 时生效）</label>
    </div>
    <div class="hint">关闭 Thinking 后请求体里会带 <code>"thinking":{"type":"disabled"}</code>，速度会快很多，抽取/改写质量基本不受影响。</div>
    <div class="small muted" style="margin-top:6px">实际请求地址：<span class="mono p-effective">${esc(effectiveUrl(p.base_url))}</span></div>
    <div class="test-msg"></div>
  </div>`;
}

function writerRow(w, i) {
  const styleOpts = STYLE_ORDER.map((k) => `<option value="${k}" ${w.style === k ? 'selected' : ''}>${esc(state.styles[k] || k)}</option>`).join('');
  const voiceOpts = Object.entries(VOICES).map(([k, v]) => `<option value="${k}" ${w.voice === k ? 'selected' : ''}>${esc(v)}</option>`).join('');
  return `
  <div class="writer-row" data-writer="${esc(w.id)}">
    ${providerSelect('w-provider', w.provider)}
    <input type="text" autocomplete="off" spellcheck="false" class="w-model" value="${esc(w.model || '')}" placeholder="模型 ID">
    <select class="w-style">${styleOpts}</select>
    <select class="w-voice">${voiceOpts}</select>
    <input type="number" class="w-temp" step="0.1" min="0" max="2" value="${w.temperature ?? 0.8}">
    <label class="check"><input type="checkbox" class="w-enabled" ${w.enabled ? 'checked' : ''}></label>
    <button class="btn btn-sm btn-ghost w-del" title="删除">✕</button>
  </div>`;
}

function collectProviders() {
  return $$('#providers .provider-card').map((card) => ({
    id: card.dataset.pid,
    label: $('.p-label', card).value.trim() || card.dataset.pid,
    base_url: $('.p-base', card).value.trim(),
    api_key: $('.p-key', card).value.trim(),
    models: $('.p-models', card).value.split(/[,，\n]/).map((x) => x.trim()).filter(Boolean),
    json_mode: $('.p-json', card).checked,
    reasoning: $('.p-reasoning', card).checked,
    disable_thinking: $('.p-nothink', card).checked,
    enabled: $('.p-enabled', card).checked,
  }));
}

function collectWriters() {
  return $$('#writers .writer-row[data-writer]').map((row) => ({
    id: row.dataset.writer,
    provider: $('.w-provider', row).value,
    model: $('.w-model', row).value.trim(),
    style: $('.w-style', row).value,
    voice: $('.w-voice', row).value,
    temperature: parseFloat($('.w-temp', row).value || '0.8'),
    enabled: $('.w-enabled', row).checked,
  }));
}

async function saveConfigFromForm() {
  const cfg = {
    providers: collectProviders(),
    roles: {},
    judge_policy: $('#judge-policy').value,
    writers: collectWriters(),
    settings: {
      min_year: parseInt($('#s-minyear').value || '2023', 10),
      concurrency: parseInt($('#s-conc').value || '3', 10),
      drafts_per_article: parseInt($('#s-drafts').value || '2', 10),
      structure_override_rate: parseFloat($('#s-override').value || '0.12'),
      request_timeout: parseInt($('#s-timeout').value || '300', 10),
      max_tokens_cap: parseInt($('#s-tokcap').value || '32000', 10),
      auto_repair_rounds: parseInt($('#s-repair').value || '0', 10),
    },
    export: {
      system_prompt: $('#e-system').value,
      instructions: $('#e-instr').value.split('\n').map((x) => x.trim()).filter(Boolean),
      only_accepted: $('#e-accepted').checked,
      strip_promo: $('#e-strip').checked,
      segment: $('#e-seg').checked,
      segment_min: parseInt($('#e-segmin').value || '2000', 10),
      segment_max: parseInt($('#e-segmax').value || '3000', 10),
    },
  };
  ['classify', 'brief', 'destyle', 'judge'].forEach((k) => {
    const el = $(`[data-role="${k}"]`);
    cfg.roles[k] = {
      provider: $('.r-provider', el).value,
      model: $('.r-model', el).value.trim(),
      temperature: parseFloat($('.r-temp', el).value || '0.2'),
    };
  });
  // 保留未在表单里的自定义字段
  cfg.auto_repair = state.config.auto_repair !== false;
  try {
    const r = await api('/api/config', { config: cfg });
    state.config = r.config;
    toast('配置已保存', 'ok');
    loadArticles(true);
  } catch (e) { toast(e.message, 'bad'); }
}

/* ================= 启动 ================= */

async function boot() {
  try {
    const r = await api('/api/config');
    state.config = r.config;
    state.styles = r.styles || {};
    DEFAULTS.writers = JSON.parse(JSON.stringify(state.config.writers || []));
    const h = await api('/api/health');
    $('#health').className = 'pill pill-ok';
    $('#health').textContent = '本地已连接';
  } catch (e) {
    $('#health').className = 'pill pill-bad';
    $('#health').textContent = '连接失败';
    view.innerHTML = `<div class="empty">连不上本地服务：${esc(e.message)}</div>`;
    return;
  }
  try { await loadArticles(false); } catch (e) { /* 首次扫描可能慢，忽略 */ }
  window.addEventListener('hashchange', route);
  route();
}

boot();
