import os
import aiohttp
import asyncio
import logging
from typing import Dict
from fastapi import Request
import discord
from dotenv import load_dotenv

from .common import (
    get_push_targets, generate_trader_summary_image, format_timestamp_ms_to_utc,
    create_async_response, get_i18n, normalize_locale
)

load_dotenv()

logger = logging.getLogger(__name__)

def validate_copy_signal(data: dict) -> None:
    """驗證 copy signal 請求資料，失敗時拋出 ValueError。"""
    required_fields = {
        "trader_uid", "trader_name", "trader_pnl", "trader_pnlpercentage",
        "trader_detail_url", "pair", "base_coin", "quote_coin",
        "pair_leverage", "pair_type", "price", "time", "trader_url",
        "pair_side", "pair_margin_type"
    }

    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        raise ValueError(f"缺少欄位: {', '.join(missing)}")

    # 數值與類型檢查
    try:
        pnl = float(data["trader_pnl"])
        pnl_perc = float(data["trader_pnlpercentage"])
        float(data["pair_leverage"])
    except (TypeError, ValueError):
        raise ValueError("trader_pnlpercentage / pair_leverage / trader_pnl 必須為數字格式")

    # 正負號須一致
    if (pnl >= 0) ^ (pnl_perc >= 0):
        raise ValueError("trader_pnl 與 trader_pnlpercentage 正負號不一致")

    if data["pair_type"] not in {"buy", "sell"}:
        raise ValueError("pair_type 只能是 'buy' 或 'sell'")

    # pair_side 必須為 1 或 2（字串或數字）
    if str(data["pair_side"]) not in {"1", "2"}:
        raise ValueError("pair_side 只能是 '1'(Long) 或 '2'(Short)")

    # pair_margin_type 必須為 1 或 2（字串或數字）
    if str(data["pair_margin_type"]) not in {"1", "2"}:
        raise ValueError("pair_margin_type 只能是 '1'(Cross) 或 '2'(Isolated)")

    # time 欄位必須為毫秒級時間戳（13 位數/大於等於 1e12）
    try:
        ts_val = int(float(data["time"]))
    except (TypeError, ValueError):
        raise ValueError("time 必須為毫秒級時間戳 (數字格式)")

    # 檢查是否可能為秒級時間戳（10 位數），若是則判定錯誤
    if ts_val < 10**12:
        raise ValueError("time 必須為毫秒級時間戳 (13 位)")

async def process_copy_signal_discord(data: dict, bot) -> None:
    """背景協程：查詢推送目標、產圖並發送訊息到 Discord。"""
    logger.info("[CopySignal] 開始執行背景處理任務")
    try:
        trader_uid = str(data["trader_uid"])
        logger.info(f"[CopySignal] 處理交易員 UID: {trader_uid}")

        # 獲取推送目標
        logger.info("[CopySignal] 開始獲取推送目標")
        push_targets = await get_push_targets(trader_uid)
        logger.info(f"[CopySignal] 獲取到 {len(push_targets)} 個推送目標")

        if not push_targets:
            logger.warning(f"[CopySignal] 未找到符合條件的 Discord 頻道: {trader_uid}")
            return

        # 產生交易員統計圖片
        # logger.info("[CopySignal] 開始產生交易員統計圖片")
        # img_path = await generate_trader_summary_image(
        #     data["trader_url"],
        #     data["trader_name"],
        #     data["trader_pnlpercentage"],
        #     data["trader_pnl"],
        # )
        # if not img_path:
        #     logger.warning("[CopySignal] 圖片生成失敗，取消推送")
        #     return
        # logger.info(f"[CopySignal] 圖片生成成功: {img_path}")

        # 將毫秒級時間戳轉為 UTC+0 可讀格式
        formatted_time = format_timestamp_ms_to_utc(data.get('time'))
        logger.info(f"[CopySignal] 格式化時間: {formatted_time}")

        # 準備發送任務
        tasks = []
        logger.info(f"[CopySignal] 準備發送到 {len(push_targets)} 個頻道")
        
        # 載入 i18n 與語言
        i18n = get_i18n()
        req_locale = normalize_locale(data.get('lang'))
        
        for i, (channel_id, topic_id, jump) in enumerate(push_targets):
            logger.info(f"[CopySignal] 處理第 {i+1} 個頻道: {channel_id}, topic: {topic_id}, jump: {jump}")

            # 映射方向/倉位/保證金類型
            pair_type_key = (data.get("pair_type") or "").lower()
            pair_type_text = i18n.t(f"copy_signal.pair_types.{pair_type_key}", req_locale)
            pair_side_text = i18n.t(f"common.sides.{str(data.get('pair_side', ''))}", req_locale)
            margin_type_text = i18n.t(f"common.margin_types.{str(data.get('pair_margin_type',''))}", req_locale)

            # 決定標題
            title_key = "copy_signal.title_open" if pair_type_key == "buy" else "copy_signal.title_close"
            title = i18n.render(title_key, req_locale, {"trader_name": data["trader_name"]})

            # 根據 jump 決定是否顯示連結
            detail_line = i18n.render(
                "common.detail_line", req_locale,
                {"trader_name": data['trader_name'], "url": data['trader_detail_url']}
            ) if jump == "1" else ""

            body = i18n.render(
                "copy_signal.body", req_locale,
                {
                    "pair": data["pair"],
                    "margin_type": margin_type_text,
                    "leverage": data["pair_leverage"],
                    "time_label": i18n.t("common.labels.time", req_locale),
                    "time": formatted_time,
                    "direction_label": i18n.t("common.labels.direction", req_locale),
                    "pair_type": pair_type_text,
                    "pair_side": pair_side_text,
                    "entry_price_label": i18n.t("common.labels.entry_price", req_locale),
                    "price": data["price"],
                    "detail_line": detail_line
                }
            )

            caption = f"{title}\n\n{body}"
            
            logger.info(f"[CopySignal] 為頻道 {channel_id} 準備消息內容")
            tasks.append(
                send_discord_message_with_image(
                    bot=bot,
                    channel_id=channel_id,
                    text=caption,
                    image_path=None
                )
            )

        # 等待 Discord 發送結果
        logger.info(f"[CopySignal] 開始並發發送 {len(tasks)} 個消息")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 檢查發送結果
        success_count = 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"[CopySignal] 頻道 {push_targets[i][0]} 發送失敗: {result}")
            else:
                success_count += 1
                logger.info(f"[CopySignal] 頻道 {push_targets[i][0]} 發送成功")
        
        logger.info(f"[CopySignal] 發送完成: {success_count}/{len(tasks)} 成功")

    except Exception as e:
        logger.error(f"[CopySignal] 推送 copy signal 到 Discord 失敗: {type(e).__name__} - {e}")
        import traceback
        logger.error(f"[CopySignal] 詳細錯誤: {traceback.format_exc()}")

