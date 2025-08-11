import re

AI_TRANSLATE_HINT = {
    "zh_CN": "\n\n--- 由 AI 自動翻譯，僅供參考 ---",
    "zh_TW": "\n\n--- 由 AI 自動翻譯，僅供參考 ---",
    "en_US": "\n\n--- Automatically translated by AI. For reference only. ---",
    "ru_RU": "\n\n--- Переведено ИИ, только для справки ---",
    "in_ID": "\n\n--- Diterjemahkan AI, hanya sebagai referensi ---",
    "ja_JP": "\n\n--- AI翻訳、参考用です ---",
    "pt_PT": "\n\n--- Traduzido por IA, apenas para referência ---",
    "fr_FR": "\n\n--- Traduction IA, à titre indicatif ---",
    "es_ES": "\n\n--- Traducción por IA, solo para referencia ---",
    "tr_TR": "\n\n--- Yapay zeka çevirisi, sadece bilgi amaçlı ---",
    "de_DE": "\n\n--- KI-Übersetzung, nur zur Orientierung ---",
    "it_IT": "\n\n--- Tradotto da AI, solo a scopo informativo ---",
    "vi_VN": "\n\n--- Dịch bởi AI, chỉ mang tính tham khảo ---",
    "tl_PH": "\n\n--- Isinalin ng AI, para sa sanggunian lamang ---",
    "ar_AE": "\n\n--- مترجم بواسطة الذكاء الاصطناعي، للاستشارة فقط ---",
    "fa_IR": "\n\n--- ترجمه شده توسط هوش مصنوعی، فقط برای مرجع ---",
    "km_KH": "\n\n--- បកប្រែដោយ AI សម្រាប់គោលបំណងយោបល់ប៉ុណ្ណោះ ---",
    "ko_KR": "\n\n--- AI 자동 번역 내용이며, 참고용입니다. ---",
    "ms_MY": "\n\n--- Diterjemahkan oleh AI, untuk rujukan sahaja ---",
    "th_TH": "\n\n--- แปลโดย AI เฉพาะเพื่อการอ้างอิง ---",
}

# 語言代碼映射表，將社群語言代碼映射到接口語言代碼
LANGUAGE_CODE_MAPPING = {
    "zh": "zh_CN",
    "en": "en_US", 
    "ru": "ru_RU",
    "id": "in_ID",
    "ja": "ja_JP",
    "pt": "pt_PT",
    "fr": "fr_FR",
    "es": "es_ES",
    "tr": "tr_TR",
    "de": "de_DE",
    "it": "it_IT",
    "vi": "vi_VN",
    "tl": "tl_PH",
    "ar": "ar_AE",
    "fa": "fa_IR",
    "km": "km_KH",
    "ko": "ko_KR",
    "ms": "ms_MY",
    "th": "th_TH",
}

def escape_markdown_v2(text):
    # 確保 text 不為 None
    if text is None:
        text = ""
    
    # 修正 escape_chars 的定義，確保正確分割字符
    escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for ch in escape_chars:
        text = text.replace(ch, '\\' + ch)
    return text

def html_to_discord_markdown(text):
    """將HTML標籤轉換為Discord Markdown格式"""
    if not text:
        return text
    # 處理粗體
    text = re.sub(r'<b>(.*?)</b>', r'**\1**', text, flags=re.IGNORECASE)
    text = re.sub(r'<strong>(.*?)</strong>', r'**\1**', text, flags=re.IGNORECASE)
    # 處理斜體
    text = re.sub(r'<i>(.*?)</i>', r'*\1*', text, flags=re.IGNORECASE)
    text = re.sub(r'<em>(.*?)</em>', r'*\1*', text, flags=re.IGNORECASE)
    # 處理底線
    text = re.sub(r'<u>(.*?)</u>', r'__\1__', text, flags=re.IGNORECASE)
    # 處理連結 - 將 <a href="URL">文字</a> 轉換為 [文字](URL)
    text = re.sub(r'<a\s+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r'[\2](\1)', text, flags=re.IGNORECASE)
    return text

def get_multilingual_content(post, lang):
    """
    根據語言代碼取得對應的翻譯內容並加上 AI 提示（只給 Discord 用，不做跳脫）
    """
    translations = post.get("translations")
    if not translations:
        content = post.get("content", "")
        # 處理 \n 換行
        content = content.replace("\\n", "\n")
        # 處理HTML標籤
        content = html_to_discord_markdown(content)
        if lang in ['en', 'en_US']:
            return content
        else:
            # 使用映射後的語言代碼
            api_lang_code = LANGUAGE_CODE_MAPPING.get(lang, "en_US")
            hint = AI_TRANSLATE_HINT.get(api_lang_code, AI_TRANSLATE_HINT["en_US"])
            return content + hint

    if "_" in lang:
        api_lang_code = lang
    else:
        api_lang_code = LANGUAGE_CODE_MAPPING.get(lang, "en_US")

    content = translations.get(api_lang_code)
    if not content:
        content = translations.get("en_US") or post.get("content", "")
    if content is None:
        content = ""
    # 處理 \n 換行
    content = content.replace("\\n", "\n")
    # 處理HTML標籤
    content = html_to_discord_markdown(content)

    if lang in ['en', 'en_US'] or api_lang_code == 'en_US':
        return content
    else:
        hint = AI_TRANSLATE_HINT.get(api_lang_code, AI_TRANSLATE_HINT["en_US"])
        return content + hint 