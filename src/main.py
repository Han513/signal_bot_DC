# import os
# import discord
# import logging
# import aiohttp
# import aiofiles
# from discord.ext import commands, tasks
# from dotenv import load_dotenv
# from db_handler_aio import *

# logging.basicConfig(level=logging.INFO)

# # Bot token and intents
# load_dotenv()
# TOKEN = os.getenv("Discord_TOKEN")
# intents = discord.Intents.default()
# intents.members = True  # 追蹤成員加入事件
# intents.message_content = True  # 啟用 Message Content Intent

# bot = commands.Bot(command_prefix="!", intents=intents)

# # 已驗證的使用者資料
# verified_users = {}

# WELCOME_API = os.getenv("WELCOME_API")
# VERIFY_API = os.getenv("VERIFY_API")
# DETAIL_API = os.getenv("DETAIL_API")
# SOCIAL_API = os.getenv("SOCIAL_API")

# MESSAGE_API_URL = os.getenv("MESSAGE_API_URL")
# UPDATE_MESSAGE_API_URL = os.getenv("UPDATE_MESSAGE_API_URL")

# @bot.event
# async def on_ready():
#     print(f"Bot is ready. Logged in as {bot.user}")
#     fetch_unpublished_messages.start()  # 啟動定時任務

# # @bot.event
# # async def on_member_join(member):
# #     """監聽新用戶加入事件，調用歡迎語 API 並在 welcome 頻道發送圖片和文字。"""
# #     try:
# #         payload = {"verifyGroup": str(member.guild.id), "brand": "BYD"}
# #         welcome_channel = member.guild.get_channel(WELCOME_CHANNEL_ID)  # welcome 頻道 ID

# #         # 圖片路徑
# #         current_dir = os.path.dirname(os.path.abspath(__file__))
# #         image_path = os.path.join(current_dir, "..", "pics", "FindUID.jpg")

# #         if welcome_channel:
# #             with open(image_path, "rb") as image:
# #                 file = discord.File(image, filename="FindUID.jpg")
# #                 await welcome_channel.send(
# #                     content=f"{member.mention}, Welcome!",  # 發送文字內容
# #                     file=file  # 附加圖片
# #                 )
# #         else:
# #             print("指定的 welcome 頻道未找到，無法發送歡迎消息。")
# #     except Exception as e:
# #         logging.error(f"Error handling member join: {e}")

# @bot.event
# async def on_member_join(member):
#     """當新用戶加入伺服器時，自動檢測伺服器的 verify 頻道，並發送歡迎消息。"""
#     try:
#         # 找出名稱為 "verify" 的頻道
#         verify_channel = discord.utils.get(member.guild.text_channels, name="verify")

#         if not verify_channel:
#             logging.warning(f"未找到伺服器 {member.guild.name} 的 verify 頻道。")
#             return

#         verify_group_id = verify_channel.id

#         # 調用歡迎語 API
#         payload = {
#             "verifyGroup": str(verify_group_id),
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

#         # 移除 @{username} 字段，調整 Dear 的位置
#         welcome_message = welcome_message.replace("📣 Dear @{username}", "")
#         welcome_message = welcome_message.replace("<a>", "").replace("</a>", "")
#         welcome_message = f"📣 Dear {member.mention}{welcome_message}".strip()

#         # 圖片路徑
#         current_dir = os.path.dirname(os.path.abspath(__file__))
#         image_path = os.path.join(current_dir, "..", "pics", "FindUID.jpg")

#         # 發送歡迎消息和圖片
#         welcome_channel = discord.utils.get(member.guild.text_channels, name="welcome")
#         if welcome_channel:
#             with open(image_path, "rb") as image:
#                 file = discord.File(image, filename="FindUID.jpg")
#                 await welcome_channel.send(content=welcome_message, file=file, allowed_mentions=discord.AllowedMentions(users=True))

#         logging.info(f"成功發送歡迎消息到 {member.guild.name} 的頻道 {welcome_channel.name}。")

#     except Exception as e:
#         logging.error(f"處理新成員加入事件時發生錯誤: {e}")

# @bot.command()
# async def verify(ctx, uid: str = None):
#     """處理驗證指令，並限定在指定頻道中執行。"""
#     try:
#         verify_channel = discord.utils.get(ctx.guild.text_channels, name="verify")

#         # 如果找不到 verify 频道，向用户返回错误提示
#         if not verify_channel:
#             await ctx.send(f"{ctx.author.mention}, the 'verify' channel was not found in this server. Please contact an admin.")
#             return

#         # 检查指令是否在 verify 频道中执行
#         if ctx.channel.id != verify_channel.id:
#             await ctx.send(f"{ctx.author.mention}, you can only use this command in the designated {verify_channel.mention} channel.")
#             return

