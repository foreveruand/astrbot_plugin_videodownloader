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

from astrbot.api import AstrBotConfig, star
from astrbot.api.event import filter
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .downloader import download_with_yt_dlp
from .rclone import rclone_move_directory, rclone_transfer

logger = logging.getLogger("astrbot")

# Session state storage for interactive selection
SESSION_STATE: dict[str, dict] = {}
MAX_RETRIES = 4


class Main(star.Star):
    """Main class for the Video Downloader plugin."""

    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        self.context = context
        self.config = config
        self._initialized = False

    async def initialize(self) -> None:
        """Called when the plugin is activated."""
        if self._initialized:
            return

        # Create archive directory if needed
        archive_path = Path(get_astrbot_data_path()) / "archive.txt"
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

    def _build_selection_message(self, session_id: str, selected_idx: int = 0) -> str:
        """Build the folder selection message."""
        folders = self._get_download_folders()

        lines = ["请选择目录：\n"]
        for idx, folder in enumerate(folders, start=1):
            marker = "✅ " if idx - 1 == selected_idx else ""
            lines.append(f"{marker}{idx}. {folder}")

        # Get current config
        state = SESSION_STATE.get(session_id, {})
        enable_archive = state.get(
            "enable_archive", self.config.get("enable_archive", True)
        )
        use_proxy = state.get("use_proxy", self.config.get("video_proxy", False))

        lines.append("\n配置选项：")
        lines.append(f"- 存档: {'开' if enable_archive else '关'}")
        lines.append(f"- 代理: {'开' if use_proxy else '关'}")

        lines.append("\n回复说明：")
        lines.append(f"- 数字(1-{len(folders)}): 选择目录")
        lines.append("- 存档: 切换存档选项")
        lines.append("- 代理: 切换代理选项")
        lines.append("- 视频: 开始下载视频")
        lines.append("- 音频: 仅下载音频")

        return "\n".join(lines)

    @filter.command("video")
    async def video_command(self, event: filter.AstrMessageEvent) -> None:
        """Download video or audio from URL.

        Usage:
            /video <url> - Start download selection
            /video - Show selection menu (if in session)
        """
        await self.initialize()

        message = event.message_str.strip()
        url_or_action = message.replace("video", "").strip()

        session_id = str(event.unified_msg_origin)

        # Handle URL input
        if url_or_action and re.match(r"^https?://", url_or_action):
            # Store URL in session
            SESSION_STATE[session_id] = {
                "url": url_or_action,
                "selected_folder_idx": 0,
                "enable_archive": self.config.get("enable_archive", True),
                "use_proxy": self.config.get("video_proxy", False),
            }

            # Show selection menu
            msg = self._build_selection_message(session_id)
            event.set_result(event.make_result().message(msg))
            return

        # Handle session actions
        if session_id in SESSION_STATE:
            state = SESSION_STATE[session_id]
            folders = self._get_download_folders()

            # Handle number selection
            if url_or_action.isdigit():
                idx = int(url_or_action) - 1
                if 0 <= idx < len(folders):
                    state["selected_folder_idx"] = idx
                    msg = self._build_selection_message(session_id, idx)
                    event.set_result(event.make_result().message(msg))
                    return
                else:
                    event.set_result(
                        event.make_result().message(
                            f"❌ 无效的目录序号，请选择 1-{len(folders)}"
                        )
                    )
                    return

            # Handle toggle options
            if url_or_action == "存档":
                state["enable_archive"] = not state.get("enable_archive", True)
                msg = self._build_selection_message(
                    session_id, state["selected_folder_idx"]
                )
                event.set_result(event.make_result().message(msg))
                return

            if url_or_action == "代理":
                state["use_proxy"] = not state.get("use_proxy", False)
                msg = self._build_selection_message(
                    session_id, state["selected_folder_idx"]
                )
                event.set_result(event.make_result().message(msg))
                return

            # Handle download actions
            if url_or_action in ("视频", "音频"):
                url = state.get("url")
                if not url:
                    event.set_result(event.make_result().message("❌ 未找到下载链接"))
                    return

                audio_only = url_or_action == "音频"
                await self._handle_download(event, url, state, audio_only, session_id)
                return

        # Show usage
        if not url_or_action:
            event.set_result(
                event.make_result().message(
                    "用法：/video <链接>\n"
                    "支持 YouTube, Bilibili, Twitter 等平台的视频下载"
                )
            )
        else:
            event.set_result(
                event.make_result().message(f"❌ 无效的链接: {url_or_action}")
            )

    async def _handle_download(
        self,
        event: filter.AstrMessageEvent,
        url: str,
        state: dict,
        audio_only: bool,
        session_id: str,
    ) -> None:
        """Handle the actual download process."""
        folders = self._get_download_folders()
        selected_idx = state.get("selected_folder_idx", 0)
        enable_archive = state.get("enable_archive", True)
        use_proxy = state.get("use_proxy", False)

        if not folders:
            event.set_result(event.make_result().message("❌ 未配置下载目录"))
            return

        select_path = folders[selected_idx]

        # Determine download folder
        if self.config.get("rclone_upload", False):
            download_folder = str(
                Path(get_astrbot_data_path()) / "temp" / "video_downloader"
            )
            Path(download_folder).mkdir(parents=True, exist_ok=True)
        else:
            download_folder = select_path
            Path(download_folder).mkdir(parents=True, exist_ok=True)

        # Send initial progress message
        event.set_result(event.make_result().message("⏳ 开始下载..."))

        # Build output template
        outtmpl = f"{download_folder}/%(uploader)s/%(title).100s.%(ext)s"

        # Get config values
        cookie_file = self.config.get("cookie_file", "")
        proxy_url = self.config.get("video_proxy_url", "") if use_proxy else ""
        archive_path = str(Path(get_astrbot_data_path()) / "archive.txt")

        # Download
        attempt = 0
        downloaded_files = []
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
                    # Update progress message
                    event.set_result(event.make_result().message(f"📥 下载中：{data}"))
                elif state_type == "save_path":
                    downloaded_files.append(data)
                elif state_type == "failed":
                    failed = True
                    event.set_result(
                        event.make_result().message(
                            f"❌ 下载失败：{data}，重试 {attempt + 1}/{MAX_RETRIES}"
                        )
                    )
                    break
                elif state_type == "success":
                    # Download complete without explicit save path
                    if not downloaded_files:
                        event.set_result(
                            event.make_result().message(f"✅ 下载完成：{data}")
                        )
                    break

            if failed:
                attempt += 1
                await asyncio.sleep(5)
                continue

            # Process downloaded files
            if downloaded_files:
                await self._process_downloaded_files(
                    event, downloaded_files, download_folder, select_path
                )
            else:
                # Fallback: transfer entire folder for rclone
                if self.config.get("rclone_upload", False):
                    await self._handle_rclone_directory_transfer(
                        event, download_folder, select_path
                    )

            break

        # Cleanup session
        SESSION_STATE.pop(session_id, None)

        if attempt >= MAX_RETRIES:
            event.set_result(
                event.make_result().message("❌ 下载失败，已达到最大重试次数")
            )

    async def _process_downloaded_files(
        self,
        event: filter.AstrMessageEvent,
        downloaded_files: list[str],
        download_folder: str,
        select_path: str,
    ) -> None:
        """Process downloaded files and optionally transfer via rclone."""
        results = []

        for local_path in downloaded_files:
            local_path = str(Path(local_path).resolve())

            if self.config.get("rclone_upload", False):
                # Calculate relative path
                rel_path = os.path.relpath(local_path, download_folder)
                remote_dir = os.path.dirname(rel_path)
                remote_name = self.config.get("rclone_server", "")

                event.set_result(
                    event.make_result().message(
                        f"✅ 下载完成：{os.path.basename(local_path)}，正在传输..."
                    )
                )

                async for state_type, data in rclone_transfer(
                    local_path, remote_name, os.path.join(select_path, remote_dir)
                ):
                    if state_type == "progress":
                        event.set_result(
                            event.make_result().message(f"📤 传输中：{data}")
                        )
                    elif state_type == "success":
                        results.append(f"✅ {os.path.basename(local_path)}")
                    elif state_type == "failed":
                        results.append(f"❌ {os.path.basename(local_path)}: {data}")
            else:
                results.append(f"✅ {local_path}")

        # Send final result
        event.set_result(
            event.make_result().message("\n".join(results) if results else "下载完成")
        )

    async def _handle_rclone_directory_transfer(
        self,
        event: filter.AstrMessageEvent,
        download_folder: str,
        select_path: str,
    ) -> None:
        """Handle rclone transfer of entire directory."""
        remote_name = self.config.get("rclone_server", "")

        event.set_result(event.make_result().message("📤 正在传输文件夹..."))

        async for state_type, data in rclone_move_directory(
            download_folder, remote_name, select_path
        ):
            if state_type == "progress":
                event.set_result(event.make_result().message(f"📤 传输中：{data}"))
            elif state_type == "success":
                event.set_result(event.make_result().message(f"✅ 传输完成：{data}"))
            elif state_type == "failed":
                event.set_result(event.make_result().message(f"❌ 传输失败：{data}"))
