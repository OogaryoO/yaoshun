import logging
from datetime import datetime, timedelta, timezone
from linebot import LineBotApi
from linebot.models import (
    MessageEvent,
    BubbleContainer,
    CarouselContainer,
    BoxComponent,
    TextComponent,
    ButtonComponent,
    SeparatorComponent,
    FillerComponent,
    MessageAction,
    URIAction,
)

# 匯入我們剛剛建立好的所有 Service 模組
from config import Config
from services.line_service import LineService
from services.firebase_db import FirebaseDB
from services.dev_mode import is_dev_mode, handle_dev_command, get_role_override

logger = logging.getLogger(__name__)

_pending_orders: dict[str, dict] = {}
_boss_pending_orders: dict[str, dict] = {}

_CUSTOMER_COMMAND_PREFIXES = (
    "查看商品",
    "商品類別",
    "商品確認",
    "下單",
    "我的未付款",
    "回報付款",
    "直接聯絡老闆",
    "聯絡老闆",
)

_BOSS_COMMAND_PREFIXES = (
    "幫客戶下單",
    "商品類別",
    "商品確認",
    "老闆下單",
    "查詢客戶",
    "客戶訂單",
    "未付款清單",
    "確認付款",
    "送達",
)


def _starts_with_known_command(user_msg: str, prefixes: tuple[str, ...]) -> bool:
    return any(user_msg == prefix or user_msg.startswith(prefix) for prefix in prefixes)


def _product_summary_text(product_name: str, quantity: int, unit_price: int) -> str:
    return f"{product_name} x{quantity}  ${unit_price * quantity:,}"


def _build_customer_order_success_text(
    order_id: str,
    product_name: str,
    spec: str,
    quantity: int,
    unit_price: int,
    address: str,
) -> str:
    total = unit_price * quantity
    spec_line = f"\n尺寸：{spec}" if spec else ""
    return (
        f"✅ 下單成功！\n"
        f"訂單編號：{order_id}\n"
        f"品項：{product_name} x {quantity}{spec_line}\n"
        f"單價：${unit_price:,}\n"
        f"總金額：${total:,}\n"
        f"付款狀態：未付款\n"
        f"配送狀態：尚未送達\n"
        f"🏠 配送地址：{address}\n\n"
        f"如有疑問請輸入「直接聯絡老闆」"
    )

def handle_text_message(event: MessageEvent, line_bot_api: LineBotApi):
    """
    處理 LINE 文字訊息的進入點。
    負責解析使用者身分，並將請求分流給對應的角色邏輯模組。

    系統目前僅支援兩個角色：
      - boss   ：老闆，負責訂單管理、確認付款、回報送達
      - customer：客戶，負責下單、查詢、回報付款
    （driver 角色已下線，送貨回報改由老闆於 LINE Bot 直接操作）
    """
    user_id = event.source.user_id
    user_msg = event.message.text.strip()

    # ── 本機開發模式：角色切換指令攔截 ──────────────────────────────
    if is_dev_mode():
        dev_reply = handle_dev_command(user_id, user_msg)
        if dev_reply is not None:
            LineService.reply_text(line_bot_api, event.reply_token, dev_reply)
            return
    # ───────────────────────────────────────────────────────────────

    # 嘗試撈取使用者的 LINE 暱稱，讓 Firebase 裡的資料更具可讀性
    # (注意：若使用者未加 Bot 為好友，可能無法取得 profile)
    display_name = "Unknown"
    try:
        profile = line_bot_api.get_profile(user_id)
        display_name = profile.display_name
    except Exception as e:
        logger.warning(f"Could not get profile: {e}")

    # 1. 查詢或建立使用者，取得角色權限
    #    DEV_MODE 下若有記憶體覆蓋值，優先使用，跳過 Firestore 查詢
    if is_dev_mode() and (override := get_role_override(user_id)):
        role = override
        logger.info(f"[DEV] Using overridden role '{role}'.")
    else:
        try:
            role = FirebaseDB.get_or_create_user(user_id, display_name)
        except Exception as e:
            logger.error(f"Database error when getting user role: {e}")
            LineService.reply_text(line_bot_api, event.reply_token, "系統連線異常，請稍後再試。")
            return

    # 2. 根據角色進行主路由分流：boss 走老闆流程，其餘一律視為 customer
    if role == 'boss':
        _handle_boss_message(event, line_bot_api, user_msg, user_id)
    else:
        _handle_customer_message(event, line_bot_api, user_msg, user_id, display_name)


