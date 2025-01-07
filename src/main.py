# import os
# import discord
# import logging
# import aiohttp
# from discord.ext import commands
# from dotenv import load_dotenv

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

# # 指定驗證頻道 ID
# VERIFICATION_CHANNEL_ID = 1318102706254123020
# INFORMATION_CHANNEL_ID = 1318102942850613298

# WELCOME_API = "http://127.0.0.1:5002/admin/telegram/social/welcome_msg"
# VERIFY_API = "http://127.0.0.1:5002/admin/telegram/social/verify"
# DETAIL_API = "http://127.0.0.1:5002/admin/telegram/social/detail"
# SOCIAL_API = "http://127.0.0.1:5002/admin/telegram/social/socials"

# MESSAGE_API_URL = "http://127.0.0.1:5003/bot/posts/list?status=0"
# UPDATE_MESSAGE_API_URL = "http://127.0.0.1:5003/bot/posts/edit"

# @bot.event
# async def on_ready():
#     print(f"Bot is ready. Logged in as {bot.user}")

# # @bot.event
# # async def on_member_join(member):
# #     """監聽新用戶加入事件，調用歡迎語 API 並在指定 welcome 頻道發送消息。"""
# #     try:
# #         # 調用 API 獲取歡迎語
# #         print(f"{member.guild.id} 用戶進群了！！！！！")
# #         payload = {"verifyGroup": str(member.guild.id), "brand": "BYD"}
# #         welcome_channel = member.guild.get_channel(1318120388924014694)  # 使用頻道 ID 獲取頻道

# #         # 如果找到 welcome 頻道，發送歡迎消息
# #         if welcome_channel:
# #             await welcome_channel.send(f"{member.mention}, Hello")
# #             # await welcome_channel.send(f"{member.mention}, {welcome_msg}")
# #         else:
# #             print("指定的 welcome 頻道未找到，無法發送歡迎消息。")
# #         async with aiohttp.ClientSession() as session:
# #             async with session.post(WELCOME_API, json=payload) as response:
# #                 if response.status == 200:
# #                     data = await response.json()
# #                     welcome_msg = data.get("data", "Welcome to the server!")  # 預設歡迎語
# #                 else:
# #                     welcome_msg = "Welcome to the server!"

# #         # 獲取指定的 welcome 頻道
# #         welcome_channel = member.guild.get_channel(1318120388924014694)  # 使用頻道 ID 獲取頻道

# #         # 如果找到 welcome 頻道，發送歡迎消息
# #         if welcome_channel:
# #             await welcome_channel.send(f"{member.mention}, {welcome_msg}")
# #         else:
# #             print("指定的 welcome 頻道未找到，無法發送歡迎消息。")
# #     except Exception as e:
# #         logging.error(f"Error handling member join: {e}")

# @bot.event
# async def on_member_join(member):
#     """監聽新用戶加入事件，調用歡迎語 API 並在 welcome 頻道發送圖片和文字。"""
#     try:
#         # 調用 API 獲取歡迎語
#         print(f"{member.guild.id} 用戶進群了！！！！！")
#         payload = {"verifyGroup": str(member.guild.id), "brand": "BYD"}
#         # async with aiohttp.ClientSession() as session:
#         #     async with session.post(WELCOME_API, json=payload) as response:
#         #         if response.status == 200:
#         #             data = await response.json()
#         #             welcome_msg = data.get("data", "Welcome to the server!")  # 預設歡迎語
#         #         else:
#         #             welcome_msg = "Welcome to the server!"

#         # 獲取指定的 welcome 頻道
#         welcome_channel = member.guild.get_channel(1318120388924014694)  # welcome 頻道 ID

#         # 圖片路徑
#         current_dir = os.path.dirname(os.path.abspath(__file__))  # 當前文件所在目錄
#         image_path = os.path.join(current_dir, "..", "pics", "FindUID.jpg")  # 確保是 FindUID.jpg 的絕對路徑
#         print(f"圖片路徑: {image_path}")

