import os
import sys
import re
import time
import asyncio
import aiohttp
import datetime
import contextlib
import discord
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from dotenv import load_dotenv
from pathlib import Path
# ===== YouTube 監控（新增）=====
import threading
import json
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

def get_base_dir() -> Path:
    # 若是打包為 exe（例如 PyInstaller），使用 exe 所在目錄，
    # 否則以目前腳本所在目錄作為基準（便於相對路徑與資源尋址）
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent  # 一般執行時使用腳本目錄

BASE_DIR = get_base_dir()  # 專案根目錄（動態決定，支援打包與原始執行）
ENV_PATH = BASE_DIR / ".env"  # .env 檔路徑（可自訂位置）

PTT_BASE = "https://www.ptt.cc/bbs/NBA/"
URL_RE = re.compile(r'https?://\S+')

# 明確載入 .env（若不存在也不報錯；override=False 表示保留現有環境變數）
load_dotenv(dotenv_path=str(ENV_PATH), override=False)

# ===== 檔案與日誌設定 =====
LOG_DIR = BASE_DIR / "log"  # 日誌目錄（每日分檔）
os.makedirs(LOG_DIR, exist_ok=True)  # 若不存在則建立目錄
def get_daily_log_file() -> Path:
    # 依日期分檔：ptt_asabox_YYYY-MM-DD.log（便於輪替與檢索）
    date_str = datetime.date.today().strftime("%Y-%m-%d")
    return LOG_DIR / f"ptt_asabox_{date_str}.log"

def _ts(ts: float | None = None) -> str:
    # 將 UNIX timestamp 轉為人類可讀的時間字串（本地時區）
    ts = ts or time.time()
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

def write_ptt_log(start_time: float, status: str, error_message: str | None = None):
    # PTT/一般運行日誌：記錄事件時間、狀態、錯誤訊息（若有）
    ts_iso = datetime.datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts_iso}\t{status}"
    if error_message:
        # 將多行錯誤訊息壓成一行，便於檔案查閱與處理
        msg = " ".join(str(error_message).splitlines())
        line += f"\t{msg}"
    line += "\n"
    log_file = get_daily_log_file()
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line)  # 追加寫入每日日誌

def write_dedupe_log(event: str, source: str, detail: str | None = None, ts: float | None = None):
    # 去重日誌：記錄去重事件、來源標籤、細節（如刪除數量或訊息 ID）
    tstr = _ts(ts)
    line = f"{tstr}\t{event}\t{source}"
    if detail:
        line += f"\t{detail}"
    line += "\n"
    log_file = get_daily_log_file()
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line)

# 兩個 Token（必填），
# 從 .env 或系統環境變數載入；缺少即拋錯提醒設定
TOKEN_ASA_BOT = os.getenv("TOKEN_ASA_BOT")
TOKEN_ASA_BOX = os.getenv("TOKEN_ASA_BOX")
if not TOKEN_ASA_BOT or not TOKEN_ASA_BOX:
    # 明確提示需設定 TOKEN_ASA_BOT / TOKEN_ASA_BOX（名稱依實際環境）
    raise RuntimeError("Missing tokens. Please set TOKEN_ASA_BOT and TOKEN_ASA_BOX in environment.")

# ===== 頻道 IDs（請在 .env 設定）=====
# .env 中請設定以下鍵名（全大寫），程式同時兼容舊鍵：
# - CHANNEL_SHARING_GIRL（兼容：Channel_Sharing_girl） 女球員分享頻道 ID
# - CHANNEL_SHARING_BOY （兼容：Channel_Sharing_boy）  男球員分享頻道 ID
# - CHANNEL_INJURIED    （兼容：Channel_Injuried）     傷病資訊頻道 ID
# - CHANNEL_GAME_BOX    （兼容：Channel_Game_Box）     BOX 類資訊頻道 ID
# - CHANNEL_CONTRACT    （兼容：Channel_contract）     合約/交易資訊頻道 ID
# - CHANNEL_INTELLIGENCE_NEWS（兼容：Channel_intelligence_news） 情報/新聞頻道 ID
# - CHANNEL_INS         （兼容：Channel_INS）          INS 發送頻道 ID（若有）
# - CHANNEL_TEST        （兼容：Channel_Test）         測試頻道 ID（若有）
CHANNEL_SHARING_GIRL = os.getenv("CHANNEL_SHARING_GIRL") or os.getenv("Channel_Sharing_girl")
CHANNEL_SHARING_BOY  = os.getenv("CHANNEL_SHARING_BOY")  or os.getenv("Channel_Sharing_boy")

CHANNEL_INJURIED          = os.getenv("CHANNEL_INJURIED")          or os.getenv("Channel_Injuried")
CHANNEL_GAME_BOX          = os.getenv("CHANNEL_GAME_BOX")          or os.getenv("Channel_Game_Box")
CHANNEL_CONTRACT          = os.getenv("CHANNEL_CONTRACT")          or os.getenv("Channel_contract")
CHANNEL_INTELLIGENCE_NEWS = os.getenv("CHANNEL_INTELLIGENCE_NEWS") or os.getenv("Channel_intelligence_news")

CHANNEL_INS  = os.getenv("CHANNEL_INS")  or os.getenv("Channel_INS")
CHANNEL_TEST = os.getenv("CHANNEL_TEST") or os.getenv("Channel_Test")

# ===== 有效媒體目標頻道集合 =====
TARGET_MEDIA_CHANNELS = {CHANNEL_SHARING_GIRL, CHANNEL_SHARING_BOY} - {0}

# 去重掃描設定（刪重訊息的掃描上限與是否在啟動時自動去重）
DUPLICATE_SCAN_LIMIT = int(os.getenv("DUPLICATE_SCAN_LIMIT", "1000"))
AUTO_DEDUPE_ON_START = os.getenv("AUTO_DEDUPE_ON_START", "false").lower() == "true"

# 心跳（兩邊各自有），
# 控制 heartbeat 訊息輸出的時間間隔（秒）
HEARTBEAT_INTERVAL_SEC = int(os.getenv("HEARTBEAT_INTERVAL_SEC", "3600"))

# PTT 設定（AsaBox 使用）
BASE_URL = "https://www.ptt.cc"  # PTT 主站域名（用於拼接相對連結）
INDEX_URL = os.getenv("PTT_URL", "https://www.ptt.cc/bbs/NBA/index.html")  # 看板索引頁 URL
FETCH_INTERVAL = int(os.getenv("PTT_FETCH_INTERVAL_SEC", "900"))  # 抓取週期（秒）
MAX_PAGES = int(os.getenv("PTT_MAX_PAGES", "12"))  # 最大索引頁數回溯
ONLY_TODAY = os.getenv("PTT_ONLY_TODAY", "true").lower() == "true"  # 僅抓取今日文章
STOP_AT_FIRST_OLDER = os.getenv("PTT_STOP_AT_FIRST_OLDER", "true").lower() == "true"  # 遇到非今日即停
TARGET_PREFIXES = [p.strip() for p in os.getenv("PTT_TARGET_PREFIXES", "BOX,情報").split(",") if p.strip()]  # 目標標題前綴
KEYWORDS_INJURY = [w.strip() for w in os.getenv("KEYWORDS_INJURY", "").split(",") if w.strip()]  # 傷病關鍵字
CONTRACT_PATTERNS = [p.strip() for p in os.getenv("KEYWORDS_CONTRACT_PATTERNS", "").split(";") if p.strip()]  # 合約模式（分號分隔）
NEGATIVE_FOR_CONTRACT_TITLE = [w.strip() for w in os.getenv("NEGATIVE_FOR_CONTRACT_TITLE", "").split(",") if w.strip()]  # 合約負面排除字

# 從 .env 讀取（注意你的環境變數大小寫）
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID", "").strip()  # 目標 YouTube 頻道 ID
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()        # YouTube Data API 金鑰
DISCORD_WEBHOOK_URL_YT = os.getenv("DISCORD_WEBHOOK_URL", "").strip()  # 用於推播 YouTube 更新的 Discord Webhook

LAST_CHECKED_FILE = BASE_DIR / (os.getenv("LAST_CHECKED_FILE", "last_checked_videos.json"))  # 已檢查影片記錄檔
YT_CHECK_INTERVAL_SECONDS = int(os.getenv("YT_CHECK_INTERVAL_SECONDS", "3600"))  # YouTube 檢查週期（秒，預設每小時）

# Intents（Discord 權限意圖設定：需與機器人 Portal 設定一致）
intents_bot = discord.Intents.default()
intents_bot.message_content = True  # 允許讀取訊息內容（需在 Portal 開啟 Message Content Intent）
intents_bot.guilds = True           # 允許公會事件
intents_bot.messages = True         # 允許訊息事件

intents_box = discord.Intents.default()
intents_box.message_content = True  # AsaBox 也需要讀取訊息內容
intents_box.guilds = True
intents_box.messages = True

