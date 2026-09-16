# Photo Organizer Service 架構

## 單一寫入者

Synology 上只執行一個 Organizer Service。每位使用者擁有完全獨立的 Inbox、正式圖庫、SQLite 索引與備份；只有 Service 可以寫入這些區域。

## 多使用者

登入帳號儲存在獨立的系統帳號資料庫，不與任何使用者的照片索引混合。每位使用者登入後只會連到自己的：

`/inbox/<username>`、`/library/<username>`、`/data/users/<username>`、`/backup/<username>`。

未來管理權限分為：

- `admin`：帳號、路徑、備份、索引、還原與所有匯入操作。
- `operator`：掃描、預覽及開始匯入；不可修改系統設定或還原。
- `viewer`：只能查看 Dashboard、匯入結果及錯誤。

每項會改變資料的操作必須寫入 `audit_events`，內容包括使用者、來源 IP、時間、操作類型與結果。密碼不得明文保存；正式版本使用強密碼雜湊、安全 Cookie、登入嘗試限制及 CSRF 防護。

## 儲存區

- `/inbox/<computer>`：各 PC 與舊照片的暫存入口。
- `/library`：正式 RAW/JPG 圖庫。
- `/data`：作用中的 SQLite、Manifest 與服務設定，只由 NAS 上的 Service 開啟。
- `/backup`：一致性快照，供 Hyper Backup 再備份到其他裝置。

## 安全順序

掃描觀察來源狀態 → EXIF 與候選 Hash → 預覽 → 驗證來源未變更及完整 SHA-256 → 同磁碟区以硬連結落檔，或複製至暫存檔並 fsync、SHA-256 驗證 → 不可覆寫地發布目的檔 → SQLite transaction → 移除 Inbox 來源 → SQLite 安全備份。主檔與 XMP 共同處理名稱衝突。

同圖庫的 Organizer 維護操作使用同一把互斥鎖。資料庫連線持有共用租約，還原前取得獨占租約並等待其他連線關閉。此協調適用於單一服務進程；每組圖庫只能由一個服務實例整理。

## 待完成的正式版功能

- 登入頁、Session、角色授權與帳號管理。
- 操作稽核頁與不可變更的稽核備份。
- XMP、影片、Live Photo 與 RAW/JPG 配對。
- 背景佇列、暫停、續傳、取消與服務重啟恢復。
- Synology Container Manager 安裝精靈與 HTTPS 反向代理說明。
