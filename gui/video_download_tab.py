import os
import re

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QProgressBar,
    QMessageBox, QGroupBox, QPlainTextEdit, QTableWidget,
    QTableWidgetItem, QHeaderView
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from gui.config import get_config, set_config


def extract_urls_from_text(text):
    """
    从粘贴的杂乱文本中提取所有URL，自动过滤无关文字。
    支持从淘宝/抖音APP分享文本中提取链接。
    """
    # 匹配 https:// 或 http:// 开头，到空白/中文/中文标点为止
    pattern = r'https?://[^\s\u4e00-\u9fff\uff00-\uffef\u3000-\u303f\u2018-\u201f]+'
    urls = re.findall(pattern, text)
    cleaned = []
    seen = set()
    for url in urls:
        # 去掉尾部多余标点
        url = url.rstrip('.,;:!?)\'"`')
        if url and url not in seen:
            seen.add(url)
            cleaned.append(url)
    return cleaned


class DownloadWorker(QThread):
    """视频下载后台线程"""
    log = pyqtSignal(str)
    progress = pyqtSignal(int, int, int)  # current, total, percent
    item_done = pyqtSignal(int, bool, str)  # index, success, message
    all_done = pyqtSignal(int, int)  # success_count, fail_count

    def __init__(self, links, output_dir):
        super().__init__()
        self.links = links
        self.output_dir = output_dir
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            from core.taobao_downloader import download_video, close_shared_browser
        except ImportError as e:
            self.log.emit(f"导入下载模块失败: {e}")
            self.all_done.emit(0, len(self.links))
            return

        success_count = 0
        fail_count = 0
        total = len(self.links)

        for i, url in enumerate(self.links):
            if self._stop:
                break

            url = url.strip()
            if not url:
                continue

            self.log.emit(f"[{i+1}/{total}] 处理: {url[:80]}")
            self.progress.emit(i, total, 0)

            def log_cb(msg):
                self.log.emit(f"  {msg}")

            def progress_cb(pct, downloaded, total_bytes):
                self.progress.emit(i, total, pct)

            success, result = download_video(
                url, self.output_dir,
                log_callback=log_cb,
                progress_callback=progress_cb
            )

            if success:
                success_count += 1
                self.item_done.emit(i, True, result)
                self.log.emit(f"  [OK] 下载完成: {os.path.basename(result)}")
            else:
                fail_count += 1
                self.item_done.emit(i, False, result)
                self.log.emit(f"  [ERR] {result}")

            self.progress.emit(i + 1, total, 100)

        # 批量结束，释放复用的浏览器资源
        try:
            close_shared_browser()
        except Exception:
            pass

        self.all_done.emit(success_count, fail_count)


class LoginWorker(QThread):
    """登录线程"""
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def run(self):
        try:
            from core.taobao_downloader import login_and_save
            success, msg = login_and_save(progress_callback=self.log.emit)
            self.done.emit(success, msg)
        except Exception as e:
            self.done.emit(False, str(e))


