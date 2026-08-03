# Personal AI Agent - Learn From Video

一个本地优先的短视频学习与知识管理工具。粘贴抖音、哔哩哔哩或文章链接，系统会自动完成内容解析、媒体下载、语音转写、AI 总结和知识库归档，并提供可调用工具的智能助手。

## 功能

- 抖音、哔哩哔哩视频和网页文章解析
- 视频下载、音频提取、压缩与长音频分段
- OpenAI 兼容接口的 ASR、LLM、Embedding 和 VLM OCR
- 多种总结模板：快速总结、知识笔记、教程和观点分析
- SQLite + FTS5 全文检索与向量检索
- 支持流式输出、会话历史和工具调用的 AI Agent
- 联网搜索、网页抓取、文件操作和受控命令执行
- 本地知识库、Markdown 归档和响应式 Web 界面

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

AI 服务使用 OpenAI 兼容接口。默认 Base URL 指向 SiliconFlow，也可以替换为其他兼容服务。模型输入框旁的刷新按钮会从对应服务的 `/models` 接口获取可用模型。

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

## 隐私与安全

项目按本地优先方式运行。以下内容已通过 `.gitignore` 排除，不应提交到版本库：

- `config.local.json` 和 `.env*` 中的 API Key
- `data/` 中的数据库
- `library/` 中的个人知识库
- `conversations/` 中的会话记录
- `workdir/`、`workspace/` 和 `temp/` 中的本地文件
- `output/` 中的日志、截图和运行产物

Agent 的命令执行带有权限分级和黑名单保护，但仍建议只在可信的本地环境中运行，并在启用高权限操作前检查请求内容。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

当前测试覆盖配置、数据库、链接解析、媒体处理、ASR、总结、知识库、流水线、Agent、OCR 和设置接口。

## 项目结构

```text
src/                 FastAPI 应用与业务逻辑
src/agent/           Agent、工具与权限控制
src/knowledge/       知识库和向量检索
src/media/           下载与音频处理
src/parsers/         平台和文章解析器
src/routes/          Web/API 路由
templates/           Jinja2 页面模板
static/              CSS 和静态资源
prompts/             总结提示词模板
tests/               自动化测试
docs/                产品与实现文档
```

更完整的产品设计和实现进度见 [docs/PRD.md](docs/PRD.md) 与 [docs/IMPLEMENTATION_CHECKLIST.md](docs/IMPLEMENTATION_CHECKLIST.md)。