#         # 檢查是否提供了 UID
#         if not uid:
#             await ctx.send(f"{ctx.author.mention}, Please provide verification code, for example: !verify 123456")
#             return

#         # 使用 is_user_verified 函数检查 UID 是否已被验证
#         verification_status = await is_user_verified(ctx.author.id, ctx.channel.id, uid)
#         print(verification_status)

#         if verification_status == "warning":
#             await ctx.send(f"{ctx.author.mention}, this UID has already been verified")
#             return

#         elif verification_status == "not_verified":
#             # 如果 UID 没有被验证过，可以继续后续的验证流程
#             payload = {
#                 "code": uid,
#                 "verifyGroup": ctx.channel.id,  # 使用當前頻道的 ID
#                 "brand": "BYD",
#                 "type": "DISCORD"
#             }

#             async with aiohttp.ClientSession() as session:
#                 async with session.post(VERIFY_API, data=payload) as response:
#                     data = await response.json()
#                     print(data)

#                     api_message = data.get("data", "Verification failed. Please try again.")

#                     admins = [member async for member in ctx.guild.fetch_members()]
#                     admin_user = next(
#                         (member for member in admins if member.guild_permissions.administrator),
#                         None
#                     )

#                     admin_mention = admin_user.mention if admin_user else "the admin team"
#                     api_message = api_message.replace("@{admin}", admin_mention)
#                     api_message = api_message.replace("<a>", "").replace("</a>", "")

#                     if response.status == 200 and "verification successful" in api_message:
#                         api_message = api_message.replace("@{username}", "").replace("{Approval Link}", "").strip()
#                         verified_users[ctx.author.id] = uid

#                         role = discord.utils.get(ctx.guild.roles, name="verified")
#                         if role:
#                             await ctx.author.add_roles(role)
#                             await ctx.send(f"{ctx.author.mention}, {api_message}")

#                             # 添加已验证用户到数据库
#                             await add_verified_user(ctx.author.id, ctx.channel.id, uid)
#                         else:
#                             await ctx.send(f"{ctx.author.mention}, verification successful, but 'verified' role not found.")
#                     else:
#                         # 處理驗證失敗的情况
#                         await ctx.send(f"{ctx.author.mention}, {api_message}")

#         else:
#             # 如果查询到 UID 已被验证过
#             await ctx.send(f"{ctx.author.mention}, {verification_status} UID.")

#     except Exception as e:
#         logging.error(f"Error in verification command: {e}")

# @tasks.loop(minutes=1)  # 每 1 分鐘執行一次
# async def fetch_unpublished_messages():
#     """定時檢查未發布文章，並根據 topic_name 發送到對應頻道。"""
#     try:
#         async with aiohttp.ClientSession() as session:
#             # 獲取未發布的文章
#             async with session.get(MESSAGE_API_URL) as message_response:
#                 if message_response.status != 200:
#                     logging.error("Failed to fetch unpublished messages.")
#                     return

#                 message_data = await message_response.json()
#                 articles = message_data.get("data", {}).get("items", [])

#                 if not articles:
#                     logging.info("No unpublished articles found.")
#                     return

#             # 獲取社群數據
#             payload = {
#                 "brand": "BYD",
#                 "type": "DISCORD"
#             }

#             async with session.post(SOCIAL_API, data=payload) as social_response:
#                 if social_response.status != 200:
#                     logging.error("Failed to fetch social data.")
#                     return

#                 social_data = await social_response.json()
#                 social_groups = social_data.get("data", [])

#             # 建立 topic_name 與 chatId 的對應表
#             topic_to_channel_map = {}
#             for group in social_groups:
#                 for chat in group.get("chats", []):
#                     if chat.get("enable", False):
#                         topic_to_channel_map[chat["name"]] = int(chat["chatId"])

#             # 發布文章到對應頻道
#             for article in articles:
#                 topic_name = article.get("topic_name")
#                 content = article.get("content", "No Content")
#                 image_url = article.get("image")
#                 article_id = article.get("id")

#                 # 找到對應的 Discord 頻道 ID
#                 normalized_topic = topic_name.strip()
#                 channel_id = topic_to_channel_map.get(normalized_topic)
#                 if not channel_id:
#                     logging.warning(f"No matching channel found for topic: {topic_name}")
#                     continue

#                 # 發送消息到對應的頻道
#                 channel = bot.get_channel(channel_id)
#                 if not channel:
#                     logging.warning(f"Channel with ID {channel_id} not found.")
#                     continue