# ===== IG / X 連結規則（AsaBot 用）=====
INSTAGRAM_URL_PATTERN = re.compile(
    # 支援 instagram.com / instagr.am / kkinstagram.com，
    # 並捕捉 p/reel/tv 三種內容型態與其短碼
    r'(https?://(?:www\.)?(?:instagram\.com|instagr\.am|kkinstagram\.com)/'
    r'((?:p|reel|tv))/([\w\-]+))'
    r'(?:/)?'            # 可選結尾斜線
    r'(?:\?[^\s#)]*)?'   # 可選查詢字串
    r'(?:#[^\s)]*)?',    # 可選片段（hash）
    re.IGNORECASE
)
TWITTER_URL_PATTERN = re.compile(
    # 支援 twitter.com / x.com 格式，
    # 捕捉使用者帳號與推文 ID（含 i/web/status）
    r'(https?://(?:www\.)?(?:twitter\.com|x\.com)/'
    r'(?:'
    r'(?:(?P<user>[A-Za-z0-9_]{1,15})/status(?:es)?/(?P<id1>\d+))'
    r'|i/web/status/(?P<id2>\d+)'
    r'))'
    r'(?:/)?'            # 可選結尾斜線
    r'(?:\?[^\s#)]*)?'   # 可選查詢字串
    r'(?:#[^\s)]*)?',    # 可選片段（hash）
    re.IGNORECASE
)

# 全域簡單去重快取，
# 記憶最近一次寫入的 key 與時間，避免短時間重覆處理
_LOG_DEDUPE_CACHE = {}
_LOG_LOCK = threading.Lock()  # 保護快取併發存取（在多執行緒/非同步情境下安全）

def _ensure_dir(path: str):
    # 確保目錄存在：不存在則建立，若建立失敗（例如權限問題）則忽略例外以免中斷
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass  # 實務上可加上 log_event 提醒

def _now_ts_str():
    # 以本地時區回傳現在時間字串（YYYY-MM-DD HH:MM:SS）
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _default_log_path():
    # 回傳預設 YouTube 監控日誌檔路徑：logs/yt/YYYY-MM-DD.log
    base = os.path.join(os.getcwd(), "logs", "yt")
    _ensure_dir(base)  # 確保目錄存在
    fname = datetime.datetime.now().strftime("%Y-%m-%d") + ".log"
    return os.path.join(base, fname)

def log_event(tag: str, source: str, message: str, *,
              level: str = "INFO",
              file_path: str | None = None,
              dedupe_key: str | None = None,
              dedupe_ttl_sec: int = 30,
              also_print: bool = True):
    """
    通用事件記錄（含去重與 console 輸出）：
    - tag: 事件類型（例如 YT_MONITOR_START, YT_NO_NEW, YT_HTTP_ERROR）
    - source: 來源系統（例如 "YouTube"、"PTT"、"AsaBox"）
    - message: 文字內容（建議包含可變資訊：id/url/秒數等）
    - level: 日誌等級（INFO/WARN/ERROR）
    - file_path: 指定寫入檔案路徑；不指定則使用預設 logs/yt/YYYY-MM-DD.log
    - dedupe_key: 去重鍵；同鍵在 TTL 期間僅寫一次（避免洗版）
    - dedupe_ttl_sec: 去重 TTL 秒數（預設 30 秒）
    - also_print: 同步印到 console（True 時印出）
    """
    ts = _now_ts_str()  # 生成時間戳字串
    line = f"{ts}\t{level}\t{tag}\t{source}\t{message}"  # 組合一行日誌

    # 去重判斷（使用全域 _LOG_DEDUPE_CACHE 與 _LOG_LOCK 保護）
    if dedupe_key:
        with _LOG_LOCK:
            last_when = _LOG_DEDUPE_CACHE.get(dedupe_key)  # 上次紀錄時間
            now_epoch = time.time()
            if last_when and (now_epoch - last_when) < dedupe_ttl_sec:
                return  # TTL 內重覆：直接略過不寫
            _LOG_DEDUPE_CACHE[dedupe_key] = now_epoch  # 更新最近寫入時間

    # 寫檔到指定或預設路徑
    path = file_path or _default_log_path()
    try:
        _ensure_dir(os.path.dirname(path))  # 確保目錄存在
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")  # 追加寫入一行
    except Exception as e:
        # 檔案寫入失敗：可印出警告，避免吞掉錯誤
        if also_print:
            print(f"[LOG] write failed: {e} path={path}")

    # console 輸出（便於即時觀察）
    if also_print:
        print(f"[LOG] {line}")

def yt_log(tag: str, message: str, *, level: str = "INFO",
           dedupe_key: str | None = None, dedupe_ttl_sec: int = 30):
    # YouTube 專用薄包：固定 source="YouTube"，傳入其他參數到 log_event
    log_event(tag=tag, source="YouTube", message=message,
              level=level, dedupe_key=dedupe_key, dedupe_ttl_sec=dedupe_ttl_sec)

def to_kkinstagram_clean(url: str) -> str | None:
    # 將原始 Instagram 連結轉為 kkinstagram 乾淨頁面（便於 Discord 嵌入與預覽）
    m = INSTAGRAM_URL_PATTERN.search(url)
    if not m:
        return None
    path_type = m.group(2)     # p/reel/tv
    content_id = m.group(3)    # 內容短碼
    return f"https://www.kkinstagram.com/{path_type}/{content_id}/"

def to_fxtwitter_clean(url: str) -> str | None:
    # 將原始 Twitter/X 連結轉為 fxtwitter 乾淨頁面（改善預覽與解析）
    m = TWITTER_URL_PATTERN.search(url)
    if not m:
        return None
    user = m.group('user')     # 使用者帳號
    twid = m.group('id1') or m.group('id2')  # 推文 ID（兩種欄位其一）
    if user and twid:
        return f"https://fxtwitter.com/{user}/status/{twid}"
    elif twid:
        return f"https://fxtwitter.com/i/web/status/{twid}"
    return None  # 無法解析時回傳 None

def _seconds_until_next_1505(now: datetime.datetime | None = None) -> int:
    # 計算距離下一次 15:05 的秒數（最少回傳 1 秒）
    now = now or datetime.datetime.now()
    target_today = now.replace(hour=15, minute=5, second=0, microsecond=0)
    target = target_today if now <= target_today else (target_today + datetime.timedelta(days=1))
    return max(1, int((target - now).total_seconds()))

def _yt_build_service():
    # 建立 YouTube Data API v3 的 service 物件；缺金鑰則拋錯，提醒設定 .env
    if not YOUTUBE_API_KEY:
        raise RuntimeError(f"Missing Youtube_API_KEY in .env at {ENV_PATH}")
    return build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)

def _yt_get_channel_uploads_playlist_id(youtube, channel_id: str) -> str | None:
    # 用 channel_id 取回該頻道的「uploads」播放清單 ID（頻道所有上傳影片）
    resp = youtube.channels().list(part="contentDetails", id=channel_id).execute()
    items = resp.get('items') or []
    if items:
        return items[0]['contentDetails']['relatedPlaylists']['uploads']  # 取第一筆的 uploads 欄位
    return None  # 找不到頻道或缺欄位時回傳 None

def _yt_get_latest_videos_from_playlist(youtube, playlist_id: str, max_results: int = 10) -> list[dict]:
    # 以播放清單 ID 抓取最新影片（回傳字典列表：id/title/publishedAt/url）
    resp = youtube.playlistItems().list(
        part="snippet,contentDetails",
        playlistId=playlist_id,
        maxResults=max_results
    ).execute()
    videos = []
    for item in resp.get('items', []):
        vid = item['contentDetails']['videoId']
        title = item['snippet']['title']
        published_at = item['snippet']['publishedAt']  # ISO 8601 格式時間
        videos.append({"id": vid, "title": title, "publishedAt": published_at, "url": f"https://www.youtube.com/watch?v={vid}"})
    return videos

def _yt_load_last_checked() -> list[dict]:
    # 載入上次檢查記錄（JSON 檔）：若無或錯誤則回空列表
    try:
        if LAST_CHECKED_FILE.exists():
            with open(LAST_CHECKED_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)  # 預期為 list[dict]，含 id/title/publishedAt/url
    except Exception:
        pass  # 可選：yt_log("YT_LOAD_LAST_CHECKED_FAILED", str(e), level="WARN")
    return []

def _yt_save_last_checked(videos: list[dict]):
    # 保存本次檢查的影片列表到 JSON：格式化縮排便於手動檢視
    try:
        with open(LAST_CHECKED_FILE, 'w', encoding='utf-8') as f:
            json.dump(videos, f, ensure_ascii=False, indent=4)
    except Exception as e:
        write_ptt_log(time.time(), "YT_SAVE_LAST_CHECKED_FAILED", str(e))  # 失敗記錄於一般日誌

def _yt_send_discord_message(title: str, url: str):
    # 將新影片通知以 Discord Webhook 推送：content=標題 + URL
    if not DISCORD_WEBHOOK_URL_YT:
        write_ptt_log(time.time(), "YT_WEBHOOK_MISSING", "DISCORD_WEBHOOK_URL not set")
        return
    payload = {"content": f"{title}\n{url}"}
    try:
        r = requests.post(DISCORD_WEBHOOK_URL_YT, json=payload, timeout=10)  # POST JSON，10 秒 timeout
        if r.status_code not in (200, 204):
            write_ptt_log(time.time(), "YT_WEBHOOK_FAIL", f"{r.status_code} {r.text}")  # 非成功狀態碼記錄
    except Exception as e:
        write_ptt_log(time.time(), "YT_WEBHOOK_EXCEPTION", str(e))  # 例外記錄

