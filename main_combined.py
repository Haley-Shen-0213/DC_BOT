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
    # 若是打包為 exe，使用 exe 所在目錄；否則使用腳本所在目錄
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent

BASE_DIR = get_base_dir()
ENV_PATH = BASE_DIR / ".env"

# 明確載入 .env（若不存在也不報錯，但稍後會檢查）
load_dotenv(dotenv_path=str(ENV_PATH), override=False)

# ===== 檔案與日誌設定 =====
LOG_DIR = BASE_DIR / "log"
os.makedirs(LOG_DIR, exist_ok=True)
def get_daily_log_file() -> Path:
    # 依日期分檔：ptt_asabox_YYYY-MM-DD.log
    date_str = datetime.date.today().strftime("%Y-%m-%d")
    return LOG_DIR / f"ptt_asabox_{date_str}.log"

def _ts(ts: float | None = None) -> str:
    ts = ts or time.time()
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

def write_ptt_log(start_time: float, status: str, error_message: str | None = None):
    ts_iso = datetime.datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts_iso}\t{status}"
    if error_message:
        msg = " ".join(str(error_message).splitlines())
        line += f"\t{msg}"
    line += "\n"
    log_file = get_daily_log_file()
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line)

def write_dedupe_log(event: str, source: str, detail: str | None = None, ts: float | None = None):
    tstr = _ts(ts)
    line = f"{tstr}\t{event}\t{source}"
    if detail:
        line += f"\t{detail}"
    line += "\n"
    log_file = get_daily_log_file()
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line)

def _ts(ts: float | None = None) -> str:
    ts = ts or time.time()
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

def write_ptt_log(start_time: float, status: str, error_message: str | None = None):
    ts_iso = datetime.datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts_iso}\t{status}"
    if error_message:
        msg = " ".join(str(error_message).splitlines())
        line += f"\t{msg}"
    line += "\n"
    with open(get_daily_log_file(), "a", encoding="utf-8") as f:
        f.write(line)

def write_dedupe_log(event: str, source: str, detail: str | None = None, ts: float | None = None):
    tstr = _ts(ts)
    line = f"{tstr}\t{event}\t{source}"
    if detail:
        line += f"\t{detail}"
    line += "\n"
    with open(get_daily_log_file(), "a", encoding="utf-8") as f:
        f.write(line)

# 兩個 Token（必填）
TOKEN_ASA_BOT = os.getenv("TOKEN_ASA_BOT") or os.getenv("TOKEN_ASA_BOT")
TOKEN_ASA_BOX = os.getenv("TOKEN_ASA_BOX") or os.getenv("TOKEN_ASA_BOX")
if not TOKEN_ASA_BOT or not TOKEN_ASA_BOX:
    raise RuntimeError("Missing tokens. Please set DISCORD_TOKEN_ASA_BOT and DISCORD_TOKEN_ASA_BOX in environment.")

# ===== 頻道 IDs（請在 .env 設定）=====
CHANNEL_SHARING_GIRL = int(os.getenv("CHANNEL_SHARING_GIRL", "0") or 0)
CHANNEL_SHARING_BOY  = int(os.getenv("CHANNEL_SHARING_BOY", "0") or 0)
CHANNEL_INJURIED = int(os.getenv("CHANNEL_INJURIED", "0") or 0)
CHANNEL_GAME_BOX = int(os.getenv("CHANNEL_GAME_BOX", "0") or 0)
CHANNEL_CONTRACT = int(os.getenv("CHANNEL_CONTRACT", "0") or 0)
CHANNEL_INTELLIGENCE_NEWS = int(os.getenv("CHANNEL_INTELLIGENCE_NEWS", "0") or 0)

TARGET_MEDIA_CHANNELS = {CHANNEL_SHARING_GIRL, CHANNEL_SHARING_BOY} - {0}

# 去重掃描設定
DUPLICATE_SCAN_LIMIT = int(os.getenv("DUPLICATE_SCAN_LIMIT", "1000"))
AUTO_DEDUPE_ON_START = os.getenv("AUTO_DEDUPE_ON_START", "false").lower() == "true"

# 心跳（兩邊各自有）
HEARTBEAT_INTERVAL_SEC = int(os.getenv("HEARTBEAT_INTERVAL_SEC", "3600"))

