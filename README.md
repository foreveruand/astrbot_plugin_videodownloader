# AstrBot Video Downloader Plugin

A video/audio downloader plugin for AstrBot using yt-dlp.

## Features

- Download videos from YouTube, Bilibili, Twitter, and many other platforms
- Audio-only download mode
- Download progress display
- Editable Telegram progress updates and throttled progress messages on other platforms
- Configurable download folders
- Optional rclone upload support
- Proxy support
- Download archive to avoid re-downloading
- Telegram file upload support (download files directly)
- Optional separate folder per video

## Installation

1. Place the plugin folder in `data/plugins/astrbot_plugin_videodownloader/`
2. Install dependencies:
   ```bash
   pip install yt-dlp httpx
   ```
3. Restart AstrBot

## Configuration

Configure the plugin in the AstrBot admin panel:

| Configuration | Description |
|--------------|-------------|
| `download_folders` | List of directories for saving downloaded videos |
| `cookie_file` | Path to cookies.txt file for authentication |
| `enable_archive` | Enable download archive to avoid re-downloading |
| `video_seperate_folder` | Create a separate folder per video (default setting) |
| `video_proxy` | Enable proxy for downloads |
| `video_proxy_url` | Proxy URL (e.g., `http://127.0.0.1:7890`) |
| `rclone_upload` | Enable rclone upload |
| `rclone_server` | rclone remote name |
| `rclone_folders` | List of remote directories for rclone upload |

## Usage

### Basic Download

```
/video <url>
```

Example:
```
/video https://www.youtube.com/watch?v=xxxxx
```

### Audio Download

```
/audio <url>
```

### Telegram File Download

```
/video <filename>
```

Then upload a file in Telegram. The plugin will download the file to the selected directory.

### Interactive Selection

After sending a URL, you can:
1. Reply with a number (1, 2, 3...) to select a download directory
2. Reply "存档" to toggle archive option
3. Reply "代理" to toggle proxy option
4. Reply "独立文件夹" to toggle per-video folder layout
5. Reply "视频" to download video
6. Reply "音频" to download audio only
7. Reply "开始" to download using the default mode

## Supported Platforms

yt-dlp supports 1000+ sites including:
- YouTube
- Bilibili
- Twitter/X
- TikTok
- Instagram
- Vimeo
- And many more...

## Requirements

- yt-dlp (installed system-wide or via pip)
- FFmpeg (for audio extraction and video merging)

## License

MIT License