# ==========================================
# 角色專屬邏輯區塊 (Private Functions)
# ==========================================

def _handle_boss_message(event: MessageEvent, line_bot_api: LineBotApi, user_msg: str, user_id: str):
    """老闆端功能（兼任送貨回報）"""

    if user_msg == "取消下單":
        if _boss_pending_orders.pop(user_id, None) is not None:
            reply_text = "已取消下單。"
        else:
            reply_text = "目前沒有進行中的下單流程。"

    elif user_id in _boss_pending_orders and not _starts_with_known_command(user_msg, _BOSS_COMMAND_PREFIXES):
        pending = _boss_pending_orders[user_id]
        product = pending["product"]
        product_name = product.get("productName", "")
        spec = product.get("spec", "")
        unit_price = int(product.get("price", 0) or 0)
        quantity = pending["quantity"]
        customer_name = pending["customer_name"]
        customer_id = pending["customer_id"] or f"MANUAL_{customer_name}"
        address = user_msg
        try:
            order_id, _ = FirebaseDB.create_order(
                customer_id,
                customer_name,
                product_name,
                spec,
                quantity,
                unit_price,
                delivery_address=address,
            )
            _boss_pending_orders.pop(user_id, None)
            items_str = f"{product_name} x{quantity}"
            LineService.reply_flex(
                line_bot_api,
                event.reply_token,
                "訂單建立成功",
                _flex_boss_order_success(order_id, customer_name, items_str, unit_price * quantity, address),
            )
            return
        except Exception as e:
            logger.error(f"Boss order creation failed: {e}")
            reply_text = "建立訂單時發生錯誤，請稍後再試。"

    elif user_msg == "幫客戶下單":
        try:
            products = FirebaseDB.get_products()
        except Exception as e:
            logger.error(f"Failed to fetch products: {e}")
            LineService.reply_text(line_bot_api, event.reply_token, "查詢商品時發生錯誤，請稍後再試。")
            return

        if not products:
            LineService.reply_text(line_bot_api, event.reply_token, "目前暫無上架商品，請稍後再查詢。")
            return

        LineService.reply_flex(line_bot_api, event.reply_token, "商品目錄", _flex_category_picker(products))
        return

    elif user_msg.startswith("商品類別"):
        category_name = user_msg[4:].strip()
        try:
            products = FirebaseDB.get_products()
        except Exception as e:
            logger.error(f"Failed to fetch products: {e}")
            LineService.reply_text(line_bot_api, event.reply_token, "查詢商品時發生錯誤，請稍後再試。")
            return

        variants = [
            (idx, p) for idx, p in enumerate(products, start=1)
            if p.get('productName') == category_name
        ]
        if not variants:
            LineService.reply_text(line_bot_api, event.reply_token,
                                   f"找不到商品「{category_name}」，請輸入「幫客戶下單」重新選擇。")
            return

        if len(variants) == 1:
            global_idx, product = variants[0]
            LineService.reply_flex(line_bot_api, event.reply_token,
                                   f"商品資訊 #{global_idx}", _flex_product_detail(global_idx, product))
        else:
            LineService.reply_flex(line_bot_api, event.reply_token,
                                   f"{category_name} 規格選擇", _flex_spec_picker(category_name, variants))
        return

    elif user_msg.startswith("商品確認"):
        try:
            idx = int(user_msg[4:].strip())
        except ValueError:
            LineService.reply_text(line_bot_api, event.reply_token, "無效的商品編號，請重新選擇。")
            return

        try:
            products = FirebaseDB.get_products()
        except Exception as e:
            logger.error(f"Failed to fetch products: {e}")
            LineService.reply_text(line_bot_api, event.reply_token, "查詢商品時發生錯誤，請稍後再試。")
            return

        if idx < 1 or idx > len(products):
            LineService.reply_text(line_bot_api, event.reply_token,
                                   f"商品編號 {idx} 不存在，請輸入「幫客戶下單」重新選擇。")
            return

        LineService.reply_flex(line_bot_api, event.reply_token,
                               f"商品資訊 #{idx}", _flex_product_detail(idx, products[idx - 1]))
        return

    elif user_msg.startswith("老闆下單"):
        parts = user_msg.split(maxsplit=3)
        if len(parts) != 4:
            reply_text = (
                "格式不正確，請輸入：\n"
                "老闆下單 編號 數量 客戶名稱\n"
                "例：老闆下單 3 2 王小明"
            )
        else:
            _, idx_str, qty_str, customer_name = parts
            try:
                idx = int(idx_str)
                quantity = int(qty_str)
                if quantity <= 0:
                    raise ValueError
            except ValueError:
                reply_text = "編號與數量請輸入正整數\n例：老闆下單 3 2 王小明"
            else:
                try:
                    products = FirebaseDB.get_products()
                    if idx < 1 or idx > len(products):
                        reply_text = (
                            f"編號 {idx} 不存在，請先輸入「幫客戶下單」確認正確編號"
                            f"（1 ～ {len(products)}）。"
                        )
                    else:
                        customers = FirebaseDB.search_customers_by_name(customer_name)
                        if len(customers) > 1:
                            lines = [f"找到多位符合「{customer_name}」的客戶，請輸入更完整的姓名："]
                            for customer in customers:
                                lines.append(f"• {customer.get('displayName', '未知客戶')}")
                            reply_text = "\n".join(lines)
                        else:
                            customer = customers[0] if customers else None
                            product = products[idx - 1]
                            unit_price = int(product.get("price", 0) or 0)
                            resolved_name = customer.get("displayName", customer_name) if customer else customer_name
                            _boss_pending_orders[user_id] = {
                                "product_idx": idx,
                                "quantity": quantity,
                                "customer_name": resolved_name,
                                "customer_id": customer.get("userId", "") if customer else "",
                                "product": product,
                            }
                            LineService.reply_flex(
                                line_bot_api,
                                event.reply_token,
                                "輸入客戶配送地址",
                                _flex_boss_address_prompt(
                                    resolved_name,
                                    _product_summary_text(product.get("productName", ""), quantity, unit_price),
                                ),
                            )
                            return
                except Exception as e:
                    logger.error(f"Boss pending order creation failed: {e}")
                    reply_text = "建立訂單流程時發生錯誤，請稍後再試。"

    # ── 查詢客戶 <關鍵字> ─────────────────────────────────────────────
    elif user_msg.startswith("查詢客戶"):
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
                logger.error(f"Failed to fetch orders for customer: {e}")
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
                        delivered = "🚚已送達" if o.get('deliveryDate') else "📦未送達"
                        total = o.get('totalAmount', 0)
                        order_id = o.get('orderId', 'N/A')
                        lines.append(f"• [{order_id}]\n  {items_str}\n  總額：${total}　{status}　{delivered}")
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
                        # 待確認的訂單以「🕓 待確認」標示，未付款則用一般 bullet
                        if o.get('paymentStatus') == 'pending_confirmation':
                            prefix = "🕓 待確認"
                        else:
                            prefix = "•"
                        lines.append(f"{prefix} {customer}｜{items_str}｜${total}\n  訂單編號：{order_id}")
                    lines.append("\n💡 客戶已回報但尚未確認的訂單：輸入「確認付款 訂單編號 付款方式」即可入帳。")
                    lines.append("    付款方式：cash / transfer / check")
                    reply_text = "\n".join(lines)

    # ── 確認付款 <訂單編號> <付款方式> ─────────────────────────────────
    elif user_msg.startswith("確認付款"):
        rest = user_msg[4:].strip()
        parts = rest.split()
        if len(parts) != 2:
            reply_text = (
                "格式不正確，請輸入：\n"
                "確認付款 訂單編號 付款方式\n"
                "付款方式：cash / transfer / check\n"
                "例：確認付款 ORD-20260514-101530-a1b2 cash"
            )
        else:
            order_id, method = parts
            try:
                FirebaseDB.confirm_payment(order_id, method)
                reply_text = (
                    f"✅ 已確認付款並入帳。\n"
                    f"訂單編號：{order_id}\n"
                    f"付款方式：{method}"
                )
            except ValueError as e:
                msg = str(e)
                if msg == "already_paid":
                    reply_text = f"訂單「{order_id}」已是已付款狀態，無需再次確認。"
                elif msg == "invalid_method":
                    reply_text = "付款方式不合法，請輸入 cash / transfer / check。"
                else:
                    reply_text = f"找不到訂單「{order_id}」，請確認編號是否正確。"
            except Exception as e:
                logger.error(f"confirm_payment failed: {e}")
                reply_text = "確認付款時發生錯誤，請稍後再試。"

    # ── 送達 <訂單編號> ────────────────────────────────────────────────
    # 由老闆直接於 LINE Bot 標記訂單為已送達。
    # 這是唯一會寫入 deliveryDate 的入口。
    elif user_msg.startswith("送達"):
        order_id = user_msg[2:].strip()
        if not order_id:
            reply_text = (
                "請在「送達」後面加上訂單編號，例：\n"
                "送達 ORD-20260514-101530-a1b2"
            )
        else:
            try:
                FirebaseDB.mark_delivered(order_id, user_id)
                reply_text = f"✅ 已標記訂單 {order_id} 為已送達。"
            except Exception as e:
                logger.error(f"mark_delivered failed: {e}")
                reply_text = "標記送達時發生錯誤，請稍後再試。"

    # ── 預設提示 ──────────────────────────────────────────────────────
    else:
        reply_text = (
            "老闆您好！可用指令如下：\n"
            "• 輸入「幫客戶下單」替客戶下訂單\n"
            "• 輸入「查詢客戶 關鍵字」模糊搜尋客戶\n"
            "• 輸入「客戶訂單 姓名」查看客戶歷史訂單\n"
            "• 輸入「未付款清單 本週／本月／近兩個月／近三個月」查看未付款訂單\n"
            "• 輸入「確認付款 訂單編號 付款方式」確認客戶已付款（cash/transfer/check）\n"
            "• 輸入「送達 訂單編號」標記訂單為已送達"
        )

    LineService.reply_text(line_bot_api, event.reply_token, reply_text)


