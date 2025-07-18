import io
import os
import discord
import logging
import asyncio
import aiohttp
import aiofiles
import uvicorn
from discord.ext import commands, tasks
from discord.ui import Button, View, Modal, TextInput
from discord import ButtonStyle, TextStyle
from dotenv import load_dotenv
from db_handler_aio import *
from typing import Dict, Optional
from functools import lru_cache
from discord.ext.commands import CommandNotFound
from fastapi import FastAPI, Query, Request, BackgroundTasks
from threading import Thread
from typing import Union
import re
from handlers.copy_signal_handler import handle_send_copy_signal
from handlers.trade_summary_handler import handle_send_trade_summary
from handlers.scalp_update_handler import handle_send_scalp_update
from handlers.holding_report_handler import handle_holding_report
from handlers.weekly_report_handler import handle_weekly_report
from multilingual_utils import get_multilingual_content

# Set up logging
logging.basicConfig(level=logging.INFO)

# Load environment variables
load_dotenv()
app = FastAPI()

# API endpoints
WELCOME_API = os.getenv("WELCOME_API")
VERIFY_API = os.getenv("VERIFY_API")
DETAIL_API = os.getenv("DETAIL_API")
SOCIAL_API = os.getenv("SOCIAL_API")
MESSAGE_API_URL = os.getenv("MESSAGE_API_URL")
UPDATE_MESSAGE_API_URL = os.getenv("UPDATE_MESSAGE_API_URL")

# Bot initialization
TOKEN = os.getenv("Discord_TOKEN")
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.presences = True  # 新增 presence intent 用於檢測在線狀態

class ChannelManager:
    def __init__(self):
        self.channel_cache: Dict[int, Dict[str, int]] = {}

    async def get_channel_id(self, guild: discord.Guild, channel_name: str) -> Optional[int]:
        """Get channel ID from cache or fetch it from guild"""
        if guild.id not in self.channel_cache:
            self.channel_cache[guild.id] = {}

        if channel_name not in self.channel_cache[guild.id]:
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if channel:
                self.channel_cache[guild.id][channel_name] = channel.id
            else:
                return None

        return self.channel_cache[guild.id].get(channel_name)

    def invalidate_cache(self, guild_id: int):
        """Invalidate cache for a specific guild"""
        if guild_id in self.channel_cache:
            del self.channel_cache[guild_id]

class MessagePublisher:
    def __init__(self, bot, session):
        self.bot = bot
        self.session = session
        # topic_name -> list of dict: {"channel_id": int, "lang": str}
        self.topic_to_channel_map = {}

    async def refresh_social_mapping(self):
        """Refresh the topic to channel mapping, now with lang info"""
        payload = {
            "brand": "BYD",
            "type": "DISCORD"
        }

        async with self.session.post(SOCIAL_API, data=payload) as response:
            if response.status != 200:
                raise ValueError("Failed to fetch social data")

            social_data = await response.json()
            social_groups = social_data.get("data", [])

            # Update mapping
            self.topic_to_channel_map.clear()
            for group in social_groups:
                for chat in group.get("chats", []):
                    if chat.get("enable", False):
                        topic = chat["name"]
                        channel_id = int(chat["chatId"])
                        lang = chat.get("lang", "en")
                        if topic not in self.topic_to_channel_map:
                            self.topic_to_channel_map[topic] = []
                        self.topic_to_channel_map[topic].append({"channel_id": channel_id, "lang": lang})


    async def handle_image(self, image_url, article_id):
        """Handle image download and return file path"""
        if not image_url:
            return None

        if not image_url.startswith("http"):
            image_url = f"https://sp.signalcms.com{image_url}"
            # image_url = f"http://172.25.183.139:5003{image_url}"
            # image_url = f"http://127.0.0.1:5003{image_url}"

        pics_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pics")
        os.makedirs(pics_dir, exist_ok=True)
        temp_file_path = os.path.join(pics_dir, f"temp_image_{article_id}.jpg")

        async with self.session.get(image_url) as response:
            if response.status != 200:
                logging.error(f"Failed to download image: {image_url}")
                return None

            async with aiofiles.open(temp_file_path, "wb") as f:
                await f.write(await response.read())

        return temp_file_path

    # async def mark_as_published(self, article_id):
    #     """Mark article as published"""
    #     update_payload = {"id": article_id, "is_sent_dc": 1}
    #     async with self.session.post(UPDATE_MESSAGE_API_URL, json=update_payload) as response:
    #         if response.status != 200:
    #             raise ValueError(f"Failed to mark article {article_id} as published")
    #         logging.info(f"Article {article_id} marked as published")

    async def mark_as_published(self, article_id):
        """標記文章為已發布"""
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            update_payload = {"id": article_id, "is_sent_dc": 1}
            async with self.session.post(UPDATE_MESSAGE_API_URL, json=update_payload) as response:
                response_text = await response.text()
                if response.status != 200:
                    logging.error(f"標記文章 {article_id} 為已發布失敗: 狀態碼 {response.status}, 回應: {response_text} - 時間: {current_time}")
                    return False
                logging.info(f"文章 {article_id} 已標記為已發布 - 時間: {current_time}")
                return True
        except Exception as e:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logging.error(f"標記文章 {article_id} 為已發布時發生錯誤: {type(e).__name__} - {e} - 時間: {current_time}")
            return False

class OptimizedBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.channel_manager = ChannelManager()
        self.verified_users = {}

    @lru_cache(maxsize=1000)
    def get_admin_mention(self, guild_id: int) -> str:
        """Cache admin mention for each guild"""
        guild = self.get_guild(guild_id)
        if not guild:
            return "the admin team"

        for member in guild.members:
            if member.guild_permissions.administrator:
                return member.mention
        return "the admin team"

    def get_guild_member_count(self, guild_id: int) -> Dict:
        """Get member count for a specific guild"""
        guild = self.get_guild(guild_id)
        if not guild:
            return {
                "success": False,
                "message": "Guild not found",
                "data": {
                    "guild_id": guild_id,
                    "guild_name": None,
                    "total_members": 0,
                    "online_members": 0,
                    "verified_members": 0
                }
            }

        return {
            "success": True,
            "message": "Data retrieved successfully",
            "data": {
                "guild_id": guild_id,
                "guild_name": guild.name,
                "total_members": guild.member_count,
                "online_members": len([m for m in guild.members if m.status != discord.Status.offline]),
                "verified_members": len([m for m in guild.members if any(r.name == "BYDFi Signal" for r in m.roles)])
            }
        }

bot = OptimizedBot(command_prefix="!", intents=intents)

# 註冊 Copy-Signal 端點
@app.post("/api/discord/copy_signal")
async def send_copy_signal_to_discord(request: Request):
    return await handle_send_copy_signal(request, bot)

# 註冊 Trade Summary 端點
@app.post("/api/discord/trade_summary")
async def send_trade_summary_to_discord(request: Request):
    return await handle_send_trade_summary(request, bot)

# 註冊 Scalp Update 端點
@app.post("/api/discord/scalp_update")
async def send_scalp_update_to_discord(request: Request):
    return await handle_send_scalp_update(request, bot)

# 註冊 Holding Report 端點
@app.post("/api/report/holdings")
async def send_holding_report_to_discord(request: Request):
    return await handle_holding_report(request, bot)

# 註冊 Weekly Report 端點
@app.post("/api/report/weekly")
async def send_weekly_report_to_discord(request: Request):
    return await handle_weekly_report(request, bot)

