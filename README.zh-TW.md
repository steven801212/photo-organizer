# Photo Organizer

[English](README.md) | **繁體中文（台灣）**

Photo Organizer 是一套適合放在 Synology NAS 上運作的私人照片整理服務。

你只要把照片放進自己的 Inbox，先在網頁上確認分類結果，再按下「開始整理」，系統就會根據照片的實際拍攝日期，自動整理到個人圖庫。每位使用者都有自己的 Inbox、圖庫、索引、備份與其他檔案隔離區，彼此不會混在一起。

目前版本：**v1.0.1**。升級步驟請看 [1.0.1 發布說明](RELEASE-NOTES.md)。

下載安裝包：[GitHub Releases](https://github.com/steven801212/photo-organizer/releases/latest)。Synology 部署請選擇 `photo-organizer-v1.0.1-synology.zip`，並閱讀 [NAS 安裝指南](SYNOLOGY.md)。

> 定位為家庭或個人內網使用。同一組圖庫與索引由單一 Organizer Service 容器管理；重要照片仍應另外使用 Hyper Backup 或其他方式備份。

## 它可以做什麼？

### 自動整理照片

- 讀取 EXIF 拍攝時間，不依賴檔名判斷日期。
- 依照「種類 → 年份 → 日期」建立清楚的資料夾。
- 支援 JPG、JPEG、PNG、TIFF、HEIC、HEIF。
- 支援 Sony、Canon、Nikon、Panasonic、Fujifilm 等常見 RAW 格式。
- 支援 MOV、MP4 影片。
- 找不到拍攝日期的檔案會移到 `_Unsorted`，不會亂猜日期。
- 搬移前先顯示目的地，使用者確認後才真正整理。

整理後的圖庫大致如下：

```text
PhotoLibrary/
└─ steven/
   ├─ JPG/
   │  └─ 2023/
   │     └─ 2023-02-04/
   ├─ RAW/
   │  └─ 2023/
   │     └─ 2023-02-04/
   ├─ VIDEO/
   ├─ LIVE/
   ├─ _Duplicates/
   ├─ _Unsorted/
   └─ _Trash/
```

### iPhone Live Photos

- 自動辨認同一資料夾、同一檔名的 `JPG/HEIC + MOV`。
- 靜態照片與 MOV 會一起整理到 `LIVE/年份/日期`。
- 圖庫只顯示一個 Live Photo 項目，避免同一張照片出現兩次。
- 放大預覽時可以切換播放配對的 MOV。
- 下載、移到回收站及還原時，照片與 MOV 會成組處理。
- 可先重新索引舊圖庫，再預覽及整理既有、分散存放的 Live Photos。

### RAW、XMP 與 HEIC

- RAW 照片會整理到獨立的 `RAW` 分類。
- 同檔名的 `.xmp` sidecar 會跟著 RAW 一起搬移及復原，不會被誤判為無關檔案。
- Docker 映像內建 HEIC/HEIF 解碼器。
- 網頁縮圖與 1600px 放大預覽會轉成瀏覽器通用的 sRGB JPEG。
- 會套用照片方向資訊及處理 ICC 色彩描述檔，減少預覽方向或顏色異常。

### 重複檔案保護

- 使用 SHA-256 比對檔案內容，不只比較檔名。
- 重複照片不會覆寫原檔，會移到 `_Duplicates`。
- 同名但內容不同的檔案會自動產生安全的新名稱。
- 同一次匯入中的重複檔案也能辨認。

### 其他檔案隔離

Inbox 裡的 PDF、EXE、ZIP、文件或其他非媒體檔案不會被忽略或刪除，而是集中到：

```text
PhotoOrganizer/rejected/<使用者>/<匯入批次>/
```

- 保留原本的子資料夾結構。
- 不會執行 EXE、BAT 等程式檔。
- 內容完全相同的其他檔案會保留在個人的 `_Duplicates`。
- 整理完成後，系統會由最深層開始清除 Inbox 的空子資料夾。
- `PhotoInbox/<使用者>` 根目錄永遠保留，方便電腦繼續上傳。
- 尚未複製完成、已變更或處理失敗的檔案會留在 Inbox，不會冒險移除。

### 網頁照片圖庫

- 按年份及日期分層瀏覽，照片與日期由近到遠排列，日期未知項目置底。
- 年份預設收合，需要時再展開；介面隨視窗寬度延展，自動調整每列照片數。
- 顯示照片縮圖，支援載入更多照片。
- 依檔名、日期、相機、鏡頭、ISO、焦距搜尋。
- 依 JPG/HEIC、RAW、影片、Live Photo 篩選。
- 收藏最愛照片。
- 建立每位使用者自己的私人相簿。
- 多選照片並下載 ZIP，單次最多 200 個檔案。
- 以互動式 OpenStreetMap 地圖瀏覽具有 GPS 資訊的照片。
- 地圖標記直接顯示照片縮圖；同一地點會顯示照片數量，點擊即可開啟完整預覽與 EXIF。
- 進入地圖會自動縮放到所有拍攝地點，也可隨時按「顯示所有地點」回到全覽。
- 地圖程式已內建於 Docker 映像；底圖需由瀏覽器連線至 OpenStreetMap 載入，不會把原始照片上傳給地圖服務。

### 放大預覽與 EXIF

- 方向鍵切換上一張或下一張照片。
- 滑鼠滾輪縮放，雙擊恢復原始比例。
- EXIF 資訊放在照片旁邊，不遮住圖片。
- 顯示拍攝時間、相機品牌、機身、鏡頭、焦距、光圈、快門、ISO、曝光補償、閃光燈、尺寸、GPS 與海拔等資訊。

### 掃描與整理進度

- 顯示目前處理階段。
- 匯入預覽每頁最多 500 筆，可使用上一頁與下一頁瀏覽。
- 500 筆只是網頁分頁大小；「開始整理」會由後端處理完整掃描計畫，沒有匯入張數上限。
- 顯示已處理數量、總檔案數、百分比及目前檔名。
- 掃描或整理期間可以要求停止。
- 停止會在目前檔案的安全處理邊界生效，避免搬到一半造成檔案損壞。
- 重新整理網頁後，仍可從服務端取得目前工作狀態。
- 工作狀態與掃描計畫寫入個人索引目錄；服務重啟後會明確標示上次工作已中斷。

### 多使用者隔離

- 管理員可新增、停用、啟用使用者及重設密碼。
- 密碼只要求不可空白，適合受控的家庭內網環境。
- 每位使用者擁有完全分開的 Inbox、PhotoLibrary、SQLite 索引、縮圖快取、索引備份及其他檔案隔離區。
- 目前沒有家庭共用圖庫，不會把不同使用者的照片混合顯示。

### 安全回收站

- 圖庫移除操作會先移到 `_Trash`，不會立即永久刪除。
- 可選擇照片還原及設定保留天數。
- 手動永久清除需要再次確認；自動清除為獨立選項，預設關閉，只有明確勾選後才執行。
- Live Photo 的照片與 MOV 會一起移除或還原。

### 索引、備份與修復

- 每位使用者使用獨立 SQLite 索引。
- 每次正式匯入前後會自動備份索引，也可以手動備份。
- 還原前會再建立目前索引的安全備份。
- 備份與還原後都會執行 SQLite 完整性檢查。
- 「重新索引」可以在搬移 NAS 資料、重裝 Windows 或更換電腦後，重新掃描既有圖庫並修正路徑。
- 索引健康檢查可找出遺失檔案與尚未建立索引的圖庫檔案。
- 安全修復不會重新整理或搬動正式圖庫中的照片。

### 自動化與通知

- 可設定定時掃描 Inbox。
- 可選擇只掃描，或掃描後自動整理。
- 自動整理預設關閉，必須由使用者自行啟用。
- 可設定 Webhook，在完成或失敗時傳送通知。

## 安全設計

Photo Organizer 採取偏保守的檔案處理方式：

1. 掃描預覽不會搬移任何檔案。
2. 目的檔永不直接覆寫。
3. 同一個檔案系統內優先建立不可覆寫的硬連結，索引提交後才移除 Inbox 來源。
4. 跨檔案系統時先複製，再驗證 SHA-256，成功後才移除來源。
5. 若資料庫寫入失敗，會盡可能復原搬移並保留 Inbox 原檔。
6. 程式異常時寧可留下額外副本，也不刪除唯一的一份資料。
7. 每次整理都寫入 manifest 與操作紀錄，支援安全復原。
8. DSM 的 `@eaDir`、`@tmp`、`.DS_Store`、`Thumbs.db` 及未完成暫存檔會在底層直接忽略。

即使有這些保護，照片仍應至少保留另一份獨立備份。RAID 不能取代備份。

## Synology 快速安裝

需求：DSM 7.2、Container Manager，以及可執行 Docker 的 Synology NAS。主要部署目標為 x86-64 NAS（例如 DS1821+）；已完成本機回歸測試，但尚未驗證所有 NAS 機型。

### 1. 建立資料夾

在共享資料夾 `Organized Photo` 下建立：

```text
/volume1/Organized Photo/PhotoInbox
/volume1/Organized Photo/PhotoLibrary
/volume1/Organized Photo/PhotoOrganizer/app
/volume1/Organized Photo/PhotoOrganizer/data
/volume1/Organized Photo/PhotoOrganizer/backups
/volume1/Organized Photo/PhotoOrganizer/rejected
```

### 2. 放入程式

將發行 ZIP 解壓縮後，把內容放進：

```text
/volume1/Organized Photo/PhotoOrganizer/app
```

更新版本時只替換 `app` 裡的程式檔案。不要刪除 `data`、`backups`、`PhotoLibrary`、`PhotoInbox` 或 `rejected`。

### 3. 設定管理員密碼

將 `.env.example` 複製成 `.env`：

```env
PHOTO_ADMIN_PASSWORD=your-password
```

既有安裝更新時，帳號資料庫會保留，不會用 `.env` 覆蓋現有密碼。

如果希望容器使用指定的 DSM UID/GID 建立檔案，可使用 `id <DSM使用者>` 查詢後另外設定：

```env
PUID=1026
PGID=100
```

兩項都沒有設定時會保留舊版的 root 執行模式，避免升級後因現有 NAS 權限而無法啟動。

### 4. 建立服務

在 Container Manager 使用 `docker-compose.yml` 建立並啟動專案。第一次建置會下載 Python 套件並安裝 ExifTool，因此需要一些時間。

完成後開啟：

```text
http://NAS-IP:8080
```

正式使用時建議透過 Synology 反向代理提供 HTTPS，而且只允許內網或 VPN 存取。

## NAS 資料位置

以使用者 `steven` 為例：

```text
PhotoInbox/steven/                         等待整理的檔案
PhotoLibrary/steven/                       整理完成的個人圖庫
PhotoOrganizer/data/users/steven/          個人索引與縮圖快取
PhotoOrganizer/backups/steven/             個人索引備份
PhotoOrganizer/rejected/steven/            個人其他檔案隔離區
PhotoOrganizer/data/system/accounts.db     系統帳號資料庫
```

多台電腦可以把檔案放進同一位使用者的 Inbox，實際掃描、比對及整理工作統一由 NAS 上的服務執行。

## 日常使用方式

1. 把照片或舊資料夾複製到自己的 `PhotoInbox/<使用者>`。
2. 等待電腦完成複製。
3. 登入 Photo Organizer，按「掃描 Inbox」。
4. 查看照片、重複檔、無日期檔及其他檔案數量。
5. 確認目的地後按「開始整理」。
6. 完成後到照片圖庫瀏覽，或在 File Station 查看實際資料夾。

第一次整理大量舊照片時，建議先複製一小批測試，確認分類符合預期後再放入完整資料。

## 與 Immich 搭配

Photo Organizer 不要求 Immich 讀取它的 SQLite 索引。Immich 只需要把以下位置設定成 External Library：

```text
/volume1/Organized Photo/PhotoLibrary/<使用者>
```

建議在 Immich 排除 `_Trash`、`_Duplicates` 及 `_Unsorted`。`LIVE` 應保留在掃描範圍內，因為靜態照片與 MOV 放在同一個資料夾。

Immich 是否能將特定舊檔辨認成 Live Photo，仍取決於 Immich 版本及檔案內的 Apple 配對資訊。

## 目前限制

- 正式整理大量唯一副本前請先備份；應保持單一服務實例，避免多個容器同時整理同一組資料夾。
- 主要介面針對桌面瀏覽器設計，尚未製作手機版或 PWA。
- 沒有家庭共用圖庫。
- iPhone HEVC MOV 能否直接在網頁播放，取決於瀏覽器與作業系統是否支援該編碼；檔案本身仍會完整保存。
- 大型 Inbox 的第一次掃描需要讀取 EXIF 並計算 SHA-256，NAS CPU 與硬碟會有明顯負載。
- 系統會先用大小及頭尾樣本快速指紋篩選重複候選；最終重複判斷及跨磁碟區驗證仍使用完整 SHA-256。
- 目前不應直接公開到 Internet；若需要遠端存取，請使用 VPN 或妥善設定的 HTTPS 反向代理。

## 支援格式

| 類型 | 副檔名 |
|---|---|
| 一般照片 | JPG、JPEG、PNG、TIFF、TIF、HEIC、HEIF |
| RAW | ARW、RW2、CR2、CR3、NEF、NRW、ORF、RAF、DNG |
| 影片 | MOV、MP4 |
| RAW sidecar | XMP，跟隨同檔名 RAW |

## 技術組成與測試

- Python 3.11 以上及標準函式庫 Web Service
- SQLite 索引
- ExifTool 讀取照片資訊
- Pillow 與 pillow-heif 產生縮圖及預覽
- Docker / Docker Compose 部署
- 自動測試涵蓋帳號隔離、匯入、重複檔、Live Photo、XMP、回收站、索引備份還原、其他檔案隔離、空資料夾清理、HEIC 解碼及服務端 API

詳細 Synology 說明請參閱 [SYNOLOGY.md](SYNOLOGY.md)，版本變更請參閱 [CHANGELOG.md](CHANGELOG.md)。

## 授權

目前公開原始碼，但尚未指定開源授權；本專案暫不宣告為 MIT 或其他開源授權。內附第三方元件保留各自的授權聲明。
