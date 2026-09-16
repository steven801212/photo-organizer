# Synology 安裝與升級

目前版本為 **1.0.1**。已安裝 Beta 版本者請依 [RELEASE-NOTES.md](RELEASE-NOTES.md) 更新；既有 `.env`、照片與索引需要保留。

此版本使用 Container Manager 執行。正式測試前，請先在 NAS 建立：

```text
/volume1/Organized Photo/PhotoInbox
/volume1/Organized Photo/PhotoLibrary
/volume1/Organized Photo/PhotoOrganizer/data
/volume1/Organized Photo/PhotoOrganizer/backups
/volume1/Organized Photo/PhotoOrganizer/rejected
```

把專案放到 NAS 後，將 `.env.example` 複製成 `.env`，設定管理員密碼，再由 Container Manager 建立 `docker-compose.yml` 專案。

若要讓新建檔案使用指定 DSM 帳號權限，可先用 `id <username>` 查詢數字 UID/GID，再於 `.env` 設定 `PUID` 與 `PGID`。請先在 DSM 確認該帳號對 `Organized Photo` 共用資料夾具有讀寫權限。不設定時會保留舊版相容的 root 執行模式。

開啟：

```text
http://NAS-IP:8080
```

首次登入帳號為 `admin`。管理員可在 Dashboard 新增使用者。

每位使用者會自動擁有完全分開的資料：

```text
/volume1/Organized Photo/PhotoInbox/<username>/
/volume1/Organized Photo/PhotoLibrary/<username>/
/volume1/Organized Photo/PhotoOrganizer/data/users/<username>/photo-organizer.db
/volume1/Organized Photo/PhotoOrganizer/backups/<username>/
/volume1/Organized Photo/PhotoOrganizer/rejected/<username>/
```

帳號資料獨立存放於：

```text
/volume1/Organized Photo/PhotoOrganizer/data/system/accounts.db
```

建議使用 Synology 反向代理提供 HTTPS，且不要直接將 8080 連接埠暴露到網際網路。請用 Hyper Backup 備份 `PhotoLibrary` 與整個 `PhotoOrganizer` 資料夾。

## 原有照片

登入自己的帳號後，將舊照片先「複製」到 `/volume1/Organized Photo/PhotoInbox/<username>/OldPhotos/`。先用小批照片掃描預覽，確認後再整批匯入。RAW 與 XMP、JPG/HEIC 與 MOV Live Photo 會配對處理；非媒體檔會保留在個人 `rejected` 隔離區。
