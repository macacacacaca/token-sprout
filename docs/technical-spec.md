# Token Sprout 🌿 — v0.1 Technical Spec

> 將 AI Agent 消耗的 API Token 轉化為終端機裡的植物養分。
> 讓等待 AI 思考的時間，變成「看著植物被 Token 澆水」的養成體驗。

> **文件定位**：這是 v0.1 的實作規格（technical spec），不只是願景藍圖。
> 任何與本文件衝突的實作決定，必須先回來改這份文件。

---

## 0. 一頁摘要

- **是什麼**：本機透明 proxy，攔截 Anthropic API response 的 token usage metadata，餵給一株終端機 ASCII 植物。
- **最高工程原則**：**proxy 絕對不能弄壞 Claude Code**。所有設計決策以此為最優先，植物功能其次。
- **架構一句話**：byte-level 透明轉發 + 旁路（tee）解析 usage + 單一 JSON state 檔 + 獨立 terminal UI。
- **成敗關鍵**：不在植物，在 proxy 的透明度。Milestone 0 沒全數通過前，不寫任何遊戲程式碼。

---

## 1. 專案定位

**Token Sprout** 是一個本機端開源小工具，針對使用 Claude Code、AI coding agents、LLM API 的開發者設計。

它會在本機啟動一個 Anthropic API 透明 proxy，從 API response 的 usage metadata 擷取 token 數字，並把 token 消耗轉換成植物的 EXP、等級與 ASCII 成長動畫。

### 一句話說明

> Grow a tiny terminal plant with the tokens you spend on AI coding agents.

### 核心價值

- 把枯燥的 AI 等待時間轉化為視覺回饋。
- 把 token 消耗的剝奪感轉化為電子寵物養成感。
- 除了使用者本來就在呼叫的 Anthropic API 之外，不與任何伺服器通訊；prompt、completion、憑證僅在記憶體中透傳，永不落地。
- proxy 程式碼刻意保持極小（目標 < 300 行），任何人 10 分鐘可讀完並自行稽核——**可稽核性本身就是安全賣點**。

### 為什麼用 proxy，而不是讀 Claude Code 本地紀錄 / hooks / telemetry？

這個問題發布後一定會被問（ccusage 等工具就是讀本地 transcript），先把答案寫進規格：

| 方案 | 能拿到 usage | 能拿到「即時 thinking 狀態」 | 泛用性 |
|---|---|---|---|
| 讀 transcript JSONL | ✅ | ❌（訊息完成後才寫入） | 只限 Claude Code，格式未文件化、隨版本變動 |
| Claude Code hooks | ✅（間接） | 部分（prompt submit / stop） | 只限 Claude Code，需使用者改 settings |
| OpenTelemetry metrics | ✅ | ❌（批次匯出） | 只限 Claude Code |
| **Local proxy（本案）** | ✅ | ✅（request 進來的那一刻） | 任何走 Anthropic API 的 client |

「植物在 AI thinking 時即時喝水」是本專案的 demo 核心，只有 proxy 拿得到這個訊號——這是選擇 proxy 架構的唯一且充分的理由。**若 Milestone 0 驗證失敗，備援架構是 hooks + transcript 方案**（犧牲即時性，換取零風險）。

---

## 2. v0.1 核心原則

v0.1 不追求完整遊戲系統，只追求一個清楚、穩定、好 demo 的 MVP。

### v0.1 必須做到（硬性需求）

- Claude Code 透過 `ANTHROPIC_BASE_URL` 走 local proxy，**API key 模式與訂閱 OAuth 模式皆可用**。
- Proxy 以 **raw bytes** 原封不動轉發任意路徑、任意 method 的 request（catch-all，不只 `/v1/messages`）。
- **Streaming（SSE）透傳是第一優先**，不是進階功能：Claude Code 幾乎所有請求都是 `stream: true`，不支援 streaming 的 proxy 等於不能用。non-streaming 是順手支援的簡單情境。
- **Fail-open**：usage 解析、state 寫入、任何植物邏輯掛掉，都不得影響轉發。轉發路徑上沒有任何可拋出例外會中斷 request 的植物程式碼。
- Proxy 從 response（SSE 與 JSON 兩種）擷取 usage metadata，含 prompt caching 欄位。
- Token usage 寫入本機 `plant_state.json`（single writer + atomic rename）。
- Terminal UI 根據 `plant_state.json` 顯示植物狀態；`active_requests > 0` 時播放澆水動畫。
- Response 結束後，植物吸收 token，EXP 增加，必要時升級；Bloom 後進入下一代種子（generation +1）。
- Proxy 僅綁定 `127.0.0.1`。
- 任何 log 輸出不含 request/response body；auth headers 出現在任何輸出前必須遮蔽。

### v0.1 不做

