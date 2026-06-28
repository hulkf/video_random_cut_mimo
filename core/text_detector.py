from paddleocr import PaddleOCR
import os
import subprocess
import glob as glob_module


_ocr_instance = None


def _init_worker():
    global _ocr_instance
    _ocr_instance = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)


def _has_chinese(text):
    """判断文本是否包含中文"""
    for char in text:
        if '\u4e00' <= char <= '\u9fff':
            return True
    return False


def detect_single_video(args):
    """Worker function: extract frames + OCR for one video (runs in subprocess)."""
    global _ocr_instance
    video_path, frame_interval, threshold = args

    try:
        # Initialize OCR instance if not already done
        if _ocr_instance is None:
            _init_worker()
        
        frames_dir = os.path.join(os.path.dirname(video_path), "_frames_tmp",
                                  os.path.splitext(os.path.basename(video_path))[0])
        os.makedirs(frames_dir, exist_ok=True)

        cmd = [
            "ffmpeg", "-i", video_path, "-vf", f"fps=1/{frame_interval}",
            "-q:v", "2", os.path.join(frames_dir, "frame_%04d.jpg")
        ]
        subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore")

        frame_files = sorted(glob_module.glob(os.path.join(frames_dir, "*.jpg")))
        if not frame_files:
            return video_path, False, frames_dir

        # 字幕检测：只要有一帧有字幕就返回True
        from PIL import Image
        
        for frame in frame_files:
            try:
                with Image.open(frame) as img:
                    img_width, img_height = img.size
            except Exception:
                img_width, img_height = 1920, 1080
            
            result = _ocr_instance.ocr(frame, cls=True)
            
            if result and result[0]:
                for line in result[0]:
                    box = line[0]
                    text = line[1][0]
                    confidence = line[1][1]
                    
                    if confidence < 0.6:
                        continue
                    
                    # 过滤纯英文内容
                    if not _has_chinese(text):
                        continue
                    
                    # 计算文字框的宽高
                    x_coords = [point[0] for point in box]
                    y_coords = [point[1] for point in box]
                    box_width = max(x_coords) - min(x_coords)
                    box_height = max(y_coords) - min(y_coords)
                    
                    width_ratio = box_width / img_width
                    height_ratio = box_height / img_height
                    
                    # 字幕特征：宽>30%，高<10%
                    if width_ratio > 0.3 and height_ratio < 0.1:
                        return video_path, True, frames_dir

        return video_path, False, frames_dir
    except Exception as e:
        return video_path, False, None


class TextDetector:
    def __init__(self):
        self.ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)

    def _get_image_size(self, image_path):
        try:
            from PIL import Image
            with Image.open(image_path) as img:
                return img.width, img.height
        except Exception:
            return 1920, 1080  # 默认值

    def _is_subtitle_shape(self, box, img_width, img_height):
        """判断文字框是否符合字幕的几何特征：宽且扁"""
        x_coords = [point[0] for point in box]
        y_coords = [point[1] for point in box]
        box_width = max(x_coords) - min(x_coords)
        box_height = max(y_coords) - min(y_coords)

        width_ratio = box_width / img_width
        height_ratio = box_height / img_height

        return width_ratio > 0.3 and height_ratio < 0.1

    def _has_chinese(self, text):
        """判断文本是否包含中文"""
        for char in text:
            if '\u4e00' <= char <= '\u9fff':
                return True
        return False

    def detect_subtitle_in_image(self, image_path):
        """检测图片中是否有字幕（宽扁形状的中文文字框）"""
        result = self.ocr.ocr(image_path, cls=True)
        if not result or not result[0]:
            return False

        img_width, img_height = self._get_image_size(image_path)

        for line in result[0]:
            box = line[0]
            text = line[1][0]
            confidence = line[1][1]

            if confidence < 0.6:
                continue

            if not self._has_chinese(text):
                continue

            if self._is_subtitle_shape(box, img_width, img_height):
                return True

        return False

    def detect_text(self, image_path):
        """兼容旧接口：检测图片中是否有文字"""
        return self.detect_subtitle_in_image(image_path)

    def has_text_in_frames(self, frame_paths, threshold=None):
        """检测帧序列中是否有字幕，只要有一帧有字幕就返回True"""
        if not frame_paths:
            return False
        for frame in frame_paths:
            if self.detect_subtitle_in_image(frame):
                return True
        return False
