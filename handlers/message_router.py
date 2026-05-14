import logging
from datetime import datetime, timedelta, timezone
from linebot import LineBotApi
from linebot.models import MessageEvent

# 匯入我們剛剛建立好的所有 Service 模組
from config import Config
from services.line_service import LineService
from services.firebase_db import FirebaseDB
from services.sheets_service import SheetsService
from services.dev_mode import is_dev_mode, handle_dev_command, get_role_override

logger = logging.getLogger(__name__)

def handle_text_message(event: MessageEvent, line_bot_api: LineBotApi):
    """
    處理 LINE 文字訊息的進入點。
    負責解析使用者身分，並將請求分流給對應的角色邏輯模組。
    """
    user_id = event.source.user_id
    user_msg = event.message.text.strip()

    # ── 本機開發模式：角色切換指令攔截 ──────────────────────────────
    if is_dev_mode():
        dev_reply = handle_dev_command(user_id, user_msg)
        if dev_reply is not None:
            LineService.reply_text(line_bot_api, event.reply_token, dev_reply)
            return
    # ─────────────────────────────────────────────────────────────────
    
    # 嘗試撈取使用者的 LINE 暱稱，讓 Firebase 和 Google Sheets 裡的資料更具可讀性
    # (注意：若使用者未加 Bot 為好友，可能無法取得 profile)
    display_name = "Unknown"
    try:
        profile = line_bot_api.get_profile(user_id)
        display_name = profile.display_name
    except Exception as e:
        logger.warning(f"Could not get profile for {user_id}: {e}")

    # 1. 查詢或建立使用者，取得角色權限
    #    DEV_MODE 下若有記憶體覆蓋值，優先使用，跳過 Firestore 查詢
    if is_dev_mode() and (override := get_role_override(user_id)):
        role = override
        logger.info(f"[DEV] Using overridden role '{role}' for user {user_id}")
    else:
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
    """老闆端功能"""

    # ── 查詢客戶 <關鍵字> ─────────────────────────────────────────────
    if user_msg.startswith("查詢客戶"):
        keyword = user_msg[4:].strip()
        if not keyword:
            reply_text = "請在「查詢客戶」後面加上搜尋關鍵字，例：\n查詢客戶 王"
        else:
            try:
                customers = FirebaseDB.search_customers_by_name(keyword)
            except Exception as e:
                logger.error(f"Customer search failed: {e}")
                reply_text = "查詢客戶時發生錯誤，請稍後再試。"
            else:
                if not customers:
                    reply_text = f"找不到名稱含有「{keyword}」的客戶。"
                else:
                    lines = [f"🔍 搜尋「{keyword}」，共找到 {len(customers)} 位客戶：\n"]
                    for c in customers:
                        name = c.get('displayName', '未知')
                        lines.append(f"• {name}")
                    lines.append("\n📋 輸入「客戶訂單 姓名」可查看該客戶的歷史訂單")
                    reply_text = "\n".join(lines)

    # ── 客戶訂單 <姓名> ───────────────────────────────────────────────
    elif user_msg.startswith("客戶訂單"):
        customer_name = user_msg[4:].strip()
        if not customer_name:
            reply_text = "請在「客戶訂單」後面加上客戶姓名，例：\n客戶訂單 王小明"
        else:
            try:
                orders = FirebaseDB.get_orders_by_customer_name(customer_name)
            except Exception as e:
                logger.error(f"Failed to fetch orders for customer {customer_name}: {e}")
                reply_text = "查詢訂單時發生錯誤，請稍後再試。"
            else:
                if not orders:
                    reply_text = f"找不到客戶「{customer_name}」的訂單紀錄。"
                else:
                    lines = [f"📋 {customer_name} 的最近 {len(orders)} 筆訂單：\n"]
                    for o in orders:
                        items_list = o.get('items', [])
                        items_str = "、".join([
                            f"{i.get('productName', '')} x{i.get('quantity', 0)}"
                            for i in items_list
                        ])
                        status = "✅已付款" if o.get('paymentStatus') == 'paid' else "❌未付款"
                        total = o.get('totalAmount', 0)
                        order_id = o.get('orderId', 'N/A')
                        lines.append(f"• [{order_id}]\n  {items_str}\n  總額：${total}　{status}")
                    reply_text = "\n".join(lines)

    # ── 未付款清單 <時間範圍> ─────────────────────────────────────────
    elif user_msg.startswith("未付款清單"):
        range_keyword = user_msg[5:].strip()
        now = datetime.now(timezone.utc)

        RANGE_MAP = {
            "本週":   (now - timedelta(weeks=1),  "本週"),
            "本月":   (now - timedelta(days=30),  "本月（近30天）"),
            "近兩個月": (now - timedelta(days=60),  "近兩個月"),
            "近三個月": (now - timedelta(days=90),  "近三個月"),
        }

        if range_keyword == "":
            reply_text = (
                "請選擇時間範圍：\n"
                "• 未付款清單 本週\n"
                "• 未付款清單 本月\n"
                "• 未付款清單 近兩個月\n"
                "• 未付款清單 近三個月"
            )
        elif range_keyword not in RANGE_MAP:
            reply_text = (
                f"不認識的時間範圍「{range_keyword}」，請輸入：\n"
                "本週 / 本月 / 近兩個月 / 近三個月"
            )
        else:
            since, label = RANGE_MAP[range_keyword]
            try:
                orders = FirebaseDB.get_unpaid_orders(since=since)
            except Exception as e:
                logger.error(f"Failed to fetch unpaid orders: {e}")
                reply_text = "查詢未付款清單時發生錯誤，請稍後再試。"
            else:
                if not orders:
                    reply_text = f"老闆好，{label}內沒有任何未付款的訂單！"
                else:
                    total_debt = sum(o.get('totalAmount', 0) for o in orders)
                    lines = [f"💰 {label}未付款訂單，共 {len(orders)} 筆（合計 ${total_debt}）：\n"]
                    for o in orders:
                        customer = o.get('customerName', '未知客戶')
                        items_list = o.get('items', [])
                        items_str = "、".join([
                            f"{i.get('productName', '')} x{i.get('quantity', 0)}"
                            for i in items_list
                        ])
                        total = o.get('totalAmount', 0)
                        order_id = o.get('orderId', 'N/A')
                        lines.append(f"• {customer}｜{items_str}｜${total}\n  訂單編號：{order_id}")
                    reply_text = "\n".join(lines)

    # ── 預設提示 ──────────────────────────────────────────────────────
    else:
        reply_text = (
            "老闆您好！可用指令如下：\n"
            "• 輸入「查詢客戶 關鍵字」模糊搜尋客戶\n"
            "• 輸入「客戶訂單 姓名」查看客戶歷史訂單\n"
            "• 輸入「未付款清單 本週／本月／近兩個月／近三個月」查看未付款訂單"
        )

    LineService.reply_text(line_bot_api, event.reply_token, reply_text)


def _handle_driver_message(event: MessageEvent, line_bot_api: LineBotApi, user_msg: str):
    """送貨司機端功能"""
    # 未來這裡可以攔截特定的關鍵字，或是直接提示司機點擊圖文選單打開 LIFF 表單
    reply_text = f"辛苦了！送貨回報請點擊下方選單...\n您剛才輸入的是：{user_msg}\n(待開發：司機回報 LIFF)"
    LineService.reply_text(line_bot_api, event.reply_token, reply_text)


def _handle_customer_message(event: MessageEvent, line_bot_api: LineBotApi, user_msg: str, user_id: str, display_name: str):
    """客戶端功能"""

    # ── 查看商品 ───────────────────────────────────────────────────────
    if user_msg == "查看商品":
        try:
            products = FirebaseDB.get_products()
        except Exception as e:
            logger.error(f"Failed to fetch products: {e}")
            LineService.reply_text(line_bot_api, event.reply_token, "查詢商品時發生錯誤，請稍後再試。")
            return

        if not products:
            reply_text = "目前暫無上架商品，請稍後再查詢。"
        else:
            lines = ["📦 目前可訂購的商品如下：\n"]
            for p in products:
                name = p.get('productName', '未知商品')
                price = p.get('price', 0)
                spec = p.get('spec', '')
                spec_str = f"（{spec}）" if spec else ""
                lines.append(f"• {name}{spec_str}｜${price}")
            lines.append("\n📝 下單方式：輸入「下單 商品名稱 數量」")
            lines.append("例：下單 電線桿 2")
            reply_text = "\n".join(lines)

    # ── 下單 <商品名稱> <數量> ─────────────────────────────────────────
    elif user_msg.startswith("下單"):
        parts = user_msg.split()
        if len(parts) != 3:
            reply_text = "下單格式不正確，請輸入：\n下單 商品名稱 數量\n例：下單 水蜜桃 2"
        else:
            product_name = parts[1]
            try:
                quantity = int(parts[2])
                if quantity <= 0:
                    raise ValueError
            except ValueError:
                reply_text = "數量請輸入正整數，例：下單 水蜜桃 2"
            else:
                try:
                    products = FirebaseDB.get_products()
                    product = next((p for p in products if p.get('productName') == product_name), None)
                    if not product:
                        reply_text = f"找不到商品「{product_name}」，請先輸入「查看商品」確認可訂購項目。"
                    else:
                        unit_price = product.get('price', 0)
                        spec = product.get('spec', '')
                        spec_str = f"（{spec}）" if spec else ""
                        order_id, sheets_data = FirebaseDB.create_order(
                            user_id, display_name, product_name, quantity, unit_price
                        )
                        SheetsService.append_order(sheets_data)
                        total = unit_price * quantity
                        reply_text = (
                            f"✅ 下單成功！\n"
                            f"訂單編號：{order_id}\n"
                            f"品項：{product_name}{spec_str} x {quantity}\n"
                            f"總金額：${total}\n"
                            f"付款狀態：未付款\n\n"
                            f"如有疑問請輸入「聯絡老闆 您的問題」"
                        )
                except Exception as e:
                    logger.error(f"Order creation failed for user {user_id}: {e}")
                    reply_text = "下單時發生錯誤，請稍後再試或聯絡老闆。"

    # ── 我的未付款 ─────────────────────────────────────────────────────
    elif user_msg == "我的未付款":
        try:
            orders = FirebaseDB.get_customer_unpaid_orders(user_id)
        except Exception as e:
            logger.error(f"Failed to fetch unpaid orders for {user_id}: {e}")
            LineService.reply_text(line_bot_api, event.reply_token, "查詢時發生錯誤，請稍後再試。")
            return

        if not orders:
            reply_text = "您目前沒有任何未付款的訂單！"
        else:
            total_debt = sum(o.get('totalAmount', 0) for o in orders)
            lines = [f"📋 您目前共有 {len(orders)} 筆未付款訂單（合計 ${total_debt}）：\n"]
            for o in orders:
                items_list = o.get('items', [])
                items_str = "、".join([
                    f"{i.get('productName', '')} x{i.get('quantity', 0)}"
                    for i in items_list
                ])
                total = o.get('totalAmount', 0)
                order_id = o.get('orderId', 'N/A')
                status = o.get('paymentStatus', '')
                status_str = "（已回報，待確認）" if status == 'pending_confirmation' else ""
                lines.append(f"• {order_id}\n  {items_str}｜${total}{status_str}")
            lines.append("\n💡 輸入「回報付款 訂單編號」可通知老闆您已付款")
            reply_text = "\n".join(lines)

    # ── 回報付款 <訂單編號> ──────────────────────────────────────────────
    elif user_msg.startswith("回報付款"):
        order_id = user_msg[4:].strip()
        if not order_id:
            reply_text = "請在「回報付款」後面加上訂單編號，例：\n回報付款 ORD-20260510-123456"
        elif not Config.BOSS_LINE_ID:
            logger.error("BOSS_LINE_ID is not configured.")
            reply_text = "目前無法傳送通知，請稍後再試。"
        else:
            try:
                order = FirebaseDB.notify_payment(order_id, user_id)
                items_list = order.get('items', [])
                items_str = "、".join([
                    f"{i.get('productName', '')} x{i.get('quantity', 0)}"
                    for i in items_list
                ])
                total = order.get('totalAmount', 0)
                push_msg = (
                    f"💳 客戶回報付款\n"
                    f"客戶：{display_name}\n"
                    f"訂單編號：{order_id}\n"
                    f"品項：{items_str}\n"
                    f"金額：${total}\n\n"
                    f"請確認收款後更新訂單狀態。"
                )
                LineService.push_text(line_bot_api, Config.BOSS_LINE_ID, push_msg)
                reply_text = (
                    f"✅ 已通知老闆您的付款回報！\n"
                    f"訂單編號：{order_id}\n"
                    f"老闆確認後即會更新狀態。"
                )
            except PermissionError:
                reply_text = f"找不到屬於您的訂單「{order_id}」，請確認編號是否正確。"
            except ValueError as e:
                if str(e) == "already_paid":
                    reply_text = f"訂單「{order_id}」已標註為已付款，無需重複回報。"
                else:
                    reply_text = f"找不到訂單「{order_id}」，請確認編號是否正確。"
            except Exception as e:
                logger.error(f"notify_payment failed for {order_id}: {e}")
                reply_text = "回報付款時發生錯誤，請稍後再試。"

    # ── 聯絡老闆 <訊息內容> ────────────────────────────────────────────
    elif user_msg.startswith("聯絡老闆"):
        message_content = user_msg[4:].strip()
        if not message_content:
            reply_text = "請在「聯絡老闆」後面加上您的訊息，例：\n聯絡老闆 我想詢問水蜜桃的產地資訊"
        elif not Config.BOSS_LINE_ID:
            logger.error("BOSS_LINE_ID is not configured.")
            reply_text = "目前無法轉達訊息，請稍後再試。"
        else:
            try:
                push_msg = f"📩 客戶留言\n來自：{display_name}\n\n{message_content}"
                LineService.push_text(line_bot_api, Config.BOSS_LINE_ID, push_msg)
                reply_text = "✅ 已將您的訊息轉達給老闆，請耐心等候回覆。"
            except Exception as e:
                logger.error(f"Failed to push message to boss: {e}")
                reply_text = "訊息傳送失敗，請稍後再試。"

    # ── 預設提示 ──────────────────────────────────────────────────────
    else:
        reply_text = (
            "歡迎光臨！您可以：\n"
            "• 輸入「查看商品」瀏覽目前商品\n"
            "• 輸入「下單 商品名稱 數量」直接下單\n"
            "• 輸入「我的未付款」查看您尚未付款的訂單\n"
            "• 輸入「回報付款 訂單編號」通知老闆您已付款\n"
            "• 輸入「聯絡老闆 訊息內容」與老闆溝通"
        )

    LineService.reply_text(line_bot_api, event.reply_token, reply_text)
