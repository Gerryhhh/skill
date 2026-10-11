---
name: book-to-skill
description: 把一本书/长文/PDF 转成可复用的 WorkBuddy Skill 的标准流水线。当用户说「把这本书/PDF/长文转成
  Skill」「做成知识库 Skill」「读书做技能」「book to skill」「把文章变成 AI 技能」「用某本书的框架分析
  X」时使用。覆盖：探测源文件是否含文字层、PyMuPDF 抽取全文与目录、子代理（必要时多代理并行）提炼结构化框架、构建 SKILL.md +
  references/ + sources/、部署到用户级 skills 目录、更新 PAWS
  技能总清单。已验证于《高情商领导力》PDF、罗斯《公司理财》EPUB、848 页 43 万字大部头。
version: 2.0.0
agent_created: true
allowed-tools: Read,Write,Edit,Bash,Glob,Grep,Agent,TaskCreate,TaskUpdate,AskUserQuestion,present_files
disable: true
---

# 书 → Skill 转换流水线

把一本书（PDF / EPUB / 扫描件 / 长文）提炼成可复用的 WorkBuddy Skill，
让 WorkBuddy 之后能以「该书领域专家」的身份讲解、引用、并把书中框架套到新问题上。

这是一条**工作流型 Skill**：抽取 → 探测 → 提炼 → 构建 → 部署 → 归档。按顺序走。

## 适用触发

「把这本书转成 Skill」「做成知识库 Skill」「book to skill」「把文章变成 AI 技能」
「用《X》里的模型分析 Y」。

## 标准流程（8 步）

### 1. 定位源文件并判断类型

确认文件存在、记下扩展名（`.pdf` / `.epub` / `.txt` / `.md` / `.html`）。未提供则问用户。

### 2. 探测文字层（PDF 必做，别凭外观猜）

```bash
<venv-python> <skill_dir>/scripts/extract_book.py "<book>" --probe
```

输出 `AVG_CHARS_PER_PAGE` 和判定：

- `>= 100` → 有文字层，直接抽取
- `20~100` → 薄文字层，抽几页人工核对再信
- `< 20` → 近似扫描件，需要 OCR

**看着像扫描件 ≠ 没有文字层**。《高情商领导力》143 页 PDF 看着像扫描，实际含 9.5 万字文字层。
EPUB 本身就是 ZIP + XHTML 文本层，**永远不需要 OCR**。

### 3. 抽取全文与目录

```bash
<venv-python> <skill_dir>/scripts/extract_book.py "<book>" /tmp/<name>/raw_text.txt --toc /tmp/<name>/toc.json
```

- EPUB：按 `container.xml → OPF → spine` 的权威阅读顺序解析，自动跳过 nav 导航文档。
- PDF：优先 PyMuPDF（`import pymupdf`，可顺便拿到 `doc.get_toc()` 真目录），失败自动回退 pypdf。
- TXT/MD/HTML：直接读并清洗标签。
- 输出带 `========== PAGE N ==========` / `========== CHAPTER <href> ==========` 标记，
  供子代理按位置引用。

> **必须用受管 venv 的 python**，裸 `python3` 没有 pypdf/pymupdf。见下方「环境」。

### 4. OCR（用户要求，或确认无文字层时）

- ⚠️ **用户若显式要求 OCR，必须执行** —— 即使 PDF 含文字层、纯抽取更快。
  不要自作主张替代用户指定的方法（踩过坑：自作主张跳过 OCR 被纠正）。
- 推荐 **RapidOCR**（纯 pip、原生中文、无需外部二进制）。
- 渲染：`pymupdf` 的 `page.get_pixmap(dpi=300)` 出 PNG → 逐页 OCR → 合并为带 `PAGE N` 标记的全文。
- 备选：`brew install tesseract tesseract-lang`（含 chi_sim），本机 brew 较慢，优先 RapidOCR。

### 5. 子代理通读提炼（关键：把重读甩出主上下文）

派 `general-purpose` 子代理 Read 全文，产出结构化 Markdown。提示词模板见
`references/distill_prompt.md`，需填入 `<BOOK_TITLE>` / `<TEXT_FILE>` / `<OUTPUT_MD>`。

**大部头必用「多子代理按章并行」**（> 300 页 或 > 20 万字）：

1. 先按 L1 章节切分成 `/tmp/<name>/chNN.txt`（PDF 用 `toc.json` 的页码区间；EPUB 用 spine 顺序）。
2. 派 N 个子代理**并行**，每个负责 1~3 章，统一模板：
   章节摘要 / 术语（含原文对照）/ 关键模型（定义 + 适用 + 影响 + 慎用）/ 决策框架 / 案例要点 / 金句 / 章节关联。
3. 各代理只写自己那份 `/tmp/<name>_distill/distill_chNN.md`。
4. 主代理只做合并，不通读全文。

实测：848 页 / 43 万字，9 个代理并行约 4.5 分钟完成，格式统一，主代理省下大量 context。

**子代理指令务必写清楚**：

- 文件较长时先 Read 前 2000 行，再用 offset 续读**直到读完，不要漏后半部分**。
- 页码取自 `========== PAGE N ==========` 标记，以 `pN` 标注。
- 只写自己负责的文件，不改动他人文件。
- 完成后只回报路径 + ≤300 字结论，**不要把正文贴回主上下文**。

