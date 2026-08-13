# CursorChatManager

<p align="center">
  <strong>Cursor 对话管理器</strong><br/>
  浏览 / 硬删除 / 备份恢复 · Windows 本地桌面工具
</p>

<p align="center">
  <a href="https://github.com/LRZX36/CursorChatManager/releases"><img alt="Release" src="https://img.shields.io/github/v/release/LRZX36/CursorChatManager?style=flat-square&color=2ea44f"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white">
  <img alt="Platform" src="https://img.shields.io/badge/平台-Windows-0078D4?style=flat-square&logo=windows&logoColor=white">
  <img alt="UI" src="https://img.shields.io/badge/UI-CustomTkinter-1F6AA5?style=flat-square">
</p>

---

## 使用前准备

1. 已安装 **Cursor**
2. **删除或恢复前必须完全退出 Cursor**（托盘图标也要关掉）
3. Windows 10 / 11（x64）

---

## 安装与使用

### 方式一：下载发布包（推荐）

1. 打开 [Releases](https://github.com/LRZX36/CursorChatManager/releases) 页面
2. 下载最新的 `CursorChatManager-vX.Y.Z.exe`
3. 双击运行（无需安装）
4. 在工具中浏览、删除或恢复对话

### 方式二：从源码运行

```powershell
git clone https://github.com/LRZX36/CursorChatManager.git
cd CursorChatManager
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

### 打包 exe

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pyinstaller build.spec
```

产物：`dist\CursorChatManager.exe`

---

## 功能一览

| 功能 | 说明 |
|------|------|
| 列出对话 | 默认只显示侧栏可见的主对话 |
| 筛选显示 | 可选显示僵尸残留 / 系统草稿 / 空标签 / 子代理 |
| 预览内容 | 查看对话消息预览 |
| 硬删除 | 删除 header + bubbles + checkpoints + 搜索索引 + transcripts |
| 清理僵尸 | 一键清理已归档且无消息的残留 |
| VACUUM | 删除后可选回收磁盘空间 |
| 恢复备份 | 选择历史备份一键还原，显示备份时间与有效对话条数 |

---

## 还原备份

1. **完全退出 Cursor**（含托盘）
2. 在本工具底部点击「恢复备份」
3. 列表会显示每份备份的**时间**与**有效对话条数**，选中后确认恢复
4. 恢复前会自动再备份一份当前 `state.vscdb`；完成后重新启动 Cursor

也可手动：将 `%USERPROFILE%\.cursor-chat-manager\backups\<时间戳>\` 中的 `state.vscdb` / `-wal` / `-shm`（及 `conversation-search.db` 若有）复制回 `%APPDATA%\Cursor\User\globalStorage\`。

---

## 数据位置

| 路径 | 用途 |
|------|------|
| `%APPDATA%\Cursor\User\globalStorage\state.vscdb` | 对话正文与 `composerHeaders` 索引 |
| `%APPDATA%\Cursor\User\globalStorage\conversation-search.db` | 搜索索引 |
| `%APPDATA%\Cursor\User\workspaceStorage\*\workspace.json` | 项目路径映射 |
| `%USERPROFILE%\.cursor\projects\*\agent-transcripts\` | 文本日志（删除时一并清理） |
| `%USERPROFILE%\.cursor-chat-manager\backups\` | 本工具自动备份目录 |

---

## 项目结构

```text
CursorChatManager/
├── main.py                      # 程序入口
├── build.spec                   # PyInstaller 打包配置
├── requirements.txt
└── src/cursor_chat_manager/
    ├── app.py                   # CustomTkinter 界面
    ├── store.py                 # 对话读写 / 删除
    ├── backup.py                # 备份创建 / 列表 / 恢复
    ├── paths.py                 # Cursor 与备份路径
    └── process.py               # 检测 Cursor 是否运行
```

---

## 注意事项

- 删除或恢复前请**完全退出 Cursor**；运行中仅允许浏览
- 每次删除会自动备份到 `%USERPROFILE%\.cursor-chat-manager\backups\<时间戳>\`
- 不要手动删除整个 `state.vscdb`，可能导致对话卡在 Loading
- 本项目为非官方社区工具，与 Cursor 官方无关
- 因修改本地对话数据库带来的风险请自行评估

---

## Changelog

### v1.1.0

- 新增「恢复备份」：可选历史备份并一键还原
- 备份列表展示备份时间与有效对话条数（与主界面默认列表一致）
- 恢复前自动安全备份当前数据

### v1.0.0

- 首次发布：浏览、预览、硬删除、清理僵尸、自动备份、VACUUM
