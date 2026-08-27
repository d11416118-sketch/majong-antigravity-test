# 台灣 16 張麻將線上牌桌

<p align="center">
  <img src="static/icons/app-icon-192.png" width="144" height="144" alt="台灣麻將 PWA 圖標">
</p>

這是一個以 Flask、Flask-SocketIO 與原生 JavaScript 製作的多人台灣麻將網頁遊戲。由一台電腦啟動遊戲伺服器，其他玩家可使用桌面或手機瀏覽器連線；最簡單的使用方式，是讓四位玩家連接同一個 Wi-Fi／區域網路，再透過六位數房號加入同一張牌桌。

本專案目前以「可自行架設的基本麻將牌桌」為主要定位。遊戲規則與房間狀態由伺服器統一判定，瀏覽器只負責顯示牌桌、送出玩家操作及接收即時更新。

## 主要功能

- 台灣 16 張麻將的發牌、摸牌、打牌與補花流程。
- 吃、碰、明槓、暗槓、加槓、胡牌、自摸、聽牌及過水等動作。
- 牌桌上方明確顯示目前由誰出牌，或仍在等待哪些玩家回應吃、碰、槓、胡或過。
- 莊家、連莊、風位、花牌與多種台型的計分處理。
- 四人自訂房：房主建立房間，其他玩家以六位數房號加入，第四位玩家進房後自動開局。
- 一局結束後由四位玩家準備下一局；整場結束後可投票留在同房再戰。
- 帳號註冊、登入及工作階段恢復，資料儲存在主機的 SQLite 資料庫。
- 斷線重連、回到原座位、回合操作倒數及離線代理。
- 每位玩家只會收到自己可以看到的牌局資訊；其他玩家的暗牌及牌牆順序不會傳給 AI 或前端。
- 響應式桌面／手機介面，以及基本 PWA manifest、service worker 與應用程式圖示。
- 可選的文字、貼圖與圖片房間聊天。
- 預設不需要 API Key 的本地啟發式 AI；亦可選擇使用本機 Ollama，或串接 Gemini、NVIDIA 及其他 OpenAI-compatible 模型。

完整且以目前程式實作為準的玩法，請閱讀 [RULES.md](RULES.md)。

> GitHub 在這個專案中是程式下載來源，不是遊戲伺服器。GitHub Pages 無法執行 Flask、Socket.IO、SQLite 或 Ollama；下載後仍需由其中一位玩家的電腦啟動伺服器，其他玩家再連線到那台電腦。

## 運作方式

```text
玩家瀏覽器
    │  HTTP + Socket.IO
    ▼
Flask-SocketIO 伺服器
    ├─ 房間、連線、倒數與廣播：app.py
    ├─ 牌局狀態與合法動作：backend/game_engine.py
    ├─ 麻將規則與胡牌判定：backend/rules.py
    ├─ 結算與台數：backend/settlement.py
    ├─ 公平可見資訊：backend/knowledge.py
    ├─ AI 決策與 API fallback：backend/visible_ai.py
    └─ 帳號與歷史紀錄：backend/accounts.py → data/mahjong.db
```

正在進行的房間與牌局保存在伺服器記憶體中，因此主機關閉程式或重新啟動後，未完成的牌局不會恢復。帳號、玩家資料與已寫入的歷史紀錄則會保留在 `data/mahjong.db`。

## 資料庫放在哪裡？

預設資料庫是專案根目錄下的 `data/mahjong.db`。它是 SQLite 單一檔案，實際位於啟動遊戲伺服器的主機上，不是公開的線上資料庫。其他玩家只會透過 Flask-SocketIO 遊戲伺服器註冊、登入及讀寫紀錄，不應直接連接或下載資料庫。

對一台主機、四位玩家的自用娛樂環境，保留本機 SQLite 是最簡單且合適的方式，不需要另外架設 MySQL、PostgreSQL 或雲端資料庫。

