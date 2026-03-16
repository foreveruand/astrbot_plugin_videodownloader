"""
AstrBot Video Downloader Plugin - Download videos and audio using yt-dlp.

This plugin provides video/audio download functionality from various platforms
using yt-dlp, with optional rclone upload support.
"""

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File, Record, Video
from astrbot.api.util import SessionController, session_waiter
from astrbot.core.utils.path_utils import (
    get_data_dir,
    get_plugin_data_dir,
    get_temp_dir,
)

from .downloader import determine_filename, download_file, download_with_yt_dlp
from .rclone import rclone_move_directory, rclone_transfer

logger = logging.getLogger("astrbot")

SESSION_STATE: dict[str, dict[str, Any]] = {}
SESSION_TIMEOUT = 300
MAX_RETRIES = 4


class Main(star.Star):
    """Main class for the Video Downloader plugin."""

    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        super().__init__(context, config)
        self.context = context
        self.config = config
        self._initialized = False

    async def initialize(self) -> None:
        """Called when the plugin is activated."""
        if self._initialized:
            return

        archive_path = get_data_dir() / "archive.txt"
        archive_path.parent.mkdir(parents=True, exist_ok=True)

        self._initialized = True
        logger.info("Video Downloader plugin initialized successfully")

    async def terminate(self) -> None:
        """Called when the plugin is disabled or reloaded."""
        logger.info("Video Downloader plugin terminated")

    def _get_download_folders(self) -> list[str]:
        """Get download folders based on rclone setting."""
        if self.config.get("rclone_upload", False):
            return self.config.get("rclone_folders", [])
        return self.config.get("download_folders", [])

    def _is_url(self, text: str) -> bool:
        return bool(re.match(r"^https?://", text))

    def _is_telegram_file_url(self, url: str) -> bool:
        return "/file/bot" in url

    def _collect_file_sources(self, event: AstrMessageEvent) -> tuple[list[str], str]:
        file_urls: list[str] = []
        filename_hint = ""

        for component in event.get_messages():
            if isinstance(component, File):
                if component.name and not filename_hint:
                    filename_hint = component.name
                if component.url:
                    file_urls.append(component.url)
                elif component.file:
                    file_urls.append(component.file)
            elif isinstance(component, Video):
                candidate = getattr(component, "file", "")
                if candidate:
                    file_urls.append(candidate)
                path_hint = getattr(component, "path", "")
                if path_hint and not filename_hint:
                    filename_hint = Path(path_hint).name
            elif isinstance(component, Record):
                candidate = getattr(component, "file", "")
                if candidate:
                    file_urls.append(candidate)

        normalized: list[str] = []
        for url in file_urls:
            if url.startswith("file://"):
                normalized.append(url[7:])
            else:
                normalized.append(url)

        return normalized, filename_hint

    def _build_selection_message(self, session_id: str, selected_idx: int = 0) -> str:
        """Build the folder selection message."""
        folders = self._get_download_folders()

        lines = ["请选择目录：\n"]
        for idx, folder in enumerate(folders, start=1):
            marker = "✅ " if idx - 1 == selected_idx else ""
            lines.append(f"{marker}{idx}. {folder}")

        state = SESSION_STATE.get(session_id, {})
        enable_archive = state.get(
            "enable_archive", self.config.get("enable_archive", True)
        )
        use_proxy = state.get("use_proxy", self.config.get("video_proxy", False))
        separate_folder = state.get(
            "video_separate_folder",
            self.config.get("video_seperate_folder", False),
        )
        default_action = state.get("default_action", "video")

        lines.append("\n配置选项：")
        lines.append(f"- 存档: {'开' if enable_archive else '关'}")
        lines.append(f"- 代理: {'开' if use_proxy else '关'}")
        lines.append(f"- 独立文件夹: {'开' if separate_folder else '关'}")
        lines.append(f"- 默认下载: {'音频' if default_action == 'audio' else '视频'}")

        lines.append("\n回复说明：")
        lines.append(f"- 数字(1-{len(folders)}): 选择目录")
        lines.append("- 存档: 切换存档选项")
        lines.append("- 代理: 切换代理选项")
        lines.append("- 独立文件夹: 切换目录结构")
        lines.append("- 视频: 开始下载视频")
        lines.append("- 音频: 仅下载音频")
        lines.append("- 开始: 使用默认模式下载")
        lines.append("- 取消: 退出当前会话")

        return "\n".join(lines)

    def _init_session_state(
        self, session_id: str, default_action: str
    ) -> dict[str, Any]:
        state = {
            "stage": "select",
            "mode": "yt-dlp",
            "url": "",
            "file_urls": [],
            "filename_hint": "",
            "selected_folder_idx": 0,
            "enable_archive": self.config.get("enable_archive", True),
            "use_proxy": self.config.get("video_proxy", False),
            "video_separate_folder": self.config.get("video_seperate_folder", False),
            "default_action": default_action,
        }
        SESSION_STATE[session_id] = state
        return state

    async def _start_command(self, event: AstrMessageEvent, default_action: str):
        await self.initialize()

        message = event.message_str.strip()
        command = "audio" if default_action == "audio" else "video"
        args_text = message.replace(command, "", 1).strip()

        session_id = str(event.unified_msg_origin)
        state = self._init_session_state(session_id, default_action)

        file_urls, filename_hint = self._collect_file_sources(event)
        if filename_hint:
            state["filename_hint"] = filename_hint

        if args_text and self._is_url(args_text):
            if self._is_telegram_file_url(args_text):
                state.update({"mode": "file", "file_urls": [args_text]})
            else:
                state.update({"mode": "yt-dlp", "url": args_text})
        elif args_text:
            state["filename_hint"] = args_text
            if file_urls:
                state.update({"mode": "file", "file_urls": file_urls})
            else:
                state["stage"] = "await_file"
        elif file_urls:
            state.update({"mode": "file", "file_urls": file_urls})
        else:
            usage = (
                "用法：/video <链接> 或 /video <文件名>\n"
                "支持 YouTube, Bilibili, Twitter 等平台的视频下载。\n"
                "在 Telegram 中可直接发送文件或文件链接。"
            )
            yield event.plain_result(usage)
            return

        if state["stage"] == "await_file":
            yield event.plain_result(
                "📤 请上传文件或发送下载链接。\n"
                f"保存文件名：{state['filename_hint']}\n"
                "回复 '取消' 退出。"
            )
        else:
            msg = self._build_selection_message(session_id)
            yield event.plain_result(msg)

        @session_waiter(timeout=SESSION_TIMEOUT)
        async def wait_for_reply(
            controller: SessionController, reply_event: AstrMessageEvent
        ) -> None:
            nonlocal session_id
            current_state = SESSION_STATE.get(session_id)
            if not current_state:
                controller.stop()
                return

            reply_text = reply_event.message_str.strip()

            if reply_text.lower() in ("取消", "cancel", "退出", "exit"):
                await reply_event.send(reply_event.plain_result("已取消操作。"))
                SESSION_STATE.pop(session_id, None)
                controller.stop()
                return

            if current_state["stage"] == "await_file":
                file_urls, filename_hint = self._collect_file_sources(reply_event)
                if filename_hint and not current_state.get("filename_hint"):
                    current_state["filename_hint"] = filename_hint

                if reply_text and self._is_url(reply_text):
                    if self._is_telegram_file_url(reply_text):
                        current_state.update(
                            {"mode": "file", "file_urls": [reply_text]}
                        )
                    else:
                        current_state.update({"mode": "yt-dlp", "url": reply_text})
                elif file_urls:
                    current_state.update({"mode": "file", "file_urls": file_urls})
                else:
                    await reply_event.send(
                        reply_event.plain_result(
                            "请发送文件或有效的下载链接，或回复 '取消' 退出。"
                        )
                    )
                    return

                current_state["stage"] = "select"
                msg = self._build_selection_message(
                    session_id, current_state.get("selected_folder_idx", 0)
                )
                await reply_event.send(reply_event.plain_result(msg))
                return

            folders = self._get_download_folders()

            if reply_text.isdigit():
                idx = int(reply_text) - 1
                if 0 <= idx < len(folders):
                    current_state["selected_folder_idx"] = idx
                    msg = self._build_selection_message(session_id, idx)
                    await reply_event.send(reply_event.plain_result(msg))
                else:
                    await reply_event.send(
                        reply_event.plain_result(
                            f"❌ 无效的目录序号，请选择 1-{len(folders)}"
                        )
                    )
                return

            if reply_text in ("存档",):
                current_state["enable_archive"] = not current_state.get(
                    "enable_archive", True
                )
                msg = self._build_selection_message(
                    session_id, current_state.get("selected_folder_idx", 0)
                )
                await reply_event.send(reply_event.plain_result(msg))
                return

            if reply_text in ("代理",):
                current_state["use_proxy"] = not current_state.get("use_proxy", False)
                msg = self._build_selection_message(
                    session_id, current_state.get("selected_folder_idx", 0)
                )
                await reply_event.send(reply_event.plain_result(msg))
                return

            if reply_text in ("独立", "独立文件夹"):
                current_state["video_separate_folder"] = not current_state.get(
                    "video_separate_folder", False
                )
                msg = self._build_selection_message(
                    session_id, current_state.get("selected_folder_idx", 0)
                )
                await reply_event.send(reply_event.plain_result(msg))
                return

            if reply_text in ("视频", "音频", "开始", "下载"):
                action = reply_text
                if action in ("开始", "下载"):
                    action = (
                        "音频"
                        if current_state.get("default_action") == "audio"
                        else "视频"
                    )
                audio_only = action == "音频"

                if current_state.get("mode") == "file":
                    await self._handle_file_download(reply_event, current_state)
                else:
                    url = current_state.get("url", "")
                    if not url:
                        await reply_event.send(
                            reply_event.plain_result("❌ 未找到下载链接")
                        )
                        return
                    await self._handle_download(
                        reply_event, url, current_state, audio_only
                    )

                controller.stop()
                return

            await reply_event.send(
                reply_event.plain_result("⚠️ 无效输入，请按提示回复或回复 '取消' 退出")
            )

        try:
            await wait_for_reply(event)
        except TimeoutError:
            yield event.plain_result("⏰ 等待超时，操作已取消。")
        finally:
            SESSION_STATE.pop(session_id, None)
            event.stop_event()

    @filter.command("video")
    async def video_command(self, event: AstrMessageEvent):
        """Download video or audio from URL or Telegram file."""
        async for result in self._start_command(event, "video"):
            yield result

    @filter.command("audio")
    async def audio_command(self, event: AstrMessageEvent):
        """Download audio from URL or Telegram file."""
        async for result in self._start_command(event, "audio"):
            yield result

    async def _handle_download(
        self,
        event: AstrMessageEvent,
        url: str,
        state: dict[str, Any],
        audio_only: bool,
    ) -> None:
        """Handle the actual download process."""
        folders = self._get_download_folders()
        selected_idx = state.get("selected_folder_idx", 0)
        enable_archive = state.get("enable_archive", True)
        use_proxy = state.get("use_proxy", False)
        separate_folder = state.get("video_separate_folder", False)

        if not folders:
            await event.send(event.plain_result("❌ 未配置下载目录"))
            return

        select_path = folders[selected_idx]

        if self.config.get("rclone_upload", False):
            download_folder = get_temp_dir() / "video_downloader"
        else:
            download_folder = Path(select_path)
        download_folder.mkdir(parents=True, exist_ok=True)

        await event.send(event.plain_result("⏳ 开始下载..."))

        if separate_folder:
            outtmpl = str(
                download_folder
                / "%(uploader)s"
                / "%(title).20s"
                / "%(title).100s.%(ext)s"
            )
        else:
            outtmpl = str(
                download_folder
                / "%(uploader)s"
                / "%(uploader)s投稿"
                / "%(title).100s.%(ext)s"
            )

        # Get cookie file path
        cookie_file_config = self.config.get("cookie_file", [])
        if cookie_file_config and isinstance(cookie_file_config, list):
            cookie_file = str(
                get_plugin_data_dir("astrbot_plugin_videodownloader")
                / cookie_file_config[0]
            )
        else:
            cookie_file = ""
        proxy_url = self.config.get("video_proxy_url", "") if use_proxy else ""
        archive_path = str(get_data_dir() / "archive.txt")

        attempt = 0
        downloaded_files: list[str] = []
        failed = False

        while attempt < MAX_RETRIES:
            downloaded_files = []

            async for state_type, data in download_with_yt_dlp(
                url,
                outtmpl,
                cookie_file,
                proxy_url,
                audio_only,
                enable_archive,
                archive_path,
            ):
                if state_type == "progress":
                    await event.send(event.plain_result(f"📥 下载中：{data}"))
                elif state_type == "save_path":
                    downloaded_files.append(data)
                elif state_type == "failed":
                    failed = True
                    await event.send(
                        event.plain_result(
                            f"❌ 下载失败：{data}，重试 {attempt + 1}/{MAX_RETRIES}"
                        )
                    )
                    break
                elif state_type == "success":
                    if not downloaded_files:
                        await event.send(event.plain_result(f"✅ 下载完成：{data}"))
                    break

            if failed:
                attempt += 1
                await asyncio.sleep(5)
                continue

            if downloaded_files:
                await self._process_downloaded_files(
                    event, downloaded_files, download_folder, select_path
                )
            else:
                if self.config.get("rclone_upload", False):
                    await self._handle_rclone_directory_transfer(
                        event, download_folder, select_path
                    )

            break

        if attempt >= MAX_RETRIES:
            await event.send(event.plain_result("❌ 下载失败，已达到最大重试次数"))

    async def _handle_file_download(
        self, event: AstrMessageEvent, state: dict[str, Any]
    ) -> None:
        file_urls = state.get("file_urls", [])
        if not file_urls:
            await event.send(event.plain_result("❌ 未找到可下载的文件"))
            return

        folders = self._get_download_folders()
        if not folders:
            await event.send(event.plain_result("❌ 未配置下载目录"))
            return

        selected_idx = state.get("selected_folder_idx", 0)
        select_path = folders[selected_idx]

        if self.config.get("rclone_upload", False):
            download_folder = get_temp_dir() / "video_downloader"
        else:
            download_folder = Path(select_path)
        download_folder.mkdir(parents=True, exist_ok=True)

        filename_hint = state.get("filename_hint", "")
        downloaded_files: list[str] = []

        for idx, file_url in enumerate(file_urls, start=1):
            filename = determine_filename(filename_hint, [file_url])
            if len(file_urls) > 1:
                stem = Path(filename).stem
                suffix = Path(filename).suffix
                filename = f"{stem}_{idx}{suffix}"

            save_path = download_folder / filename
            await event.send(
                event.plain_result(
                    f"📥 正在下载文件 {idx}/{len(file_urls)}: {filename}"
                )
            )

            success, _ = await download_file(file_url, save_path)
            if success:
                downloaded_files.append(str(save_path))
            else:
                await event.send(event.plain_result(f"❌ 文件下载失败: {filename}"))

        if downloaded_files:
            await self._process_downloaded_files(
                event, downloaded_files, download_folder, select_path
            )
        else:
            await event.send(event.plain_result("❌ 所有文件下载失败"))

    async def _process_downloaded_files(
        self,
        event: AstrMessageEvent,
        downloaded_files: list[str],
        download_folder: Path,
        select_path: str,
    ) -> None:
        """Process downloaded files and optionally transfer via rclone."""
        results = []

        for local_path in downloaded_files:
            local_path_obj = Path(local_path).resolve()

            if self.config.get("rclone_upload", False):
                try:
                    rel_path = local_path_obj.relative_to(download_folder)
                except ValueError:
                    rel_path = Path(os.path.relpath(local_path_obj, download_folder))
                remote_dir = rel_path.parent
                remote_name = self.config.get("rclone_server", "")
                remote_path = (
                    str(Path(select_path) / remote_dir)
                    if str(remote_dir) not in ("", ".")
                    else select_path
                )

                await event.send(
                    event.plain_result(
                        f"✅ 下载完成：{local_path_obj.name}，正在传输..."
                    )
                )

                async for state_type, data in rclone_transfer(
                    local_path_obj, remote_name, remote_path
                ):
                    if state_type == "progress":
                        await event.send(event.plain_result(f"📤 传输中：{data}"))
                    elif state_type == "success":
                        results.append(f"✅ {local_path_obj.name}")
                    elif state_type == "failed":
                        results.append(f"❌ {local_path_obj.name}: {data}")
            else:
                results.append(f"✅ {local_path_obj}")

        await event.send(
            event.plain_result("\n".join(results) if results else "下载完成")
        )

    async def _handle_rclone_directory_transfer(
        self,
        event: AstrMessageEvent,
        download_folder: Path,
        select_path: str,
    ) -> None:
        """Handle rclone transfer of entire directory."""
        remote_name = self.config.get("rclone_server", "")

        await event.send(event.plain_result("📤 正在传输文件夹..."))

        async for state_type, data in rclone_move_directory(
            download_folder, remote_name, select_path
        ):
            if state_type == "progress":
                await event.send(event.plain_result(f"📤 传输中：{data}"))
            elif state_type == "success":
                await event.send(event.plain_result(f"✅ 传输完成：{data}"))
            elif state_type == "failed":
                await event.send(event.plain_result(f"❌ 传输失败：{data}"))