#                 try:
#                     pics_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pics")
#                     os.makedirs(pics_dir, exist_ok=True)

#                     if image_url:
#                         # 確保圖片為完整 URL
#                         if not image_url.startswith("http"):
#                             image_url = f"http://127.0.0.1:5003{image_url}"

#                         # 下載圖片到 pics 資料夾
#                         temp_file_path = os.path.join(pics_dir, f"temp_image_{article_id}.jpg")
#                         async with session.get(image_url) as img_response:
#                             if img_response.status == 200:
#                                 async with aiofiles.open(temp_file_path, "wb") as f:
#                                     await f.write(await img_response.read())

#                                 # 發送圖片和文字
#                                 with open(temp_file_path, "rb") as image_file:
#                                     await channel.send(content=content, file=discord.File(image_file))

#                                 # 刪除臨時檔案
#                                 os.remove(temp_file_path)
#                             else:
#                                 logging.error(f"Failed to download image: {image_url}")
#                                 continue
#                     else:
#                         # 僅發送文字
#                         await channel.send(content=content)

#                     logging.info(f"Successfully sent article {article_id} to channel {channel_id}.")

#                     # 標記文章為已發布
#                     update_payload = {"id": article_id, "is_sent_dc": 1}
#                     async with session.post(UPDATE_MESSAGE_API_URL, json=update_payload) as update_response:
#                         if update_response.status == 200:
#                             logging.info(f"Article {article_id} marked as published.")
#                         else:
#                             logging.error(f"Failed to mark article {article_id} as published.")

#                 except Exception as e:
#                     logging.error(f"Failed to send article {article_id} to channel {channel_id}: {e}")

#     except Exception as e:
#         logging.error(f"Error fetching or sending unpublished articles: {e}")

# @fetch_unpublished_messages.before_loop
# async def before_fetch_unpublished_messages():
#     await bot.wait_until_ready()

# # 執行機器人
# bot.run(TOKEN)

import os
import discord
import logging
import aiohttp
import aiofiles
from discord.ext import commands, tasks
from dotenv import load_dotenv
from db_handler_aio import *
from typing import Dict, Optional
from functools import lru_cache

# Set up logging
logging.basicConfig(level=logging.INFO)

# Load environment variables
load_dotenv()

# API endpoints
WELCOME_API = os.getenv("WELCOME_API")
VERIFY_API = os.getenv("VERIFY_API")
DETAIL_API = os.getenv("DETAIL_API")
SOCIAL_API = os.getenv("SOCIAL_API")
MESSAGE_API_URL = os.getenv("MESSAGE_API_URL")
UPDATE_MESSAGE_API_URL = os.getenv("UPDATE_MESSAGE_API_URL")

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
        self.topic_to_channel_map = {}
        
    async def refresh_social_mapping(self):
        """Refresh the topic to channel mapping"""
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
                        self.topic_to_channel_map[chat["name"]] = int(chat["chatId"])
                        
    async def handle_image(self, image_url, article_id):
        """Handle image download and return file path"""
        if not image_url:
            return None
            
        if not image_url.startswith("http"):
            image_url = f"{os.getenv('API_BASE_URL_2')}{image_url}"
            
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
        """Mark article as published"""
        update_payload = {"id": article_id, "is_sent_dc": 1}
        async with self.session.post(UPDATE_MESSAGE_API_URL, json=update_payload) as response:
            if response.status != 200:
                raise ValueError(f"Failed to mark article {article_id} as published")
            logging.info(f"Article {article_id} marked as published")

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

# Bot initialization
TOKEN = os.getenv("Discord_TOKEN")
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = OptimizedBot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot is ready. Logged in as {bot.user}")
    fetch_unpublished_messages.start()

