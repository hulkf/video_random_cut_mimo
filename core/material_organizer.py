"""安全归档千川素材压缩包或视频文件。"""
from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from utils.media_utils import VIDEO_EXTS

MATERIAL_NAMES = {
    "model": "模特素材", "模特": "模特素材", "模特素材": "模特素材",
    "flat": "平铺素材", "平铺": "平铺素材", "平铺素材": "平铺素材",
}


def _infer_cargo(filename: str) -> str:
    match = re.search(r"(.+?)视频", Path(filename).stem, flags=re.IGNORECASE)
    if not match or not match.group(1).strip(" _-—"):
        raise ValueError("无法从文件名识别货号：文件名中未找到“视频”前的货号")
    return match.group(1).strip(" _-—")


def _unique_path(folder: Path, name: str) -> Path:
    candidate = folder / Path(name.replace("\\", "/")).name
    if not candidate.exists():
        return candidate
    for index in range(2, 1000000):
        candidate = folder / f"{candidate.stem}_{index}{candidate.suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("无法为文件生成唯一名称")


def _download_url(url: str, temp_dir: Path) -> Path:
    name = Path(unquote(Path(urlparse(url).path).name)).name or "material.zip"
    target = temp_dir / name
    response = requests.get(url, stream=True, timeout=(30, 300))
    response.raise_for_status()
    with target.open("wb") as stream:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                stream.write(chunk)
    return target


def organize_materials(source_path, *, material_type="model", cargo_number="",
                       output_root=r"D:\千川素材", delete_archive=True) -> dict:
    """归档来源中的视频；压缩包只提取视频文件并安全展平到分类目录。"""
    if material_type not in MATERIAL_NAMES:
        raise ValueError("material_type 必须是 model/flat（或模特/平铺）")
    sources = source_path if isinstance(source_path, (list, tuple)) else [source_path]
    if not sources:
        raise ValueError("source_path 不能为空")
    local_paths, temp_dirs = [], []
    try:
        for raw in sources:
            value = str(raw)
            if value.startswith(("http://", "https://")):
                temp_dir = Path(tempfile.mkdtemp(prefix="material_download_"))
                temp_dirs.append(temp_dir)
                local_paths.append(_download_url(value, temp_dir))
            else:
                path = Path(value).expanduser().resolve()
                if not path.exists():
                    raise FileNotFoundError(f"输入路径不存在: {path}")
                local_paths.append(path)
        cargo = str(cargo_number).strip() or _infer_cargo(local_paths[0].name)
        cargo_dir = Path(output_root).expanduser().resolve() / cargo
        material_dir = cargo_dir / MATERIAL_NAMES[material_type]
        material_dir.mkdir(parents=True, exist_ok=True)
        videos, stored, deleted, kinds = [], [], [], []
        for path in local_paths:
            if path.suffix.lower() == ".zip":
                kinds.append("archive")
                archive_copy = _unique_path(cargo_dir, path.name)
                if path.resolve() != archive_copy.resolve():
                    shutil.copy2(path, archive_copy)
                stored.append(str(archive_copy))
                with zipfile.ZipFile(path) as archive:
                    for member in archive.infolist():
                        if member.is_dir() or Path(member.filename).suffix.lower() not in VIDEO_EXTS:
                            continue
                        target = _unique_path(material_dir, member.filename)
                        with archive.open(member) as source, target.open("wb") as destination:
                            shutil.copyfileobj(source, destination)
                        videos.append(str(target))
                if delete_archive and archive_copy.exists():
                    archive_copy.unlink()
                    deleted.append(str(archive_copy))
            elif path.is_file() and path.suffix.lower() in VIDEO_EXTS:
                kinds.append("video")
                target = _unique_path(material_dir, path.name)
                shutil.copy2(path, target)
                videos.append(str(target))
            elif path.is_dir():
                kinds.append("folder")
                for video in sorted(path.rglob("*")):
                    if video.is_file() and video.suffix.lower() in VIDEO_EXTS:
                        target = _unique_path(material_dir, video.name)
                        shutil.copy2(video, target)
                        videos.append(str(target))
            else:
                raise ValueError(f"不支持的输入：{path}")
        if not videos:
            raise ValueError("输入中没有找到可归档的视频文件")
        return {"cargo_number": cargo, "material_type": MATERIAL_NAMES[material_type],
                "material_folder": str(material_dir), "videos": videos,
                "video_count": len(videos), "source_kinds": sorted(set(kinds)),
                "stored_archives": stored, "deleted_archives": deleted,
                "archive_deleted": bool(deleted),
                "optimization_required": MATERIAL_NAMES[material_type] == "平铺素材"}
    finally:
        for temp_dir in temp_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)
