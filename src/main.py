import io
import re
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
from handlers.copy_signal_handler import handle_send_copy_signal
from handlers.trade_summary_handler import handle_send_trade_summary
from handlers.scalp_update_handler import handle_send_scalp_update
from handlers.holding_report_handler import handle_holding_report
from handlers.weekly_report_handler import handle_weekly_report
from multilingual_utils import get_multilingual_content, AI_TRANSLATE_HINT, LANGUAGE_CODE_MAPPING

logging.basicConfig(level=logging.INFO)

load_dotenv()
app = FastAPI()

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
                # lang 在 group 層級，不是 chat 層級
                group_lang = group.get("lang", "en_US")
                for chat in group.get("chats", []):
                    if chat.get("enable", False):
                        topic = chat["name"]
                        channel_id = int(chat["chatId"])
                        if topic not in self.topic_to_channel_map:
                            self.topic_to_channel_map[topic] = []
                        
                        # 檢查是否已經存在相同的 channel_id，避免重複添加
                        existing_channels = [ch["channel_id"] for ch in self.topic_to_channel_map[topic]]
                        if channel_id not in existing_channels:
                            self.topic_to_channel_map[topic].append({"channel_id": channel_id, "lang": group_lang})
                        else:
                            logging.warning(f"主題 '{topic}' 中已存在頻道 ID {channel_id}，跳過重複添加")

    async def handle_image(self, image_url, article_id):
        """Handle image download and return file path"""
        if not image_url:
            return None

        if not image_url.startswith("http"):
            # image_url = f"https://sp.signalcms.com{image_url}"
            image_url = f"http://172.25.183.139:5003{image_url}"
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

                # 添加調試信息，查看 API 返回的原始數據格式
                if articles:
                    first_article = articles[0]
                    logging.info(f"API 返回的第一篇文章結構: {list(first_article.keys())}")
                    if 'content' in first_article:
                        content = first_article['content']
                        logging.info(f"第一篇文章內容類型: {type(content)}")
                        logging.info(f"第一篇文章內容長度: {len(content) if content else 0}")
                        if content:
                            logging.info(f"第一篇文章內容中的換行符: {content.count(chr(10))}")
                            logging.info(f"第一篇文章內容前200字符: {repr(content[:200])}")

                if not articles:
                    return

            await publisher.refresh_social_mapping()
            
            # 添加調試信息，檢查頻道映射
            for topic, channels in publisher.topic_to_channel_map.items():
                logging.info(f"主題 '{topic}' 的頻道配置: {channels}")

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
                
                # 添加調試信息
                logging.info(f"文章 {article_id} 的內容結構: content={article.get('content') is not None}, translations={article.get('translations') is not None}")
                logging.info(f"文章 {article_id} 的頻道列表: {channel_lang_list}")
                successful_sends = 0
                temp_file_path = None
                if article.get("image"):
                    try:
                        temp_file_path = await publisher.handle_image(article.get("image"), article_id)
                    except Exception as e:
                        logging.error(f"下載文章 {article_id} 的圖片時出錯: {e}")
                for channel_info in channel_lang_list:
                    channel_id = channel_info["channel_id"]
                    lang = channel_info.get("lang", "en_US")
                    logging.info(f"準備發送文章 {article_id} 到頻道 {channel_id}，語言: {lang}")
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
                        try:
                            # 添加調試信息，查看原始內容的換行符
                            raw_content = article.get('content', '')
                            if raw_content:
                                logging.info(f"處理文章 {article_id} 的語言 {lang}，原始內容長度: {len(raw_content)}")
                                logging.info(f"原始內容中的換行符數量: {raw_content.count(chr(10))}")
                                logging.info(f"原始內容前100字符: {repr(raw_content[:100])}")
                            
                            content = get_multilingual_content(article, lang)
                            if not content:
                                logging.warning(f"文章 {article_id} 的內容為空，跳過發送")
                                continue
                            logging.info(f"文章 {article_id} 處理完成，內容長度: {len(content)}")
                            logging.info(f"處理後內容中的換行符數量: {content.count(chr(10))}")
                            logging.info(f"處理後內容前100字符: {repr(content[:100])}")
                        except Exception as e:
                            logging.error(f"處理文章 {article_id} 的多語言內容時出錯: {type(e).__name__} - {e}")
                            logging.error(f"文章 {article_id} 的詳細資料: content={article.get('content')}, translations={article.get('translations')}")
                            # 使用原始內容作為備用
                            content = article.get("content", "No content available")
                        
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
    text = re.sub(r'<b>(.*?)</b>', r'**\1**', text, flags=re.IGNORECASE)
    text = re.sub(r'<i>(.*?)</i>', r'*\1*', text, flags=re.IGNORECASE)
    text = re.sub(r'<u>(.*?)</u>', r'__\1__', text, flags=re.IGNORECASE)
    return text

