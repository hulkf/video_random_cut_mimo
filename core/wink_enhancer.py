"""Wink 画质修复引擎（调用美图 Wink 官方 CLI，复用本地已登录账号）。

背景
----
Wink 桌面端没有公开 API，但安装目录里藏了一个官方 CLI 入口
（``Wink.exe --cli picture_quality``）。它直接复用客户端本地登录态，
不需要自己处理 token 和签名，是目前最稳的自动化路径。

**处理在云端进行**：本地把整个文件上传到美图服务器，处理完再整个下载回来。
实测上传约 1.2 MB/s，所以大文件耗时主要花在传输上，断网即失败。

已知的 CLI 坑（本模块已全部填平）
--------------------------------
1. ``--output`` 参数完全失效（实测 3.7.5，带不带尾部反斜杠都一样），
   结果永远写到客户端设置里的默认输出目录。
   -> 解析 stdout 里"结果路径："拿到真实路径，再自行搬运到目标目录。
2. ``--dry-run`` 是陷阱：名字像空跑，实际照样真跑真计费。所以本模块**不使用**它。
3. ``--async`` 是空壳，仍然同步阻塞，因此这里就按同步处理并放到 QThread 里跑。
4. 子进程默认会弹控制台黑窗，批量跑几十个文件会疯狂闪屏。
   -> Windows 下统一加 ``CREATE_NO_WINDOW``。

计费
----
档位价格由账号 VIP 状态决定（实测 VIP 为 0 美豆，非 VIP 每次 6 美豆）。
CLI 输出里会打印"消耗美豆：N"，本模块会解析出来汇总，方便用户盯着余额。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time

from utils.media_utils import VIDEO_EXTS as _BASE_VIDEO_EXTS

# ---------------------------------------------------------------- 常量

#: 画质修复档位表（CLI 的 --level 取值）
LEVELS = {
    1: "高清",
    2: "超清",
    3: "人像增强",
    4: "AI超清",
    5: "商品图",
    6: "文字图表",
    7: "游戏",
    8: "动漫",
    9: "高糊图",
    10: "演唱会",
}

#: 仅支持图片的档位。传视频进去会白白上传一遍才失败，所以提前拦截。
IMAGE_ONLY_LEVELS = {5, 6, 9}

#: 从公共 VIDEO_EXTS 派生（保持 8 元组语义不变）
VIDEO_EXTS = _BASE_VIDEO_EXTS + (".webm", ".m4v", ".wmv")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")

#: CLI 成功时打印"结果路径："，真实路径通常在**下一行**
RESULT_MARKER = "结果路径"
BEAN_RE = re.compile(r"消耗美豆[：:]\s*(\d+)")

#: Wink 常见安装位置，用于自动探测
_WINK_ROOT_CANDIDATES = (
    r"D:\software\Meitu\Wink",
    r"C:\Program Files\Meitu\Wink",
    r"C:\Program Files (x86)\Meitu\Wink",
    os.path.expandvars(r"%LOCALAPPDATA%\Meitu\Wink"),
    os.path.expandvars(r"%APPDATA%\Meitu\Wink"),
)

# Windows 下隐藏子进程控制台窗口
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------- 路径探测

def _version_key(name):
    """把 "3.7.5" 这类目录名转成可比较的元组，非数字段落排最后。"""
    parts = []
    for seg in name.split("."):
        parts.append(int(seg) if seg.isdigit() else -1)
    return parts


def find_wink_exe():
    """自动探测可用的 Wink.exe。

    注意：安装根目录下的 ``Wink.exe`` 只是个启动器，``--cli`` 不输出任何东西。
    真正能用的是版本子目录（如 ``3.7.5\\Wink.exe``），所以这里**优先取版本目录下
    版本号最高的那个**，实在找不到才退回根目录。
    """
    fallback = None
    for root in _WINK_ROOT_CANDIDATES:
        if not root or not os.path.isdir(root):
            continue

        # 先找版本子目录
        versions = []
        try:
            for name in os.listdir(root):
                sub = os.path.join(root, name)
                if not os.path.isdir(sub):
                    continue
                exe = os.path.join(sub, "Wink.exe")
                if os.path.isfile(exe) and name[:1].isdigit():
                    versions.append((_version_key(name), exe))
        except OSError:
            pass

        if versions:
            versions.sort(reverse=True)
            return versions[0][1]

        root_exe = os.path.join(root, "Wink.exe")
        if fallback is None and os.path.isfile(root_exe):
            fallback = root_exe

    return fallback


def collect_media(folder, include_images=False):
    """递归收集文件夹里的媒体文件，返回排序后的绝对路径列表（支持目录或单文件输入）。"""
    from utils.media_utils import collect_files
    exts = VIDEO_EXTS + (IMAGE_EXTS if include_images else ())
    return [os.path.abspath(p) for p in collect_files(folder, exts)]


def is_video(path):
    return path.lower().endswith(VIDEO_EXTS)


def human_size(n):
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024.0
    return str(n)


# ---------------------------------------------------------------- 引擎

class WinkEnhancer:
    """单文件画质修复执行器。

    用法::

        engine = WinkEnhancer(level=2)
        result = engine.process(r"D:\\a.mp4", r"D:\\out\\a.mp4")

    ``process`` 返回 dict::

        {"success": bool, "output": str|None, "beans": int,
         "elapsed": float, "error": str}
    """

    def __init__(self, exe_path=None, level=2, timeout=1800):
        self.exe_path = exe_path or find_wink_exe()
        self.level = level
        self.timeout = timeout
        self._proc = None
        self._stopped = False

    # -------------------------------------------------- 校验

    def validate(self):
        """启动前自检，返回错误描述；没问题返回 None。"""
        if not self.exe_path:
            return (
                "没有找到 Wink 客户端。请先安装美图 Wink 桌面版，"
                "或在下方手动指定 Wink.exe 路径。"
            )
        if not os.path.isfile(self.exe_path):
            return f"Wink 路径不存在：{self.exe_path}"
        if self.level not in LEVELS:
            return f"不支持的档位：{self.level}"
        return None

    # -------------------------------------------------- 停止

    def stop(self):
        """请求中止。会连带杀掉 Wink 拉起的子进程树。"""
        self._stopped = True
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                # Wink CLI 会拉起子进程，必须整棵树杀，否则会留残留进程
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=_NO_WINDOW,
                )
            else:
                proc.terminate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    @property
    def stopped(self):
        return self._stopped

    # -------------------------------------------------- 执行

    def process(self, input_path, output_path=None):
        """处理单个文件。``output_path`` 为最终期望落地的完整文件路径。"""
        t0 = time.time()

        err = self.validate()
        if err:
            return self._fail(err, t0)

        if not os.path.isfile(input_path):
            return self._fail(f"输入文件不存在：{input_path}", t0)

        if self.level in IMAGE_ONLY_LEVELS and is_video(input_path):
            return self._fail(
                f"档位「{LEVELS[self.level]}」仅支持图片，跳过视频", t0
            )

        cmd = [
            self.exe_path, "--cli", "picture_quality",
            "--level", str(self.level),
            "--input", input_path,
        ]

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=_NO_WINDOW,
            )
            raw_out, raw_err = self._proc.communicate(timeout=self.timeout)
            returncode = self._proc.returncode
        except subprocess.TimeoutExpired:
            self.stop()
            return self._fail(f"超时（超过 {self.timeout} 秒）", t0)
        except Exception as exc:
            return self._fail(f"启动 Wink 失败：{exc}", t0)
        finally:
            self._proc = None

        if self._stopped:
            return self._fail("已手动停止", t0)

        combined = (
            (raw_out or b"").decode("utf-8", "replace")
            + "\n"
            + (raw_err or b"").decode("utf-8", "replace")
        )
        lines = [ln.strip() for ln in combined.splitlines() if ln.strip()]

        beans = 0
        m = BEAN_RE.search(combined)
        if m:
            beans = int(m.group(1))

        result_path = self._parse_result_path(lines)

        if not result_path:
            if returncode != 0:
                tail = " | ".join(lines[-3:]) if lines else "无输出"
                return self._fail(f"CLI 返回码 {returncode}：{tail}", t0, beans)
            tail = " | ".join(lines[-3:]) if lines else "无输出"
            return self._fail(f"没解析到结果路径：{tail}", t0, beans)

        if not os.path.isfile(result_path):
            return self._fail(
                f"CLI 报了结果路径但文件不存在：{result_path}", t0, beans
            )

        final = self._relocate(result_path, output_path)

        return {
            "success": True,
            "output": final,
            "beans": beans,
            "elapsed": time.time() - t0,
            "error": "",
        }

    # -------------------------------------------------- 内部

    @staticmethod
    def _parse_result_path(lines):
        """从 CLI 输出里抠出结果文件真实路径。

        兼容两种格式：路径跟在"结果路径："同一行冒号后面，或者单独占下一行。

        注意：**只能从标记后面切第一个冒号**。之前图省事写成
        ``.split("：")[-1].split(":")[-1]``，结果把 ``D:/a/b.mp4`` 的盘符
        也当分隔符切掉了，得到 ``/a/b.mp4``。
        """
        for i, line in enumerate(lines):
            pos = line.find(RESULT_MARKER)
            if pos < 0:
                continue

            rest = line[pos + len(RESULT_MARKER):]
            # 只认标记紧后面的那一个冒号，盘符里的冒号不受影响
            sep = min(
                (idx for idx in (rest.find("："), rest.find(":")) if idx >= 0),
                default=-1,
            )
            tail = rest[sep + 1:].strip() if sep >= 0 else rest.strip()

            if tail:
                candidate = tail
            elif i + 1 < len(lines):
                candidate = lines[i + 1]
            else:
                return None
            return os.path.normpath(candidate.replace("/", os.sep))
        return None

    @staticmethod
    def _relocate(result_path, output_path):
        """把 Wink 默认目录里的成品搬到用户指定位置。

        搬运失败不算致命错误——文件确实产出来了，只是没挪窝，
        所以退回原路径让用户还能拿到东西。
        """
        if not output_path:
            return result_path

        if os.path.abspath(output_path).lower() == os.path.abspath(result_path).lower():
            return result_path

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        # 同名冲突加序号，绝不默默覆盖用户已有文件
        dest = output_path
        base, ext = os.path.splitext(output_path)
        n = 1
        while os.path.exists(dest):
            dest = f"{base}({n}){ext}"
            n += 1

        try:
            shutil.move(result_path, dest)
            return dest
        except Exception:
            return result_path

    @staticmethod
    def _fail(msg, t0, beans=0):
        return {
            "success": False,
            "output": None,
            "beans": beans,
            "elapsed": time.time() - t0,
            "error": msg,
        }


def build_output_path(input_path, input_folder, output_folder, level):
    """算出单个文件的目标落地路径，保留输入目录下的子文件夹结构。"""
    rel = os.path.relpath(input_path, input_folder)
    rel_dir = os.path.dirname(rel)
    stem, ext = os.path.splitext(os.path.basename(rel))
    name = f"{stem}_画质修复_{LEVELS.get(level, level)}{ext}"
    return os.path.join(output_folder, rel_dir, name)