- 不做雲端同步、帳號系統。
- 不做 prompt / response / API key / OAuth token 的任何形式儲存或記錄。
- 不做多植物種類。
- 不做 VSCode extension。
- 不做完整寵物互動系統。
- 不做 MCP 作為核心架構。
- 不做升級特效動畫（降級為一行文字訊息 + 更換 ASCII art）。
- 不做 responsive terminal layout（宣告最小尺寸，小於則顯示提示文字）。
- 不用 pydantic 建模 Anthropic API request/response（見 §5.1，這是風險不是便利）。
- 不在 v0.1 實測或宣稱支援 Claude Code 以外的 agents。

---

## 3. 技術架構總覽

核心是 **tee 模式**：上游 bytes 一到就立刻轉發給 client，同一份 bytes 的複本丟給旁路解析器。解析器與轉發路徑完全解耦。

```text
Claude Code
   |
   |  HTTP request（含原始 auth headers，proxy 不讀不改）
   v
127.0.0.1:8000  Token Sprout Proxy（catch-all，raw bytes 轉發）
   |
   v
Anthropic API
   |
   |  Response / SSE stream
   v
Proxy ──立即轉發原始 bytes──> Claude Code        （主路徑：零緩衝、零改寫）
   |
   └──bytes 複本──> 旁路 usage parser             （副路徑：失敗不影響主路徑）
                        |
                        v
                  plant_state.json（single writer, atomic rename）
                        |
                        |  poll / read-only
                        v
                  Terminal UI（token-sprout watch）
```

### 終端機使用情境

```text
一次安裝: token-sprout install-claude      # 在 shell rc 加可移除的 managed function
Terminal 1: claude                         # 日常入口；內部仍走安全的 run wrapper
Terminal 2: token-sprout watch             # 選用
```

`install-claude` 不取代 Claude 官方 executable / symlink，而是在目前 shell 的 rc
檔末端 merge 一段有 begin/end marker 的 function；function 使用 Claude executable
與 token-sprout 的絕對路徑，呼叫 `token-sprout run -- <real-claude> "$@"`，所以不會
alias recursion，Claude 官方 symlink 也能繼續自動更新。若偵測到使用者既有的非
Token-Sprout `alias claude=` 或 `claude() { ... }`，預設拒絕覆蓋。`uninstall-claude`
只移除自己的 managed block。安裝後新 terminal 可直接輸入 `claude`；`/plant` 只負責
讀取/顯示 state，不能在 Claude 已啟動後回頭改變該行程的 API routing。

shell path 規格：zsh 尊重 `ZDOTDIR`，否則寫 `~/.zshrc`；bash 在 macOS 寫
`~/.bash_profile`、其他平台寫 `~/.bashrc`。安裝器除了靜態掃描目標 rc，也要在
managed block 被 source 時再次檢查先前由其他檔案載入的 `claude` alias/function；
未帶 `--force` 時保留使用者定義並印固定警告，不可默默覆蓋。

v0.1 平台範圍為 macOS、Linux，以及 Windows 10/11 的 **WSL2 Linux
環境**。Windows 使用者的 Claude Code、Python、Token Sprout 必須全部安裝並
從同一個 WSL2 distro 啟動，才能共用 shell rc、localhost 與 Linux home 下的
`0600`/`0700` 權限邊界。v0.1 不宣稱支援原生 Windows PowerShell/CMD；在
WSL2 實機安裝清單通過前，README 必須標示該路徑尚待驗證。

`run` 將 `ANTHROPIC_BASE_URL` 只注入 Claude 子行程，並在使用者沒有自行設定時
補上 `ENABLE_TOOL_SEARCH=true`，因為 Claude Code 會把 localhost 視為非 first-party
gateway，而 proxy 能透明轉發 tool-reference 流量。Remote Control 在非 first-party
base URL 下仍會被 Claude Code 停用；英文與繁中 README 都必須明確揭露此限制。
`run` 的啟動提示只顯示 executable 名稱，不得回印其餘 CLI arguments，避免
`claude "<prompt>"` 的 prompt 落入 terminal scrollback。

或手動模式：

```text
Terminal 1: token-sprout proxy
Terminal 2: ANTHROPIC_BASE_URL=http://127.0.0.1:8000 claude
Terminal 3: token-sprout watch
```

> ⚠️ 文件必須明確警告：**不要把 `ANTHROPIC_BASE_URL` 寫進 `.zshrc` / `.bashrc`**。
> proxy 沒開時 Claude Code 會整個壞掉，這會是最大宗的支援問題來源。`token-sprout run` 就是為此存在。

---

## 4. 為什麼 v0.1 不用 MCP

MCP 適合讓 Claude Code 使用外部工具，但 Token Sprout v0.1 需要的是：

- 精準觀測 API request 開始 / 結束時間（即時 thinking 訊號）。
- 精準擷取 response 中的 usage metadata。

