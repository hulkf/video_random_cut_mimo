import os
import random
import shutil
import subprocess
import tempfile
from core.audio_extractor import AudioExtractor
from utils.video_utils import (
    get_video_duration, concat_videos, add_audio,
    extract_audio, add_audio_with_silence, image_to_video
)


AUDIO_EXTS = (".mp3", ".wav", ".aac", ".flac", ".ogg")
VIDEO_EXTS = (".mp4", ".avi", ".mov", ".mkv", ".flv")
MEDIA_EXTS = AUDIO_EXTS + VIDEO_EXTS


class VideoMixer:
    def __init__(self, cover_enabled=False, cover_folder="",
                 cover_duration_min=0.5, cover_duration_max=1.0):
        self.audio_extractor = AudioExtractor()
        self.cover_enabled = cover_enabled
        self.cover_folder = cover_folder
        self.cover_duration_min = cover_duration_min
        self.cover_duration_max = cover_duration_max
        self._clip_duration_cache = {}

    def get_media_files(self, folder):
        """Get all audio and video files recursively from folder."""
        files = []
        for root, dirs, file_list in os.walk(folder):
            for f in file_list:
                if f.lower().endswith(MEDIA_EXTS):
                    files.append(os.path.join(root, f))
        return files

    def get_duration(self, path):
        """Get duration of audio or video file."""
        ext = os.path.splitext(path)[1].lower()
        if ext in VIDEO_EXTS:
            if path not in self._clip_duration_cache:
                self._clip_duration_cache[path] = get_video_duration(path)
            return self._clip_duration_cache[path]
        return self.audio_extractor.get_audio_duration(path)

    def _extract_audio_if_video(self, path, tmp_dir, tag):
        """If path is video, extract audio to tmp_dir and return audio path. Otherwise return as-is."""
        ext = os.path.splitext(path)[1].lower()
        if ext in VIDEO_EXTS:
            audio_path = os.path.join(tmp_dir, f"{tag}.aac")
            extract_audio(path, audio_path)
            return audio_path
        return path

    def get_cover_images(self):
        """Get list of cover images from cover folder recursively."""
        if not self.cover_enabled or not self.cover_folder:
            return []
        image_exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
        images = []
        for root, dirs, files in os.walk(self.cover_folder):
            for f in files:
                if f.lower().endswith(image_exts):
                    images.append(os.path.join(root, f))
        return images

    def mix_videos(self, clips_dir, media_path, output_path, callback=None):
        """Mix clips to match media duration. media_path can be audio or video."""
        media_duration = self.get_duration(media_path)

        clip_files = []
        for root, dirs, files in os.walk(clips_dir):
            for f in files:
                if f.lower().endswith(VIDEO_EXTS):
                    clip_files.append(os.path.join(root, f))

        if not clip_files:
            raise ValueError("No video clips found")

        selected_clips = []
        current_duration = 0
        used_indices = set()

        while current_duration < media_duration and len(used_indices) < len(clip_files):
            available = [
                i for i in range(len(clip_files))
                if i not in used_indices
            ]
            if not available:
                used_indices.clear()
                available = list(range(len(clip_files)))

            idx = random.choice(available)
            clip = clip_files[idx]
            clip_duration = self.get_duration(clip)

            if current_duration + clip_duration <= media_duration * 1.1:
                selected_clips.append(clip)
                current_duration += clip_duration
                used_indices.add(idx)

                if callback:
                    callback(len(selected_clips), current_duration, media_duration)

        if not selected_clips:
            raise ValueError("No clips selected")

        tmp_dir = tempfile.mkdtemp()
        try:
            all_parts = []
            cover_duration = 0

            if self.cover_enabled and self.cover_folder:
                cover_images = self.get_cover_images()
                if cover_images:
                    cover_img = random.choice(cover_images)
                    cover_duration = random.uniform(
                        self.cover_duration_min, self.cover_duration_max
                    )
                    cover_path = os.path.join(tmp_dir, "cover.mp4")
                    image_to_video(cover_img, cover_duration, cover_path)
                    all_parts.append(cover_path)

            video_only_dir = os.path.join(tmp_dir, "clips_no_audio")
            os.makedirs(video_only_dir, exist_ok=True)
            for i, clip in enumerate(selected_clips):
                no_audio_path = os.path.join(video_only_dir, f"clip_{i}.mp4")
                cmd = [
                    "ffmpeg", "-i", clip, "-an",
                    "-c", "copy", "-y", no_audio_path
                ]
                subprocess.run(cmd, capture_output=True,
                               encoding="utf-8", errors="ignore", timeout=60)
                all_parts.append(no_audio_path)

            tmp_video = os.path.join(tmp_dir, "concat.mp4")
            concat_videos(all_parts, tmp_video)

            audio_path = self._extract_audio_if_video(media_path, tmp_dir, "audio")
            final_tmp = os.path.join(tmp_dir, "final.mp4")
            add_audio_with_silence(tmp_video, audio_path, final_tmp, cover_duration)
            shutil.move(final_tmp, output_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return output_path

    def mix_folder(self, clips_dir, media_dir, output_dir, callback=None):
        """Mix clips for each audio/video file in folder and subfolders."""
        media_files = self.get_media_files(media_dir)

        if not media_files:
            raise ValueError("No audio or video files found")

        os.makedirs(output_dir, exist_ok=True)
        results = []

        for idx, media_path in enumerate(media_files):
            media_name = os.path.splitext(os.path.basename(media_path))[0]
            output_path = os.path.join(output_dir, f"{media_name}.mp4")

            self.mix_videos(clips_dir, media_path, output_path)
            results.append(output_path)

            if callback:
                callback(idx + 1, len(media_files))

        return results
