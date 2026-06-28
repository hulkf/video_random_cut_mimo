import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.json")


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get_config(section, key, default=""):
    config = load_config()
    return config.get(section, {}).get(key, default)


def set_config(section, key, value):
    config = load_config()
    if section not in config:
        config[section] = {}
    config[section][key] = value
    save_config(config)