這些只有 local proxy 拿得到（比較表見 §1）。MCP 留到 v0.2+ 作為 optional companion layer（`get_plant_status()`、`rename_plant(name)` 等互動功能）。

v0.1 的核心仍然是：

```text
Local Proxy + plant_state.json + Terminal UI
```

---

## 5. 系統模組

### 5.1 Local Proxy

**定位：透明轉發器（transparent forwarder），不是 API server。** 它對 Anthropic API 的 schema 一無所知，也不應該知道。

技術選型：

```text
starlette（或 FastAPI，但只用 raw Request.stream() + StreamingResponse）
httpx（streaming client）
uvicorn
```

> 選 starlette 而非完整 FastAPI 的理由：轉發路徑只需要 raw ASGI 能力，
> 依賴越少、程式碼越短，社群稽核成本越低。pydantic **禁止**出現在轉發路徑上
> ——任何對 request body 的 parse + re-serialize 都可能改動欄位順序、escaping
> 或丟棄未知欄位，而 Anthropic API 常態性新增欄位。

#### 轉發規格（normative）

1. **路由**：catch-all——任意路徑、任意 method（不使用 method allow-list）。Claude Code 會呼叫 `/v1/messages`、`/v1/messages/count_tokens`、`/v1/models` 等端點，未來還會增加；未知路徑一律原樣轉發。
2. **Request**：body 以 raw bytes 串流轉發，不解析、不緩衝整包；request target 必須從 ASGI `raw_path` + `query_string` 重建，保留 `%2F` 等 percent-encoding，不得用已解碼的 `request.url.path` 重組。
3. **憑證**：`x-api-key`、`authorization` 等 auth headers **原樣透傳，永不讀取、永不記錄、永不落地**。proxy 本身沒有憑證概念——這同時支援 API key 與訂閱 OAuth 兩種模式。
4. **Header 政策**：
   - 透傳：`x-api-key`、`authorization`、`anthropic-version`、`anthropic-beta`、`content-type` 及其他未列名 headers。
   - 剝除 hop-by-hop headers：`connection`、`keep-alive`、`transfer-encoding`、`upgrade`、`te`、`trailer`、`proxy-authorization`、`proxy-authenticate`。
   - 對上游強制 `accept-encoding: identity`（避免解析 gzip SSE；帶寬損失可忽略）。
   - 回程透傳 `request-id` 與所有 `anthropic-ratelimit-*` headers（否則 client 的退避邏輯會壞）。
5. **Timeout 與連線數**：connect 可短（如 10s）；**read timeout 設為 None**——高階模型單一 streaming request 可跑數分鐘。**連線數不設上限**（`max_connections=None`）：httpx 預設的 100 連線上限加上無限期 pool 等待，會讓第 101 條並發 streaming request 無聲懸掛——並發上限由 client 自己決定，proxy 不得成為隱形瓶頸。
6. **Streaming**：SSE bytes 逐 chunk 立即轉發，零緩衝。轉發與解析用 tee：解析器收 bytes 複本，在獨立 task 中處理。
7. **Fail-open（最重要的一條）**：解析器拋例外、state 檔被鎖死、磁碟滿——轉發照常完成。實作上：轉發路徑的 code path 中不得 `await` 任何植物邏輯。
8. **錯誤透傳**：上游 4xx/5xx/529、mid-stream `error` event，一律原樣轉發給 client，proxy 不重試、不改寫、不吞掉。
9. **綁定**：僅 `127.0.0.1`，v0.1 不提供改綁參數。
10. **Logging**：每個完成的 request 只記「method + 路徑 + 狀態碼 + 耗時」；路徑中的控制字元必須 escape、長度設上限，避免 log injection。另可有不含 request-derived data 的固定 lifecycle / warning 訊息。任何層級的 log 都不得包含 body、usage、headers；非官方 upstream 顯示時不得包含 URL userinfo/query；例外訊息輸出前遮蔽 auth headers；禁用 rich traceback 的 `show_locals`。

#### Usage 擷取規格

只對 `/v1/messages` 的實際推理回應計數（`count_tokens` 端點的回應也有 `input_tokens`，但不是真實消耗，**排除**）。

- **SSE**（主要情境）：
  - `message_start` 事件 → `message.usage` 內的 `input_tokens`、`cache_creation_input_tokens`、`cache_read_input_tokens`。
  - **最後一個** `message_delta` 事件 → `usage.output_tokens`（累計值）。
  - 必須處理：`data:` 行跨 chunk 分割（需自行 buffer 到事件邊界）、`ping` 事件、mid-stream `error` 事件、client 中途斷線（使用者按 Esc）——中斷時以已收到的最後一筆 usage 結算，或整筆放棄，兩者擇一並保持一致。
