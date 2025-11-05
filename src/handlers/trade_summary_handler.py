import os
import asyncio
import logging
from typing import Dict
from fastapi import Request
import discord
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from .common import (
    get_push_targets, format_float, format_timestamp_ms_to_utc, get_i18n, normalize_locale
)

load_dotenv()

logger = logging.getLogger(__name__)

def validate_trade_summary(data: dict) -> None:
    """驗證交易總結請求資料，失敗時拋出 ValueError。"""
    required_fields = {
        "trader_uid", "trader_name", "trader_detail_url", "pair", "pair_side",
        "pair_margin_type", "pair_leverage", "entry_price", "exit_price",
        "realized_pnl", "realized_pnl_percentage", "close_time"
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
        float(data["exit_price"])
        float(data["realized_pnl"])
        float(data["realized_pnl_percentage"])
        float(data["pair_leverage"])
    except (TypeError, ValueError):
        raise ValueError("數值欄位必須為正確的數字格式")

    # time 欄位檢查
    try:
        ts_val = int(float(data["close_time"]))
        if ts_val < 10**12:
            raise ValueError("close_time 必須為毫秒級時間戳 (13 位)")
    except (TypeError, ValueError):
        raise ValueError("close_time 必須為毫秒級時間戳 (數字格式)")

async def process_trade_summary_discord(data: dict, bot) -> None:
    """背景協程：處理交易總結推送到 Discord"""
    logger.info("[TradeSummary] 開始執行背景處理任務")
    try:
        trader_uid = str(data["trader_uid"])
        logger.info(f"[TradeSummary] 處理交易員 UID: {trader_uid}")

        # 獲取推送目標
        logger.info("[TradeSummary] 開始獲取推送目標")
        push_targets = await get_push_targets(trader_uid)
        logger.info(f"[TradeSummary] 獲取到 {len(push_targets)} 個推送目標")

        if not push_targets:
            logger.warning(f"[TradeSummary] 未找到符合條件的交易總結推送頻道: {trader_uid}")
            return

        # 生成交易總結圖片
        logger.info("[TradeSummary] 開始生成交易總結圖片")
        img_path = generate_trade_summary_image(data)
        if not img_path:
            logger.warning("[TradeSummary] 交易總結圖片生成失敗，取消推送")
            return
        logger.info(f"[TradeSummary] 圖片生成成功: {img_path}")

        # 準備發送任務
        tasks = []
        logger.info(f"[TradeSummary] 準備發送到 {len(push_targets)} 個頻道")
        
        for i, (channel_id, topic_id, jump, channel_lang) in enumerate(push_targets):
            logger.info(f"[TradeSummary] 處理第 {i+1} 個頻道: {channel_id}, topic: {topic_id}, jump: {jump}, lang: {channel_lang}")
            
            # 根據 jump 值決定是否包含連結
            include_link = (jump == "1")
            text = format_trade_summary_text(data, include_link, channel_lang)
            logger.info(f"[TradeSummary] 為頻道 {channel_id} 準備消息內容")
            
            tasks.append(
                send_discord_message_with_image(
                    bot=bot,
                    channel_id=channel_id,
                    text=text,
                    image_path=img_path
                )
            )

        # 等待 Discord 發送結果
        logger.info(f"[TradeSummary] 開始並發發送 {len(tasks)} 個消息")
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 檢查發送結果
        success_count = 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"[TradeSummary] 頻道 {push_targets[i][0]} 發送失敗: {result}")
            else:
                success_count += 1
                logger.info(f"[TradeSummary] 頻道 {push_targets[i][0]} 發送成功")
        
        logger.info(f"[TradeSummary] 發送完成: {success_count}/{len(tasks)} 成功")

    except Exception as e:
        logger.error(f"[TradeSummary] 推送交易總結到 Discord 失敗: {type(e).__name__} - {e}")
        import traceback
        logger.error(f"[TradeSummary] 詳細錯誤: {traceback.format_exc()}")