# PTT 設定（AsaBox 使用）
BASE_URL = "https://www.ptt.cc"
INDEX_URL = os.getenv("PTT_URL", "https://www.ptt.cc/bbs/NBA/index.html")
FETCH_INTERVAL = int(os.getenv("PTT_FETCH_INTERVAL_SEC", "900"))
MAX_PAGES = int(os.getenv("PTT_MAX_PAGES", "12"))
ONLY_TODAY = os.getenv("PTT_ONLY_TODAY", "true").lower() == "true"
STOP_AT_FIRST_OLDER = os.getenv("PTT_STOP_AT_FIRST_OLDER", "true").lower() == "true"
TARGET_PREFIXES = [p.strip() for p in os.getenv("PTT_TARGET_PREFIXES", "BOX,情報").split(",") if p.strip()]
KEYWORDS_INJURY = [w.strip() for w in os.getenv("KEYWORDS_INJURY", "").split(",") if w.strip()]
CONTRACT_PATTERNS = [p.strip() for p in os.getenv("KEYWORDS_CONTRACT_PATTERNS", "").split(";") if p.strip()]
NEGATIVE_FOR_CONTRACT_TITLE = [w.strip() for w in os.getenv("NEGATIVE_FOR_CONTRACT_TITLE", "").split(",") if w.strip()]

# 從 .env 讀取（注意你的環境變數大小寫）
YOUTUBE_CHANNEL_ID = os.getenv("Youtube_CHANNEL_ID", "").strip()
YOUTUBE_API_KEY = os.getenv("Youtube_API_KEY", "").strip()
DISCORD_WEBHOOK_URL_YT = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

LAST_CHECKED_FILE = BASE_DIR / (os.getenv("LAST_CHECKED_FILE", "last_checked_videos.json"))
YT_CHECK_INTERVAL_SECONDS = int(os.getenv("YT_CHECK_INTERVAL_SECONDS", "3600"))  # 每小時

# Intents
intents_bot = discord.Intents.default()
intents_bot.message_content = True
intents_bot.guilds = True
intents_bot.messages = True

intents_box = discord.Intents.default()
intents_box.message_content = True
intents_box.guilds = True
intents_box.messages = True

# ===== IG / X 連結規則（AsaBot 用）=====
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

# 全域簡單去重快取（記憶最近一次寫入的 key 與時間）
_LOG_DEDUPE_CACHE = {}
_LOG_LOCK = threading.Lock()

def _ensure_dir(path: str):
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass

def _now_ts_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _default_log_path():
    base = os.path.join(os.getcwd(), "logs", "yt")
    _ensure_dir(base)
    fname = datetime.datetime.now().strftime("%Y-%m-%d") + ".log"
    return os.path.join(base, fname)

def log_event(tag: str, source: str, message: str, *,
              level: str = "INFO",
              file_path: str | None = None,
              dedupe_key: str | None = None,
              dedupe_ttl_sec: int = 30,
              also_print: bool = True):
    """
    通用事件記錄：
    - tag: 事件類型（例如 YT_MONITOR_START, YT_NO_NEW, YT_HTTP_ERROR）
    - source: 來源系統（例如 'YouTube'）
    - message: 文字內容（建議含可變資訊：id/url/秒數）
    - level: INFO/WARN/ERROR
    - file_path: 指定要寫入的檔案路徑；不指定則使用預設 logs/yt/YYYY-MM-DD.log
    - dedupe_key: 用於去重的鍵；相同鍵於 TTL 內只寫一次，避免洗版
    - dedupe_ttl_sec: 去重 TTL 秒數（預設 30 秒）
    - also_print: 同步印到 console
    """
    ts = _now_ts_str()
    line = f"{ts}\t{level}\t{tag}\t{source}\t{message}"

    # 去重判斷
    if dedupe_key:
        with _LOG_LOCK:
            last_when = _LOG_DEDUPE_CACHE.get(dedupe_key)
            now_epoch = time.time()
            if last_when and (now_epoch - last_when) < dedupe_ttl_sec:
                return
            _LOG_DEDUPE_CACHE[dedupe_key] = now_epoch

    # 寫檔
    path = file_path or _default_log_path()
    try:
        _ensure_dir(os.path.dirname(path))
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        if also_print:
            print(f"[LOG] write failed: {e} path={path}")

    # console
    if also_print:
        print(f"[LOG] {line}")