class UIDInputModal(Modal):
    def __init__(self):
        super().__init__(title="Enter Your UID")
        
        # 建立文字輸入框
        self.uid_input = TextInput(
            label="Your UID",
            placeholder="Enter your UID here...",
            min_length=1,
            max_length=20,
            required=True,
            style=TextStyle.short
        )
        self.add_item(self.uid_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        # 暫緩回應以處理驗證
        await interaction.response.defer(ephemeral=True)
        
        # 獲取UID
        uid = self.uid_input.value
        
        # 獲取機器人實例
        bot = interaction.client
        
        # 檢查用戶是否已經驗證
        role = discord.utils.get(interaction.user.roles, name="BYDFi Signal")
        if role:
            await interaction.followup.send("You have already been verified.", ephemeral=True)
            return
        
        # 使用現有的驗證邏輯
        verify_channel_id = interaction.channel.id
        
        verification_status = await is_user_verified(interaction.user.id, verify_channel_id, uid)
        
        if verification_status == "verified":
            await interaction.followup.send("You have already been verified.", ephemeral=True)
            return
        
        if verification_status == "warning":
            await interaction.followup.send("This UID has already been verified.", ephemeral=True)
            return
        
        if verification_status == "not_verified" or verification_status == "reverified":
            # 使用現有的API驗證邏輯
            payload = {
                "code": uid,
                "verifyGroup": verify_channel_id,
                "brand": "BYD",
                "type": "DISCORD"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(VERIFY_API, data=payload) as response:
                    data = await response.json()
                    print(f"response: {data}")
                    api_message = data.get("data", "Verification failed. Please try again.")
                    
                    admin_mention = interaction.guild.owner.mention if interaction.guild.owner else "@admin"
                    api_message = api_message.replace("@{admin}", admin_mention)
                    api_message = api_message.replace("<a>", "").replace("</a>", "")
                    
                    if response.status == 200 and "verification successful" in api_message:
                        api_message = api_message.replace("@{username}", "").replace("{Approval Link}", "").strip()
                        bot.verified_users[interaction.user.id] = uid
                        
                        try:
                            role = discord.utils.get(interaction.guild.roles, name="BYDFi Signal")
                            if role:
                                # 檢查機器人是否有權限添加角色
                                bot_member = interaction.guild.get_member(interaction.client.user.id)
                                if not bot_member.guild_permissions.manage_roles:
                                    await interaction.followup.send("機器人缺少管理角色的權限，請聯繫伺服器管理員。", ephemeral=True)
                                    return
                                    
                                # 檢查機器人角色是否高於目標角色
                                if role.position >= bot_member.top_role.position:
                                    await interaction.followup.send("機器人的角色等級不足以分配此角色，請聯繫伺服器管理員。", ephemeral=True)
                                    return
                                    
                                try:
                                    await interaction.user.add_roles(role)
                                    await interaction.followup.send(f"{api_message}", ephemeral=True)
                                    await add_verified_user(interaction.user.id, verify_channel_id, uid)
                                except discord.Forbidden:
                                    # 仍然添加到數據庫，但告知用戶需請管理員手動授予角色
                                    await add_verified_user(interaction.user.id, verify_channel_id, uid)
                                    await interaction.followup.send(f"{api_message}\n\n但無法自動分配角色，請聯繫伺服器管理員獲取「BYDFi Signal」角色。", ephemeral=True)
                                    # 可選：向管理員發送通知
                            else:
                                await interaction.followup.send("驗證成功，但找不到'BYDFi Signal'角色，請聯繫伺服器管理員。", ephemeral=True)
                        except Exception as e:
                            logging.error(f"角色分配錯誤: {e}")
                            await interaction.followup.send("驗證過程中發生錯誤，請聯繫管理員。", ephemeral=True)
                    else:
                        await interaction.followup.send(f"{api_message}", ephemeral=True)
        else:
            await interaction.followup.send(f"{verification_status} UID.", ephemeral=True)

# 驗證按鈕視圖
class VerifyView(View):
    def __init__(self):
        super().__init__(timeout=None)  # 設置為永久按鈕
        
        # 添加驗證按鈕
        verify_button = Button(
            style=ButtonStyle.primary,
            label="\n🔥 Enter BYDFi UID here! 🔥\n",
            custom_id="verify_button"
        )
        self.add_item(verify_button)
        
        # 設置回調函數
        verify_button.callback = self.verify_callback
    
    async def verify_callback(self, interaction: discord.Interaction):
    # 顯示UID輸入模態框
        await interaction.response.send_modal(UIDInputModal())

# 註冊持久化視圖
@bot.event
async def on_ready():
    print(f"Bot is ready. Logged in as {bot.user}")
    
    # 註冊持久化視圖
    bot.add_view(VerifyView())
    
    # 啟動定時任務
    fetch_unpublished_messages.start()

# 權限檢查函數 - 根據設定的角色清單檢查權限
def has_permission_to_create(ctx):
    # 設定允許使用指令的角色清單
    allowed_roles = ["Admin", "Moderator", "BYDFi Admin"]
    
    # 如果用戶是伺服器擁有者或管理員，允許使用
    if ctx.author.guild_permissions.administrator or ctx.author == ctx.guild.owner:
        return True
    
    # 檢查用戶是否有允許的角色
    for role in ctx.author.roles:
        if role.name in allowed_roles:
            return True
    
    return False

# @bot.command(name="createwelcome")
# async def create_welcome(ctx, *, text=None):
#     """創建帶有驗證按鈕的歡迎訊息 (可選帶圖片)"""
#     # 立即刪除用戶的命令消息
#     try:
#         await ctx.message.delete()
#     except Exception as e:
#         logging.error(f"Error deleting message: {e}")
    
#     # 檢查權限
#     if not has_permission_to_create(ctx):
#         # 發送私人消息
#         await ctx.author.send(f"You don't have permission to use this command.")
#         return
    
#     # 檢查文本是否提供
#     if not text:
#         # 發送私人消息
#         await ctx.author.send(f"Please provide welcome text. Usage: `!createwelcome Your welcome text`")
#         return
    
#     # 檢查伺服器中是否已有驗證按鈕消息
    # has_verify_button = False
    # button_channel = None
    # for channel in ctx.guild.text_channels:
    #     try:
    #         async for message in channel.history(limit=100):
    #             if message.author == bot.user and len(message.components) > 0:
    #                 for row in message.components:
    #                     for component in row.children:
    #                         if component.custom_id == "verify_button":
    #                             has_verify_button = True
    #                             button_channel = channel
    #                             break
    #                     if has_verify_button:
    #                         break
    #             if has_verify_button:
    #                 break
    #         if has_verify_button:
    #             break
    #     except (discord.Forbidden, discord.HTTPException):
    #         continue
    
#     if has_verify_button:
#         # 發送私人消息
#         await ctx.author.send(f"This server already has a verification button in #{button_channel.name}. Please delete the existing message first to create a new one.")
#         return
    
#     # 檢查是否有附加圖片
#     has_image = False
#     image_url = None
    
#     if len(ctx.message.attachments) > 0:
#         for attachment in ctx.message.attachments:
#             if attachment.content_type.startswith('image/'):
#                 has_image = True
#                 image_url = attachment.url
#                 break
    
#     bold_text = f"**{text}**"
#     # 創建嵌入式消息
#     embed = discord.Embed(
#         description=bold_text,
#         color=discord.Color.blue()
#     )
    
#     # 如果有圖片，添加到嵌入消息中
#     if has_image:
#         embed.set_image(url=image_url)
#         message = await ctx.send(embed=embed, view=VerifyView())
#     else:
#         # 發送不帶圖片的歡迎消息
#         message = await ctx.send(embed=embed, view=VerifyView())
    
#     # 將消息置頂
#     try:
#         await message.pin(reason="Welcome message with verification button")
#         logging.info(f"Successfully pinned welcome message in channel {ctx.channel.name}")
#     except discord.Forbidden:
#         # 使用 ephemeral=True 讓錯誤消息只有發送命令的用戶可見
#         await ctx.send(f"{ctx.author.mention}, I don't have permission to pin messages.", ephemeral=True)
#     except Exception as e:
#         logging.error(f"Error pinning message: {e}")

@bot.command(name="createwelcome")
async def create_welcome(ctx, *, text=None):
    """創建帶有驗證按鈕的歡迎訊息 (可選帶圖片)"""
    # 先保存附件，再刪除消息
    has_image = False
    temp_file_path = None
    
    if len(ctx.message.attachments) > 0:
        for attachment in ctx.message.attachments:
            if attachment.content_type and attachment.content_type.startswith('image/'):
                # 創建臨時目錄
                temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "temp")
                os.makedirs(temp_dir, exist_ok=True)
                
                # 使用唯一文件名
                import uuid
                temp_filename = f"{uuid.uuid4()}{os.path.splitext(attachment.filename)[1]}"
                temp_file_path = os.path.join(temp_dir, temp_filename)
                
                # 下載附件
                await attachment.save(temp_file_path)
                has_image = True
                break
    
    # 現在刪除用戶的命令消息
    try:
        await ctx.message.delete()
    except Exception as e:
        logging.error(f"Error deleting message: {e}")
    
    # 檢查權限
    if not has_permission_to_create(ctx):
        await safe_dm(ctx, "You don't have permission to use this command.")
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)  # 清理臨時文件
        return
    
    # 檢查文本是否提供
    if not text:
        await safe_dm(ctx, "Please provide welcome text. Usage: `!createwelcome Your welcome text`")
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)  # 清理臨時文件
        return
    
    has_verify_button = False
    button_channel = None
    for channel in ctx.guild.text_channels:
        try:
            async for message in channel.history(limit=100):
                if message.author == bot.user and len(message.components) > 0:
                    for row in message.components:
                        for component in row.children:
                            if component.custom_id == "verify_button":
                                has_verify_button = True
                                button_channel = channel
                                break
                        if has_verify_button:
                            break
                if has_verify_button:
                    break
            if has_verify_button:
                break
        except (discord.Forbidden, discord.HTTPException):
            continue
    
    if has_verify_button:
        await safe_dm(ctx, f"This server already has a verification button in #{button_channel.name}. Please delete the existing message first to create a new one.")
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)  # 清理臨時文件
        return
    text = text.replace('\\n', '\n')
    # bold_text = f"**{text}**"
    # 創建嵌入式消息
    embed = discord.Embed(
        description=text,
        color=discord.Color.blue()
    )
    
    # 如果有圖片，添加到嵌入消息中
    if has_image and temp_file_path and os.path.exists(temp_file_path):
        file = discord.File(temp_file_path, filename=os.path.basename(temp_file_path))
        embed.set_image(url=f"attachment://{os.path.basename(temp_file_path)}")
        message = await ctx.send(file=file, embed=embed, view=VerifyView())
        
        # 刪除臨時文件
        try:
            os.remove(temp_file_path)
        except:
            pass
    else:
        # 發送不帶圖片的歡迎消息
        message = await ctx.send(embed=embed, view=VerifyView())
    
    # 將消息置頂
    try:
        await message.pin(reason="Welcome message with verification button")
        logging.info(f"Successfully pinned welcome message in channel {ctx.channel.name}")
        # 通知用戶操作成功
        await safe_dm(ctx, f"Welcome message has been created successfully in #{ctx.channel.name}.")
    except discord.Forbidden:
        await safe_dm(ctx, f"I don't have permission to pin messages in #{ctx.channel.name}.")
    except Exception as e:
        logging.error(f"Error pinning message: {e}")
        await safe_dm(ctx, f"Error pinning message: {str(e)}")

