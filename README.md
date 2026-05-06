# yaoshun

```text
uncle-line-bot/
├── app.py                 # 程式進入點：註冊 Flask 路由與 LINE Webhook 接收
├── requirements.txt       # 依賴套件清單 (Flask, line-bot-sdk, firebase-admin 等)
├── config.py              # 環境變數與設定檔管理
├── services/              # 核心業務邏輯層
│   ├── line_service.py    # 處理 LINE 訊息發送、圖文選單、推播等邏輯
│   ├── firebase_db.py     # 封裝 Firestore 的讀寫與查詢邏輯
│   └── sheets_service.py  # 封裝寫入 Google Sheets 的自動化邏輯
└── handlers/              # 訊息處理分流層
    └── message_router.py  # 根據使用者身分 (老闆/司機/客戶) 將訊息導向對應模組
```