def _handle_customer_message(event: MessageEvent, line_bot_api: LineBotApi, user_msg: str, user_id: str, display_name: str):
    """客戶端功能"""

    if user_msg == "取消下單":
        if _pending_orders.pop(user_id, None) is not None:
            reply_text = "已取消下單。"
        else:
            reply_text = "目前沒有進行中的下單流程。"

    elif user_id in _pending_orders and not _starts_with_known_command(user_msg, _CUSTOMER_COMMAND_PREFIXES):
        pending = _pending_orders[user_id]
        product = pending["product"]
        product_name = product.get("productName", "")
        spec = product.get("spec", "")
        quantity = pending["quantity"]
        unit_price = int(product.get("price", 0) or 0)
        address = user_msg
        try:
            order_id, _ = FirebaseDB.create_order(
                user_id,
                display_name,
                product_name,
                spec,
                quantity,
                unit_price,
                delivery_address=address,
            )
            _pending_orders.pop(user_id, None)
            reply_text = _build_customer_order_success_text(
                order_id,
                product_name,
                spec,
                quantity,
                unit_price,
                address,
            )
        except Exception as e:
            logger.error(f"Order creation failed: {e}")
            reply_text = "下單時發生錯誤，請稍後再試或聯絡老闆。"

    # ── 查看商品：Step 1 — 商品類別選擇 ──────────────────────────────────
    elif user_msg == "查看商品":
        try:
            products = FirebaseDB.get_products()
        except Exception as e:
            logger.error(f"Failed to fetch products: {e}")
            LineService.reply_text(line_bot_api, event.reply_token, "查詢商品時發生錯誤，請稍後再試。")
            return

        if not products:
            LineService.reply_text(line_bot_api, event.reply_token, "目前暫無上架商品，請稍後再查詢。")
            return

        LineService.reply_flex(line_bot_api, event.reply_token,
                               "商品目錄", _flex_category_picker(products))
        return

    # ── 商品類別 <品名>：Step 2 — 規格選擇（或直接顯示商品詳情）───────────────
    elif user_msg.startswith("商品類別"):
        category_name = user_msg[4:].strip()
        try:
            products = FirebaseDB.get_products()
        except Exception as e:
            logger.error(f"Failed to fetch products: {e}")
            LineService.reply_text(line_bot_api, event.reply_token, "查詢商品時發生錯誤，請稍後再試。")
            return

        variants = [
            (idx, p) for idx, p in enumerate(products, start=1)
            if p.get('productName') == category_name
        ]
        if not variants:
            LineService.reply_text(line_bot_api, event.reply_token,
                                   f"找不到商品「{category_name}」，請輸入「查看商品」重新選擇。")
            return

        if len(variants) == 1:
            # Only one spec — skip spec picker and go straight to product detail
            global_idx, product = variants[0]
            LineService.reply_flex(line_bot_api, event.reply_token,
                                   f"商品資訊 #{global_idx}", _flex_product_detail(global_idx, product))
        else:
            LineService.reply_flex(line_bot_api, event.reply_token,
                                   f"{category_name} 規格選擇", _flex_spec_picker(category_name, variants))
        return

    # ── 商品確認 <編號>：Step 3 — 商品詳情卡片 ──────────────────────────
    elif user_msg.startswith("商品確認"):
        try:
            idx = int(user_msg[4:].strip())
        except ValueError:
            LineService.reply_text(line_bot_api, event.reply_token, "無效的商品編號，請重新選擇。")
            return

        try:
            products = FirebaseDB.get_products()
        except Exception as e:
            logger.error(f"Failed to fetch products: {e}")
            LineService.reply_text(line_bot_api, event.reply_token, "查詢商品時發生錯誤，請稍後再試。")
            return

        if idx < 1 or idx > len(products):
            LineService.reply_text(line_bot_api, event.reply_token,
                                   f"商品編號 {idx} 不存在，請輸入「查看商品」重新選擇。")
            return

        LineService.reply_flex(line_bot_api, event.reply_token,
                               f"商品資訊 #{idx}", _flex_product_detail(idx, products[idx - 1]))
        return

    # ── 下單 <編號> <數量> ────────────────────────────────────────────
    elif user_msg.startswith("下單"):
        parts = user_msg.split()
        if len(parts) != 3:
            reply_text = "下單格式不正確，請輸入：\n下單 編號 數量\n先輸入「查看商品」取得編號\n例：下單 3 2"
        else:
            try:
                idx = int(parts[1])
                quantity = int(parts[2])
                if quantity <= 0:
                    raise ValueError
            except ValueError:
                reply_text = "編號與數量請輸入正整數\n例：下單 3 2"
            else:
                try:
                    products = FirebaseDB.get_products()
                    if idx < 1 or idx > len(products):
                        reply_text = (
                            f"編號 {idx} 不存在，請輸入「查看商品」確認正確編號"
                            f"（1 ～ {len(products)}）。"
                        )
                    else:
                        product = products[idx - 1]
                        unit_price = int(product.get('price', 0) or 0)
                        _pending_orders[user_id] = {
                            "product_idx": idx,
                            "quantity": quantity,
                            "product": product,
                        }
                        LineService.reply_flex(
                            line_bot_api,
                            event.reply_token,
                            "輸入配送地址",
                            _flex_customer_address_prompt(
                                _product_summary_text(product.get("productName", ""), quantity, unit_price),
                            ),
                        )
                        return
                except Exception as e:
                    logger.error(f"Order creation failed: {e}")
                    reply_text = "下單時發生錯誤，請稍後再試或聯絡老闆。"

    # ── 我的未付款 ─────────────────────────────────────────────────────
    elif user_msg == "我的未付款":
        try:
            orders = FirebaseDB.get_customer_unpaid_orders(user_id)
        except Exception as e:
            logger.error(f"Failed to fetch unpaid orders: {e}")
            LineService.reply_text(line_bot_api, event.reply_token, "查詢時發生錯誤，請稍後再試。")
            return

        if not orders:
            reply_text = "您目前沒有任何未付款的訂單！"
        else:
            # 以下單時間由舊到新排列，讓序號穩定
            orders.sort(key=lambda o: (o.get('orderDate') is None, o.get('orderDate')))
            total_debt = sum(o.get('totalAmount', 0) for o in orders)
            lines = [f"📋 您目前共有 {len(orders)} 筆未付款訂單（合計 ${total_debt}）：\n"]
            tw_tz = timezone(timedelta(hours=8))
            for idx, o in enumerate(orders, start=1):
                items_list = o.get('items', [])
                items_str = "、".join([
                    f"{i.get('productName', '')} x{i.get('quantity', 0)}"
                    for i in items_list
                ])
                total = o.get('totalAmount', 0)
                status = o.get('paymentStatus', '')
                status_str = "（已回報，待確認）" if status == 'pending_confirmation' else ""
                order_date = o.get('orderDate')
                if order_date is not None:
                    try:
                        date_str = order_date.astimezone(tw_tz).strftime('%Y/%m/%d %H:%M')
                    except Exception:
                        date_str = str(order_date)
                else:
                    date_str = "—"
                lines.append(f"#{idx}  {date_str}\n  {items_str}｜${total}{status_str}")
            lines.append("\n💡 輸入「回報付款 序號」可通知老闆您已付款（例：回報付款 1）")
            reply_text = "\n".join(lines)

    # ── 回報付款 <序號> ──────────────────────────────────────────────
    elif user_msg.startswith("回報付款"):
        serial_str = user_msg[4:].strip()
        if not serial_str:
            reply_text = "請在「回報付款」後面加上序號，例：\n回報付款 1"
        else:
            try:
                serial = int(serial_str)
            except ValueError:
                serial = None
            if serial is None or serial < 1:
                reply_text = "序號請輸入正整數，例：\n回報付款 1"
            elif not Config.BOSS_LINE_ID:
                logger.error("BOSS_LINE_ID is not configured.")
                reply_text = "目前無法傳送通知，請稍後再試。"
            else:
                try:
                    unpaid_orders = FirebaseDB.get_customer_unpaid_orders(user_id)
                    unpaid_orders.sort(key=lambda o: (o.get('orderDate') is None, o.get('orderDate')))
                    if serial > len(unpaid_orders):
                        reply_text = f"序號 {serial} 不存在，您目前有 {len(unpaid_orders)} 筆未付款訂單。"
                    else:
                        o = unpaid_orders[serial - 1]
                        order_id = o.get('orderId')
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
                            f"請確認收款後輸入：\n"
                            f"確認付款 {order_id} cash／transfer／check"
                        )
                        LineService.push_text(line_bot_api, Config.BOSS_LINE_ID, push_msg)
                        reply_text = (
                            f"✅ 已通知老闆您的付款回報！\n"
                            f"品項：{items_str}\n"
                            f"金額：${total}\n"
                            f"老闆確認後即會更新狀態。"
                        )
                except PermissionError:
                    reply_text = "找不到屬於您的訂單，請確認序號是否正確。"
                except ValueError as e:
                    if str(e) == "already_paid":
                        reply_text = f"第 {serial} 筆訂單已標註為已付款，無需重複回報。"
                    else:
                        reply_text = "找不到對應訂單，請確認序號是否正確。"
                except Exception as e:
                    logger.error(f"notify_payment failed: {e}")
                    reply_text = "回報付款時發生錯誤，請稍後再試。"

    # ── 聯絡老闆 <訊息內容> ────────────────────────────────────────────
    elif user_msg == "直接聯絡老闆" or user_msg == "聯絡老闆":
        if not Config.BOSS_LINE_ID:
            logger.error("BOSS_LINE_ID is not configured.")
            reply_text = "目前無法連結到老闆，請稍後再試。"
        else:
            LineService.reply_flex(
                line_bot_api,
                event.reply_token,
                "聯絡老闆",
                _flex_contact_boss_card(Config.BOSS_LINE_ID),
            )
            return

    # ── 預設提示 ──────────────────────────────────────────────────────
    else:
        reply_text = (
            "歡迎光臨！您可以：\n"
            "• 輸入「查看商品」瀏覽目前商品\n"
            "• 輸入「下單 編號 數量」下單（編號請先查看商品取得）\n"
            "• 輸入「我的未付款」查看您尚未付款的訂單\n"
            "• 輸入「回報付款 序號」通知老闆您已付款（序號請先查看「我的未付款」）\n"
            "• 輸入「直接聯絡老闆」立即開啟與老闆的對話\n"
            "• 快速輸入「直接聯絡老闆」即可直接聯繫老闆"
        )

    LineService.reply_text(line_bot_api, event.reply_token, reply_text)


