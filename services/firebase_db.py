import json
import logging
from datetime import datetime, timezone
import firebase_admin
from firebase_admin import credentials, firestore
from config import Config

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
    """

    # ==========================
    # 1. 權限管理 (Users)
    # ==========================
    @staticmethod
    def get_or_create_user(user_id: str, display_name: str = "Unknown") -> str:
        """
        身分驗證與路由：透過 LINE UID 查詢使用者。
        若查無資料，預設新建為 "customer" (除非是老闆的 UID)。
        回傳使用者的 role 字串。
        """
        user_ref = db.collection('Users').document(user_id)
        doc = user_ref.get()

        if doc.exists:
            return doc.to_dict().get('role', 'customer')
        else:
            # 建立新使用者，若 UID 與設定檔的老闆 UID 相同則賦予 boss 權限
            role = 'boss' if user_id == Config.BOSS_LINE_ID else 'customer'
            user_ref.set({
                'role': role,
                'displayName': display_name,
                'createdAt': firestore.SERVER_TIMESTAMP
            })
            logger.info(f"Created new user {user_id} with role {role}.")
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
        return results

    @staticmethod
    def create_order(user_id: str, display_name: str, product_name: str, quantity: int, unit_price: int) -> tuple:
        """
        建立新訂單，存入 Firestore Orders 集合。
        回傳 (order_id, sheets_data) tuple，sheets_data 供 SheetsService 使用。
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        order_id = f"ORD-{now.strftime('%Y%m%d-%H%M%S')}"
        total_amount = unit_price * quantity

        db.collection('Orders').document(order_id).set({
            'customerId': user_id,
            'customerName': display_name,
            'items': [{'productName': product_name, 'quantity': quantity}],
            'totalAmount': total_amount,
            'paymentStatus': 'unpaid',
            'driverId': '尚未指派',
            'orderDate': firestore.SERVER_TIMESTAMP,
            'createdAt': firestore.SERVER_TIMESTAMP,
        })
        logger.info(f"Created order {order_id} for user {user_id}.")

        sheets_data = {
            'orderId': order_id,
            'orderDate': now,
            'customerName': display_name,
            'items': [{'productName': product_name, 'quantity': quantity}],
            'totalAmount': total_amount,
            'paymentStatus': 'unpaid',
            'driverId': '尚未指派',
        }
        return order_id, sheets_data

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
    def update_order_payment(order_id: str, driver_id: str, status: str, method: str):
        """
        司機回報更新：根據 Order ID 更新收款狀態與付款方式。
        status: "unpaid" 或 "paid"
        method: "cash", "transfer", "check" 等
        """
        order_ref = db.collection('Orders').document(order_id)
        try:
            order_ref.update({
                'paymentStatus': status,
                'paymentMethod': method,
                'driverId': driver_id,
                'deliveryDate': firestore.SERVER_TIMESTAMP
            })
            logger.info(f"Order {order_id} updated by driver {driver_id}.")
        except Exception as e:
            logger.error(f"Failed to update order {order_id}: {e}")
            raise

    # ==========================
    # 4. 老闆端功能 (Users / Orders)
    # ==========================
    @staticmethod
    def search_customers_by_name(keyword: str) -> list:
        """
        模糊搜尋客戶：撈取所有 role=customer 的使用者，
        在 Python 端以 keyword 做不分大小寫的子字串比對。
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
            raise PermissionError(f"Order {order_id} does not belong to user {user_id}.")
        if data.get('paymentStatus') == 'paid':
            raise ValueError("already_paid")
        order_ref.update({'paymentStatus': 'pending_confirmation'})
        logger.info(f"Order {order_id} marked as pending_confirmation by {user_id}.")
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
        """
        query = db.collection('Orders').where('paymentStatus', '==', 'unpaid')
        if since is not None:
            query = query.where('orderDate', '>=', since)
        results = []
        for doc in query.stream():
            data = doc.to_dict()
            data['orderId'] = doc.id
            results.append(data)
        return results
