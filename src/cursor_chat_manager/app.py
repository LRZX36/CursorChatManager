"""CustomTkinter desktop UI for Cursor Chat Manager."""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk

import customtkinter as ctk

from . import __version__, backup as backup_mod, paths
from .process import is_cursor_running
from .store import (
    ChatKind,
    ChatSummary,
    CursorBusyError,
    StoreNotFoundError,
    delete_chats,
    format_size,
    format_ts,
    get_preview,
    list_chats,
    list_zombie_ids,
)


def run() -> None:
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    app = ChatManagerApp()
    app.mainloop()


class ChatManagerApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"Cursor 对话管理器  v{__version__}")
        self.geometry("1100x680")
        self.minsize(900, 520)

        self._chats: list[ChatSummary] = []
        self._id_to_chat: dict[str, ChatSummary] = {}
        self._show_hidden = ctk.BooleanVar(value=False)
        self._show_subagents = ctk.BooleanVar(value=False)
        self._vacuum = ctk.BooleanVar(value=True)

        self._build()
        self.after(100, self.refresh)

    def _build(self) -> None:
        self.grid_columnconfigure(0, weight=3)
        self.grid_columnconfigure(1, weight=2)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 6))
        top.grid_columnconfigure(6, weight=1)

        ctk.CTkButton(top, text="刷新", width=80, command=self.refresh).grid(
            row=0, column=0, padx=(0, 8)
        )
        ctk.CTkCheckBox(
            top,
            text="显示僵尸/草稿/空标签",
            variable=self._show_hidden,
            command=self._reload_table,
        ).grid(row=0, column=1, padx=8)
        ctk.CTkCheckBox(
            top,
            text="显示子代理",
            variable=self._show_subagents,
            command=self._reload_table,
        ).grid(row=0, column=2, padx=8)
        ctk.CTkCheckBox(
            top, text="删除后 VACUUM", variable=self._vacuum
        ).grid(row=0, column=3, padx=8)

        self.status_label = ctk.CTkLabel(top, text="", anchor="e")
        self.status_label.grid(row=0, column=6, sticky="e")

        # Left: table
        left = ctk.CTkFrame(self)
        left.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=6)
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Chat.Treeview",
            rowheight=28,
            font=("Segoe UI", 10),
        )
        style.configure("Chat.Treeview.Heading", font=("Segoe UI", 10, "bold"))

        cols = ("name", "kind", "project", "bubbles", "size", "updated")
        self.tree = ttk.Treeview(
            left,
            columns=cols,
            show="headings",
            selectmode="extended",
            style="Chat.Treeview",
        )
        self.tree.heading("name", text="标题")
        self.tree.heading("kind", text="类型")
        self.tree.heading("project", text="项目")
        self.tree.heading("bubbles", text="消息")
        self.tree.heading("size", text="大小")
        self.tree.heading("updated", text="更新时间")
        self.tree.column("name", width=220, minwidth=120)
        self.tree.column("kind", width=80, minwidth=60, stretch=False)
        self.tree.column("project", width=220, minwidth=100)
        self.tree.column("bubbles", width=50, minwidth=40, stretch=False, anchor="e")
        self.tree.column("size", width=70, minwidth=50, stretch=False, anchor="e")
        self.tree.column("updated", width=120, minwidth=90, stretch=False)

        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        # Right: preview
        right = ctk.CTkFrame(self)
        right.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=6)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.detail_title = ctk.CTkLabel(
            right, text="选择左侧对话查看预览", anchor="w", font=ctk.CTkFont(size=14, weight="bold")
        )
        self.detail_title.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))

        self.preview = ctk.CTkTextbox(right, wrap="word", font=ctk.CTkFont(family="Consolas", size=12))
        self.preview.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        self.preview.configure(state="disabled")

        # Bottom actions
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(6, 12))

        ctk.CTkButton(
            bottom,
            text="删除选中",
            fg_color="#b42318",
            hover_color="#912018",
            command=self.delete_selected,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            bottom,
            text="清理全部僵尸",
            command=self.clean_zombies,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            bottom,
            text="恢复备份",
            command=self.restore_backup_dialog,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            bottom,
            text="打开备份目录",
            command=self.open_backups,
        ).pack(side="left", padx=8)

        self.hint = ctk.CTkLabel(
            bottom,
            text="删除/恢复前请完全退出 Cursor。操作会先自动备份 state.vscdb。",
            anchor="e",
            text_color=("gray40", "gray70"),
        )
        self.hint.pack(side="right")

    def _set_status(self) -> None:
        running = is_cursor_running()
        db = paths.state_vscdb()
        db_ok = db.is_file()
        if not db_ok:
            text = "未找到 Cursor 对话库"
            color = "#b42318"
        elif running:
            text = "Cursor 运行中（可浏览；删除前请退出）"
            color = "#b54708"
        else:
            text = "Cursor 已退出 — 可安全删除"
            color = "#067647"
        visible = len(self._filtered())
        text = f"{text}  |  显示 {visible} / 共 {len(self._chats)} 条"
        self.status_label.configure(text=text, text_color=color)

    def refresh(self) -> None:
        try:
            self._chats = list_chats(include_hidden=True)
        except StoreNotFoundError as exc:
            self._chats = []
            messagebox.showerror("找不到数据库", str(exc), parent=self)
        except Exception as exc:  # noqa: BLE001
            self._chats = []
            messagebox.showerror("读取失败", str(exc), parent=self)
        self._id_to_chat = {c.composer_id: c for c in self._chats}
        self._reload_table()
        self._set_status()

    def _filtered(self) -> list[ChatSummary]:
        out: list[ChatSummary] = []
        for c in self._chats:
            if c.kind == ChatKind.SUBAGENT and not self._show_subagents.get():
                continue
            if c.is_default_hidden and not self._show_hidden.get():
                continue
            # When show_hidden is off, also hide orphans with 0 bubbles? Keep orphans if main-like
            if c.kind == ChatKind.ORPHAN and c.bubble_count == 0 and not self._show_hidden.get():
                continue
            out.append(c)
        return out

    def _reload_table(self) -> None:
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for c in self._filtered():
            project = c.workspace_path or c.workspace_id or "-"
            if project and len(project) > 48:
                project = "…" + project[-47:]
            iid = c.composer_id
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    c.name,
                    c.kind_label,
                    project,
                    c.bubble_count,
                    format_size(c.size_bytes),
                    format_ts(c.last_updated_at or c.created_at),
                ),
            )
            if iid in selected:
                self.tree.selection_add(iid)
        self._set_status()

    def _on_select(self, _event: object = None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        cid = sel[0]
        chat = self._id_to_chat.get(cid)
        if not chat:
            return
        self.detail_title.configure(text=f"{chat.name}  ({chat.kind_label})")
        lines = [
            f"ID: {chat.composer_id}",
            f"项目: {chat.workspace_path or chat.workspace_id or '-'}",
            f"模式: {chat.unified_mode or '-'}",
            f"消息: {chat.bubble_count}    大小: {format_size(chat.size_bytes)}",
            f"归档: {chat.is_archived}    子代理: {chat.is_subagent}",
            "-" * 40,
        ]
        try:
            previews = get_preview(cid, limit=10)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"(预览失败: {exc})")
            previews = []
        if not previews:
            lines.append("(无消息内容)")
        for p in previews:
            role = {1: "用户", 2: "助手"}.get(p.type or -1, f"类型{p.type}")
            text = (p.text or "").strip() or "(空)"
            if len(text) > 800:
                text = text[:800] + "…"
            lines.append(f"[{role}]\n{text}\n")
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", "\n".join(lines))
        self.preview.configure(state="disabled")

    def _selected_ids(self) -> list[str]:
        return list(self.tree.selection())

    def delete_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo("未选择", "请先选择要删除的对话。", parent=self)
            return
        names = [self._id_to_chat[i].name for i in ids if i in self._id_to_chat]
        preview = "\n".join(f"• {n}" for n in names[:12])
        if len(names) > 12:
            preview += f"\n… 共 {len(names)} 条"
        if is_cursor_running():
            messagebox.showerror(
                "请先退出 Cursor",
                "检测到 Cursor 仍在运行。\n请完全退出 Cursor（托盘图标也要退出）后再删除。",
                parent=self,
            )
            self._set_status()
            return
        ok = messagebox.askyesno(
            "确认硬删除",
            f"将永久删除以下 {len(ids)} 条对话（不可从本工具恢复，仅有本地备份）：\n\n{preview}\n\n"
            "会先备份 state.vscdb。确定继续？",
            parent=self,
            icon="warning",
        )
        if not ok:
            return
        self._do_delete(ids)

    def clean_zombies(self) -> None:
        zombies = list_zombie_ids()
        if not zombies:
            messagebox.showinfo("无需清理", "没有发现已归档且无消息的僵尸残留。", parent=self)
            return
        if is_cursor_running():
            messagebox.showerror(
                "请先退出 Cursor",
                "检测到 Cursor 仍在运行。请完全退出后再清理。",
                parent=self,
            )
            return
        ok = messagebox.askyesno(
            "清理僵尸",
            f"将删除 {len(zombies)} 条僵尸残留（archived 且无消息）。\n会先自动备份。继续？",
            parent=self,
        )
        if not ok:
            return
        self._do_delete(zombies)

    def _do_delete(self, ids: list[str]) -> None:
        try:
            result = delete_chats(ids, vacuum=bool(self._vacuum.get()))
        except CursorBusyError as exc:
            messagebox.showerror("Cursor 仍在运行", str(exc), parent=self)
            return
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("删除失败", str(exc), parent=self)
            return

        msg = f"已删除 {len(result.deleted_ids)} 条。"
        if result.backup_path:
            msg += f"\n备份: {result.backup_path}"
        if result.vacuumed:
            msg += "\n已执行 VACUUM。"
        if result.errors:
            msg += "\n部分错误:\n" + "\n".join(result.errors[:8])
        messagebox.showinfo("完成", msg, parent=self)
        self.refresh()

    def open_backups(self) -> None:
        path = paths.backups_dir()
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)

    def restore_backup_dialog(self) -> None:
        backups = backup_mod.list_backups()
        if not backups:
            messagebox.showinfo(
                "无备份",
                f"尚未找到备份。\n备份目录: {paths.backups_dir()}",
                parent=self,
            )
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("恢复备份")
        dialog.geometry("640x420")
        dialog.minsize(520, 320)
        dialog.transient(self)
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            dialog,
            text="选择要恢复的备份（将覆盖当前 Cursor 对话库）",
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))

        list_frame = ctk.CTkFrame(dialog)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=14, pady=6)
        list_frame.grid_rowconfigure(0, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)

        style = ttk.Style(dialog)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Backup.Treeview", rowheight=28, font=("Segoe UI", 10))
        style.configure("Backup.Treeview.Heading", font=("Segoe UI", 10, "bold"))

        cols = ("time", "chats", "size", "folder")
        tree = ttk.Treeview(
            list_frame,
            columns=cols,
            show="headings",
            selectmode="browse",
            style="Backup.Treeview",
        )
        tree.heading("time", text="备份时间")
        tree.heading("chats", text="对话条数")
        tree.heading("size", text="大小")
        tree.heading("folder", text="目录名")
        tree.column("time", width=160, minwidth=120, stretch=False)
        tree.column("chats", width=80, minwidth=60, stretch=False, anchor="e")
        tree.column("size", width=80, minwidth=60, stretch=False, anchor="e")
        tree.column("folder", width=220, minwidth=100)

        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

        by_iid: dict[str, backup_mod.BackupInfo] = {}
        for info in backups:
            iid = info.stamp
            by_iid[iid] = info
            tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    info.time_label,
                    info.chat_count_label,
                    format_size(info.size_bytes),
                    info.stamp,
                ),
            )
        if backups:
            tree.selection_set(backups[0].stamp)
            tree.focus(backups[0].stamp)

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.grid(row=2, column=0, sticky="ew", padx=14, pady=(6, 14))

        def do_restore() -> None:
            sel = tree.selection()
            if not sel:
                messagebox.showinfo("未选择", "请先选择一个备份。", parent=dialog)
                return
            info = by_iid.get(sel[0])
            if not info:
                return
            if is_cursor_running():
                messagebox.showerror(
                    "请先退出 Cursor",
                    "检测到 Cursor 仍在运行。\n请完全退出 Cursor（托盘图标也要退出）后再恢复。",
                    parent=dialog,
                )
                return
            ok = messagebox.askyesno(
                "确认恢复备份",
                f"将用以下备份覆盖当前对话库：\n\n"
                f"时间: {info.time_label}\n"
                f"对话条数: {info.chat_count_label}\n"
                f"目录: {info.path}\n\n"
                "恢复前会再备份一份当前数据。确定继续？",
                parent=dialog,
                icon="warning",
            )
            if not ok:
                return
            try:
                safety = backup_mod.restore_backup(info.path, safety_backup=True)
            except backup_mod.CursorBusyError as exc:
                messagebox.showerror("Cursor 仍在运行", str(exc), parent=dialog)
                return
            except backup_mod.BackupNotFoundError as exc:
                messagebox.showerror("备份无效", str(exc), parent=dialog)
                return
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("恢复失败", str(exc), parent=dialog)
                return

            msg = f"已恢复备份：{info.time_label}\n对话条数: {info.chat_count_label}"
            if safety:
                msg += f"\n恢复前安全备份: {safety}"
            messagebox.showinfo("恢复完成", msg, parent=dialog)
            dialog.destroy()
            self.refresh()

        ctk.CTkButton(btn_row, text="取消", width=90, command=dialog.destroy).pack(
            side="right", padx=(8, 0)
        )
        ctk.CTkButton(
            btn_row,
            text="恢复选中",
            width=110,
            fg_color="#067647",
            hover_color="#05603a",
            command=do_restore,
        ).pack(side="right")
