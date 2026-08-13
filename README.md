# Cursor Chat Manager

Windows 本地桌面工具：浏览并**硬删除** Cursor IDE 对话（含 UI 删除留下的僵尸残留），并支持从本地备份恢复。

当前版本：**v1.1.0**

## 下载

从 [Releases](../../releases) 下载 `CursorChatManager.exe`，无需安装，退出 Cursor 后直接运行。

## 重要

- **删除 / 恢复前必须完全退出 Cursor**（含托盘）。Cursor 运行时本工具只允许浏览。
- 每次删除会自动备份 `%USERPROFILE%\.cursor-chat-manager\backups\<时间戳>\` 下的 `state.vscdb` 等文件。
- 不要手动删除整个 `state.vscdb` 文件，会导致对话卡在 Loading。

## 功能

- 列出对话（默认只显示侧栏可见的主对话）
- 可选显示：僵尸残留 / 系统草稿 / 空标签 / 子代理
- 预览消息内容
- 多选硬删除（header + bubbles + checkpoints + 搜索索引 + transcripts）
- 一键清理全部僵尸
- 删除后可选 VACUUM 回收磁盘
- 从本地备份恢复（可选备份，显示备份时间与有效对话条数）

## 运行（开发）

```powershell
cd C:\Users\lrzx8\Documents\projects\cursorChatmanager
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## 打包 exe

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pyinstaller build.spec
```

产物：`dist\CursorChatManager.exe`

## 数据位置

| 路径 | 用途 |
|------|------|
| `%APPDATA%\Cursor\User\globalStorage\state.vscdb` | 对话正文与 `composerHeaders` 索引 |
| `%APPDATA%\Cursor\User\globalStorage\conversation-search.db` | 搜索索引 |
| `%APPDATA%\Cursor\User\workspaceStorage\*\workspace.json` | 项目路径映射 |
| `%USERPROFILE%\.cursor\projects\*\agent-transcripts\` | 文本日志（删除时一并清理） |

## 还原备份

1. **完全退出 Cursor**（含托盘）。
2. 在本工具底部点击「恢复备份」。
3. 列表会显示每份备份的**时间**与**有效对话条数**，选中后确认恢复。
4. 恢复前会自动再备份一份当前 `state.vscdb`；完成后可重新启动 Cursor。

也可手动：将 `%USERPROFILE%\.cursor-chat-manager\backups\<时间戳>\` 中的 `state.vscdb` / `-wal` / `-shm`（及 `conversation-search.db` 若有）复制回 `%APPDATA%\Cursor\User\globalStorage\`。

## Changelog

### v1.1.0

- 新增「恢复备份」：可选历史备份并一键还原
- 备份列表展示备份时间与有效对话条数（与主界面默认列表一致）
- 恢复前自动安全备份当前数据

### v1.0.0

- 首次发布：浏览、预览、硬删除、清理僵尸、自动备份、VACUUM