async def send_discord_message_with_image(bot, channel_id: int, text: str, image_path: str) -> None:
    """發送帶圖片的 Discord 消息"""
    logger.info(f"[TradeSummary] 開始發送消息到頻道 {channel_id}")
    try:
        channel = bot.get_channel(channel_id)
        if not channel:
            logger.warning(f"[TradeSummary] 找不到頻道 {channel_id}")
            return

        logger.info(f"[TradeSummary] 找到頻道: {channel.name} (ID: {channel_id})")

        # 檢查權限
        permissions = channel.permissions_for(channel.guild.me)
        logger.info(f"[TradeSummary] 頻道權限檢查 - 發送消息: {permissions.send_messages}, 附加文件: {permissions.attach_files}")
        
        if not permissions.send_messages:
            logger.warning(f"[TradeSummary] 在頻道 {channel_id} 中沒有發送消息的權限")
            return

        # 檢查圖片文件是否存在
        if image_path:
            import os
            if os.path.exists(image_path):
                logger.info(f"[TradeSummary] 圖片文件存在: {image_path}")
            else:
                logger.warning(f"[TradeSummary] 圖片文件不存在: {image_path}")
                image_path = None

        if image_path and permissions.attach_files:
            logger.info(f"[TradeSummary] 發送帶圖片的消息到頻道 {channel_id}")
            discord_file = discord.File(image_path, filename="trade_summary.png")
            await channel.send(content=text, file=discord_file, allowed_mentions=discord.AllowedMentions.none())
        else:
            logger.info(f"[TradeSummary] 發送純文字消息到頻道 {channel_id}")
            await channel.send(content=text, allowed_mentions=discord.AllowedMentions.none())

        logger.info(f"[TradeSummary] 成功發送到 Discord 頻道 {channel_id}")

    except discord.Forbidden as e:
        logger.error(f"[TradeSummary] 權限錯誤 - 發送到 Discord 頻道 {channel_id} 失敗: {e}")
    except discord.HTTPException as e:
        logger.error(f"[TradeSummary] HTTP 錯誤 - 發送到 Discord 頻道 {channel_id} 失敗: {e}")
    except Exception as e:
        logger.error(f"[TradeSummary] 未知錯誤 - 發送到 Discord 頻道 {channel_id} 失敗: {type(e).__name__} - {e}")
        import traceback
        logger.error(f"[TradeSummary] 詳細錯誤: {traceback.format_exc()}")

def format_trade_summary_text(data: dict, include_link: bool = True, lang: str = None) -> str:
    """格式化交易總結文本（i18n）"""
    i18n = get_i18n()
    locale = normalize_locale(lang)

    pair_side = i18n.t(f"common.sides.{str(data.get('pair_side',''))}", locale)
    margin_type = i18n.t(f"common.margin_types.{str(data.get('pair_margin_type',''))}", locale)

    entry_price = str(data.get("entry_price", 0))
    exit_price = str(data.get("exit_price", 0))
    realized_pnl = format_float(float(data.get("realized_pnl_percentage", 0)) * 100)
    leverage = format_float(data.get("pair_leverage", 0))

    formatted_time = format_timestamp_ms_to_utc(data.get('close_time'))

    text = (
        i18n.t("summary.title", locale) + "\n\n" +
        i18n.render("summary.close", locale, {"trader_name": data.get('trader_name', 'Trader')}) + "\n\n" +
        f"**{data.get('pair', '')}** {margin_type} **{leverage}X**\n\n" +
        i18n.render("summary.line_time", locale, {"time": formatted_time}) + "\n" +
        i18n.render("summary.line_direction", locale, {"pair_side": pair_side}) + "\n" +
        i18n.render("summary.line_roi", locale, {"roi": realized_pnl}) + "\n" +
        i18n.render("summary.line_entry", locale, {"price": entry_price}) + "\n" +
        i18n.render("summary.line_exit", locale, {"price": exit_price})
    )

    if include_link:
        trader_name = data.get('trader_name', 'Trader')
        detail_url = data.get('trader_detail_url', '')
        text += "\n\n" + i18n.render("common.detail_line", locale, {"trader_name": trader_name, "url": detail_url})

    return text

