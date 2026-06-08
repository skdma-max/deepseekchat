# DeepSeekChat

全自动 Agent 桌面应用 — 双击运行，无需终端。基于 DeepSeek API 的本地 AI 助手。

## 功能

| 类别 | 能力 |
|------|------|
| 💬 对话 | 流式输出，深色主题，聊天历史 |
| 📋 工作流程 | 任务列表、进度条、勾选完成 |
| 📌 持久化任务 | 后台任务管理，关闭不丢失 |
| 🤖 子代理并行 | 最多 5 个独立代理同时执行任务 |
| 🗜️ 上下文压缩 | 自动压缩长对话，保持响应速度 |
| 🔒 沙箱隔离 | Shell 命令在临时目录执行 |
| 📄 文件读写 | 读取/编辑/写入本地文件 |
| 🔀 Git | status / diff / log / show / blame |
| 🔎 代码搜索 | 正则搜索 + 文件名搜索 |
| 🐍 Python 沙箱 | 执行 Python 代码 |
| ⚡ Shell | 执行 PowerShell / CMD 命令 |
| 🌐 Web 搜索 | DuckDuckGo 搜索 |
| 🧪 测试 | 自动检测项目类型并运行测试 |
| 💾 会话管理 | 保存/加载/列出对话 |
| 🎨 界面 | 模式切换、模型切换、快捷键弹窗 |

## 快速开始

### 1. 获取 API Key

前往 [platform.deepseek.com](https://platform.deepseek.com) 注册并获取 API Key。

### 2. 下载

从 [Releases](../../releases) 下载 `DeepSeekChat.exe`。

### 3. 运行

双击 `DeepSeekChat.exe`，首次运行会弹出设置窗口输入 API Key。
之后会自动保存，无需重复配置。

> 也可以在同目录创建 `config.toml` 文件：
> ```toml
> api_key = "sk-xxxxxxxxxxxxxxxx"
> default_text_model = "deepseek-chat"
> ```

## 从源码运行

```bash
pip install openai tomli
python DeepSeekChat.py
```

## 打包为 EXE

```bash
pip install pyinstaller
python -m PyInstaller --onefile --windowed --name DeepSeekChat DeepSeekChat.py
# 输出在 dist/DeepSeekChat.exe
```

或双击 `build.bat`。

## 用户命令

在输入框以 `/` 开头：

| 命令 | 说明 |
|------|------|
| `/read <路径>` | 读取文件或列出目录 |
| `/write <路径>` | 弹出编辑器写文件 |
| `/run <命令>` | 执行 Shell 命令 |
| `/py <代码>` | 执行 Python 代码 |
| `/grep <模式> [路径]` | 正则搜索代码 |
| `/find <文件名>` | 搜索文件 |
| `/git <操作>` | Git 操作 |
| `/search <关键词>` | Web 搜索 |
| `/test` | 运行项目测试 |
| `/agent <任务>` | 创建子代理并行执行 |
| `/agents` | 查看子代理状态 |
| `/compress` | 手动压缩上下文 |
| `/save [名称]` | 保存当前对话 |
| `/load [名称]` | 加载历史对话 |
| `/list` | 列出已保存会话 |

## AI 自动工具

AI 在回复中使用标记自动调用工具：

```
[READ: 路径] [WRITE: 路径]内容[/WRITE] [RUN: 命令] [PY: 代码]
[GIT: 操作] [GREP: 模式] [FIND: 文件名] [SEARCH: 关键词]
[AGENT: 任务描述]
```

## 界面

```
┌──────────────────────────────────────────────────────────────────┐
│ 🧠 DeepSeekChat                    Agent  Pro                   │  
│ 💡 /help  | ⌨️ 快捷键                      [✕]                  │
├─────────────┬────────────────────────────────────────────────────┤
│[📋流程][📌任务][🤖代理]    │                                    │
│                            │            聊天区域                 │
│                            │                                    │
│                            ├────────────────────────────────────┤
│                            │        输入框              [发送]   │
├─────────────┴───────────────────────────────────────────────────┤
│ Tok: 1.2K │ ¥0.004 │ 轮 3      Agent·Pro                        │
└──────────────────────────────────────────────────────────────────┘
```

## 快捷键

| 按键 | 功能 |
|------|------|
| Enter | 发送消息 |
| Shift + Enter | 换行 |
| Ctrl + Enter | 发送消息 |
| 点击蓝色标签 | 切换模式 |
| 点击绿色标签 | 切换模型 |
| 点击 ⌨️ | 查看全部快捷键 |

## 技术栈

- Python 3.11+
- tkinter (GUI)
- OpenAI Python SDK (DeepSeek API)
- PyInstaller (打包)

## License

MIT
