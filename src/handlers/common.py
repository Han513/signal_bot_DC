import os
import re
import aiohttp
import asyncio
import logging
from io import BytesIO
from datetime import datetime, timezone
from typing import List, Tuple, Dict
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv
import requests

load_dotenv()

SOCIAL_API = os.getenv("SOCIAL_API")

# 新增: i18n 單例存取與語言正規化
try:
    from ..i18n_loader import I18n, normalize_locale
except Exception:
    try:
        from i18n_loader import I18n, normalize_locale  # type: ignore
    except Exception:
        I18n = None  # 避免在開發過程中匯入失敗導致整檔報錯
        def normalize_locale(lang):
            return "en"

_i18n_instance = None

def get_i18n():
    global _i18n_instance
    if _i18n_instance is None and I18n is not None:
        i18n_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'i18n'))
        _i18n_instance = I18n(i18n_dir)
    return _i18n_instance

def escape_markdown_v2(text):
    """跳脫 Telegram MarkdownV2 特殊字符"""
    escape_chars = r'_ * [ ] ( ) ~ ` > # + - = | { } . !'.split()
    for ch in escape_chars:
        text = text.replace(ch, '\\' + ch)
    return text

def format_float(val):
    """格式化浮點數顯示"""
    try:
        f = round(float(val), 2)
        if f == int(f):
            return str(int(f))
        elif (f * 10) == int(f * 10):
            return f"{f:.1f}"
        else:
            return f"{f:.2f}"
    except Exception:
        return str(val)

def format_timestamp_ms_to_utc(ms):
    """將毫秒級時間戳轉為 UTC+0 可讀格式"""
    try:
        ts_int = int(float(ms))
        dt = datetime.fromtimestamp(ts_int / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ms)

async def generate_trader_summary_image(trader_url, trader_name, pnl_percentage, pnl):
    """產生交易員統計圖片"""
    logging.info(f"[CopySignal] 開始產生交易員統計圖片: {trader_name}")
    
    # 基本設定
    W, H = 1200, 675
    avatar_size = 180

    # 背景圖：若存在 copy_trade.png 則使用，否則建立黑底
    bg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'pics', 'copy_trade.png'))
    logging.info(f"[CopySignal] 背景圖片路徑: {bg_path}")
    
    if os.path.exists(bg_path):
        try:
            img = Image.open(bg_path).convert('RGB')
            img = img.resize((W, H))
            logging.info(f"[CopySignal] 成功載入背景圖片")
        except Exception as e:
            logging.warning(f"[CopySignal] 載入背景圖片失敗: {e}")
            img = Image.new("RGB", (W, H), (0, 0, 0))
    else:
        logging.info(f"[CopySignal] 背景圖片不存在，使用黑色背景")
        img = Image.new("RGB", (W, H), (0, 0, 0))

    draw = ImageDraw.Draw(img)

    # 下載頭像
    logging.info(f"[CopySignal] 開始下載頭像: {trader_url}")
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession() as session:
            async with session.get(trader_url, timeout=aiohttp.ClientTimeout(total=6), headers=headers) as resp:
                if resp.status == 200:
                    avatar_data = await resp.read()
                    avatar = Image.open(BytesIO(avatar_data)).resize((avatar_size, avatar_size)).convert("RGBA")
                    logging.info(f"[CopySignal] 成功下載頭像")
                else:
                    raise Exception(f"Failed to download avatar: {resp.status}")
    except Exception as e:
        logging.warning(f"[CopySignal] 下載頭像失敗: {e}")
        avatar = Image.new("RGBA", (avatar_size, avatar_size), (120, 120, 120, 255))

    mask = Image.new("L", (avatar_size, avatar_size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, avatar_size, avatar_size), fill=255)
    avatar.putalpha(mask)
    img.paste(avatar, (100, 150), avatar)
    logging.info(f"[CopySignal] 頭像處理完成")

    # 字體
    font_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'text'))
    bold_font_path = os.path.join(font_dir, 'BRHendrix-Bold-BF6556d1b5459d3.otf')
    noto_font_path = os.path.join(font_dir, 'NotoSansSC-Bold.ttf')
    
    logging.info(f"[CopySignal] 字體目錄: {font_dir}")
    logging.info(f"[CopySignal] 粗體字體路徑: {bold_font_path}")
    logging.info(f"[CopySignal] Noto字體路徑: {noto_font_path}")
    
    def load_font(p, size):
        try:
            return ImageFont.truetype(p, size)
        except Exception as e:
            logging.warning(f"[CopySignal] 載入字體失敗 {p}: {e}")
            return ImageFont.load_default()

    # 中文名需使用支持 CJK 的字體
    def is_all_ascii(s: str):
        try:
            s.encode('ascii')
            return True
        except UnicodeEncodeError:
            return False

    title_font_path = bold_font_path if is_all_ascii(trader_name) else noto_font_path
    logging.info(f"[CopySignal] 使用字體: {title_font_path} (交易員名稱: {trader_name})")
    
    title_font = load_font(title_font_path, 70)
    number_font = load_font(bold_font_path, 100)
    label_font = load_font(noto_font_path, 45)

    # 名稱垂直置中至頭像，英文微調 +13px
    avatar_x, avatar_y = 100, 150
    title_font_size = 70
    name_y = avatar_y + (avatar_size - title_font_size) // 2
    if is_all_ascii(trader_name):
        name_y += 13  # 英文向下
    else:
        name_y -= 8   # 中文向上一點
    name_x = avatar_x + avatar_size + 30
    draw.text((name_x, name_y), trader_name, font=title_font, fill=(255, 255, 255))

    # ROI/PNL
    try:
        perc = float(pnl_percentage) * 100
    except Exception:
        perc = 0.0
    is_pos = perc >= 0
    color = (0, 191, 99) if is_pos else (237, 29, 36)
    roi_text = f"{format_float(perc)}%"
    try:
        pnl_val = float(pnl)
    except Exception:
        pnl_val = 0.0
    pnl_text = f"${format_float(abs(pnl_val))}" if is_pos else f"-${format_float(abs(pnl_val))}"

    draw.text((100, 415), roi_text, font=number_font, fill=color)
    draw.text((550, 415), pnl_text, font=number_font, fill=color)
    draw.text((100, 415 + 100 + 5), "7D ROI", font=label_font, fill=(200, 200, 200))
    draw.text((550, 415 + 100 + 5), "7D PNL", font=label_font, fill=(200, 200, 200))

    logging.info(f"[CopySignal] 圖片文字繪製完成 - ROI: {roi_text}, PNL: {pnl_text}")

    tmp_path = "/tmp/trader_summary_discord.png"
    logging.info(f"[CopySignal] 保存圖片到: {tmp_path}")
    try:
        img.save(tmp_path, quality=95)
        logging.info(f"[CopySignal] 圖片保存成功")
        return tmp_path
    except Exception as e:
        logging.error(f"[CopySignal] 圖片保存失敗: {e}")
        return None

