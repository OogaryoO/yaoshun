import os
import logging
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage

# 匯入環境變數設定與稍後會實作的模組
from config import Config
from services.firebase_db import init_firebase
from handlers.message_router import handle_text_message

# 設定基本的日誌紀錄，方便在 Render 上 debug
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 1. 載入 LINE Bot 相關金鑰
line_bot_api = LineBotApi(Config.LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(Config.LINE_CHANNEL_SECRET)

# 2. 啟動伺服器前，初始化 Firebase 連線以及sheets
init_firebase()
init_sheets()

# 3. LINE Webhook 接收端點
@app.route("/callback", methods=['POST'])
def callback():
    # 取得 LINE 官方發送的 X-Line-Signature header
    signature = request.headers['X-Line-Signature']
    
    # 取得 request body 作為純文字
    body = request.get_data(as_text=True)
    logger.debug(f"Received webhook body: {body}")
    
    # 驗證 Webhook 的簽章並交由 handler 處理
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("Invalid signature. Check your LINE_CHANNEL_ACCESS_TOKEN and LINE_CHANNEL_SECRET.")
        abort(400)
        
    return 'OK'

# 4. 訊息事件綁定
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """
    捕捉到文字訊息後，不在此處撰寫商業邏輯，
    而是直接委派給 message_router 進行身分驗證與路由分流。
    """
    try:
        # 將 event 與 line_bot_api 傳遞進 router，由 router 決定回覆內容
        handle_text_message(event, line_bot_api)
    except Exception as e:
        logger.error(f"Error handling message: {e}", exc_info=True)

if __name__ == "__main__":
    # 在 Render 環境中，系統會自動分配 PORT
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
