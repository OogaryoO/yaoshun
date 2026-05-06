import logging
from linebot import LineBotApi
from linebot.models import TextSendMessage
from linebot.exceptions import LineBotApiError

logger = logging.getLogger(__name__)

class LineService:
    """
    封裝與 LINE Messaging API 互動的所有邏輯。
    所有的回覆 (reply) 與推播 (push) 行為都應透過此類別統一處理。
    """

    @staticmethod
    def reply_text(line_bot_api: LineBotApi, reply_token: str, text: str):
        """
        基礎回覆功能：回覆純文字訊息給使用者。
        (LINE 限制：reply_token 只能使用一次，且需在收到訊息後的一段時間內回覆)
        """
        try:
            message = TextSendMessage(text=text)
            line_bot_api.reply_message(reply_token, message)
            logger.info("Successfully replied text message.")
        except LineBotApiError as e:
            logger.error(f"LINE API Error (reply): {e.status_code} {e.error.message}")
        except Exception as e:
            logger.error(f"Unexpected error in reply_text: {e}", exc_info=True)

    @staticmethod
    def push_text(line_bot_api: LineBotApi, user_id: str, text: str):
        """
        基礎推播功能：主動發送文字訊息給特定使用者。
        (適用場景：每日定時傳送未付款清單給老闆、提醒司機回報)
        """
        try:
            message = TextSendMessage(text=text)
            line_bot_api.push_message(user_id, message)
            logger.info(f"Successfully pushed text message to {user_id}.")
        except LineBotApiError as e:
            logger.error(f"LINE API Error (push): {e.status_code} {e.error.message}")
        except Exception as e:
            logger.error(f"Unexpected error in push_text: {e}", exc_info=True)

    # ==========================================
    # 預留區域：未來開發 LIFF 與 Flex Message 的擴充點
    # ==========================================
    
    @staticmethod
    def reply_liff_menu(line_bot_api: LineBotApi, reply_token: str, role: str):
        """
        [待實作] 根據不同角色 (老闆、司機、客戶) 
        回覆對應的圖文選單 (Flex Message) 或是帶有 LIFF URL 的按鈕。
        """
        # TODO: 實作 Flex Message 的 JSON 組裝邏輯
        pass
