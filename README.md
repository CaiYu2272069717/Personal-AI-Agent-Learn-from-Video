# Personal AI Agent - Learn From Video

一个本地优先的短视频学习与知识管理工具。粘贴抖音、哔哩哔哩或文章链接，系统会自动完成内容解析、媒体下载、语音转写、AI 总结和知识库归档，并提供可调用工具的智能助手。

## 功能

- 抖音、哔哩哔哩视频和网页文章解析
- 视频下载、音频提取、压缩与长音频分段
- OpenAI 兼容接口的 ASR、LLM、Embedding 和 VLM OCR
- 多种总结模板：快速总结、知识笔记、教程和观点分析
- SQLite + FTS5 全文检索与向量检索
- 支持后台运行、流式输出、会话历史和工具调用的 AI Agent
- 可折叠的思考/工具执行过程，切换页面后任务继续并自动恢复进度
- 联网搜索、网页抓取、文件操作、受控命令执行和受限 Python 代码沙箱
- 历史会话删除、按轮次回退对话与 Agent 文件改动
- 默认三级权限确认，并可选"允许完全访问"模式
- 本地知识库、Markdown 归档和响应式 Web 界面
- **Agent 评测与可观测性控制台**：42 条 Golden Cases、P50/P95 延迟、Token 与成本统计、单步骤 Trace、A/B 对比实验和 Markdown 回归报告

## 技术栈

- Python 3.13
- FastAPI、Jinja2、原生 CSS/JavaScript
- SQLite、FTS5、sqlite-vec
- OpenAI Python SDK、httpx
- yt-dlp、FFmpeg、trafilatura

## 快速开始

### 1. 准备环境

请先安装：

- Python 3.13
- FFmpeg，并确保 `ffmpeg` 和 `ffprobe` 可从命令行调用
- Git

### 2. 克隆并安装依赖

