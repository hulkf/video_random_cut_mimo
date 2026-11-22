"""Disposable, content-addressed intermediates for template 001."""

import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import time


def prepare_cache(directory):
    root = Path(directory)
    try:
        root.mkdir(exist_ok=True)
        if root.resolve() != root.absolute() or root.is_symlink():
            return None
        entries = []
        for entry in root.glob("*.mp4"):
            if not entry.is_symlink() and entry.is_file():
                stat = entry.stat()
                entries.append((stat.st_mtime, stat.st_size, entry))
        remaining = sum(size for _, size, _ in entries)
        cutoff = time.time() - 7 * 24 * 3600
        for modified, size, entry in sorted(entries):
            if modified >= cutoff and remaining <= 4 * 1024**3:
                break
            try:
                entry.unlink()
                remaining -= size
            except OSError:
                pass
        return root
    except OSError:
        return None


def _identity(source):
    st = os.stat(source)
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _digest(source):
    with open(source, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def normalize_cached(root, source, target, profile, convert, validate):
    identity = _identity(source)
    digest = _digest(source)
    key = hashlib.sha256(repr((profile, Path(source).suffix.lower(), digest)).encode()).hexdigest()
    cached = root / (key + ".mp4")
    if cached.is_file() and not cached.is_symlink():
        try:
            shutil.copyfile(cached, target)
            if validate(target) and _identity(source) == identity:
                try:
                    os.utime(cached, None)
                except OSError:
                    pass
                return True
        except (OSError, RuntimeError, ValueError):
            pass
    convert(source, target)
    if _identity(source) != identity or _digest(source) != digest:
        raise RuntimeError("竖屏转换期间输入素材发生变化，请核对原文件")
    if not validate(target):
        return False
    try:
        with tempfile.NamedTemporaryFile(dir=root, suffix=".tmp", delete=False) as temporary:
            temp_path = Path(temporary.name)
        try:
            shutil.copyfile(target, temp_path)
            os.replace(temp_path, cached)
        finally:
            temp_path.unlink(missing_ok=True)
    except OSError:
        pass
    return False