def yt_log(tag: str, message: str, *, level: str = "INFO",
           dedupe_key: str | None = None, dedupe_ttl_sec: int = 30):
    log_event(tag=tag, source="YouTube", message=message,
              level=level, dedupe_key=dedupe_key, dedupe_ttl_sec=dedupe_ttl_sec)
    
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

def _seconds_until_next_1505(now: datetime.datetime | None = None) -> int:
    now = now or datetime.datetime.now()
    target_today = now.replace(hour=15, minute=5, second=0, microsecond=0)
    target = target_today if now <= target_today else (target_today + datetime.timedelta(days=1))
    return max(1, int((target - now).total_seconds()))

def _yt_build_service():
    if not YOUTUBE_API_KEY:
        raise RuntimeError(f"Missing Youtube_API_KEY in .env at {ENV_PATH}")
    return build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)

def _yt_get_channel_uploads_playlist_id(youtube, channel_id: str) -> str | None:
    resp = youtube.channels().list(part="contentDetails", id=channel_id).execute()
    items = resp.get('items') or []
    if items:
        return items[0]['contentDetails']['relatedPlaylists']['uploads']
    return None

def _yt_get_latest_videos_from_playlist(youtube, playlist_id: str, max_results: int = 10) -> list[dict]:
    resp = youtube.playlistItems().list(
        part="snippet,contentDetails",
        playlistId=playlist_id,
        maxResults=max_results
    ).execute()
    videos = []
    for item in resp.get('items', []):
        vid = item['contentDetails']['videoId']
        title = item['snippet']['title']
        published_at = item['snippet']['publishedAt']
        videos.append({"id": vid, "title": title, "publishedAt": published_at, "url": f"https://www.youtube.com/watch?v={vid}"})
    return videos

def _yt_load_last_checked() -> list[dict]:
    try:
        if LAST_CHECKED_FILE.exists():
            with open(LAST_CHECKED_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return []

def _yt_save_last_checked(videos: list[dict]):
    try:
        with open(LAST_CHECKED_FILE, 'w', encoding='utf-8') as f:
            json.dump(videos, f, ensure_ascii=False, indent=4)
    except Exception as e:
        write_ptt_log(time.time(), "YT_SAVE_LAST_CHECKED_FAILED", str(e))

def _yt_send_discord_message(title: str, url: str):
    if not DISCORD_WEBHOOK_URL_YT:
        write_ptt_log(time.time(), "YT_WEBHOOK_MISSING", "DISCORD_WEBHOOK_URL not set")
        return
    payload = {"content": f"{title}\n{url}"}
    try:
        r = requests.post(DISCORD_WEBHOOK_URL_YT, json=payload, timeout=10)
        if r.status_code not in (200, 204):
            write_ptt_log(time.time(), "YT_WEBHOOK_FAIL", f"{r.status_code} {r.text}")
    except Exception as e:
        write_ptt_log(time.time(), "YT_WEBHOOK_EXCEPTION", str(e))

def _extract_id(item: dict) -> str | None:
    # 你的 items 來自 _yt_get_latest_videos_from_playlist，已經有 "id"
    # 若來源不同時也相容 contentDetails.videoId
    return item.get("id") or item.get("contentDetails", {}).get("videoId")

def _parse_ts(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0

def _sort_by_published(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda it: _parse_ts(it.get("publishedAt") or it.get("snippet", {}).get("publishedAt")))

# ===== 媒體限定監控規則（AsaBot 用）=====
IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VID_EXT = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}
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
        return any(host.endswith(h) for h in EMBED_HOSTS)
    except Exception:
        return False

# ===== PTT/NBA 工具（AsaBox 用）=====
def make_session():
    s = requests.Session()
    s.cookies.set('over18', '1', domain='.ptt.cc')
    s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; PTTFetcher/2.1)"})
    return s

def fetch_page(session, url):
    resp = session.get(url, timeout=10)
    resp.raise_for_status()
    return resp.text

def extract_bracket_prefix(title: str):
    t = title.strip()
    if len(t) >= 3 and t[0] == '[':
        close_idx = t.find(']')
        if close_idx != -1:
            inner = ''.join(t[1:close_idx].strip().split())
            remaining = t[close_idx+1:].lstrip()
            return (inner if inner else None), remaining
    return None, t

