import os
import asyncio
import logging
from typing import Dict
from fastapi import Request
import discord
from dotenv import load_dotenv

from .common import (
    get_push_targets, format_float, get_i18n, normalize_locale
)

load_dotenv()

logger = logging.getLogger(__name__)

def validate_holding_report(data) -> None:
    """支持批量trader+infos结构的校验"""
    if isinstance(data, list):
        if not data:
            raise ValueError("列表不能為空")
        for i, trader in enumerate(data):
            if not isinstance(trader, dict):
                raise ValueError(f"列表項目 {i} 必須為字典格式，收到: {type(trader)}")
            # 校验trader主字段
            required_fields = {"trader_uid", "trader_name", "trader_detail_url"}
            missing = [f for f in required_fields if not trader.get(f)]
            if missing:
                raise ValueError(f"trader {i} 缺少欄位: {', '.join(missing)}")
            # 校验infos
            infos = trader.get("infos")
            if not infos or not isinstance(infos, list):
                raise ValueError(f"trader {i} 缺少infos或格式錯誤")
            for j, info in enumerate(infos):
                validate_single_holding_report(info, f"trader {i} - info {j}")
    elif isinstance(data, dict):
        # 单个trader
        required_fields = {"trader_uid", "trader_name", "trader_detail_url"}
        missing = [f for f in required_fields if not data.get(f)]
        if missing:
            raise ValueError(f"trader 缺少欄位: {', '.join(missing)}")
        infos = data.get("infos")
        if not infos or not isinstance(infos, list):
            raise ValueError(f"trader 缺少infos或格式錯誤")
        for j, info in enumerate(infos):
            validate_single_holding_report(info, f"info {j}")
    else:
        raise ValueError("請求資料必須為字典或列表格式")

def validate_single_holding_report(data: dict, prefix: str = "") -> None:
    """驗證單個持倉報告項目（只校验币种相关字段）"""
    required_fields = {
        "pair", "pair_side", "pair_margin_type", "pair_leverage",
        "entry_price", "current_price", "unrealized_pnl_percentage"
    }
    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        error_msg = f"缺少欄位: {', '.join(missing)}"
        if prefix:
            error_msg = f"{prefix} - {error_msg}"
        raise ValueError(error_msg)

    # 檢查 pair_side
    if str(data["pair_side"]) not in {"1", "2"}:
        error_msg = "pair_side 只能是 '1'(Long) 或 '2'(Short)"
        if prefix:
            error_msg = f"{prefix} - {error_msg}"
        raise ValueError(error_msg)

    # 檢查 pair_margin_type
    if str(data["pair_margin_type"]) not in {"1", "2"}:
        error_msg = "pair_margin_type 只能是 '1'(Cross) 或 '2'(Isolated)"
        if prefix:
            error_msg = f"{prefix} - {error_msg}"
        raise ValueError(error_msg)

    # 數值檢查
    try:
        float(data["entry_price"])
        float(data["current_price"])
        float(data["unrealized_pnl_percentage"])
        float(data["pair_leverage"])
        # 檢查可選的止盈止損價格
        if data.get("tp_price") not in (None, "", "None"):
            float(data["tp_price"])
        if data.get("sl_price") not in (None, "", "None"):
            float(data["sl_price"])
    except (TypeError, ValueError):
        error_msg = "數值欄位必須為數字格式"
        if prefix:
            error_msg = f"{prefix} - {error_msg}"
        raise ValueError(error_msg)

async def process_holding_report_discord(data: dict, bot) -> None:
    """背景協程：處理持倉報告推送到 Discord，支援多trader，每個trader合併所有infos發一條訊息"""
    logger.info("[HoldingReport] 開始執行背景處理任務")
    try:
        # 支援多個 trader
        traders = data if isinstance(data, list) else [data]
        for trader in traders:
            trader_uid = str(trader["trader_uid"])
            logger.info(f"[HoldingReport] 處理交易員 UID: {trader_uid}")

            # 獲取推送目標
            push_targets = await get_push_targets(trader_uid)
            logger.info(f"[HoldingReport] 獲取到 {len(push_targets)} 個推送目標")

            if not push_targets:
                logger.warning(f"[HoldingReport] 未找到符合條件的持倉報告推送頻道: {trader_uid}")
                continue

            infos = trader.get("infos")
            logger.info(f"[HoldingReport] trader_name={trader.get('trader_name')} infos={infos}")
            await send_holding_to_all_targets(infos, trader, push_targets, bot)

    except Exception as e:
        logger.error(f"[HoldingReport] 推送持倉報告到 Discord 失敗: {type(e).__name__} - {e}")
        import traceback
        logger.error(f"[HoldingReport] 詳細錯誤: {traceback.format_exc()}")