@bot.command(name="createwelcome_local")
async def create_welcome_local(ctx, image_name=None, *, text=None):
    """使用本地圖片創建帶有驗證按鈕的歡迎訊息"""
    # 立即刪除用戶的命令消息
    try:
        await ctx.message.delete()
    except Exception as e:
        logging.error(f"刪除消息時發生錯誤: {e}")
    
    # 檢查權限
    if not has_permission_to_create(ctx):
        # 發送私人消息
        await safe_dm(ctx, "You don't have permission to use this command.")
        return
    
    # 檢查文本是否提供
    if not text:
        # 發送私人消息
        await safe_dm(ctx, "Please provide welcome text. Usage:\n"
            "`!createwelcome_local image_name Your welcome text`\n"
            "Or without image:\n"
            "`!createwelcome_local none Your welcome text`")
        return
    
    # 檢查伺服器中是否已有驗證按鈕消息
    has_verify_button = False
    button_channel = None
    for channel in ctx.guild.text_channels:
        try:
            async for message in channel.history(limit=100):
                if message.author == bot.user and len(message.components) > 0:
                    for row in message.components:
                        for component in row.children:
                            if component.custom_id == "verify_button":
                                has_verify_button = True
                                button_channel = channel
                                break
                        if has_verify_button:
                            break
                if has_verify_button:
                    break
            if has_verify_button:
                break
        except (discord.Forbidden, discord.HTTPException):
            continue
    
    if has_verify_button:
        # 發送私人消息
        await safe_dm(ctx, f"This server already has a verification button in #{button_channel.name}. Please delete the existing message first to create a new one.")
        return
    text = text.replace('\\n', '\n')
    # bold_text = f"**{text}**"
    # 創建嵌入式消息
    embed = discord.Embed(
        description=text,
        color=discord.Color.blue()
    )
    
    # 檢查是否使用本地圖片
    if image_name and image_name.lower() != "none":
        current_dir = os.path.dirname(os.path.abspath(__file__))
        image_path = os.path.join(current_dir, "..", "pics", image_name)
        
        if os.path.exists(image_path):
            file = discord.File(image_path, filename=image_name)
            embed.set_image(url=f"attachment://{image_name}")
            # 發送帶有圖片和驗證按鈕的歡迎消息
            message = await ctx.send(file=file, embed=embed, view=VerifyView())
            
            # 將消息置頂
            try:
                await message.pin(reason="Welcome message with verification button")
                logging.info(f"Successfully pinned welcome message in channel {ctx.channel.name}")
            except discord.Forbidden:
                # 使用 ephemeral=True 讓錯誤消息只有發送命令的用戶可見
                await safe_dm(ctx, f"{ctx.author.mention}, I don't have permission to pin messages.")
            except Exception as e:
                logging.error(f"Error pinning message: {e}")
        else:
            # 使用 ephemeral=True 讓錯誤消息只有發送命令的用戶可見
            await safe_dm(ctx, f"{ctx.author.mention}, Image not found: {image_name}. Please make sure the image file is in the pics directory.")
            return
    else:
        # 發送不帶圖片的歡迎消息
        message = await ctx.send(embed=embed, view=VerifyView())
        
        # 將消息置頂
        try:
            await message.pin(reason="Welcome message with verification button")
            logging.info(f"Successfully pinned welcome message in channel {ctx.channel.name}")
        except discord.Forbidden:
            # 使用 ephemeral=True 讓錯誤消息只有發送命令的用戶可見
            await safe_dm(ctx, f"{ctx.author.mention}, I don't have permission to pin messages.")
        except Exception as e:
            logging.error(f"Error pinning message: {e}")

