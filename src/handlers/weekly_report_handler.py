import os
import asyncio
import logging
import discord
import aiohttp
from fastapi import Request
from typing import Dict, Any
from .common import (
    get_push_targets, format_float, create_async_response,
    generate_trader_summary_image
)

logger = logging.getLogger(__name__)

async def handle_weekly_report(request: Request, bot) -> Dict[str, Any]:
    """
    處理 /api/report/weekly 介面：發送每週績效報告到Discord
    """
    try:
        # 解析 JSON
        data = await request.json()
        logger.info(f"收到週報請求: {data.get('trader_uid', 'unknown')}")
        
        # 資料驗證
        try:
            validate_weekly_report(data)
        except ValueError as err:
            logger.error(f"週報資料驗證失敗: {err}")
            return {"status": "error", "message": str(err)}
        
        # 背景處理，不阻塞 HTTP 回應
        asyncio.create_task(process_weekly_report(data, bot))
        
        return {"status": "success", "message": "週報推送已開始處理"}
        
    except Exception as e:
        logger.error(f"處理週報請求時發生錯誤: {e}")
        return {"status": "error", "message": "內部服務錯誤"}

def validate_weekly_report(data: dict) -> None:
    """驗證週報請求資料，失敗時拋出 ValueError。"""
    required_fields = {
        "trader_uid", "trader_name", "trader_url", "trader_detail_url",
        "total_roi", "total_pnl", "total_trades",
        "win_trades", "loss_trades", "win_rate"
    }

    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        raise ValueError(f"缺少欄位: {', '.join(missing)}")

    # 數值檢查
    try:
        float(data["total_roi"])
        float(data["total_pnl"])
        int(data["total_trades"])
        int(data["win_trades"])
        int(data["loss_trades"])
        float(data["win_rate"])
    except (TypeError, ValueError):
        raise ValueError("數值欄位必須為正確的數字格式")

    # 驗證勝率範圍
    win_rate = float(data["win_rate"])
    if not (0 <= win_rate <= 100):
        raise ValueError("勝率必須在 0-100 之間")

async def process_weekly_report(data: dict, bot) -> None:
    """背景協程：處理週報推送"""
    try:
        trader_uid = str(data["trader_uid"])
        logger.info(f"開始處理週報推送: {trader_uid}")

        # 獲取推送目標
        push_targets = await get_push_targets(trader_uid)
        logger.info(f"找到 {len(push_targets)} 個推送目標")

        if not push_targets:
            logger.warning(f"未找到符合條件的週報推送頻道: {trader_uid}")
            return

        # 生成週報圖片
        img_path = await generate_weekly_report_image(data)
        if not img_path:
            logger.warning("週報圖片生成失敗，取消推送")
            return

        # 準備發送任務
        tasks = []
        for chat_id, topic_id, jump in push_targets:
            try:
                channel = bot.get_channel(int(chat_id))
                if not channel:
                    logger.warning(f"找不到頻道: {chat_id}")
                    continue
                
                # 檢查權限
                permissions = channel.permissions_for(channel.guild.me)
                if not permissions.send_messages:
                    logger.warning(f"在頻道 {chat_id} 中沒有發送消息的權限")
                    continue
                
                # 格式化消息
                content = format_weekly_report_text(data, jump == "1")
                
                # 創建發送任務
                task = send_discord_weekly_report(
                    channel=channel,
                    content=content,
                    image_path=img_path,
                    permissions=permissions
                )
                tasks.append(task)
                
            except Exception as e:
                logger.error(f"準備頻道 {chat_id} 的發送任務時出錯: {e}")

        # 等待所有發送任務完成
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            successful_sends = sum(1 for result in results if result is True)
            logger.info(f"週報推送完成: {successful_sends}/{len(tasks)} 個頻道發送成功")
        else:
            logger.warning("沒有有效的發送任務")

    except Exception as e:
        logger.error(f"推送週報失敗: {e}")

async def send_discord_weekly_report(channel, content: str, image_path: str, permissions) -> bool:
    """發送週報到Discord頻道"""
    try:
        if image_path and os.path.exists(image_path) and permissions.attach_files:
            # 發送帶圖片的消息
            with open(image_path, "rb") as image_file:
                file = discord.File(image_file, filename="weekly_report.png")
                await channel.send(content=content, file=file)
        else:
            # 只發送文字消息
            await channel.send(content=content)
        
        logger.info(f"成功發送週報到頻道: {channel.name} ({channel.id})")
        return True
        
    except Exception as e:
        logger.error(f"發送週報到頻道 {channel.id} 失敗: {e}")
        return False

def format_weekly_report_text(data: dict, include_link: bool = True) -> str:
    """格式化週報文本"""
    # 計算虧損筆數
    total_trades = int(data.get("total_trades", 0))
    win_trades = int(data.get("win_trades", 0))
    loss_trades = total_trades - win_trades
    
    # 格式化數值 - total_roi 需要乘上100以匹配圖片顯示
    total_roi = format_float(float(data.get("total_roi", 0)) * 100)
    win_rate = format_float(data.get("win_rate", 0))
    
    # 判斷盈虧顏色
    is_positive = float(data.get("total_roi", 0)) >= 0
    roi_emoji = "🔥" if is_positive else "📉"
    
    text = (
        f"⚡️{data.get('trader_name', 'Trader')} Weekly Performance Report\n\n"
        f"{roi_emoji} TOTAL R: {total_roi}%\n\n"
        f"📈 Total Trades: {total_trades}\n"
        f"✅ Wins: {win_trades}\n"
        f"❌ Losses: {loss_trades}\n"
        f"🏆 Win Rate: {win_rate}%"
    )
    
    if include_link:
        # 使用 Discord 格式創建可點擊的超連結
        trader_name = data.get('trader_name', 'Trader')
        detail_url = data.get('trader_detail_url', '')
        text += f"\n\n[About {trader_name}, more actions>>]({detail_url})"
    
    return text

async def generate_weekly_report_image(data: dict) -> str:
    """生成週報圖片 - 使用 generate_trader_summary_image 函數"""
    try:
        # 調用 generate_trader_summary_image 函數
        img_path = await generate_trader_summary_image(
            trader_url=data.get("trader_url", ""),
            trader_name=data.get("trader_name", "Unknown"),
            pnl_percentage=data.get("total_roi", 0),
            pnl=data.get("total_pnl", 0)
        )
        
        if img_path:
            # 複製圖片到週報專用的臨時文件
            import shutil
            import tempfile
            weekly_img_path = os.path.join(tempfile.gettempdir(), "weekly_report.png")
            shutil.copy2(img_path, weekly_img_path)
            logger.info(f"週報圖片生成成功: {weekly_img_path}")
            return weekly_img_path
        else:
            logger.error("generate_trader_summary_image 返回空路徑")
            return None
            
    except Exception as e:
        logger.error(f"生成週報圖片失敗: {e}")
        return None 