# ==========================================
# Flex Message Builders — Product Catalogue
# ==========================================

def _flex_category_picker(products: list):
    """
    Step 1: Shows all unique product names as tappable buttons.
    Tapping sends "商品類別 <productName>" back to the bot.
    """
    seen = []
    for p in products:
        name = p.get('productName', '')
        if name and name not in seen:
            seen.append(name)

    buttons = [
        ButtonComponent(
            action=MessageAction(label=name, text=f'商品類別 {name}'),
            style='secondary',
            height='sm',
            margin='xs',
        )
        for name in seen
    ]

    return BubbleContainer(
        size='giga',
        header=BoxComponent(
            layout='vertical',
            background_color='#2C7A4B',
            padding_all='lg',
            contents=[
                TextComponent(text='堯順企業社', color='#CCFFCC', size='xs'),
                TextComponent(text='商品目錄', color='#FFFFFF', weight='bold', size='xl'),
            ],
        ),
        body=BoxComponent(
            layout='vertical',
            spacing='xs',
            padding_all='md',
            contents=buttons,
        ),
    )


def _flex_spec_picker(category_name: str, variants: list):
    """
    Step 2: Shows all spec variants of a product name as tappable rows.
    Each row displays spec and price; tapping sends "商品確認 <idx>".
    variants: list of (global_idx, product_dict)
    """
    rows = []
    for i, (global_idx, p) in enumerate(variants):
        spec = p.get('spec', '') or '—'
        price = p.get('price', 0)
        rows.append(BoxComponent(
            layout='horizontal',
            action=MessageAction(label=f'選擇#{global_idx}', text=f'商品確認 {global_idx}'),
            padding_all='md',
            contents=[
                BoxComponent(
                    layout='vertical',
                    flex=1,
                    contents=[
                        TextComponent(text=f'#{global_idx}', size='xxs', color='#AAAAAA'),
                        TextComponent(text=spec, size='sm', wrap=True, color='#333333'),
                    ],
                ),
                TextComponent(
                    text=f'${price:,}',
                    size='sm',
                    align='end',
                    gravity='center',
                    color='#1DB446',
                    weight='bold',
                    flex=0,
                ),
            ],
        ))
        if i < len(variants) - 1:
            rows.append(SeparatorComponent(margin='none', color='#DDDDDD'))

    return BubbleContainer(
        size='giga',
        header=BoxComponent(
            layout='vertical',
            background_color='#2C7A4B',
            padding_all='lg',
            contents=[
                TextComponent(text=category_name, color='#FFFFFF', weight='bold',
                              size='md', wrap=True),
                TextComponent(text='請選擇規格', color='#CCFFCC', size='xs', margin='xs'),
            ],
        ),
        body=BoxComponent(
            layout='vertical',
            padding_all='none',
            spacing='none',
            contents=rows,
        ),
    )