#         # 發送歡迎消息和圖片
#         if welcome_channel:
#             with open(image_path, "rb") as image:
#                 file = discord.File(image, filename="FindUID.jpg")  # 上傳圖片文件
#                 await welcome_channel.send(
#                     # content=f"{member.mention}, {welcome_msg}",  # 發送文字內容
#                     content=f"{member.mention}, Welcome",  # 發送文字內容
#                     file=file  # 附加圖片
#                 )
#         else:
#             print("指定的 welcome 頻道未找到，無法發送歡迎消息。")
#     except Exception as e:
#         logging.error(f"Error handling member join: {e}")

# @bot.command()
# async def verify(ctx, uid: str):
#     """處理驗證指令，並限定在指定頻道中執行。"""
#     try:
#         # 檢查是否在指定的驗證頻道中
#         if ctx.channel.id != VERIFICATION_CHANNEL_ID:
#             await ctx.send(f"{ctx.author.mention}, you can only verify yourself in the designated verification channel.")
#             return

#         # 刪除用戶的輸入訊息
#         # await ctx.message.delete()

#         # 檢查 UID 是否已經存在
#         if any(user_id == uid for user_id in verified_users.values()):
#             await ctx.send(f"{ctx.author.mention}, this UID has already been verified by another user. Please check again.")
#             return

#         # 調用 API 進行驗證
#         verify_url = "http://127.0.0.1:5002/admin/telegram/social/verify"
#         print(ctx.channel.id)
#         payload = {
#             "code": uid,
#             "verifyGroup": ctx.channel.id,  # 使用當前頻道的 ID
#             "brand": "BYD",
#             "type": "DISCORD"
#         }

#         async with aiohttp.ClientSession() as session:
#             async with session.post(verify_url, data=payload) as response:
#                 data = await response.json()
#                 print(data)

#                 # 處理返回消息
#                 api_message = data.get("data", "Verification failed. Please try again.")

#                 # 查找群組管理員或創建者
#                 admins = [member async for member in ctx.guild.fetch_members()]
#                 admin_user = next(
#                     (member for member in admins if member.guild_permissions.administrator),
#                     None
#                 )

#                 # 如果找到管理員，替換 {admin}
#                 if admin_user:
#                     admin_mention = admin_user.mention
#                 else:
#                     admin_mention = "the admin team"

#                 # 替換 {admin}
#                 api_message = api_message.replace("@{admin}", admin_mention)

#                 # 格式化 HTML 為 Discord 支援的 Markdown
#                 api_message = api_message.replace("<a>", "").replace("</a>", "")

#                 if response.status == 200 and data.get("status") == "success":
#                     verified_users[ctx.author.id] = uid

#                     # 分配 "verified" 身分組
#                     role = discord.utils.get(ctx.guild.roles, name="verified")
#                     if role:
#                         await ctx.author.add_roles(role)
#                         await ctx.send(f"{ctx.author.mention}, {api_message}")
#                     else:
#                         await ctx.send(f"{ctx.author.mention}, verification successful, but 'verified' role not found.")
#                 else:
#                     await ctx.send(f"{ctx.author.mention}, {api_message}")

#     except Exception as e:
#         logging.error(f"Error in verification command: {e}")
#         # await ctx.send(f"{ctx.author.mention}, an error occurred during verification. Please try again later.")

# # 執行機器人
# bot.run(TOKEN)



import os
import discord
import logging
import aiohttp
from discord.ext import commands, tasks
from dotenv import load_dotenv
from db_handler_aio import *

logging.basicConfig(level=logging.INFO)

# Bot token and intents
load_dotenv()
TOKEN = os.getenv("Discord_TOKEN")
intents = discord.Intents.default()
intents.members = True  # 追蹤成員加入事件
intents.message_content = True  # 啟用 Message Content Intent

bot = commands.Bot(command_prefix="!", intents=intents)

# 已驗證的使用者資料
verified_users = {}

# 指定驗證頻道和訊息頻道 ID
VERIFICATION_CHANNEL_ID = 1318102706254123020
INFORMATION_CHANNEL_ID = 1325745464884068404
WELCOME_CHANNEL_ID = 1318120388924014694