- **非 streaming**：response JSON 頂層 `usage` 物件，同樣四個欄位。
- 解析失敗：記一筆不含內容的警告 log，該 request 不計分，**轉發不受影響**。

#### Request 生命週期訊號

- request 進入且路徑為 `/v1/messages`（推理）→ `active_requests += 1`。
- response 完成 / 中斷 / 出錯 → `active_requests -= 1` 並結算 usage。
- 用計數器而非布林值：Claude Code 會並發多個 request（subagents、背景 Haiku 請求），布林值會被互相覆蓋。

---

### 5.2 State Manager

儲存位置：

```text
~/.token-sprout/plant_state.json
```

權限（POSIX normative）：`~/.token-sprout/` 固定 `0700`；`plant_state.json`、
`plant_state.json.corrupt`（壞檔隔離備份，見下）、`proxy.secret`、`proxy.log`、
`state.lock` 固定 `0600`。建立與既有安裝升級時都要收緊，不依賴使用者 umask；
修復一律透過 `O_NOFOLLOW` 描述符 chmod，不做「先檢查再以路徑 chmod」的
check-then-act（same-user symlink 競態）。

v0.1 用 JSON，不用 SQLite。pydantic 可以用在這一層（state 是我們自己的 schema）。

#### 併發協議（normative）

- **Single writer**：只有 proxy 行程寫入。
- **寫入 = temp file + `os.rename`**（atomic），確保 UI 永遠讀到完整 JSON。
- **filelock** 只用來序列化 proxy 內部的並發寫入（多個 request 同時結算）。
- 背景 writer 對 filelock 使用有限等待；逾時則固定文字警告並放棄該次 state 工作，不得卡住轉發或 shutdown。
- request start / finish / usage 結算維持 FIFO、不可主動丟棄；高頻 `live_tokens_estimate` 工作允許以 latest-value-wins 合併，避免磁碟或 lock 異常時 queue 無限成長。
- **UI 只讀、不取鎖**；讀到壞 JSON（理論上不會發生，防禦性處理）時：連續刷新的 `watch` 沿用上一幀；一次性行程的 `status` / `statusline` 沒有上一幀可用，以預設值顯示。
- 寫入端讀不到檔（首次啟動）即重建初始 state，不 crash；**檔案存在但毀損時，必須先把壞檔改名為 `plant_state.json.corrupt` 保留（只留最近一份），再從預設重建**。若隔離改名失敗，必須放棄該次 state 寫入並交由背景 writer fail-open，不得覆蓋原檔。合法 JSON 若 schema 型別、範圍或 derived 欄位錯誤，也必須 normalize；`stage` / `level` 永遠由 `current_exp` 重算。

#### State schema

```json
{
  "version": "0.1.0",
  "generation": 1,
  "total_tokens": 0,
  "total_input_tokens": 0,
  "total_output_tokens": 0,
  "total_cache_creation_tokens": 0,
  "total_cache_read_tokens": 0,
  "current_exp": 0,
  "level": 1,
  "stage": "seed",
  "active_requests": 0,
  "live_tokens_estimate": 0,
  "last_request_tokens": 0,
  "last_input_tokens": 0,
  "last_output_tokens": 0,
  "last_request_started_at": null,
  "last_request_finished_at": null
}
```

相對第一版藍圖的變更：`is_thinking: bool` → `active_requests: int`（UI 以 `active_requests > 0` 判斷 thinking）；新增 cache 兩欄、`generation` 與僅供顯示的 `live_tokens_estimate`。

---

### 5.3 顯示（statusline 為主，watch 為輔）

**主力是 Claude Code 底部狀態列**（`token-sprout statusline`），不是獨立終端機。
不再使用大型 ASCII 植物——植物用**單一 emoji 排出顆數**呈現（見 §5.4）。

- **statusline**（同視窗底部一行）：消耗 token 時顯示，閒置時整行消失。格式為
  「預估階段 emoji 排 N 顆 + N/20 + 💧 + 10 格單顆進度條 + 百分比 + 即時估算」。
  例：`🌱🌱 2/20 · 💧 [██████░░░░] 67% · +5,000 tokens`。進度條的 `█`/`░`
  固定十格；填滿格數向下取整，百分比四捨五入到整數。進度條表示「距離下一顆
  當前階單位」的進度，因此幼芽階每顆是 200,000，盆栽階每顆是 4,000,000，
  不是每階都以 10,000 為一格。
- **watch**（`token-sprout watch`，選用的獨立面板）：`rich` 面板，同樣用 emoji 排顆數
  （空格分隔便於數），加 generation、到下一顆/開花的 token、total、澆水動畫。
- `active_requests > 0` 時播放澆水動畫；升階/開花顯示一行訊息，**不做特效動畫**。
- watch 宣告最小尺寸（44×12），小於顯示提示；**不做 responsive layout**。
- Polling `plant_state.json`，4–10 Hz；純讀取，不影響 proxy。

