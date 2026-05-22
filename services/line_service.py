import logging
from linebot import LineBotApi
from linebot.exceptions import LineBotApiError
from linebot.models import FlexSendMessage, TextSendMessage

from config import Config

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
    def reply_flex(line_bot_api: LineBotApi, reply_token: str, alt_text: str, contents):
        """回覆 Flex Message（互動式卡片）給使用者。"""
        try:
            message = FlexSendMessage(alt_text=alt_text, contents=contents)
            line_bot_api.reply_message(reply_token, message)
            logger.info("Successfully replied flex message.")
        except LineBotApiError as e:
            logger.error(f"LINE API Error (reply flex): {e.status_code} {e.error.message}")
        except Exception as e:
            logger.error(f"Unexpected error in reply_flex: {e}", exc_info=True)

    @staticmethod
    def bind_rich_menu_to_user(line_bot_api: LineBotApi, user_id: str, rich_menu_id: str):
        """綁定指定 rich menu 到單一使用者。"""
        if not rich_menu_id:
            logger.warning(f"No rich menu ID configured for binding user {user_id}.")
            return

        try:
            line_bot_api.link_rich_menu_to_user(user_id, rich_menu_id)
            logger.info(f"Bound rich menu {rich_menu_id} to user {user_id}.")
        except LineBotApiError as e:
            logger.error(f"LINE API Error (bind rich menu): {e.status_code} {e.error.message}")
        except Exception as e:
            logger.error(f"Unexpected error in bind_rich_menu_to_user: {e}", exc_info=True)

    @staticmethod
    def ensure_user_rich_menu(line_bot_api: LineBotApi, user_id: str, role: str):
        """確認使用者是否已綁定正確角色的 rich menu，若沒有則重新綁定。"""
        expected_rich_menu_id = Config.BOSS_RICH_MENU_ID if role == 'boss' else Config.CUSTOMER_RICH_MENU_ID
        if not expected_rich_menu_id:
            logger.warning(f"Expected rich menu ID for role '{role}' is not configured.")
            return

        try:
            current_rich_menu_id = line_bot_api.get_rich_menu_id_of_user(user_id)
        except LineBotApiError as e:
            if e.status_code == 404:
                current_rich_menu_id = None
            else:
                logger.error(f"LINE API Error (get rich menu id): {e.status_code} {e.error.message}")
                return
        except Exception as e:
            logger.error(f"Unexpected error in ensure_user_rich_menu: {e}", exc_info=True)
            return

        if current_rich_menu_id == expected_rich_menu_id:
            logger.debug(f"User {user_id} already has the correct rich menu bound.")
            return

        LineService.bind_rich_menu_to_user(line_bot_api, user_id, expected_rich_menu_id)

    @staticmethod
    def reply_liff_menu(line_bot_api: LineBotApi, reply_token: str, role: str):
        """
        [待實作] 根據不同角色 (老闆、司機、客戶) 
        回覆對應的圖文選單 (Flex Message) 或是帶有 LIFF URL 的按鈕。
        """
        # TODO: 實作 Flex Message 的 JSON 組裝邏輯
        pass
