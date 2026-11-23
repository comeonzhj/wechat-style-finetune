---
id: classify
version: 2
model_hint: cheap/fast model is enough
output: json
variables: [title, published_at, body]
---

# 提示词 01：清洗分类与可用性判定

> 作用：给每篇文章定性，决定它进训练 / 进评测 / 只取风格 / 丢弃；同时定位推广段落，供后续裁切。

## System

你是公众号文章的数据标注员。你的工作是把一篇文章切成「可用正文 / 推广内容 / 噪声」，并给出它的类型和用途判定。你只输出 JSON，不做解释。

## User

下面是一篇公众号文章。请按 Schema 输出标注结果。

```markdown
<<<ARTICLE>>>
标题：{{title}}
发布时间：{{published_at}}

正文：
{{body}}
<<<END>>>
```

## 标注规则

1. `kind` 从这些里选（可多选，按主要程度排序）：`干货教程`、`观点评论`、`数据解读`、`复盘`、`产品推广`、`杂谈`。
2. `body_ratio`：去掉推广、报名、二维码引导、课程价格、团队招募后，剩余正文占全文的字数比（0–1，保留两位小数）。
3. `promo.segments`：逐段找出推广性内容。只标**连续成段**的，不要标一句话。每段给：
   - `type`：`课程/训练营`、`报名引导`、`价格涨价`、`社群招募`、`自有产品导流`、`求关注/求转发`、`其他`
   - `quote`：该段开头的**原文摘录，20–30 字，逐字照抄**（这里允许照抄，程序要靠它在原文里定位切点，抄错就切不准）
   - `approx_chars`：大致字数
   - `keep`：`true` 仅当这段同时承担了正文论述功能（例如用课程案例讲方法论），否则 `false`
4. `structure_mode`：
   - `flow`：正文没有 `##` 小标题，连续行文
   - `sectioned`：有 1–3 个 `##`
   - `heavily_sectioned`：有 4 个及以上 `##`
5. `length_tier`：按去推广后的正文字数，`short`<800、`mid`<2000、`long`<6000、`xl`≥6000。
6. `needs_split`：`length_tier == xl` 时为 `true`。
7. `topic_tags`：2–5 个，从 `AI工具`、`提示词`、`Agent`、`产品经理`、`职业转型`、`私域运营`、`抖音`、`视频号`、`小红书`、`微信生态`、`数据解读`、`认知方法`、`内容创作`、`知识付费` 中选，不够可自拟。
8. `pii`：是否出现手机号、微信号、邮箱、真实姓名、身份证、具体公司内部数据。是则 `true` 并列出类型。
9. `train_role` 判定（按顺序，命中即停）：
   - 去推广后正文 < 300 字 → `skip`，`reason: 正文过短`
   - `body_ratio` < 0.6 且 `kind` 首选为 `产品推广` → `skip`，`reason: 推广为主`
   - `body_ratio` < 0.75 → `style_only`，`reason: 正文占比低，仅取文风`
   - 其余 → `train`
   > 注意：`eval` 不由你判定，由外层分层抽样决定，你这里一律给 `train` 或 `skip` / `style_only`。
10. `has_code` / `has_table`：是否含代码块 / markdown 表格。
11. `warnings`：任何会影响后续处理的问题（大段引用他人、通篇是问答、主要是图片说明、疑似重复文章等）。

## 输出格式

严格输出 JSON，不要代码块围栏，不要在前后加任何文字。

```json
{
  "kind": ["干货教程"],
  "body_ratio": 0.86,
  "promo": {
    "has": true,
    "segments": [
      {"type": "课程/训练营", "quote": "有这个智能体全程视频操作教程的课程", "approx_chars": 120, "keep": false}
    ]
  },
  "structure_mode": "flow",
  "length_tier": "mid",
  "needs_split": false,
  "topic_tags": ["AI工具", "提示词"],
  "pii": false,
  "train_role": "train",
  "reason": "",
  "has_code": false,
  "has_table": false,
  "warnings": []
}
```
