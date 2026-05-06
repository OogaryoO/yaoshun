import os
import logging
from dotenv import load_dotenv

# 載入本地端的 .env 檔案。
# 若部署到 Render，Render 會優先使用其後台設定的環境變數。
load_dotenv()

class Config:
    # LINE Bot 相關設定
    LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN')
    LINE_CHANNEL_SECRET = os.environ.get('LINE_CHANNEL_SECRET')

    # Google / Firebase 憑證 (以 JSON 字串格式儲存)
    GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')
    
    # 老闆作帳用的 Google 試算表 ID
    GOOGLE_SHEET_ID = os.environ.get('GOOGLE_SHEET_ID')

    # 老闆的 LINE UID (初期測試與權限判斷用)
    BOSS_LINE_ID = os.environ.get('BOSS_LINE_ID')

    @classmethod
    def validate(cls):
        """
        驗證必填的環境變數是否都已設定。
        建議在 app.py 啟動時呼叫此方法，確保金鑰齊全。
        """
        missing_keys = []
        if not cls.LINE_CHANNEL_ACCESS_TOKEN: missing_keys.append('LINE_CHANNEL_ACCESS_TOKEN')
        if not cls.LINE_CHANNEL_SECRET: missing_keys.append('LINE_CHANNEL_SECRET')
        if not cls.GOOGLE_CREDENTIALS_JSON: missing_keys.append('GOOGLE_CREDENTIALS_JSON')
        
        if missing_keys:
            error_msg = f"系統啟動失敗！缺少必要的環境變數: {', '.join(missing_keys)}"
            logging.error(error_msg)
            raise ValueError(error_msg)

# 執行驗證，確保 import config 時就能抓出錯誤
Config.validate()