def _extract_id(item: dict) -> str | None:
    # 從影片項目擷取 videoId：
    # - 你的來源（_yt_get_latest_videos_from_playlist）已含 "id"
    # - 若來源不同（例如直接用 playlistItems 原生結構），則相容 contentDetails.videoId
    return item.get("id") or item.get("contentDetails", {}).get("videoId")

def _parse_ts(ts: str | None) -> float:
    # 將 ISO 8601（含 Z）時間字串轉為 epoch 秒數；解析失敗回傳 0.0
    # 例：2024-10-01T12:34:56Z -> 轉為 UTC 時區的 timestamp
    if not ts:
        return 0.0
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0  # 解析失敗時給零，方便排序時自然排在最前（或視情況調整）

def _sort_by_published(items: list[dict]) -> list[dict]:
    # 依發布時間（publishedAt）排序由舊到新：
    # - 優先讀取 item["publishedAt"]
    # - 若沒有，嘗試讀取 item["snippet"]["publishedAt"]
    # - 使用 _parse_ts 將字串轉 timestamp 作為排序 key
    return sorted(items, key=lambda it: _parse_ts(it.get("publishedAt") or it.get("snippet", {}).get("publishedAt")))

# ===== 媒體限定監控規則（AsaBot 用）=====
IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}  # 影像副檔名集合
VID_EXT = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}   # 影片副檔名集合
EMBED_HOSTS = {
    # 常見可嵌入／社群平台主機（支援尾端比對，例如 subdomain.twitter.com 也會符合）
    "instagram.com", "instagr.am", "kkinstagram.com",
    "twitter.com", "x.com", "fxtwitter.com",
    "youtube.com", "youtu.be",
    "tiktok.com",
    "reddit.com", "v.redd.it",
    "imgur.com", "i.imgur.com",
    "gfycat.com", "streamable.com",
}
URL_PATTERN = re.compile(r'https?://[^\s)]+', re.IGNORECASE)  # 簡單抓取 URL 的正則：空白或右括號前截止

def is_media_url(u: str) -> bool:
    # 判斷字串是否為「媒體連結」：
    # 1) 若 path 擁有常見圖片/影片副檔名 -> True
    # 2) 若主機為常見嵌入社群站台（EMBED_HOSTS） -> True
    # 任何例外或不匹配 -> False
    try:
        p = urlparse(u)  # 解析 URL（scheme/netloc/path/query/fragment）
        ext = (p.path or "").lower()
        for e in IMG_EXT | VID_EXT:
            if ext.endswith(e):
                return True  # 副檔名直接匹配
        host = (p.netloc or "").lower()
        return any(host.endswith(h) for h in EMBED_HOSTS)  # 以尾端比對支援子網域
    except Exception:
        return False

# ===== PTT/NBA 工具（AsaBox 用）=====
def make_session():
    # 建立 requests Session，帶入：
    # - over18=1 cookie（跳過 PTT 年齡確認）
    # - 自訂 UA（避免被視為爬蟲或取得較穩定結果）
    s = requests.Session()
    s.cookies.set('over18', '1', domain='.ptt.cc')
    s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; PTTFetcher/2.1)"})
    return s

def fetch_page(session, url):
    # 以既有 session 取頁面，10 秒逾時；狀態碼非 2xx 時 raise_for_status 拋錯
    resp = session.get(url, timeout=10)
    resp.raise_for_status()
    return resp.text  # 回傳 HTML 文字

def extract_bracket_prefix(title: str):
    # 解析標題前綴（中括號）：
    # [BOX] XXX -> 回傳 ("BOX", "XXX")
    # 規則：
    # - 以 '[' 開頭且存在 ']'，取中間內容為 prefix（移除空白）
    # - remaining 為 ']' 之後的標題（去除左側空白）
    # - 內容空字串時回傳 None 作為 prefix
    t = title.strip()
    if len(t) >= 3 and t[0] == '[':
        close_idx = t.find(']')
        if close_idx != -1:
            inner = ''.join(t[1:close_idx].strip().split())  # 去空白與中間空白（確保一致性）
            remaining = t[close_idx+1:].lstrip()
            return (inner if inner else None), remaining
    return None, t  # 無中括號前綴時：prefix=None, remaining=整標題

def ptt_date_to_full_date(mmdd: str, today: datetime.date):
    # 將 PTT 列表的日期（MM/DD）轉為 "YYYY/MM/DD"：
    # - 年份取今日年份（PTT 列表不含年）
    # - 不可解析或數值非法則回傳 None
    parts = (mmdd or "").strip().split('/')
    if len(parts) != 2:
        return None
    try:
        m = int(parts[0].strip()); d = int(parts[1].strip())
        return datetime.date(today.year, m, d).strftime("%Y/%m/%d")
    except ValueError:
        return None

def parse_entries(html: str, today: datetime.date):
    # 解析索引頁 HTML，擷取每筆文章資訊：
    # - title: 原始標題
    # - title_no_prefix: 去除中括號前綴後的標題
    # - prefix: 中括號內前綴（BOX／情報 等）
    # - ptt_mmdd: PTT 列表顯示的 MM/DD
    # - full_date: 轉為 "YYYY/MM/DD"（以 today 年份）
    # - url: 文章完整 URL
    soup = BeautifulSoup(html, "html.parser")
    rlist = soup.select("div.r-list-container div.r-ent")  # PTT 列表條目
    results = []
    for ent in rlist:
        title_div = ent.select_one("div.title")
        date_div = ent.select_one("div.meta > div.date")
        if not title_div or not date_div:
            continue  # 結構不完整：略過
        a = title_div.find("a")
        if not a:
            continue  # 例如已刪文或無連結：略過
        title_text = a.get_text(strip=True)
        prefix, remaining_title = extract_bracket_prefix(title_text)  # 解析前綴
        href = a.get("href")
        full_url = urljoin(BASE_URL, href) if href else None
        date_text = date_div.get_text(strip=True)
        full_date = ptt_date_to_full_date(date_text, today)
        results.append({
            "title": title_text,
            "title_no_prefix": remaining_title,
            "prefix": prefix,
            "ptt_mmdd": date_text,
            "full_date": full_date,
            "url": full_url
        })
    return results

def find_prev_page_url(html: str):
    # 從索引頁 HTML 找到「上頁」連結（上一頁 index.html）：
    # - 目標選擇器：div.btn-group-paging 內 a.btn.wide[href]
    # - 文字含「上頁」且 href 包含 "index" 且 .html 結尾
    soup = BeautifulSoup(html, "html.parser")
    paging = soup.select_one("div.btn-group-paging")
    if not paging:
        return None
    for a in paging.select("a.btn.wide[href]"):
        text = a.get_text(strip=True)
        href = a["href"]
        if "上頁" in text and "index" in href and href.endswith(".html"):
            return urljoin(BASE_URL, href)
    return None  # 找不到則回傳 None

def filter_by_target_prefix(items, target_prefixes):
    # 依目標前綴過濾項目（空集合時回傳原列表）：
    # - 只保留 prefix 在 target_prefixes 內的文章
    if not target_prefixes:
        return items
    return [it for it in items if (it.get("prefix") or "") in target_prefixes]

def is_injury(title: str) -> bool:
    # 判斷是否為「傷病」資訊：
    # - 將標題轉小寫，檢查是否包含 KEYWORDS_INJURY 任一關鍵字（亦轉小寫）
    tl = (title or "").lower()
    return any(kw.lower() in tl for kw in KEYWORDS_INJURY)

def is_contract(title: str) -> bool:
    # 判斷是否為「合約／交易」資訊：
    # - 先做負面排除（NEGATIVE_FOR_CONTRACT_TITLE）
    # - 再以 CONTRACT_PATTERNS（正則）匹配標題（忽略大小寫）
    t = title or ""
    tl = t.lower()
    if any(neg.lower() in tl for neg in NEGATIVE_FOR_CONTRACT_TITLE):
        return False
    for pat in CONTRACT_PATTERNS:
        if re.search(pat, t, flags=re.IGNORECASE):
            return True
    return False

def classify_info(title: str) -> str:
    # 將情報類別分類為三種：
    # - INFO_INJURIED（傷病）/ INFO_CONTRACT（合約交易）/ INFO_OTHER（其他）
    if is_injury(title):
        return "INFO_INJURIED"
    if is_contract(title):
        return "INFO_CONTRACT"
    return "INFO_OTHER"

def build_content_box(full_date: str, title_no_prefix: str, url: str):
    # 組合 BOX 類訊息內容（便於推播到 Discord）
    # 格式：
    # YYYY/MM/DD
    # [BOX] <去前綴標題>
    # <文章 URL>
    return f"{full_date}\n[BOX] {title_no_prefix}\n{url}"

def build_content_info(full_date: str, info_type: str, title_no_prefix: str, url: str):
    # 組合 情報 類訊息內容，依 info_type 映射中文標籤
    label_map = {
        "INFO_CONTRACT": "情報-合約/交易",
        "INFO_INJURIED": "情報-受傷",
        "INFO_OTHER": "情報-其他",
    }
    label = label_map.get(info_type, "情報")
    return f"{full_date}\n[{label}] {title_no_prefix}\n{url}"

