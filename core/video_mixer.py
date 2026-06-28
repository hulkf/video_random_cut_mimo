import os
import random
import tempfile
import shutil
from utils.video_utils import (
    get_video_duration, cut_video, remove_audio,
    concat_videos, add_audio, add_audio_with_silence, image_to_video
)




class VideoMixerEngine:
    def __init__(self, config):
        self.config = config
        self.clips_folder = config["clips_folder"]
        self.output_folder = config["output_folder"]
        self.head_tail = config["head_tail"]
        self.head_min = config["head_min"]
        self.head_max = config["head_max"]
        self.tail_min = config["tail_min"]
        self.tail_max = config["tail_max"]
        self.slice_count_min = config["slice_count_min"]
        self.slice_count_max = config["slice_count_max"]
        self.slice_duration_min = config["slice_duration_min"]
        self.slice_duration_max = config["slice_duration_max"]
        self.mode = config["mode"]
        self.mix_count = config["mix_count"]
        self.cover_enabled = config.get("cover_enabled", False)
        self.cover_folder = config.get("cover_folder", "")
        self.cover_duration_min = config.get("cover_duration_min", 2)
        self.cover_duration_max = config.get("cover_duration_max", 4)
    
    def get_base_videos(self, video_folder):
        video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
        videos = []
        for root, dirs, files in os.walk(video_folder):
            for f in files:
                if f.lower().endswith(video_exts):
                    videos.append(os.path.join(root, f))
        return videos
    
    def get_clip_videos(self):
        video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
        clips = []
        for root, dirs, files in os.walk(self.clips_folder):
            for f in files:
                if f.lower().endswith(video_exts):
                    clips.append(os.path.join(root, f))
        return clips
    
    def get_cover_images(self):
        """Get list of cover images from the specified folder and all subfolders."""
        if not self.cover_enabled or not self.cover_folder:
            return []
        image_exts = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
        images = []
        for root, dirs, files in os.walk(self.cover_folder):
            for f in files:
                if f.lower().endswith(image_exts):
                    images.append(os.path.join(root, f))
        return images
    
    def generate_plan(self, duration):
        """Generate a plan for the video mix.
        
        Returns a list of segments describing what goes where:
        - {"type": "head", "start": 0, "duration": N}
        - {"type": "gap", "start": X, "duration": Y}
        - {"type": "middle", "start": X, "duration": Y}
        - {"type": "gap", "start": X, "duration": Y}
        - {"type": "tail", "start": X, "duration": Y}
        
        Note: Cover image is handled separately in create_mix, not in the plan.
        """
        plan = []
        
        if self.head_tail:
            head_duration = random.uniform(self.head_min, self.head_max)
            tail_duration = random.uniform(self.tail_min, self.tail_max)
        else:
            head_duration = 0
            tail_duration = 0
        
        head_duration = min(head_duration, duration * 0.3)
        tail_duration = min(tail_duration, duration * 0.3)
        
        if head_duration > 0:
            plan.append({"type": "head", "start": 0, "duration": head_duration})
        
        middle_start = head_duration
        middle_end = duration - tail_duration
        middle_duration = middle_end - middle_start
        
        if middle_duration > 0:
            slice_count = random.randint(self.slice_count_min, self.slice_count_max)
            
            if self.mode == 0:
                slice_durations = []
                remaining = middle_duration
                for i in range(slice_count):
                    d = random.uniform(self.slice_duration_min, self.slice_duration_max)
                    d = min(d, remaining / (slice_count - i))
                    d = max(d, 0.5)
                    slice_durations.append(d)
                    remaining -= d
                
                total_slices = sum(slice_durations)
                gap_time = middle_duration - total_slices
                gap_interval = gap_time / (slice_count + 1) if slice_count > 0 else 0
                
                current_pos = middle_start + gap_interval
                for d in slice_durations:
                    if gap_interval > 0.1:
                        gap_start = current_pos - gap_interval
                        plan.append({"type": "gap", "start": gap_start, "duration": gap_interval})
                    plan.append({"type": "middle", "start": current_pos, "duration": d})
                    current_pos += d + gap_interval
                
                if gap_interval > 0.1:
                    plan.append({"type": "gap", "start": current_pos - gap_interval, "duration": gap_interval})
            else:
                middle_segments = []
                for i in range(slice_count):
                    max_start_pos = max(0, middle_duration - self.slice_duration_min)
                    if max_start_pos > 0:
                        start_offset = random.uniform(0, max_start_pos)
                    else:
                        start_offset = 0
                    d = random.uniform(self.slice_duration_min, self.slice_duration_max)
                    actual_start = middle_start + start_offset
                    if actual_start + d <= middle_end:
                        middle_segments.append({"start": actual_start, "duration": d})
                
                middle_segments.sort(key=lambda x: x["start"])
                
                current_pos = middle_start
                for seg in middle_segments:
                    if seg["start"] > current_pos + 0.1:
                        plan.append({"type": "gap", "start": current_pos, "duration": seg["start"] - current_pos})
                    plan.append({"type": "middle", "start": seg["start"], "duration": seg["duration"]})
                    current_pos = seg["start"] + seg["duration"]
                
                if middle_end - current_pos > 0.1:
                    plan.append({"type": "gap", "start": current_pos, "duration": middle_end - current_pos})
        
        if tail_duration > 0:
            plan.append({"type": "tail", "start": duration - tail_duration, "duration": tail_duration})
        
        return plan
    
    def fill_gap(self, gap_duration, clips, tmp_dir, part_index):
        """Fill a gap with clip videos (no audio).
        
        Returns a list of clip segments that fill the gap.
        Multiple clips can be added, and the last one is trimmed if needed.
        """
        if not hasattr(self, '_clip_duration_cache'):
            self._clip_duration_cache = {}
        
        for clip in clips:
            if clip not in self._clip_duration_cache:
                self._clip_duration_cache[clip] = get_video_duration(clip)
        
        clips_in_gap = []
        remaining = gap_duration
        current_start = 0
        
        while remaining > 0.1:
            clip = random.choice(clips)
            clip_duration = self._clip_duration_cache[clip]
            
            if clip_duration >= remaining:
                clip_start = random.uniform(0, max(0, clip_duration - remaining))
                actual_duration = remaining
            else:
                clip_start = 0
                actual_duration = clip_duration
            
            clip_cut_path = os.path.join(tmp_dir, f"clip_cut_{part_index}_{len(clips_in_gap)}.mp4")
            cut_video(clip, clip_start, actual_duration, clip_cut_path)
            
            clip_path = os.path.join(tmp_dir, f"clip_{part_index}_{len(clips_in_gap)}.mp4")
            remove_audio(clip_cut_path, clip_path)
            
            clips_in_gap.append({
                "path": clip_path,
                "duration": actual_duration,
                "type": "clip"
            })
            
            remaining -= actual_duration
            part_index += 1
        
        return clips_in_gap
    
    def create_mix(self, base_video, clips, output_path, progress_callback=None):
        """Create a single mixed video.
        
        Cover image is added at the beginning, with silence during cover duration.
        Audio = silence(cover_duration) + base_video_complete_audio
        """
        duration = get_video_duration(base_video)
        plan = self.generate_plan(duration)
        
        if not plan:
            return None
        
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
            
            for i, segment in enumerate(plan):
                if progress_callback:
                    progress_callback(int((i + 1) / (len(plan) + 2) * 100), f"处理片段 {i+1}/{len(plan)}")
                
                if segment["type"] in ("head", "tail", "middle"):
                    seg_path = os.path.join(tmp_dir, f"base_{len(all_parts)}.mp4")
                    cut_video(base_video, segment["start"], segment["duration"], seg_path)
                    all_parts.append(seg_path)
                elif segment["type"] == "gap":
                    gap_clips = self.fill_gap(segment["duration"], clips, tmp_dir, len(all_parts))
                    all_parts.extend([c["path"] for c in gap_clips])
            
            if progress_callback:
                progress_callback(int((len(plan) + 1) / (len(plan) + 2) * 100), "拼接视频...")
            
            concat_video_only = os.path.join(tmp_dir, "concat_video_only.mp4")
            concat_videos(all_parts, concat_video_only)
            
            if progress_callback:
                progress_callback(100, "添加音频...")
            
            final_output = os.path.join(tmp_dir, "final.mp4")
            add_audio_with_silence(concat_video_only, base_video, final_output, cover_duration)
            
            shutil.move(final_output, output_path)
            
            return output_path
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    
    def run(self, callback=None):
        """Run the mixing process."""
        os.makedirs(self.output_folder, exist_ok=True)
        
        base_videos = self.get_base_videos(self.config["video_folder"])
        clips = self.get_clip_videos()
        
        if not base_videos:
            raise ValueError("No base videos found")
        if not clips:
            raise ValueError("No clip videos found")
        
        results = []
        total_videos = len(base_videos) * self.mix_count
        
        for idx, base_video in enumerate(base_videos):
            base_name = os.path.splitext(os.path.basename(base_video))[0]
            for i in range(self.mix_count):
                output_name = f"{base_name}_mix_{i+1}.mp4" if self.mix_count > 1 else f"{base_name}_mix.mp4"
                output_path = os.path.join(self.output_folder, output_name)
                
                if callback:
                    callback(idx * self.mix_count + i, total_videos, f"{base_name} - 处理中...", 0)
                
                self.create_mix(base_video, clips, output_path)
                results.append(output_path)
                
                if callback:
                    callback(idx * self.mix_count + i + 1, total_videos, f"{base_name} - 完成", 100)
        
        return results
