import os
import random
import subprocess
import json
import cv2
import numpy as np
import onnxruntime as ort


class SCRFDetector:
    """SCRFD 人脸检测器 (适配 scrfd_10g_lite onnx 模型)"""
    
    def __init__(self, model_path: str, score_thresh: float = 0.5, nms_thresh: float = 0.45):
        self.score_thresh = score_thresh
        self.nms_thresh = nms_thresh
        
        # 创建推理会话
        self.session = ort.InferenceSession(
            model_path,
            providers=['CPUExecutionProvider']  # 只用CPU避免警告
        )
        self.input_name = self.session.get_inputs()[0].name
        
        # 获取模型输出名称
        self.output_names = [o.name for o in self.session.get_outputs()]
        
        # 解析输出层
        self._stride_configs = []
        self._parse_output_shapes()
    
    def _parse_output_shapes(self):
        """解析输出层形状，自动识别stride"""
        for name in self.output_names:
            shape = self.session.get_outputs()[
                [o.name for o in self.session.get_outputs()].index(name)
            ].shape
            
            if len(shape) == 2:
                n, c = shape
                if c == 1:  # score
                    self._stride_configs.append(('score', name, n))
                elif c == 4:  # bbox
                    self._stride_configs.append(('bbox', name, n))
                elif c == 10:  # kps
                    self._stride_configs.append(('kps', name, n))
    
    def _preprocess(self, img: np.ndarray) -> tuple:
        """预处理图像 (SCRFD标准预处理)"""
        h, w = img.shape[:2]
        
        # 处理灰度图
        if len(img.shape) == 2 or img.shape[2] == 1:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            h, w = img.shape[:2]
        
        # 固定输入尺寸640x640，保持宽高比缩放后填充
        target_size = 640
        scale = target_size / max(h, w)
        
        # 缩放
        new_w = int(w * scale)
        new_h = int(h * scale)
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        
        # 填充到640x640
        padded = np.zeros((target_size, target_size, 3), dtype=np.uint8)
        padded[:new_h, :new_w] = resized
        
        # BGR -> RGB
        blob = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
        
        # 归一化到 [0, 1]
        blob = blob.astype(np.float32) / 256.0
        
        # HWC -> CHW -> NCHW
        blob = blob.transpose(2, 0, 1)
        blob = np.expand_dims(blob, 0)
        
        return blob, scale, (new_h, new_w)
    
    def _generate_anchors_single(self, feat_h: int, feat_w: int, stride: int) -> np.ndarray:
        """生成单个特征图的anchor坐标 (每个位置2个anchor)"""
        # 生成网格左上角坐标
        anchor_x = np.arange(feat_w).astype(np.float32) * stride
        anchor_y = np.arange(feat_h).astype(np.float32) * stride
        
        # 创建网格
        anchor_y, anchor_x = np.meshgrid(anchor_y, anchor_x, indexing='ij')
        
        # 展平
        x1 = anchor_x.flatten()
        y1 = anchor_y.flatten()
        
        # 每个位置生成2个anchor
        anchors1 = np.stack([x1, y1], axis=-1)
        anchors2 = np.stack([x1, y1], axis=-1)
        
        anchors = np.concatenate([anchors1, anchors2], axis=0)
        
        return anchors
    
    def _decode_bbox(self, bbox_pred: np.ndarray, anchors: np.ndarray) -> np.ndarray:
        """解码边界框"""
        # bbox_pred: [N, 4] - SCRFD输出的是 (cx, cy, w, h) 相对于anchor的偏移
        # anchors: [N, 2] - (x, y) anchor左上角坐标
        
        # SCRFD的bbox输出格式: (cx, cy, w, h) 是相对于anchor中心的偏移
        # anchor中心 = anchor左上角 + stride/2
        # 这里简化处理，直接将bbox视为相对于anchor的偏移
        
        # 计算anchor中心
        anchor_cx = anchors[:, 0] + 8  # stride=16时，中心在左上角+8
        anchor_cy = anchors[:, 1] + 8
        
        # 解码: cx, cy, w, h
        pred_cx = anchor_cx + bbox_pred[:, 0]
        pred_cy = anchor_cy + bbox_pred[:, 1]
        pred_w = bbox_pred[:, 2]
        pred_h = bbox_pred[:, 3]
        
        # 转换为 x1, y1, x2, y2
        boxes = np.zeros_like(bbox_pred)
        boxes[:, 0] = pred_cx - pred_w / 2  # x1
        boxes[:, 1] = pred_cy - pred_h / 2  # y1
        boxes[:, 2] = pred_cx + pred_w / 2  # x2
        boxes[:, 3] = pred_cy + pred_h / 2  # y2
        
        return boxes
    
    def _decode_score(self, score_pred: np.ndarray) -> np.ndarray:
        """解码得分"""
        return score_pred.flatten()
    
    def _decode_kps(self, kps_pred: np.ndarray, anchors: np.ndarray) -> np.ndarray:
        """解码关键点"""
        # kps_pred: [N, 10] - 5个关键点的(x,y)偏移
        # anchors: [N, 2] - anchor中心点
        kps = np.zeros((kps_pred.shape[0], 5, 2))
        for i in range(5):
            kps[:, i, 0] = anchors[:, 0] + kps_pred[:, i * 2]
            kps[:, i, 1] = anchors[:, 1] + kps_pred[:, i * 2 + 1]
        
        return kps
    
    def _nms(self, dets: np.ndarray, scores: np.ndarray) -> list:
        """非极大值抑制"""
        x1 = dets[:, 0]
        y1 = dets[:, 1]
        x2 = dets[:, 2]
        y2 = dets[:, 3]
        
        areas = (x2 - x1) * (y2 - y1)
        order = scores.argsort()[::-1]
        
        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)
            
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            
            w = np.maximum(0.0, xx2 - xx1)
            h = np.maximum(0.0, yy2 - yy1)
            inter = w * h
            
            iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
            inds = np.where(iou <= self.nms_thresh)[0]
            order = order[inds + 1]
        
        return keep
    
    def detect(self, img: np.ndarray) -> list:
        """
        检测人脸
        
        Returns:
            list of dict: [{'bbox': [x1,y1,x2,y2], 'score': float}]
        """
        h, w = img.shape[:2]
        blob, scale, (new_h, new_w) = self._preprocess(img)
        input_h, input_w = blob.shape[2], blob.shape[3]
        
        # 推理
        outputs = self.session.run(self.output_names, {self.input_name: blob})
        
        # 按类型分组
        score_outputs = {}
        bbox_outputs = {}
        num_anchors_per_pos = {}  # 每个位置的anchor数
        
        for out_name, out_data in zip(self.output_names, outputs):
            for typ, name, anchor_count in self._stride_configs:
                if name == out_name:
                    # 根据anchor数量推断stride
                    if anchor_count < 1000:
                        stride = 32
                    elif anchor_count < 5000:
                        stride = 16
                    else:
                        stride = 8
                    
                    feat_h = input_h // stride
                    feat_w = input_w // stride
                    num_anchors = anchor_count // (feat_h * feat_w)
                    num_anchors_per_pos[stride] = num_anchors
                    
                    if typ == 'bbox':
                        bbox_outputs[stride] = (out_data, anchor_count)
                    elif typ == 'score':
                        score_outputs[stride] = (out_data, anchor_count)
        
        if not bbox_outputs:
            return []
        
        all_boxes = []
        all_scores = []
        
        for stride in sorted(bbox_outputs.keys()):
            bbox_data, anchor_count = bbox_outputs[stride]
            score_data, _ = score_outputs[stride]
            num_anc = num_anchors_per_pos.get(stride, 1)
            
            # 计算特征图尺寸
            feat_h = input_h // stride
            feat_w = input_w // stride
            
            # Reshape to (feat_h, feat_w, num_anchors, ...)
            score_map = score_data.reshape(feat_h, feat_w, num_anc)
            bbox_map = bbox_data.reshape(feat_h, feat_w, num_anc, 4)
            
            boxes = []
            scores = []
            
            for y in range(feat_h):
                for x in range(feat_w):
                    for a in range(num_anc):
                        s = score_map[y, x, a]
                        b = bbox_map[y, x, a]
                        
                        if s < self.score_thresh:
                            continue
                        
                        # SCRFD bbox: l, t, r, b (from anchor center), 需要乘以stride
                        cx = x * stride + stride / 2
                        cy = y * stride + stride / 2
                        
                        x1 = cx - b[0] * stride
                        y1 = cy - b[1] * stride
                        x2 = cx + b[2] * stride
                        y2 = cy + b[3] * stride
                        
                        # 还原到原图坐标
                        # 注意：图像被缩放到 new_h x new_w 然后填充到 640x640
                        # 所以需要先除以scale得到原图坐标
                        x1_orig = x1 / scale
                        y1_orig = y1 / scale
                        x2_orig = x2 / scale
                        y2_orig = y2 / scale
                        
                        # 裁剪到原图范围
                        x1_orig = max(0, min(w, x1_orig))
                        y1_orig = max(0, min(h, y1_orig))
                        x2_orig = max(0, min(w, x2_orig))
                        y2_orig = max(0, min(h, y2_orig))
                        
                        if x2_orig > x1_orig and y2_orig > y1_orig:
                            boxes.append([x1_orig, y1_orig, x2_orig, y2_orig])
                            scores.append(float(s))
            
            if boxes:
                all_boxes.append(np.array(boxes))
                all_scores.append(np.array(scores))
        
        if not all_boxes:
            return []
        
        boxes = np.concatenate(all_boxes, axis=0)
        scores = np.concatenate(all_scores, axis=0)
        
        # NMS
        keep = self._nms(boxes, scores)
        
        # 构建结果
        results = []
        for i in keep:
            results.append({
                'bbox': boxes[i].tolist(),
                'score': scores[i]
            })
        
        return results