def collect_today(session):
    # 以 PTT 索引頁為起點，回溯最多 MAX_PAGES 頁，收集「今日」且符合目標前綴的文章：
    # - buckets 以前綴分類（BOX、情報三類）
    # - STOP_AT_FIRST_OLDER=True 時，遇到第一筆非今日即停止（加速）
    today = datetime.date.today()
    today_str = today.strftime("%Y/%m/%d")

    current_url = INDEX_URL  # 起始索引頁
    pages = 0
    buckets = {"BOX": [], "INFO_CONTRACT": [], "INFO_INJURIED": [], "INFO_OTHER": []}  # 結果桶

    while current_url and pages < MAX_PAGES:
        html = fetch_page(session, current_url)  # 取得頁面 HTML（可能拋錯）
        entries = parse_entries(html, today=today)

        # 日誌：觀察頁面日期分布（偵測排序異常）
        seen_mmdd = [e.get("ptt_mmdd") or "" for e in entries]
        if seen_mmdd:
            try:
                mmdd_sorted = sorted(seen_mmdd)
                newest = mmdd_sorted[-1]; oldest = mmdd_sorted[0]
                write_ptt_log(time.time(), f"[PTT_PAGE_DATE_STATS] page={pages+1} today_seen={sum(1 for e in entries if e.get('full_date')==today_str)} total_seen={len(entries)} newest={newest} oldest={oldest}", None)
            except Exception as _:
                write_ptt_log(time.time(), f"[PTT_PAGE_DATE_STATS_ERR] page={pages+1}", None)

        # 僅保留今日條目，再依目標前綴過濾
        entries_today = [e for e in entries if e.get("full_date") == today_str]
        entries_today = filter_by_target_prefix(entries_today, TARGET_PREFIXES)

        # [新增] 印出本頁每一筆抓到的原始條目（過濾後）
        for i, e in enumerate(entries_today, start=1):
            write_ptt_log(time.time(), f"[PTT][RAW] page={pages+1} idx={i}, date={e.get('full_date')} mmdd={e.get('ptt_mmdd')}, prefix={e.get('prefix')}, title={e.get('title')}, title_no_prefix={e.get('title_no_prefix')}, url={e.get('url')}", None)

        # 分桶：BOX 與 情報（情報需再分類為合約/傷病/其他）
        for e in entries_today:
            if e.get("prefix") == "BOX":
                buckets["BOX"].append(e)
            elif e.get("prefix") == "情報":
                k = classify_info(e.get("title", ""))
                buckets[k].append(e)

        # 繼續往上一頁
        prev_url = find_prev_page_url(html)
        if not prev_url:
            break  # 沒有上一頁或結構變動：停止
        current_url = prev_url
        pages += 1
    write_ptt_log(time.time(), buckets, None)

    return buckets  # 回傳分類後的今日文章集合

def normalize_url(u: str) -> str:
    # 去除末尾常見標點/括號
    return u.rstrip(').,;!?>"]\'')

def is_ptt_nba_url(u: str) -> bool:
    return isinstance(u, str) and u.startswith(PTT_BASE)

def extract_urls_from_message(msg) -> set:
    urls = set()

    # 文字內容
    content = getattr(msg, "content", None)
    if content:
        for m in URL_RE.findall(content):
            u = normalize_url(m)
            if is_ptt_nba_url(u):
                urls.add(u)

    # embeds
    embeds = getattr(msg, "embeds", None)
    if embeds:
        for emb in embeds:
            # 直接 URL 欄位
            if getattr(emb, "url", None):
                u = normalize_url(emb.url)
                if is_ptt_nba_url(u):
                    urls.add(u)
            # 圖片/縮圖的 URL
            thumb = getattr(emb, "thumbnail", None)
            if thumb and getattr(thumb, "url", None):
                u = normalize_url(thumb.url)
                if is_ptt_nba_url(u):
                    urls.add(u)
            image = getattr(emb, "image", None)
            if image and getattr(image, "url", None):
                u = normalize_url(image.url)
                if is_ptt_nba_url(u):
                    urls.add(u)
            # 也可掃 emb.description/fields 文字（視需求再加）

    # 附件
    attachments = getattr(msg, "attachments", None)
    if attachments:
        for att in attachments:
            if getattr(att, "url", None):
                u = normalize_url(att.url)
                if is_ptt_nba_url(u):
                    urls.add(u)

    return urls

async def collect_seen_ptt_urls_from_channel(channel, limit: int = 20) -> set:
    seen = set()
    try:
        async for msg in channel.history(limit=limit):
            seen |= extract_urls_from_message(msg)
    except Exception as e:
        print(f"[WARN] fetch history failed ch={getattr(channel,'id',None)} err={e}")
    return seen

# ===== 頻道錨點與去重管理（AsaBox 用）=====
ANCHOR_URL_REGEX = re.compile(r'https?://[^\s]+', re.IGNORECASE)  # 抓取訊息中第一個 URL（直到空白）

class ChannelAnchorManager:
    def __init__(self):
        # map：每個分類對應 Discord 頻道與最後錨點 URL（last_url）
        self.map = {
            "BOX": {"channel_id": CHANNEL_GAME_BOX, "last_url": None},
            "INFO_CONTRACT": {"channel_id": CHANNEL_CONTRACT, "last_url": None},
            "INFO_INJURIED": {"channel_id": CHANNEL_INJURIED, "last_url": None},
            "INFO_OTHER": {"channel_id": CHANNEL_INTELLIGENCE_NEWS, "last_url": None},
        }
        self.sent_urls = set()  # 全域已發送 URL 集合（跨分類用於去重）

    async def load_last_anchors(self, client: discord.Client, started_at: float):
        # 從各目標頻道讀取最近（最多 100 則）由 bot 發送的訊息，擷取首個 URL 當作錨點
        # - 目的：之後從來源列表倒序掃描時，遇錨點就停止（避免重覆推播）
        for key, rec in self.map.items():
            ch_id = rec["channel_id"]
            if not ch_id:
                continue
            try:
                # 先從快取取頻道，沒有則 API 取回
                channel = client.get_channel(ch_id) or await client.fetch_channel(ch_id)
            except Exception as e:
                print(f"[WARN] fetch_channel({ch_id}) failed: {e}")
                continue

            last_url = None
            try:
                # 掃描頻道歷史訊息，限定 100 則
                async for msg in channel.history(limit=100):
                    if msg.author.bot:  # 只認 bot 自己/其他 bot 的訊息作為錨點（避免人類貼文干擾）
                        m = ANCHOR_URL_REGEX.search(msg.content or "")
                        if m:
                            last_url = m.group(0)  # 擷取第一個 URL
                            break
                rec["last_url"] = last_url
                if last_url:
                    self.sent_urls.add(last_url)  # 也加入 sent_urls，避免再次推送
                print(f"[ANCHOR] {key} channel={ch_id} last_url={last_url}")
                # 使用呼叫端傳入的 started_at 寫日誌，避免 AttributeError
                write_ptt_log(started_at, f"[ANCHOR] {key} channel={ch_id} last_url={last_url}", None)
            except Exception as e:
                print(f"[WARN] load_last_anchor for {key} failed: {e}")

    def stop_when_hit_anchor(self, key: str, items: list):
        # 給定分類 key 與項目清單 items（通常已按時間新->舊或舊->新排序）
        # - 若遇到錨點 URL（last_url）即停止收集
        # - 若項目 URL 已在 sent_urls 也跳過（避免重發）
        # - 回傳需「新發送」的 items（保持原順序）
        rec = self.map.get(key)
        print(f"rec = {rec}")
        if not rec:
            return []
        anchor = rec.get("last_url")
        print(f"anchor = {anchor}")
        if not items:
            return []
        collected = []
        for it in items:
            print(f"items = {items}, it = {it}")
            u = it.get("url")
            if not u:
                continue
            if anchor and u == anchor:
                break  # 命中錨點：停止再往後收集
            if u in self.sent_urls:
                continue  # 已發送過：略過
            collected.append(it)  # 新的、未發送過的：加入
        return collected

    def mark_sent(self, key: str, items: list):
        # 發送完成後，標記已發送：
        # - 更新此分類的 last_url 為這批的第一則 URL（視需求可改為最後一則）
        # - 將所有 items 的 URL 加入 sent_urls
        if not items:
            return
        u_first = items[0].get("url")
        if u_first:
            self.map[key]["last_url"] = u_first
        for it in items:
            u = it.get("url")
            if u:
                self.sent_urls.add(u)