# 查看可用圖片列表的命令
@bot.command(name="listimages")
async def list_images(ctx):
    """List available images for welcome messages"""
    # 立即刪除用戶的命令消息
    try:
        await ctx.message.delete()
    except Exception as e:
        logging.error(f"Error deleting message: {e}")
    
    # 檢查權限
    if not has_permission_to_create(ctx):
        await safe_dm(ctx, "You don't have permission to use this command.")
        return
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    pics_dir = os.path.join(current_dir, "..", "pics")
    
    if not os.path.exists(pics_dir):
        await safe_dm(ctx, "The pics directory was not found.")
        return
    
    images = []
    for file in os.listdir(pics_dir):
        if file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
            images.append(file)
    
    if images:
        message = "Available images:\n" + "\n".join(images)
        await safe_dm(ctx, message)
    else:
        await safe_dm(ctx, "No available images found.")

# @bot.event
# async def on_member_join(member):
#     try:
#         verify_channel_id = await bot.channel_manager.get_channel_id(member.guild, "verify")
#         if not verify_channel_id:
#             logging.warning(f"未找到伺服器 {member.guild.name} 的 verify 頻道。")
#             return

#         payload = {
#             "verifyGroup": str(verify_channel_id),
#             "brand": "BYD",
#             "type": "DISCORD"
#         }

#         async with aiohttp.ClientSession() as session:
#             async with session.post(WELCOME_API, data=payload) as response:
#                 if response.status == 200:
#                     data = await response.json()
#                     welcome_message = data.get("data", "Welcome to the server!")
#                 else:
#                     welcome_message = "Welcome to the server!"

#         welcome_message = welcome_message.replace("📣 Dear @{username}", "")
#         welcome_message = welcome_message.replace("/verify", "!verify")
#         welcome_message = welcome_message.replace("<a>", "").replace("</a>", "")
#         welcome_message = f"📣 Dear {member.mention} {welcome_message}".strip()

#         welcome_channel_id = await bot.channel_manager.get_channel_id(member.guild, "welcome")
#         if welcome_channel_id:
#             welcome_channel = member.guild.get_channel(welcome_channel_id)
#             current_dir = os.path.dirname(os.path.abspath(__file__))
#             image_path = os.path.join(current_dir, "..", "pics", "FindUID.jpg")

#             with open(image_path, "rb") as image:
#                 file = discord.File(image, filename="FindUID.jpg")
#                 await welcome_channel.send(
#                     content=welcome_message,
#                     file=file,
#                     allowed_mentions=discord.AllowedMentions(users=True)
#                 )

#     except Exception as e:
#         logging.error(f"處理新成員加入事件時發生錯誤: {e}")

@bot.event
async def on_member_remove(member):
    try:
        # 直接查詢用戶記錄，不依賴頻道名稱
        async with Session() as session:
            stmt = select(VerifyUser).where(
                VerifyUser.user_id == str(member.id),
                VerifyUser.is_active == True
            )
            result = await session.execute(stmt)
            records = result.scalars().all()
            
            for record in records:
                # 停用所有該用戶的活躍記錄
                await deactivate_verified_user(str(member.id), record.verify_group_id)
                logging.info(f"用戶 {member.name} 已從驗證中停用")
    except Exception as e:
        logging.error(f"處理用戶退出事件時發生錯誤: {e}")

# @bot.command()
# async def verify(ctx, uid: str = None):
#     try:
#         # 立即刪除用戶的命令消息，避免其他人看到
#         try:
#             await ctx.message.delete()
#         except Exception as e:
#             logging.error(f"Error deleting message: {e}")
            
#         verify_channel_id = await bot.channel_manager.get_channel_id(ctx.guild, "verify")
#         if not verify_channel_id:
#             await ctx.send(f"{ctx.author.mention}, the 'verify' channel was not found in this server. Please contact an admin.", ephemeral=True)
#             return

#         if ctx.channel.id != verify_channel_id:
#             await ctx.send(f"{ctx.author.mention}, you can only use this command in the designated <#{verify_channel_id}> channel.", ephemeral=True)
#             return

#         if not uid:
#             await ctx.send(f"{ctx.author.mention}, Please provide verification code, for example: !verify 123456", ephemeral=True)
#             return

#         role = discord.utils.get(ctx.author.roles, name="BYDFi Signal")
#         if role:
#             await ctx.send(f"{ctx.author.mention}, You have already been verified.", ephemeral=True)
#             return

#         verification_status = await is_user_verified(ctx.author.id, ctx.channel.id, uid)
#         if verification_status == "verified":
#             await ctx.send(f"{ctx.author.mention}, You have already been verified.", ephemeral=True)
#             return

#         if verification_status == "warning":
#             await ctx.send(f"{ctx.author.mention}, this UID has already been verified", ephemeral=True)
#             return
#         elif verification_status == "not_verified" or verification_status == "reverified":
#             payload = {
#                 "code": uid,
#                 "verifyGroup": ctx.channel.id,
#                 "brand": "BYD",
#                 "type": "DISCORD"
#             }

#             async with aiohttp.ClientSession() as session:
#                 async with session.post(VERIFY_API, data=payload) as response:
#                     data = await response.json()
#                     logging.info(data)
#                     api_message = data.get("data", "Verification failed. Please try again.")