---

### 5.4 Game Logic

#### 食物口徑（哪些 token 餵植物）

Claude Code 重度使用 prompt caching：`input_tokens` 只是未快取殘餘，`cache_read_input_tokens` 一天可達數百萬。v0.1 口徑：

```text
食物 = input_tokens + output_tokens + cache_creation_input_tokens
cache_read_input_tokens：記錄、顯示，但不餵植物
```

README 必須說明此口徑，否則使用者拿帳單比對會開 issue 問「數字為什麼對不上」。

#### 成長機制：20:1 階層合成（數量堆疊）

植物用「顆數」成長，不是一格直接跳級：

```text
每 TOKENS_PER_UNIT 食物 = 1 顆種子
每集滿 UNITS_PER_STAGE 顆當前單位 → 合成下一階的 1 顆
🌰×1 → ×2 → … → ×20  ⟶  🌱×1 → …×20  ⟶  🪴…  ⟶  🌷…  ⟶  🌸 開花
```

v0.1 參數（使用者選定長期累積；開花才重開一代）：

```python
TOKENS_PER_UNIT = 10_000  # 1 顆種子需要的食物 token
UNITS_PER_STAGE = 20      # 20 顆當前單位合成 1 顆下一階單位
GROWTH_STAGES   = ["seed", "sprout", "leaf", "bud"]   # 4 層 20:1 合成後開花
# BLOOM 是終點；種子→開花 = 10,000 × 20^4 = 1,600,000,000 食物 token
```

每階單位成本是前一階的 20 倍：種子 10,000、幼芽 200,000、盆栽 4,000,000、花苞 80,000,000；20 顆花苞合成開花，門檻為 1,600,000,000。`current_exp` 在同一世代內持續累積，合成時不歸零；`stage` 取目前能合成的最高階，`count` 是該階已合成數（0–19，其中只有尚未獲得第一顆種子時會是 0）。只有開花後的下一次進食才開始新世代。

#### 階段 emoji 與世代循環

| 階段 | emoji | 說明 |
|---|---|---|
| seed | 🌰 | 種子 |
| sprout | 🌱 | 發芽 |
| leaf | 🪴 | 幼苗 |
| bud | 🌷 | 含苞 |
| bloom | 🌸 | 開花（終點） |

**Bloom 之後**：停在開花畫面，下一次 token 進帳時 `generation += 1`、當前世代 progress 歸零、回到 seed 階並再以每 10,000 食物 token 累積種子，但 lifetime total token 繼續累積保留。

> 實作註：`current_exp` 欄位語意 = 當前世代累計食物；`level`/`stage`/`count` 由它推導
> （`game.plant_view()`）。第 `i` 階單位成本為 `TOKENS_PER_UNIT * UNITS_PER_STAGE**i`；
> 開花門檻為 `TOKENS_PER_UNIT * UNITS_PER_STAGE**len(GROWTH_STAGES)`。

---

## 6. CLI 設計

```bash
token-sprout init            # 初始化 ~/.token-sprout/ 與 plant_state.json
token-sprout proxy [--port 8000]   # 啟動 proxy（僅綁 127.0.0.1）
token-sprout run -- <command>      # 底層／手動入口：自動確保 proxy 在跑，
                                   # 將 ANTHROPIC_BASE_URL 只注入子行程
                                   # 例：token-sprout run -- claude
token-sprout install-claude        # 一次性安裝 managed shell function；之後只打 claude
token-sprout uninstall-claude      # 只移除上面的 managed block
token-sprout watch           # 植物動畫 UI（完整版，獨立終端機）
token-sprout statusline      # 給 Claude Code statusLine hook 的單行植物：
                             # 消耗 token 時出現（含即時估算計數），閒置時整行消失；
                             # --always 改為閒置時顯示安靜縮小版
token-sprout install-statusline  # 一鍵把上面那行掛進 ~/.claude/settings.json：
                             # 自動探測絕對路徑、merge 既有設定、不覆蓋別人的
                             # statusLine、寫入前自我測試。這是建議的設定方式，
                             # 取代要使用者手動編 JSON。
token-sprout uninstall-statusline # 只在現有 command 確認是 Token Sprout 時移除
token-sprout status          # 一次性顯示 tokens / EXP / level / stage / generation
token-sprout reset           # 重置植物狀態（必要功能，不是可選——數值一定會調錯，
                             # 這是使用者和開發者的逃生門）
```

### statusline 模式（免開第二個終端機）

