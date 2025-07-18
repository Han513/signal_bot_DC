import os
import asyncio
import logging
from typing import Dict
from fastapi import Request
import discord
from dotenv import load_dotenv

from .common import (
    get_push_targets, format_float
)

load_dotenv()

logger = logging.getLogger(__name__)

def validate_holding_report(data: dict) -> None:
    """驗證持倉報告請求資料，失敗時拋出 ValueError。"""
    required_fields = {
        "trader_uid", "trader_name", "trader_detail_url", "pair", "pair_side",
        "pair_margin_type", "pair_leverage", "entry_price", "current_price",
        "unrealized_pnl_percentage"
    }

    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        raise ValueError(f"缺少欄位: {', '.join(missing)}")

    # 檢查 pair_side
    if str(data["pair_side"]) not in {"1", "2"}:
        raise ValueError("pair_side 只能是 '1'(Long) 或 '2'(Short)")

    # 檢查 pair_margin_type
    if str(data["pair_margin_type"]) not in {"1", "2"}:
        raise ValueError("pair_margin_type 只能是 '1'(Cross) 或 '2'(Isolated)")

    # 數值檢查
    try:
        float(data["entry_price"])
        float(data["current_price"])
        float(data["unrealized_pnl_percentage"])
        float(data["pair_leverage"])
        # 檢查可選的止盈止損價格
        if data.get("tp_price"):
            float(data["tp_price"])
        if data.get("sl_price"):
            float(data["sl_price"])
    except (TypeError, ValueError):
        raise ValueError("數值欄位必須為數字格式")

async def process_holding_report_discord(data: dict, bot) -> None:
    """背景協程：處理持倉報告推送到 Discord"""
    logger.info("[HoldingReport] 開始執行背景處理任務")
    try:
        trader_uid = str(data["trader_uid"])
        logger.info(f"[HoldingReport] 處理交易員 UID: {trader_uid}")

        # 獲取推送目標
        logger.info("[HoldingReport] 開始獲取推送目標")
        push_targets = await get_push_targets(trader_uid)
        logger.info(f"[HoldingReport] 獲取到 {len(push_targets)} 個推送目標")

        if not push_targets:
            logger.warning(f"[HoldingReport] 未找到符合條件的持倉報告推送頻道: {trader_uid}")
            return

        # 準備發送任務
        tasks = []
        logger.info(f"[HoldingReport] 準備發送到 {len(push_targets)} 個頻道")
        
        for i, (channel_id, topic_id, jump) in enumerate(push_targets):
            logger.info(f"[HoldingReport] 處理第 {i+1} 個頻道: {channel_id}, topic: {topic_id}, jump: {jump}")
            
            text = format_holding_report_text(data, jump == "1")
            logger.info(f"[HoldingReport] 為頻道 {channel_id} 準備消息內容")
            
            tasks.append(
                send_discord_message(
                    bot=bot,
                    channel_id=channel_id,
                    text=text
                )
            )

        # 等待 Discord 發送結果
        logger.info(f"[HoldingReport] 開始並發發送 {len(tasks)} 個消息")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 檢查發送結果
        success_count = 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"[HoldingReport] 頻道 {push_targets[i][0]} 發送失敗: {result}")
            else:
                success_count += 1
                logger.info(f"[HoldingReport] 頻道 {push_targets[i][0]} 發送成功")
        
        logger.info(f"[HoldingReport] 發送完成: {success_count}/{len(tasks)} 成功")

    except Exception as e:
        logger.error(f"[HoldingReport] 推送持倉報告到 Discord 失敗: {type(e).__name__} - {e}")
        import traceback
        logger.error(f"[HoldingReport] 詳細錯誤: {traceback.format_exc()}")

async def send_discord_message(bot, channel_id: int, text: str) -> None:
    """發送 Discord 消息"""
    logger.info(f"[HoldingReport] 開始發送消息到頻道 {channel_id}")
    try:
        channel = bot.get_channel(channel_id)
        if not channel:
            logger.warning(f"[HoldingReport] 找不到頻道 {channel_id}")
            return

        logger.info(f"[HoldingReport] 找到頻道: {channel.name} (ID: {channel_id})")

        # 檢查權限
        permissions = channel.permissions_for(channel.guild.me)
        logger.info(f"[HoldingReport] 頻道權限檢查 - 發送消息: {permissions.send_messages}")
        
        if not permissions.send_messages:
            logger.warning(f"[HoldingReport] 在頻道 {channel_id} 中沒有發送消息的權限")
            return

        logger.info(f"[HoldingReport] 發送消息到頻道 {channel_id}")
        await channel.send(content=text, allowed_mentions=discord.AllowedMentions.none())

        logger.info(f"[HoldingReport] 成功發送到 Discord 頻道 {channel_id}")

    except discord.Forbidden as e:
        logger.error(f"[HoldingReport] 權限錯誤 - 發送到 Discord 頻道 {channel_id} 失敗: {e}")
    except discord.HTTPException as e:
        logger.error(f"[HoldingReport] HTTP 錯誤 - 發送到 Discord 頻道 {channel_id} 失敗: {e}")
    except Exception as e:
        logger.error(f"[HoldingReport] 未知錯誤 - 發送到 Discord 頻道 {channel_id} 失敗: {type(e).__name__} - {e}")
        import traceback
        logger.error(f"[HoldingReport] 詳細錯誤: {traceback.format_exc()}")