async def send_discord_message_with_image(bot, channel_id: int, text: str, image_path: str) -> None:
    """發送帶圖片的 Discord 消息"""
    logger.info(f"[CopySignal] 開始發送消息到頻道 {channel_id}")
    try:
        channel = bot.get_channel(channel_id)
        if not channel:
            logger.warning(f"[CopySignal] 找不到頻道 {channel_id}")
            return

        logger.info(f"[CopySignal] 找到頻道: {channel.name} (ID: {channel_id})")

        # 檢查權限
        permissions = channel.permissions_for(channel.guild.me)
        logger.info(f"[CopySignal] 頻道權限檢查 - 發送消息: {permissions.send_messages}, 附加文件: {permissions.attach_files}")
        
        if not permissions.send_messages:
            logger.warning(f"[CopySignal] 在頻道 {channel_id} 中沒有發送消息的權限")
            return

        # 檢查圖片文件是否存在
        if image_path:
            import os
            if os.path.exists(image_path):
                logger.info(f"[CopySignal] 圖片文件存在: {image_path}")
            else:
                logger.warning(f"[CopySignal] 圖片文件不存在: {image_path}")
                image_path = None

        if image_path and permissions.attach_files:
            logger.info(f"[CopySignal] 發送帶圖片的消息到頻道 {channel_id}")
            discord_file = discord.File(image_path, filename="trader.png")
            await channel.send(content=text, file=discord_file, allowed_mentions=discord.AllowedMentions.none())
        else:
            logger.info(f"[CopySignal] 發送純文字消息到頻道 {channel_id}")
            await channel.send(content=text, allowed_mentions=discord.AllowedMentions.none())

        logger.info(f"[CopySignal] 成功發送到 Discord 頻道 {channel_id}")

    except discord.Forbidden as e:
        logger.error(f"[CopySignal] 權限錯誤 - 發送到 Discord 頻道 {channel_id} 失敗: {e}")
    except discord.HTTPException as e:
        logger.error(f"[CopySignal] HTTP 錯誤 - 發送到 Discord 頻道 {channel_id} 失敗: {e}")
    except Exception as e:
        logger.error(f"[CopySignal] 未知錯誤 - 發送到 Discord 頻道 {channel_id} 失敗: {type(e).__name__} - {e}")
        import traceback
        logger.error(f"[CopySignal] 詳細錯誤: {traceback.format_exc()}")

async def handle_send_copy_signal(request: Request, bot) -> Dict:
    """
    處理 /api/discord/copy_signal 介面：
    1. 先同步驗證輸入資料，失敗直接回傳 400。
    2. 成功則立即回 200，並將實際推送工作交由背景協程處理。
    """
    logger.info("[CopySignal] 開始處理 copy signal 請求")
    
    # Content-Type 檢查
    content_type = request.headers.get("content-type", "").split(";")[0].lower()
    logger.info(f"[CopySignal] Content-Type: {content_type}")
    if content_type != "application/json":
        logger.error(f"[CopySignal] Content-Type 錯誤: {content_type}")
        return {"status": "400", "message": "Content-Type must be application/json"}

    # 解析 JSON
    try:
        data = await request.json()
        logger.info(f"[CopySignal] 成功解析 JSON 數據: {list(data.keys())}")
    except Exception as e:
        logger.error(f"[CopySignal] JSON 解析失敗: {e}")
        return {"status": "400", "message": "Invalid JSON body"}

    # 資料驗證
    try:
        validate_copy_signal(data)
        logger.info("[CopySignal] 數據驗證通過")
    except ValueError as err:
        logger.error(f"[CopySignal] 數據驗證失敗: {err}")
        return {"status": "400", "message": str(err)}

    # 背景處理：在 Discord 事件迴圈執行
    logger.info("[CopySignal] 開始背景處理，調度到 Discord 事件迴圈")
    try:
        asyncio.run_coroutine_threadsafe(process_copy_signal_discord(data, bot), bot.loop)
        logger.info("[CopySignal] 成功調度背景任務")
    except Exception as e:
        logger.error(f"[CopySignal] 調度背景任務失敗: {e}")
        return {"status": "500", "message": "Internal server error"}
    
    return {"status": "200", "message": "接收成功，稍後發送"} 