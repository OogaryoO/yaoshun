import json
import logging
import secrets
from datetime import datetime, timezone
import firebase_admin
from firebase_admin import credentials, firestore
from config import Config
from services.schemas import (
    OrderDoc,
    OrderItem,
    UserDoc,
    PAYMENT_METHODS,
    PAYMENT_STATUSES,
    validate_payment_update_patch,
)

logger = logging.getLogger(__name__)

# 建立一個全域變數來儲存 Firestore Client
db = None

def init_firebase():
    """
    初始化 Firebase Admin SDK 與 Firestore 連線。
    在 app.py 啟動時會呼叫此函式。
    """
    global db
    # 避免在 Serverless 環境（如 Render）或熱重載時重複初始化
    if not firebase_admin._apps:
        try:
            cert = json.loads(Config.GOOGLE_CREDENTIALS_JSON)
            cred = credentials.Certificate(cert)
            firebase_admin.initialize_app(cred)
            db = firestore.client()
            logger.info("Firebase Firestore initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Firebase: {e}")
            raise
    else:
        db = firestore.client()

class FirebaseDB:
    """
    封裝對 Firestore 的所有 CRUD 操作。

    所有寫入 (set/update) 之前都會經過 services.schemas 的驗證，
    確保資料與 dashboard (cpcap1214/IM2008) 定義的型別契約一致。
    """

    # ==========================
    # 1. 權限管理 (Users)
    # ==========================
    @staticmethod
    def get_or_create_user(user_id: str, display_name: str = "Unknown") -> str:
        """
        身分驗證與路由：透過 LINE UID 查詢使用者。
        若查無資料，預設新建為 "customer" (除非是老闆的 UID)。
        BOSS_LINE_ID 的比對永遠優先於 Firestore 中存放的 role 值，
        確保即使資料庫記錄有誤也能正確分流。
        回傳使用者的 role 字串。
        """
        # 永遠以 BOSS_LINE_ID 為最高優先：只要 UID 符合，無論資料庫內容為何都給 boss
        is_boss = bool(Config.BOSS_LINE_ID and user_id == Config.BOSS_LINE_ID)
        role = 'boss' if is_boss else 'customer'

        user_ref = db.collection('Users').document(user_id)
        doc = user_ref.get()

        if doc.exists:
            stored_role = doc.to_dict().get('role', 'customer')
            if is_boss and stored_role != 'boss':
                # 修正資料庫中錯誤的 role，確保一致性
                user_ref.update({'role': 'boss'})
                logger.info(f"Updated user {user_id} role from '{stored_role}' to 'boss'.")
            elif not is_boss:
                role = stored_role
        else:
            # 建立新使用者
            payload = UserDoc(
                role=role,
                displayName=display_name or "Unknown",
                phone="",
                notes="",
                createdAt=firestore.SERVER_TIMESTAMP,
            ).to_dict()
            user_ref.set(payload)
            logger.info(f"Created new user with role {role}.")

        return role

    # ==========================
    # 2. 客戶端功能 (Products / Orders)
    # ==========================
    @staticmethod
    def get_products() -> list:
        """
        查詢目前上架中的商品列表。
        只回傳 isActive=True 的商品。
        """
        products_ref = db.collection('Products').where('isActive', '==', True)
        results = []
        for doc in products_ref.stream():
            data = doc.to_dict()
            data['productId'] = doc.id
            results.append(data)
        # Sort by name then spec so same-name products are grouped and the
        # numbered list order is stable across calls (customers order by index).
        results.sort(key=lambda p: (p.get('productName', ''), p.get('spec', '')))
        return results

    @staticmethod
    def create_order(
        user_id: str,
        display_name: str,
        product_name: str,
        spec: str,
        quantity: int,
        unit_price: int,
        delivery_address: str = "",
    ) -> tuple:
        """
        建立新訂單，寫入 Firestore Orders 集合。

        參數對齊 dashboard OrderDoc / OrderItem 契約 — 每個品項都會帶上
        productName / spec / quantity / unitPrice / subtotal 五個欄位。

        回傳 (order_id, order_data) tuple。order_data 為實際送進
        Firestore 的 dict（已通過 schemas 驗證），方便後續觀察 / 記錄。
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        # 4-hex-char 隨機尾碼避免同秒下單造成 ID 撞單覆寫。
        order_id = f"ORD-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"

        subtotal = int(unit_price) * int(quantity)
        total_amount = subtotal
        item = OrderItem(
            productName=product_name,
            spec=spec or "",
            quantity=int(quantity),
            unitPrice=int(unit_price),
            subtotal=subtotal,
        )

        order = OrderDoc(
            customerId=user_id,
            customerName=display_name or "Unknown",
            driverId=None,                              # 未指派一律 None，絕不使用 "尚未指派"
            items=[item],
            totalAmount=total_amount,
            paymentStatus="unpaid",
            paymentMethod=None,                         # 未付款時必為 None
            orderDate=firestore.SERVER_TIMESTAMP,
            deliveryDate=None,                          # 尚未配送 → None
            createdAt=firestore.SERVER_TIMESTAMP,
            deliveryAddress=delivery_address,
        )
        payload = order.to_dict()

        db.collection('Orders').document(order_id).set(payload)
        logger.info(f"Created order {order_id}.")

        return order_id, payload

    @staticmethod
    def get_customer_orders(user_id: str, limit: int = 5) -> list:
        """
        查詢單一客戶的歷史訂單 (預設撈取最近 5 筆)。
        """
        orders_ref = db.collection('Orders')
        # 實作 where 查詢並以時間反序排列
        query = orders_ref.where('customerId', '==', user_id) \
                          .order_by('orderDate', direction=firestore.Query.DESCENDING) \
                          .limit(limit)

        results = []
        for doc in query.stream():
            data = doc.to_dict()
            data['orderId'] = doc.id  # 將自動產生的 Document ID 塞回資料中
            results.append(data)

        return results

    @staticmethod
    def get_customer_unpaid_orders(user_id: str) -> list:
        """
        查詢該客戶所有未付款（unpaid）或待確認（pending_confirmation）的訂單。
        僅用單一 where 過濾 customerId 避免複合索引需求，付款狀態在 Python 端篩選。
        """
        query = db.collection('Orders').where('customerId', '==', user_id)
        results = []
        for doc in query.stream():
            data = doc.to_dict()
            if data.get('paymentStatus') in ('unpaid', 'pending_confirmation'):
                data['orderId'] = doc.id
                results.append(data)
        return results

    # ==========================
    # 3. 司機端功能 (Orders)
    # ==========================
    @staticmethod
    def update_order_payment(order_id: str, driver_id: str, status: str, method=None):
        """
        司機 / 系統更新收款狀態。
        status: 'unpaid' | 'paid'
        method: 'cash' | 'transfer' | 'check' | None

        重點：此方法 **不再** 觸碰 deliveryDate（請改用 mark_delivered）。
              當 status='unpaid' 時，paymentMethod 與 paidAt 會被清空為 None。
              當 status='paid' 時，必須帶入 method，並會寫入 paidAt 伺服器時間戳。
        """
        if status not in ("unpaid", "paid"):
            raise ValueError(f"update_order_payment: invalid status {status!r}")
        if status == "paid" and method not in PAYMENT_METHODS:
            raise ValueError(
                f"update_order_payment: paid orders require method in {PAYMENT_METHODS}, got {method!r}"
            )

        patch: dict = {
            "paymentStatus": status,
            "driverId": driver_id,
        }
        if status == "paid":
            patch["paymentMethod"] = method
            patch["paidAt"] = firestore.SERVER_TIMESTAMP
        else:
            patch["paymentMethod"] = None
            patch["paidAt"] = None

        validate_payment_update_patch(patch)

        try:
            db.collection('Orders').document(order_id).update(patch)
            logger.info(f"Order {order_id} payment updated -> {status}.")
        except Exception as e:
            logger.error(f"Failed to update order {order_id}: {e}")
            raise

    @staticmethod
    def mark_delivered(order_id: str, driver_id: str):
        """
        司機回報送達。此為 deliveryDate 的**唯一**寫入點。
        """
        if not isinstance(driver_id, str) or not driver_id or driver_id == "尚未指派":
            raise ValueError("mark_delivered: driver_id must be a non-empty str (not '尚未指派')")
        try:
            db.collection('Orders').document(order_id).update({
                "driverId": driver_id,
                "deliveryDate": firestore.SERVER_TIMESTAMP,
            })
            logger.info(f"Order {order_id} marked as delivered.")
        except Exception as e:
            logger.error(f"Failed to mark order {order_id} as delivered: {e}")
            raise

    # ==========================
    # 4. 老闆端功能 (Users / Orders)
    # ==========================
    @staticmethod
    def search_customers_by_name(keyword: str) -> list:
        """
        模糊搜尋客戶：撈取所有 role=customer 的使用者，
        �� Python 端以 keyword 做不分大小寫的子字串比對。
        """
        keyword_lower = keyword.lower()
        results = []
        for doc in db.collection('Users').where('role', '==', 'customer').stream():
            data = doc.to_dict()
            name = data.get('displayName', '')
            if keyword_lower in name.lower():
                data['userId'] = doc.id
                results.append(data)
        return results

    @staticmethod
    def create_customer(display_name: str) -> str:
        """
        由老闆手動新增一位客戶，寫入 Firestore Users 集合。
        回傳新建立的 document ID。
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        customer_id = f"MANUAL-{now.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        user = UserDoc(
            role="customer",
            displayName=display_name,
            phone="",
            notes="",
            createdAt=firestore.SERVER_TIMESTAMP,
        )
        db.collection('Users').document(customer_id).set(user.to_dict())
        logger.info(f"Manually created customer {customer_id}: {display_name}")
        return customer_id

    @staticmethod
    def notify_payment(order_id: str, user_id: str) -> dict:
        """
        客戶回報已付款：驗證訂單屬於該客戶，更新狀態為 'pending_confirmation'。
        回傳訂單資料 dict，供老闆確認使用。
        """
        order_ref = db.collection('Orders').document(order_id)
        doc = order_ref.get()
        if not doc.exists:
            raise ValueError(f"Order {order_id} not found.")
        data = doc.to_dict()
        if data.get('customerId') != user_id:
            raise PermissionError(f"Order {order_id} does not belong to this user.")
        if data.get('paymentStatus') == 'paid':
            raise ValueError("already_paid")
        order_ref.update({'paymentStatus': 'pending_confirmation'})
        logger.info(f"Order {order_id} marked as pending_confirmation.")
        return data

    @staticmethod
    def confirm_payment(order_id: str, method: str) -> dict:
        """
        老闆確認客戶回報的付款。
        將 pending_confirmation / unpaid 狀態的訂單轉為 paid，
        並寫入 paymentMethod 與 paidAt。

        - 已是 paid → raise ValueError('already_paid')
        - method 不合法 → raise ValueError('invalid_method')
        - 訂單不存在 → raise ValueError(f"Order {order_id} not found.")
        """
        ref = db.collection('Orders').document(order_id)
        snap = ref.get()
        if not snap.exists:
            raise ValueError(f"Order {order_id} not found.")
        data = snap.to_dict()
        if data.get("paymentStatus") == "paid":
            raise ValueError("already_paid")
        if method not in PAYMENT_METHODS:
            raise ValueError("invalid_method")

        patch = {
            "paymentStatus": "paid",
            "paymentMethod": method,
            "paidAt": firestore.SERVER_TIMESTAMP,
        }
        validate_payment_update_patch(patch)
        ref.update(patch)
        logger.info(f"Order {order_id} confirmed paid by boss ({method}).")
        return data

    @staticmethod
    def get_orders_by_customer_name(customer_name: str, limit: int = 10) -> list:
        """
        查詢指定客戶名稱的歷史訂單，以下單時間反序排列。
        """
        query = db.collection('Orders') \
                  .where('customerName', '==', customer_name) \
                  .order_by('orderDate', direction=firestore.Query.DESCENDING) \
                  .limit(limit)
        results = []
        for doc in query.stream():
            data = doc.to_dict()
            data['orderId'] = doc.id
            results.append(data)
        return results

    @staticmethod
    def get_unpaid_orders(since: datetime = None) -> list:
        """
        未付款推播名單：撈取欠款清單，可選填 since 以限制最早下單時間。

        包含 'unpaid' 與 'pending_confirmation'（客戶已回報但老闆尚未確認入帳）。
        搭配 since 過濾時需要 firestore.indexes.json 中對應的複合索引。
        """
        query = db.collection('Orders').where(
            'paymentStatus', 'in', ['unpaid', 'pending_confirmation']
        )
        if since is not None:
            query = query.where('orderDate', '>=', since)
        results = []
        for doc in query.stream():
            data = doc.to_dict()
            data['orderId'] = doc.id
            results.append(data)
        return results

    @staticmethod
    def get_undelivered_orders(since: datetime = None) -> list:
        """
        查詢尚未送達的訂單（deliveryDate == None）。
        可選填 since 以在 Python 端篩選最早下單時間，避免複合索引需求。
        """
        query = db.collection('Orders').where('deliveryDate', '==', None)
        results = []
        for doc in query.stream():
            data = doc.to_dict()
            data['orderId'] = doc.id
            if since is not None:
                order_date = data.get('orderDate')
                if order_date is not None:
                    try:
                        if order_date.tzinfo is None:
                            order_date = order_date.replace(tzinfo=timezone.utc)
                        if order_date < since:
                            continue
                    except Exception:
                        pass
            results.append(data)
        return results