# ===== 共同工具：刪除重複訊息（跨 Bot 可用，含日誌）=====
async def delete_duplicate_messages(
    client: discord.Client,
    channel_ids: list[int],
    limit: int = DUPLICATE_SCAN_LIMIT,
    source: str = "auto.Asabox",
    *,
    verbose: bool = True,                 # 是否輸出逐則訊息的詳細 LOG（True 會更詳細）
    verbose_cap_per_channel: int = 200    # 每個頻道最多記錄多少筆逐則 LOG（避免檔案爆量）
):
    """
    掃描指定頻道最近 limit 則訊息，刪除文字內容完全相同的重複訊息。
    - 僅對「一般訊息」（discord.MessageType.default）進行去重
    - 空內容（只有附件或嵌入）不去重
    - 以訊息「完整文字內容」作為去重 key（完全一致才算重複）
    LOG 分層：
    - dedupe_start/dedupe_done：整體開始與結束
    - dedupe_channel_begin/dedupe_channel_end：每個頻道的掃描起訖與耗時
    - dedupe_msg_*：逐則結果（受 verbose 與 cap 控制）
    - dedupe_error：任何例外
    """

    # 記錄整體作業開始時間（epoch 秒），用於耗時計算與 LOG 統一時間標記
    started_at = time.time()

    # 開始 LOG：列出來源、頻道清單、limit 上限與 verbose 狀態
    write_dedupe_log(
        "dedupe_start",                        # 事件標籤：全域開始
        source,                                # 來源標記（例如 auto.Asabox / manual.Asabot）
        detail=f"channels={','.join(str(cid) for cid in channel_ids if cid)} limit={limit} verbose={verbose}",
        ts=started_at                          # 使用統一時間戳，方便串接
    )

    # 全域統計：累加刪除數與掃描數
    total_deleted = 0
    total_scanned = 0

    # 逐一處理每個頻道 ID
    for ch_id in channel_ids:
        # 若 ch_id 無效（None 或 0），略過
        if not ch_id:
            continue

        # 記錄單一頻道的作業開始時間，用於 per-channel 耗時統計
        ch_begin = time.time()

        try:
            # 嘗試從快取取得頻道；若沒有，透過 API fetch
            channel = client.get_channel(ch_id) or await client.fetch_channel(ch_id)
        except Exception as e:
            # 若頻道取得失敗，印出錯誤並寫入 LOG，跳過此頻道
            msg = f"fetch_channel_failed channel={ch_id} err=" + " ".join(str(e).splitlines())
            print(f"[DEDUPE] {msg}")
            write_dedupe_log("dedupe_error", source, detail=msg)
            continue

        # 每頻道開始 LOG：標記此頻道即將開始掃描，附帶 limit
        write_dedupe_log("dedupe_channel_begin", source, detail=f"channel={ch_id} limit={limit}")

        # 用 set 記錄已見過的「完整文字內容」，第一個出現者保留，其後相同者刪除
        seen = set()

        # 此頻道的統計：刪除數與掃描數
        deleted = 0
        scanned = 0

        # 計數器：本頻道已輸出的逐則 LOG 數量，用於控制在 verbose_cap_per_channel 以內
        per_msg_logged = 0

        try:
            # 非同步迭代：抓取此頻道最近 limit 則訊息
            async for msg in channel.history(limit=limit):
                # 每抓到一則訊息，先增加掃描數
                scanned += 1

                # 在 verbose 模式下，記錄基礎掃描狀態（訊息類型、是否有文字內容）
                if verbose and per_msg_logged < verbose_cap_per_channel:
                    per_msg_logged += 1

                # 抽取並標準化文字內容：去除前後空白，None 轉空字串
                content = (msg.content or "").strip()

                # 若沒有文字內容（例如只有附件或嵌入），不納入去重，直接略過
                if not content:
                    if verbose and per_msg_logged < verbose_cap_per_channel:
                        per_msg_logged += 1
                    continue

                # 只處理「一般訊息」；系統訊息、pin、thread 事件等型別一律略過
                if msg.type != discord.MessageType.default:
                    if verbose and per_msg_logged < verbose_cap_per_channel:
                        per_msg_logged += 1
                    continue

                # 使用完整文字內容作為去重鍵（完全一致才算重複）
                key = content

                # 若此內容已經出現過，代表這則是重複者，嘗試刪除
                if key in seen:
                    try:
                        # 刪除訊息（需要頻道刪除權限）
                        await msg.delete()
                        # 刪除成功，增加刪除統計
                        deleted += 1
                        # 詳細 LOG：刪除成功
                        if verbose and per_msg_logged < verbose_cap_per_channel:
                            write_dedupe_log(
                                "dedupe_msg_deleted",
                                source,
                                detail=f"channel={ch_id} msg_id={msg.id}"
                            )
                            per_msg_logged += 1
                    except discord.Forbidden:
                        # 權限不足：無法刪除訊息
                        if verbose and per_msg_logged < verbose_cap_per_channel:
                            write_dedupe_log(
                                "dedupe_msg_delete_forbidden",
                                source,
                                detail=f"channel={ch_id} msg_id={msg.id}"
                            )
                            per_msg_logged += 1
                    except discord.HTTPException as he:
                        # HTTP 相關失敗（例如速率限制、API 錯誤）
                        if verbose and per_msg_logged < verbose_cap_per_channel:
                            write_dedupe_log(
                                "dedupe_msg_delete_http_error",
                                source,
                                detail=f"channel={ch_id} msg_id={msg.id} err=" + " ".join(str(he).splitlines())
                            )
                            per_msg_logged += 1
                else:
                    # 第一次看到此內容：加入 seen，視為保留的原始訊息
                    seen.add(key)
                    if verbose and per_msg_logged < verbose_cap_per_channel:
                        per_msg_logged += 1

            # 每頻道掃描完成：印出控制台摘要
            print(f"[DEDUPE] channel={ch_id} scanned={scanned} deleted={deleted}")

            # 每頻道結束 LOG：包含掃描數、刪除數與耗時
            write_dedupe_log(
                "dedupe_channel_end",
                source,
                detail=f"channel={ch_id} scanned={scanned} deleted={deleted} elapsed={round(time.time()-ch_begin,2)}s"
            )

            # 更新全域統計
            total_deleted += deleted
            total_scanned += scanned

        except Exception as e:
            # 掃描迴圈中任何未預期例外：記錄並繼續下一個頻道
            msg = f"scan_failed channel={ch_id} err=" + " ".join(str(e).splitlines())
            print(f"[DEDUPE] {msg}")
            write_dedupe_log("dedupe_error", source, detail=msg)

    # 全域結束 LOG：輸出總掃描數、總刪除數與總耗時
    write_dedupe_log(
        "dedupe_done",
        source,
        detail=f"total_scanned={total_scanned} total_deleted={total_deleted} elapsed={round(time.time()-started_at,2)}s"
    )

    # 回傳總刪除數，供呼叫端顯示或後續決策使用
    return total_deleted

# ===== AsaBot：IG/X 清理 + 媒體限定監控 + 去重指令 + 連線檢查 =====
class AsaBot(discord.Client):
    def __init__(self, *args, **kwargs):
        # 初始化：記錄啟動時間，以便回覆 !ping
        super().__init__(*args, **kwargs)
        self.started_at = time.time()

    async def on_ready(self):
        # Bot 登入成功後：
        # - 印出登入身分
        # - 啟動 heartbeat 背景任務（固定間隔輸出心跳）
        # - 若設定 AUTO_DEDUPE_ON_START，啟動一次去重掃描
        print(f"[READY] AsaBot logged in as {self.user}")
        asyncio.create_task(self.heartbeat())
        if AUTO_DEDUPE_ON_START:
            asyncio.create_task(self.run_dedupe_once())

    async def heartbeat(self):
        # 心跳背景任務：每 HEARTBEAT_INTERVAL_SEC 秒輸出一次時間戳（健康檢查用途）
        while True:
            print(f"[HEARTBEAT-AsaBot] {time.strftime('%Y-%m-%d %H:%M:%S')}")
            await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)

    async def run_dedupe_once(self):
        # 啟動時自動掃描去重的頻道集合（可依需求調整）
        channel_ids = [
            CHANNEL_SHARING_GIRL, CHANNEL_SHARING_BOY, CHANNEL_INJURIED,
            CHANNEL_GAME_BOX, CHANNEL_CONTRACT, CHANNEL_INTELLIGENCE_NEWS
        ]
        total = await delete_duplicate_messages(self, channel_ids, DUPLICATE_SCAN_LIMIT, source="auto.Asabot")
        print(f"[DEDUPE] finished on start. total_deleted={total}")

    async def on_message(self, message: discord.Message):
        # 事件：收到新訊息
        try:
            if message.author.id == self.user.id:
                return  # 忽略自己發出的訊息，避免自觸發

            content = (message.content or "").strip().lower()

            # 連線檢查指令：!ping -> 回覆延遲、啟動時間、心跳間隔
            if content == "!ping":
                latency_ms = round(self.latency * 1000) if self.latency is not None else -1
                started = _ts(self.started_at)  # 將 epoch 轉可讀字串（假設 _ts 已定義）
                await message.channel.send(f"Pong! 延遲: {latency_ms} ms | 啟動時間: {started} | 心跳: {HEARTBEAT_INTERVAL_SEC}s")
                return

            # 手動去重：!dedupe（需 Manage Messages 或管理員）
            if content == "!dedupe":
                perms = message.channel.permissions_for(message.author)
                if not (perms.manage_messages or perms.administrator):
                    await message.reply("需要 Manage Messages 權限才能執行去重。")
                    return
                await message.channel.send("開始去重，請稍候...")
                channel_ids = [
                    CHANNEL_SHARING_GIRL, CHANNEL_SHARING_BOY, CHANNEL_INJURIED,
                    CHANNEL_GAME_BOX, CHANNEL_CONTRACT, CHANNEL_INTELLIGENCE_NEWS
                ]
                total = await delete_duplicate_messages(self, channel_ids, DUPLICATE_SCAN_LIMIT, source="manual.Asabot")
                await message.channel.send(f"去重完成，刪除重複訊息共 {total} 則。")
                return

            # IG/X 連結清理（不限制頻道）：偵測原始連結並回覆對應的「乾淨」頁面
            if content:
                replies = []

                # Instagram -> kkinstagram
                for match in INSTAGRAM_URL_PATTERN.finditer(message.content or ""):
                    cleaned = f"https://www.kkinstagram.com/{match.group(2)}/{match.group(3)}/"
                    replies.append(cleaned)

                # Twitter/X -> fxtwitter
                for match in TWITTER_URL_PATTERN.finditer(message.content or ""):
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
                    # 使用 dict.fromkeys 去重並保留原順序，再一次性回覆
                    unique_replies = list(dict.fromkeys(replies))
                    await message.channel.send("\n".join(unique_replies))

            # 媒體限定監控（僅針對特定禁聊頻道）：無媒體則刪文並提示
            if message.channel.id in TARGET_MEDIA_CHANNELS:
                # 判斷附件是否為圖片/影片（透過 content_type 或副檔名）
                has_attachment_media = any(
                    (att.content_type or "").startswith("image/")
                    or (att.content_type or "").startswith("video/")
                    or (att.filename or "").lower().endswith(tuple(IMG_EXT | VID_EXT))
                    for att in message.attachments
                )
                # 判斷文字中的 URL 是否屬於可嵌入媒體站或具媒體副檔名
                urls = URL_PATTERN.findall(message.content or "")
                has_media_url = any(is_media_url(u) for u in urls)

                if not (has_attachment_media or has_media_url):
                    # 嘗試刪除訊息；若無權限，給出臨時警告訊息
                    try:
                        await message.delete()
                    except discord.Forbidden:
                        # 缺刪除權限：發一則 5 秒後自刪的告知訊息
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

                    # 刪除成功：再發一則點名的提示，5 秒後自刪
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
            # on_message 最外層保護，避免單筆錯誤中斷事件處理
            print(f"[ERROR-AsaBot] on_message: {e}")