- 程式會以 `backend/accounts.py` 所在位置反推出專案根目錄；即使從錯誤的目前工作目錄執行，也不會在該目錄意外建立第二份 `data/mahjong.db`。
- 若設定 `MAHJONG_DB_PATH`，絕對路徑會直接使用；相對路徑仍以專案根目錄解析，不以啟動命令的目前目錄解析。
- Docker 會把主機的 `./data` 掛載到容器的 `/app/data`，所以重新建置或刪除容器不會刪掉主機資料庫。請勿在未備份時自行刪除 `data/`。
- 要搬到另一台主機時，先關閉遊戲伺服器，再連同 `data/mahjong.db` 複製到新主機相同的 `data/` 位置。
- 要備份時，也建議先停止伺服器再複製資料庫檔案，避免剛好遇到寫入中的交易。
- 不要把含有真實帳號資料的 `data/mahjong.db` 提交到 GitHub。
- 伺服器重啟後，資料庫中的帳號與歷史仍在，但記憶體中的進行中房間與未完成牌局會消失。
- 只有未來改成多台遊戲伺服器、雲端主機沒有永久磁碟，或需要多人同時大量寫入時，才需要改成獨立的線上資料庫。

開發測試帳號若使用本專案測試套件的 `名稱_八位十六進位字元` 格式，可先預覽、再備份並刪除：

```powershell
python scripts/cleanup_test_accounts.py
python scripts/cleanup_test_accounts.py --apply
```

`--apply` 會先在 `data/backups/` 建立完整 SQLite 備份，再於同一個交易中移除匹配帳號及其直接關聯資料，最後執行資料庫完整性檢查。

## 系統需求

- 建議使用 Docker Desktop（包含 Docker Compose）。Windows 使用者必須先啟動 Docker Desktop 並等待引擎就緒。
- 若不使用 Docker，則需要 Python 3.10 或更新版本、`pip` 與 Python 虛擬環境支援；此專案主要以 Python 3.10 驗證。
- 現代瀏覽器，例如 Chrome、Edge、Firefox 或 Safari。
- 多人區網遊戲時，主機與其他裝置必須位於可互相連線的同一個區域網路。
- Node.js 不是執行遊戲的必要條件；只有在執行 JavaScript 語法檢查時才需要。
- 若要使用本地語言模型代理，需另外安裝並啟動 Ollama，再準備 `qwen3:4b` 或自行指定的模型。

## 下載與安裝

請將 `<repository-url>` 與 `<repository-folder>` 換成實際的 GitHub 倉庫網址及下載後的資料夾名稱。下列指令不依賴任何開發者電腦上的固定路徑。

### GitHub Download ZIP：Windows 最短流程

1. 在 GitHub 專案頁面按 `Code` → `Download ZIP`。
2. 將 ZIP 完整解壓縮到一般資料夾；不要直接在壓縮檔預覽視窗內執行。
3. 安裝並啟動 Docker Desktop，等待狀態顯示引擎已就緒。
4. 若要使用千問 AI，在主機安裝 Ollama，然後執行 `ollama pull qwen3:4b`。只使用真人或 Python 備援 AI 時可略過。
5. 雙擊 `start_game.bat`。它會建置並啟動容器，等健康檢查通過後才打開瀏覽器。
6. 主機使用 `http://localhost:5001/`；同一 Wi-Fi 的其他玩家使用 `http://<主機IPv4>:5001/`。
7. 遊戲結束後雙擊 `stop_game.bat`。帳號與紀錄仍保留在解壓縮目錄的 `data/mahjong.db`。

第一次啟動需要下載 Docker 基礎映像與 Python 套件，因此會比之後啟動久。若移動遊戲資料夾，請連同 `data/` 一起移動，才能保留原有帳號。

### 建議方式：Docker

```powershell
git clone <repository-url>
cd <repository-folder>
docker compose config
docker compose up --build
```

Windows 也可以直接雙擊 `start_game.bat`。這個檔案不再呼叫不確定版本的 `python` 或 `pip`，而是先確認 Docker、Compose 與 Docker Desktop 引擎可用，再執行 Docker 建置、啟動及健康檢查。

