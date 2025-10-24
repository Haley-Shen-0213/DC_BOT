# README.md
# PTT x YouTube x Discord 多機器人整合（AsaBot / AsaBox）
English version is provided below.

## 簡介
本專案同時啟動兩個 Discord Bot 與一個 YouTube 監控器：
- AsaBot（媒體維護/轉連結/去重）
  - 自動將 Instagram 連結轉為 kkinstagram、Twitter/X 連結轉為 fxtwitter（改善內嵌預覽）
  - 指定頻道（香香區/帥帥區）僅允許圖片/影片或可內嵌媒體連結，違者刪文並提醒
  - 手動與自動的重複訊息刪除（完全相同文字內容）
  - !ping 心跳檢查
- AsaBox（PTT 抓取/分類/推送）
  - 週期性抓取 PTT NBA 與 basketballTW 看板
  - NBA：僅抓今日 [BOX]/[情報]；情報再依關鍵詞分類（合約/傷病/其他）
  - TB：僅抓今日 [情報/乳摸/新聞/專欄]，依隊伍關鍵字分流；未匹配者記錄到每日檔案
  - 完成推送後自動去重
  - !status 抓取狀態查詢
- YouTube 監控
  - 偵測目標頻道新影片，使用 Discord Webhook 推播
  - quotaExceeded 時休眠至下一個 15:05，醒來重建 service 後繼續

## 架構
- 一個 Python 程式同時啟動：
  - 2 個 Discord Bot（AsaBot / AsaBox）
  - 1 個 YouTube 監控背景任務
- 使用 .env 管理 Token、頻道 ID 與抓取/輪詢設定
- 詳實日誌與錯誤處理、自動重試

## 需求
- Python 3.10+
- 依賴請見 requirements.txt

### 安裝步驟
- 建立虛擬環境
  - Windows: `python -m venv .venv && .venv\Scripts\activate`
  - macOS/Linux: `python3 -m venv .venv && source .venv/bin/activate`
- 安裝套件
  - `pip install -r requirements.txt`
- 建立 .env
  - 參考 `.env.example`，填入你的 Token、頻道 ID、API Key 等

## 執行
- 本地開發：`python main.py`
- 伺服器常駐：可搭配 screen/tmux/systemd/pm2 等
- 啟動後 Console 會看到 READY/HEARTBEAT/PTT/YT 相關日誌

## .env 主要鍵值（摘要）
- Discord Bot Tokens：`TOKEN_ASA_BOT`、`TOKEN_ASA_BOX`（必填）
- 頻道 IDs：`CHANNEL_SHARING_GIRL`、`CHANNEL_SHARING_BOY`、`CHANNEL_INJURIED`、`CHANNEL_GAME_BOX`、`CHANNEL_CONTRACT`、`CHANNEL_INTELLIGENCE_NEWS`、`CHANNEL_BRAVES`、`CHANNEL_PILOTS`、`CHANNEL_TSG`、`CHANNEL_YKE_ARK`
- PTT 設定：`NBA_PTT_URL`、`TB_PTT_URL`、`PTT_FETCH_INTERVAL_SEC`、`PTT_MAX_PAGES`、`PTT_TARGET_PREFIXES`、`PTT_ONLY_TODAY`、`PTT_STOP_AT_FIRST_OLDER`
- 情報分類：`KEYWORDS_INJURY`、`KEYWORDS_CONTRACT_PATTERNS`、`NEGATIVE_FOR_CONTRACT_TITLE`
- 一般：`LOG_LEVEL`、`HEARTBEAT_INTERVAL_SEC`、`DUPLICATE_SCAN_LIMIT`、`AUTO_DEDUPE_ON_START`
- YouTube：`YOUTUBE_CHANNEL_ID`、`YOUTUBE_API_KEY`、`DISCORD_WEBHOOK_URL`、`LAST_CHECKED_FILE`、`YT_CHECK_INTERVAL_SECONDS`

## 指令與權限
- AsaBot
  - `!ping`：延遲、啟動時間、心跳
  - `!dedupe`：手動去重（需要 Manage Messages 或管理員權限）
- AsaBox
  - `!status`：顯示抓取狀態
- 權限與 Intents
  - 需啟用 Message Content Intent
  - 建議權限：View Channels、Send Messages、Manage Messages

## 功能細節
- 媒體限定頻道：若無圖片/影片附件、或非可內嵌媒體連結，訊息會被刪除並提示（缺權限時提示後自刪）
- 連結清理：自動回覆 kkinstagram/fxtwitter 的乾淨連結
- 去重：掃描最近 N 則訊息，刪除完全相同文字內容的重複訊息（僅限一般訊息）
- PTT 抓取：
  - NBA：[BOX]/[情報]，情報依關鍵字分類（合約/傷病/其他），以頻道近 20 則訊息 URL 去重
  - TB：四類前綴，依隊伍關鍵字推送到各隊頻道；無隊名關鍵字者寫入 logs 檔案
- YouTube 監控：抓 uploads 播放清單，推送新片標題+URL；配額超限時自動退避

## 日誌與檔案
- `log/ptt_asabox_YYYY-MM-DD.log`：PTT/去重/一般運行日誌
- `logs/yt/YYYY-MM-DD.log`：YouTube 監控日誌
- `last_checked_videos.json`：YouTube 快取
- `basketballTW_log_YYYY-MM-DD.log`：TB 未匹配隊伍文章清單

## 常見問題
- `Missing tokens. Please set TOKEN_ASA_BOT and TOKEN_ASA_BOX in environment.`
  - 未設定或鍵名錯誤，請確認 .env
- 刪文失敗
  - 請賦予 Bot Manage Messages 權限