# ========== 你的 YouTube 監控迴圈（已加完整 LOG） ==========
async def youtube_monitor_loop():
    # 啟動監控：印出啟動訊息與紀錄 LOG，方便在系統啟動時追蹤
    print("[YT] monitor starting...")
    yt_log("YT_MONITOR_START", f"channel={YOUTUBE_CHANNEL_ID}")

    # 1) 基本設定檢查：若缺少 YOUTUBE_CHANNEL_ID，直接結束函式
    if not YOUTUBE_CHANNEL_ID:
        print("[YT] YOUTUBE_CHANNEL_ID missing, return")
        # 記錄錯誤，便於排查配置問題
        yt_log("YT_CONFIG_MISSING", "YOUTUBE_CHANNEL_ID missing", level="ERROR")
        return

    # 2) 建立 YouTube 服務物件（API Client）
    try:
        youtube = _yt_build_service()
        print("[YT] service built")
        # 紀錄服務建立成功
        yt_log("YT_SERVICE_BUILT", "ok")
    except Exception as e:
        # 建立服務失敗：印出錯誤並記錄 LOG，然後結束函式
        print(f"[YT] build service failed: {e}")
        yt_log("YT_SERVICE_BUILD_FAIL", str(e), level="ERROR")
        return

    # 3) 取得指定頻道的「上傳影片播放清單」ID
    uploads_playlist_id = _yt_get_channel_uploads_playlist_id(youtube, YOUTUBE_CHANNEL_ID)
    if not uploads_playlist_id:
        # 若取不到播放清單，表示頻道或 API 權限有問題，直接返回
        msg = f"channel={YOUTUBE_CHANNEL_ID}"
        print(f"[YT] uploads playlist not found for {msg}, return")
        yt_log("YT_PLAYLIST_NOT_FOUND", msg, level="ERROR")
        return

    # 成功取得播放清單：印出與記錄 OK
    print(f"[YT] monitoring uploads playlist: {uploads_playlist_id}")
    yt_log("YT_PLAYLIST_OK", uploads_playlist_id)

    # 新增：重建旗標
    need_rebuild = False

    # 4) 主要監控迴圈：持續輪詢該播放清單
    while True:
        # 每輪開始，印出輪詢起點並紀錄時間戳，方便觀測輪詢節奏
        print("[YT] poll begin")
        yt_log("YT_POLL_BEGIN", _now_ts_str(), dedupe_key="YT_POLL_BEGIN", dedupe_ttl_sec=5)

        # 若上次遇到 quotaExceeded，睡醒後先重建再繼續
        # 說明：
        # - need_rebuild 只會在上一輪捕捉到 quotaExceeded 時被設為 True
        # - 這裡放在每輪 while 的開頭，確保「睡醒後」第一件事就是重建 service
        # - 好處：語意一致（先休眠等待配額重置，再以乾淨 client 重新開始）
        if need_rebuild:
            try:
                # 重新建立 YouTube API client（service）
                # 目的：
                # - 清除可能的過期/受限狀態（token、session、連線池）
                # - 避免延續前一輪的內部錯誤狀態
                # 風險：
                # - 可能因為憑證或網路問題而失敗（下方 catch 處理）
                youtube = _yt_build_service()

                # 重取 uploads_playlist_id（上傳清單 ID）
                # 原因：
                # - 雖然大多數情況不會改變，但在某些授權/資源刷新情境，
                #   重新取得一次可保險，避免使用舊的引用造成 404 或權限錯誤
                # 策略：
                # - 若新取值為 None，則 fallback 回舊的 uploads_playlist_id，避免中斷
                uploads_playlist_id = _yt_get_channel_uploads_playlist_id(youtube, YOUTUBE_CHANNEL_ID) or uploads_playlist_id

                # 記錄成功重建的訊息與語意時間點（after sleep）
                # 方便日後從 log 上還原時序與排查「重建是否真的在睡醒後發生」
                print("[YT] service rebuilt after sleep")
                yt_log("YT_REBUILT", "after sleep")

                # 重建成功後清除旗標，回到正常輪詢流程
                need_rebuild = False

            except Exception as re:
                # 捕捉任何重建流程中的例外（例如：網路暫失、OAuth 失效、內部錯誤）
                remsg = str(re)
                # 在 console 與 log 中都留下明確訊息與退避策略，方便告警與觀察
                print(f"[YT] rebuild failed: {re}, backoff 60s")
                yt_log("YT_REBUILD_FAIL", remsg, level="ERROR")

                # 策略說明：
                # - 重建失敗時，採用短退避（60 秒）後再試，而不是直接進入本輪主流程
                # - 原因：service 未就緒時，後續 API 呼叫大機率仍會失敗，提早短暫等待可降低無謂錯誤與 log 噪音
                # - 同時保留 need_rebuild=True，使得下一次醒來仍會從「先重建」開始
                await asyncio.sleep(60)

                # 繼續下一輪 while（跳過本輪後續的抓資料邏輯）
                continue

        # 本輪「睡眠秒數」的變數，分支決定值，finally 統一執行睡眠
        sleep_seconds = None

        try:
            # 4.1) 從播放清單抓取最新的 10 部影片資料（標題、ID、URL 等）
            items = _yt_get_latest_videos_from_playlist(youtube, uploads_playlist_id, max_results=10)
            count_msg = f"count={len(items)}"
            print(f"[YT] fetched={len(items)}")
            # 紀錄抓取數量，便於觀察 API 回傳是否異常
            yt_log("YT_FETCHED", count_msg, dedupe_key="YT_FETCHED", dedupe_ttl_sec=10)

            # 4.2) 載入上次檢查時保存的影片資料（本地快取或資料檔）
            last = _yt_load_last_checked()
            # 將舊資料中的影片 ID 收集成集合，用來比對是否有新影片
            last_ids = {_extract_id(it) for it in last if _extract_id(it)}
            # 4.3) 用本次抓取的 items 與 last_ids 比對，找出「未見過」的影片
            new_items = [it for it in items if _extract_id(it) not in last_ids]

            # 4.4) 若沒有新影片：
            if not new_items:
                # 記錄「沒有新影片」的 LOG，含時間戳，方便觀測空轉情形
                yt_log("YT_NO_NEW", f"ts={_now_ts_str()}", dedupe_key="YT_NO_NEW", dedupe_ttl_sec=30)
                # 仍保存目前抓到的 10 部，保持快取新鮮度（避免舊資料殘留）
                _yt_save_last_checked(items)
                # 設定常規睡眠間隔（例如每 N 秒再檢查一次）
                sleep_seconds = YT_CHECK_INTERVAL_SECONDS
                # 跳到 finally，由 finally 統一執行睡眠與 LOG
                continue

            # 4.5) 有新影片：依照發佈時間排序，從舊到新逐一通知（避免逆序造成通知混亂）
            for it in _sort_by_published(new_items):
                # 安全提取影片 ID（可能來源欄位不同）
                vid = _extract_id(it)
                # 安全提取標題：先看 it["title"]，再回退到 snippet.title，最後給預設字串
                title = it.get("title") or it.get("snippet", {}).get("title") or "(no title)"
                # 安全組合 URL：若物件已有 url 用它，否則用 ID 組 YouTube 網址；若無 ID 則給空字串
                url = it.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
                # 發送通知到 Discord（或你指定的通知管道）
                _yt_send_discord_message(title, url)
                # 記錄每一部新影片的通知成功 LOG，並用影片 ID 做去重 key（避免重覆）
                yt_log("YT_NOTIFY_OK", f"{vid} {title}", dedupe_key=f"YT_NOTIFY_OK_{vid}", dedupe_ttl_sec=300)

            # 4.6) 通知完成後，覆蓋保存本次抓到的 10 部，作為下次輪詢的比較基準
            _yt_save_last_checked(items)
            yt_log("YT_SAVED", f"count={len(items)}", dedupe_key="YT_SAVED", dedupe_ttl_sec=30)

            # 4.7) 設定常規睡眠間隔，交由 finally 統一執行
            sleep_seconds = YT_CHECK_INTERVAL_SECONDS

        except HttpError as e:
            # 5) 處理 YouTube API 的 HTTP 層級錯誤（例如配額、憑證、網路等）
            emsg = str(e)
            print(f"[YT] HttpError: {emsg}")
            # 記錄錯誤 LOG，並用 dedupe_key 做一定時間內去重（避免灌爆 LOG）
            yt_log("YT_HTTP_ERROR", emsg, level="ERROR", dedupe_key="YT_HTTP_ERROR", dedupe_ttl_sec=60)

            if "quotaExceeded" in emsg:
                # 5.1) 若判定是配額超限（quotaExceeded），計算距離下一個 15:05 的秒數，按照 YouTube 配額重置策略暫停
                sec = _seconds_until_next_1505()
                # 計算醒來的時間點（現在 + sec）
                wake_dt = datetime.datetime.now() + datetime.timedelta(seconds=sec)
                # 轉成人類可讀時間字串
                wake_str = wake_dt.strftime('%Y-%m-%d %H:%M:%S')
                print(f"[YT] quotaExceeded -> sleep {sec}s until {wake_str}")
                # 記錄暫停與預計醒來時間，便於監控
                yt_log("YT_PAUSE_UNTIL_15_05", f"sleep={sec}s wake={wake_str}")

                # 交由 finally 統一睡眠，但先把睡眠秒數設好
                sleep_seconds = sec

                # 關鍵改動：設定旗標，讓下一輪（睡醒）再重建
                need_rebuild = True

                # 5.1.a) 嘗試在配額睡眠期間之後重建服務（有些情況下重建可以恢復）
                try:
                    youtube = _yt_build_service()
                    # 重新取得上傳清單 ID（有時需要刷新）
                    uploads_playlist_id = _yt_get_channel_uploads_playlist_id(youtube, YOUTUBE_CHANNEL_ID) or uploads_playlist_id
                    print("[YT] service rebuilt after quota sleep")
                    yt_log("YT_REBUILT", "after quota sleep")
                except Exception as re:
                    # 重建失敗：記錄錯誤，並將睡眠秒數改為較短退避（例如 60s），以便快速重試
                    remsg = str(re)
                    print(f"[YT] rebuild failed: {re}, retry in 60s")
                    yt_log("YT_REBUILD_FAIL", remsg, level="ERROR")
                    sleep_seconds = 60
            else:
                # 5.2) 若非配額錯誤：使用固定退避時間（例如 120 秒）以減輕 API 壓力或等待網路恢復
                print("[YT] HttpError non-quota, backoff 120s")
                yt_log("YT_BACKOFF_120S", "HTTP non-quota")
                sleep_seconds = 120

        except Exception as e:
            # 6) 其他未預期的例外（程式邏輯錯誤、型別錯誤、外部服務異常等）
            emsg = str(e)
            print(f"[YT] unexpected error: {emsg}, backoff 120s")
            # 記錄錯誤 LOG，並給 dedupe key 避免短時間內大量重覆
            yt_log("YT_MONITOR_EXCEPTION", emsg, level="ERROR", dedupe_key="YT_MONITOR_EXCEPTION", dedupe_ttl_sec=60)
            # 設定退避睡眠時間，交由 finally 統一睡眠
            sleep_seconds = 120

        finally:
            # 7) 統一的收尾與睡眠：
            # 防呆：如果上面分支忘了設定睡眠秒數，使用常規間隔避免緊密輪詢造成壓力
            if sleep_seconds is None:
                sleep_seconds = YT_CHECK_INTERVAL_SECONDS

            # 印出與記錄這次睡眠秒數，便於追蹤輪詢節奏與退避行為
            sleep_msg = f"{sleep_seconds}s"
            print(f"[YT] sleep {sleep_msg}")
            yt_log("YT_SLEEP", sleep_msg, dedupe_key="YT_SLEEP", dedupe_ttl_sec=10)

            # 真正進入睡眠（非阻塞），讓事件迴圈在這段時間內可處理其他協程
            await asyncio.sleep(sleep_seconds)