#                     admin_mention = ctx.guild.owner.mention if ctx.guild.owner else "@admin"
#                     api_message = api_message.replace("@{admin}", admin_mention)
#                     api_message = api_message.replace("<a>", "").replace("</a>", "")

#                     if response.status == 200 and "verification successful" in api_message:
#                         api_message = api_message.replace("@{username}", "").replace("{Approval Link}", "").strip()
#                         bot.verified_users[ctx.author.id] = uid

#                         role = discord.utils.get(ctx.guild.roles, name="BYDFi Signal")
#                         if role:
#                             await ctx.author.add_roles(role)
#                             await ctx.send(f"{ctx.author.mention}, {api_message}", ephemeral=True)
#                             await add_verified_user(ctx.author.id, ctx.channel.id, uid)
#                         else:
#                             await ctx.send(f"{ctx.author.mention}, verification successful, but 'verified' role not found.", ephemeral=True)
#                     else:
#                         await ctx.send(f"{ctx.author.mention}, {api_message}", ephemeral=True)
#         else:
#             await ctx.send(f"{ctx.author.mention}, {verification_status} UID.", ephemeral=True)

#     except Exception as e:
#         logging.error(f"Error in verification command: {e}")
#         await ctx.send(f"An error occurred during verification. Please try again later.", ephemeral=True)

# @tasks.loop(minutes=1)
# async def fetch_unpublished_messages():
#     """定時檢查未發布文章，並根據 topic_name 發送到對應頻道。"""
#     try:
#         async with aiohttp.ClientSession() as session:
#             publisher = MessagePublisher(bot, session)

#             async with session.get(MESSAGE_API_URL) as response:
#                 if response.status != 200:
#                     logging.error("Failed to fetch unpublished messages")
#                     return

#                 message_data = await response.json()
#                 articles = message_data.get("data", {}).get("items", [])

#                 if not articles:
#                     return

#             await publisher.refresh_social_mapping()

#             for article in articles:
#                 try:
#                     topic_name = article.get("topic_name", "").strip()
#                     channel_ids = publisher.topic_to_channel_map.get(topic_name)

#                     if not channel_ids:
#                         logging.warning(f"No matching channels found for topic: {topic_name}")
#                         continue

#                     for channel_id in channel_ids:  # 遍歷所有符合的頻道
#                         channel = bot.get_channel(channel_id)
#                         if not channel:
#                             logging.warning(f"無法訪問頻道 ID {channel_id}，可能是機器人已被踢出伺服器或頻道已刪除")
#                             continue

#                         guild_name = channel.guild.name if channel.guild else "Unknown Guild"
#                         channel_name = channel.name if channel else "Unknown Channel"
                        
#                         # 詳細檢查權限
#                         permissions = channel.permissions_for(channel.guild.me)
#                         if not permissions.send_messages:
#                             logging.warning(f"權限不足: 機器人在 {guild_name}/{channel_name} (ID: {channel_id}) 沒有發送消息的權限")
#                             continue

#                         temp_file_path = await publisher.handle_image(
#                             article.get("image"),
#                             article.get("id")
#                         )

#                         content = article.get("content", "No Content")
#                         if temp_file_path:
#                             with open(temp_file_path, "rb") as image_file:
#                                 await channel.send(
#                                     content=content,
#                                     file=discord.File(image_file)
#                                 )
#                             os.remove(temp_file_path)
#                         else:
#                             await channel.send(content=content)

#                         await publisher.mark_as_published(article.get("id"))
#                         logging.info(f"Successfully sent article {article.get('id')} to channel {channel_id}")

#                 except Exception as e:
#                     logging.error(f"Error processing article {article.get('id')}: {e}")
#                     continue

#     except Exception as e:
#         logging.error(f"Error in fetch_unpublished_messages: {e}")

@tasks.loop(minutes=1)
async def fetch_unpublished_messages():
    """定時檢查未發布文章，並根據 topic_name 發送到對應頻道。"""
    try:
        async with aiohttp.ClientSession() as session:
            publisher = MessagePublisher(bot, session)

            # 獲取未發布的文章
            async with session.get(MESSAGE_API_URL) as response:
                if response.status != 200:
                    logging.error(f"獲取未發布文章失敗: {response.status}")
                    return

                message_data = await response.json()
                articles = message_data.get("data", {}).get("items", [])

                if not articles:
                    return  # 沒有未發布的文章

            # 更新頻道映射
            await publisher.refresh_social_mapping()

            # 處理每篇文章
            for article in articles:
                article_id = article.get("id")
                topic_name = article.get("topic_name", "").strip()
                logging.info(f"處理文章 ID: {article_id}, 主題: {topic_name}")
                # 獲取與該主題匹配的頻道列表（含 lang）
                channel_lang_list = publisher.topic_to_channel_map.get(topic_name)
                if not channel_lang_list:
                    logging.warning(f"未找到與主題 '{topic_name}' 匹配的頻道，跳過文章 {article_id}")
                    continue
                successful_sends = 0
                temp_file_path = None
                if article.get("image"):
                    try:
                        temp_file_path = await publisher.handle_image(article.get("image"), article_id)
                    except Exception as e:
                        logging.error(f"下載文章 {article_id} 的圖片時出錯: {e}")
                for channel_info in channel_lang_list:
                    channel_id = channel_info["channel_id"]
                    lang = channel_info.get("lang", "en")
                    try:
                        channel = bot.get_channel(int(channel_id))
                        if not channel:
                            logging.warning(f"找不到頻道 ID {channel_id}，可能已被刪除或機器人已被踢出")
                            continue
                        guild_name = channel.guild.name if channel.guild else "Unknown"
                        permissions = channel.permissions_for(channel.guild.me)
                        if not permissions.send_messages:
                            logging.warning(f"在伺服器 '{guild_name}' 的頻道 '{channel.name}' (ID: {channel_id}) 中沒有發送消息的權限")
                            continue
                        # 多語言文案
                        content = get_multilingual_content(article, lang)
                        if temp_file_path and permissions.attach_files:
                            with open(temp_file_path, "rb") as image_file:
                                await channel.send(content=content, file=discord.File(image_file))
                        else:
                            await channel.send(content=content)
                        successful_sends += 1
                        logging.info(f"成功發送文章 {article_id} 到伺服器 '{guild_name}' 的頻道 '{channel.name}' (ID: {channel_id})，語言: {lang}")
                    except discord.Forbidden as e:
                        logging.error(f"權限錯誤: 無法在頻道 {channel_id} 中發送消息: {e}")
                    except Exception as e:
                        logging.error(f"向頻道 {channel_id} 發送文章 {article_id} 時出錯: {type(e).__name__} - {e}")
                if temp_file_path and os.path.exists(temp_file_path):
                    try:
                        os.remove(temp_file_path)
                    except:
                        pass
                if successful_sends > 0:
                    await publisher.mark_as_published(article_id)
                    logging.info(f"文章 {article_id} 已被標記為已發布，成功發送到 {successful_sends} 個頻道")
                else:
                    logging.warning(f"文章 {article_id} 未成功發送到任何頻道，不標記為已發布")
    except Exception as e:
        logging.error(f"fetch_unpublished_messages 任務中發生未處理的錯誤: {type(e).__name__} - {e}")

