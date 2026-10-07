import os
import random
import shutil
import tempfile
import math
import logging
from core.concat_batch import concat_workers, ordered_batch, reserve_output
from core.audio_extractor import AudioExtractor
from utils.media_utils import VIDEO_EXTS, collect_files
from core.video_utils import (
    get_video_duration, concat_videos,
    extract_audio, add_audio_with_silence, image_to_video
)


AUDIO_EXTS = (".mp3", ".wav", ".aac", ".flac", ".ogg")
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
        """Get all audio and video files recursively from folder (or single file)."""
        return collect_files(folder, MEDIA_EXTS)

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
        """Get list of cover images from cover folder (or single file)."""
        if not self.cover_enabled or not self.cover_folder:
            return []
        image_exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
        return collect_files(self.cover_folder, image_exts)

    def mix_videos(self, clips_dir, media_path, output_path, callback=None):
        """Mix clips to match media duration. media_path can be audio or video."""
        media_duration = self.get_duration(media_path)

        clip_files = getattr(self, "_batch_clips", None)
        if clip_files is None:
            clip_files = collect_files(clips_dir, VIDEO_EXTS)

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
            used_indices.add(idx)  # Overlong/invalid candidates must also advance the search.
            clip = clip_files[idx]
            try:
                clip_duration = self.get_duration(clip)
            except Exception:
                used_indices.add(idx)
                continue

            if math.isfinite(clip_duration) and clip_duration > 0 and current_duration + clip_duration <= media_duration * 1.1:
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
                    try:
                        image_to_video(cover_img, cover_duration, cover_path)
                        all_parts.append(cover_path)
                    except Exception:
                        cover_duration = 0

            # concat_videos maps only video and already discards source audio.
            all_parts.extend(selected_clips)

            if not all_parts:
                raise ValueError("No valid video parts to concatenate")

            tmp_video = os.path.join(tmp_dir, "concat.mp4")
            concat_videos(all_parts, tmp_video)

            audio_path = self._extract_audio_if_video(media_path, tmp_dir, "audio")
            final_tmp = os.path.join(tmp_dir, "final.mp4")
            add_audio_with_silence(tmp_video, audio_path, final_tmp, cover_duration)
            shutil.move(final_tmp, output_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return output_path

    def mix_folder(self, clips_dir, media_dir, output_dir, callback=None, *, max_workers=None):
        """Mix clips for each audio/video file in folder and subfolders."""
        media_files = self.get_media_files(media_dir)

        if not media_files:
            raise ValueError("No audio or video files found")

        os.makedirs(output_dir, exist_ok=True)
        self._clip_duration_cache.clear()
        self._batch_clips = collect_files(clips_dir, VIDEO_EXTS)
        for clip in self._batch_clips:
            try:
                self._clip_duration_cache[clip] = get_video_duration(clip)
            except Exception:
                self._clip_duration_cache[clip] = 0
        jobs, reserved = [], set()
        for media_path in media_files:
            media_name = os.path.splitext(os.path.basename(media_path))[0]
            output_path = reserve_output(os.path.join(output_dir, f"{media_name}.mp4"), reserved)
            jobs.append((media_path, output_path))

        def process(job):
            media_path, output_path = job
            try:
                self.mix_videos(clips_dir, media_path, output_path)
                return output_path
            except Exception as e:
                logging.warning("[mixer] 跳过 %s: %s", os.path.basename(media_path), e)
                return None
        workers = concat_workers(max_workers)
        try:
            results = ordered_batch(jobs, process, workers=workers,
                callback=(lambda cur, total, *_: callback(cur, total)) if callback else None,
                ffmpeg_threads=max(1, min(4, (os.cpu_count() or 1) // workers)))
            return [path for path in results if path is not None]
        finally:
            self._batch_clips = None