class VideoDownloadTab(QWidget):
    """综合视频下载标签页"""

    def __init__(self):
        super().__init__()
        self.worker = None
        self.login_worker = None
        self._init_ui()
        self._load_config()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # === 登录区域 ===
        login_group = QGroupBox("淘宝登录（淘宝商品页解析需要，抖音不需要登录）")
        login_layout = QHBoxLayout(login_group)
        self.btn_login = QPushButton("登录淘宝")
        self.btn_login.clicked.connect(self._on_login)
        self.lbl_auth_status = QLabel("检查中...")
        self.btn_check_auth = QPushButton("检查状态")
        self.btn_check_auth.clicked.connect(self._check_auth)
        login_layout.addWidget(self.btn_login)
        login_layout.addWidget(self.lbl_auth_status)
        login_layout.addStretch()
        login_layout.addWidget(self.btn_check_auth)
        layout.addWidget(login_group)

        # === 输出目录 ===
        out_group = QGroupBox("输出目录")
        out_layout = QHBoxLayout(out_group)
        self.edit_output = QLineEdit()
        self.edit_output.setPlaceholderText("选择视频保存目录")
        self.btn_browse = QPushButton("浏览...")
        self.btn_browse.clicked.connect(self._browse_output)
        out_layout.addWidget(self.edit_output)
        out_layout.addWidget(self.btn_browse)
        layout.addWidget(out_group)

        # === 链接输入 ===
        link_group = QGroupBox("视频链接（每行一个，支持直接粘贴分享文本）")
        link_layout = QVBoxLayout(link_group)
        self.txt_links = QPlainTextEdit()
        self.txt_links.setPlaceholderText(
            "支持多种链接类型，可直接粘贴APP分享文本，自动提取链接:\n"
            "1. 淘宝/天猫短链接: https://e.tb.cn/h.xxx\n"
            "2. 淘宝/天猫完整链接: https://detail.tmall.com/item.htm?id=123456\n"
            "3. 淘宝视频直链: https://cloud.video.taobao.com/play/u/0/p/1/e/6/t/1/1234567890.mp4\n"
            "4. 抖音商品链接: https://v.douyin.com/xxxxx/\n"
            "5. 抖音视频链接: https://www.douyin.com/video/7634032388893858545\n"
            "\n"
            "可直接粘贴如下格式文本，会自动识别链接:\n"
            "【淘宝】https://e.tb.cn/h.xxx 点击链接直接打开\n"
            "【抖音商城】https://v.douyin.com/xxx/ 长按复制此条消息"
        )
        self.txt_links.setMinimumHeight(120)
        link_layout.addWidget(self.txt_links)

        btn_layout = QHBoxLayout()
        self.btn_download = QPushButton("开始下载")
        self.btn_download.setMinimumHeight(36)
        self.btn_download.clicked.connect(self._on_download)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_download)
        btn_layout.addWidget(self.btn_stop)
        link_layout.addLayout(btn_layout)
        layout.addWidget(link_group)

        # === 进度 ===
        prog_group = QGroupBox("下载进度")
        prog_layout = QVBoxLayout(prog_group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFormat("就绪")
        prog_layout.addWidget(self.progress_bar)
        self.lbl_status = QLabel("")
        prog_layout.addWidget(self.lbl_status)
        layout.addWidget(prog_group)

        # === 日志 ===
        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout(log_group)
        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setMinimumHeight(150)
        log_layout.addWidget(self.txt_log)
        layout.addWidget(log_group, 1)

        # === 支持链接类型说明（底部） ===
        help_group = QGroupBox("支持链接类型")
        help_layout = QVBoxLayout(help_group)
        help_text = QLabel(
            "  1. 淘宝/天猫商品链接（短链接和完整链接）— 需登录淘宝\n"
            "  2. 淘宝视频直链: cloud.video.taobao.com/play/u/0/p/1/e/6/t/1/{contentId}.mp4 — 无需登录\n"
            "  3. 抖音商品链接: v.douyin.com 短链 / haohuo.jinritemai.com 链接 — 无需登录\n"
            "  4. 抖音视频链接: www.douyin.com/video/{id} — 无需登录"
        )
        help_text.setStyleSheet("color: #999; font-size: 12px;")
        help_layout.addWidget(help_text)
        layout.addWidget(help_group)

    def _load_config(self):
        output_dir = get_config("video_download", "output_dir", "")
        if not output_dir:
            output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "downloads")
        self.edit_output.setText(output_dir)
        self._check_auth()

    def _save_config(self):
        set_config("video_download", "output_dir", self.edit_output.text())

    def _browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", self.edit_output.text())
        if d:
            self.edit_output.setText(d)
            self._save_config()

    def _check_auth(self):
        try:
            from core.taobao_downloader import check_auth_file
            valid, msg = check_auth_file()
            if valid:
                self.lbl_auth_status.setText(f"<span style='color:green'>{msg}</span>")
            else:
                self.lbl_auth_status.setText(f"<span style='color:red'>{msg}</span>")
        except ImportError:
            self.lbl_auth_status.setText("<span style='color:red'>模块未安装</span>")

    def _on_login(self):
        try:
            from core.taobao_downloader import HAS_PLAYWRIGHT
            if not HAS_PLAYWRIGHT:
                QMessageBox.warning(self, "提示", "未安装 Playwright，请运行:\npip install playwright\nplaywright install chromium")
                return
        except ImportError:
            QMessageBox.warning(self, "提示", "下载模块未就绪")
            return

        self.btn_login.setEnabled(False)
        self.btn_login.setText("登录中...")
        self.login_worker = LoginWorker()
        self.login_worker.log.connect(self._log)
        self.login_worker.done.connect(self._on_login_done)
        self.login_worker.start()

    def _on_login_done(self, success, msg):
        self.btn_login.setEnabled(True)
        self.btn_login.setText("登录淘宝")
        self._log(f"登录结果: {msg}")
        self._check_auth()

    def _on_download(self):
        text = self.txt_links.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "请输入至少一个链接")
            return

        output_dir = self.edit_output.text().strip()
        if not output_dir:
            QMessageBox.warning(self, "提示", "请选择输出目录")
            return

        # 预清洗: 从杂乱文本中提取URL
        links = extract_urls_from_text(text)
        if not links:
            QMessageBox.warning(self, "提示", "未识别到有效链接")
            return

        # 显示提取到的链接
        if len(links) > 0:
            self._log(f"已识别 {len(links)} 个链接:")
            for i, link in enumerate(links):
                self._log(f"  [{i+1}] {link[:80]}")

        # 预检查: 如果包含淘宝商品链接，检查登录状态
        try:
            from core.taobao_downloader import detect_link_type, LINK_TYPE_TAOBAO_PRODUCT, check_auth_file
            has_taobao = any(detect_link_type(url) == LINK_TYPE_TAOBAO_PRODUCT for url in links)
            if has_taobao:
                valid, auth_msg = check_auth_file()
                if not valid:
                    reply = QMessageBox.question(
                        self, "淘宝登录过期",
                        f"{auth_msg}\n\n淘宝商品链接将全部失败，抖音链接不受影响。\n是否继续下载（仅抖音链接）？",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                    )
                    if reply == QMessageBox.No:
                        return
                    self._log(f"警告: {auth_msg}，淘宝链接将跳过")
        except ImportError:
            pass

        self._save_config()
        os.makedirs(output_dir, exist_ok=True)

        self.btn_download.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("准备中...")

        self.worker = DownloadWorker(links, output_dir)
        self.worker.log.connect(self._log)
        self.worker.progress.connect(self._on_progress)
        self.worker.item_done.connect(self._on_item_done)
        self.worker.all_done.connect(self._on_all_done)
        self.worker.start()

    def _on_stop(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self._log("正在停止...")

    def _on_progress(self, current, total, percent):
        if total > 0:
            overall = (current * 100 + percent) // (total * 100) if total > 0 else 0
            overall = min(overall, 100)
            self.progress_bar.setValue(overall)
            self.progress_bar.setFormat(f"[{current}/{total}] {percent}%")
            self.lbl_status.setText(f"正在下载第 {current + 1}/{total} 个视频...")

    def _on_item_done(self, index, success, message):
        status = "成功" if success else "失败"
        self._log(f"  -> 第{index+1}个: {status}")

    def _on_all_done(self, success_count, fail_count):
        self.btn_download.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.progress_bar.setValue(100)
        self.progress_bar.setFormat(f"完成: {success_count}成功, {fail_count}失败")
        self.lbl_status.setText(f"下载完成: 成功 {success_count}, 失败 {fail_count}")
        self._log(f"===== 全部完成: 成功 {success_count}, 失败 {fail_count} =====")

    def _log(self, msg):
        self.txt_log.appendPlainText(msg)