@bot.command(name="checkpermissions")
async def check_permissions(ctx):
    """檢查機器人在所有頻道的權限狀態"""
    # 檢查執行命令的人是否有管理員權限
    if not ctx.author.guild_permissions.administrator:
        await safe_dm(ctx, "Only administrators can execute this command")
        return
    
    await safe_dm(ctx, "Checking robot permissions, please wait...")
    
    permission_report = []
    
    for channel in ctx.guild.text_channels:
        permissions = channel.permissions_for(ctx.guild.me)
        status = "✅" if permissions.send_messages else "❌"
        attach_status = "✅" if permissions.attach_files else "❌"
        
        permission_report.append(
            f"{status} #{channel.name} - 發送消息: {permissions.send_messages}, "
            f"附加文件: {attach_status}"
        )
    
    # 分批發送報告（避免超過 Discord 的消息長度限制）
    report_chunks = [permission_report[i:i+20] for i in range(0, len(permission_report), 20)]
    
    for chunk in report_chunks:
        await safe_dm(ctx, "\n".join(chunk))

@bot.event
async def on_command_error(ctx, error):
    """全局錯誤處理器"""
    if isinstance(error, CommandNotFound):
        # 靜默忽略 CommandNotFound 錯誤
        return

    # 處理其他錯誤
    await safe_dm(ctx, f"⚠️ An error occurred: {error}")
    logging.error(f"Error occurred: {error}")

@fetch_unpublished_messages.before_loop
async def before_fetch_unpublished_messages():
    await bot.wait_until_ready()

# Event handlers for channel updates
@bot.event
async def on_guild_channel_delete(channel):
    """Invalidate cache when a channel is deleted"""
    bot.channel_manager.invalidate_cache(channel.guild.id)

@bot.event
async def on_guild_channel_create(channel):
    """Invalidate cache when a channel is created"""
    bot.channel_manager.invalidate_cache(channel.guild.id)

@bot.event
async def on_guild_channel_update(before, after):
    """Invalidate cache when a channel is updated"""
    bot.channel_manager.invalidate_cache(before.guild.id)

# FastAPI endpoints
@app.get("/api/discord/members")
async def get_members(id: Union[int, None] = Query(default=None, description="Discord Guild ID")):
    """
    統一的成員查詢接口

    參數:
        - guild_id: 可選，特定伺服器的ID。如果不提供，則返回所有伺服器的資料

    使用方式:
        - 查詢特定伺服器: /api/discord/members?guild_id=123456789
        - 查詢所有伺服器: /api/discord/members
    """
    if id is not None:
        # 返回特定伺服器的資料
        return bot.get_guild_member_count(id)

    # 返回所有伺服器的資料
    guilds_data = []
    for guild in bot.guilds:
        guild_data = bot.get_guild_member_count(guild.id)
        if guild_data["success"]:
            guilds_data.append(guild_data["data"])

    return {
        "success": True,
        "message": "successful",
        "data": guilds_data
    }

def html_to_discord_markdown(text):
    # 粗體
    text = re.sub(r'<b>(.*?)</b>', r'**\1**', text, flags=re.IGNORECASE)
    # 斜體
    text = re.sub(r'<i>(.*?)</i>', r'*\1*', text, flags=re.IGNORECASE)
    # 底線
    text = re.sub(r'<u>(.*?)</u>', r'__\1__', text, flags=re.IGNORECASE)
    return text