- YouTube 推播缺失或 `YT_WEBHOOK_MISSING`
  - 檢查 `DISCORD_WEBHOOK_URL` 是否設定
- PTT 無推文
  - 檢查 `PTT_ONLY_TODAY`、`PTT_TARGET_PREFIXES` 是否過嚴，或目標頻道 ID 是否設定

## 安全注意
- 務必不要將 Token/API Key/Webhook URL/Channel ID 等敏感資訊提交到版本庫
- 建議透過 .env 管理，並將 .env 納入 .gitignore

---

# PTT x YouTube x Discord Multi-bot Integration (AsaBot / AsaBox)

## Overview
This project launches two Discord bots and one YouTube monitor:
- AsaBot (media moderation/link rewriter/deduper)
  - Rewrite Instagram links to kkinstagram and Twitter/X links to fxtwitter for better embeds
  - Enforce media-only channels (images/videos or embeddable media URLs); delete non-compliant posts and notify
  - Manual and automatic duplicate deletion (exact same text)
  - `!ping` health check
- AsaBox (PTT fetch/classify/push)
  - Periodically fetch PTT NBA and basketballTW boards
  - NBA: only today’s `[BOX]/[情報]`; further classify info (contract/injury/other) by keywords
  - TB: only today’s `[情報/乳摸/新聞/專欄]`, route by team keywords; unmatched entries logged to daily file
  - Auto-dedup after pushing
  - `!status` to display current state
- YouTube monitor
  - Detect new uploads from the target channel and push via Discord Webhook
  - On quotaExceeded, sleep until next 15:05 and rebuild the service

## Architecture
- Single Python process runs:
  - 2 Discord bots (AsaBot / AsaBox)
  - 1 background YouTube monitor
- `.env` for tokens, channel IDs, and polling configs
- Extensive logs, error handling, and auto-retry

## Requirements
- Python 3.10+
- See `requirements.txt`

### Setup
- Create virtual environment
  - Windows: `python -m venv .venv && .venv\Scripts\activate`
  - macOS/Linux: `python3 -m venv .venv && source .venv/bin/activate`
- Install dependencies
  - `pip install -r requirements.txt`
- Create `.env`
  - Copy from `.env.example` and fill in your tokens, channel IDs, API keys

## Run
- Local: `python main.py`
- Production: use screen/tmux/systemd/pm2, etc.
- Console shows READY/HEARTBEAT/PTT/YT logs

## Key .env variables (summary)
- Discord: `TOKEN_ASA_BOT`, `TOKEN_ASA_BOX` (required)
- Channels: `CHANNEL_SHARING_GIRL`, `CHANNEL_SHARING_BOY`, `CHANNEL_INJURIED`, `CHANNEL_GAME_BOX`, `CHANNEL_CONTRACT`, `CHANNEL_INTELLIGENCE_NEWS`, `CHANNEL_BRAVES`, `CHANNEL_PILOTS`, `CHANNEL_TSG`, `CHANNEL_YKE_ARK`
- PTT: `NBA_PTT_URL`, `TB_PTT_URL`, `PTT_FETCH_INTERVAL_SEC`, `PTT_MAX_PAGES`, `PTT_TARGET_PREFIXES`, `PTT_ONLY_TODAY`, `PTT_STOP_AT_FIRST_OLDER`
- Classification: `KEYWORDS_INJURY`, `KEYWORDS_CONTRACT_PATTERNS`, `NEGATIVE_FOR_CONTRACT_TITLE`
- General: `LOG_LEVEL`, `HEARTBEAT_INTERVAL_SEC`, `DUPLICATE_SCAN_LIMIT`, `AUTO_DEDUPE_ON_START`
- YouTube: `YOUTUBE_CHANNEL_ID`, `YOUTUBE_API_KEY`, `DISCORD_WEBHOOK_URL`, `LAST_CHECKED_FILE`, `YT_CHECK_INTERVAL_SECONDS`

## Commands and Permissions
- AsaBot
  - `!ping`: latency, start time, heartbeat interval
  - `!dedupe`: manual dedupe (requires Manage Messages or admin)
- AsaBox
  - `!status`: show current fetching state
- Permissions & Intents
  - Enable Message Content Intent
  - Recommended perms: View Channels, Send Messages, Manage Messages

## Feature details
- Media-only channels: non-media messages are deleted with a short-lived notice (fallback notice if lacking delete permissions)
- Link rewriting: reply with cleaned kkinstagram/fxtwitter links
- Deduplication: scan last N messages and delete exact-duplicate text (default message type only)
- PTT:
  - NBA: `[BOX]/[情報]`, classify info by keywords, dedupe with last 20 message URLs in channel
  - TB: four prefixes, route by team keywords; unmatched entries written to logs
- YouTube: watch uploads playlist, push Title+URL; on quotaExceeded, back off until next 15:05

## Logs and Files
- `log/ptt_asabox_YYYY-MM-DD.log`: PTT/dedupe/general logs
- `logs/yt/YYYY-MM-DD.log`: YouTube monitor logs
- `last_checked_videos.json`: YouTube cache
- `basketballTW_log_YYYY-MM-DD.log`: TB unmatched entries

## FAQ
- `Missing tokens. Please set TOKEN_ASA_BOT and TOKEN_ASA_BOX in environment.`
  - Check `.env` or variable names
- Failed to delete messages
  - Grant the bot Manage Messages permission
- YouTube pushes missing or `YT_WEBHOOK_MISSING`
  - Set `DISCORD_WEBHOOK_URL` properly
- No PTT posts pushed
  - Check `PTT_ONLY_TODAY`/`PTT_TARGET_PREFIXES` and ensure channel IDs are set

## Security
- Never commit real Tokens/API Keys/Webhook URLs/Channel IDs
- Use `.env` and add it to `.gitignore`
