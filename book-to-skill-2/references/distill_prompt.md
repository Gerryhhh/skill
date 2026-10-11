# Distillation Prompt Template (book → knowledge skeleton)

Use this prompt (fill in `<BOOK_TITLE>` and `<TEXT_FILE>`) when spawning a
`general-purpose` sub-agent to turn a book's extracted text into a structured
knowledge skeleton. The skeleton becomes `references/book_knowledge.md` of the
target skill.

---

You are a rigorous research assistant. Read the full plain-text of the book
**<BOOK_TITLE>** at `<TEXT_FILE>` (read ALL of it, do not sample). Distill its
core knowledge into a structured "knowledge skeleton" Markdown document saved to
`<OUTPUT_MD>`, so that another AI assistant can act as a domain expert on this
book.

Cover these dimensions with clear Markdown heading levels:

1. **Core thesis** — the one-sentence central argument.
2. **Core model(s)** — the book's key frameworks, mechanisms, cycles, or stages.
3. **Named typologies / tables** — e.g. historical cases, categories, with
   concrete data (dates, names, figures) taken strictly from the text.
4. **Key concepts / terminology** — important terms, with CN/EN pairs where the
   book is translated.
5. **Methodology & theoretical lineage** — what schools of thought or authors it
   draws on and where it diverges.
6. **Policy / practical implications** — what the author recommends or implies.
7. **Transferable analytical framework** — step-by-step instructions for applying
   the book's model to a NEW situation (e.g. a current event, a different domain).
8. **Key diagrams / models in the text** — list notable figures/tables as anchors.

Requirements:
- Write in the book's language; keep key terms with original-language glosses.
- Base every claim on the text. Do NOT fabricate data. If the text is silent,
  mark it "原文未详述" (not detailed in source).
- Structure clearly; be thorough but not padded.
- At the end, report in ≤300 words: the core framework, whether key data is
  complete, and any important model/diagram details you found.

Return the file path when done.

---

## 分章并行模式（书 > 300 页 / > 20 万字时用这个）

把 `<TEXT_FILE>` 先按 L1 章节切成 `chNN.txt`，再为每个子代理套用下面模板，
每个代理负责 1~3 章，各自把结果写入 `<OUTPUT_DIR>/distill_chNN.md`，**并行**执行。

### 单章提炼模板

你是严谨的研究助理。用下面**统一的 7 段模板**，把第 **<CH_NUM>** 章（文件 `<CH_FILE>`，
页码标记形如 `========== PAGE N ==========`）提炼为 Markdown，写入 `<OUTPUT_MD>`。

1. **章节摘要**（200–400 字）
2. **术语表** — 术语｜定义｜原文对照（译著必填）
3. **关键模型 / 框架** — 逐条给出：名称｜定义｜适用场景｜影响或结论｜慎用情形
4. **决策 / 实操框架** — 可执行的步骤清单
5. **案例要点** — 含具体数据、年份、人名（严格取自原文）
6. **金句** — 3–8 条，标注页码 `pN`
7. **章节关联** — 与前后章的逻辑关系

### 硬性要求（踩过坑，别违反）

- **必须读完整个文件**。文件较长时先 `Read` 前 2000 行，再用 `offset` 续读，
  **一直读到结尾，不要只看开头就收工**。
- 页码取自 `========== PAGE N ==========` 标记，以 `pN` 形式标注。
- **只写你负责的那一个文件**，不要改动其他 `distill_ch*.md`，不要写汇总文件。
- 严格基于原文。原文没写的写「原文未详述」，**绝不编造**数据、年份、引文。
- 完成后**只回报**：文件路径 + 不超过 300 字的结论。**不要把正文贴回主上下文。**

### 主代理收尾

全部子代理完成后，主代理只做：按章号顺序拼接 → 去重（跨章重复的模型/术语合并）→
补一份全局「核心模型速查表」→ 写入 `book_content.md`。主代理**不通读**分章原文。