第一次建置會下載 Python 基礎映像並安裝套件，之後只在 `Dockerfile`、程式或相依檔案變更時重建相關層。要停止服務可雙擊 `stop_game.bat`，或執行：

```powershell
docker compose down
```

若要使用本機 Ollama，先在主機安裝 Ollama 並準備模型：

```powershell
ollama pull qwen3:4b
```

容器預設以 `http://host.docker.internal:11434` 連回主機 Ollama。若主機環境需要其他位址，可在 `.env` 設定 `DOCKER_OLLAMA_API_BASE`。若連線被拒絕，先確認 Ollama 正在主機執行，並確認其監聽設定允許 Docker Desktop 存取。

### 替代方式：Windows PowerShell 直接執行 Python

```powershell
git clone <repository-url>
cd <repository-folder>

py -3.10 -m venv .venv
.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

Copy-Item .env.example .env
python run_server.py
```

如果電腦只有一個 Python 版本，也可以把 `py -3.10` 改成 `python`。

### 替代方式：Windows 命令提示字元

```bat
git clone <repository-url>
cd <repository-folder>

py -3.10 -m venv .venv
.venv\Scripts\activate.bat

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

copy .env.example .env
python run_server.py
```

### 替代方式：macOS／Linux 直接執行 Python

```bash
git clone <repository-url>
cd <repository-folder>

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

cp .env.example .env
python run_server.py
```

## 啟動與多人連線

Docker 啟動：

```bash
docker compose up --build
```

或不使用 Docker，直接啟動 Python：

```bash
python run_server.py
```

預設使用 `5001` 連接埠，啟動後終端會顯示類似以下內容：

```text
Local: http://127.0.0.1:5001/
Four clients: http://127.0.0.1:5001/test-clients
LAN: http://<主機區網IP>:5001/
LAN four clients: http://<主機區網IP>:5001/test-clients
```

- 主機自己使用 `http://127.0.0.1:5001/` 或 `http://localhost:5001/`。
- 直接執行 Python 時，可使用終端顯示的 `LAN` 網址。
- 使用 Docker 時，容器內顯示的 LAN IP 可能是 Docker 私有位址；請在 Windows 執行 `ipconfig`，找出目前 Wi-Fi／乙太網路介面的 IPv4 位址，讓其他裝置使用 `http://<主機IPv4>:5001/`。
- 不要把 `127.0.0.1` 傳給手機；在手機上，這個位址代表手機本身。
- 主機的區網 IP 可能在重新連線或路由器重新分配位址後改變，每次應以終端當次顯示的 `LAN` 網址為準。
- Windows 第一次啟動時可能顯示防火牆提示。若只在家用區網遊玩，僅允許「私人網路」即可。
- 主機必須持續執行伺服器；停止容器或 Python 程序會中止目前的房間。

### 玩家操作流程

1. 四位玩家分別打開遊戲網址。
2. 每位玩家註冊不同的帳號，或登入既有帳號。
3. 一位玩家在首頁的「自訂模式」建立房間。
4. 房主將畫面上的六位數房號傳給另外三位玩家。
5. 其他玩家在「自訂模式」輸入房號並加入。
6. 第四位玩家加入後，伺服器會自動開始牌局。
7. 手機遊玩時建議改為橫向顯示。

`/test-clients` 是開發者用的四客戶端測試頁面，不是一般玩家必須使用的入口。

## 環境變數

專案啟動時會讀取根目錄的 `.env`。建議先複製 `.env.example`，再依需求修改。

