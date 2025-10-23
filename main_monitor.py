# 負責監控兩個禁止聊天的頻道 main_monitor.py
import os
import re
import asyncio
import contextlib
import discord
from urllib.parse import urlparse
from dotenv import load_dotenv

# 載入 .env
load_dotenv()

TOKEN = os.getenv("TOKEN_ASA_BOT")
if not TOKEN:
    raise RuntimeError("TOKEN_ASA_BOT not set")

# 目標頻道（只允許媒體）
CHANNEL_SHARING_GIRL = int(os.getenv("CHANNEL_SHARING_GIRL", "0") or 0)
CHANNEL_SHARING_BOY  = int(os.getenv("CHANNEL_SHARING_BOY", "0") or 0)
TARGET_CHANNELS = {CHANNEL_SHARING_GIRL, CHANNEL_SHARING_BOY} - {0}

# Intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

# 支援的副檔名
IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VID_EXT = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}

# 常見可內嵌媒體平台
EMBED_HOSTS = {
    "instagram.com", "instagr.am", "kkinstagram.com",
    "twitter.com", "x.com", "fxtwitter.com",
    "youtube.com", "youtu.be",
    "tiktok.com",
    "reddit.com", "v.redd.it",
    "imgur.com", "i.imgur.com",
    "gfycat.com", "streamable.com",
}

URL_PATTERN = re.compile(r'https?://[^\s)]+', re.IGNORECASE)


def is_media_url(u: str) -> bool:
    try:
        p = urlparse(u)
        ext = (p.path or "").lower()
        for e in IMG_EXT | VID_EXT:
            if ext.endswith(e):
                return True
        host = (p.netloc or "").lower()
        # 只要是常見可內嵌媒體平台就視為多媒體
        return any(host.endswith(h) for h in EMBED_HOSTS)
    except Exception:
        return False


class MediaOnlyBot(discord.Client):
    async def on_ready(self):
        print(f"[READY] MediaOnlyBot logged in as {self.user}")

    async def on_message(self, message: discord.Message):
        try:
            # 忽略機器人與不在目標頻道的訊息
            if message.author.bot:
                return
            if message.channel.id not in TARGET_CHANNELS:
                return

            # 是否包含附件的媒體
            has_attachment_media = any(
                (att.content_type or "").startswith("image/")
                or (att.content_type or "").startswith("video/")
                or (att.filename or "").lower().endswith(tuple(IMG_EXT | VID_EXT))
                for att in message.attachments
            )

            # 是否包含可視為媒體的連結
            urls = URL_PATTERN.findall(message.content or "")
            has_media_url = any(is_media_url(u) for u in urls)

            # 若不合規：直接刪除
            if not (has_attachment_media or has_media_url):
                try:
                    await message.delete()
                except discord.Forbidden:
                    # 權限不足：送出一次性提醒（5 秒自刪）
                    warn = await message.channel.send(
                        "此頻道僅允許圖片 / 影片或含內嵌媒體的連結。請重新張貼，謝謝。（缺少刪除訊息權限）"
                    )
                    await asyncio.sleep(5)
                    with contextlib.suppress(Exception):
                        await warn.delete()
                    return
                except discord.HTTPException as e:
                    print(f"[ERROR] delete failed: {e}")
                    return

                # 刪除成功後：發送臨時提醒（5 秒自刪）
                try:
                    tip = await message.channel.send(
                        f"{message.author.mention} 此頻道僅允許圖片 / 影片或含內嵌媒體的連結，請重新張貼，謝謝。"
                    )
                    await asyncio.sleep(5)
                    with contextlib.suppress(Exception):
                        await tip.delete()
                except Exception as e:
                    print(f"[ERROR] tip send/delete failed: {e}")

        except Exception as e:
            print(f"[ERROR] on_message: {e}")


def main():
    client = MediaOnlyBot(intents=intents)
    client.run(TOKEN, reconnect=True)


if __name__ == "__main__":
    main()