WELCOME_API = "http://127.0.0.1:5002/admin/telegram/social/welcome_msg"
VERIFY_API = "http://127.0.0.1:5002/admin/telegram/social/verify"
DETAIL_API = "http://127.0.0.1:5002/admin/telegram/social/detail"
SOCIAL_API = "http://127.0.0.1:5002/admin/telegram/social/socials"

MESSAGE_API_URL = "http://127.0.0.1:5003/bot/posts/list?is_sent_dc=0"
UPDATE_MESSAGE_API_URL = "http://127.0.0.1:5003/bot/posts/edit"

@bot.event
async def on_ready():
    print(f"Bot is ready. Logged in as {bot.user}")
    fetch_unpublished_messages.start()  # 啟動定時任務

# @bot.event
# async def on_member_join(member):
#     """監聽新用戶加入事件，調用歡迎語 API 並在 welcome 頻道發送圖片和文字。"""
#     try:
#         payload = {"verifyGroup": str(member.guild.id), "brand": "BYD"}
#         welcome_channel = member.guild.get_channel(WELCOME_CHANNEL_ID)  # welcome 頻道 ID

#         # 圖片路徑
#         current_dir = os.path.dirname(os.path.abspath(__file__))
#         image_path = os.path.join(current_dir, "..", "pics", "FindUID.jpg")

#         if welcome_channel:
#             with open(image_path, "rb") as image:
#                 file = discord.File(image, filename="FindUID.jpg")
#                 await welcome_channel.send(
#                     content=f"{member.mention}, Welcome!",  # 發送文字內容
#                     file=file  # 附加圖片
#                 )
#         else:
#             print("指定的 welcome 頻道未找到，無法發送歡迎消息。")
#     except Exception as e:
#         logging.error(f"Error handling member join: {e}")

@bot.event
async def on_member_join(member):
    """当新用户加入时，自动检测每个服务器的 welcome 频道并发送欢迎消息。"""
    try:
        welcome_channel = discord.utils.get(member.guild.text_channels, name="welcome")

        if not welcome_channel:
            welcome_channel = member.guild.system_channel

        if not welcome_channel:
            logging.warning(f"未找到服务器 {member.guild.name} 的欢迎频道。")
            return

        # 图片路径
        current_dir = os.path.dirname(os.path.abspath(__file__))  # 当前文件所在目录
        image_path = os.path.join(current_dir, "..", "pics", "FindUID.jpg")  # 确保图片路径正确
        print(f"图片路径: {image_path}")

        # 欢迎消息内容
        welcome_message = f"🎉 Welcome {member.mention} to {member.guild.name}! Feel free to explore and have fun!"

        # 发送欢迎消息和图片
        with open(image_path, "rb") as image:
            file = discord.File(image, filename="FindUID.jpg")
            await welcome_channel.send(content=welcome_message, file=file)

        logging.info(f"成功发送欢迎消息到 {member.guild.name} 的频道 {welcome_channel.name}。")

    except Exception as e:
        logging.error(f"处理新成员加入事件时发生错误: {e}")

