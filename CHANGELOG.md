# Changelog

All notable changes to this project will be documented in this file.

## [1.1.0] - 2025-03-17

### Added
- Telegram inline keyboard support for `/video` and `/audio` commands
  - Folder selection buttons with visual indicator (✅) for selected folder
  - Config toggle buttons with status indicators (✅/⭕)
    - 存档 (Archive)
    - 代理 (Proxy)
    - 独立文件夹 (Separate folder)
  - Action buttons: 🎬 视频, 🎵 音频, ❌ 取消
- Session-based keyboard state management with unique session IDs

### Technical
- Added `keyboard_session_id` to `SESSION_STATE` for callback tracking
- Platform-aware: keyboard on Telegram, text menu preserved for other platforms

## [1.0.0] - Initial Release

### Features
- Video/audio download using yt-dlp
- Rclone upload support
- Telegram API server support for files >50MB
- Session-based folder and config selection