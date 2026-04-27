import os
from dotenv import load_dotenv
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    FlexSendMessage, FollowEvent
)
from linebot.exceptions import InvalidSignatureError
import logging

load_dotenv()

line_token = os.getenv('LINE_TOKEN')
line_secret = os.getenv('LINE_SECRET')

if not line_token or not line_secret:
    raise ValueError("LINE_TOKEN 或 LINE_SECRET 未設置")

line_bot_api = LineBotApi(line_token)
handler = WebhookHandler(line_secret)

app = Flask(__name__)
app.logger.setLevel(logging.DEBUG)


def get_main_menu():
    flex_content = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {
                    "type": "text",
                    "text": "🔍 失物招領",
                    "weight": "bold",
                    "size": "xl",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "請選擇你的狀況",
                    "size": "sm",
                    "color": "#888888",
                    "align": "center"
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#4CAF50",
                    "action": {
                        "type": "message",
                        "label": "📦 我撿到東西了",
                        "text": "我撿到東西了"
                    }
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#2196F3",
                    "action": {
                        "type": "message",
                        "label": "🔎 我在找東西",
                        "text": "我在找東西"
                    }
                },
                {
                    "type": "button",
                    "style": "secondary",
                    "action": {
                        "type": "message",
                        "label": "📋 查看所有失物",
                        "text": "查看所有失物"
                    }
                }
            ]
        }
    }
    return FlexSendMessage(alt_text="失物招領選單", contents=flex_content)


@app.route("/", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info(f"Request body: {body}")
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


@handler.add(FollowEvent)
def handle_follow(event):
    line_bot_api.reply_message(
        event.reply_token,
        [
            TextSendMessage(text="歡迎使用失物招領服務！👋"),
            get_main_menu()
        ]
    )


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    app.logger.info(f"收到的訊息: {text}")

    if text in ["選單", "開始", "menu"]:
        line_bot_api.reply_message(event.reply_token, get_main_menu())

    elif text == "我撿到東西了":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="好的！請問撿到的是哪類物品？\n\n請回覆數字：\n1. 電子產品\n2. 衣物配件\n3. 證件錢包\n4. 鑰匙\n5. 其他")
        )

    elif text == "我在找東西":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="沒關係！請問遺失的是哪類物品？\n\n請回覆數字：\n1. 電子產品\n2. 衣物配件\n3. 證件錢包\n4. 鑰匙\n5. 其他")
        )

    elif text == "查看所有失物":
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="（這裡之後會顯示失物清單）")
        )

    else:
        line_bot_api.reply_message(
            event.reply_token,
            [
                TextSendMessage(text="請使用下方選單操作 👇"),
                get_main_menu()
            ]
        )


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)