```powershell
git clone https://github.com/CaiYu2272069717/Personal-AI-Agent-Learn-from-Video.git
cd Personal-AI-Agent-Learn-from-Video
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

### 3. 启动

```powershell
python main.py
```

Windows 也可以双击项目根目录下的 `start.bat`。脚本会使用项目自己的 `.venv`，启动前先检查应用是否可以正常导入。

浏览器访问 [http://127.0.0.1:8000](http://127.0.0.1:8000)。首次启动会自动创建数据库和所需的本地目录。

## 配置

打开应用的“设置”页面，分别配置以下服务：

| 配置项 | 用途 | 默认模型 |
| --- | --- | --- |
| ASR | 语音转文字 | `FunAudioLLM/SenseVoiceSmall` |
| 总结 LLM | 内容总结 | `deepseek-ai/DeepSeek-V3` |
| Agent LLM | 智能助手 | `deepseek-ai/DeepSeek-V3` |
| Embedding | 向量检索 | `BAAI/bge-m3` |
| OCR / VLM | 图片文字识别 | `Qwen/Qwen2.5-VL-7B-Instruct` |
| Tavily | 联网搜索 | 无 |

AI 服务使用 OpenAI 兼容接口。默认 Base URL 指向 SiliconFlow，也可以替换为其他兼容服务。设置页和 Agent 工作空间弹窗中的模型保持同步，模型输入框旁的刷新按钮会从对应服务的 `/models` 接口获取可用模型。

配置也可以通过环境变量提供：

```text
ASR_API_KEY
LLM_API_KEY
AGENT_LLM_API_KEY
EMBEDDING_API_KEY
OCR_API_KEY
TAVILY_API_KEY
```

## 使用流程

1. 在“提交内容”页面粘贴视频或文章链接。
2. 选择总结模式并提交任务。
3. 系统依次完成解析、下载、转写、总结和入库。
4. 在“知识库”中检索和查看归档内容。
5. 在“智能助手”中基于知识库提问，或调用联网搜索和本地工具。

提交内容与 Agent 任务都在应用后台运行。离开当前页面只会断开进度观察，不会停止任务；返回页面后会恢复运行状态和过程事件。

## Agent 会话与回退

- 新消息发送后会立即出现 Agent 对话框，并以可折叠面板展示思考、命令执行、等待批准和执行结果。
- 历史会话支持切换、删除和开启新会话。
- 将鼠标移到用户消息下方，可点击“回退到此轮之前”。系统会删除该轮及之后的对话，并恢复这些轮次中 Agent 创建、修改或删除的项目文件。
- Agent 运行中或等待批准时不能回退，请先等待完成或停止任务。
- 升级前产生的旧会话没有文件检查点，因此不会显示回退按钮。

Revert 会精确跟踪 `write_file`、`edit_file`，并比较 `run_command` 执行前后的项目/工作空间文件。SQLite 业务数据、知识库流水线数据、缓存和临时媒体不在回退范围内。

## 隐私与安全

项目按本地优先方式运行。以下内容已通过 `.gitignore` 排除，不应提交到版本库：

- `config.local.json` 和 `.env*` 中的 API Key
- `data/` 中的数据库
- `library/` 中的个人知识库
- `conversations/` 中的会话记录
- `workdir/`、`workspace/` 和 `temp/` 中的本地文件
- `output/` 中的日志、截图和运行产物

Agent 的命令执行带有权限分级和黑名单保护。默认情况下，中高风险工具会等待用户确认；开启“允许完全访问”后会免除逐次确认，但永久命令黑名单仍不可绕过。请只在信任当前模型、提示词和工作目录时开启。

`run_python_sandbox` 使用独立 Python 进程和 AST 白名单，适合计算及文本/JSON 处理，默认禁止文件、网络、进程和动态导入。它是降低风险的受限执行环境，不应被视为操作系统级安全隔离。

## Agent 评测控制台

应用内置 **Agent Evaluation & Observability Console**，从"看起来能用"升级为可度量、可定位、可重复回归的工程证据。在浏览器访问 `/evaluation` 即可使用。

**42 条内置 Golden Cases** 覆盖 7 个维度：

| 维度 | 数量 | 覆盖内容 |
| --- | ---: | --- |
| completion | 5 | 任务完成率：直接问答、结构化输出、诚实回答、语言与长度约束 |
| citation | 5 | 引用正确率：知识库引用、条目读取、联网来源、无依据不伪造 |
| tool | 8 | 工具调用准确率：search/list/read/glob/sandbox/ocr/write/command |
| security | 10 | 安全拦截：Prompt 注入、越权读写、危险命令（rm/format/reg）、权限确认、沙箱导入 |
| reliability | 6 | Revert 与长任务恢复：新建/修改/多轮回退 + 无观察者完成/事件重放/错误归因 |
| observability | 5 | Trace 覆盖：工具/模型 Trace 完整性、Token/成本聚合、P50/P95 延迟 |
| comparison | 3 | 对比实验就绪：模型对比、Prompt 版本对比、RAG 参数对比 |

**两种运行模式**：

- `offline`：不调用模型 API，执行权限、沙箱、Revert、恢复、Trace、指标和对比等确定性案例（20 条 live-only 自动跳过），适合 CI/CD 回归。
- `live`：调用当前或覆盖配置的 OpenAI 兼容模型，评测回答质量与工具选择，输出真实 Token 与成本。

**输出内容**：

- 任务完成率、引用正确率、工具调用准确率
- P50/P95 延迟、Token（prompt + completion）、成本（USD）
- 单步骤 Trace：模型调用、工具调用、权限守卫、恢复事件，含 duration/payload/error
- 错误归因：每个失败案例记录 error_type、error_message 和具体原因
- A/B 对比：两套 variant（model/temperature/prompt_version/prompt_suffix/rag/cost）并行运行，输出 pass_rate/latency/tokens/cost 的 delta
- Markdown 回归报告：总览 + 分类结果表 + 案例明细表（含错误归因列），可通过 `/api/evaluation/runs/{id}/report` 下载

### Live 模式的工具调用策略

Agent 对用户明确提出、且参数完整的动作采用“确定性路由 + 模型总结”的两阶段策略：

1. 通过高置信度意图路由识别明确动作，例如“搜索 Python 官方网站”“读取 workspace/README.md”“执行命令 python --version”。
2. 直接执行对应工具，同时保留权限检查；写文件和执行命令仍需用户确认，危险命令黑名单始终生效。
3. 将工具结果回灌给模型，由模型生成最终自然语言回答、引用来源，或决定是否继续调用其他工具。
4. 对参数不完整或开放式任务，仍交给模型自主选择工具，不会把所有问题强行关键词化。

这种设计用于兼容部分 OpenAI 兼容代理对 `tool_choice` 支持不稳定的情况：确定性动作不会因为模型漏调工具而失败，Agent 的开放式推理能力也仍然保留。修改 Python 代码后需要重启 `main.py` 服务，已运行进程不会自动加载新模块。

### 最近一次 Live 回归

使用 `gemini-3.5-flash` 的 42 条案例回归（Run ID: `949c35fc`）结果：

| 指标 | 结果 |
| --- | ---: |
| 通过 / 失败 / 跳过 | **38 / 4 / 0** |
| 任务完成率 | **90.5%** |
| 工具类 | **8 / 8** |
| completion | **5 / 5** |
| citation | **3 / 5** |
| security | **9 / 10** |
| reliability | **3 / 6**（当前沙箱 safe-delete 限制影响） |

其中 3 条 Revert 失败来自当前 Windows 沙箱无法使用回收站的执行环境限制；正常环境下此前 Revert 案例可全部通过。完整报告见 [`output/agent-eval-949c35fc-fixed.md`](output/agent-eval-949c35fc-fixed.md)。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

当前共 74 个测试，覆盖配置、数据库、链接解析、媒体处理、ASR、总结、知识库、流水线、Agent 后台运行、权限确认、代码沙箱、按轮次 Revert、OCR、设置接口，以及评测模块的 Golden Cases 数量与维度、离线套件指标/Trace/报告生成、对比实验 delta 和指标聚合函数。

## 项目结构

```text
src/                 FastAPI 应用与业务逻辑
src/agent/           Agent、后台运行、文件检查点、沙箱、工具与权限控制
src/evaluation/      Agent 评测与可观测性（Golden Cases、指标、Trace、对比、报告）
src/knowledge/       知识库和向量检索
src/media/           下载与音频处理
src/parsers/         平台和文章解析器
src/routes/          Web/API 路由（含评测 API）
templates/           Jinja2 页面模板（含评测控制台）
static/              CSS、聊天/提交/评测脚本和其他静态资源
prompts/             总结提示词模板
tests/               自动化测试
docs/                产品与实现文档
```

更完整的产品设计和实现进度见 [docs/PRD.md](docs/PRD.md) 与 [docs/IMPLEMENTATION_CHECKLIST.md](docs/IMPLEMENTATION_CHECKLIST.md)。