def ptt_date_to_full_date(mmdd: str, today: datetime.date):
    parts = (mmdd or "").strip().split('/')
    if len(parts) != 2:
        return None
    try:
        m = int(parts[0].strip()); d = int(parts[1].strip())
        return datetime.date(today.year, m, d).strftime("%Y/%m/%d")
    except ValueError:
        return None

def parse_entries(html: str, today: datetime.date, stop_at_first_older: bool):
    soup = BeautifulSoup(html, "html.parser")
    rlist = soup.select("div.r-list-container div.r-ent")
    results = []
    for ent in rlist:
        title_div = ent.select_one("div.title")
        date_div = ent.select_one("div.meta > div.date")
        if not title_div or not date_div:
            continue
        a = title_div.find("a")
        if not a:
            continue
        title_text = a.get_text(strip=True)
        prefix, remaining_title = extract_bracket_prefix(title_text)
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
        if ONLY_TODAY and stop_at_first_older and full_date and full_date != today.strftime("%Y/%m/%d"):
            break
    return results

def find_prev_page_url(html: str):
    soup = BeautifulSoup(html, "html.parser")
    paging = soup.select_one("div.btn-group-paging")
    if not paging:
        return None
    for a in paging.select("a.btn.wide[href]"):
        text = a.get_text(strip=True)
        href = a["href"]
        if "上頁" in text and "index" in href and href.endswith(".html"):
            return urljoin(BASE_URL, href)
    return None

def filter_by_target_prefix(items, target_prefixes):
    if not target_prefixes:
        return items
    return [it for it in items if (it.get("prefix") or "") in target_prefixes]

def is_injury(title: str) -> bool:
    tl = (title or "").lower()
    return any(kw.lower() in tl for kw in KEYWORDS_INJURY)

def is_contract(title: str) -> bool:
    t = title or ""
    tl = t.lower()
    if any(neg.lower() in tl for neg in NEGATIVE_FOR_CONTRACT_TITLE):
        return False
    for pat in CONTRACT_PATTERNS:
        if re.search(pat, t, flags=re.IGNORECASE):
            return True
    return False

def classify_info(title: str) -> str:
    if is_injury(title):
        return "INFO_INJURIED"
    if is_contract(title):
        return "INFO_CONTRACT"
    return "INFO_OTHER"

def build_content_box(full_date: str, title_no_prefix: str, url: str):
    return f"{full_date}\n[BOX] {title_no_prefix}\n{url}"

def build_content_info(full_date: str, info_type: str, title_no_prefix: str, url: str):
    label_map = {
        "INFO_CONTRACT": "情報-合約/交易",
        "INFO_INJURIED": "情報-受傷",
        "INFO_OTHER": "情報-其他",
    }
    label = label_map.get(info_type, "情報")
    return f"{full_date}\n[{label}] {title_no_prefix}\n{url}"

def collect_today(session):
    today = datetime.date.today()
    today_str = today.strftime("%Y/%m/%d")

    current_url = INDEX_URL
    pages = 0
    buckets = {"BOX": [], "INFO_CONTRACT": [], "INFO_INJURIED": [], "INFO_OTHER": []}

    while current_url and pages < MAX_PAGES:
        html = fetch_page(session, current_url)
        entries = parse_entries(html, today=today, stop_at_first_older=STOP_AT_FIRST_OLDER)

        entries_today = [e for e in entries if e.get("full_date") == today_str]
        entries_today = filter_by_target_prefix(entries_today, TARGET_PREFIXES)

        for e in entries_today:
            if e.get("prefix") == "BOX":
                buckets["BOX"].append(e)
            elif e.get("prefix") == "情報":
                k = classify_info(e.get("title", ""))
                buckets[k].append(e)

        if STOP_AT_FIRST_OLDER:
            if entries and (len(entries_today) < len(entries)):
                break

        prev_url = find_prev_page_url(html)
        if not prev_url:
            break
        current_url = prev_url
        pages += 1

    return buckets

# ===== 頻道錨點與去重管理（AsaBox 用）=====
ANCHOR_URL_REGEX = re.compile(r'https?://[^\s]+', re.IGNORECASE)