def format_holding_report_text(data: dict, include_link: bool = True) -> str:
    """格式化持倉報告文本"""
    # 文案映射
    pair_side_map = {"1": "Long", "2": "Short", 1: "Long", 2: "Short"}
    margin_type_map = {"1": "Cross", "2": "Isolated", 1: "Cross", 2: "Isolated"}
    
    pair_side = pair_side_map.get(str(data.get("pair_side", "")), str(data.get("pair_side", "")))
    margin_type = margin_type_map.get(str(data.get("pair_margin_type", "")), str(data.get("pair_margin_type", "")))
    
    # 格式化數值
    entry_price = format_float(data.get("entry_price", 0))
    current_price = format_float(data.get("current_price", 0))
    roi = format_float(data.get("unrealized_pnl_percentage", 0))
    leverage = format_float(data.get("pair_leverage", 0))
    
    # 判斷是否有設置止盈止損
    has_tp = bool(data.get("tp_price"))
    has_sl = bool(data.get("sl_price"))
    
    text = (
        f"📊 **Holding Report**\n\n"
        f"⚡️**{data.get('trader_name', 'Trader')}** Trading Summary (Updated every 2 hours)\n\n"
        f"**{data.get('pair', '')}** {margin_type} **{leverage}X**\n"
        f"Direction: {pair_side}\n"
        f"Entry Price: ${entry_price}\n"
        f"Current Price: ${current_price}\n"
        f"ROI: {roi}%"
    )
    
    # 如果有設置止盈止損，添加相關信息
    tp_sl_lines = []
    if has_tp:
        tp_price = format_float(data.get("tp_price", 0))
        tp_sl_lines.append(f"✅TP Price: ${tp_price}")
    if has_sl:
        sl_price = format_float(data.get("sl_price", 0))
        tp_sl_lines.append(f"🛑SL Price: ${sl_price}")
    
    if tp_sl_lines:
        text += "\n" + "\n".join(tp_sl_lines)
    
    if include_link:
        # 使用 Discord Markdown 格式創建可點擊的超連結
        trader_name = data.get('trader_name', 'Trader')
        detail_url = data.get('trader_detail_url', '')
        text += f"\n\n[About {trader_name}, more actions>>]({detail_url})"
    
    return text

async def handle_holding_report(request: Request, bot) -> Dict:
    """
    處理 /api/report/holdings 介面：
    1. 先同步驗證輸入資料，失敗直接回傳 400。
    2. 成功則立即回 200，並將實際推送工作交由背景協程處理。
    """
    logger.info("[HoldingReport] 開始處理 holding report 請求")
    
    # Content-Type 檢查
    content_type = request.headers.get("content-type", "").split(";")[0].lower()
    logger.info(f"[HoldingReport] Content-Type: {content_type}")
    if content_type != "application/json":
        logger.error(f"[HoldingReport] Content-Type 錯誤: {content_type}")
        return {"status": "400", "message": "Content-Type must be application/json"}

    # 解析 JSON
    try:
        data = await request.json()
        logger.info(f"[HoldingReport] 成功解析 JSON 數據: {list(data.keys())}")
    except Exception as e:
        logger.error(f"[HoldingReport] JSON 解析失敗: {e}")
        return {"status": "400", "message": "Invalid JSON body"}

    # 資料驗證
    try:
        validate_holding_report(data)
        logger.info("[HoldingReport] 數據驗證通過")
    except ValueError as err:
        logger.error(f"[HoldingReport] 數據驗證失敗: {err}")
        return {"status": "400", "message": str(err)}

    # 背景處理：在 Discord 事件迴圈執行
    logger.info("[HoldingReport] 開始背景處理，調度到 Discord 事件迴圈")
    try:
        asyncio.run_coroutine_threadsafe(process_holding_report_discord(data, bot), bot.loop)
        logger.info("[HoldingReport] 成功調度背景任務")
    except Exception as e:
        logger.error(f"[HoldingReport] 調度背景任務失敗: {e}")
        return {"status": "500", "message": "Internal server error"}
    
    return {"status": "200", "message": "接收成功，稍後發送"} 