@app.post("/api/discord/announcement")
async def send_announcement_to_discord(request: Request):
    payload = await request.json()
    content = payload.get("content")
    image = payload.get("image")  # 可為 None

    if not content:
        return {"status": "error", "message": "Missing content"}

    # 自動將 HTML 轉為 Discord Markdown
    content = html_to_discord_markdown(content)

    try:
        # 使用 asyncio.run_coroutine_threadsafe 來在 Discord 的事件循環中執行任務
        async def send_announcement_task():
            async with aiohttp.ClientSession() as session:
                # 獲取社交數據
                payload = {
                    "brand": "BYD",
                    "type": "DISCORD"
                }
                async with session.post(SOCIAL_API, data=payload) as response:
                    if response.status != 200:
                        raise Exception("Failed to fetch social data")
                    social_data = await response.json()

                social_groups = social_data.get("data", [])

                # 獲取 Announcements 頻道
                channel_ids = []
                for group in social_groups:
                    for chat in group.get("chats", []):
                        if chat.get("enable", False) and chat["name"] == "Announcements":
                            channel_ids.append(int(chat["chatId"]))

                if not channel_ids:
                    raise Exception("No Discord channels with topic 'Announcements' found")

                # 下載圖片（如果需要）
                image_bytes = None
                if image:
                    async with session.get(image) as img_resp:
                        if img_resp.status == 200:
                            image_bytes = await img_resp.read()
                        else:
                            logging.warning(f"[DC] 圖片下載失敗，狀態碼: {img_resp.status}")

                # 發送到所有頻道
                for channel_id in channel_ids:
                    channel = bot.get_channel(channel_id)
                    if not channel:
                        continue

                    if image_bytes:
                        file = discord.File(fp=io.BytesIO(image_bytes), filename="announcement.jpg")
                        await channel.send(content=content, file=file)
                    else:
                        await channel.send(content=content)

        # 使用 run_coroutine_threadsafe 在 Discord 的事件循環中執行
        asyncio.run_coroutine_threadsafe(send_announcement_task(), bot.loop)

        return {"status": "success", "message": "Announcement sent to Discord"}

    except Exception as e:
        logging.error(f"[DC] 發送公告失敗: {e}")
        return {"status": "error", "message": str(e)}

# @app.post("/api/discord/announcement")
# async def send_announcement_to_discord(request: Request, background_tasks: BackgroundTasks):
#     payload = await request.json()
#     content = payload.get("content")
#     image = payload.get("image")  # 可為 None

#     if not content:
#         return {"status": "error", "message": "Missing content"}

#     background_tasks.add_task(dispatch_discord_announcement, content, image)
#     return {"status": "success", "message": "Dispatching to Discord"}

# async def dispatch_discord_announcement(content: str, image: str = None, max_retries: int = 3):
#     for attempt in range(max_retries):
#         try:
#             # 檢查頻道是否存在
#             channel = bot.get_channel(1326740046409371729)  # 測試頻道
#             if not channel:
#                 logging.error("[DC] 找不到頻道 ID: 1326740046409371729")
#                 return
            
#             logging.info(f"[DC] 找到頻道: {channel.name} (ID: {channel.id})")
            
#             # 檢查機器人權限
#             bot_member = channel.guild.get_member(bot.user.id)
#             if not bot_member:
#                 logging.error("[DC] 無法獲取機器人在伺服器中的成員信息")
#                 return
            
#             permissions = channel.permissions_for(bot_member)
#             if not permissions.send_messages:
#                 logging.error(f"[DC] 機器人在頻道 {channel.name} 中沒有發送消息的權限")
#                 return
            
#             if image and not permissions.attach_files:
#                 logging.warning(f"[DC] 機器人在頻道 {channel.name} 中沒有附加文件的權限，將只發送文字")
#                 image = None
            
#             logging.info(f"[DC] 權限檢查通過，準備發送消息")
            
#             # 使用 discord.http.session 而不是創建新的 aiohttp session
#             if image:
#                 try:
#                     # 使用 Discord 的內建 session
#                     async with bot.http.session.get(image) as img_resp:
#                         if img_resp.status == 200:
#                             image_bytes = await img_resp.read()
#                             file = discord.File(fp=io.BytesIO(image_bytes), filename="announcement.jpg")
#                             await channel.send(content=content, file=file)
#                             logging.info("[DC] 發送成功（含圖片）")
#                             return
#                         else:
#                             logging.warning(f"[DC] 圖片下載失敗，狀態碼: {img_resp.status}")
#                             await channel.send(content=content + "\n[Image failed to load]")
#                             logging.info("[DC] 發送成功（圖片下載失敗）")
#                             return
#                 except Exception as img_error:
#                     logging.error(f"[DC] 圖片處理錯誤: {img_error}")
#                     await channel.send(content=content + "\n[Image processing failed]")
#                     logging.info("[DC] 發送成功（圖片處理失敗）")
#                     return
#             else:
#                 await channel.send(content=content)
#                 logging.info("[DC] 發送成功（純文字）")
#                 return

#         except Exception as e:
#             if attempt == max_retries - 1:  # 最後一次嘗試
#                 logging.error(f"[DC] 第 {attempt + 1} 次嘗試失敗，放棄發送: {e}")
#             else:
#                 logging.warning(f"[DC] 第 {attempt + 1} 次嘗試失敗，將重試: {e}")
#                 await asyncio.sleep(2 ** attempt)  # 指數退避

async def safe_dm(ctx, content: str):
    """嘗試對用戶發送私訊；若失敗，改為在頻道提示並於 30 秒後自動刪除。"""
    try:
        await ctx.author.send(content)
    except discord.Forbidden:
        # 用戶關閉 DM 或封鎖機器人，改在頻道提示
        await ctx.send(f"{ctx.author.mention} ⚠️ Please enable your Direct Messages (DM) and try again.", delete_after=30)

def run_api():
    """Run the FastAPI server"""
    # uvicorn.run(app, host="172.31.91.89", port=5011)
    uvicorn.run(app, host="0.0.0.0", port=5011)
    # uvicorn.run(app, host="172.25.183.177", port=5011)

# 在主函數中啟動 API 服務
if __name__ == "__main__":
    # 在新線程中啟動 API 服務
    api_thread = Thread(target=run_api, daemon=True)
    api_thread.start()

    # 運行 Discord bot
    bot.run(TOKEN)