async def send_holding_to_all_targets(infos, trader, push_targets, bot):
    tasks = []
    for channel_id, topic_id, jump in push_targets:
        # 根據 jump 值決定是否包含連結
        include_link = (jump == "1")
        
        if infos and isinstance(infos, list):
            logger.info(f"[HoldingReport] infos 長度: {len(infos)}")
            # 合併所有 infos，發一條訊息
            text = format_holding_report_list_text(infos, trader, include_link)
        else:
            logger.info(f"[HoldingReport] 無 infos 或不是 list，使用單一持倉格式")
            # 沒有 infos，當作單一持倉
            text = format_holding_report_text(trader, include_link)
        
        tasks.append(
            send_discord_message(
                bot=bot,
                channel_id=channel_id,
                text=text
            )
        )
    await asyncio.gather(*tasks, return_exceptions=True)

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
    """格式化持倉報告文本（i18n）"""
    i18n = get_i18n()
    locale = normalize_locale(data.get('lang'))

    pair_side = i18n.t(f"common.sides.{str(data.get('pair_side',''))}", locale)
    margin_type = i18n.t(f"common.margin_types.{str(data.get('pair_margin_type',''))}", locale)

    entry_price = str(data.get("entry_price", 0))
    current_price = str(data.get("current_price", 0))
    roi = format_float(data.get("unrealized_pnl_percentage", 0) * 100)
    leverage = format_float(data.get("pair_leverage", 0))

    has_tp = bool(data.get("tp_price"))
    has_sl = bool(data.get("sl_price"))

    text = (
        i18n.t("holding.title", locale) + "\n\n" +
        i18n.render("holding.summary", locale, {"trader_name": data.get('trader_name', 'Trader')}) + "\n\n" +
        f"**{data.get('pair', '')}** {margin_type} **{leverage}X**\n" +
        i18n.render("holding.line_direction", locale, {"pair_side": pair_side}) + "\n" +
        i18n.render("holding.line_entry", locale, {"price": entry_price}) + "\n" +
        i18n.render("holding.line_current", locale, {"price": current_price}) + "\n" +
        i18n.render("holding.line_roi", locale, {"roi": roi})
    )

    tp_sl_lines = []
    if has_tp:
        tp_price = str(data.get("tp_price", 0))
        tp_sl_lines.append(i18n.render("holding.tp", locale, {"price": tp_price}))
    if has_sl:
        sl_price = str(data.get("sl_price", 0))
        tp_sl_lines.append(i18n.render("holding.sl", locale, {"price": sl_price}))

    if tp_sl_lines:
        text += "\n" + "\n".join(tp_sl_lines)

    if include_link:
        trader_name = data.get('trader_name', 'Trader')
        detail_url = data.get('trader_detail_url', '')
        text += "\n\n" + i18n.render("common.detail_line", locale, {"trader_name": trader_name, "url": detail_url})

    return text

def format_holding_report_list_text(infos: list, trader: dict, include_link: bool = True) -> str:
    logger.info(f"[HoldingReport] format_holding_report_list_text called, infos={infos}")
    if not infos:
        return ""
    i18n = get_i18n()
    locale = normalize_locale(trader.get('lang') or (infos[0].get('lang') if infos else None))
    trader_name = trader.get('trader_name', 'Trader')
    text = i18n.render("holding.summary", locale, {"trader_name": trader_name}) + "\n\n"
    for i, data in enumerate(infos, 1):
        pair_side = i18n.t(f"common.sides.{str(data.get('pair_side',''))}", locale)
        margin_type = i18n.t(f"common.margin_types.{str(data.get('pair_margin_type',''))}", locale)
        entry_price = str(data.get("entry_price", 0))
        current_price = str(data.get("current_price", 0))
        roi = format_float(float(data.get("unrealized_pnl_percentage", 0)) * 100)
        leverage = format_float(data.get("pair_leverage", 0))
        has_tp = data.get("tp_price") not in (None, "None", "null", "")
        has_sl = data.get("sl_price") not in (None, "None", "null", "")
        text += f"**{i}. {data.get('pair', '')} {margin_type} {leverage}X**\n"
        text += i18n.render("holding.line_direction", locale, {"pair_side": pair_side}) + "\n"
        text += i18n.render("holding.line_entry", locale, {"price": entry_price}) + "\n"
        text += i18n.render("holding.line_current", locale, {"price": current_price}) + "\n"
        text += i18n.render("holding.line_roi", locale, {"roi": roi})
        tp_sl_lines = []
        if has_tp:
            tp_price = str(data.get("tp_price", 0))
            tp_sl_lines.append(i18n.render("holding.tp", locale, {"price": tp_price}))
        if has_sl:
            sl_price = str(data.get("sl_price", 0))
            tp_sl_lines.append(i18n.render("holding.sl", locale, {"price": sl_price}))
        if tp_sl_lines:
            text += "\n" + "\n".join(tp_sl_lines)
        text += "\n\n"
    text = text.rstrip('\n')
    if include_link:
        detail_url = trader.get('trader_detail_url', '')
        text += "\n\n" + i18n.render("common.detail_line", locale, {"trader_name": trader_name, "url": detail_url})
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
        if isinstance(data, dict):
            logger.info(f"[HoldingReport] 成功解析 JSON 數據: {list(data.keys())}")
        elif isinstance(data, list):
            logger.info(f"[HoldingReport] 成功解析 JSON 數據: list, 長度={len(data)}")
        else:
            logger.info(f"[HoldingReport] 成功解析 JSON 數據: type={type(data)}")
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