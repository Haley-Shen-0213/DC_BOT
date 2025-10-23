# 原本的主程式，負責監控IG、推特訊息轉成內崁格式 main.py
import os
import re
import time
import asyncio
import discord
from dotenv import load_dotenv

# 讀取 .env
load_dotenv()

# 選擇使用哪個 Bot 啟動：默認使用 ASA_BOT
BOT_SELECT = os.getenv("BOT_SELECT", "ASA_BOT").upper()
TOKEN_MAP = {
    "ASA_BOT": os.getenv("DISCORD_TOKEN_ASA_BOT"),
    "ASA_BOX": os.getenv("DISCORD_TOKEN_ASA_BOX"),
}
TOKEN = TOKEN_MAP.get(BOT_SELECT)

# 頻道 IDs（如需使用直接發送或過濾，可從環境讀取）
CHANNEL_SHARING_GIRL = int(os.getenv("CHANNEL_SHARING_GIRL", "0") or 0)
CHANNEL_SHARING_BOY = int(os.getenv("CHANNEL_SHARING_BOY", "0") or 0)
CHANNEL_INJURIED = int(os.getenv("CHANNEL_INJURIED", "0") or 0)
CHANNEL_GAME_BOX = int(os.getenv("CHANNEL_GAME_BOX", "0") or 0)
CHANNEL_CONTRACT = int(os.getenv("CHANNEL_CONTRACT", "0") or 0)
CHANNEL_INTELLIGENCE_NEWS = int(os.getenv("CHANNEL_INTELLIGENCE_NEWS", "0") or 0)

PTT_URL = os.getenv("PTT_URL", "https://www.ptt.cc/bbs/NBA/index.html")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
HEARTBEAT_INTERVAL_SEC = int(os.getenv("HEARTBEAT_INTERVAL_SEC", "3600"))

# === 原有規則保留 ===

INSTAGRAM_URL_PATTERN = re.compile(
    r'(https?://(?:www\.)?(?:instagram\.com|instagr\.am|kkinstagram\.com)/'
    r'((?:p|reel|tv))/([\w\-]+))'
    r'(?:/)?'
    r'(?:\?[^\s#)]*)?'
    r'(?:#[^\s)]*)?',
    re.IGNORECASE
)

TWITTER_URL_PATTERN = re.compile(
    r'(https?://(?:www\.)?(?:twitter\.com|x\.com)/'
    r'(?:'
    r'(?:(?P<user>[A-Za-z0-9_]{1,15})/status(?:es)?/(?P<id1>\d+))'
    r'|i/web/status/(?P<id2>\d+)'
    r'))'
    r'(?:/)?'
    r'(?:\?[^\s#)]*)?'
    r'(?:#[^\s)]*)?',
    re.IGNORECASE
)

intents = discord.Intents.default()
intents.message_content = True

def to_kkinstagram_clean(url: str) -> str | None:
    m = INSTAGRAM_URL_PATTERN.search(url)
    if not m:
        return None
    path_type = m.group(2)
    content_id = m.group(3)
    return f"https://www.kkinstagram.com/{path_type}/{content_id}/"

def to_fxtwitter_clean(url: str) -> str | None:
    m = TWITTER_URL_PATTERN.search(url)
    if not m:
        return None

    user = m.group('user')
    twid = m.group('id1') or m.group('id2')

    if user and twid:
        return f"https://fxtwitter.com/{user}/status/{twid}"
    elif twid:
        return f"https://fxtwitter.com/i/web/status/{twid}"
    return None

class IGLinkBot(discord.Client):
    async def on_ready(self):
        print(f'Logged in as {self.user} (BOT_SELECT={BOT_SELECT})')
        self.bg_task = asyncio.create_task(self.health_check())

    async def health_check(self):
        while True:
            print(f"[HEARTBEAT] Bot alive at {time.strftime('%Y-%m-%d %H:%M:%S')}")
            await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)

    async def on_message(self, message: discord.Message):
        try:
            if message.author.id == self.user.id:
                return

            content = message.content or ""
            if not content:
                return

            replies = []

            # Instagram / kkinstagram
            for match in INSTAGRAM_URL_PATTERN.finditer(content):
                cleaned = f"https://www.kkinstagram.com/{match.group(2)}/{match.group(3)}/"
                replies.append(cleaned)

            # Twitter / X -> fxtwitter
            for match in TWITTER_URL_PATTERN.finditer(content):
                user = match.group('user')
                twid = match.group('id1') or match.group('id2')
                if user and twid:
                    cleaned = f"https://fxtwitter.com/{user}/status/{twid}"
                elif twid:
                    cleaned = f"https://fxtwitter.com/i/web/status/{twid}"
                else:
                    continue
                replies.append(cleaned)

            if replies:
                unique_replies = list(dict.fromkeys(replies))
                await message.channel.send("\n".join(unique_replies))

        except Exception as e:
            print(f"[ERROR] on_message: {e}")

def main():
    if not TOKEN:
        raise RuntimeError(
            "Missing Discord token. Set DISCORD_TOKEN_ASA_BOT or DISCORD_TOKEN_ASA_BOX in .env "
            "and optionally BOT_SELECT=ASA_BOT|ASA_BOX"
        )

    client = IGLinkBot(intents=intents)
    client.run(TOKEN, reconnect=True)

if __name__ == "__main__":
    main()