# ===== AsaBox：PTT 抓取推送（含錨點 + 日誌 + 自動去重，含日誌）=====
# AsaBox 類別繼承自 discord.Client，負責：
# - 啟動後載入錨點（避免重覆推送）
# - 週期性心跳（可觀測是否存活）
# - 週期性抓取 PTT 資料並分發到指定頻道
# - 支援 "!status" 指令查詢目前抓取狀態
# - 完成後自動去重刪除重覆訊息
class AsaBox(discord.Client):
    def __init__(self, *args, **kwargs):

        # 初始化基類 discord.Client，確保事件迴圈、連線等基礎功能正常
        super().__init__(*args, **kwargs)

        # 建立錨點管理器，用來記錄各分類最後處理到的項目，避免重覆推送
        self.anchor_mgr = ChannelAnchorManager()

        # 記錄 AsaBox 啟動時間（UNIX timestamp），用於日誌與狀態輸出
        self.started_at = time.time()

        # 記錄上次抓取輪次「開始」的時間，None 表示尚未有任何輪次
        self.last_round_started_at: float | None = None
        
        # 記錄上次抓取輪次「完成」的時間，None 表示尚未有任何輪次
        self.last_round_completed_at: float | None = None

        # 旗標：目前是否正在抓取（True 抓取中；False 待機中）
        self.is_fetching: bool = False

        # 啟動日誌：便於在系統層面追蹤 AsaBox 啟動事件
        write_ptt_log(self.started_at, "[PTT-AsaBox] start", None)

    async def on_ready(self):

        # 控制台輸出目前登入帳號，
        # 方便確認機器人身份是否正確
        print(f"[READY] AsaBox logged in as {self.user}")

        # 記錄就緒事件到日誌，
        # 含帳號資訊以便追蹤
        write_ptt_log(self.started_at, f"[READY] AsaBox logged in as {self.user}", None)

        # 從持久化存儲載入各分類錨點，
        # 確保不會重覆推送已處理內容
        await self.anchor_mgr.load_last_anchors(self, started_at=self.started_at)

        # 啟動心跳協程，
        # 定期輸出心跳以觀察服務存活
        asyncio.create_task(self.heartbeat())

        # 啟動 PTT 抓取主迴圈，
        # 週期性抓取並推送到各頻道
        asyncio.create_task(self.ptt_loop())

    async def heartbeat(self):

        # 心跳迴圈，
        # 每 HEARTBEAT_INTERVAL_SEC 秒記錄一次心跳
        while True:
            # 控制台印出心跳時間（人類可讀）
            print(f"[HEARTBEAT-AsaBox] {time.strftime('%Y-%m-%d %H:%M:%S')}")

            # 寫入心跳到日誌，
            # 便於後端檢索與排錯
            write_ptt_log(self.started_at, f"[HEARTBEAT-AsaBox] {time.strftime('%Y-%m-%d %H:%M:%S')}", None)

            # 非阻塞睡眠，
            # 保持事件迴圈流暢
            await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)

    async def on_message(self, message: discord.Message):

        # 忽略機器人與自身訊息，
        # 避免自動回覆造成訊息迴圈
        if message.author.bot or (self.user and message.author.id == self.user.id):
            return

        # 取得訊息內容，
        # 去除前後空白並轉小寫利於比對
        content = (message.content or "").strip().lower()

        # 使用者輸入 "!status" 時，
        # 回覆目前抓取狀態與時間資訊
        if content == "!status":
            # 格式化啟動時間為人類可讀
            started = _ts(self.started_at)

            # 上次輪次起始，
            # 若尚未有輪次則顯示 "N/A"
            last_start = _ts(self.last_round_started_at) if self.last_round_started_at else "N/A"

            # 上次輪次完成，
            # 若尚未有輪次則顯示 "N/A"
            last_done = _ts(self.last_round_completed_at) if self.last_round_completed_at else "N/A"

            # 目前狀態，
            # 依 is_fetching 旗標輸出文字
            state = "抓取中" if self.is_fetching else "待機中"

            # 傳送狀態訊息到目前頻道
            await message.channel.send(
                f"AsaBox 狀態: {state} | 啟動: {started} | 上次起始: {last_start} | 上次完成: {last_done} | 週期: {FETCH_INTERVAL}s"
            )

    async def ptt_loop(self):

        # 建立 HTTP session，
        # 減少重覆建立連線成本（可含重試策略）
        session = make_session()

        # 指定需要進行去重掃描的頻道清單，
        # 用於刪除重覆訊息
        target_channels_for_dedupe = [
            CHANNEL_SHARING_GIRL, CHANNEL_SHARING_BOY, CHANNEL_INJURIED,
            CHANNEL_GAME_BOX, CHANNEL_CONTRACT, CHANNEL_INTELLIGENCE_NEWS, CHANNEL_INS 
        ]

        # 主抓取迴圈，
        # 以固定週期執行一輪抓取與推送
        while True:
            # 記錄本輪開始時間（UNIX timestamp）
            round_start = time.time()

            # 更新「上次起始時間」
            self.last_round_started_at = round_start

            # 標記狀態為「抓取中」
            self.is_fetching = True

            try:
                # 以執行緒跑 collect_today(session)，
                # 避免阻塞事件迴圈（I/O 或 CPU 操作）
                buckets = await asyncio.to_thread(collect_today, session)
                # 分類到頻道的映射，
                # 將不同內容分類對應到不同頻道
                mapping = {
                    "BOX": CHANNEL_GAME_BOX,
                    "INFO_CONTRACT": CHANNEL_CONTRACT,
                    "INFO_INJURIED": CHANNEL_INJURIED,
                    "INFO_OTHER": CHANNEL_INTELLIGENCE_NEWS
                }

                # 逐分類處理推送
                for key, ch_id in mapping.items():

                    # 若頻道 ID 未設定（None 或 0），
                    # 直接跳過該分類
                    if not ch_id:
                        continue

                    # 嘗試從快取取得頻道
                    channel = self.get_channel(ch_id)

                    # 若快取沒有（或不在同 guild），
                    # 以 API 拉取頻道物件
                    if not channel:
                        try:
                            channel = await self.fetch_channel(ch_id)
                        except Exception as e:
                            # 頻道不可存取或拉取失敗，
                            # 輸出警告並記錄日誌，然後跳過
                            print(f"[WARN] Channel not accessible: {ch_id} err={e}")
                            write_ptt_log(self.started_at, f"[WARN] Channel not accessible: {ch_id} err={e}", None)
                            continue

                    print(f"[PTT] category={key} ch_id={ch_id} buckets_count={len(buckets.get(key, []))}")

                    # 從 buckets 取得該分類的今日項目，
                    # 若不存在則為空清單
                    todays_items = buckets.get(key, [])

                    # 拉取該頻道近 20 則訊息，抽取 PTT NBA 基底的 URL
                    seen_urls = await collect_seen_ptt_urls_from_channel(channel, limit=20)

                    # 同輪保險：若你已有 self.sent_urls 作為去重集合，加入避免同輪重覆
                    if hasattr(self, "sent_urls") and isinstance(self.sent_urls, set):
                        seen_urls |= {u for u in self.sent_urls if is_ptt_nba_url(u)}

                    # 過濾：只保留 PTT NBA 基底的 URL，且不在 seen_urls 中
                    to_send = []
                    for it in todays_items:
                        u = it.get("url")
                        if not u:
                            continue
                        if not is_ptt_nba_url(u):
                            # 非目標基底，略過（避免搜尋過多/跨站）
                            continue
                        if u in seen_urls:
                            continue
                        to_send.append(it)

                    print(f"[PTT] to_send count for {key}: {len(to_send)}")

                    # 新增：記錄本分類即將發送的清單
                    if to_send:
                        lines = []
                        for e in to_send[:20]:  # 最多記 20 筆，避免 log 過長
                            d = e.get("full_date","")
                            t = e.get("title_no_prefix") or e.get("title") or ""
                            u = e.get("url","")
                            lines.append(f"{d} | {t} | {u}")
                        write_ptt_log(time.time(), f"[PTT_TO_SEND] cat={key} count={len(to_send)} sample<=20:\t" + " || ".join(lines), None)
                    else:
                        write_ptt_log(time.time(), f"[PTT_TO_SEND] cat={key} count=0", None)

                    # 若沒有需要推送的新項目，
                    # 跳過該分類
                    if not to_send:
                        continue

                    # 準備訊息 payload（文字）列表，
                    # 依分類使用不同格式建構
                    payloads = []
                    for e in to_send:
                        # BOX 類（例如比賽資訊），使用 build_content_box
                        if key == "BOX":
                            payloads.append(
                                build_content_box(
                                    e.get("full_date",""),
                                    e.get("title_no_prefix",""),
                                    e.get("url",""),
                                )
                            )
                        else:
                            # 其他 INFO 類，使用 build_content_info（含分類 key）
                            payloads.append(
                                build_content_info(
                                    e.get("full_date",""),
                                    key,
                                    e.get("title_no_prefix",""),
                                    e.get("url",""),
                                )
                            )

                    # 發文一個連結發一次：逐條送出，不再合併 buffer
                    for p in payloads:
                        # Discord 每則訊息長度限制約 2000 字，單條足夠；保險檢查
                        if len(p) > 1900:
                            # 如超長，可適度截斷標題或僅保留 URL
                            trimmed = p[:1900] + "\n(內容過長已截斷)"
                            await channel.send(trimmed)
                        else:
                            await channel.send(p)

                    # 標記這批項目為已送出錨點，
                    # 供下次增量推送使用
                    self.anchor_mgr.mark_sent(key, to_send)

                # 一輪抓取與推送完成，
                # 控制台提示與日誌記錄
                print("[PTT-AsaBox] one round done (anchor-aware)")
                write_ptt_log(round_start, "[PTT-AsaBox] completed", None)

                # 更新「上次完成時間」
                self.last_round_completed_at = time.time()
                print(f"target_channels_for_dedupe={target_channels_for_dedupe}")
                # 自動去重，
                # 掃描指定頻道刪除重覆訊息（依 source tag）
                total_deleted = await delete_duplicate_messages(
                    self,
                    target_channels_for_dedupe,
                    DUPLICATE_SCAN_LIMIT,
                    source="auto.Asabox",
                )

                # 控制台輸出去重結果
                print(f"[PTT-AsaBox] auto dedupe done. total_deleted={total_deleted}")

            except Exception as e:
                # 抓取迴圈內未預期錯誤，
                # 在控制台與日誌中記錄
                print(f"[ERROR-AsaBox] ptt_loop: {e}")
                write_ptt_log(round_start, "error", str(e))

            finally:
                # 無論成功或失敗，
                # 都將狀態設為「非抓取中」
                self.is_fetching = False

                # 記錄「完成並進入睡眠」的日誌，
                # 便於追蹤週期
                write_ptt_log(round_start, "[PTT-AsaBox] completed, sleep", None)

                # 進入固定抓取週期的睡眠，
                # 下一輪再由事件迴圈喚醒
                await asyncio.sleep(FETCH_INTERVAL)

