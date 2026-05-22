import os
from dotenv import load_dotenv
from linebot import LineBotApi
from linebot.models import (
    RichMenu,
    RichMenuArea,
    RichMenuBounds,
    RichMenuSize,
    MessageAction,
)

load_dotenv()

from config import Config

line_bot_api = LineBotApi(Config.LINE_CHANNEL_ACCESS_TOKEN)


def create_rich_menu(name: str, chat_bar_text: str, actions: list[tuple[str, str]], image_path: str) -> str:
    rich_menu = RichMenu(
        size=RichMenuSize(width=2500, height=1686),
        selected=False,
        name=name,
        chat_bar_text=chat_bar_text,
        areas=[
            RichMenuArea(
                bounds=RichMenuBounds(
                    x=(index % 3) * 833,
                    y=(index // 3) * 843,
                    width=833,
                    height=843,
                ),
                action=MessageAction(label=label, text=text),
            )
            for index, (label, text) in enumerate(actions)
        ],
    )

    rich_menu_id = line_bot_api.create_rich_menu(rich_menu)
    print(f"Created rich menu '{name}' with ID: {rich_menu_id}")

    with open(image_path, 'rb') as image_file:
        line_bot_api.set_rich_menu_image(rich_menu_id, 'image/png', image_file)
    print(f"Uploaded image for rich menu '{name}'.")
    return rich_menu_id


if __name__ == '__main__':
    customer_actions = [
        ('查看商品', '查看商品'),
        ('我要下單', '我要下單'),
        ('我的未付款', '我的未付款'),
        ('回報付款', '回報付款'),
        ('聯絡老闆', '聯絡老闆'),
        (' ', ' '),
    ]

    boss_actions = [
        ('查詢客戶', '查詢客戶'),
        ('客戶訂單', '客戶訂單'),
        ('未付款清單', '未付款清單'),
        ('幫客戶下單', '幫客戶下單'),
        ('確認付款', '確認付款'),
        ('送達', '送達'),
    ]

    print('Create or update the customer rich menu using customer_menu.png')
    customer_id = create_rich_menu('customer-rich-menu', '客戶選單', customer_actions, 'customer_menu.png')
    print('Create or update the boss rich menu using boss_menu.png')
    boss_id = create_rich_menu('boss-rich-menu', '老闆選單', boss_actions, 'boss_menu.png')

    print('\nWrite these IDs into your .env:')
    print(f'CUSTOMER_RICH_MENU_ID={customer_id}')
    print(f'BOSS_RICH_MENU_ID={boss_id}')
