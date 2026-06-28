import qt_material  # noqa: E402 (must come after PyQt5 import in caller)
from qt_material import apply_stylesheet


# 强制覆盖 qt_material 主题对控件尺寸的限制（追加到主题 QSS 末尾）
FIX_LAYOUT_QSS = """
/* ===== 按钮尺寸修复 ===== */
QPushButton {
    min-width: 60px;
    min-height: 28px;
    padding: 4px 12px;
}
QPushButton[text*="浏览"] {
    min-width: 72px;
}

/* ===== 输入框高度修复 ===== */
QLineEdit {
    min-height: 30px;
    padding: 2px 6px;
}

/* ===== 下拉框/数字框高度修复 ===== */
QSpinBox,
QDoubleSpinBox,
QComboBox {
    min-height: 28px;
    padding: 2px 8px;
}

/* ===== 勾选框间距修复 ===== */
QCheckBox {
    min-height: 26px;
    spacing: 8px;
}

/* ===== GroupBox 内边距 ===== */
QGroupBox {
    margin-top: 10px;
    padding-top: 20px;
}

/* ===== 表格行高 ===== */
QTableWidget {
    min-height: 200px;
}
"""

# checkbox indicator 样式（独立于上面的布局修复）
CHECKBOX_QSS = """
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1.5px solid #aaaaaa;
    border-radius: 3px;
    background-color: #2a2a2a;
}
QCheckBox::indicator:hover {
    border-color: #ffffff;
    background-color: #3a3a3a;
}
QCheckBox::indicator:checked {
    background-color: #26a69a;
    border-color: #26a69a;
}
QCheckBox::indicator:disabled {
    background-color: #3a3a3a;
    border-color: #666666;
}
"""


def build_extra_qss(font_size):
    """根据字体大小生成附加 QSS。"""
    return f"""
QWidget {{
    font-size: {font_size}pt;
    font-family: "Microsoft YaHei", "SimHei", "Segoe UI", sans-serif;
}}
{FIX_LAYOUT_QSS}
{CHECKBOX_QSS}
"""


def apply_theme_and_font(app, theme, font_size):
    """统一应用主题 + 字体大小 + 布局修复。

    先 apply_stylesheet 写入主题 QSS → 读出 → 追加自定义 QSS（布局修复+checkbox）。
    追加的规则在 CSS 级联中优先级更高，可覆盖主题对控件尺寸的限制。
    """
    apply_stylesheet(app, theme=theme)
    existing_qss = app.styleSheet() or ""
    extra_qss = build_extra_qss(font_size)
    app.setStyleSheet(existing_qss + "\n" + extra_qss)