Claude Code 的 `statusLine` 設定會以約 300ms 節流執行指定指令，並把輸出顯示在
同視窗底部。`token-sprout statusline` 讀取 `plant_state.json` 輸出單行植物；
「思考中」的即時 token 數字來自 proxy 對串流文字量的節流估算
（`live_tokens_estimate`，約 4 字元 ≈ 1 token）。thinking 時的 emoji 顆數、階段與
單顆進度條是 `game.plant_view(state, pending_exp=live_tokens_estimate)` 的唯讀預覽：
視同暫時把估算食物加到 `current_exp`，但不寫回 state；Bloom 後有 pending food 時
預覽下一世代。回應結束時仍只以真實 usage 結算並校正。`--always` 閒置模式維持
安靜縮小版，不顯示水滴、進度條或估算數字。
在 TUI 上疊浮動視窗（右下角 overlay）已評估並否決：需要攔改 Claude Code 的
畫面輸出，違反「不弄壞 Claude Code」原則。真正的視窗右下角體驗由 v0.2 的
VSCode 狀態列 extension 承接。

---

## 7. 安全模型

> 原則：**用可驗證的事實取代絕對化承諾**。「100% local、不傳任何資料到外部伺服器」
> 這類說法與 proxy 的轉發本質矛盾，發布後第一個留言就會被打臉。

### README 安全聲明（定稿措辭）

措辭原則：**逐句可驗證，且完整揭露所有磁碟寫入**——不可再宣稱「唯一寫入的是
plant_state.json」，因為現在還有 proxy.log、proxy.secret、state.lock，且
`install-statusline` 會改 `~/.claude/settings.json`；`install-claude` 會在 shell rc
加入可辨識、可移除的 managed block。README 的定稿版本列出每個寫入位置與內容。
最後一行「proxy 幾百行可自己讀」仍是真正的信任來源。

### v0.1 資安設計（實作層）

- **Proxy 沒有憑證概念**：auth headers 原樣透傳、不讀不記不存；同時支援 API key 與訂閱 OAuth。
- **request/response body 不落地、不進 log**；只儲存 usage metadata 與時間戳。
- **子行程啟動提示不回印 arguments**：只顯示 executable 名稱；初始 prompt、檔案路徑或其他 CLI arguments 不進 terminal scrollback。
- **`run` 的 proxy 身份驗證（防跨使用者搶 port 竊取憑證）**：`~/.token-sprout/proxy.secret`（0600）+ health endpoint 的 port-bound HMAC challenge。proof 的 normative payload 是 `token-sprout-health-v1\0<configured-port>\0<nonce>`；`configured-port` 必須來自 proxy 啟動設定，不可信任 request 的 Host/header/query。health JSON 回傳 `app`、`version`、`port`、`proof`，`run` 同時驗 port 與 proof。如此其他 port 上的真 proxy 不能被當成 signing oracle 來冒充目標 listener。假 proxy 驗證失敗時，`run` 拒絕重用、不注入 `ANTHROPIC_BASE_URL`。同使用者的惡意 process 本就能讀你的 key/OAuth，屬威脅模型外。
- **state 寫入不阻塞轉發路徑**：proxy 主路徑只做 non-blocking 的 `writer.submit()` / `writer.submit_latest()`（O(1) queue operation），實際檔案寫入在背景 thread（即 spec §5.2 的 single writer）。符合「轉發路徑不得 await 植物邏輯」。
- **response headers 完全透傳**：保留重複 header（set-cookie/warning/link 等），不壓扁成 dict。
- **`--upstream` 是隱藏旗標**：預設只送 Anthropic；指向非官方 upstream 會在 stderr 印明顯警告（credential-routing footgun 防呆）。
- **plain `claude` 安裝器**：只 merge 目前 shell rc 中有明確 begin/end marker 的 managed function，不覆蓋 Claude 官方 executable/symlink；靜態偵測目標 rc，且 managed block 執行時再防守由其他 sourced files 定義的 claude alias/function。function 只呼叫 `token-sprout run`，不把 `ANTHROPIC_BASE_URL` 持久化。uninstall 只刪除自己的 block。
- **私有本機檔案**：state home `0700`，其五個檔案固定 `0600`，不依賴 umask。
- **Claude gateway 相容性**：`run` 在未設定時注入 `ENABLE_TOOL_SEARCH=true`；README 揭露 Remote Control 在 localhost gateway 下不可用。
- 僅綁定 `127.0.0.1`。
- logging 政策寫成測試：斷言 log 輸出不含 body 樣本、auth header 樣本不出現。
- 禁用任何會 dump 區域變數的 traceback 美化（如 rich 的 `show_locals`）。

---

## 8. Repo 結構