async def get_push_targets(trader_uid: str) -> List[Tuple[int, str, str]]:
    """獲取推送目標列表，返回 (channel_id, topic_id, jump) 的列表"""
    logging.info(f"[CopySignal] 開始獲取推送目標，交易員 UID: {trader_uid}")
    try:
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        payload = {"brand": "BYD", "type": "DISCORD"}
        
        logging.info(f"[CopySignal] 調用 SOCIAL_API: {SOCIAL_API}")
        async with aiohttp.ClientSession() as session:
            async with session.post(SOCIAL_API, headers=headers, data=payload) as resp:
                logging.info(f"[CopySignal] SOCIAL_API 回應狀態: {resp.status}")
                if resp.status != 200:
                    logging.error(f"[CopySignal] SOCIAL_API 回應錯誤: {resp.status}")
                    return []
                
                social_data = await resp.json()
                logging.info(f"[CopySignal] 成功獲取社交數據，包含 {len(social_data.get('data', []))} 個群組")

        push_targets = []
        for i, social in enumerate(social_data.get("data", [])):
            logging.info(f"[CopySignal] 處理第 {i+1} 個群組: {social.get('name', 'Unknown')}")
            for j, chat in enumerate(social.get("chats", [])):
                logging.info(f"[CopySignal] 檢查聊天 {j+1}: type={chat.get('type')}, enable={chat.get('enable')}, traderUid={chat.get('traderUid')}")
                if (
                    chat.get("type") == "copy"
                    and chat.get("enable")
                    and str(chat.get("traderUid")) == trader_uid
                ):
                    cid = chat.get("chatId")
                    if cid:
                        # 處理 jump 值：如果為 null 或未設置，默認為 "0"
                        jump_value = chat.get("jump")
                        if jump_value is None or jump_value == "" or jump_value == "null":
                            jump_value = "0"
                        else:
                            jump_value = str(jump_value)
                        
                        push_targets.append((
                            int(cid),
                            str(chat.get("topicId", "")),
                            jump_value
                        ))
                        logging.info(f"[CopySignal] 找到匹配的推送目標: channel_id={cid}, topic_id={chat.get('topicId')}, jump={jump_value}")

        logging.info(f"[CopySignal] 總共找到 {len(push_targets)} 個推送目標")
        return push_targets
    except Exception as e:
        logging.error(f"[CopySignal] 獲取推送目標失敗: {type(e).__name__} - {e}")
        import traceback
        logging.error(f"[CopySignal] 詳細錯誤: {traceback.format_exc()}")
        return []

async def send_discord_message(discord_bot_url: str, data: dict) -> None:
    """發送消息到 Discord Bot"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(discord_bot_url, json=data) as resp:
                if resp.status != 200:
                    logging.error(f"Discord Bot 推送失敗: {resp.status}")
                else:
                    logging.info("Discord Bot 推送成功")
    except Exception as e:
        logging.error(f"Discord Bot 推送異常: {e}")

def create_async_response(coroutine_func, *args, **kwargs):
    """創建異步回應的包裝器"""
    async def wrapper():
        try:
            await coroutine_func(*args, **kwargs)
        except Exception as e:
            logging.error(f"異步任務執行失敗: {e}")
    
    # 啟動背景任務
    asyncio.create_task(wrapper())
    return {"status": "200", "message": "接收成功，稍後發送"} 