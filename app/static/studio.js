/* 公众号文风微调 · 产品层（原理 / 导入 / 清洗 / 训练）
   依赖 app.js 的全局工具：$ $$ api esc toast badge fmt view */

const STUDIO = (() => {
  let statusCache = null;
  let trainPoll = null;
  let globalPoll = null;
  let panelTask = null;

  /* ================= 任务浮层（全局） ================= */

  function panelShow(title) {
    $('#sg-panel-title').textContent = title || '任务进度';
    $('#sg-panel').classList.remove('hidden');
  }
  function panelHide() { $('#sg-panel').classList.add('hidden'); panelTask = null; }
  function panelLog(lines) {
    const el = $('#sg-panel-log');
    el.textContent = lines.join('\n');
    el.scrollTop = el.scrollHeight;
  }

  function watchTask(kind, body, title) {
    return api('/api/studio/' + kind, body).then((r) => {
      panelShow(title);
      const tid = r.task_id;
      panelTask = tid;
      const timer = setInterval(async () => {
        try {
          const t = (await api('/api/studio/task/' + tid)).task;
          panelLog(t.logs);
          if (['done', 'failed'].includes(t.status)) {
            clearInterval(timer);
            panelTask = null;
            toast(t.status === 'done' ? '完成' : (t.error || '任务失败'), t.status === 'done' ? '' : 'bad');
            statusCache = null;
            const cur = (location.hash.replace(/^#\/?/, '') || 'home').split('/')[0];
            if (cur === 'home' || cur === 'import') location.reload();
          }
        } catch (e) { clearInterval(timer); }
      }, 900);
      return r;
    });
  }

  /* 顶栏指示灯：有任务在跑就能点开看，任何页面都不会丢 */
  function startGlobalWatcher() {
    if (globalPoll) return;
    globalPoll = setInterval(async () => {
      try {
        const r = await api('/api/studio/jobs');
        const running = [...(r.tasks || []), ...(r.train || [])]
          .filter((x) => x.status === 'running');
        const pill = $('#sg-jobpill');
        if (!running.length) { pill.classList.add('hidden'); return; }
        pill.classList.remove('hidden');
        $('#sg-jobtext').textContent = running.length > 1 ? `${running.length} 个任务运行中` : '任务运行中';
        $('#sg-jobdot').classList.add('run');
        if (!$('#sg-panel').classList.contains('hidden') && panelTask) {
          const t = running.find((x) => x.id === panelTask);
          if (t) panelLog(t.logs && t.logs.length ? t.logs : ['…']);
        } else if (!panelTask) {
          const t = running[running.length - 1];
          panelShow('任务进度');
          panelTask = t.id;
        }
      } catch (e) { /* 忽略 */ }
    }, 2500);
  }

  /* ================= 工具 ================= */

  async function getStatus(force) {
    if (statusCache && !force) return statusCache;
    statusCache = await api('/api/studio/status');
    return statusCache;
  }

  function head(title, desc, right = '') {
    return `<div class="page-head"><div><div class="page-title">${esc(title)}</div>
      <div class="page-desc">${desc}</div></div><div class="row">${right}</div></div>`;
  }

  function tabbar(items, active) {
    return `<div class="row" style="gap:6px;margin-bottom:18px">` + items.map((it) =>
      `<a class="btn btn-sm ${it.key === active ? 'btn-primary' : ''}" data-itab="${it.key}" href="javascript:;">${esc(it.label)}</a>`
    ).join('') + `</div>`;
  }

  /* ================= 原理（首页） ================= */

  async function viewHome() {
    view.innerHTML = `
      <section class="sg-hero">
        <div class="sg-eyebrow">local · mlx · lora</div>
        <h1>让你的模型，学会你的写法</h1>
        <p>通用模型能写「一篇文章」，写不出「<b>你</b>的一篇文章」。区别不在内容，在声音。
           这个工具把你自己写的文章拆成训练数据，在你的 Mac 上训一个会说你的话的 LoRA。</p>
        <div class="sg-start">
          <a class="btn btn-primary" id="sg-start" style="height:38px;padding:0 20px">开始</a>
          <span class="hint" id="sg-start-hint">准备文章 →</span>
        </div>
      </section>

      <div class="sg-tri">
        <div><h3>内容</h3><p>讲了什么。训练时两边保持一致，交给「事实清单」承住。</p></div>
        <div><h3>结构</h3><p>怎么组织。小标题、段落顺序，同样两边对齐。</p></div>
        <div><h3>声音</h3><p>谁在说话。<b>只有这一项是变量</b>，模型学的就是它。</p></div>
      </div>

      <h3 style="font-size:14.5px;margin:34px 0 4px">怎么造出这种数据</h3>
      <p class="small muted" style="margin:0 0 6px;max-width:720px">
        直接拿文章训练不行——模型会记住「这个话题下写这些内容」。所以要反向构造：
        先把你文章里的「声音」剥掉，交给通用模型重写成通稿，再让模型学「通稿 → 你的原文」。</p>
      <div class="sg-flow">
        <div><div class="n">01</div><div class="t">你的原文</div><div class="d">476 篇公众号文章</div></div>
        <div><div class="n">02</div><div class="t">提取事实</div><div class="d">拆成结构化清单，不带走情绪和句式</div></div>
        <div><div class="n">03</div><div class="t">去风格</div><div class="d">把「我觉得」改成客观陈述，反复打补丁</div></div>
        <div><div class="n">04</div><div class="t">造通用稿</div><div class="d">2–3 家模型各写一版，挑最像通稿的</div></div>
        <div><div class="n">05</div><div class="t">配对训练</div><div class="d">通稿当输入，原文当输出</div></div>
      </div>

      <h3 style="font-size:14.5px;margin:34px 0 4px">设备要求</h3>
      <div class="sg-spec">
        <table>
          <tr><th>模型</th><th>内存占用</th><th>24GB Mac</th><th>说明</th></tr>
          <tr><td>Qwen3-0.6B·4bit</td><td>~2GB</td><td>轻松</td><td>只用来验证流程通不通</td></tr>
          <tr><td>Qwen3-1.7B·4bit</td><td>~4GB</td><td>轻松</td><td>能跑，文风偏平</td></tr>
          <tr><td><b>Qwen3-4B·4bit</b></td><td><b>~8GB</b></td><td><b>推荐</b></td><td>效果够用，约 1 小时</td></tr>
          <tr><td>Qwen3-8B·4bit</td><td>~14GB</td><td>贴上限</td><td>必须开梯度检查点</td></tr>
        </table>
      </div>
      <p class="small muted" style="margin-top:-8px">
        训练只支持 macOS + Apple Silicon（MLX）。其他系统可以做语料，不能训练。
        语料生成需要任意一家 OpenAI 兼容的模型 API。</p>

      <h3 style="font-size:14.5px;margin:34px 0 4px">四个可量化的验收指标</h3>
      <p class="small muted" style="margin:0 0 12px;max-width:720px">
        训完读一遍「好像有点像？」是最容易走错路的地方。所以每一步都给数字：</p>
      <div class="sg-spec">
        <table>
          <tr><th>指标</th><th>怎么算</th><th>坏了说明什么</th></tr>
          <tr><td>长度比</td><td>生成字数 ÷ 原文字数</td><td>稳定低于 0.8 = 模型在偷懒压缩</td></tr>
          <tr><td>重合度</td><td>与输入的最长公共子串</td><td>超过 25 字 = 在照抄输入</td></tr>
          <tr><td>第一人称密度</td><td>「我」出现次数</td><td>掉到 2 以下 = 写成了通稿</td></tr>
          <tr><td>小标题数</td><td>Markdown 标题行数</td><td>明显多于原文 = 退回通用排版</td></tr>
        </table>
      </div>`;

    $('#sg-start').addEventListener('click', async () => {
      try {
        const p = await api('/api/studio/plan');
        const map = { import: 'import', clean: 'clean', corpus: 'articles', train: 'train' };
        const hint = { import: '从抓取文章开始', clean: '去清洗语料', corpus: '去造语料', train: '去本地训练' };
        location.hash = '#/' + (map[p.next] || 'import');
        toast(hint[p.next] || '继续');
      } catch (e) { location.hash = '#/import'; }
    });

    try {
      const p = await api('/api/studio/plan');
      const hint = { import: '下一步：准备文章', clean: '下一步：清洗', corpus: '下一步：造语料', train: '下一步：训练' };
      $('#sg-start-hint').textContent = hint[p.next] || '';
    } catch (e) { /* 忽略 */ }
  }

  /* ================= 导入 ================= */

  async function viewImport() {
    view.innerHTML = head('导入文章', '三种来源，任选一种。图片自动转占位符，链接自动去 URL。') + `
      <div class="row" style="gap:6px;margin-bottom:16px">
        <a class="btn btn-sm btn-primary" data-tab="acct" href="javascript:;">一键导出公众号</a>
        <a class="btn btn-sm" data-tab="urls" href="javascript:;">粘贴链接</a>
        <a class="btn btn-sm" data-tab="path" href="javascript:;">已有文章 · 给路径</a>
      </div>

      <div id="sg-tab-acct">
        <div class="card" style="padding:18px 20px">
          <div class="card-title">自动导出</div>
          <div class="card-desc" style="margin:4px 0 14px">
            借公众号后台的列表接口把历史文章一次抓完。需要你在浏览器里登录 mp.weixin.qq.com，
            然后复制两样东西：
          </div>
          <div class="grid grid-2" style="gap:14px">
            <div>
              <div class="field-label">Cookie</div>
              <textarea class="sg-ta" id="sg-cookie" style="min-height:88px" placeholder="F12 → Network → 任意 cgi-bin 请求 → Request Headers → Cookie；（macOS 也可打开「开发者工具 → 应用 → Cookie」）"></textarea>
            </div>
            <div>
              <div class="field-label">Token</div>
              <input class="sg-input" id="sg-token" placeholder="后台地址栏里的 token=xxxx">
              <div class="field-label" style="margin-top:14px">公众号名称（用来查账号 ID）</div>
              <div class="row">
                <input class="sg-input" id="sg-acct-name" placeholder="例如：我的公众号" style="flex:1">
                <button class="btn" id="sg-search">查账号</button>
              </div>
              <div id="sg-acct-list" class="small" style="margin-top:8px"></div>
            </div>
          </div>
          <div class="row" style="margin-top:14px">
            <div class="field-label" style="margin:0">最多导出</div>
            <input class="sg-input" id="sg-limit" type="number" placeholder="留空=全部" style="width:130px">
            <button class="btn btn-primary" id="sg-export-all" disabled>开始导出全部文章</button>
            <span class="small muted" id="sg-acct-hint"></span>
          </div>
        </div>
      </div>

      <div id="sg-tab-urls" class="hidden">
        <div class="card" style="padding:18px 20px">
          <div class="card-title">粘贴文章链接</div>
          <div class="card-desc" style="margin:4px 0 12px">每行一个 mp.weixin.qq.com/s/... 链接。只抓公开文章页，不需要登录。</div>
          <textarea class="sg-ta" id="sg-urls" placeholder="https://mp.weixin.qq.com/s/xxxxxxxx"></textarea>
          <div class="row" style="margin-top:12px">
            <button class="btn btn-primary" id="sg-import-run">开始抓取</button>
            <span class="small muted">每篇间隔 0.8 秒，避免被限流</span>
          </div>
        </div>
      </div>

      <div id="sg-tab-path" class="hidden">
        <div class="card" style="padding:18px 20px">
          <div class="card-title">已有文章，直接给路径</div>
          <div class="card-desc" style="margin:4px 0 12px">
            填一个目录（会递归找里面的 .md）或几个 .md 文件的绝对路径，每行一个。
            目前只支持 Markdown——其它格式请先自己转一下。
          </div>
          <textarea class="sg-ta" id="sg-paths" placeholder="/Users/you/Documents/我的文章&#10;/Users/you/Downloads/某篇.md"></textarea>
          <div class="row" style="margin-top:12px">
            <button class="btn btn-primary" id="sg-import-path">导入这些文件</button>
            <span class="small muted">没有 frontmatter 的会自动补一份</span>
          </div>
        </div>
      </div>`;

    $$('[data-tab]').forEach((b) => b.addEventListener('click', () => {
      $$('[data-tab]').forEach((x) => x.classList.remove('btn-primary'));
      b.classList.add('btn-primary');
      ['acct', 'urls', 'path'].forEach((k) => $('#sg-tab-' + k).classList.toggle('hidden', k !== b.dataset.tab));
    }));

    let picked = null;

    $('#sg-search').addEventListener('click', async () => {
      const name = $('#sg-acct-name').value.trim();
      if (!name) return toast('先填公众号名称', 'bad');
      try {
        const r = await api('/api/studio/wxmp/search', {
          name, token: $('#sg-token').value.trim(), cookie: $('#sg-cookie').value.trim(),
        });
        if (!r.ok) return toast(r.error, 'bad');
        if (!r.accounts.length) { $('#sg-acct-list').textContent = '没搜到，检查名称或登录态'; return; }
        $('#sg-acct-list').innerHTML = r.accounts.map((a, i) =>
          `<label class="check" style="display:block;margin:4px 0">
             <input type="radio" name="sg-acct" value="${esc(a.fakeid)}" ${i === 0 ? 'checked' : ''}>
             ${esc(a.nickname)} <span class="muted mono">${esc(a.fakeid)}</span></label>`).join('');
        picked = r.accounts[0].fakeid;
        $$('input[name=sg-acct]').forEach((el) => el.addEventListener('change', () => { picked = el.value; }));
        $('#sg-export-all').disabled = false;
        $('#sg-acct-hint').textContent = '找到 ' + r.accounts.length + ' 个';
      } catch (e) { toast(e.message, 'bad'); }
    });

    $('#sg-export-all').addEventListener('click', () => {
      if (!picked) return toast('先查账号', 'bad');
      watchTask('wxmp/export', {
        cookie: $('#sg-cookie').value.trim(),
        token: $('#sg-token').value.trim(),
        fakeid: picked,
        limit: +($('#sg-limit').value || 0),
      }, '一键导出公众号');
    });

    $('#sg-import-run').addEventListener('click', () => {
      const urls = $('#sg-urls').value.trim();
      if (!urls) return toast('先粘贴链接', 'bad');
      watchTask('import', { urls }, '抓取文章');
    });

    $('#sg-import-path').addEventListener('click', () => {
      const paths = $('#sg-paths').value.trim();
      if (!paths) return toast('先填路径', 'bad');
      watchTask('import/path', { paths }, '导入本地文章');
    });
  }

  /* ================= 清洗 ================= */

  let featRules = [{ mode: 'remove', text: '', line: true }];

  async function viewClean() {
    view.innerHTML = head('清洗', '去掉固定导流语、图床链接这类噪音。改动前会自动备份。') + `
      <div class="card" style="padding:18px 20px;margin-bottom:16px">
        <div class="card-title">结构化处理</div>
        <div class="card-desc" style="margin:4px 0 12px">这两项一般都要开</div>
        <label class="check"><input type="checkbox" id="sg-r-img" checked> 图片 → 配图占位符
          <span class="small muted">（图床 URL 会失效，对学文风也没价值）</span></label>
        <label class="check" style="margin-top:8px;display:block"><input type="checkbox" id="sg-r-link" checked> 超链接 → 只留文字
          <span class="small muted">（删掉 URL 和裸链接）</span></label>
      </div>

      <div class="card" style="padding:18px 20px;margin-bottom:16px">
        <div class="row"><div class="grow">
          <div class="card-title">按特征删除</div>
          <div class="card-desc" style="margin-top:4px">给你文章里固定出现的句子。可以加多条，按顺序执行。</div>
        </div>
          <button class="btn btn-sm" id="sg-add-rule">+ 加一条规则</button>
        </div>
        <div id="sg-rules" style="margin-top:14px"></div>
        <div class="hint" style="margin-top:10px">
          安全阀：某条规则让一篇正文减少一半以上，这篇会被跳过并单独提示——不会把整批文章洗没。
        </div>
      </div>

      <div class="card" style="padding:18px 20px">
        <div class="row">
          <button class="btn" id="sg-preview">预览效果</button>
          <button class="btn btn-primary" id="sg-apply">开始清洗</button>
          <button class="btn btn-ghost" id="sg-restore">从备份还原</button>
          <span class="grow"></span>
          <span class="small muted" id="sg-clean-count"></span>
        </div>
        <div id="sg-preview-box" style="margin-top:16px"></div>
      </div>`;

    const renderRules = () => {
      $('#sg-rules').innerHTML = featRules.map((r, i) => `
        <div class="sg-rule" style="background:var(--surface-2);border:1px solid var(--line-2);border-radius:6px;padding:12px;margin-bottom:10px">
          <div class="row">
            <span class="mono small muted" style="width:22px">${i + 1}</span>
            <select class="sg-input" data-r-mode="${i}" style="max-width:210px">
              <option value="remove" ${r.mode === 'remove' ? 'selected' : ''}>删除匹配到的全部内容</option>
              <option value="before" ${r.mode === 'before' ? 'selected' : ''}>删除这个特征之前的所有内容</option>
              <option value="after" ${r.mode === 'after' ? 'selected' : ''}>删除这个特征之后的所有内容</option>
            </select>
            <input class="sg-input" data-r-text="${i}" placeholder="特征文本，例如：关注+星标" value="${esc(r.text)}" style="flex:1;min-width:200px">
            <select class="sg-input" data-r-line="${i}" style="max-width:150px">
              <option value="1" ${r.line ? 'selected' : ''}>整行删除</option>
              <option value="0" ${!r.line ? 'selected' : ''}>只删这段文字</option>
            </select>
            <button class="btn btn-ghost btn-sm" data-r-del="${i}">删除</button>
          </div>
        </div>`).join('');

      $$('[data-r-mode]').forEach((el) => el.addEventListener('change', () => { featRules[+el.dataset.rMode].mode = el.value; }));
      $$('[data-r-text]').forEach((el) => el.addEventListener('input', () => { featRules[+el.dataset.rText].text = el.value; }));
      $$('[data-r-line]').forEach((el) => el.addEventListener('change', () => { featRules[+el.dataset.rLine].line = el.value === '1'; }));
      $$('[data-r-del]').forEach((el) => el.addEventListener('click', () => {
        featRules.splice(+el.dataset.rDel, 1);
        if (!featRules.length) featRules.push({ mode: 'remove', text: '', line: true });
        renderRules();
      }));
    };
    renderRules();

    $('#sg-add-rule').addEventListener('click', () => {
      featRules.push({ mode: 'remove', text: '', line: true });
      renderRules();
    });

    const collect = () => {
      const rules = [{ id: 'img', enabled: $('#sg-r-img').checked },
                     { id: 'link', enabled: $('#sg-r-link').checked }];
      featRules.forEach((r) => {
        if (r.text.trim()) rules.push({ id: 'feat', enabled: true, mode: r.mode, text: r.text.trim(), line: r.line });
      });
      return rules;
    };

    try {
      const s = await getStatus();
      $('#sg-clean-count').textContent = `共 ${s.corpus.count} 篇文章`;
    } catch (e) { /* 忽略 */ }

    $('#sg-preview').addEventListener('click', async () => {
      try {
        const r = await api('/api/studio/cleanup/preview', { rules: collect(), limit: 2 });
        $('#sg-preview-box').innerHTML = r.preview.map((p) => `
          <div style="margin-bottom:16px">
            <div class="row" style="margin-bottom:6px"><b class="small">${esc(p.file)}</b>
              ${p.changed ? badge('会改动', 'ok') : badge('不变', 'line')}
              ${p.blocked ? badge('安全阀拦截', 'bad') : ''}
              <span class="small muted mono">${esc(JSON.stringify(p.hits))}</span></div>
            <div class="sg-diff" style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
              <div><div class="small muted" style="margin-bottom:4px">清洗前</div>
                <pre style="background:var(--surface-2);border:1px solid var(--line-2);border-radius:6px;padding:10px;font-size:12px;max-height:220px;overflow:auto;white-space:pre-wrap;margin:0">${esc(p.before)}</pre></div>
              <div><div class="small muted" style="margin-bottom:4px">清洗后</div>
                <pre style="background:var(--surface-2);border:1px solid var(--line-2);border-radius:6px;padding:10px;font-size:12px;max-height:220px;overflow:auto;white-space:pre-wrap;margin:0">${esc(p.after)}</pre></div>
            </div>
          </div>`).join('');
      } catch (e) { toast(e.message, 'bad'); }
    });

    $('#sg-apply').addEventListener('click', () => watchTask('cleanup/apply', { rules: collect() }, '清洗文章'));
    $('#sg-restore').addEventListener('click', () => watchTask('cleanup/restore', {}, '从备份还原'));
  }

  /* ================= 训练 ================= */

  async function viewTrain() {
    let s;
    try { s = await getStatus(true); } catch (e) { view.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    const mlxOk = s.mlx && s.mlx.ok;
    const models = s.models || [];
    const ds = s.dataset || {};

    view.innerHTML = head('本地训练', 'MLX + LoRA。数据不出本机，4B 模型约 1 小时。') + `
      <div class="card" style="padding:18px 20px;margin-bottom:16px">
        <div class="row"><div class="grow">
          <div class="card-title">运行环境</div>
          <div class="card-desc" style="margin-top:4px">${mlxOk
            ? 'MLX ' + esc(s.mlx.mlx || '') + ' 已就绪'
            : '还没安装 MLX（首次约 2 分钟，走国内镜像）'}</div>
        </div>
        ${mlxOk ? badge('就绪', 'ok') : '<button class="btn btn-primary" id="sg-install">一键安装</button>'}</div>
      </div>

      <div class="card" style="padding:18px 20px;margin-bottom:16px">
        <div class="card-title">训练数据</div>
        <div class="card-desc" style="margin-top:4px">${ds.ready
          ? `train ${ds.train} 条 / valid ${ds.valid} 条`
          : '还没生成。先去「语料」页导出，再回来点一下。'}</div>
        <div class="row" style="margin-top:12px">
          <select class="sg-input" id="sg-export" style="max-width:420px">
            ${(s.exports || []).map((f) => `<option value="${esc(f.name)}">${esc(f.name)}（${f.mb}MB）</option>`).join('') || '<option value="">没有导出文件</option>'}
          </select>
          <button class="btn" id="sg-build-ds">生成数据集</button>
          <span class="small muted" id="sg-ds-msg"></span>
        </div>
      </div>

      <div class="card" style="padding:18px 20px;margin-bottom:16px">
        <div class="card-title">训练参数</div>
        <div class="card-desc" style="margin-top:4px">第一次建议用 0.6B 验证流程，再换 4B。上下文 3072 能覆盖大部分稿件。</div>
        <div class="grid grid-3" style="gap:12px;margin-top:14px">
          <div><div class="field-label">模型</div>
            <select class="sg-input" id="sg-model">
              ${models.map((m, i) => `<option value="${esc(m.id)}" ${i === 2 ? 'selected' : ''}>${esc(m.label)}</option>`).join('')}
            </select>
            <div class="small muted" id="sg-model-note" style="margin-top:4px"></div></div>
          <div><div class="field-label">迭代步数</div><input class="sg-input" id="sg-iters" type="number" value="300"></div>
          <div><div class="field-label">上下文长度</div><input class="sg-input" id="sg-seq" type="number" value="3072"></div>
          <div><div class="field-label">batch size</div><input class="sg-input" id="sg-batch" type="number" value="1"></div>
          <div><div class="field-label">学习率</div><input class="sg-input" id="sg-lr" value="1e-5"></div>
          <div><div class="field-label">Adapter 名称</div><input class="sg-input" id="sg-adapter" placeholder="留空自动命名"></div>
        </div>
        <div class="row" style="margin-top:12px">
          <label class="check"><input type="checkbox" id="sg-gc" checked> 梯度检查点（省内存）</label>
          <label class="check"><input type="checkbox" id="sg-layers-on"> 只训后</label>
          <input class="sg-input" id="sg-layers" type="number" value="16" style="width:78px">
          <span class="small">层</span>
          <span class="grow"></span>
          <span class="field-label" style="margin:0">模型源</span>
          <input class="sg-input" id="sg-hf" value="${esc(s.hf_endpoint || '')}" style="max-width:260px">
        </div>
        <div class="row" style="margin-top:16px">
          <button class="btn btn-primary" id="sg-train">开始训练</button>
          <button class="btn btn-ghost" id="sg-stop" disabled>停止</button>
          <span class="grow"></span>
          <span class="small muted" id="sg-train-msg"></span>
        </div>
        <div class="progress" style="margin:12px 0"><div id="sg-bar" style="width:0"></div></div>
      </div>

      <div class="card" style="padding:18px 20px">
        <div class="card-title">已训练的 Adapter</div>
        <div class="card-desc" style="margin-top:4px">产物在 adapters/ 目录</div>
        <div id="sg-adapters" style="margin-top:10px"></div>
      </div>`;

    const noteFor = () => {
      const m = models.find((x) => x.id === $('#sg-model').value);
      $('#sg-model-note').textContent = m ? `${m.note} · ${m.size}` : '';
    };
    $('#sg-model').addEventListener('change', noteFor);
    noteFor();

    if (!mlxOk) {
      $('#sg-install').addEventListener('click', () => {
        panelShow('安装 MLX');
        api('/api/studio/setup/install', {}).then((r) => {
          const timer = setInterval(async () => {
            const t = (await api('/api/studio/task/' + r.task_id)).task;
            panelLog(t.logs);
            if (['done', 'failed'].includes(t.status)) {
              clearInterval(timer);
              if (t.status === 'done') { toast('安装完成'); viewTrain(); }
              else toast(t.error || '安装失败', 'bad');
            }
          }, 1200);
        });
      });
    }

    $('#sg-build-ds').addEventListener('click', async () => {
      try {
        const r = await api('/api/studio/dataset', { export: $('#sg-export').value });
        $('#sg-ds-msg').textContent = `train ${r.dataset.train} / valid ${r.dataset.valid}`;
        toast('数据集已生成');
      } catch (e) { toast(e.message, 'bad'); }
    });

    const renderAdapters = (list) => {
      $('#sg-adapters').innerHTML = list.length ? list.map((a) => `
        <div class="row" style="padding:8px 0;border-bottom:1px solid var(--line-2)">
          <b class="mono small">${esc(a.name)}</b>${badge(a.mb + 'MB', 'line')}
          ${a.has_weights ? badge('有权重', 'ok') : badge('空目录', 'warn')}
          <span class="small muted">${esc(a.mtime)}</span><span class="grow"></span>
          <button class="btn btn-ghost btn-sm sg-del" data-name="${esc(a.name)}">删除</button>
        </div>`).join('') : '<div class="empty">还没有</div>';
      $$('.sg-del').forEach((b) => b.addEventListener('click', async () => {
        await api('/api/studio/adapters/delete', { name: b.dataset.name });
        viewTrain();
      }));
    };
    renderAdapters(s.adapters || []);

    let curJob = null;
    $('#sg-train').addEventListener('click', async () => {
      const params = {
        model: $('#sg-model').value, iters: +$('#sg-iters').value,
        max_seq_length: +$('#sg-seq').value, batch_size: +$('#sg-batch').value,
        learning_rate: $('#sg-lr').value, adapter: $('#sg-adapter').value.trim() || undefined,
        grad_checkpoint: $('#sg-gc').checked,
        num_layers: $('#sg-layers-on').checked ? +$('#sg-layers').value : 0,
      };
      try {
        const r = await api('/api/studio/train/start', { params, hf_endpoint: $('#sg-hf').value.trim() });
        curJob = r.job.id; $('#sg-stop').disabled = false; panelShow('训练 ' + curJob); pollJob(curJob);
      } catch (e) {
        if (/内存/.test(e.message) && confirm(e.message + '\n\n仍然继续？')) {
          const r = await api('/api/studio/train/start', { params, hf_endpoint: $('#sg-hf').value.trim(), force: true });
          curJob = r.job.id; $('#sg-stop').disabled = false; pollJob(curJob);
        } else toast(e.message, 'bad');
      }
    });

    $('#sg-stop').addEventListener('click', async () => {
      if (!curJob) return;
      await api('/api/studio/train/stop', { id: curJob });
      toast('已停止');
    });

    function pollJob(id) {
      clearInterval(trainPoll);
      trainPoll = setInterval(async () => {
        try {
          const j = (await api('/api/studio/train/job', { id })).job;
          panelLog(j.logs);
          const p = j.iters_total ? Math.min(100, (j.iters_done / j.iters_total) * 100) : 0;
          $('#sg-bar').style.width = p + '%';
          $('#sg-train-msg').textContent = `${j.status} · ${j.iters_done}/${j.iters_total}`
            + (j.loss != null ? ' · loss ' + j.loss : '') + (j.val_loss != null ? ' · val ' + j.val_loss : '');
          if (['done', 'failed', 'stopped'].includes(j.status)) {
            clearInterval(trainPoll);
            $('#sg-stop').disabled = true;
            toast(j.status === 'done' ? '训练完成' : '训练结束：' + j.status, j.status === 'done' ? '' : 'bad');
            $('#sg-jobpill').classList.add('hidden');
            viewTrain();
          }
        } catch (e) { clearInterval(trainPoll); }
      }, 1500);
    }
  }

  /* ================= 路由 ================= */

  function render(name) {
    clearInterval(trainPoll);
    startGlobalWatcher();
    $('#sg-panel-close').addEventListener('click', panelHide);
    if (name === 'home') return viewHome();
    if (name === 'import') return viewImport();
    if (name === 'clean') return viewClean();
    if (name === 'train') return viewTrain();
  }

  return { render };
})();

window.STUDIO = STUDIO;
