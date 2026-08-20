# -*- coding: utf-8 -*-
"""函数式配置模块：普通配置（config.json）+ 密钥独立存储（config.local.json）。

P0 安全改造（AC-P0-5）：
  - 保留 load_config/save_config/get_config/set_config 四个历史函数签名（15 页仍在用，接口不变）；
  - 密钥（api_key / secret_key 等）独立存储到 config.local.json，不入库、不进 git；
  - 模块级 _cache 内存缓存：get_config 基于缓存读，set_config 改缓存并立即原子写盘；
  - bool 统一序列化：set_config 存小写 "true"/"false"；get_config 读取时兼容历史
    "True"/"False"/"1"/"yes" 等写法（default 为 bool 时返回 bool，否则返回原字符串）。
"""
import json
import os
import tempfile

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.json")
SECRET_PATH = os.path.join(os.path.dirname(__file__), "..", "config.local.json")

# 模块级缓存：None=未加载；dict=已加载的普通配置（密钥不进入此缓存）
_cache = None

# ── 原子写盘 ────────────────────────────────────────────────
_TRUE_VALUES = ("true", "1", "yes", "on")


def _atomic_write_json(path: str, data) -> None:
    """原子写 JSON：写临时文件 + os.replace，避免中途崩溃损坏配置。"""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".config_tmp_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise


# ── 普通配置（config.json）───────────────────────────────────
def load_config() -> dict:
    """加载普通配置（带内存缓存；文件被外部修改时用 reload_config 强制重读）。"""
    global _cache
    if _cache is not None:
        return _cache
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                _cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            _cache = {}
    else:
        _cache = {}
    return _cache


def reload_config() -> dict:
    """强制从磁盘重读普通配置（返回新缓存）。"""
    global _cache
    _cache = None
    return load_config()


def save_config(config: dict) -> None:
    """全量保存普通配置（原子写盘；同时更新内存缓存）。"""
    global _cache
    _atomic_write_json(CONFIG_PATH, config)
    _cache = config


def _coerce_bool(value, default) -> object:
    """bool 读取兼容：default 为 bool 时把字符串/原值统一成 bool。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    # 非 bool 默认或无法识别的值：保持原样（向后兼容）
    if isinstance(default, bool):
        try:
            return bool(int(value))
        except (TypeError, ValueError):
            return bool(default)
    return value


def get_config(section: str, key: str, default=""):
    """读取普通配置。default 为 bool 时返回 bool（兼容历史 "True"/"False" 字符串）。"""
    config = load_config()
    value = config.get(section, {}).get(key, default)
    if isinstance(default, bool):
        return _coerce_bool(value, default)
    return value


def set_config(section: str, key: str, value) -> None:
    """写入普通配置：bool 统一存小写 "true"/"false"，立即原子写盘。"""
    config = load_config()
    if section not in config or not isinstance(config[section], dict):
        config[section] = {}
    if isinstance(value, bool):
        value = "true" if value else "false"
    config[section][key] = value
    save_config(config)


# ── 密钥独立存储（config.local.json）─────────────────────────
_SECRET_CACHE = None


def _load_secrets() -> dict:
    global _SECRET_CACHE
    if _SECRET_CACHE is not None:
        return _SECRET_CACHE
    if os.path.exists(SECRET_PATH):
        try:
            with open(SECRET_PATH, "r", encoding="utf-8") as f:
                _SECRET_CACHE = json.load(f)
        except (json.JSONDecodeError, OSError):
            _SECRET_CACHE = {}
    else:
        _SECRET_CACHE = {}
    return _SECRET_CACHE


def get_secret(section: str, key: str, default: str = "") -> str:
    """读取密钥（config.local.json）。与普通配置完全隔离。"""
    secrets = _load_secrets()
    value = secrets.get(section, {}).get(key, default)
    return value if isinstance(value, str) else default


def set_secret(section: str, key: str, value: str) -> None:
    """写入密钥（config.local.json，原子写盘）。空值也会覆盖旧值。"""
    global _SECRET_CACHE
    secrets = _load_secrets()
    if section not in secrets or not isinstance(secrets[section], dict):
        secrets[section] = {}
    secrets[section][key] = value if isinstance(value, str) else str(value)
    _atomic_write_json(SECRET_PATH, secrets)
    _SECRET_CACHE = secrets