def generate_trade_summary_image(data: dict) -> str:
    """生成交易總結圖片 - 配合新背景圖格式"""
    logger.info(f"[TradeSummary] 開始生成交易總結圖片")
    try:
        # 載入背景圖
        bg_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'pics', 'trade_summary.png'))
        logger.info(f"[TradeSummary] 背景圖片路徑: {bg_path}")
        
        if os.path.exists(bg_path):
            img = Image.open(bg_path).convert('RGB')
            logger.info(f"[TradeSummary] 成功載入背景圖片")
        else:
            # 如果背景圖不存在，創建預設背景
            logger.info(f"[TradeSummary] 背景圖片不存在，使用預設背景")
            img = Image.new('RGB', (1200, 675), color=(40, 40, 40))
        
        draw = ImageDraw.Draw(img)
        
        # 載入字體
        font_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'text'))
        bold_font_path = os.path.join(font_dir, 'BRHendrix-Bold-BF6556d1b5459d3.otf')
        medium_font_path = os.path.join(font_dir, 'BRHendrix-Medium-BF6556d1b4e12b2.otf')
        noto_bold_font_path = os.path.join(font_dir, 'NotoSansSC-Bold.ttf')
        
        logger.info(f"[TradeSummary] 字體目錄: {font_dir}")
        logger.info(f"[TradeSummary] 粗體字體路徑: {bold_font_path}")
        logger.info(f"[TradeSummary] Noto字體路徑: {noto_bold_font_path}")
        
        try:
            # 大字體用於主要數值
            large_font = ImageFont.truetype(bold_font_path, 110)
            # 中等字體用於標籤
            medium_font = ImageFont.truetype(noto_bold_font_path, 53)
            # 小字體用於其他信息
            small_font = ImageFont.truetype(noto_bold_font_path, 35)
            logger.info(f"[TradeSummary] 字體載入成功")
        except Exception as e:
            logger.warning(f"[TradeSummary] 字體載入失敗: {e}")
            return None
        
        # 格式化數值
        realized_pnl = format_float(float(data.get("realized_pnl_percentage", 0)) * 100)
        entry_price = str(data.get("entry_price", 0))
        exit_price = str(data.get("exit_price", 0))
        leverage = format_float(data.get("pair_leverage", 0))
        
        # 判斷盈虧顏色
        is_positive = float(data.get("realized_pnl_percentage", 0)) >= 0
        pnl_color = (0, 191, 99) if is_positive else (237, 29, 36)  # 綠色或紅色
        
        # 判斷交易方向顏色
        is_long = str(data.get("pair_side", "")) == "1"
        direction_color = (0, 191, 99) if is_long else (237, 29, 36)  # Long用綠色，Short用紅色
        
        # 在背景圖上填充數值到對應位置
        # 根據第二張照片的風格調整位置，增加間距並靠左
        
        # 交易對標題 (頂部)
        pair_text = f"{data.get('pair', '')} Perpetual"
        draw.text((80, 70), pair_text, font=medium_font, fill=(255, 255, 255))
        
        # 槓桿信息 (交易對下方) - 根據方向設置顏色
        pair_side_map = {"1": "Long", "2": "Short", 1: "Long", 2: "Short"}
        pair_side = pair_side_map.get(str(data.get("pair_side", "")), str(data.get("pair_side", "")))
        leverage_text = f"{pair_side} {leverage}X"
        draw.text((80, 140), leverage_text, font=small_font, fill=direction_color)
        
        # Cumulative ROI 標籤
        draw.text((80, 265), "Cumulative ROI", font=medium_font, fill=(200, 200, 200))
        
        # ROI 數值 (主要顯示，在標籤下方) - 根據盈虧設置顏色
        roi_text = f"{realized_pnl}%"
        draw.text((80, 340), roi_text, font=large_font, fill=pnl_color)
        
        # 價格信息 (底部) - 分開繪製標籤和數值
        # Exit Price 標籤和數值 (在上方)
        draw.text((80, 500), "Exit Price", font=small_font, fill=(200, 200, 200))
        draw.text((290, 500), exit_price, font=small_font, fill=(255, 255, 255))
        
        # Entry Price 標籤和數值 (在下方)
        draw.text((80, 560), "Entry Price", font=small_font, fill=(200, 200, 200))
        draw.text((290, 560), entry_price, font=small_font, fill=(255, 255, 255))
        
        logger.info(f"[TradeSummary] 圖片文字繪製完成")
        
        # 保存圖片
        temp_path = "/tmp/trade_summary_discord.png"
        logger.info(f"[TradeSummary] 保存圖片到: {temp_path}")
        try:
            img.save(temp_path, quality=95)
            logger.info(f"[TradeSummary] 圖片保存成功")
            return temp_path
        except Exception as e:
            logger.error(f"[TradeSummary] 圖片保存失敗: {e}")
            return None
        
    except Exception as e:
        logger.error(f"[TradeSummary] 生成交易總結圖片失敗: {type(e).__name__} - {e}")
        import traceback
        logger.error(f"[TradeSummary] 詳細錯誤: {traceback.format_exc()}")
        return None

async def handle_send_trade_summary(request: Request, bot) -> Dict:
    """
    處理 /api/discord/trade_summary 介面：
    1. 先同步驗證輸入資料，失敗直接回傳 400。
    2. 成功則立即回 200，並將實際推送工作交由背景協程處理。
    """
    logger.info("[TradeSummary] 開始處理 trade summary 請求")
    
    # Content-Type 檢查
    content_type = request.headers.get("content-type", "").split(";")[0].lower()
    logger.info(f"[TradeSummary] Content-Type: {content_type}")
    if content_type != "application/json":
        logger.error(f"[TradeSummary] Content-Type 錯誤: {content_type}")
        return {"status": "400", "message": "Content-Type must be application/json"}

    # 解析 JSON
    try:
        data = await request.json()
        logger.info(f"[TradeSummary] 成功解析 JSON 數據: {list(data.keys())}")
    except Exception as e:
        logger.error(f"[TradeSummary] JSON 解析失敗: {e}")
        return {"status": "400", "message": "Invalid JSON body"}

    # 資料驗證
    try:
        validate_trade_summary(data)
        logger.info("[TradeSummary] 數據驗證通過")
    except ValueError as err:
        logger.error(f"[TradeSummary] 數據驗證失敗: {err}")
        return {"status": "400", "message": str(err)}

    # 背景處理：在 Discord 事件迴圈執行
    logger.info("[TradeSummary] 開始背景處理，調度到 Discord 事件迴圈")
    try:
        asyncio.run_coroutine_threadsafe(process_trade_summary_discord(data, bot), bot.loop)
        logger.info("[TradeSummary] 成功調度背景任務")
    except Exception as e:
        logger.error(f"[TradeSummary] 調度背景任務失敗: {e}")
        return {"status": "500", "message": "Internal server error"}
    
    return {"status": "200", "message": "接收成功，稍後發送"} 