# -*- coding: utf-8 -*-
import os
import sys
import json
import subprocess
import traceback
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QSpinBox, QFileDialog,
    QTableWidget, QTableWidgetItem, QProgressBar,
    QMessageBox, QGroupBox, QComboBox, QColorDialog,
    QScrollArea, QFrame, QCheckBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from gui.config import get_config, set_config


FIREMODELS_DIR = r"D:\Models\FireRed"
FUNASR_DIR = r"D:\Models\FunASR\paraformer-large-zh-en-timestamp-onnx-offline"
SENSEVOICE_DIR = r"D:\Models\SenseVoiceSmall"


class SubtitleWorker(QThread):
    progress = pyqtSignal(int, int)
    video_done = pyqtSignal(dict)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, folder_path, output_folder, font_name,
                 font_size, font_color, outline_color, outline_width,
                 position, model_dir, model_type, enable_correction=False,
                 keep_srt=False):
        super().__init__()
        self.folder_path = folder_path
        self.output_folder = output_folder
        self.font_name = font_name
        self.font_size = font_size
        self.font_color = font_color
        self.outline_color = outline_color
        self.outline_width = outline_width
        self.position = position
        self.model_dir = model_dir
        self.model_type = model_type
        self.enable_correction = enable_correction
        self.keep_srt = keep_srt
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            video_exts = (".mp4", ".avi", ".mov", ".mkv", ".flv")
            video_files = []
            for root, dirs, files in os.walk(self.folder_path):
                for f in files:
                    if f.lower().endswith(video_exts):
                        video_files.append(os.path.join(root, f))

            os.makedirs(self.output_folder, exist_ok=True)
            all_results = []
            total = len(video_files)

            # P1: 模型只加载一次
            asr = None
            if self.model_type == "SenseVoice":
                from core.sherpa_asr import SherpaASR
                asr = SherpaASR(self.model_dir, self.model_type)
            elif self.model_type in ("FunASR (Paraformer)", "FireRedASR"):
                from core.onnx_asr import OnnxASR
                asr = OnnxASR(self.model_dir, self.model_type)

            for idx, video_path in enumerate(video_files):
                if self._cancelled:
                    break

                if self.model_type in ("SenseVoice", "FunASR (Paraformer)", "FireRedASR"):
                    result = self._process_video(video_path, asr)
                elif self.model_type == "Whisper (\u72ec\u7acb\u8fdb\u7a0b)":
                    result = self._process_video_whisper(video_path)
                else:
                    result = {"video": video_path, "success": False}
                all_results.append(result)
                self.video_done.emit(result)
                self.progress.emit(idx + 1, total)

            self.finished.emit(all_results)
        except Exception as e:
            log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core", "onnx_asr_error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n=== Worker Error ===\n")
                traceback.print_exc(file=f)
            self.error.emit(str(e))

    def _transcribe(self, video_path, asr):
        segments = asr.transcribe(video_path)
        return segments

    def _transcribe_funasr(self, video_path, asr):
        segments = asr.transcribe(video_path)
        return segments

    def _get_position_style(self):
        if self.position == "\u4e0a":
            return "Alignment=8,MarginV=20"
        elif self.position == "\u4e2d":
            return "Alignment=5"
        else:
            return "Alignment=2,MarginV=40"

    def _burn_subtitles(self, video_path, srt_path, output_path):
        """\u5c06 SRT \u5b57\u5e55\u70e4\u5236\u5230\u89c6\u9891\u4e2d"""
        color_hex = self.font_color.replace("#", "")
        r = int(color_hex[0:2], 16) if len(color_hex) >= 2 else 255
        g = int(color_hex[2:4], 16) if len(color_hex) >= 4 else 255
        b = int(color_hex[4:6], 16) if len(color_hex) >= 6 else 255
        ass_color = f"&H00{b:02X}{g:02X}{r:02X}"

        outline_r, outline_g, outline_b = 0, 0, 0
        if self.outline_color.startswith("#"):
            ol_hex = self.outline_color.replace("#", "")
            outline_r = int(ol_hex[0:2], 16) if len(ol_hex) >= 2 else 0
            outline_g = int(ol_hex[2:4], 16) if len(ol_hex) >= 4 else 0
            outline_b = int(ol_hex[4:6], 16) if len(ol_hex) >= 6 else 0
        ass_outline_color = f"&H00{outline_b:02X}{outline_g:02X}{outline_r:02X}"

        position_style = self._get_position_style()
        srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")

        subtitle_filter = (
            f"subtitles='{srt_escaped}':"
            f"force_style='FontName={self.font_name},"
            f"FontSize={self.font_size},"
            f"PrimaryColour={ass_color},"
            f"OutlineColour={ass_outline_color},"
            f"Outline={self.outline_width},"
            f"{position_style}'"
        )

        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", subtitle_filter,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "copy",
            "-y", output_path
        ]

        ff_result = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                                   errors="ignore", timeout=3600)

        return {
            "video": os.path.relpath(video_path, self.folder_path),
            "full_path": video_path,
            "output_path": output_path,
            "success": ff_result.returncode == 0 and os.path.exists(output_path)
        }

    def _process_video(self, video_path, asr):
        """统一视频处理流程（P5: 合并重复代码）"""
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        output_path = os.path.join(self.output_folder, f"{video_name}_subtitled.mp4")
        srt_path = os.path.join(self.output_folder, f"{video_name}.srt")

        try:
            segments = asr.transcribe(video_path)
            if self.enable_correction and segments:
                from core.text_corrector import TextCorrector
                corrector = TextCorrector()
                segments = corrector.correct_segments(segments)
            if not segments:
                return {"video": video_path, "success": False}

            with open(srt_path, "w", encoding="utf-8") as f:
                for i, seg in enumerate(segments, 1):
                    start = seg["start"]
                    end = seg["end"]
                    text = seg["text"].strip()
                    sh = int(start // 3600)
                    sm = int((start % 3600) // 60)
                    ss = int(start % 60)
                    sms = int((start % 1) * 1000)
                    eh = int(end // 3600)
                    em = int((end % 3600) // 60)
                    es = int(end % 60)
                    ems = int((end % 1) * 1000)
                    f.write(f"{i}\n")
                    f.write(f"{sh:02d}:{sm:02d}:{ss:02d},{sms:03d} --> "
                           f"{eh:02d}:{em:02d}:{es:02d},{ems:03d}\n")
                    f.write(f"{text}\n\n")

            result = self._burn_subtitles(video_path, srt_path, output_path)
            # P8: 保留SRT文件选项
            if not self.keep_srt and os.path.exists(srt_path):
                try:
                    os.remove(srt_path)
                except:
                    pass
            return result
        except Exception:
            return {"video": video_path, "success": False}

    def _transcribe(self, video_path, asr):
        return asr.transcribe(video_path)

    def _transcribe_funasr(self, video_path, asr):
        return asr.transcribe(video_path)



    def _process_video_whisper(self, video_path):
        """\u901a\u8fc7\u72ec\u7acb\u8fdb\u7a0b\u8c03\u7528 Whisper \u8fdb\u884c\u8bed\u97f3\u8bc6\u522b"""
        import tempfile, json, shutil
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        output_path = os.path.join(self.output_folder, f"{video_name}_subtitled.mp4")
        srt_path = os.path.join(self.output_folder, f"{video_name}.srt")
        tmp_audio = os.path.join(tempfile.gettempdir(), f"whisper_audio_{os.getpid()}_{video_name}.wav")

        try:
            # 1. \u63d0\u53d6\u97f3\u9891
            subprocess.run(["ffmpeg", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
                           "-ar", "16000", "-ac", "1", "-y", tmp_audio],
                          capture_output=True, timeout=3600, check=True)

            # 2. \u89e3\u6790\u6a21\u578b\u8def\u5f84
            model_dir = self.model_dir
            model_name = "large-v3"
            if model_dir:
                if model_dir.endswith(".pt"):
                    model_name = os.path.splitext(os.path.basename(model_dir))[0]
                    model_dir = os.path.dirname(model_dir)
                elif os.path.isfile(model_dir):
                    model_name = os.path.splitext(os.path.basename(model_dir))[0]
                    model_dir = os.path.dirname(model_dir)
                else:
                    # \u53ef\u80fd\u662f\u76ee\u5f55\u6216\u6a21\u578b\u540d\u79f0
                    if os.path.isdir(model_dir):
                        pt_files = [f for f in os.listdir(model_dir) if f.endswith(".pt")]
                        if pt_files:
                            # \u7528\u6237\u53ef\u80fd\u60f3\u7528\u7279\u5b9a\u6a21\u578b\uff0c\u9ed8\u8ba4\u7b2c\u4e00\u4e2a
                            pass  # \u4f7f\u7528\u9ed8\u8ba4\u7684 large-v3

            # 3. \u8c03\u7528\u72ec\u7acb\u8fdb\u7a0b
            whisper_script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "_whisper_transcribe.py")
            whisper_script = os.path.abspath(whisper_script)

            cmd = [sys.executable, whisper_script, tmp_audio, model_name, srt_path]
            if model_dir and os.path.isdir(model_dir):
                cmd.append(model_dir)

            proc = subprocess.run(cmd, capture_output=True, timeout=7200,
                                  encoding="utf-8", errors="replace")

            # 4. \u89e3\u6790\u7ed3\u679c
            stdout = proc.stdout.strip()
            if stdout:
                try:
                    result_data = json.loads(stdout)
                    if result_data.get("success") and os.path.exists(srt_path):
                        if self.enable_correction:
                            from core.text_corrector import TextCorrector
                            self._correct_srt_file(srt_path, TextCorrector())
                        return self._burn_subtitles(video_path, srt_path, output_path)
                except json.JSONDecodeError:
                    pass

            return {"video": video_path, "success": False}
        except subprocess.CalledProcessError:
            return {"video": video_path, "success": False}
        except Exception as e:
            return {"video": video_path, "success": False}
        finally:
            try:
                if os.path.exists(tmp_audio):
                    os.remove(tmp_audio)
            except:
                pass
            try:
                if os.path.exists(srt_path):
                    os.remove(srt_path)
            except:
                pass

    def _correct_srt_file(self, srt_path, corrector):
        """\u8bfb\u53d6 SRT \u6587\u4ef6\uff0c\u5bf9\u5b57\u5e55\u6587\u672c\u9010\u6761\u7ea0\u9519\u540e\u91cd\u5199"""
        import re
        with open(srt_path, "r", encoding="utf-8") as f:
            content_srt = f.read()
        blocks = content_srt.strip().split("\n\n")
        new_blocks = []
        for block in blocks:
            lines = block.split("\n")
            if len(lines) >= 3:
                text = "\n".join(lines[2:])
                corrected = corrector.correct(text)
                new_blocks.append("\n".join(lines[:2] + [corrected]))
            else:
                new_blocks.append(block)
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(new_blocks) + "\n")


class SubtitleTab(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.results = []
        self.init_ui()
        self.load_config()

    def init_ui(self):
        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        input_group = QGroupBox("\u8f93\u5165\u8bbe\u7f6e")
        input_layout = QVBoxLayout()

        folder_layout = QHBoxLayout()
        folder_layout.setSpacing(8)
        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText("\u9009\u62e9\u89c6\u9891\u6587\u4ef6\u5939...")
        self.folder_input.setMinimumHeight(30)
        folder_btn = QPushButton("\u6d4f\u89c8")
        folder_btn.setFixedWidth(80)
        folder_btn.clicked.connect(self.browse_folder)
        folder_layout.addWidget(self.folder_input, 1)
        folder_layout.addWidget(folder_btn)

        output_layout = QHBoxLayout()
        output_layout.setSpacing(8)
        self.output_input = QLineEdit()
        self.output_input.setPlaceholderText("\u8f93\u51fa\u6587\u4ef6\u5939...")
        self.output_input.setMinimumHeight(30)
        output_btn = QPushButton("\u6d4f\u89c8")
        output_btn.setFixedWidth(80)
        output_btn.clicked.connect(self.browse_output)
        output_layout.addWidget(self.output_input, 1)
        output_layout.addWidget(output_btn)

        input_layout.addLayout(folder_layout)
        input_layout.addLayout(output_layout)
        input_group.setLayout(input_layout)

        subtitle_group = QGroupBox("\u5b57\u5e55\u8bbe\u7f6e")
        subtitle_layout = QVBoxLayout()

        font_layout = QHBoxLayout()
        font_layout.addWidget(QLabel("\u5b57\u4f53:"))
        self.font_combo = QComboBox()
        self.font_combo.addItems(["SimHei", "SimSun", "Microsoft YaHei", "KaiTi", "FangSong"])
        self.font_combo.setEditable(True)
        self.font_combo.setMinimumHeight(28)
        font_layout.addWidget(self.font_combo)

        font_layout.addWidget(QLabel("\u5b57\u53f7:"))
        self.font_size = QSpinBox()
        self.font_size.setRange(10, 100)
        self.font_size.setValue(12)
        self.font_size.setMinimumHeight(28)
        font_layout.addWidget(self.font_size)

        subtitle_layout.addLayout(font_layout)

        color_layout = QHBoxLayout()
        color_layout.addWidget(QLabel("\u5b57\u4f53\u989c\u8272:"))
        self.color_btn = QPushButton("\u9009\u62e9\u989c\u8272")
        self.color_btn.clicked.connect(self.choose_font_color)
        self.color_label = QLabel("#FFFFFF")
        self.color_label.setStyleSheet("background-color: #FFFFFF; padding: 5px; border: 1px solid black;")
        self.current_color = "#FFFFFF"
        color_layout.addWidget(self.color_btn)
        color_layout.addWidget(self.color_label)
        color_layout.addStretch()

        subtitle_layout.addLayout(color_layout)

        outline_layout = QHBoxLayout()
        outline_layout.addWidget(QLabel("\u63cf\u8fb9\u989c\u8272:"))
        self.outline_color_btn = QPushButton("\u9009\u62e9\u989c\u8272")
        self.outline_color_btn.clicked.connect(self.choose_outline_color)
        self.outline_color_label = QLabel("#000000")
        self.outline_color_label.setStyleSheet("background-color: #000000; padding: 5px; border: 1px solid gray;")
        self.current_outline_color = "#000000"
        outline_layout.addWidget(self.outline_color_btn)
        outline_layout.addWidget(self.outline_color_label)
        outline_layout.addStretch()

        outline_layout.addWidget(QLabel("\u63cf\u8fb9\u7c97\u7ec6:"))
        self.outline_width = QSpinBox()
        self.outline_width.setRange(0, 10)
        self.outline_width.setValue(2)
        self.outline_width.setMinimumHeight(28)
        outline_layout.addWidget(self.outline_width)

        subtitle_layout.addLayout(outline_layout)

        position_layout = QHBoxLayout()
        position_layout.addWidget(QLabel("\u5b57\u5e55\u4f4d\u7f6e:"))
        self.position_combo = QComboBox()
        self.position_combo.addItems(["\u4e0a", "\u4e2d", "\u4e0b"])
        self.position_combo.setCurrentIndex(2)
        self.position_combo.setMinimumHeight(28)
        position_layout.addWidget(self.position_combo)
        position_layout.addStretch()

        subtitle_layout.addLayout(position_layout)

        # \u6a21\u578b\u9009\u62e9
        model_choice_layout = QHBoxLayout()
        model_choice_layout.setSpacing(8)
        model_choice_layout.addWidget(QLabel("\u8bc6\u522b\u5f15\u64ce:"))
        self.model_combo = QComboBox()
        self.model_combo.addItems(["FireRedASR", "FunASR (Paraformer)", "SenseVoice", "Whisper (\u72ec\u7acb\u8fdb\u7a0b)"])
        self.model_combo.setMinimumHeight(28)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        model_choice_layout.addWidget(self.model_combo)
        model_choice_layout.addStretch()
        subtitle_layout.addLayout(model_choice_layout)

        self.model_path_container = QWidget()
        model_path_box = QHBoxLayout(self.model_path_container)
        model_path_box.setContentsMargins(0, 0, 0, 0)
        model_path_box.addWidget(QLabel("模型路径:"))
        self.model_path_input = QLineEdit()
        self.model_path_input.setMinimumHeight(30)
        self.model_path_input.setPlaceholderText("选择 ASR 模型目录...")
        model_browse_btn = QPushButton("浏览")
        model_browse_btn.setFixedWidth(80)
        model_browse_btn.clicked.connect(self.browse_model)
        model_path_box.addWidget(self.model_path_input, 1)
        model_path_box.addWidget(model_browse_btn)
        subtitle_layout.addWidget(self.model_path_container)

        # AI \u7ea0\u9519\u5f00\u5173
        corr_layout = QHBoxLayout()
        self.correction_check = QCheckBox("AI \u667a\u80fd\u7ea0\u9519 (MiniCPM3-4B)")
        self.correction_check.setToolTip("\u5229\u7528\u672c\u5730 Ollama + MiniCPM3-4B \u5bf9\u8bc6\u522b\u7ed3\u679c\u8fdb\u884c\u667a\u80fd\u7ea0\u9519\uff0c\u9002\u5408\u7535\u5546\u5185\u88e4\u7c7b\u76ee\u573a\u666f")
        corr_layout.addWidget(self.correction_check)
        self.keep_srt_check = QCheckBox("\u4fdd\u7559 SRT \u5b57\u5e55\u6587\u4ef6")
        self.keep_srt_check.setToolTip("\u5904\u7406\u5b8c\u6210\u540e\u4fdd\u7559 .srt \u6587\u4ef6\uff0c\u65b9\u4fbf\u4e8c\u6b21\u7f16\u8f91")
        corr_layout.addWidget(self.keep_srt_check)
        corr_layout.addStretch()
        subtitle_layout.addLayout(corr_layout)
        subtitle_group.setLayout(subtitle_layout)

        self.start_btn = QPushButton("\u5f00\u59cb\u6dfb\u52a0\u5b57\u5e55")
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self.start_subtitle)

        self.cancel_btn = QPushButton("\u505c\u6b62")
        self.cancel_btn.setMinimumHeight(36)
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel_subtitle)

        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.cancel_btn)

        self.progress_bar = QProgressBar()

        self.stats_label = QLabel("\u7edf\u8ba1: \u7b49\u5f85\u5904\u7406...")
        self.stats_label.setStyleSheet("font-weight: bold; padding: 5px;")

        self.result_table = QTableWidget()
        self.result_table.setColumnCount(3)
        self.result_table.setHorizontalHeaderLabels(["\u89c6\u9891\u6587\u4ef6", "\u8f93\u51fa\u6587\u4ef6", "\u72b6\u6001"])
        self.result_table.setMinimumHeight(180)
        self.result_table.horizontalHeader().setStretchLastSection(True)

        desc_label = QLabel(
            "\u529f\u80fd\u8bf4\u660e\uff1a\n"
            "1. \u652f\u6301 FireRedASR / FunASR Paraformer / SenseVoice / Whisper \u56db\u79cd\u8bed\u97f3\u8bc6\u522b\u5f15\u64ce\n"
            "2. \u667a\u80fd\u8bed\u97f3\u8bc6\u522b\u81ea\u52a8\u751f\u6210\u5b57\u5e55\uff0c\u652f\u6301\u4e2d\u82f1\u6587\n"
            "3. \u652f\u6301\u81ea\u5b9a\u4e49\u5b57\u4f53\u3001\u5b57\u53f7\u3001\u5b57\u4f53\u989c\u8272\u3001\u63cf\u8fb9\u989c\u8272\u548c\u63cf\u8fb9\u7c97\u7ec6\n"
            "4. \u652f\u6301\u5b57\u5e55\u4f4d\u7f6e\u9009\u62e9\uff08\u4e0a/\u4e2d/\u4e0b\uff09\n"
            "5. \u5b57\u5e55\u4f1a\u786c\u7f16\u7801\u5230\u89c6\u9891\u4e2d"
        )
        desc_label.setStyleSheet("color: gray; padding: 10px;")

        layout.addWidget(input_group)
        layout.addWidget(subtitle_group)
        layout.addLayout(btn_layout)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.stats_label)
        layout.addWidget(self.result_table, 1)
        layout.addWidget(desc_label)

        scroll.setWidget(content)
        outer_layout.addWidget(scroll)
        self.setLayout(outer_layout)

    def _check_ollama(self):
        from PyQt5.QtCore import QThread, pyqtSignal

        class OllamaChecker(QThread):
            result = pyqtSignal(bool)

            def run(self):
                try:
                    import urllib.request, json
                    req = urllib.request.Request("http://localhost:11434/api/tags")
                    with urllib.request.urlopen(req, timeout=2) as resp:
                        data = json.loads(resp.read())
                        models = [m["name"] for m in data.get("models", [])]
                        self.result.emit(any("minicpm" in m.lower() for m in models))
                except Exception:
                    self.result.emit(False)

        self._ollama_checker = OllamaChecker()
        self._ollama_checker.result.connect(self._on_ollama_checked)
        self.correction_check.setVisible(False)
        self._ollama_checker.start()

    def _on_ollama_checked(self, has_model):
        self.correction_check.setVisible(True)
        self.correction_check.setEnabled(has_model)
        if not has_model:
            self.correction_check.setToolTip("检测到 Ollama 但未安装 MiniCPM3-4B 模型")

    def _on_model_changed(self, idx):
        from gui.config import get_config
        mt = self.model_combo.currentText()
        if "FunASR" in mt:
            self.model_path_input.setPlaceholderText("FunASR Paraformer ONNX 模型目录...")
            self.model_path_container.hide()
            saved = get_config("settings", "funasr_model_path", FUNASR_DIR)
            self.model_path_input.setText(saved)
        elif "SenseVoice" in mt:
            self.model_path_input.setPlaceholderText("SenseVoiceSmall ONNX 模型目录...")
            self.model_path_container.hide()
            saved = get_config("settings", "sensevoice_model_path", SENSEVOICE_DIR)
            self.model_path_input.setText(saved)
        elif "Whisper" in mt:
            self.model_path_input.setPlaceholderText("Whisper 模型名称或目录 (如 large-v3)...")
            self.model_path_container.show()
            saved = get_config("settings", "whisper_model_dir", "")
            self.model_path_input.setText(saved)
        else:
            self.model_path_input.setPlaceholderText("FireRedASR ONNX 模型目录...")
            self.model_path_container.hide()
            saved = get_config("settings", "fireredasr_model_path", FIREMODELS_DIR)
            self.model_path_input.setText(saved)

    def showEvent(self, event):
        super().showEvent(event)
        from gui.config import get_config
        mt = self.model_combo.currentText()
        if "FunASR" in mt:
            saved = get_config("settings", "funasr_model_path", FUNASR_DIR)
        elif "SenseVoice" in mt:
            saved = get_config("settings", "sensevoice_model_path", SENSEVOICE_DIR)
        elif "Whisper" in mt:
            saved = get_config("settings", "whisper_model_dir", "")
        else:
            saved = get_config("settings", "fireredasr_model_path", FIREMODELS_DIR)
        if saved:
            self.model_path_input.setText(saved)
        self._check_ollama()

    def load_config(self):
        mt = get_config("subtitle", "model_type", "FireRedASR")
        idx = self.model_combo.findText(mt)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        self._on_model_changed(idx if idx >= 0 else 0)

        self.folder_input.setText(get_config("subtitle", "folder", ""))
        self.output_input.setText(get_config("subtitle", "output", ""))
        self.font_combo.setCurrentText(get_config("subtitle", "font", "SimHei"))
        self.font_size.setValue(int(get_config("subtitle", "font_size", "12")))
        self.current_color = get_config("subtitle", "color", "#FFFFFF")
        self.color_label.setStyleSheet(f"background-color: {self.current_color}; padding: 5px; border: 1px solid black;")
        self.current_outline_color = get_config("subtitle", "outline_color", "#000000")
        self.outline_color_label.setStyleSheet(f"background-color: {self.current_outline_color}; padding: 5px; border: 1px solid gray;")
        self.outline_width.setValue(int(get_config("subtitle", "outline_width", "2")))
        self.position_combo.setCurrentText(get_config("subtitle", "position", "\u4e0b"))

        cur_mt = self.model_combo.currentText()
        if "FunASR" in cur_mt:
            default_path = get_config("settings", "funasr_model_path", FUNASR_DIR)
        elif "SenseVoice" in cur_mt:
            default_path = get_config("settings", "sensevoice_model_path", SENSEVOICE_DIR)
        elif "Whisper" in cur_mt:
            default_path = get_config("settings", "whisper_model_dir", "")
        else:
            default_path = get_config("settings", "fireredasr_model_path", FIREMODELS_DIR)
        self.model_path_input.setText(get_config("subtitle", "model_path", default_path))

    def save_config(self):
        set_config("subtitle", "model_type", self.model_combo.currentText())
        set_config("subtitle", "folder", self.folder_input.text())
        set_config("subtitle", "output", self.output_input.text())
        set_config("subtitle", "font", self.font_combo.currentText())
        set_config("subtitle", "font_size", str(self.font_size.value()))
        set_config("subtitle", "color", self.current_color)
        set_config("subtitle", "outline_color", self.current_outline_color)
        set_config("subtitle", "outline_width", str(self.outline_width.value()))
        set_config("subtitle", "position", self.position_combo.currentText())
        set_config("subtitle", "model_path", self.model_path_input.text())
        set_config("subtitle", "enable_correction", "true" if self.correction_check.isChecked() else "false")

    def choose_font_color(self):
        from PyQt5.QtGui import QColor
        color = QColorDialog.getColor(QColor(self.current_color), self, "\u9009\u62e9\u5b57\u4f53\u989c\u8272")
        if color.isValid():
            self.current_color = color.name()
            self.color_label.setStyleSheet(f"background-color: {self.current_color}; padding: 5px; border: 1px solid black;")

    def choose_outline_color(self):
        from PyQt5.QtGui import QColor
        color = QColorDialog.getColor(QColor(self.current_outline_color), self, "\u9009\u62e9\u63cf\u8fb9\u989c\u8272")
        if color.isValid():
            self.current_outline_color = color.name()
            self.outline_color_label.setStyleSheet(f"background-color: {self.current_outline_color}; padding: 5px; border: 1px solid gray;")

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "\u9009\u62e9\u89c6\u9891\u6587\u4ef6\u5939")
        if folder:
            self.folder_input.setText(folder)
            self.save_config()

    def browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "\u9009\u62e9\u8f93\u51fa\u6587\u4ef6\u5939")
        if folder:
            self.output_input.setText(folder)
            self.save_config()

    def browse_model(self):
        mt = self.model_combo.currentText()
        if "Whisper" in mt:
            file_path, _ = QFileDialog.getOpenFileName(
                self, "\u9009\u62e9 Whisper \u6a21\u578b .pt \u6587\u4ef6",
                "", "Whisper \u6a21\u578b (*.pt);;\u6240\u6709\u6587\u4ef6 (*.*)"
            )
            if file_path:
                self.model_path_input.setText(file_path)
                self.save_config()
        else:
            folder = QFileDialog.getExistingDirectory(self, "\u9009\u62e9 ASR \u6a21\u578b\u76ee\u5f55")
            if folder:
                self.model_path_input.setText(folder)
                self.save_config()

    def start_subtitle(self):
        folder = self.folder_input.text()
        output = self.output_input.text()

        if not folder or not output:
            QMessageBox.warning(self, "\u8b66\u544a", "\u8bf7\u9009\u62e9\u8f93\u5165\u548c\u8f93\u51fa\u6587\u4ef6\u5939")
            return

        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "\u8b66\u544a", "\u4efb\u52a1\u6b63\u5728\u6267\u884c\u4e2d")
            return

        self.save_config()
        self.start_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.result_table.setRowCount(0)
        self.stats_label.setText("\u7edf\u8ba1: \u5904\u7406\u4e2d...")
        self.results = []

        self.worker = SubtitleWorker(
            folder, output,
            self.font_combo.currentText(),
            self.font_size.value(),
            self.current_color,
            self.current_outline_color,
            self.outline_width.value(),
            self.position_combo.currentText(),
            self.model_path_input.text(),
            self.model_combo.currentText(),
            enable_correction=self.correction_check.isChecked(),
            keep_srt=self.keep_srt_check.isChecked()
        )
        self.worker.progress.connect(self.on_progress)
        self.worker.video_done.connect(self.on_video_done)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def cancel_subtitle(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.stats_label.setText("\u7edf\u8ba1: \u5df2\u505c\u6b62")

    def on_progress(self, current, total):
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)

    def on_video_done(self, result):
        self.results.append(result)
        row = self.result_table.rowCount()
        self.result_table.insertRow(row)

        video_name = os.path.basename(result["video"])
        self.result_table.setItem(row, 0, QTableWidgetItem(video_name))

        if result["success"]:
            output_name = os.path.basename(result["output_path"])
            self.result_table.setItem(row, 1, QTableWidgetItem(output_name))
            status_item = QTableWidgetItem("\u6210\u529f")
        else:
            self.result_table.setItem(row, 1, QTableWidgetItem("-"))
            status_item = QTableWidgetItem("\u5931\u8d25")
            status_item.setForeground(Qt.red)
        self.result_table.setItem(row, 2, status_item)

    def on_finished(self, results):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(100)

        success_count = sum(1 for r in results if r["success"])
        fail_count = len(results) - success_count

        msg = f"\u7edf\u8ba1: \u5171{len(results)}\u4e2a\u89c6\u9891, \u6210\u529f{success_count}\u4e2a"
        if fail_count > 0:
            msg += f", \u5931\u8d25{fail_count}\u4e2a"
        self.stats_label.setText(msg)

        QMessageBox.information(self, "\u5b8c\u6210", msg)

    def on_error(self, msg):
        self.start_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.stats_label.setText("\u7edf\u8ba1: \u5904\u7406\u5931\u8d25")
        QMessageBox.critical(self, "\u9519\u8bef", msg)