def get_video_duration(video_path):
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if result.returncode != 0 or not result.stdout:
        return 0
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def extract_random_frames(video_path, output_dir, count=5, prefix="frame"):
    os.makedirs(output_dir, exist_ok=True)
    duration = get_video_duration(video_path)
    if duration <= 0:
        return []

    video_name = os.path.splitext(os.path.basename(video_path))[0]
    saved = []

    for i in range(count):
        t = random.uniform(0, duration)
        output_path = os.path.join(output_dir, f"{video_name}_{prefix}_{i:04d}.jpg")
        cmd = [
            "ffmpeg", "-ss", str(t), "-i", video_path,
            "-vframes", "1", "-q:v", "2",
            "-y", output_path
        ]
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="ignore")
        if result.returncode == 0 and os.path.exists(output_path):
            saved.append(output_path)

    return saved


def detect_face_in_image(image_path, detector=None):
    """检测图片中是否包含人脸
    
    Args:
        image_path: 图片路径
        detector: SCRFDetector实例，如果为None则使用默认模型
        
    Returns:
        bool: 是否包含人脸
    """
    if detector is None:
        # 默认模型路径
        default_model = r"D:\Models\scrfd_10g\det_10g.onnx"
        if os.path.exists(default_model):
            detector = SCRFDetector(default_model)
        else:
            return False
    
    # 使用np.fromfile+cv2.imdecode读取含中文路径的图片
    try:
        img = cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        img = None
    
    if img is None:
        return False
    
    # SCRFD检测
    results = detector.detect(img)
    return len(results) > 0


