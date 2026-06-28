---
feature: screenshot-tab-and-cover-image
status: delivered
specs: []
plans:
  - docs/compose/plans/2026-06-18-screenshot-tab-and-cover-image.md
branch: main
commits: none
---

# Video Screenshot Tab & Cover Image Feature — Final Report

## What Was Built

Two new features were added to the video random cut tool:

1. **Video Screenshot Tab** — A new tab that extracts random frames from videos in a specified folder and saves them to an output folder. Includes face detection to identify and optionally delete screenshots containing faces. Supports batch selection and deletion operations.

2. **Cover Image Feature** — An optional feature in the Video Mix tab that adds a static image as the first segment of the mixed video. The image has no audio and uses configurable duration. Non-9:16 images are automatically padded with blur background.

## Architecture

### Video Screenshot Tab

**New file:** `gui/screenshot_tab.py`
- `ScreenshotWorker(QThread)` — Background worker for frame extraction and face detection
- `ScreenshotTab(QWidget)` — UI with input/output folders, frame count, face detection options, and result table

**Modified files:**
- `gui/main_window.py` — Added screenshot tab import and registration
- `config.json` — Added `screenshot` section for persistent settings

**Data flow:**
1. User selects video folder and output folder
2. Worker extracts N random frames per video using FFmpeg
3. OpenCV Haar cascades detect faces in each frame
4. Optionally auto-delete face images
5. Results displayed in table with batch operations

### Cover Image Feature

**Modified files:**
- `utils/video_utils.py` — Added `image_to_video()` function for static image to video conversion with blur padding
- `core/video_mixer.py` — Extended `VideoMixerEngine` with cover image support in `generate_plan()` and `create_mix()`
- `gui/video_mix_tab.py` — Added cover image UI controls (enable/disable, folder selection, duration settings)
- `config.json` — Added cover image settings to `video_mix` section

**Data flow:**
1. User enables cover image and selects image folder
2. `generate_plan()` adds cover segment at beginning if enabled
3. `create_mix()` converts random image to video using `image_to_video()`
4. Cover video (no audio) prepended to final output

## Usage

### Video Screenshot Tab
1. Go to "视频截图" tab
2. Select video folder and output folder
3. Set number of frames per video (default: 5)
4. Optionally enable "自动删除包含人脸的截图"
5. Click "开始截图"
6. Use batch operations to select/delete specific results

### Cover Image Feature
1. Go to "视频混剪" tab
2. Enable "启用封面图" checkbox
3. Select folder containing cover images (jpg/png/bmp)
4. Set duration range (default: 2~4 seconds)
5. Configure other mix settings as usual
6. Click "开始混剪"

## Verification

- All Python files pass syntax check (`py_compile`)
- Application initializes successfully with 6 tabs
- Screenshot tab has all required UI controls
- Video mix tab has cover image controls
- Config persistence works for both features

## Journey Log

- [lesson] Face detection uses OpenCV Haar cascades (frontalface + profile + eye confirmation) for reliable detection
- [lesson] Image-to-video conversion uses FFmpeg `-loop 1` with blur padding filter for non-9:16 images
