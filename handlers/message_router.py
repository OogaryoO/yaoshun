import logging
from linebot import LineBotApi
from linebot.models import MessageEvent

# 匯入我們剛剛建立好的所有 Service 模組
from services.line_service import LineService
from services.firebase_db import FirebaseDB
from services.sheets_service import SheetsService

logger = logging.getLogger(__name__)

def handle_text_message(event: MessageEvent, line_bot_api: LineBotApi):
    """
    處理 LINE 文字訊息的進入點。
    負責解析使用者身分，並將請求分流給對應的角色邏輯模組。
    """
    user_id = event.source.user_id
    user_msg = event.message.text.strip()
    
    # 嘗試撈取使用者的 LINE 暱稱，讓 Firebase 和 Google Sheets 裡的資料更具可讀性
    # (注意：若使用者未加 Bot 為好友，可能無法取得 profile)
    display_name = "Unknown"
    try:
        profile = line_bot_api.get_profile(user_id)
        display_name = profile.display_name
    except Exception as e:
        logger.warning(f"Could not get profile for {user_id}: {e}")

    # 1. 查詢或建立使用者，取得角色權限
    try:
        role = FirebaseDB.get_or_create_user(user_id, display_name)
    except Exception as e:
        logger.error(f"Database error when getting user role: {e}")
        LineService.reply_text(line_bot_api, event.reply_token, "系統連線異常，請稍後再試。")
        return

    # 2. 根據角色進行主路由分流
    if role == 'boss':
        _handle_boss_message(event, line_bot_api, user_msg)
    elif role == 'driver':
        _handle_driver_message(event, line_bot_api, user_msg)
    else:
        _handle_customer_message(event, line_bot_api, user_msg, user_id, display_name)


# ==========================================
# 角色專屬邏輯區塊 (Private Functions)
# ==========================================

def _handle_boss_message(event: MessageEvent, line_bot_api: LineBotApi, user_msg: str):
    """舅舅專用的老闆端功能"""
    if user_msg == "催款清單":
        # 示範串接 Firebase 撈取未付款訂單
        unpaid_orders = FirebaseDB.get_unpaid_orders()
        if not unpaid_orders:
            reply_text = "老闆好，目前沒有任何未付款的訂單喔！"
        else:
            reply_text = f"老闆好，目前共有 {len(unpaid_orders)} 筆未付款訂單。\n(待開發：詳細推播列表與格式化)"
    else:
        reply_text = f"老闆您好！您剛輸入了：{user_msg}\n(待開發：管理員圖文選單)"
        
    LineService.reply_text(line_bot_api, event.reply_token, reply_text)


def _handle_driver_message(event: MessageEvent, line_bot_api: LineBotApi, user_msg: str):
    """送貨司機端功能"""
    # 未來這裡可以攔截特定的關鍵字，或是直接提示司機點擊圖文選單打開 LIFF 表單
    reply_text = f"辛苦了！送貨回報請點擊下方選單...\n您剛才輸入的是：{user_msg}\n(待開發：司機回報 LIFF)"
    LineService.reply_text(line_bot_api, event.reply_token, reply_text)


def _handle_customer_message(event: MessageEvent, line_bot_api: LineBotApi, user_msg: str, user_id: str, display_name: str):
    """預設客戶端功能"""
    if user_msg == "測試下單":
        # 示範串接 SheetsService 寫入測試訂單至 Google 試算表
        import datetime
        test_order = {
            "orderId": f"TEST-{datetime.datetime.now().strftime('%H%M%S')}",
            "orderDate": datetime.datetime.now(),
            "customerName": display_name,
            "items": [{"productName": "測試水蜜桃", "quantity": 2}],
            "totalAmount": 1200,
            "paymentStatus": "unpaid",
            "driverId": "尚未指派"
        }
        SheetsService.append_order(test_order)
        reply_text = "✅ 已為您建立測試訂單，並自動同步至老闆的作帳儀表板！"
    else:
        reply_text = f"歡迎光臨！請點擊下方按鈕開始下單：\n(待開發：客戶下單 LIFF 表單)\n\n您輸入了：{user_msg}"
        
    LineService.reply_text(line_bot_api, event.reply_token, reply_text)
