from PyQt5.QtWidgets import QTabWidget, QVBoxLayout

from gui.common.base_tab import BaseTab
from gui.video_concat_tab import VideoConcatPage


class TemplateTab(BaseTab):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.template_tabs = QTabWidget()
        self.template_tabs.setDocumentMode(True)
        self.template_001_page = VideoConcatPage(
            "video_concat_template_001", template_id="001"
        )
        self.template_tabs.addTab(self.template_001_page, "模板001")
        layout.addWidget(self.template_tabs)

    def load_config(self):
        self.template_001_page.load_config()

    def save_config(self):
        self.template_001_page.save_config()
