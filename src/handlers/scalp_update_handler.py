import os
import asyncio
import logging
from typing import Dict
from fastapi import Request
import discord
from dotenv import load_dotenv

from .common import (
    get_push_targets, format_float, format_timestamp_ms_to_utc, get_i18n, normalize_locale
)

load_dotenv()

logger = logging.getLogger(__name__)

def validate_scalp_update(data: dict) -> None:
    """驗證止盈止損更新請求資料，失敗時拋出 ValueError。"""
    required_fields = {
        "trader_uid", "trader_name", "trader_detail_url", "pair", "pair_side",
        "time"
    }

    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        raise ValueError(f"缺少欄位: {', '.join(missing)}")

    # 檢查 pair_side
    if str(data["pair_side"]) not in {"1", "2"}:
        raise ValueError("pair_side 只能是 '1'(Long) 或 '2'(Short)")

    # 檢查是否為設置或更新操作
    has_tp_price = data.get("tp_price") is not None
    has_sl_price = data.get("sl_price") is not None
    
    if not has_tp_price and not has_sl_price:
        raise ValueError("至少需要提供 tp_price 或 sl_price 其中之一")

    # 數值檢查
    try:
        if has_tp_price:
            float(data["tp_price"])
        if has_sl_price:
            float(data["sl_price"])
        # 檢查 previous 價格（可選）
        if data.get("previous_tp_price"):
            float(data["previous_tp_price"])
        if data.get("previous_sl_price"):
            float(data["previous_sl_price"])
    except (TypeError, ValueError):
        raise ValueError("價格欄位必須為數字格式")

    # time 欄位檢查
    try:
        ts_val = int(float(data["time"]))
        if ts_val < 10**12:
            raise ValueError("time 必須為毫秒級時間戳 (13 位)")
    except (TypeError, ValueError):
        raise ValueError("time 必須為毫秒級時間戳 (數字格式)")

async def process_scalp_update_discord(data: dict, bot) -> None:
    """背景協程：處理止盈止損更新推送到 Discord"""
    logger.info("[ScalpUpdate] 開始執行背景處理任務")
    try:
        trader_uid = str(data["trader_uid"])
        logger.info(f"[ScalpUpdate] 處理交易員 UID: {trader_uid}")

        # 獲取推送目標
        logger.info("[ScalpUpdate] 開始獲取推送目標")
        push_targets = await get_push_targets(trader_uid)
        logger.info(f"[ScalpUpdate] 獲取到 {len(push_targets)} 個推送目標")

        if not push_targets:
            logger.warning(f"[ScalpUpdate] 未找到符合條件的止盈止損推送頻道: {trader_uid}")
            return

        # 格式化時間
        formatted_time = format_timestamp_ms_to_utc(data.get('time'))
        logger.info(f"[ScalpUpdate] 格式化時間: {formatted_time}")

        # 準備發送任務
        tasks = []
        logger.info(f"[ScalpUpdate] 準備發送到 {len(push_targets)} 個頻道")
        
        for i, (channel_id, topic_id, jump, channel_lang) in enumerate(push_targets):
            logger.info(f"[ScalpUpdate] 處理第 {i+1} 個頻道: {channel_id}, topic: {topic_id}, jump: {jump}, lang: {channel_lang}")
            
            # 根據 jump 值決定是否包含連結
            include_link = (jump == "1")
            text = format_scalp_update_text(data, formatted_time, include_link, channel_lang)
            logger.info(f"[ScalpUpdate] 為頻道 {channel_id} 準備消息內容")
            
            tasks.append(
                send_discord_message(
                    bot=bot,
                    channel_id=channel_id,
                    text=text
                )
            )

        # 等待 Discord 發送結果
        logger.info(f"[ScalpUpdate] 開始並發發送 {len(tasks)} 個消息")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 檢查發送結果
        success_count = 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"[ScalpUpdate] 頻道 {push_targets[i][0]} 發送失敗: {result}")
            else:
                success_count += 1
                logger.info(f"[ScalpUpdate] 頻道 {push_targets[i][0]} 發送成功")
        
        logger.info(f"[ScalpUpdate] 發送完成: {success_count}/{len(tasks)} 成功")

    except Exception as e:
        logger.error(f"[ScalpUpdate] 推送止盈止損更新到 Discord 失敗: {type(e).__name__} - {e}")
        import traceback
        logger.error(f"[ScalpUpdate] 詳細錯誤: {traceback.format_exc()}")

async def send_discord_message(bot, channel_id: int, text: str) -> None:
    """發送 Discord 消息"""
    logger.info(f"[ScalpUpdate] 開始發送消息到頻道 {channel_id}")
    try:
        channel = bot.get_channel(channel_id)
        if not channel:
            logger.warning(f"[ScalpUpdate] 找不到頻道 {channel_id}")
            return

        logger.info(f"[ScalpUpdate] 找到頻道: {channel.name} (ID: {channel_id})")

        # 檢查權限
        permissions = channel.permissions_for(channel.guild.me)
        logger.info(f"[ScalpUpdate] 頻道權限檢查 - 發送消息: {permissions.send_messages}")
        
        if not permissions.send_messages:
            logger.warning(f"[ScalpUpdate] 在頻道 {channel_id} 中沒有發送消息的權限")
            return

        logger.info(f"[ScalpUpdate] 發送消息到頻道 {channel_id}")
        await channel.send(content=text, allowed_mentions=discord.AllowedMentions.none())

        logger.info(f"[ScalpUpdate] 成功發送到 Discord 頻道 {channel_id}")

    except discord.Forbidden as e:
        logger.error(f"[ScalpUpdate] 權限錯誤 - 發送到 Discord 頻道 {channel_id} 失敗: {e}")
    except discord.HTTPException as e:
        logger.error(f"[ScalpUpdate] HTTP 錯誤 - 發送到 Discord 頻道 {channel_id} 失敗: {e}")
    except Exception as e:
        logger.error(f"[ScalpUpdate] 未知錯誤 - 發送到 Discord 頻道 {channel_id} 失敗: {type(e).__name__} - {e}")
        import traceback
        logger.error(f"[ScalpUpdate] 詳細錯誤: {traceback.format_exc()}")

