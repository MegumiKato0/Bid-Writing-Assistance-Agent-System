# BidPrep · 投标材料清单自动生成

从**招标文件 PDF**抽取信息，输出（**Markdown + Excel + PDF** 三套对应）：

- `投标材料准备清单.md` / `投标材料准备清单.pdf`
- `投标材料清单表.xlsx` / `投标材料清单表.pdf`（五列：序号、材料类别、材料名称、是否具备、备注；表头主题色 **#66CCFF**）
- `需要填空的内容项表.xlsx` / `需要填空的内容项表.pdf`
- `extraction_meta.json`（字段溯源与审查记录）

PDF 由 **reportlab** 生成，中文优先使用系统字体（如 Windows 微软雅黑）；无中文字体时可能回退为 Helvetica。

流水线：**规则兜底 → Agent1 结构化抽取 → Agent2 审查（带页码证据）→ 程序合并 → 导出**。

**第二阶段（已实现 · CLI + API）**：在招标拆解之后增加 **「客户材料核验与归档」**。以 Phase 1 导出的 **`投标材料清单表.xlsx`**（或 JSON 基准）为客户侧比对基准，支持客户 **PDF / DOCX / 图片 / ZIP**，输出 **`reports/`**（核验总表 xlsx、缺失清单 md、归档索引 json）、**`normalized/`**、**`previews_png/`**，并保留 **`originals/`** 原件。OCR 为可选兜底（`pip install -e .[ocr]` + 安装 Tesseract）。

- CLI：`bidprep-phase2 --baseline 投标材料清单表.xlsx --client 客户材料.zip -o phase2_out`（可重复 `--client-file`）。阶段二**固定双 Agent**（依赖 `.env` LLM）；可选 `--llm-assist`（灰区）、`--llm-file-read-batch-size`、`--no-include-unused-files` 等，见 `python -m bidprep.phase2 --help`。
- API：`POST /api/phase2/verify`（`baseline` 必填；`clients_zip` 与 `extra_files` 至少其一；**须**传阶段一相同 `llm_mode`/Ollama/OpenAI；双 Agent 固定开启；可选 `llm_assist`、`llm_file_read_batch_size`、`include_unused_files` 等）。下载：`GET /api/phase2/jobs/{id}/{verification_xlsx|checklist_filled_xlsx|ingested_files_json|missing_md|...}`。
- 冒烟：`python scripts/smoke_phase2.py`

详细产品说明见 [`docs/PHASE2_客户材料核验与归档.md`](docs/PHASE2_客户材料核验与归档.md)。

## 环境要求

- Python 3.10+
- Node.js 18+（仅前端）
- 本地 **Ollama** 或任意 **OpenAI 兼容** API（如阿里云 DashScope `https://coding.dashscope.aliyuncs.com/v1`）

## 安装（后端 · Windows）

**推荐**：在项目根目录双击 [`setup_venv.bat`](setup_venv.bat)，会在 **`d:\book\.venv`** 创建虚拟环境并执行 `pip install -e .`（安装 **openpyxl**、pymupdf、fastapi 等全部依赖）。

然后复制环境变量模板：

```bat
copy .env.example .env
REM 编辑 .env：LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
```

**一键启动**：[`start.bat`](start.bat) 会在没有 `.venv` 时自动调用 `setup_venv.bat`；若已激活环境但缺少 `openpyxl` 等，会自动 `pip install -e .` 补全。

手动安装（与上等价）：

```bash
cd d:\book
python -m venv .venv
.venv\Scripts\activate
pip install -U pip
pip install -e .
```

## 命令行

```bash
python -m bidprep path\to\tender.pdf -o out
# 无模型时仅测规则与导出骨架：
python -m bidprep path\to\tender.pdf -o out --skip-llm
```

## API 服务

**Windows 一键启动**：双击 [`start.bat`](start.bat)（若无 `.venv` 会先跑 [`setup_venv.bat`](setup_venv.bat)；随后激活虚拟环境、缺包时自动 `pip install -e .`，再启动服务并打开浏览器）。

或命令行：

```bash
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

- `GET /api/llm/ollama/models?host=http://127.0.0.1:11434`：读取本机 Ollama 已安装模型列表（需 Ollama 已启动）。
- `POST /api/process`：表单字段 `file`（PDF）及 LLM 参数（同前）。**返回 JSON**（非 ZIP），含 `job_id` 与按类别分组的下载路径 `categories[].items[].href`。默认还会在**运行后端的机器上**自动复制一份到项目目录 **`exports/phase1/{job_id}/`**（含清单、表、元数据及 `input.pdf`）；可通过环境变量 **`PHASE1_AUTO_EXPORT=false`** 关闭，或用 **`PHASE1_AUTO_EXPORT_DIR`** 指定其他绝对路径（见 `.env.example`）。成功时响应中可能含 `local_saved_path` 字段。
- `GET /api/jobs/{job_id}/{file_key}`：分项下载。`file_key` 为：`checklist_md`、`checklist_pdf`、`materials_xlsx`、`materials_pdf`、`fill_xlsx`、`fill_pdf`、`meta_json`。结果暂存在系统临时目录，默认约 **24 小时**后清理。  
  Web 界面会展示「分类下载」按钮（Markdown / Excel / PDF / 元数据 JSON）。

若已执行 `cd frontend && npm install && npm run build`，访问 `http://127.0.0.1:8000/` 可同时打开**主题色 #66CCFF** 的 Web 界面（与 API 同源，无需 CORS）。界面顶部可在 **「阶段一 · 招标拆解」** 与 **「阶段二 · 客户材料核验」** 之间切换；阶段二支持基准清单 + ZIP/多文件上传、结果下载，以及**核验明细分页预览**（规则引擎，**未接大模型**）。

## 前端（Vue 3 + Vite 5，主题色 #66CCFF）

技术栈：**Vue 3**（`<script setup>`）、TypeScript、Vite；依赖见 [`frontend/package.json`](frontend/package.json)。

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 `http://127.0.0.1:5173`，Vite 将 `/api` 代理到后端 `8000` 端口。

**API Key 加密（前端）**：在「OpenAI 兼容」模式下，可设置**本地主密码**，使用浏览器 **Web Crypto（PBKDF2 + AES-GCM）** 将 API Key 加密后写入 `localStorage`；**主密码默认不落盘**。可选「本会话记住主密码」写入 `sessionStorage`，关闭标签页后失效。处理请求时 Key 仍以 HTTPS 表单发往本机后端（与此前一致），请仅在可信环境使用。

若出现 `socksio` / SOCKS 代理相关报错，说明系统里配置了 `ALL_PROXY`/`HTTPS_PROXY` 等 **socks5** 代理。请执行 **`pip install -e .`** 安装已包含的 `socksio` 依赖；或在 `.env` 中设置 **`LLM_HTTP_TRUST_ENV=false`** 让 LLM 请求忽略系统代理（访问本机 Ollama 时常用）。

## 免责声明

生成结果仅供投标准备辅助，关键条款以正式招标文件及澄清文件为准。