class ChannelAnchorManager:
    def __init__(self):
        self.map = {
            "BOX": {"channel_id": CHANNEL_GAME_BOX, "last_url": None},
            "INFO_CONTRACT": {"channel_id": CHANNEL_CONTRACT, "last_url": None},
            "INFO_INJURIED": {"channel_id": CHANNEL_INJURIED, "last_url": None},
            "INFO_OTHER": {"channel_id": CHANNEL_INTELLIGENCE_NEWS, "last_url": None},
        }
        self.sent_urls = set()

    async def load_last_anchors(self, client: discord.Client):
        for key, rec in self.map.items():
            ch_id = rec["channel_id"]
            if not ch_id:
                continue
            try:
                channel = client.get_channel(ch_id) or await client.fetch_channel(ch_id)
            except Exception as e:
                print(f"[WARN] fetch_channel({ch_id}) failed: {e}")
                continue

            last_url = None
            try:
                async for msg in channel.history(limit=100):
                    if msg.author.bot:
                        m = ANCHOR_URL_REGEX.search(msg.content or "")
                        if m:
                            last_url = m.group(0)
                            break
                rec["last_url"] = last_url
                if last_url:
                    self.sent_urls.add(last_url)
                print(f"[ANCHOR] {key} channel={ch_id} last_url={last_url}")
            except Exception as e:
                print(f"[WARN] load_last_anchor for {key} failed: {e}")

    def stop_when_hit_anchor(self, key: str, items: list):
        rec = self.map.get(key)
        if not rec:
            return []
        anchor = rec.get("last_url")
        if not items:
            return []
        collected = []
        for it in items:
            u = it.get("url")
            if not u:
                continue
            if anchor and u == anchor:
                break
            if u in self.sent_urls:
                continue
            collected.append(it)
        return collected

    def mark_sent(self, key: str, items: list):
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
async def delete_duplicate_messages(client: discord.Client, channel_ids: list[int], limit: int = DUPLICATE_SCAN_LIMIT, source: str = "auto.Asabox"):
    started_at = time.time()
    write_dedupe_log("dedupe_start", source, detail=f"channels={','.join(str(cid) for cid in channel_ids if cid)} limit={limit}", ts=started_at)

    total_deleted = 0
    for ch_id in channel_ids:
        if not ch_id:
            continue
        try:
            channel = client.get_channel(ch_id) or await client.fetch_channel(ch_id)
        except Exception as e:
            msg = f"fetch_channel_failed channel={ch_id} err=" + " ".join(str(e).splitlines())
            print(f"[DEDUPE] {msg}")
            write_dedupe_log("dedupe_error", source, detail=msg)
            continue

        seen = set()
        deleted = 0
        try:
            async for msg in channel.history(limit=limit):
                content = (msg.content or "").strip()
                if not content:
                    continue
                if msg.type != discord.MessageType.default:
                    continue
                key = content
                if key in seen:
                    with contextlib.suppress(discord.Forbidden, discord.HTTPException):
                        await msg.delete()
                        deleted += 1
                else:
                    seen.add(key)
            print(f"[DEDUPE] channel={ch_id} deleted={deleted}")
            write_dedupe_log("dedupe_channel", source, detail=f"channel={ch_id} deleted={deleted}")
            total_deleted += deleted
        except Exception as e:
            msg = f"scan_failed channel={ch_id} err=" + " ".join(str(e).splitlines())
            print(f"[DEDUPE] {msg}")
            write_dedupe_log("dedupe_error", source, detail=msg)

    write_dedupe_log("dedupe_done", source, detail=f"total_deleted={total_deleted}")
    return total_deleted

# ===== AsaBot：IG/X 清理 + 媒體限定監控 + 去重指令 + 連線檢查 =====
class AsaBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started_at = time.time()

    async def on_ready(self):
        print(f"[READY] AsaBot logged in as {self.user}")
        asyncio.create_task(self.heartbeat())
        if AUTO_DEDUPE_ON_START:
            asyncio.create_task(self.run_dedupe_once())

    async def heartbeat(self):
        while True:
            print(f"[HEARTBEAT-AsaBot] {time.strftime('%Y-%m-%d %H:%M:%S')}")
            await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)

    async def run_dedupe_once(self):
        channel_ids = [
            CHANNEL_SHARING_GIRL, CHANNEL_SHARING_BOY, CHANNEL_INJURIED,
            CHANNEL_GAME_BOX, CHANNEL_CONTRACT, CHANNEL_INTELLIGENCE_NEWS
        ]
        total = await delete_duplicate_messages(self, channel_ids, DUPLICATE_SCAN_LIMIT, source="auto.Asabot")
        print(f"[DEDUPE] finished on start. total_deleted={total}")

    async def on_message(self, message: discord.Message):
        try:
            if message.author.bot:
                return
            if message.author.id == self.user.id:
                return

            content = (message.content or "").strip().lower()

            # 連線檢查指令：!ping
            if content == "!ping":
                latency_ms = round(self.latency * 1000) if self.latency is not None else -1
                started = _ts(self.started_at)
                await message.channel.send(f"Pong! 延遲: {latency_ms} ms | 啟動時間: {started} | 心跳: {HEARTBEAT_INTERVAL_SEC}s")
                return

            # 手動去重：!dedupe
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

            # IG/X 連結清理（不限制頻道）
            if content:
                replies = []

                for match in INSTAGRAM_URL_PATTERN.finditer(message.content or ""):
                    cleaned = f"https://www.kkinstagram.com/{match.group(2)}/{match.group(3)}/"
                    replies.append(cleaned)

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
                    unique_replies = list(dict.fromkeys(replies))
                    await message.channel.send("\n".join(unique_replies))

            # 媒體限定監控（僅目標禁聊頻道）
            if message.channel.id in TARGET_MEDIA_CHANNELS:
                has_attachment_media = any(
                    (att.content_type or "").startswith("image/")
                    or (att.content_type or "").startswith("video/")
                    or (att.filename or "").lower().endswith(tuple(IMG_EXT | VID_EXT))
                    for att in message.attachments
                )
                urls = URL_PATTERN.findall(message.content or "")
                has_media_url = any(is_media_url(u) for u in urls)

                if not (has_attachment_media or has_media_url):
                    try:
                        await message.delete()
                    except discord.Forbidden:
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
            print(f"[ERROR-AsaBot] on_message: {e}")

# ========== 你的 YouTube 監控迴圈（已加完整 LOG） ==========
async def youtube_monitor_loop():
    print("[YT] monitor starting...")
    yt_log("YT_MONITOR_START", f"channel={YOUTUBE_CHANNEL_ID}")

    if not YOUTUBE_CHANNEL_ID:
        print("[YT] YOUTUBE_CHANNEL_ID missing, return")
        yt_log("YT_CONFIG_MISSING", "YOUTUBE_CHANNEL_ID missing", level="ERROR")
        return

    try:
        youtube = _yt_build_service()
        print("[YT] service built")
        yt_log("YT_SERVICE_BUILT", "ok")
    except Exception as e:
        print(f"[YT] build service failed: {e}")
        yt_log("YT_SERVICE_BUILD_FAIL", str(e), level="ERROR")
        return

    uploads_playlist_id = _yt_get_channel_uploads_playlist_id(youtube, YOUTUBE_CHANNEL_ID)
    if not uploads_playlist_id:
        msg = f"channel={YOUTUBE_CHANNEL_ID}"
        print(f"[YT] uploads playlist not found for {msg}, return")
        yt_log("YT_PLAYLIST_NOT_FOUND", msg, level="ERROR")
        return

    print(f"[YT] monitoring uploads playlist: {uploads_playlist_id}")
    yt_log("YT_PLAYLIST_OK", uploads_playlist_id)

    while True:
        print("[YT] poll begin")
        yt_log("YT_POLL_BEGIN", _now_ts_str(), dedupe_key="YT_POLL_BEGIN", dedupe_ttl_sec=5)
        try:
            items = _yt_get_latest_videos_from_playlist(youtube, uploads_playlist_id, max_results=10)
            count_msg = f"count={len(items)}"
            print(f"[YT] fetched={len(items)}")
            yt_log("YT_FETCHED", count_msg, dedupe_key="YT_FETCHED", dedupe_ttl_sec=10)

            
            # 2) 載入舊資料
            last = _yt_load_last_checked()
            last_ids = {_extract_id(it) for it in last if _extract_id(it)}
            # 4) 找出新影片
            new_items = [it for it in items if _extract_id(it) not in last_ids]

            if not new_items:
                yt_log("YT_NO_NEW", f"ts={_now_ts_str()}", dedupe_key="YT_NO_NEW", dedupe_ttl_sec=30)
                # 也可選擇仍然覆蓋一次，以保持檔案為最新 10
                _yt_save_last_checked(items)
                return
            
            # 5) 依時間由舊到新發送，避免逆序
            for it in _sort_by_published(new_items):
                vid = _extract_id(it)
                title = it.get("title") or it.get("snippet", {}).get("title") or "(no title)"
                url = it.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
                _yt_send_discord_message(title, url)
                yt_log("YT_NOTIFY_OK", f"{vid} {title}", dedupe_key=f"YT_NOTIFY_OK_{vid}", dedupe_ttl_sec=300)
            
            # 6) 覆蓋保存為目前抓到的 10 部
            _yt_save_last_checked(items)
            yt_log("YT_SAVED", f"count={len(items)}", dedupe_key="YT_SAVED", dedupe_ttl_sec=30)

            sleep_msg = f"{YT_CHECK_INTERVAL_SECONDS}s"
            print(f"[YT] sleep {sleep_msg}")
            yt_log("YT_SLEEP", sleep_msg, dedupe_key="YT_SLEEP", dedupe_ttl_sec=10)
            await asyncio.sleep(YT_CHECK_INTERVAL_SECONDS)

        except HttpError as e:
            emsg = str(e)
            print(f"[YT] HttpError: {emsg}")
            yt_log("YT_HTTP_ERROR", emsg, level="ERROR", dedupe_key="YT_HTTP_ERROR", dedupe_ttl_sec=60)

            # 判斷是否 quotaExceeded
            reason = "quotaExceeded" if "quotaExceeded" in emsg else None
            if reason == "quotaExceeded":
                sec = _seconds_until_next_1505()
                wake_dt = datetime.datetime.now() + datetime.timedelta(seconds=sec)
                wake_str = wake_dt.strftime('%Y-%m-%d %H:%M:%S')
                print(f"[YT] quotaExceeded -> sleep {sec}s until {wake_str}")
                yt_log("YT_PAUSE_UNTIL_15_05", f"sleep={sec}s wake={wake_str}")
                await asyncio.sleep(sec)
                try:
                    youtube = _yt_build_service()
                    uploads_playlist_id = _yt_get_channel_uploads_playlist_id(youtube, YOUTUBE_CHANNEL_ID) or uploads_playlist_id
                    print("[YT] service rebuilt after quota sleep")
                    yt_log("YT_REBUILT", "after quota sleep")
                except Exception as re:
                    remsg = str(re)
                    print(f"[YT] rebuild failed: {re}, retry in 60s")
                    yt_log("YT_REBUILD_FAIL", remsg, level="ERROR")
                    await asyncio.sleep(60)
            else:
                print("[YT] HttpError non-quota, backoff 120s")
                yt_log("YT_BACKOFF_120S", "HTTP non-quota")
                await asyncio.sleep(120)

        except Exception as e:
            emsg = str(e)
            print(f"[YT] unexpected error: {emsg}, backoff 120s")
            yt_log("YT_MONITOR_EXCEPTION", emsg, level="ERROR", dedupe_key="YT_MONITOR_EXCEPTION", dedupe_ttl_sec=60)
            await asyncio.sleep(120)

# ===== AsaBox：PTT 抓取推送（含錨點 + 日誌 + 自動去重，含日誌）=====
class AsaBox(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.anchor_mgr = ChannelAnchorManager()
        self.started_at = time.time()
        self.last_round_started_at: float | None = None
        self.last_round_completed_at: float | None = None
        self.is_fetching: bool = False
        write_ptt_log(self.started_at, "[PTT-AsaBox] start", None)

    async def on_ready(self):
        print(f"[READY] AsaBox logged in as {self.user}")
        await self.anchor_mgr.load_last_anchors(self)
        asyncio.create_task(self.heartbeat())
        asyncio.create_task(self.ptt_loop())

    async def heartbeat(self):
        while True:
            print(f"[HEARTBEAT-AsaBox] {time.strftime('%Y-%m-%d %H:%M:%S')}")
            await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)

    async def on_message(self, message: discord.Message):
        # 只有當使用者在可讀頻道輸入 !status 時回應；忽略機器人與自身訊息
        if message.author.bot or (self.user and message.author.id == self.user.id):
            return
        content = (message.content or "").strip().lower()
        if content == "!status":
            started = _ts(self.started_at)
            last_start = _ts(self.last_round_started_at) if self.last_round_started_at else "N/A"
            last_done = _ts(self.last_round_completed_at) if self.last_round_completed_at else "N/A"
            state = "抓取中" if self.is_fetching else "待機中"
            await message.channel.send(
                f"AsaBox 狀態: {state} | 啟動: {started} | 上次起始: {last_start} | 上次完成: {last_done} | 週期: {FETCH_INTERVAL}s"
            )

    async def ptt_loop(self):
        session = make_session()
        target_channels_for_dedupe = [
            CHANNEL_SHARING_GIRL, CHANNEL_SHARING_BOY, CHANNEL_INJURIED,
            CHANNEL_GAME_BOX, CHANNEL_CONTRACT, CHANNEL_INTELLIGENCE_NEWS
        ]
        while True:
            round_start = time.time()
            self.last_round_started_at = round_start
            self.is_fetching = True
            try:
                buckets = await asyncio.to_thread(collect_today, session)
                mapping = {
                    "BOX": CHANNEL_GAME_BOX,
                    "INFO_CONTRACT": CHANNEL_CONTRACT,
                    "INFO_INJURIED": CHANNEL_INJURIED,
                    "INFO_OTHER": CHANNEL_INTELLIGENCE_NEWS
                }
                for key, ch_id in mapping.items():
                    if not ch_id:
                        continue
                    channel = self.get_channel(ch_id)
                    if not channel:
                        try:
                            channel = await self.fetch_channel(ch_id)
                        except Exception as e:
                            print(f"[WARN] Channel not accessible: {ch_id} err={e}")
                            continue

                    todays_items = buckets.get(key, [])
                    to_send = self.anchor_mgr.stop_when_hit_anchor(key, todays_items)
                    if not to_send:
                        continue

                    payloads = []
                    for e in to_send:
                        if key == "BOX":
                            payloads.append(build_content_box(e.get("full_date",""), e.get("title_no_prefix",""), e.get("url","")))
                        else:
                            payloads.append(build_content_info(e.get("full_date",""), key, e.get("title_no_prefix",""), e.get("url","")))

                    buf = ""
                    for p in payloads:
                        if len(buf) + len(p) + 2 > 1800:
                            await channel.send(buf)
                            buf = ""
                        buf += (p + "\n\n")
                    if buf.strip():
                        await channel.send(buf.strip())

                    self.anchor_mgr.mark_sent(key, to_send)

                print("[PTT-AsaBox] one round done (anchor-aware)")
                write_ptt_log(round_start, "[PTT-AsaBox] completed", None)
                self.last_round_completed_at = time.time()

                total_deleted = await delete_duplicate_messages(self, target_channels_for_dedupe, DUPLICATE_SCAN_LIMIT, source="auto.Asabox")
                print(f"[PTT-AsaBox] auto dedupe done. total_deleted={total_deleted}")

            except Exception as e:
                print(f"[ERROR-AsaBox] ptt_loop: {e}")
                write_ptt_log(round_start, "error", str(e))
            finally:
                self.is_fetching = False
                await asyncio.sleep(FETCH_INTERVAL)

async def run_bot_with_retry(client, token: str, name: str, retry_delay: int = 30):
    while True:
        try:
            await client.start(token)
        except Exception as e:
            # 記錄錯誤但不退出，稍後重試
            print(f"[{name}] error: {e}, retry in {retry_delay}s")
            write_ptt_log(time.time(), f"{name}_ERROR", str(e))
            await asyncio.sleep(retry_delay)

async def main():

    client_bot = AsaBot(intents=intents_bot)
    client_box = AsaBox(intents=intents_box)

    # 分別包裝兩個 bot 的自動重試
    bot_task = asyncio.create_task(run_bot_with_retry(client_bot, TOKEN_ASA_BOT, "ASA_BOT"))
    box_task = asyncio.create_task(run_bot_with_retry(client_box, TOKEN_ASA_BOX, "ASA_BOX"))

    # 啟動 YouTube 監控（內部已處理 quotaExceeded 到 15:05 暫停，不會拋出讓主程式退出）
    yt_task = asyncio.create_task(youtube_monitor_loop())

    # 使用 gather 並允許回傳例外，不要因單一任務錯誤結束主程式
    results = await asyncio.gather(bot_task, box_task, yt_task, return_exceptions=True)

    # 如有例外，記錄但不退出（理論上 run_bot_with_retry 是無限迴圈不會返回）
    for i, res in enumerate(results, start=1):
        if isinstance(res, Exception):
            print(f"[MAIN] task {i} error: {res}")
            write_ptt_log(time.time(), "MAIN_TASK_ERROR", str(res))

if __name__ == "__main__":
    asyncio.run(main())