@bot.command()
async def verify(ctx, uid: str = None):
    """處理驗證指令，並限定在指定頻道中執行。"""
    try:
        if ctx.channel.id != VERIFICATION_CHANNEL_ID:
            await ctx.send(f"{ctx.author.mention}, you can only verify yourself in the designated verification channel.")
            return

        # 檢查是否提供了 UID
        if not uid:
            await ctx.send(f"{ctx.author.mention}, Please provide verification code, for example: !verify 123456")
            return

        # 使用 is_user_verified 函数检查 UID 是否已被验证
        verification_status = await is_user_verified(ctx.author.id, ctx.channel.id, uid)
        print(verification_status)

        if verification_status == "warning":
            await ctx.send(f"{ctx.author.mention}, this UID has already been verified")
            return

        elif verification_status == "not_verified":
            # 如果 UID 没有被验证过，可以继续后续的验证流程
            payload = {
                "code": uid,
                "verifyGroup": ctx.channel.id,  # 使用當前頻道的 ID
                "brand": "BYD",
                "type": "DISCORD"
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(VERIFY_API, data=payload) as response:
                    data = await response.json()
                    print(data)

                    api_message = data.get("data", "Verification failed. Please try again.")

                    admins = [member async for member in ctx.guild.fetch_members()]
                    admin_user = next(
                        (member for member in admins if member.guild_permissions.administrator),
                        None
                    )

                    admin_mention = admin_user.mention if admin_user else "the admin team"
                    api_message = api_message.replace("@{admin}", admin_mention)
                    api_message = api_message.replace("<a>", "").replace("</a>", "")

                    if response.status == 200 and "verification successful" in api_message:
                        verified_users[ctx.author.id] = uid

                        role = discord.utils.get(ctx.guild.roles, name="verified")
                        if role:
                            await ctx.author.add_roles(role)
                            await ctx.send(f"{ctx.author.mention}, {api_message}")

                            # 添加已验证用户到数据库
                            await add_verified_user(ctx.author.id, ctx.channel.id, uid)
                        else:
                            await ctx.send(f"{ctx.author.mention}, verification successful, but 'verified' role not found.")
                    else:
                        # 處理驗證失敗的情况
                        await ctx.send(f"{ctx.author.mention}, {api_message}")

        else:
            # 如果查询到 UID 已被验证过
            await ctx.send(f"{ctx.author.mention}, {verification_status} UID.")

    except Exception as e:
        logging.error(f"Error in verification command: {e}")

@tasks.loop(minutes=1)  # 每 1 分鐘執行一次
async def fetch_unpublished_messages():
    """定時檢查未發布文章，並根據 topic_name 發送到對應頻道。"""
    try:
        async with aiohttp.ClientSession() as session:
            # 獲取未發布的文章
            async with session.get(MESSAGE_API_URL) as message_response:
                if message_response.status != 200:
                    logging.error("Failed to fetch unpublished messages.")
                    return

                message_data = await message_response.json()
                articles = message_data.get("data", {}).get("items", [])

                if not articles:
                    logging.info("No unpublished articles found.")
                    return

            # 獲取社群數據
            payload = {
                "brand": "BYD",
                "type": "DISCORD"
            }

            async with session.post(SOCIAL_API, data=payload) as social_response:
                if social_response.status != 200:
                    logging.error("Failed to fetch social data.")
                    return

                social_data = await social_response.json()
                social_groups = social_data.get("data", [])

            # 建立 topic_name 與 chatId 的對應表
            topic_to_channel_map = {}
            for group in social_groups:
                for chat in group.get("chats", []):
                    if chat.get("enable", False):
                        topic_to_channel_map[chat["name"]] = int(chat["chatId"])

            # 發布文章到對應頻道
            for article in articles:
                topic_name = article.get("topic_name")
                content = article.get("content", "No Content")
                image_url = article.get("image")
                article_id = article.get("id")

                # 找到對應的 Discord 頻道 ID
                normalized_topic = topic_name.strip()
                channel_id = topic_to_channel_map.get(normalized_topic)
                if not channel_id:
                    logging.warning(f"No matching channel found for topic: {topic_name}")
                    continue

                # 發送消息到對應的頻道
                channel = bot.get_channel(channel_id)
                if not channel:
                    logging.warning(f"Channel with ID {channel_id} not found.")
                    continue

                try:
                    if image_url:
                        # 發送圖片和文字
                        await channel.send(content=content, file=discord.File(image_url))
                    else:
                        # 僅發送文字
                        await channel.send(content=content)

                    logging.info(f"Successfully sent article {article_id} to channel {channel_id}.")

                    # 標記文章為已發布
                    update_payload = {"id": article_id, "status": 1}
                    async with session.post(UPDATE_MESSAGE_API_URL, json=update_payload) as update_response:
                        if update_response.status == 200:
                            logging.info(f"Article {article_id} marked as published.")
                        else:
                            logging.error(f"Failed to mark article {article_id} as published.")

                except Exception as e:
                    logging.error(f"Failed to send article {article_id} to channel {channel_id}: {e}")

    except Exception as e:
        logging.error(f"Error fetching or sending unpublished articles: {e}")

@fetch_unpublished_messages.before_loop
async def before_fetch_unpublished_messages():
    await bot.wait_until_ready()

# 執行機器人
bot.run(TOKEN)