@app.post("/api/discord/announcement")
async def send_announcement_to_discord(request: Request):
    payload = await request.json()
    content = payload.get("content")
    image = payload.get("image")

    logging.info(f"[DC] 收到公告請求: content_type={type(content)}, image={image}")
    logging.info(f"[DC] 接收到的 payload: {payload}")

    if not content:
        logging.error("[DC] 缺少 content 參數")
        return {"status": "error", "message": "Missing content"}

    # 解析多語言內容（參考 TG bot 的處理方式）
    try:
        if isinstance(content, str):
            # 如果是字符串，嘗試解析為JSON
            import json
            content_dict = json.loads(content)
            logging.info(f"[DC] 成功解析字符串為 JSON: {type(content_dict)}")
        else:
            # 如果已經是字典，直接使用
            content_dict = content
            logging.info(f"[DC] 使用字典格式內容: {type(content_dict)}")
    except (json.JSONDecodeError, TypeError) as e:
        logging.error(f"[DC] 內容格式錯誤: {e}")
        return {"status": "error", "message": "Invalid content format. Expected JSON object with language codes as keys."}

    try:
        async def send_announcement_task():
            logging.info("[DC] 開始執行公告發送任務")
            async with aiohttp.ClientSession() as session:
                payload = {
                    "brand": "BYD",
                    "type": "DISCORD"
                }
                logging.info(f"[DC] 呼叫 SOCIAL_API: {SOCIAL_API}")
                async with session.post(SOCIAL_API, data=payload) as response:
                    logging.info(f"[DC] SOCIAL_API 響應狀態: {response.status}")
                    if response.status != 200:
                        raise Exception("Failed to fetch social data")
                    social_data = await response.json()
                    logging.info(f"[DC] SOCIAL_API 響應數據: {social_data}")

                social_groups = social_data.get("data", [])
                logging.info(f"[DC] 找到 {len(social_groups)} 個社交群組")

                # 獲取 Announcements 頻道及其對應的語言
                channel_lang_mapping = []
                for group in social_groups:
                    group_lang = group.get("lang", "en_US")  # 默認語言為 en_US
                    if not group_lang:
                        group_lang = "en_US"
                    
                    logging.info(f"[DC] 處理群組: uid={group.get('uid')}, lang={group_lang}")
                    
                    for chat in group.get("chats", []):
                        if chat.get("enable", False) and chat["name"] == "Announcements":
                            channel_info = {
                                "channel_id": int(chat["chatId"]),
                                "lang": group_lang
                            }
                            channel_lang_mapping.append(channel_info)
                            logging.info(f"[DC] 找到 Announcements 頻道: {channel_info}")

                logging.info(f"[DC] 總共找到 {len(channel_lang_mapping)} 個 Announcements 頻道")
                if not channel_lang_mapping:
                    raise Exception("No Discord channels with topic 'Announcements' found")

                # 下載圖片（如果需要）
                image_bytes = None
                if image:
                    logging.info(f"[DC] 開始下載圖片: {image}")
                    async with session.get(image) as img_resp:
                        logging.info(f"[DC] 圖片下載響應狀態: {img_resp.status}")
                        if img_resp.status == 200:
                            image_bytes = await img_resp.read()
                            logging.info(f"[DC] 圖片下載成功，大小: {len(image_bytes)} bytes")
                        else:
                            logging.warning(f"[DC] 圖片下載失敗，狀態碼: {img_resp.status}")

                # 發送到所有頻道，根據語言匹配對應的文案
                logging.info(f"[DC] 開始發送公告到 {len(channel_lang_mapping)} 個頻道")
                success_count = 0
                failed_count = 0
                
                for i, channel_info in enumerate(channel_lang_mapping, 1):
                    channel_id = channel_info["channel_id"]
                    lang = channel_info["lang"]
                    
                    logging.info(f"[DC] 處理第 {i}/{len(channel_lang_mapping)} 個頻道: {channel_id}, 語言: {lang}")
                    
                    channel = bot.get_channel(channel_id)
                    if not channel:
                        logging.warning(f"[DC] 找不到頻道 {channel_id}")
                        continue

                    # 根據語言獲取對應的文案
                    channel_content = content_dict.get(lang)
                    if not channel_content:
                        logging.warning(f"[DC] 找不到語言 {lang} 的文案，跳過頻道 {channel_id}")
                        continue
                    logging.info(f"[DC] 找到語言 {lang} 的文案，長度: {len(channel_content)}")

                    # 轉換 HTML 到 Discord Markdown
                    channel_content = html_to_discord_markdown(channel_content)
                    logging.info(f"[DC] HTML 轉換後文案長度: {len(channel_content)}")
                    
                    # 在文案最後加上對應語言的 AI 提示詞（除了英文）
                    if lang != "en_US":
                        # 使用映射後的語言代碼獲取 AI 提示詞
                        api_lang_code = LANGUAGE_CODE_MAPPING.get(lang, lang)
                        ai_hint = AI_TRANSLATE_HINT.get(api_lang_code, AI_TRANSLATE_HINT["en_US"])
                        channel_content += ai_hint
                        logging.info(f"[DC] 添加 AI 提示詞: {api_lang_code}")

                    try:
                        if image_bytes:
                            file = discord.File(fp=io.BytesIO(image_bytes), filename="announcement.jpg")
                            logging.info(f"[DC] 發送帶圖片的公告到頻道 {channel_id}")
                            await asyncio.wait_for(
                                channel.send(content=channel_content, file=file),
                                timeout=15.0
                            )
                        else:
                            logging.info(f"[DC] 發送純文字公告到頻道 {channel_id}")
                            await asyncio.wait_for(
                                channel.send(content=channel_content),
                                timeout=15.0
                            )
                        logging.info(f"[DC] 成功發送到頻道 {channel_id}")
                        success_count += 1
                    except asyncio.TimeoutError:
                        logging.error(f"[DC] 發送到頻道 {channel_id} 超時")
                        failed_count += 1
                        continue
                    except Exception as e:
                        logging.error(f"[DC] 發送到頻道 {channel_id} 失敗: {e}")
                        failed_count += 1
                        continue

                # 統計發送結果
                logging.info(f"[DC] 公告發送完成: 成功 {success_count}/{len(channel_lang_mapping)} 個頻道")

        # 使用 run_coroutine_threadsafe 在 Discord 的事件循環中執行
        logging.info("[DC] 準備在 Discord 事件循環中執行發送任務")
        asyncio.run_coroutine_threadsafe(send_announcement_task(), bot.loop)

        return {"status": "success", "message": "Announcement sent to Discord"}

    except Exception as e:
        logging.error(f"[DC] 發送公告失敗: {e}")
        import traceback
        logging.error(f"[DC] 詳細錯誤: {traceback.format_exc()}")
        return {"status": "error", "message": str(e)}

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