# yaoshun

```text
uncle-line-bot/
├── app.py                 # 程式進入點：註冊 Flask 路由與 LINE Webhook 接收
├── requirements.txt       # 依賴套件清單 (Flask, line-bot-sdk, firebase-admin 等)
├── config.py              # 環境變數與設定檔管理
├── firestore.indexes.json # Firestore 複合索引宣告 (供 firebase deploy 使用)
├── services/              # 核心業務邏輯層
│   ├── line_service.py    # 處理 LINE 訊息發送、圖文選單、推播等邏輯
│   ├── firebase_db.py     # 封裝 Firestore 的讀寫與查詢邏輯
│   ├── schemas.py         # OrderDoc / OrderItem / UserDoc 寫入驗證 (對齊 dashboard 契約)
│   └── dev_mode.py        # 本機開發模式：單帳號多角色模擬
└── handlers/              # 訊息處理分流層
    └── message_router.py  # 根據使用者身分 (老闆/司機/客戶) 將訊息導向對應模組
```

## 環境變數 (.env)

本專案使用 `python-dotenv` 讀取本機 `.env` 檔案，`config.py` 會根據下列變數初始化系統設定。

建議 `.env` 至少設定前 3 項；若要啟用 Google 試算表、老闆通知、Rich Menu 綁定，請補上第 4~7 項。

```env
# 1. LINE Messaging API 的 Channel Access Token
LINE_CHANNEL_ACCESS_TOKEN=你的 channel access token

# 2. LINE Messaging API 的 Channel Secret
LINE_CHANNEL_SECRET=你的 channel secret

# 3. Google Firebase 憑證 JSON（整段 JSON 字串）
GOOGLE_CREDENTIALS_JSON={"type": "service_account", ...}

# 4. Google Sheet ID（如果你使用 sheets_service.py 或 Google 試算表功能）
GOOGLE_SHEET_ID=你的 Google Sheet ID

# 5. 老闆的 LINE User ID（負責接收客戶付款回報通知）
BOSS_LINE_ID=Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 6. 客戶用 Rich Menu ID（綁定客戶角色專屬功能選單）
CUSTOMER_RICH_MENU_ID=你的 customer rich menu id

# 7. 老闆用 Rich Menu ID（綁定老闆角色專屬功能選單）
BOSS_RICH_MENU_ID=你的 boss rich menu id

# 8. 本機開發模式：true 表示啟用 DEV_MODE，不建議正式環境開啟
DEV_MODE=false
```

### 參考註記
- `GOOGLE_SHEET_ID`：只要你不使用 Google 試算表功能，可先留空。
- `BOSS_LINE_ID`：若未設定，客戶「回報付款」功能仍可執行，但無法推播通知給老闆。
- `CUSTOMER_RICH_MENU_ID` / `BOSS_RICH_MENU_ID`：若未設定，系統會跳過 Rich Menu 綁定，LINE 使用者仍可透過文字指令互動。
- `DEV_MODE`：僅供本機開發測試使用，正式上線請保持 `false` 或移除。

## 總覽 (Collections Overview)

系統由三個核心的 Collection (集合) 組成：
1. **`Users`**：使用者身分與權限管理。
2. **`Products`**：產品型錄與定價基準。
3. **`Orders`**：系統核心，記錄所有交易明細與收款狀態。

> **資料契約來源 (Authoritative schema):**
> 本 bot 寫入 Firestore 的所有欄位以 dashboard repo 為準：
> [`cpcap1214/IM2008/src/lib/firestore.ts`](https://github.com/cpcap1214/IM2008/blob/main/src/lib/firestore.ts)。
> bot 端在 `services/schemas.py` 重現同樣的 shape，所有 `set()` / `update()` 之前都會驗證，違反契約會拋出 `ValueError`。

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
```json
{
  "role": "customer",
  "displayName": "王大明",
  "phone": "0912345678",
  "createdAt": "2026-05-06T10:00:00Z",
  "notes": "每週三固定公休不送貨"
}
```

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
```json
{
  "productName": "富士蘋果",
  "spec": "特級 10kg 箱裝",
  "price": 1500,
  "isActive": true
}
```

---

## 3. 集合：`Orders` (核心訂單總表)

系統的心臟。記錄所有的訂單明細、出貨司機與收款狀態。所有的對帳、報表匯出與未付款推播提醒皆直接依賴此集合。

* **文件 ID (Document ID):** 由 bot 產生，格式為 `ORD-YYYYMMDD-HHMMSS-XXXX`（最後 4 碼為隨機 hex，避免同秒撞單）。

###  欄位定義 (Fields)
| 欄位名稱 | 資料型態 | 必填 | 說明 |
| :--- | :--- | :---: | :--- |
| `customerId` | `String` | 是 | 下單客戶的 LINE User ID |
| `customerName` | `String` | 是 | 客戶名稱（反正規化欄位） |
| `driverId` | `String \| null` | 是 | 負責送貨的司機 LINE User ID。**未指派時固定為 `null`，絕不可寫入字串 `"尚未指派"`** |
| `items` | `Array<OrderItem>` | 是 | 訂單品項清單，每個品項含五個欄位（見下方） |
| `totalAmount` | `Number` | 是 | 訂單總金額，**必須等於 `sum(items[].subtotal)`** |
| `paymentStatus` | `String` | 是 | `unpaid` / `paid` / `pending_confirmation` |
| `paymentMethod` | `String \| null` | 是 | `cash` / `transfer` / `check`。**當 `paymentStatus != 'paid'` 時必為 `null`** |
| `orderDate` | `Timestamp` | 是 | 下單時間 |
| `deliveryDate` | `Timestamp \| null` | 是 | 司機實際送達時間，**僅由 `mark_delivered` 寫入**，其餘流程一律不可變更 |
| `paidAt` | `Timestamp \| null` | 否 | 入帳時間，**僅在轉為 `paid` 時寫入** |
| `createdAt` | `Timestamp` | 否 | 系統建立時間 |

`OrderItem` 內每筆品項固定為五個欄位：`productName`、`spec`、`quantity`、`unitPrice`、`subtotal`，且 `subtotal === unitPrice * quantity`。

### 📄 文件結構範例 (JSON Document)
```json
{
  "customerId": "U1234567890abcdef1234567890",
  "customerName": "王大明",
  "driverId": null,
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
  "deliveryDate": null,
  "paidAt": null
}
```

### 不變條件 (Invariants)

- `driverId` 是 `null` 表示尚未指派——**永遠不要**寫入 `"尚未指派"` 等字串到 Firestore。LINE 回覆文字仍可顯示「尚未指派」。
- `paymentMethod` 在 `paymentStatus !== 'paid'` 時必為 `null`。
- `paymentStatus` 多了一個 `pending_confirmation`：代表客戶已透過 bot 回報付款、老闆尚未確認入帳。
- `deliveryDate` **只能**由 `FirebaseDB.mark_delivered` 寫入；`update_order_payment` / `confirm_payment` 一律不會碰它。
- `paidAt` **只能**在轉為 `paid` 時被寫入（由 `update_order_payment(status='paid')` 或 `confirm_payment` 寫入）。

### 跨 repo 待辦 (Cross-repo coordination)

Dashboard ([`cpcap1214/IM2008`](https://github.com/cpcap1214/IM2008)) 目前只宣告 `OrderPaymentStatus = "unpaid" | "paid"`。
本 bot 正式擴展此契約以加入 `"pending_confirmation"`，dashboard 端需要：

1. 在 `src/lib/firestore.ts` 將 `OrderPaymentStatus` 改為 `"unpaid" | "paid" | "pending_confirmation"`。
2. 在訂單列表 UI 上顯示「待確認」狀態（建議和「未付款」分群）。
3. 在訂單詳情頁加上「確認入帳」按鈕，呼叫對應的 update API（mirror 本 repo `FirebaseDB.confirm_payment`）。

---

## 老闆 / 司機 / 客戶 指令一覽

### 老闆
| 指令 | 說明 |
| :--- | :--- |
| `查詢客戶 <關鍵字>` | 模糊搜尋客戶 |
| `客戶訂單 <姓名>` | 查看該客戶最近的訂單 |
| `未付款清單 本週 / 本月 / 近兩個月 / 近三個月` | 撈未付款 + 待確認 (`pending_confirmation`) 訂單；待確認以 🕓 標示 |
| `確認付款 <order_id> <method>` | 將 `pending_confirmation` / `unpaid` 轉為 `paid`，`method` ∈ {`cash`, `transfer`, `check`} |

### 司機
| 指令 | 說明 |
| :--- | :--- |
| `送達 <order_id>` | 標記訂單為已送達（**唯一**會寫 `deliveryDate` 的入口） |

### 客戶
| 指令 | 說明 |
| :--- | :--- |
| `查看商品` | 列出 `isActive=true` 的商品 |
| `下單 <商品名稱> <數量>` | 建立訂單；訂單編號帶 4 碼隨機 hex 尾碼避免撞單 |
| `我的未付款` | 列出自己未付款 / 待確認的訂單 |
| `回報付款 <order_id>` | 通知老闆已付款 → 訂單轉為 `pending_confirmation` |
| `聯絡老闆 <訊息>` | 將留言推播給老闆 |

---

## Firestore indexes

部署複合索引：

```bash
firebase deploy --only firestore:indexes
```

`firestore.indexes.json` 已宣告以下索引（搭配 `get_customer_orders`、`get_orders_by_customer_name`、`get_unpaid_orders(since=...)` 使用）：

| Collection | Fields |
| :--- | :--- |
| `Orders` | `customerId ASC`, `orderDate DESC` |
| `Orders` | `customerName ASC`, `orderDate DESC` |
| `Orders` | `paymentStatus ASC`, `orderDate ASC` |

---

## 本機開發：單帳號多角色模擬

詳見 [`services/dev_mode.py`](services/dev_mode.py)。由於 LINE Bot 的角色是透過 LINE UID 對應 Firestore 決定的，本地測試時若只有一個 LINE 帳號，可以開啟 **DEV_MODE** 在記憶體中臨時覆蓋角色，無需切換帳號。

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

- 角色覆蓋值存在 Server 記憶體（`dict`），**重啟後歸零**。
- `DEV_MODE=false`（預設）時，所有 `/as`、`/whoami` 指令完全無效，走正常 Firestore 查詢路徑。
- 切換角色後，後續所有訊息都以該角色路由，直到再次切換或重啟。