```text
token-sprout/
├── .github/workflows/
│   └── test.yml           # Python 3.10–3.13 CI
├── README.md
├── README.zh-TW.md
├── SECURITY.md
├── docs/
│   └── token-sprout-overview.svg
├── pyproject.toml
├── LICENSE                # MIT
├── token_sprout/
│   ├── __init__.py
│   ├── cli.py
│   ├── proxy.py           # 目標 < 300 行，可稽核性優先
│   ├── usage_parser.py    # SSE / JSON usage 擷取（與轉發解耦）
│   ├── state.py
│   ├── game.py
│   ├── ui.py
│   └── ascii_art.py
├── examples/
│   └── plant_state.example.json
├── tests/
│   ├── test_game.py       # 純函式，重點測試對象
│   ├── test_state.py      # atomic write / 併發 / 壞檔復原
│   ├── test_usage_parser.py  # 用錄好的真實 SSE fixture 餵
│   └── test_proxy_smoke.py   # mock upstream 的 smoke test（僅此而已，
│                             # proxy 的真正驗證是 Milestone 0 實機測試）
└── assets/
    └── demo.gif
```

| 檔案 | 職責 |
|---|---|
| `cli.py` | CLI entrypoint（含 `run --` wrapper） |
| `proxy.py` | 透明轉發：catch-all、raw bytes、tee、fail-open |
| `usage_parser.py` | SSE 事件重組與 usage 擷取 |
| `state.py` | single-writer state、filelock、atomic rename |
| `game.py` | token → EXP → level / stage / generation |
| `ui.py` | rich terminal UI（read-only） |
| `ascii_art.py` | 植物 ASCII art |

---

## 9. v0.1 Milestones

> 排序原則：**風險最高的先驗證**。M0 沒全數通過，不動 M1 之後的任何程式碼；
> M0 若失敗，切換備援架構（hooks + transcript，見 §1）。

### Milestone 0 — 技術可行性驗證（擴充版，最重要）

目標：證明透明 proxy 在真實 Claude Code 工作負載下完全隱形。第一版藍圖只驗 happy path，但 proxy 的死法全在 unhappy path。

前置：

- [x] 用 mitmproxy（或最簡 echo proxy）錄一段真實 Claude Code session，盤點所有被呼叫的路徑、headers 全集、SSE 事件序列（含 `ping`、mid-stream `error`）。**這份錄檔就是轉發規格的驗收基準與測試 fixture 來源。**

驗證清單：

- [x] 啟動 `127.0.0.1:8000` proxy，Claude Code 經 `ANTHROPIC_BASE_URL` 連上。
- [x] **API key 模式**完整跑一個多輪 session（含工具呼叫）。
- [ ] **訂閱 OAuth 模式**完整跑一個多輪 session（Claude Code 主流用法，不可跳過）。
- [ ] 使用者按 Esc 中斷 streaming → Claude Code 行為與直連無異。
- [x] 上游錯誤（用假 key 觸發 401；或模擬 429）→ 原樣透傳，client 正常顯示錯誤。
- [ ] `curl -N` 實測 streaming 零緩衝：first-token 延遲與直連相當。
- [x] `/v1/messages/count_tokens`、`/v1/models` 等其他路徑正常透傳。
- [x] Proxy 能從 SSE 擷取 `input_tokens` / `output_tokens` / cache 兩欄（usage 不寫 log，只進 state）。
- [x] Fail-open 實測：故意讓解析器 crash、鎖住 state 檔 → 轉發照常成功。

### Milestone 1 — State System + 數值校準

- [x] `plant_state.json` schema（§5.2）。
- [x] state 初始化、壞檔復原。
- [x] atomic write（temp + rename）+ filelock。
- [x] `active_requests` 計數與 usage 累積。
- [x] level / stage / generation 計算（`game.py` 純函式 + 測試）。
- [ ] **用一天真實流量回放校準升級曲線**（§5.4 硬性需求）。
- [x] `token-sprout status`。

### Milestone 2 — Terminal UI

- [x] rich live layout（固定最小尺寸）。
- [x] 植物階段顯示、tokens / EXP / level / generation。
- [x] `active_requests > 0` 時澆水動畫。
- [x] response 結束後顯示 last request tokens。
- [x] level up 一行訊息 + 換圖（無特效）。
- [x] Bloom → 新一代種子的畫面轉場。

### Milestone 3 — Packaging & Release

- [x] `pyproject.toml`、CLI entry points、sdist/wheel build，以及乾淨 `pipx install .` 已實測；macOS Python 3.13 從本機 source 安裝到 `token-sprout --version` 為 28.91 秒，低於 5 分鐘門檻。
- [ ] 仍需在已可用 `uvx` 的乾淨環境驗證同一個 5 分鐘門檻；本次發布檢查的 uv launcher 下載未能在執行環境的單次下載時間窗內完成，不可視為驗證通過。
- [ ] Windows 10/11 + WSL2 Ubuntu 需完成一次從 Claude Code 安裝、`pipx install .`、shell integration 到真實 session 的端到端實機檢查。
- [x] `token-sprout run -- claude` wrapper。
- [x] `install-claude` / `uninstall-claude` managed shell function 與 plain `claude` UX。
- [x] 英文與繁中 README（含 §7 安全聲明定稿、§12 FAQ、「不要寫進 shell rc」警告、支援範圍聲明）。
- [x] logging 政策測試（不含 body、usage、auth headers）。
- [ ] Demo GIF。
- [ ] 標記 `v0.1.0` release。

