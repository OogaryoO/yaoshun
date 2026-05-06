import json
import logging
from datetime import datetime
import gspread
from config import Config

logger = logging.getLogger(__name__)

# 全域變數儲存 Google Sheets 連線狀態與工作表物件
gc = None
worksheet = None

def init_sheets():
    """
    初始化 Google Sheets API 連線。
    建議與 Firebase 相同，在 app.py 啟動時呼叫此函式。
    """
    global gc, worksheet
    try:
        cert = json.loads(Config.GOOGLE_CREDENTIALS_JSON)
        # 使用服務帳號憑證登入
        gc = gspread.service_account_from_dict(cert)
        # 透過試算表 ID 開啟檔案
        sh = gc.open_by_key(Config.GOOGLE_SHEET_ID)
        # 預設抓取第一個工作表，若有特定名稱也可改為 sh.worksheet('訂單總表')
        worksheet = sh.sheet1 
        logger.info("Google Sheets API initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize Google Sheets: {e}")

class SheetsService:
    """
    封裝寫入 Google Sheets 的邏輯，負責維護無須開發前端的「作帳儀表板」。
    """

    @staticmethod
    def append_order(order_data: dict):
        """
        當有新訂單產生，或是司機回報更新時，將該筆訂單的核心資訊新增至試算表最下方。
        """
        if not worksheet:
            logger.error("Google Sheets is not initialized. Cannot append order.")
            return

        try:
            # 將訂單內的 items 陣列轉為易讀的字串 (例如: "富士蘋果x2, 梨子x1")
            items_list = order_data.get('items', [])
            items_str = ", ".join([f"{item.get('productName', '')}x{item.get('quantity', 0)}" for item in items_list])
            
            # 處理時間格式，將 Firebase 的 datetime 物件轉為字串
            order_time = order_data.get('orderDate', '')
            if isinstance(order_time, datetime):
                order_time = order_time.strftime("%Y-%m-%d %H:%M")

            # 整理要寫入試算表的欄位順序。
            # 假設試算表的欄位 (A~G) 依序為：
            # [訂單編號, 下單時間, 客戶名稱, 訂購品項與數量, 總金額, 收款狀態, 負責司機]
            row_values = [
                order_data.get('orderId', 'N/A'),
                order_time,
                order_data.get('customerName', '未知客戶'),
                items_str,
                order_data.get('totalAmount', 0),
                "未付款" if order_data.get('paymentStatus') == 'unpaid' else "已付款",
                order_data.get('driverId', '尚未指派')
            ]

            # 呼叫 gspread 將陣列作為新的一列插入至試算表
            worksheet.append_row(row_values)
            logger.info(f"Successfully appended order {order_data.get('orderId')} to Google Sheets.")

        except Exception as e:
            logger.error(f"Failed to append row to Google Sheets: {e}", exc_info=True)
