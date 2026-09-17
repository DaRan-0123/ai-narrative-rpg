"""
配置管理模块
"""
import copy
import os
import json
import tempfile
from pathlib import Path

CONFIG_PATH = Path.home() / ".ai_rpg_config.json"

DEFAULT_CONFIG = {
    "api_keys": {
        "openai": "",
        "anthropic": "",
        "deepseek": "",
        "gemini": ""
    },
    "models": {
        "main": {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",  # 2026-08-05 用户定：0731发布后flash已优于pro，主力模型全换flash
            "api_base": "https://api.deepseek.com/v1"
        },
        "lightweight": {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_base": "https://api.deepseek.com/v1"
        }
    },
    "game": {
        "history_limit": 10,
        "archive_trigger": 250,
        "archive_chunk": 100,
        "hot_layer_size": 200
    },
    "ui": {
        "theme": "darkly",
        "font_family": "Microsoft YaHei",
        "font_size": 12,
        "resolution": "1800×1350"  # 2026-08-14 用户定：唯一分辨率
    }
}


class Config:
    def __init__(self):
        # deepcopy：防止 set/_deep_update 原地改嵌套 dict 污染模块级 DEFAULT_CONFIG
        self.data = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self):
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    self._deep_update(self.data, loaded)
            except Exception:
                pass

    def save(self):
        """原子写配置：先写临时文件再 os.replace，崩溃不损坏配置"""
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(CONFIG_PATH.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, CONFIG_PATH)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception as e:
            print(f"保存配置失败: {e}")

    def get(self, *keys, default=None):
        d = self.data
        for k in keys:
            if isinstance(d, dict) and k in d:
                d = d[k]
            else:
                return default
        return d

    def set(self, *keys, value):
        d = self.data
        for k in keys[:-1]:
            if k not in d:
                d[k] = {}
            d = d[k]
        d[keys[-1]] = value
        self.save()

    def _deep_update(self, base, update):
        for k, v in update.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                self._deep_update(base[k], v)
            else:
                base[k] = v

    def get_api_key(self, provider):
        key = self.get("api_keys", provider, default="")
        if not key:
            key = os.environ.get(f"{provider.upper()}_API_KEY", "")
        return key

    def get_model_config(self, model_type):
        return self.get("models", model_type, default={})


_config_instance = None


def get_config():
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance
