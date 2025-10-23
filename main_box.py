# 負責監控ptt/NBA之訊息程式 main_box.py
import os
import re
import time
import asyncio
import datetime
import discord
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from dotenv import load_dotenv

load_dotenv()

# ===== Discord Token（情報發送用） =====
TOKEN = os.getenv("TOKEN_ASA_BOX")
if not TOKEN:
    raise RuntimeError("TOKEN_ASA_BOX not set")

# 頻道
CHANNEL_INJURIED = int(os.getenv("CHANNEL_INJURIED", "0") or 0)
CHANNEL_GAME_BOX = int(os.getenv("CHANNEL_GAME_BOX", "0") or 0)
CHANNEL_CONTRACT = int(os.getenv("CHANNEL_CONTRACT", "0") or 0)
CHANNEL_INTELLIGENCE_NEWS = int(os.getenv("CHANNEL_INTELLIGENCE_NEWS", "0") or 0)

# PTT 與抓取控制
BASE_URL = "https://www.ptt.cc"
INDEX_URL = os.getenv("PTT_URL", "https://www.ptt.cc/bbs/NBA/index.html")
FETCH_INTERVAL = int(os.getenv("PTT_FETCH_INTERVAL_SEC", "1800"))
MAX_PAGES = int(os.getenv("PTT_MAX_PAGES", "12"))
ONLY_TODAY = os.getenv("PTT_ONLY_TODAY", "true").lower() == "true"
STOP_AT_FIRST_OLDER = os.getenv("PTT_STOP_AT_FIRST_OLDER", "true").lower() == "true"

TARGET_PREFIXES = [p.strip() for p in os.getenv("PTT_TARGET_PREFIXES", "BOX,情報").split(",") if p.strip()]

# 關鍵詞
KEYWORDS_INJURY = [w.strip() for w in os.getenv("KEYWORDS_INJURY", "").split(",") if w.strip()]
CONTRACT_PATTERNS = [p.strip() for p in os.getenv("KEYWORDS_CONTRACT_PATTERNS", "").split(";") if p.strip()]
NEGATIVE_FOR_CONTRACT_TITLE = [w.strip() for w in os.getenv("NEGATIVE_FOR_CONTRACT_TITLE", "").split(",") if w.strip()]

intents = discord.Intents.default()
intents.message_content = True

def make_session():
    s = requests.Session()
    s.cookies.set('over18', '1', domain='.ptt.cc')
    s.headers.update({"User-Agent": "Mozilla/5.0 (compatible; PTTFetcher/1.2)"})
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
        # 若只抓當日，且啟用「遇第一則非今日就停」
        if ONLY_TODAY and stop_at_first_older and full_date and full_date != today.strftime("%Y/%m/%d"):
            # 停止解析本頁後續 r-ent
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

        # 只保留當日 + 目標前綴
        entries_today = [e for e in entries if e.get("full_date") == today_str]
        entries_today = filter_by_target_prefix(entries_today, TARGET_PREFIXES)

        for e in entries_today:
            if e.get("prefix") == "BOX":
                buckets["BOX"].append(e)
            elif e.get("prefix") == "情報":
                k = classify_info(e.get("title", ""))
                buckets[k].append(e)

        # 若本頁最後一筆就已經遇到非今日，或本頁沒任何當日項目，且設定了 STOP_AT_FIRST_OLDER，就可停止翻頁
        if STOP_AT_FIRST_OLDER:
            # 若 parse_entries 中曾遇到第一則非今日就 break，代表本頁之後可能已有非今日，這時可直接停止翻頁
            # 簡易判斷：如果 entries 非空但 entries_today 數量 < entries 數量，且 entries 有出現非今日
            if entries and (len(entries_today) < len(entries)):
                break

        # 繼續翻頁
        prev_url = find_prev_page_url(html)
        if not prev_url:
            break
        current_url = prev_url
        pages += 1

    return buckets

class PTTState:
    def __init__(self):
        self.sent_urls = set()

    def filter_new(self, items):
        new_items = []
        for it in items:
            u = it.get("url")
            if not u or u in self.sent_urls:
                continue
            self.sent_urls.add(u)
            new_items.append(it)
        return new_items

ptt_state = PTTState()

class AsaBoxBot(discord.Client):
    async def on_ready(self):
        print(f"[READY] AsaBoxBot logged in as {self.user}")
        self.bg_heartbeat = asyncio.create_task(self.heartbeat())
        self.bg_ptt = asyncio.create_task(self.ptt_loop())

    async def heartbeat(self):
        hb = int(os.getenv("HEARTBEAT_INTERVAL_SEC", "3600"))
        while True:
            print(f"[HEARTBEAT] {time.strftime('%Y-%m-%d %H:%M:%S')}")
            await asyncio.sleep(hb)

    async def ptt_loop(self):
        session = make_session()
        while True:
            try:
                buckets = await asyncio.to_thread(collect_today, session)
                # 發送到對應頻道
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
                        # 可選：await self.fetch_channel(ch_id)
                        print(f"[WARN] Channel not cached: {ch_id}")
                        continue
                    new_items = ptt_state.filter_new(buckets.get(key, []))
                    if not new_items:
                        continue

                    payloads = []
                    for e in new_items:
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

                print("[PTT] one round done")
            except Exception as e:
                print(f"[ERROR] ptt_loop: {e}")
            finally:
                await asyncio.sleep(FETCH_INTERVAL)

def main():
    client = AsaBoxBot(intents=intents)
    client.run(TOKEN, reconnect=True)

if __name__ == "__main__":
    main()