def format_scalp_update_text(data: dict, formatted_time: str, include_link: bool = True, lang: str = None) -> str:
    """格式化止盈止損更新文本（i18n）"""
    i18n = get_i18n()
    locale = normalize_locale(lang)

    pair_side = i18n.t(f"common.sides.{str(data.get('pair_side',''))}", locale)

    has_previous_tp = bool(data.get("previous_tp_price"))
    has_previous_sl = bool(data.get("previous_sl_price"))
    is_update = has_previous_tp or has_previous_sl

    tp_price = str(data.get("tp_price", "")) if data.get("tp_price") else ""
    sl_price = str(data.get("sl_price", "")) if data.get("sl_price") else ""
    previous_tp_price = str(data.get("previous_tp_price", "")) if data.get("previous_tp_price") else ""
    previous_sl_price = str(data.get("previous_sl_price", "")) if data.get("previous_sl_price") else ""

    if is_update:
        text = (
            i18n.render("scalp.title_update", locale, {"trader_name": data.get('trader_name', 'Trader')}) + "\n\n" +
            i18n.render("scalp.line_pair", locale, {"pair": data.get('pair',''), "pair_side": pair_side}) + "\n" +
            i18n.render("scalp.line_time", locale, {"time": formatted_time})
        )
        update_lines = []
        if tp_price and previous_tp_price:
            update_lines.append(i18n.render("scalp.tp_update", locale, {"old": previous_tp_price, "new": tp_price}))
        elif tp_price:
            update_lines.append(i18n.render("scalp.tp", locale, {"price": tp_price}))
        if sl_price and previous_sl_price:
            update_lines.append(i18n.render("scalp.sl_update", locale, {"old": previous_sl_price, "new": sl_price}))
        elif sl_price:
            update_lines.append(i18n.render("scalp.sl", locale, {"price": sl_price}))
        if update_lines:
            text += "\n" + "\n".join(update_lines)
    else:
        text = (
            i18n.render("scalp.title_setting", locale, {"trader_name": data.get('trader_name', 'Trader')}) + "\n\n" +
            i18n.render("scalp.line_pair", locale, {"pair": data.get('pair',''), "pair_side": pair_side}) + "\n" +
            i18n.render("scalp.line_time", locale, {"time": formatted_time})
        )
        setting_lines = []
        if tp_price:
            setting_lines.append(i18n.render("scalp.tp", locale, {"price": tp_price}))
        if sl_price:
            setting_lines.append(i18n.render("scalp.sl", locale, {"price": sl_price}))
        if setting_lines:
            text += "\n" + "\n".join(setting_lines)

    if include_link:
        trader_name = data.get('trader_name', 'Trader')
        detail_url = data.get('trader_detail_url', '')
        text += "\n\n" + i18n.render("common.detail_line", locale, {"trader_name": trader_name, "url": detail_url})

    return text

async def handle_send_scalp_update(request: Request, bot) -> Dict:
    """
    處理 /api/discord/scalp_update 介面：
    1. 先同步驗證輸入資料，失敗直接回傳 400。
    2. 成功則立即回 200，並將實際推送工作交由背景協程處理。
    """
    logger.info("[ScalpUpdate] 開始處理 scalp update 請求")
    
    # Content-Type 檢查
    content_type = request.headers.get("content-type", "").split(";")[0].lower()
    logger.info(f"[ScalpUpdate] Content-Type: {content_type}")
    if content_type != "application/json":
        logger.error(f"[ScalpUpdate] Content-Type 錯誤: {content_type}")
        return {"status": "400", "message": "Content-Type must be application/json"}

    # 解析 JSON
    try:
        data = await request.json()
        logger.info(f"[ScalpUpdate] 成功解析 JSON 數據: {list(data.keys())}")
    except Exception as e:
        logger.error(f"[ScalpUpdate] JSON 解析失敗: {e}")
        return {"status": "400", "message": "Invalid JSON body"}

    # 資料驗證
    try:
        validate_scalp_update(data)
        logger.info("[ScalpUpdate] 數據驗證通過")
    except ValueError as err:
        logger.error(f"[ScalpUpdate] 數據驗證失敗: {err}")
        return {"status": "400", "message": str(err)}

    # 背景處理：在 Discord 事件迴圈執行
    logger.info("[ScalpUpdate] 開始背景處理，調度到 Discord 事件迴圈")
    try:
        asyncio.run_coroutine_threadsafe(process_scalp_update_discord(data, bot), bot.loop)
        logger.info("[ScalpUpdate] 成功調度背景任務")
    except Exception as e:
        logger.error(f"[ScalpUpdate] 調度背景任務失敗: {e}")
        return {"status": "500", "message": "Internal server error"}
    
    return {"status": "200", "message": "接收成功，稍後發送"} 