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
    # 2. 客戶端功能 (Orders)
    # ==========================
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
    # 4. 老闆端功能 (Orders)
    # ==========================
    @staticmethod
    def get_unpaid_orders() -> list:
        """
        未付款推播名單：撈取所有欠款名單，供後續排程推播給老闆。
        """
        orders_ref = db.collection('Orders')
        query = orders_ref.where('paymentStatus', '==', 'unpaid')
        
        results = []
        for doc in query.stream():
            data = doc.to_dict()
            data['orderId'] = doc.id
            results.append(data)
            
        return results