async def run_bot_with_retry(client, token: str, name: str, retry_delay: int = 30):

    # 無窮重試迴圈，
    # 確保機器人在錯誤後能自動恢復
    while True:
        try:
            # 啟動 Discord 客戶端，
            # 進入連線與事件迴圈
            await client.start(token)
        except Exception as e:
            # 記錄啟動錯誤，
            # 並輸出重試等待秒數
            print(f"[{name}] error: {e}, retry in {retry_delay}s")

            # 寫入錯誤日誌，
            # 帶上目前時間戳與錯誤訊息
            write_ptt_log(time.time(), f"{name}_ERROR", str(e))

            # 睡眠 retry_delay 秒後重試
            await asyncio.sleep(retry_delay)
async def main():

    # 建立兩個 Discord Client 實例：
    # - AsaBot：負責 IG/X 連結清理、媒體限定監控、去重指令與心跳檢查
    # - AsaBox：負責 PTT/NBA 收集與推送（假設在其他段落定義）
    # 備註：intents_bot / intents_box 應已依各自需求設定（如 message_content 權限）
    client_bot = AsaBot(intents=intents_bot)
    client_box = AsaBox(intents=intents_box)

    # 為兩個 bot 分別啟動自動重試的執行任務：
    # - run_bot_with_retry 內部應包含無限重試迴圈（例如斷線/異常時延遲後重連）
    # - 不要在這裡 await，改用 create_task 讓它們並行執行
    bot_task = asyncio.create_task(run_bot_with_retry(client_bot, TOKEN_ASA_BOT, "ASA_BOT"))
    box_task = asyncio.create_task(run_bot_with_retry(client_box, TOKEN_ASA_BOX, "ASA_BOX"))

    # 啟動 YouTube 監控背景任務：
    # - youtube_monitor_loop 內部已處理配額 quotaExceeded 的暫停策略（例如等到 15:05 再繼續）
    # - 重要的是它不會把例外泡到最外層導致主程式退出
    yt_task = asyncio.create_task(youtube_monitor_loop())

    # 等待所有主要任務；使用 return_exceptions=True：
    # - 即使其中一個任務拋出例外，也不會使 gather 直接 raise，而是將例外物件作為結果返回
    # - 這樣可以在下方統一記錄錯誤並繼續存活（若任務本來是無限迴圈則通常不會返回）
    results = await asyncio.gather(bot_task, box_task, yt_task, return_exceptions=True)

    # 收斂與記錄例外：
    # - 理論上 run_bot_with_retry 這兩個任務應該是常駐不返回，除非遇到不可回復錯誤
    # - 若有例外，印出並寫入日誌，但不主動退出程式
    for i, res in enumerate(results, start=1):
        if isinstance(res, Exception):
            print(f"[MAIN] task {i} error: {res}")
            write_ptt_log(time.time(), "MAIN_TASK_ERROR", str(res))

# 以 asyncio.run 作為進入點執行 main()
# - 注意：在某些環境（例如已存在事件迴圈的環境或嵌入式 REPL）可能需要用 nest_asyncio 或其他方式處理
if __name__ == "__main__":
    asyncio.run(main())
