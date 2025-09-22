import os
import json
from typing import Any, Dict

# 將外部傳入的 lang 正規化為固定集合: 'en' | 'zh-CN' | 'zh-TW'
CANONICAL_LOCALES = {"en", "zh-CN", "zh-TW"}
_LOCALE_ALIASES = {
    # 英文
    "en": "en", "en-us": "en", "en_us": "en", "en_US": "en", "EN": "en",
    # 簡體
    "zh-cn": "zh-CN", "zh_cn": "zh-CN", "zhCN": "zh-CN", "zh_CN": "zh-CN", "zh-Hans": "zh-CN",
    # 繁體
    "zh-tw": "zh-TW", "zh_tw": "zh-TW", "zhTW": "zh-TW", "zh_TW": "zh-TW", "zh-Hant": "zh-TW",
}

DEFAULT_LOCALE = "en"


def normalize_locale(lang: Any) -> str:
    """將傳入的語言代碼正規化為 'en'/'zh-CN'/'zh-TW'，空值則回傳預設英文。"""
    if not lang:
        return DEFAULT_LOCALE
    if isinstance(lang, (dict, list)):
        return DEFAULT_LOCALE
    raw = str(lang).strip()
    mapped = _LOCALE_ALIASES.get(raw, None)
    if mapped:
        return mapped
    # 寬鬆處理: 全小寫去掉空白、將底線轉連字號再比對
    lowered = raw.replace(" ", "").replace("_", "-").lower()
    return _LOCALE_ALIASES.get(lowered, DEFAULT_LOCALE)


class I18n:
    """簡單 JSON i18n 載入與渲染器。
    - 以目錄內的 *.json 為語言包（檔名即 locale）
    - 支援 key 路徑（用點號分隔）
    - render 以 str.format 代入變數
    - 找不到鍵時回退至預設語言；仍找不到則回傳 key 本身
    """

    def __init__(self, dir_path: str, default_locale: str = DEFAULT_LOCALE):
        self.dir_path = os.path.abspath(dir_path)
        self.default_locale = default_locale
        self._dict: Dict[str, Dict[str, Any]] = {}
        self._load_all()

    def _load_all(self) -> None:
        if not os.path.isdir(self.dir_path):
            return
        for fname in os.listdir(self.dir_path):
            if fname.endswith(".json"):
                locale = os.path.splitext(fname)[0]
                path = os.path.join(self.dir_path, fname)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        self._dict[locale] = json.load(f)
                except Exception:
                    # 損壞或格式錯誤時忽略以免阻塞推送
                    self._dict.setdefault(locale, {})

    def _get_any(self, locale: str, key: str) -> Any:
        data = self._dict.get(locale) or {}
        cur: Any = data
        for part in key.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
            if cur is None:
                return None
        return cur

    def t(self, key: str, locale: str) -> Any:
        val = self._get_any(locale, key)
        if val is not None:
            return val
        # 回退到預設語言
        val = self._get_any(self.default_locale, key)
        return val if val is not None else key

    def render(self, key: str, locale: str, variables: Dict[str, Any]) -> str:
        tmpl = self.t(key, locale)
        if not isinstance(tmpl, str):
            tmpl = str(tmpl)
        try:
            return tmpl.format(**variables)
        except Exception:
            # 任何格式化錯誤都回傳原模板以避免阻塞
            return tmpl 