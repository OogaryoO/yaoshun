"""
dev_mode.py — 本機開發專用：單一 LINE 帳號多角色模擬器

只在 DEV_MODE=true 時生效。
使用者可透過特殊指令在 customer / boss 之間切換，
切換狀態存在記憶體中，重啟伺服器後會重置。

支援指令:
  /as customer  → 切換為客戶模式
  /as boss      → 切換為老闆模式
  /whoami       → 查詢目前模擬的角色
"""

import logging
from config import Config

logger = logging.getLogger(__name__)

# 系統目前僅支援兩個角色：customer / boss（driver 已下線，由老闆兼任）
VALID_ROLES = {'customer', 'boss'}

# { user_id: role }  —— 只在記憶體中，重啟後歸零
_role_overrides: dict[str, str] = {}


def is_dev_mode() -> bool:
    return Config.DEV_MODE


def get_role_override(user_id: str) -> str | None:
    """回傳該使用者目前覆蓋的角色，若無則回傳 None。"""
    return _role_overrides.get(user_id)


def handle_dev_command(user_id: str, user_msg: str) -> str | None:
    """
    嘗試解析並執行開發者指令。
    若訊息符合開發指令格式，回傳要回覆給使用者的字串。
    若不是開發者指令，回傳 None（讓正常流程繼續）。
    """
    msg = user_msg.strip().lower()

    # /whoami
    if msg == '/whoami':
        current = _role_overrides.get(user_id, '(尚未覆蓋，使用 Firestore 真實角色)')
        return f"[DEV] 目前模擬角色：{current}"

    # /as <role>
    if msg.startswith('/as '):
        parts = msg.split()
        if len(parts) == 2 and parts[1] in VALID_ROLES:
            role = parts[1]
            _role_overrides[user_id] = role
            logger.info(f"[DEV] User switched to role: {role}")
            return (
                f"[DEV] 已切換為「{role}」模式。\n"
                f"現在傳送任何訊息都會以 {role} 身份處理。\n"
                f"輸入 /whoami 確認，輸入 /as customer|boss 切換角色。"
            )
        else:
            return "[DEV] 用法：/as customer | /as boss"

    return None  # 不是開發指令，交還給正常路由