def _flex_product_detail(global_idx: int, product: dict):
    """
    Step 3: Product detail card — name, spec, price, index, and order instruction.
    Instruction text uses no emoji per spec.
    """
    name = product.get('productName', '')
    spec = product.get('spec', '')
    price = product.get('price', 0)

    body_items = [
        TextComponent(text=name, weight='bold', size='md', wrap=True, color='#222222'),
        SeparatorComponent(margin='md', color='#EEEEEE'),
    ]
    if spec:
        body_items.append(BoxComponent(
            layout='horizontal',
            margin='md',
            contents=[
                TextComponent(text='規格', size='sm', color='#888888', flex=2),
                TextComponent(text=spec, size='sm', wrap=True, flex=5, color='#444444'),
            ],
        ))
    body_items.append(BoxComponent(
        layout='horizontal',
        margin='sm',
        contents=[
            TextComponent(text='單價', size='sm', color='#888888', flex=2),
            TextComponent(text=f'${price:,}', size='sm', color='#1DB446',
                          weight='bold', flex=5),
        ],
    ))
    body_items.append(BoxComponent(
        layout='horizontal',
        margin='sm',
        contents=[
            TextComponent(text='商品編號', size='sm', color='#888888', flex=2),
            TextComponent(text=str(global_idx), size='sm', color='#444444', flex=5),
        ],
    ))

    return BubbleContainer(
        size='kilo',
        header=BoxComponent(
            layout='vertical',
            background_color='#2C7A4B',
            padding_all='md',
            contents=[
                TextComponent(text='商品資訊', color='#FFFFFF', size='sm', weight='bold'),
            ],
        ),
        body=BoxComponent(
            layout='vertical',
            padding_all='lg',
            spacing='none',
            contents=body_items,
        ),
        footer=BoxComponent(
            layout='vertical',
            background_color='#F5F5F5',
            padding_all='lg',
            spacing='xs',
            contents=[
                TextComponent(text='下單方式', size='sm', weight='bold', color='#555555'),
                TextComponent(text=f'客戶：下單 {global_idx} 數量',
                              size='sm', color='#333333', margin='sm'),
                TextComponent(text=f'老闆：老闆下單 {global_idx} 數量 客戶名稱',
                              size='sm', color='#333333'),
                TextComponent(text=f'例: 下單 {global_idx} 2',
                              size='xs', color='#AAAAAA'),
            ],
        ),
    )