| 變數 | 預設值 | 用途 |
| --- | --- | --- |
| `HOST` | `0.0.0.0` | 監聽位址。使用 `0.0.0.0` 才能接受區網裝置連線；使用 `127.0.0.1` 則只允許本機。 |
| `PORT` | `5001` | HTTP 與 Socket.IO 使用的連接埠。 |
| `FLASK_DEBUG` | `0` | 使用 `python app.py` 時控制 Flask 除錯模式；建議的 `run_server.py` 入口固定關閉除錯。一般遊玩或對外部署時不應開啟。 |
| `SECRET_KEY` | 開發用預設值 | Flask 簽章金鑰。若部署到非信任網路，必須改成長且隨機的值。 |
| `MAHJONG_DB_PATH` | 專案根目錄的 `data/mahjong.db` | SQLite 路徑；相對值固定以專案根目錄解析。Docker 內固定使用 `/app/data/mahjong.db`。 |
| `AI_PROVIDER` | `heuristic` | AI 模式：`heuristic`、`ollama`、`gemini`、`nvidia`、`openai_compatible`。 |
| `OLLAMA_API_BASE` | `http://127.0.0.1:11434` | 主機上的 Ollama HTTP API。 |
| `DOCKER_OLLAMA_API_BASE` | `http://host.docker.internal:11434` | 僅供 Compose 設定容器連回主機 Ollama 的位址。 |
| `OLLAMA_MODEL` | `qwen3:4b` | Ollama 代理使用的模型。 |
| `OLLAMA_TIMEOUT_SECONDS` | `120` | 最長等待 Ollama 決策的秒數；期限到後由 Python 啟發式接手。 |
| `OLLAMA_KEEP_ALIVE` | `10m` | Ollama 完成請求後將模型保留在記憶體的時間。 |
| `AI_API_KEY` | 空 | NVIDIA 或 OpenAI-compatible API Key。 |
| `AI_API_BASE` | NVIDIA API base | OpenAI-compatible API 根網址；程式會呼叫其 `/chat/completions`。 |
| `AI_MODEL` | 空 | OpenAI-compatible 模型名稱。 |
| `GEMINI_API_KEY` | 空 | Gemini API Key。 |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini 模型名稱。 |
| `GEMINI_API_BASE` | Google Generative Language API | Gemini API 根網址。 |
| `AI_MAX_CONCURRENT_REQUESTS` | `1` | 同時允許的外部 AI 請求數。 |
| `AI_REQUEST_COOLDOWN_SECONDS` | `1.5` | 外部 AI 請求之間的最短間隔。 |
| `AI_CACHE_TTL_SECONDS` | `45` | 相同 AI 請求的快取秒數。 |
| `AI_TIMEOUT_SECONDS` | `8` | 外部 AI 請求逾時秒數。 |

只想使用基本麻將時，保留以下設定即可：

```env
AI_PROVIDER=heuristic
```

這個模式不需要 API Key，也不會連線到外部 AI 服務。

## AI 是怎麼決定動作的？

### 預設模式：本地啟發式程式

預設的 `AI_PROVIDER=heuristic` 不是本地大型語言模型，也不需要下載模型檔案。它是寫在 `backend/visible_ai.py` 與 `backend/strategy.py` 裡的固定 Python 評分邏輯。

AI 打牌時會比較手中每一張可丟棄的牌，主要考慮：

- 丟牌後的向聽數。
- 可改善牌型的有效牌數量。
- 順子、刻子、對子與相鄰牌形成的牌型分數。
- 孤張、么九牌、字牌與偏離主要花色的程度。
- 牌局後段根據已知牌計算的簡易安全性。
- 門風、圈風與三元牌等價值牌是否值得保留。

面對吃、碰、槓、胡等回應時：

- 有合法胡牌選項時，啟發式 AI 會優先胡牌。
- 其他動作會計算是否降低向聽數、增加有效牌或提高牌型價值。
- 吃、碰、槓分別有最低評分門檻；改善不足時會選擇 `PASS`。

這些運算都在主機的 Python 程序內完成，並沒有呼叫 Ollama、Transformers 或其他本地模型服務。

### 本機 Ollama／Qwen3 代理

若主機已安裝 Ollama，可先在終端準備模型：

```bash
ollama pull qwen3:4b
```

再於 `.env` 設定：

```env
AI_PROVIDER=ollama
OLLAMA_API_BASE=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:4b
OLLAMA_TIMEOUT_SECONDS=120
OLLAMA_KEEP_ALIVE=10m
```