def delete_images(paths):
    deleted = 0
    failed = 0
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
                deleted += 1
            else:
                failed += 1
        except Exception:
            failed += 1
    return deleted, failed


def delete_video(video_path: str) -> bool:
    """删除视频文件"""
    try:
        if os.path.exists(video_path):
            os.remove(video_path)
            return True
        return False
    except Exception:
        return False


def extract_frames_from_folder(folder_path, output_dir, count_per_video=5,
                                detect_faces=False, delete_faces=False, delete_face_videos=False,
                                progress_callback=None, video_done_callback=None,
                                model_path=None):
    """
    从文件夹中提取视频帧
    
    Args:
        folder_path: 视频文件夹路径
        output_dir: 截图输出目录
        count_per_video: 每个视频截图数量
        detect_faces: 是否检测人脸
        delete_faces: 是否删除包含人脸的截图
        delete_face_videos: 是否删除包含人脸的视频
        progress_callback: 进度回调函数
        video_done_callback: 单个视频处理完成回调
        model_path: SCRFD模型路径
    
    Returns:
        list: 处理结果列表
    """
    video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
    video_files = []
    for root, dirs, files in os.walk(folder_path):
        for f in files:
            if f.lower().endswith(video_exts):
                video_files.append(os.path.join(root, f))

    # 初始化SCRFD检测器
    detector = None
    if detect_faces:
        if model_path is None:
            model_path = r"D:\Models\scrfd_10g\det_10g.onnx"
        if os.path.exists(model_path):
            detector = SCRFDetector(model_path)
        else:
            print(f"[screenshot] 警告: 模型文件不存在 {model_path}")

    all_results = []
    total = len(video_files)

    for idx, video_path in enumerate(video_files):
        rel_path = os.path.relpath(video_path, folder_path)
        video_output = os.path.join(output_dir, os.path.splitext(os.path.basename(video_path))[0])

        images = extract_random_frames(video_path, video_output, count=count_per_video)

        face_images = []
        if detect_faces and images:
            for img_path in images:
                if detect_face_in_image(img_path, detector):
                    face_images.append(img_path)

        # 删除包含人脸的截图
        if delete_faces and face_images:
            delete_images(face_images)
            images = [i for i in images if i not in face_images]

        # 删除包含人脸的视频
        video_deleted = False
        if delete_face_videos and face_images:
            video_deleted = delete_video(video_path)

        result = {
            "video": rel_path,
            "full_path": video_path,
            "images": images,
            "face_images": face_images,
            "has_faces": len(face_images) > 0,
            "video_deleted": video_deleted
        }
        all_results.append(result)

        if video_done_callback:
            video_done_callback(result)
        if progress_callback:
            progress_callback(idx + 1, total)

    return all_results
