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

## 總覽 (Collections Overview)

系統由三個核心的 Collection (集合) 組成：
1. **`Users`**：使用者身分與權限管理。
2. **`Products`**：產品型錄與定價基準。
3. **`Orders`**：系統核心，記錄所有交易明細與收款狀態。

---

## 1. 集合：`Users` (使用者管理)

用於儲存所有曾與 LINE Bot 互動或加入系統的使用者資料。系統將依賴此集合的 `role` 欄位來決定使用者可看見的介面與功能權限。

* **文件 ID (Document ID):** 使用使用者的 `LINE User ID` (例如：`U1234567890abcdef...`)

###  欄位定義 (Fields)
| 欄位名稱 | 資料型態 | 必填 | 說明 / 範例 |
| :--- | :--- | :---: | :--- |
| `role` | `String` | 是 | 系統權限。允許值：`boss` (老闆), `driver` (司機), `customer` (客戶) |
| `displayName` | `String` | 是 | 使用者的 LINE 暱稱，或後續於系統中填寫的真實姓名 |
| `phone` | `String` | 否 | 聯絡電話 |
| `createdAt` | `Timestamp`| 是 | 初次加入系統 (建立檔案) 的時間 |
| `notes` | `String` | 否 | 管理員對此客戶的備註 (例如：固定每週二叫貨) |

### 📄 文件結構範例 (JSON Document)
{
  "role": "customer",
  "displayName": "王大明",
  "phone": "0912345678",
  "createdAt": "2026-05-06T10:00:00Z",
  "notes": "每週三固定公休不送貨"
}

---

## 2. 集合：`Products` (產品目錄)

提供給前端 (如 LIFF 表單) 動態產生「下單選單」的資料來源。管理員可直接異動此集合來更新菜單、規格與價格。

* **文件 ID (Document ID):** Firestore Auto-generated ID (自動生成)

###  欄位定義 (Fields)
| 欄位名稱 | 資料型態 | 必填 | 說明 / 範例 |
| :--- | :--- | :---: | :--- |
| `productName` | `String` | 是 | 產品主名稱 (例：富士蘋果) |
| `spec` | `String` | 是 | 產品規格說明 (例：特級 10kg 箱裝) |
| `price` | `Number` | 是 | 產品單價 (例：1500) |
| `isActive` | `Boolean` | 是 | 上下架狀態。`true` (顯示於菜單) / `false` (隱藏並停售) |

###  文件結構範例 (JSON Document)
{
  "productName": "富士蘋果",
  "spec": "特級 10kg 箱裝",
  "price": 1500,
  "isActive": true
}

---

## 3. 集合：`Orders` (核心訂單總表) 

系統的心臟。記錄所有的訂單明細、出貨司機與收款狀態。所有的對帳、報表匯出與未付款推播提醒皆直接依賴此集合。

* **設計備註 (反正規化)：** 為了避免在產出報表時需要對 `Users` 集合進行二次查詢 (Join)，我們將 `customerName` 直接冗餘寫入訂單文件中，以空間換取時間與查詢效能。
* **文件 ID (Document ID):** Firestore Auto-generated ID (自動生成)

###  欄位定義 (Fields)
| 欄位名稱 | 資料型態 | 必填 | 說明 / 範例 |
| :--- | :--- | :---: | :--- |
| `customerId` | `String` | 是 | 下單客戶的 LINE User ID，用於查詢特定客戶的歷史訂單 |
| `customerName` | `String` | 是 | 客戶名稱 (反正規化欄位，加速前端與報表渲染) |
| `driverId` | `String` | 否 | 負責送貨與收款回報的司機 LINE User ID |
| `items` | `Array` | 是 | 訂單內容物清單。內部包含 Object (詳見下方範例) |
| `totalAmount` | `Number` | 是 | 該筆訂單的總結算金額 |
| `paymentStatus` | `String` | 是 | 收款狀態。允許值：`unpaid` (未付款), `paid` (已結清) |
| `paymentMethod` | `String` | 否 | 收款方式。允許值：`cash` (現金), `transfer` (匯款), `check` (支票)。若未付款則為 `null` |
| `orderDate` | `Timestamp`| 是 | 客戶送出訂單的時間 |
| `deliveryDate` | `Timestamp`| 否 | 司機實際出貨或變更為已收款的時間。若未出貨則為 `null` |

### 📄 文件結構範例 (JSON Document)
{
  "customerId": "U1234567890abcdef1234567890", 
  "customerName": "王大明",              
  "driverId": "U0987654321fedcba0987654321",   
  "items": [                            
    {
      "productName": "富士蘋果",
      "spec": "特級 10kg 箱裝",
      "quantity": 2,
      "unitPrice": 1500,
      "subtotal": 3000
    },
    {
      "productName": "玉荷包荔枝",
      "spec": "5斤 禮盒裝",
      "quantity": 1,
      "unitPrice": 800,
      "subtotal": 800
    }
  ],
  "totalAmount": 3800,                  
  "paymentStatus": "unpaid",            
  "paymentMethod": null,                
  "orderDate": "2026-05-06T10:30:00Z",  
  "deliveryDate": null
}

---

## 本機開發：單帳號多角色模擬

由於 LINE Bot 的角色是透過 LINE UID 對應 Firestore 決定的，本地測試時若只有一個 LINE 帳號，可以開啟 **DEV_MODE** 在記憶體中臨時覆蓋角色，無需切換帳號。

### 啟用方式

在 `.env` 加入（**正式環境絕對不能設為 true**）：

```
DEV_MODE=true
```

### 支援指令

直接在 LINE Bot 對話框輸入：

| 指令 | 說明 |
| :--- | :--- |
| `/as customer` | 以「客戶」身份發送下一則訊息 |
| `/as driver` | 以「司機」身份發送下一則訊息 |
| `/as boss` | 以「老闆」身份發送下一則訊息 |
| `/whoami` | 查詢目前正在模擬的角色 |

### 運作原理

- 角色覆蓋值存在 Server 記憶體（`dict`），**重啟後歸零**
- `DEV_MODE=false`（預設）時，所有 `/as`、`/whoami` 指令完全無效，走正常 Firestore 查詢路徑
- 切換角色後，後續所有訊息都以該角色路由，直到再次切換或重啟

### 快速測試流程

```
/as boss        → 老闆模式
催款清單         → 測試老闆功能

/as driver      → 切換司機模式
（測試司機功能）

/as customer    → 切換回客戶模式
測試下單         → 測試客戶下單功能
```