> Issue / PR templates 不是發布阻擋項，發布後再補。

---

## 10. 團隊分工

v0.1 建議 2–3 人。

### Role 1 — Proxy / Backend Engineer

- 透明轉發（catch-all、raw bytes、header 政策、timeout）。
- SSE tee 與 usage parser。
- fail-open 保證與錯誤透傳。
- Milestone 0 全部驗證項目。
- state write。

### Role 2 — Terminal UI / Game Loop Engineer

- rich UI、ASCII plant rendering、澆水動畫。
- game logic（含曲線校準）。
- state read / polling。

### Role 3 — Product / DevRel / Open Source

- README、安全聲明、FAQ。
- install guide、demo GIF。
- Reddit / Hacker News launch copy 與提問應對（見 §12）。

---

## 11. README 主打文案草稿

```markdown
# Token Sprout 🌿

Grow a tiny terminal plant with the tokens you spend on AI coding agents.

Token Sprout is a local pass-through proxy for Claude Code.
It reads token usage metadata from API responses and turns it into plant
growth — replacing the anxiety of waiting with a small sense of progress.

Your requests go to exactly one place: the Anthropic API they were already
going to. Prompts, completions, and credentials pass through in memory only —
never stored, never logged. The proxy is a few hundred lines; read it yourself.

Just a local plant eating your tokens.
```

---

## 12. Launch Strategy

### GitHub Release 重點

- 清楚 README + 一張 demo GIF。
- §7 安全聲明。
- 支援範圍聲明：

```text
Currently tested with Claude Code (API key and subscription login) through
ANTHROPIC_BASE_URL. Other Anthropic API clients may work but are untested.
```

### 發布前必須準備好答案的問題（HN / Reddit 必問）

| 預期質疑 | 回答要點 |
|---|---|
| 「為什麼要 MITM 我的 AI 流量？讀本地 log 不就好了？」 | §1 比較表：即時 thinking 訊號只有 proxy 拿得到；proxy < 300 行可自行稽核；transcript 格式未文件化且限 Claude Code |
| 「我的 API key / OAuth token 安全嗎？」 | proxy 不讀、不記、不存憑證，原樣透傳；logging 政策有測試背書 |
| 「會不會拖慢 Claude Code？」 | byte-level 零緩衝轉發，M0 有 first-token 延遲實測數據 |
| 「Claude Code 改版會不會壞？」 | catch-all 透傳對 schema 無假設；usage 解析失敗只是不計分，不影響使用 |
| 「這不就是鼓勵浪費 token？」 | tongue-in-cheek 定位，文案自嘲比辯解有效 |

### 推廣渠道

- Hacker News：`Show HN: Token Sprout – grow a terminal plant with your AI coding tokens`
- Reddit：`r/ClaudeAI`、`r/Python`、`r/commandline`、`r/opensource`
- X / Twitter：短影片或 GIF。
- GitHub Topics：`claude-code`、`anthropic`、`terminal-ui`、`developer-tools`、`python`、`ai-tools`

---

## 13. v0.2 延伸方向

v0.1 成功後再考慮：

- MCP server companion（`get_plant_status()`、`rename_plant()` 等）。
- 多植物種類、成就系統。
- 每日 token 統計、本地 SQLite history。
- 升級特效與更豐富動畫。
- 支援 OpenAI-compatible gateway。
- 支援 Aider、Continue、OpenCode 等其他 agents（每個 client 都需獨立驗證 header / auth 行為）。
- VSCode sidebar extension。
- 可匯出的成長卡片。
- hooks / transcript 模式作為免 proxy 的輕量選項。

---

## 14. v0.1 成功標準

v0.1 成功不等於功能很多，而是 demo 清楚、proxy 隱形。

- 使用者能在 5 分鐘內安裝並跑起來（實際計時驗證過）。
- Claude Code 經 proxy 使用時，**行為、延遲、錯誤處理與直連無可感知差異**（含中斷、上游錯誤情境）。
- 植物邏輯任何故障都不影響 AI 請求（fail-open 實測通過）。
- Terminal 裡的植物會在 AI thinking 時動起來；response 結束後 token 增加。
- 成長符合 20:1 階層合成規則，Bloom 後的下一次進食會自動開始下一代。
- README 能讓人一眼理解用途與安全模型，且安全聲明經得起逐句檢驗。

核心畫面應該傳達：

```text
AI is thinking.
Your plant is drinking tokens.
```
