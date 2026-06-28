import os
import json
from core.text_detector import TextDetector
from utils.video_utils import (
    get_video_duration, extract_frames, cut_video
)


def organize_by_filename(output_dir):
    """将输出目录中的文件按文件名前缀整理到独立文件夹"""
    video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
    moved_count = 0
    
    for item in os.listdir(output_dir):
        item_path = os.path.join(output_dir, item)
        if os.path.isfile(item_path) and item.lower().endswith(video_exts):
            if "_" in item:
                prefix = item.split("_")[0]
            else:
                prefix = os.path.splitext(item)[0]
            
            target_dir = os.path.join(output_dir, prefix)
            os.makedirs(target_dir, exist_ok=True)
            
            target_path = os.path.join(target_dir, item)
            if item_path != target_path:
                os.rename(item_path, target_path)
                moved_count += 1
    
    return moved_count


def flatten_to_root(output_dir):
    """将子文件夹中的视频文件提取到根目录并删除空文件夹"""
    video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
    moved_count = 0
    removed_dirs = []
    
    for item in os.listdir(output_dir):
        item_path = os.path.join(output_dir, item)
        if os.path.isdir(item_path):
            for sub_item in os.listdir(item_path):
                if sub_item.lower().endswith(video_exts):
                    src_path = os.path.join(item_path, sub_item)
                    dst_path = os.path.join(output_dir, sub_item)
                    
                    if src_path != dst_path:
                        if os.path.exists(dst_path):
                            base, ext = os.path.splitext(sub_item)
                            dst_path = os.path.join(output_dir, f"{base}_dup{ext}")
                        
                        os.rename(src_path, dst_path)
                        moved_count += 1
            
            remaining = os.listdir(item_path)
            if not remaining:
                os.rmdir(item_path)
                removed_dirs.append(item)
    
    return moved_count, removed_dirs


class VideoSlicer:
    def __init__(self, min_duration=3, max_duration=5, detect_text=False):
        self.min_duration = min_duration
        self.max_duration = max_duration
        self.detect_text = detect_text
        if detect_text:
            self.text_detector = TextDetector()
    
    def slice_video(self, video_path, output_dir):
        """Slice a single video into segments."""
        os.makedirs(output_dir, exist_ok=True)
        duration = get_video_duration(video_path)
        results = []
        start = 0
        segment_index = 0
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        
        while start < duration:
            segment_duration = min(
                self.max_duration,
                duration - start
            )
            if segment_duration < self.min_duration:
                break
            
            output_path = os.path.join(
                output_dir,
                f"{video_name}_segment_{segment_index:04d}.mp4"
            )
            cut_video(video_path, start, segment_duration, output_path)
            
            has_text = False
            if self.detect_text:
                frames_dir = os.path.join(output_dir, f"frames_{segment_index:04d}")
                frames = extract_frames(output_path, frames_dir)
                has_text = self.text_detector.has_text_in_frames(frames)
            
            results.append({
                "file": output_path,
                "start": start,
                "duration": segment_duration,
                "has_text": has_text
            })
            
            start += segment_duration
            segment_index += 1
        
        return results
    
    def slice_folder(self, folder_path, output_dir, on_video_done=None, separate_folders=False):
        """Slice all videos in a folder.
        
        Args:
            folder_path: 输入文件夹路径
            output_dir: 输出文件夹路径
            on_video_done: 每完成一个视频的回调，参数为该视频的切片结果列表
            separate_folders: 是否按原始视频分文件夹存放
        """
        video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
        all_results = []
        
        video_files = [f for f in os.listdir(folder_path) if f.lower().endswith(video_exts)]
        
        for file in video_files:
            video_path = os.path.join(folder_path, file)
            video_name = os.path.splitext(file)[0]
            
            if separate_folders:
                video_output = os.path.join(output_dir, video_name)
            else:
                video_output = output_dir
            
            results = self.slice_video(video_path, video_output)
            all_results.extend(results)
            
            if on_video_done:
                on_video_done(results)
        
        report_path = os.path.join(output_dir, "slice_report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        
        return all_results