@bot.event
async def on_member_join(member):
    try:
        verify_channel_id = await bot.channel_manager.get_channel_id(member.guild, "verify")
        if not verify_channel_id:
            logging.warning(f"未找到伺服器 {member.guild.name} 的 verify 頻道。")
            return

        payload = {
            "verifyGroup": str(verify_channel_id),
            "brand": "BYD",
            "type": "DISCORD"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(WELCOME_API, data=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    welcome_message = data.get("data", "Welcome to the server!")
                else:
                    welcome_message = "Welcome to the server!"

        welcome_message = welcome_message.replace("📣 Dear @{username}", "")
        welcome_message = welcome_message.replace("<a>", "").replace("</a>", "")
        welcome_message = f"📣 Dear {member.mention}{welcome_message}".strip()

        welcome_channel_id = await bot.channel_manager.get_channel_id(member.guild, "welcome")
        if welcome_channel_id:
            welcome_channel = member.guild.get_channel(welcome_channel_id)
            current_dir = os.path.dirname(os.path.abspath(__file__))
            image_path = os.path.join(current_dir, "..", "pics", "FindUID.jpg")

            with open(image_path, "rb") as image:
                file = discord.File(image, filename="FindUID.jpg")
                await welcome_channel.send(
                    content=welcome_message,
                    file=file,
                    allowed_mentions=discord.AllowedMentions(users=True)
                )

    except Exception as e:
        logging.error(f"處理新成員加入事件時發生錯誤: {e}")

@bot.command()
async def verify(ctx, uid: str = None):
    try:
        verify_channel_id = await bot.channel_manager.get_channel_id(ctx.guild, "verify")
        if not verify_channel_id:
            await ctx.send(f"{ctx.author.mention}, the 'verify' channel was not found in this server. Please contact an admin.")
            return

        if ctx.channel.id != verify_channel_id:
            await ctx.send(f"{ctx.author.mention}, you can only use this command in the designated <#{verify_channel_id}> channel.")
            return

        if not uid:
            await ctx.send(f"{ctx.author.mention}, Please provide verification code, for example: !verify 123456")
            return

        verification_status = await is_user_verified(ctx.author.id, ctx.channel.id, uid)
        if verification_status == "warning":
            await ctx.send(f"{ctx.author.mention}, this UID has already been verified")
            return

        elif verification_status == "not_verified":
            payload = {
                "code": uid,
                "verifyGroup": ctx.channel.id,
                "brand": "BYD",
                "type": "DISCORD"
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(VERIFY_API, data=payload) as response:
                    data = await response.json()
                    api_message = data.get("data", "Verification failed. Please try again.")
                    
                    admin_mention = bot.get_admin_mention(ctx.guild.id)
                    api_message = api_message.replace("@{admin}", admin_mention)
                    api_message = api_message.replace("<a>", "").replace("</a>", "")

                    if response.status == 200 and "verification successful" in api_message:
                        api_message = api_message.replace("@{username}", "").replace("{Approval Link}", "").strip()
                        bot.verified_users[ctx.author.id] = uid

                        role = discord.utils.get(ctx.guild.roles, name="verified")
                        if role:
                            await ctx.author.add_roles(role)
                            await ctx.send(f"{ctx.author.mention}, {api_message}")
                            await add_verified_user(ctx.author.id, ctx.channel.id, uid)
                        else:
                            await ctx.send(f"{ctx.author.mention}, verification successful, but 'verified' role not found.")
                    else:
                        await ctx.send(f"{ctx.author.mention}, {api_message}")
        else:
            await ctx.send(f"{ctx.author.mention}, {verification_status} UID.")

    except Exception as e:
        logging.error(f"Error in verification command: {e}")

@tasks.loop(minutes=1)
async def fetch_unpublished_messages():
    """定時檢查未發布文章，並根據 topic_name 發送到對應頻道。"""
    try:
        async with aiohttp.ClientSession() as session:
            publisher = MessagePublisher(bot, session)
            
            async with session.get(MESSAGE_API_URL) as response:
                if response.status != 200:
                    logging.error("Failed to fetch unpublished messages")
                    return
                    
                message_data = await response.json()
                articles = message_data.get("data", {}).get("items", [])
                
                if not articles:
                    logging.info("No unpublished articles found")
                    return
                    
            await publisher.refresh_social_mapping()
            
            for article in articles:
                try:
                    topic_name = article.get("topic_name", "").strip()
                    channel_id = publisher.topic_to_channel_map.get(topic_name)
                    
                    if not channel_id:
                        logging.warning(f"No matching channel found for topic: {topic_name}")
                        continue
                        
                    channel = bot.get_channel(channel_id)
                    if not channel:
                        logging.warning(f"Channel with ID {channel_id} not found")
                        continue
                        
                    temp_file_path = await publisher.handle_image(
                        article.get("image"),
                        article.get("id")
                    )
                    
                    content = article.get("content", "No Content")
                    if temp_file_path:
                        with open(temp_file_path, "rb") as image_file:
                            await channel.send(
                                content=content,
                                file=discord.File(image_file)
                            )
                        os.remove(temp_file_path)
                    else:
                        await channel.send(content=content)
                        
                    await publisher.mark_as_published(article.get("id"))
                    logging.info(f"Successfully sent article {article.get('id')} to channel {channel_id}")
                    
                except Exception as e:
                    logging.error(f"Error processing article {article.get('id')}: {e}")
                    continue
                    
    except Exception as e:
        logging.error(f"Error in fetch_unpublished_messages: {e}")

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

# Run the bot
if __name__ == "__main__":
    bot.run(TOKEN)