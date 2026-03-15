"""
Rclone transfer module for remote storage upload.

This module provides functionality to transfer downloaded files to remote storage using rclone.
"""

import asyncio
import logging
import re
from collections.abc import AsyncGenerator
from pathlib import Path

logger = logging.getLogger("astrbot")


def format_rclone_progress(line: str) -> str | None:
    """Parse rclone progress output.

    Args:
        line: A single line of rclone output

    Returns:
        Formatted progress string or None
    """
    # Match transfer progress like: "  0% / 1.000 GiB, 0 B/s, ETA -"
    # Or: " 50% / 500.000 MiB, 10.000 MiB/s, ETA 0s"
    progress_pattern = re.compile(
        r"\s*(\d+(?:\.\d+)?)%\s*/\s*([\d.]+\s*[KMGT]?i?B),\s*([\d.]+\s*[KMGT]?i?B/s),\s*ETA\s*(.+)"
    )
    match = progress_pattern.search(line)
    if match:
        percent = match.group(1)
        total = match.group(2)
        speed = match.group(3)
        eta = match.group(4)
        return f"Transfer: {percent}% | Size: {total} | Speed: {speed} | ETA: {eta}"

    return None


async def rclone_transfer(
    local_path: str | Path,
    remote_name: str,
    remote_path: str,
    stats_interval: str = "2s",
) -> AsyncGenerator[tuple[str, str], None]:
    """Transfer a file to remote storage using rclone.

    Args:
        local_path: Local file path
        remote_name: rclone remote name
        remote_path: Remote directory path
        stats_interval: Stats update interval

    Yields:
        Tuple of (status, data) where status can be:
        - "progress": Transfer progress update
        - "success": Transfer complete
        - "failed": Transfer failed with error message
    """
    local_path = str(local_path)
    remote_full = f"{remote_name}:{remote_path}/"

    cmd = [
        "rclone",
        "move",
        local_path,
        remote_full,
        "-P",
        f"--stats={stats_interval}",
    ]

    logger.info(f"Starting rclone transfer: {local_path} -> {remote_full}")

    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    while True:
        stdout_task = asyncio.create_task(process.stdout.readline())
        stderr_task = asyncio.create_task(process.stderr.readline())

        done, pending = await asyncio.wait(
            {stdout_task, stderr_task}, return_when=asyncio.FIRST_COMPLETED
        )

        for task in done:
            output = await task
            if not output:
                continue

            decoded = output.decode("utf-8", errors="replace").strip()

            if task is stdout_task:
                formatted = format_rclone_progress(decoded)
                if formatted:
                    yield ("progress", formatted)

            elif task is stderr_task:
                if decoded and "ERROR" in decoded.upper():
                    yield ("failed", decoded)

        if process.returncode is not None:
            break

        for task in pending:
            task.cancel()

    await process.wait()

    if process.returncode == 0:
        yield ("success", f"Transfer complete: {remote_full}{Path(local_path).name}")
    else:
        yield ("failed", f"Transfer failed with code {process.returncode}")


async def rclone_move_directory(
    local_dir: str | Path,
    remote_name: str,
    remote_path: str,
    stats_interval: str = "2s",
) -> AsyncGenerator[tuple[str, str], None]:
    """Move entire directory to remote storage using rclone.

    Args:
        local_dir: Local directory path
        remote_name: rclone remote name
        remote_path: Remote directory path
        stats_interval: Stats update interval

    Yields:
        Tuple of (status, data)
    """
    local_dir = str(local_dir)
    remote_full = f"{remote_name}:{remote_path}/"

    cmd = [
        "rclone",
        "move",
        local_dir,
        remote_full,
        "-P",
        f"--stats={stats_interval}",
    ]

    logger.info(f"Starting rclone directory transfer: {local_dir} -> {remote_full}")

    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    while True:
        stdout_task = asyncio.create_task(process.stdout.readline())
        stderr_task = asyncio.create_task(process.stderr.readline())

        done, pending = await asyncio.wait(
            {stdout_task, stderr_task}, return_when=asyncio.FIRST_COMPLETED
        )

        for task in done:
            output = await task
            if not output:
                continue

            decoded = output.decode("utf-8", errors="replace").strip()

            if task is stdout_task:
                formatted = format_rclone_progress(decoded)
                if formatted:
                    yield ("progress", formatted)

            elif task is stderr_task:
                if decoded and "ERROR" in decoded.upper():
                    yield ("failed", decoded)

        if process.returncode is not None:
            break

        for task in pending:
            task.cancel()

    await process.wait()

    if process.returncode == 0:
        yield ("success", f"Directory transfer complete: {remote_full}")
    else:
        yield ("failed", f"Directory transfer failed with code {process.returncode}")