這個模式不需要 API Key。AI 代理需要作決定時，伺服器會把該座位的公平可見狀態送到本機 Ollama `/api/chat`。Qwen3 只負責在合法動作中提出選擇，遊戲規則、胡牌判定與真正執行動作仍由 Python 伺服器控制。

Ollama 最多有 120 秒產生結果。若超時、服務未啟動、模型不存在、回傳格式錯誤或選擇非法動作，伺服器會立即改用本地 Python 啟發式。逾時後才回來的舊模型結果會被丟棄，不能造成第二次出牌。

`AI_MAX_CONCURRENT_REQUESTS=1` 會把 Ollama 模型請求限制為一次一個。正常出牌階段本來就只有當前玩家能出牌；棄牌回應階段可能同時有多位玩家具備胡、碰、槓或吃的權利，伺服器會依序取得 AI 決策，再依 [多人回應與優先順序](RULES.md#5-多人回應與優先順序)判定真正成立的動作。

### 公平資訊限制

無論使用固定程式或外部模型，AI 都先經過 `backend/knowledge.py` 建立該座位的可見狀態。它可以取得：

- 自己的手牌、花牌、副露及狀態。
- 四家的公開棄牌、花牌、副露、手牌張數與聽牌狀態。
- 目前回合、莊家、圈風、最後一張棄牌及牌牆剩餘張數。
- 由自己手牌及桌面公開牌推算的未知牌數量。
- 伺服器當下提供給該玩家的合法動作。

它不會取得其他玩家的暗牌、其他玩家私有的聽牌內容或牌牆的確切順序。

### AI 必須遵守的房規與安全界線

目前伺服器與 Ollama 提示詞使用以下規則；Python 遊戲引擎是最後裁判：

- 使用台灣 16 張：一般胡牌結構為五組面子加一對將。
- AI 只能使用該座位的公平可見資訊，不能推定其他玩家的確切暗牌或牌牆順序。
- AI 只能從伺服器提供的合法動作及合法牌索引中選擇；虛構或格式錯誤的動作會被拒絕並改用 Python。
- 同一張牌有多人可胡時，以伺服器最先收到的合法 `HU` 回應為勝者，不處理一炮多響。
- 有合法胡牌動作時，代理應優先胡牌。放棄可胡或可自摸會進入過水；完成安全的摸打循環後才解除。
- 宣告聽牌後手牌鎖定；地聽依宣告者自己的第一次棄牌及當時沒有副露判定。其他玩家中途吃碰不取消地聽，但過水會取消地聽加成。
- 宣告聽牌後，明槓補牌不能自摸；暗槓與加槓補牌可以自摸。符合既有強制加槓條件時仍由伺服器處理。
- 加槓會先開啟搶槓回應窗口；所有可回應玩家都放棄後才真正完成加槓。
- 自己回合的合法胡牌、既有自動槓／聽流程先由 Python 規則處理；需要選擇棄牌時才交給 Ollama。對他人棄牌的吃、碰、槓、胡或放棄也可由 Ollama 在合法清單中決定。
- Ollama 最多思考 120 秒。超時或失敗時由 Python 啟發式接管，過期的模型結果不得再次出牌。

### 可選的外部模型

如果把 `AI_PROVIDER` 改為 `gemini`、`nvidia` 或 `openai_compatible`，伺服器會把相同的公平可見狀態傳給外部模型，並要求模型只回傳 JSON 動作。

外部模型不能自行執行任意動作。伺服器會檢查它回傳的索引及動作，並只接受當下合法動作清單中存在的選項。若發生以下任一情況，程式會自動改用本地啟發式決策：

- 沒有設定 API Key 或模型名稱。
- 網路錯誤或請求逾時。
- 模型回傳格式錯誤。
- 模型選擇不存在或不合法的動作。
- API 請求正在忙碌或受到冷卻限制。

因此，外部 AI 是可選的決策顧問，不是遊戲規則的裁判；所有合法性與牌局狀態仍由遊戲伺服器控制。

### 回合逾時與 AI 代理的差異

- 玩家單純超過回合操作時間時，伺服器會隨機丟出一張合法牌，或對未回應的動作視為 `PASS`；這不會自動開啟完整 AI。
- 玩家主動開啟 AI 代理，或離線超過伺服器設定的等待時間後，才會由 `VisibleMahjongAI` 持續代打。使用 Ollama 時，代理回合可等待最多 120 秒；超時後由 Python 啟發式接手，而不是一般玩家逾時使用的隨機出牌。
- 玩家重新連線後，可以回到同一座位並結束該座位的離線代理狀態。

## PWA 與手機使用

手機可以直接使用瀏覽器開啟區網網址進行遊戲。GitHub 下載版已包含完整 PWA 圖標與安裝資訊：

- `app-icon-32.png`：瀏覽器分頁 favicon。
- `apple-touch-icon.png`：180×180 Apple 主畫面圖標。
- `app-icon-192.png` 與 `app-icon-512.png`：一般 PWA 圖標。
- `app-icon-maskable-512.png`：Android 等平台可安全裁切的 maskable 圖標。
- `manifest.webmanifest`：PWA 名稱、色彩、啟動網址及圖標宣告。
- `service-worker.js`：快取頁面外殼、樣式、程式與圖標。

### 手機安裝方式（加入主畫面）

本遊戲支援 PWA 技術，不需前往 App Store 或 Google Play 下載，可直接透過手機瀏覽器安裝至桌面，以全螢幕、無網址列的方式遊玩：

#### iPhone / iPad (iOS Safari)
> 必須使用內建的 **Safari** 瀏覽器開啟。
1. 使用 Safari 開啟遊戲網址（例如 `http://<主機區網IP>:5001/` 或公網 HTTPS 網址）。
2. 點擊瀏覽器底部的 **「分享」** 按鈕（方框帶向上箭頭圖示）。
3. 於選單中往下滑動，點選 **「加入主畫面」**（Add to Home Screen）。
4. 確認名稱為「台灣麻將」，點擊右上角 **「新增」** 即可在手機桌面生成專屬圖標。

#### Android 手機 (Chrome)
1. 使用 **Chrome** 瀏覽器開啟遊戲網址。
2. 點擊右上角選單按鈕 **「⋮」**。
3. 點選 **「加到主畫面」** 或 **「安裝應用程式」**。
4. 點選 **「安裝」**（或新增），完成後桌面即會建立獨立 App 圖示。

> 建議：手機開始遊玩後，建議旋轉為**橫向顯示**，以獲得最佳牌桌視野。

使用時仍要注意：

- `127.0.0.1` 只能供伺服器主機自己使用。
- 手機在相同 Wi-Fi 上瀏覽普通 HTTP 網址通常可以玩遊戲。
- 瀏覽器要把網站完整安裝成 PWA 時，非 `localhost` 網址通常需要 HTTPS。
- PWA 快取只能讓介面外殼載入；多人牌局仍必須連得上正在執行的遊戲伺服器，不能真正離線遊玩。
- 這是響應式網頁應用程式，不是 App Store 或 Google Play 的原生安裝包。

## 測試

### 完整 Python 回歸測試

```bash
python -m unittest discover -s tests -v
```

測試涵蓋帳號、四人房間、斷線重連、AI 可見資訊、遊戲流程、胡牌判定、計分與 PWA 包裝。

### PWA 包裝測試

```bash
python -m unittest tests.test_pwa -v
```

### HTTP 煙霧測試

先在一個終端啟動伺服器：

```bash
python run_server.py
```

再於另一個終端執行：

```bash
python tests/check_http.py
```

### 本機 Ollama 煙霧測試

先確認 Ollama 已啟動、模型已下載，而且 `.env` 已設定 `AI_PROVIDER=ollama`，再執行：

```bash
python tests/check_ollama.py
```

成功時會輸出模型名稱、推論秒數及合法的 JSON 出牌選擇。這項測試會真的呼叫本機模型，但不會讀寫玩家資料庫。

### JavaScript 語法檢查

需要預先安裝 Node.js：

```bash
node --check static/js/game.js
node --check static/js/renderer.js
```

## 專案結構

```text
.
├─ app.py                     # Flask-SocketIO 事件、房間與連線生命週期
├─ run_server.py              # Python 伺服器入口，會顯示本機及區網網址
├─ start_game.bat             # Windows Docker 啟動腳本
├─ stop_game.bat              # Windows Docker 停止腳本
├─ Dockerfile                 # 遊戲伺服器映像
├─ compose.yml                # 連接埠、資料庫掛載與 Ollama 主機連線
├─ RULES.md                   # 本版本完整遊戲規則與台數表
├─ requirements.txt           # Python 套件
├─ .env.example               # 可公開的環境變數範例
├─ backend/
│  ├─ accounts.py             # 帳號、玩家資料與歷史紀錄
│  ├─ game_engine.py          # 牌局狀態機
│  ├─ rules.py                # 合法動作、胡牌與牌型規則
│  ├─ settlement.py           # 結算與分數
│  ├─ knowledge.py            # 每位玩家／AI 的公平可見資訊
│  ├─ visible_ai.py           # 啟發式 AI 與外部模型介面
│  └─ strategy.py             # 向聽數與牌型評分輔助
├─ templates/                 # HTML 頁面
├─ static/                    # JavaScript、CSS、模型、圖示與 PWA 檔案
├─ scripts/                   # 資料維護工具
├─ tests/                     # 單元、Socket.IO、規則與包裝測試
└─ data/mahjong.db            # 執行後產生的本機 SQLite 資料庫，不應提交
```

## GitHub 上傳前的安全檢查

請勿提交下列本機或敏感檔案：

- `.env`：可能包含 API Key 與正式環境設定。
- `data/mahjong.db`：包含本機帳號、密碼雜湊、工作階段 token 與遊戲資料。
- `*.log`：可能包含執行紀錄或錯誤資訊。
- `.venv/`、`__pycache__/`、`*.pyc`：本機環境與快取。
- 編輯器或個人工具資料夾，例如 `.vscode/`、`.gemini/`，除非確定內容適合公開。

專案已包含 `.gitignore`；第一次提交前仍應確認至少保留以下規則：

```gitignore
.env
.venv/
venv/
__pycache__/
*.py[cod]
data/*.db
*.log
.vscode/
.gemini/
```

只提交不含真實秘密的 `.env.example`。如果 API Key 曾被提交到 Git、GitHub、公開 ZIP 或聊天內容，不應只刪除檔案，還必須到供應商後台撤銷並重新產生 Key。

### 第一次上傳 GitHub

專案準備完成後，可在專案根目錄執行：

```powershell
git add .
git status
git commit -m "Initial Taiwanese Mahjong release"
git remote add origin <repository-url>
git push -u origin main
```

執行 `git add .` 後一定要先看 `git status`，確認沒有 `.env`、`data/mahjong.db`、資料庫備份、日誌或個人工具資料夾。若 Git 尚未設定姓名與信箱，請依 Git 顯示的提示設定後再 commit。

## 公開部署注意事項

目前的啟動方式適合本機開發及受信任的私人區域網路。若要讓不同網路的玩家從網際網路連線，至少還需要：

- HTTPS 與正式網域或安全的私人虛擬網路。
- 反向代理及支援 WebSocket／Socket.IO 的部署環境。
- 隨機且保密的 `SECRET_KEY`。
- 防火牆、存取限制、備份及資料保護策略。
- 依預期人數進行並行連線與長時間牌局測試。

不建議直接把開發伺服器連接埠暴露到公開網際網路。

## 授權

此專案目前尚未在倉庫中指定開源授權。若要允許其他人使用、修改或散布程式碼，請在公開前選擇合適的授權並加入 `LICENSE` 檔案。