def _flex_customer_address_prompt(items_summary: str):
    return BubbleContainer(
        size='giga',
        header=BoxComponent(
            layout='vertical',
            background_color='#2C7A4B',
            padding_all='lg',
            contents=[
                TextComponent(text='📦 輸入配送地址', color='#FFFFFF', weight='bold', size='lg'),
            ],
        ),
        body=BoxComponent(
            layout='vertical',
            padding_all='lg',
            contents=[
                TextComponent(text=items_summary, wrap=True, weight='bold', size='md', color='#222222'),
                SeparatorComponent(margin='md', color='#EEEEEE'),
                TextComponent(
                    text='請直接輸入送貨地址，例如：\n台北市大安區忠孝東路四段 1 號',
                    wrap=True,
                    margin='md',
                    size='sm',
                    color='#444444',
                ),
                TextComponent(
                    text='如需取消請輸入「取消下單」',
                    wrap=True,
                    margin='md',
                    size='xs',
                    color='#888888',
                ),
            ],
        ),
    )


def _flex_boss_address_prompt(customer_name: str, items_summary: str):
    return BubbleContainer(
        size='giga',
        header=BoxComponent(
            layout='vertical',
            background_color='#2C7A4B',
            padding_all='lg',
            contents=[
                TextComponent(text='📦 輸入客戶配送地址', color='#FFFFFF', weight='bold', size='lg'),
            ],
        ),
        body=BoxComponent(
            layout='vertical',
            padding_all='lg',
            contents=[
                BoxComponent(
                    layout='horizontal',
                    margin='md',
                    contents=[
                        TextComponent(text='客戶', size='sm', color='#888888', flex=2),
                        TextComponent(text=customer_name, size='sm', wrap=True, color='#222222', flex=5),
                    ],
                ),
                BoxComponent(
                    layout='horizontal',
                    margin='md',
                    contents=[
                        TextComponent(text='品項', size='sm', color='#888888', flex=2),
                        TextComponent(text=items_summary, size='sm', wrap=True, color='#222222', flex=5),
                    ],
                ),
                SeparatorComponent(margin='md', color='#EEEEEE'),
                TextComponent(
                    text='請直接輸入送貨地址，例如：\n台北市大安區忠孝東路四段 1 號',
                    wrap=True,
                    margin='md',
                    size='sm',
                    color='#444444',
                ),
                TextComponent(
                    text='如需取消請輸入「取消下單」',
                    wrap=True,
                    margin='md',
                    size='xs',
                    color='#888888',
                ),
            ],
        ),
    )


def _flex_boss_order_success(order_id: str, customer_name: str, items_str: str, total: int, address: str):
    def _row(label: str, value: str):
        return BoxComponent(
            layout='horizontal',
            margin='md',
            contents=[
                TextComponent(text=label, size='sm', color='#888888', flex=2),
                TextComponent(text=value, size='sm', wrap=True, color='#222222', flex=5),
            ],
        )

    return BubbleContainer(
        size='giga',
        header=BoxComponent(
            layout='vertical',
            background_color='#2C7A4B',
            padding_all='lg',
            contents=[
                TextComponent(text='✅ 訂單建立成功', color='#FFFFFF', weight='bold', size='lg'),
            ],
        ),
        body=BoxComponent(
            layout='vertical',
            padding_all='lg',
            contents=[
                _row('客戶', customer_name),
                _row('品項', items_str),
                _row('金額', f'${total:,}'),
                _row('地址', address),
                _row('訂單號', order_id),
            ],
        ),
        footer=BoxComponent(
            layout='vertical',
            padding_all='lg',
            contents=[
                TextComponent(
                    text='付款方式及狀態可於管理後台更新',
                    size='xs',
                    color='#888888',
                    wrap=True,
                ),
            ],
        ),
    )


def _flex_contact_boss_card(boss_id: str):
    line_url = f"https://line.me/R/ti/p/{boss_id}"
    return BubbleContainer(
        size='giga',
        header=BoxComponent(
            layout='vertical',
            background_color='#2C7A4B',
            padding_all='lg',
            contents=[
                TextComponent(text='💬 聯絡老闆', color='#FFFFFF', weight='bold', size='lg'),
            ],
        ),
        body=BoxComponent(
            layout='vertical',
            padding_all='lg',
            contents=[
                TextComponent(
                    text='點擊下方按鈕，即可直接開啟與老闆的對話視窗。',
                    wrap=True,
                    size='sm',
                    color='#444444',
                ),
            ],
        ),
        footer=BoxComponent(
            layout='vertical',
            padding_all='lg',
            contents=[
                FillerComponent(),
                ButtonComponent(
                    style='primary',
                    color='#2C7A4B',
                    action=URIAction(label='開啟與老闆的對話', uri=line_url),
                ),
            ],
        ),
    )