### 6. 落盘中间稿

把结果存为 `book_content.md`（结构化内容稿），与 `raw_text.txt`、`toc.json` 一并留档。

### 7. 构建 Skill

目标目录：

- **用户级**（默认，跨项目）：`~/.workbuddy/skills/<slug>/`
- **项目级**（随仓库共享）：`<project>/.workbuddy/skills/<slug>/`

`<slug>` 用 **ASCII kebab-case**（如 `primal-leadership`），中文目录名不稳。
可用 skill-creator 脚手架（内置，随应用分发）：

```bash
python3 /Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/resources/builtin-skills/skill-creator/scripts/init_skill.py <slug> --path ~/.workbuddy/skills
```

然后删掉示例文件（`scripts/example.py`、`references/api_reference.md`、`assets/example_asset.txt`）和空目录。

最终结构：

- `SKILL.md` — frontmatter 含 `name` / 描述充分的 `description`（触发词决定能否自动激活）/ `version` / `agent_created: true`；
  正文含核心模型速查表、应用工作流、参考索引、关键原则。**保持精炼（< 5k 字），细节下沉到 references。**
- `references/` — 按主题拆分：模型详解、术语清单、自评工具、章节摘要、话术模板。
- `sources/` — 忠实底稿：`book_content.md`、`raw_text.txt`、`toc.json`。

### 8. 部署与收尾

- 校验打包（可选）：
  ```bash
  python3 <skill-creator>/scripts/quick_validate.py <skill_dir>
  python3 <skill-creator>/scripts/package_skill.py <skill_dir> <output_dir>
  ```
- 写入 `~/.workbuddy/skills/<slug>/` 即可立即被调用，无需重启。
- **更新 PAWS 技能总清单**：按用户惯例，共享空间根目录的 `WORKBUDDY_SKILLS.md`
  要追加一行（序号 / 名称 / 分类 / 说明），并更新总数和最后更新日期。不更新会导致跨 AI 工具看不到。
- PAWS 项目：`begin → finish` 提交；`projects/PRJ-xxx/{work,outputs}` 由 Guard 设为只读，
  **禁止直接写入**，归档副本放会话工作目录内即可。
- 用 `present_files` 给用户可点击的成果路径。

## 环境

家目录路径随机器不同（同一台机可能既有 `gerry` 又有历史用户名 `tszcm` 的别名），
**一律用 `$HOME/` 表达，绝不要写死 `/Users/<用户名>/`** —— 这是历史同步冲突的根因：

```bash
ls -d ~/.workbuddy/binaries/python/envs/*    # 看有哪些 venv
~/.workbuddy/binaries/python/envs/default/bin/python -c "import pymupdf; print('ok')"
```

- 受管 venv python：`~/.workbuddy/binaries/python/envs/default/bin/python`
- 装包：`.../envs/default/bin/pip install pymupdf pypdf`
- **架构坑（已踩）**：venv 里曾装到 x86_64 轮子，本机 arm64，`import fitz` 直接
  `mach-o, but an incompatible architecture`。修复：`pip install --force-reinstall --no-cache-dir pymupdf`。
  装完用 `file .../pymupdf/_extra.so` 确认是 arm64。
- **镜像坑（已踩）**：本文档旧版写「国内一律加清华镜像」，但**该机器上清华源返回 403**，
  官方 pypi.org 反而 200 且 2.9s 可达。**装包前先测**：
  `curl -s -o /dev/null -w "%{http_code}" --max-time 8 https://pypi.org/simple/pymupdf/`
  哪个通用哪个，不要盲信镜像。
- OCR 用的 `envs/ocr310`（系统 Python 3.10 venv）**在部分机器上已损坏** ——
  其 `bin/python` 指向 `/Library/Frameworks/Python.framework/Versions/3.10`，
  而该框架未必存在。用前先 `.../envs/ocr310/bin/python -V` 验证；
  坏了就在受管 3.13 venv 里直接装 RapidOCR。

## 经验教训

- **用户指定的方法必须执行**：用户说 OCR 就 OCR，不能因为有更快路径就替换。
- **先探测再动手**：外观会骗人，字符数不会。
- **重读交给子代理**：主代理只做编排与合并，省 context 且更快。
- **大书按章切分并行**：单代理读 70 万字会丢后半部分，切章并行才稳。
- **EPUB 的 TOC 可能不全**：实测某 EPUB 有 381 条 TOC 但只有 14 个顶层「第 N 章」，缺 11 章，
  **不能直接靠 TOC 切章**。改用正则从合并文本里匹配正文真实章节起点：
  `re.compile(r'第\s*(\d+)\s*章[\u3000]')`（注意全角空格 `\u3000`，可排除目录页里用 ASCII 空格的重复标题），
  取每个章号**最后一次匹配**的位置作为该章正文起点。
- **slug 用 ASCII**，中文目录名不稳。
- **诚实**：严格基于原文，原文没写的标「原文未详述」，不编造数据、年份、引文。

## Resources

- `scripts/extract_book.py` — 抽取器。epub（按 spine）/ pdf（PyMuPDF→pypdf）/ txt / md / html；
  支持 `--toc <json>` 导出目录、`--probe` 探测文字层。
- `references/distill_prompt.md` — 子代理提炼提示词模板（8 个维度）。
