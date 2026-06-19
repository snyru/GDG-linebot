import os
import json
import io
import logging
from functools import lru_cache
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, request, abort, jsonify
from urllib.parse import parse_qs

# LINE SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, 
    FlexSendMessage, PostbackEvent, ImageMessage
)

import firebase_admin
from firebase_admin import credentials, firestore
import cloudinary
import cloudinary.uploader

# ============ 1. 伺服器與第三方服務初始化 ============
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = Flask(__name__)

REQUIRED_ENV_VARS = [
    "LINE_TOKEN",
    "LINE_SECRET",
    "FIREBASE_CREDENTIALS",
    "CLOUDINARY_CLOUD_NAME",
    "CLOUDINARY_API_KEY",
    "CLOUDINARY_API_SECRET",
]
missing_env_vars = [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]
if missing_env_vars:
    logger.warning("Missing environment variables: %s", ", ".join(missing_env_vars))

line_bot_api = LineBotApi(os.getenv("LINE_TOKEN"))
handler = WebhookHandler(os.getenv("LINE_SECRET"))
db = None

firebase_cert = os.getenv("FIREBASE_CREDENTIALS")
if firebase_cert:
    try:
        cred_dict = json.loads(firebase_cert)
        cred = credentials.Certificate(cred_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
    except Exception as e:
        logger.exception("Firebase 初始化失敗: %s", e)
else:
    logger.warning("尚未設定 FIREBASE_CREDENTIALS 環境變數！")

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

# ============ 2. 業務邏輯與資料庫函式 ============
CATEGORIES = {"電子產品", "衣服", "鞋子", "證件", "錢包", "雨傘", "書籍", "其他", "配飾"}
CATEGORY_CODES = {
    "錢包": "01",
    "證件": "02",
    "電子產品": "03",
    "衣服": "04",
    "鞋子": "05",
    "書籍": "06",
    "配飾": "07",
    "其他": "08",
    "雨傘": "10",
}
MAX_USER_TEXT_LENGTH = 500
ADMIN_BIND_CODE = os.getenv("ADMIN_BIND_CODE")
APP_TIMEZONE = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Taipei"))

def is_db_ready():
    return db is not None

def normalize_user_text(text):
    return (text or "").strip()

def is_text_too_long(text):
    return len(text) > MAX_USER_TEXT_LENGTH

def normalize_official_id(value):
    return (value or "").strip().upper()

def get_today_code():
    return datetime.now(APP_TIMEZONE).strftime("%y%m%d")

def parse_found_datetime(value):
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=APP_TIMEZONE) if parsed.tzinfo is None else parsed

def get_found_date_code(value):
    try:
        return parse_found_datetime(value).strftime("%y%m%d")
    except (TypeError, ValueError):
        return get_today_code()

def get_category_code(category):
    return CATEGORY_CODES.get(category, CATEGORY_CODES["其他"])

def generate_official_id(category, found_at=None):
    if not is_db_ready():
        raise RuntimeError("Firestore is not ready; cannot generate official ID.")

    date_code = get_found_date_code(found_at)
    category_code = get_category_code(category)
    counter_id = f"found_items_{date_code}_{category_code}"
    counter_ref = db.collection("counters").document(counter_id)
    transaction = db.transaction()

    @firestore.transactional
    def increment_counter(transaction, ref):
        snapshot = ref.get(transaction=transaction)
        last_number = snapshot.to_dict().get("last_number", 0) if snapshot.exists else 0
        next_number = last_number + 1
        transaction.set(ref, {
            "date": date_code,
            "category_code": category_code,
            "last_number": next_number,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)
        return next_number

    serial_number = increment_counter(transaction, counter_ref)
    return f"{date_code}-{category_code}-{serial_number:02d}", category_code, serial_number

def get_admin_profile(user_id):
    if not is_db_ready():
        logger.error("Firestore is not ready; cannot get admin profile.")
        return None
    try:
        doc = db.collection("admin_users").document(user_id).get()
        if not doc.exists:
            return None
        profile = doc.to_dict()
        return profile if profile.get("active") is True else None
    except Exception as e:
        logger.exception("讀取管理員資料失敗: %s", e)
        return None

def is_admin(user_id):
    return get_admin_profile(user_id) is not None

def register_admin(user_id, name):
    if not is_db_ready():
        logger.error("Firestore is not ready; cannot register admin.")
        return False
    try:
        db.collection("admin_users").document(user_id).set({
            "name": name or "未命名管理員",
            "role": "staff",
            "active": True,
            "created_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)
        return True
    except Exception as e:
        logger.exception("綁定管理員失敗: %s", e)
        return False

def get_admin_menu_message():
    return TextSendMessage(
        text="軍訓室管理功能：\n1. 輸入「我撿到東西了」登記拾獲物\n2. 在失物列表中使用物品按鈕標記已領回\n\n學生端可使用「查詢遺失物」或「查看所有遺失物」。"
    )

def get_session(user_id):
    if not is_db_ready():
        logger.error("Firestore is not ready; cannot get session.")
        return {}
    try:
        doc_ref = db.collection('sessions').document(user_id)
        doc = doc_ref.get()
        return doc.to_dict() if doc.exists else {}
    except Exception as e:
        logger.exception("讀取 session 失敗: %s", e)
        return {}

def set_session(user_id, data):
    if not is_db_ready():
        logger.error("Firestore is not ready; cannot set session.")
        return False
    try:
        db.collection('sessions').document(user_id).set(data, merge=True)
        return True
    except Exception as e:
        logger.exception("寫入 session 失敗: %s", e)
        return False

def clear_session(user_id):
    if not is_db_ready():
        logger.error("Firestore is not ready; cannot clear session.")
        return False
    try:
        db.collection('sessions').document(user_id).delete()
        return True
    except Exception as e:
        logger.exception("清除 session 失敗: %s", e)
        return False

# ============ 3. Flex Message 生成 ============
@lru_cache(maxsize=16)
def load_flex_content(filename):
    file_path = os.path.join(os.path.dirname(__file__), filename)
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_flex_message(filename, alt_text):
    contents = load_flex_content(filename)
    return FlexSendMessage(alt_text=alt_text, contents=contents)

def get_found_datetime_picker():
    now = datetime.now(APP_TIMEZONE).replace(second=0, microsecond=0)
    current_value = now.strftime("%Y-%m-%dT%H:%M")
    contents = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "選擇拾獲日期與時間", "weight": "bold", "size": "lg"},
                {"type": "text", "text": "請選擇物品實際被拾獲的時間。", "color": "#666666", "size": "sm", "wrap": True, "margin": "md"},
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "action": {
                        "type": "datetimepicker",
                        "label": "選擇日期與時間",
                        "data": "action=set_found_datetime",
                        "mode": "datetime",
                        "initial": current_value,
                        "max": current_value,
                    },
                }
            ],
        },
    }
    return FlexSendMessage(alt_text="請選擇拾獲日期與時間", contents=contents)

def get_search_menu():
    contents = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "查詢遺失物", "weight": "bold", "size": "xl", "align": "center"},
                {"type": "text", "text": "請選擇查詢方式", "color": "#666666", "size": "sm", "align": "center", "margin": "md"},
                {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "margin": "lg",
                    "contents": [
                        {"type": "button", "style": "secondary", "action": {"type": "message", "label": "依物品種類查詢", "text": "依物品種類查詢"}},
                        {"type": "button", "style": "secondary", "action": {"type": "message", "label": "依拾獲地點查詢", "text": "依拾獲地點查詢"}},
                        {"type": "button", "style": "secondary", "action": {"type": "message", "label": "依拾獲時間查詢", "text": "依拾獲時間查詢"}},
                        {"type": "button", "style": "secondary", "action": {"type": "message", "label": "關鍵字搜尋", "text": "關鍵字搜尋"}},
                    ],
                },
            ],
        },
    }
    return FlexSendMessage(alt_text="請選擇遺失物查詢方式", contents=contents)

def get_time_search_menu():
    contents = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "依拾獲時間查詢", "weight": "bold", "size": "lg", "align": "center"},
                {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "margin": "lg",
                    "contents": [
                        {"type": "button", "style": "secondary", "action": {"type": "message", "label": "今天", "text": "查詢今天"}},
                        {"type": "button", "style": "secondary", "action": {"type": "message", "label": "最近 7 天", "text": "查詢最近7天"}},
                        {"type": "button", "style": "secondary", "action": {"type": "message", "label": "最近 30 天", "text": "查詢最近30天"}},
                    ],
                },
            ],
        },
    }
    return FlexSendMessage(alt_text="請選擇查詢時間範圍", contents=contents)

def get_category_menu(title="我撿到的種類"):
    flex_content = {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box", "layout": "vertical",
            "contents": [
                {"type": "text", "text": title, "weight": "bold", "size": "xl", "align": "center", "margin": "md"},
                {"type": "text", "text": "請選擇你的狀況", "size": "md", "color": "#888888", "align": "center", "margin": "md"},
                {"type": "box", "layout": "vertical", "spacing": "md", "margin": "xl",
                 "contents": [
                     {"type": "box", "layout": "horizontal", "spacing": "md", "contents": [
                         {"type": "button", "style": "secondary", "action": {"type": "message", "label": "電子產品", "text": "電子產品"}},
                         {"type": "button", "style": "secondary", "action": {"type": "message", "label": "衣服", "text": "衣服"}}
                     ]},
                     {"type": "box", "layout": "horizontal", "spacing": "md", "contents": [
                         {"type": "button", "style": "secondary", "action": {"type": "message", "label": "鞋子", "text": "鞋子"}},
                         {"type": "button", "style": "secondary", "action": {"type": "message", "label": "證件", "text": "證件"}}
                     ]},
                     {"type": "box", "layout": "horizontal", "spacing": "md", "contents": [
                         {"type": "button", "style": "secondary", "action": {"type": "message", "label": "錢包", "text": "錢包"}},
                         {"type": "button", "style": "secondary", "action": {"type": "message", "label": "雨傘", "text": "雨傘"}}
                     ]},
                     {"type": "box", "layout": "horizontal", "spacing": "md", "contents": [
                         {"type": "button", "style": "secondary", "action": {"type": "message", "label": "書籍", "text": "書籍"}},
                         {"type": "button", "style": "secondary", "action": {"type": "message", "label": "其他", "text": "其他"}}
                     ]},
                     {"type": "box", "layout": "horizontal",
                      "contents": [
                          {"type": "button", "style": "secondary", "action": {"type": "message", "label": "配飾 (耳環、項鍊、手鏈)", "text": "配飾"}}
                      ]}
                 ]}
            ]
        }
    }
    return FlexSendMessage(alt_text=f"請選擇{title}", contents=flex_content)

def generate_carousel_flex(items_list, alt_text="失物列表", show_claim_button=True):
    bubbles = []
    for item in items_list[:10]:
        official_id = item.get("official_id") or item.get("doc_id", "")
        bubble = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": f"編號：{official_id}" if official_id else "編號：未建立", "weight": "bold", "size": "sm", "color": "#3366CC"},
                    {"type": "text", "text": item.get('category', '未知分類'), "weight": "bold", "size": "xl", "color": "#1DB446"},
                    {"type": "text", "text": f"特徵：{item.get('description', '無')}", "wrap": True, "margin": "md", "size": "sm"},
                    {"type": "text", "text": f"掉落點：{item.get('location', '')} {item.get('detailed_location', '')}", "wrap": True, "size": "xs", "color": "#aaaaaa"}
                ]
            }
        }
        
        # 【修改點：圖片改為 1:1 比例，並設定 fit 模式不裁切，加上淺灰背景】
        if item.get("photo_url"):
            bubble["hero"] = {
                "type": "image",
                "url": item["photo_url"],
                "size": "full",
                "aspectRatio": "1:1",
                "aspectMode": "fit",
                "backgroundColor": "#f4f4f4"
            }
        
        if show_claim_button and "doc_id" in item:
            bubble["footer"] = {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#FF6B6E",
                        "action": {
                            "type": "postback",
                            "label": "✋ 這是我的！",
                            "data": f"action=claim_item&item_id={item['doc_id']}",
                            "displayText": "我想領回這個物品"
                        }
                    }
                ]
            }
        bubbles.append(bubble)

    if not bubbles:
        return TextSendMessage(text="目前沒有找到符合的物品喔！")

    carousel = {
        "type": "carousel",
        "contents": bubbles
    }
    return FlexSendMessage(alt_text=alt_text, contents=carousel)

def search_open_items(category=None, location=None, keyword=None, since=None, limit=10):
    docs = db.collection('items').where('type', '==', 'found').where('status', '==', 'open').limit(100).stream()
    items = [{"doc_id": doc.id, **doc.to_dict()} for doc in docs]

    if category:
        items = [item for item in items if item.get("category") == category]
    if location:
        items = [item for item in items if item.get("location") == location]
    if keyword:
        normalized_keyword = keyword.casefold()
        items = [
            item for item in items
            if normalized_keyword in " ".join(str(item.get(field, "")) for field in (
                "official_id", "category", "description", "location"
            )).casefold()
        ]
    if since:
        items = [
            item for item in items
            if isinstance(item.get("found_at"), datetime) and item["found_at"] >= since
        ]

    def sort_timestamp(item):
        value = item.get("found_at") or item.get("timestamp")
        return value.timestamp() if isinstance(value, datetime) else 0

    items.sort(key=sort_timestamp, reverse=True)
    return items[:limit]

def reply_search_results(reply_token, items, alt_text="遺失物查詢結果"):
    if not items:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="目前沒有找到符合條件的未領回物品。"))
        return
    line_bot_api.reply_message(reply_token, generate_carousel_flex(items, alt_text, show_claim_button=False))

# 寫入資料庫的共用函式 (確保如果有沒填到的資料，會補上空字串而不是 None)
def save_item_to_db(user_id, session):
    item_type = session.get("type")
    category = session.get("category", "未知分類")
    found_at_value = session.get("found_at")
    admin_profile = get_admin_profile(user_id) if item_type == "found" else None
    final_data = {
        "userId": user_id,
        "type": item_type,
        "category": category,
        "description": session.get("description", "無"),
        "location": session.get("location", "未知"),
        "photo_url": session.get("photo_url", ""),
        "status": "open",
        "timestamp": firestore.SERVER_TIMESTAMP
    }
    if found_at_value:
        final_data["found_at"] = parse_found_datetime(found_at_value)
    if admin_profile:
        final_data.update({
            "created_by_user_id": user_id,
            "created_by_name": admin_profile.get("name", "未命名管理員"),
        })
    if not is_db_ready():
        logger.error("Firestore is not ready; item was not saved.")
        return final_data, False
    try:
        if item_type == "found":
            official_id, category_code, serial_number = generate_official_id(category, found_at_value)
            final_data.update({
                "official_id": official_id,
                "category_code": category_code,
                "serial_number": serial_number,
            })
            db.collection('items').document(official_id).set(final_data)
        else:
            db.collection('items').add(final_data)
        return final_data, True
    except Exception as e:
        logger.exception("寫入 items 失敗: %s", e)
        return final_data, False

def build_saved_item_messages(final_data):
    official_id = final_data.get("official_id")
    official_id_line = f"🧾 官方編號：{official_id}\n" if official_id else ""
    found_at = final_data.get("found_at")
    found_at_line = f"🕒 拾獲時間：{found_at.astimezone(APP_TIMEZONE).strftime('%Y/%m/%d %H:%M')}\n" if found_at else ""
    return [
        TextSendMessage(text=f"✅ 拾獲物登記成功！\n{official_id_line}{found_at_line}📌 分類：{final_data['category']}\n📍 拾獲地點：{final_data['location']}\n👤 登記人員：{final_data.get('created_by_name', '未命名管理員')}")
    ]

# ============ 4. 訊息與事件處理邏輯 ============
def handle_message_logic(user_id, text, reply_token):
    text = normalize_user_text(text)
    if not text:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="請輸入文字，或點選選單按鈕開始操作喔！"))
        return
    if is_text_too_long(text):
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"內容有點太長了，請控制在 {MAX_USER_TEXT_LENGTH} 字以內喔！"))
        return

    if text in {"我的ID", "我的 LINE ID", "我的LineID"}:
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"你的 LINE user ID 是：\n{user_id}"))
        return

    if text.startswith("綁定管理員"):
        if not is_db_ready():
            line_bot_api.reply_message(reply_token, TextSendMessage(text="資料庫暫時無法連線，請稍後再試。"))
            return
        if not ADMIN_BIND_CODE:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="尚未設定管理員綁定碼，請先在部署環境設定 ADMIN_BIND_CODE。"))
            return

        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="請輸入：綁定管理員 管理碼 姓名\n例如：綁定管理員 123456 王教官"))
            return

        code = parts[1]
        name = parts[2] if len(parts) >= 3 else ""
        if code != ADMIN_BIND_CODE:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="管理碼不正確，無法綁定。"))
            return

        if register_admin(user_id, name):
            line_bot_api.reply_message(reply_token, TextSendMessage(text="管理員綁定成功。之後這個 LINE 帳號可以使用軍訓室管理功能。"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="管理員綁定失敗，請稍後再試。"))
        return

    if text == "管理員狀態":
        profile = get_admin_profile(user_id)
        if profile:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"你目前是管理員。\n名稱：{profile.get('name', '未命名管理員')}\n角色：{profile.get('role', 'staff')}"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="你目前不是管理員。"))
        return

    if text == "軍訓室管理":
        if not is_admin(user_id):
            line_bot_api.reply_message(reply_token, TextSendMessage(text="你沒有管理權限。若你是軍訓室人員，請先完成管理員綁定。"))
            return
        line_bot_api.reply_message(reply_token, get_admin_menu_message())
        return

    if text in {"取消", "重新開始"}:
        clear_session(user_id)
        line_bot_api.reply_message(reply_token, TextSendMessage(text="已取消目前流程。請從圖文選單重新開始。"))
        return

    session = get_session(user_id)
    step = session.get("step")

    if text == "查詢遺失物":
        if not is_db_ready():
            line_bot_api.reply_message(reply_token, TextSendMessage(text="資料庫暫時無法連線，請稍後再試。"))
            return
        clear_session(user_id)
        set_session(user_id, {"type": "search", "step": "wait_search_method"})
        line_bot_api.reply_message(reply_token, get_search_menu())
        return

    if text in {"查看所有遺失物", "查看所有失物"}:
        if not is_db_ready():
            line_bot_api.reply_message(reply_token, TextSendMessage(text="資料庫暫時無法連線，請稍後再試。"))
            return
        try:
            reply_search_results(reply_token, search_open_items(), "所有未領回遺失物")
        except Exception as e:
            logger.exception("讀取失物列表失敗: %s", e)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="讀取失敗，請稍後再試。"))
        return

    if text in {"校園失物招領處", "校園失物招領處瀏覽"}:
        line_bot_api.reply_message(reply_token, get_flex_message('contact_places.json', '校園失物招領處'))
        return

    if text == "依物品種類查詢":
        clear_session(user_id)
        set_session(user_id, {"type": "search", "step": "wait_search_category"})
        line_bot_api.reply_message(reply_token, get_category_menu("要查詢的物品種類"))
        return

    if text == "依拾獲地點查詢":
        clear_session(user_id)
        set_session(user_id, {"type": "search", "step": "wait_location_button"})
        line_bot_api.reply_message(reply_token, get_flex_message('find_place.json', '請選擇拾獲地點'))
        return

    if text == "依拾獲時間查詢":
        clear_session(user_id)
        set_session(user_id, {"type": "search", "step": "wait_search_time"})
        line_bot_api.reply_message(reply_token, get_time_search_menu())
        return

    if text == "關鍵字搜尋":
        clear_session(user_id)
        set_session(user_id, {"type": "search", "step": "wait_search_keyword"})
        line_bot_api.reply_message(reply_token, TextSendMessage(text="請輸入物品關鍵字，例如：黑色錢包、AirPods、學生證。"))
        return

    # [防呆重點] 開始新流程時，強制清除舊的記憶，避免交錯！
    if text == "我撿到東西了":
        if not is_db_ready():
            line_bot_api.reply_message(reply_token, TextSendMessage(text="資料庫暫時無法連線，請稍後再試。"))
            return
        if not is_admin(user_id):
            line_bot_api.reply_message(reply_token, TextSendMessage(text="這個功能目前只開放軍訓室管理員使用。若你撿到物品，請交到軍訓室登記。"))
            return
        clear_session(user_id) # 強制清空舊記憶
        set_session(user_id, {"type": "found", "step": "wait_found_datetime"})
        line_bot_api.reply_message(reply_token, get_found_datetime_picker())
        return
        
    elif text == "我在找東西":
        if not is_db_ready():
            line_bot_api.reply_message(reply_token, TextSendMessage(text="資料庫暫時無法連線，請稍後再試。"))
            return
        clear_session(user_id) # 強制清空舊記憶
        set_session(user_id, {"type": "lost", "step": "wait_category"})
        line_bot_api.reply_message(reply_token, get_category_menu("我在找的東西的種類"))
        return
    
    # 2. 處理步驟流程 (防呆版)
    if step == "wait_search_method":
        line_bot_api.reply_message(reply_token, TextSendMessage(text="請點擊上方按鈕選擇查詢方式，或輸入「取消」。"))

    elif step == "wait_search_category":
        if text not in CATEGORIES:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="請從種類選單中選擇一個分類。"))
            return
        try:
            items = search_open_items(category=text)
            clear_session(user_id)
            reply_search_results(reply_token, items, f"{text}查詢結果")
        except Exception as e:
            logger.exception("依種類查詢失敗: %s", e)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="查詢失敗，請稍後再試。"))

    elif step == "wait_search_keyword":
        try:
            items = search_open_items(keyword=text)
            clear_session(user_id)
            reply_search_results(reply_token, items, "關鍵字查詢結果")
        except Exception as e:
            logger.exception("關鍵字查詢失敗: %s", e)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="查詢失敗，請稍後再試。"))

    elif step == "wait_search_custom_location":
        try:
            items = search_open_items(location=text)
            clear_session(user_id)
            reply_search_results(reply_token, items, f"{text}查詢結果")
        except Exception as e:
            logger.exception("依地點查詢失敗: %s", e)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="查詢失敗，請稍後再試。"))

    elif step == "wait_search_time":
        day_ranges = {"查詢今天": 1, "查詢最近7天": 7, "查詢最近30天": 30}
        if text not in day_ranges:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="請從上方選單選擇查詢時間範圍。"))
            return
        try:
            now = datetime.now(APP_TIMEZONE)
            since = now.replace(hour=0, minute=0, second=0, microsecond=0) if text == "查詢今天" else now - timedelta(days=day_ranges[text])
            items = search_open_items(since=since)
            clear_session(user_id)
            reply_search_results(reply_token, items, f"{text}結果")
        except Exception as e:
            logger.exception("依時間查詢失敗: %s", e)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="查詢失敗，請稍後再試。"))

    elif step == "wait_found_datetime":
        line_bot_api.reply_message(reply_token, TextSendMessage(text="請點擊上方按鈕選擇拾獲日期與時間，或輸入「取消」。"))

    elif step == "wait_category":
        if text in CATEGORIES:
            session["category"] = text
            if session.get("type") == "found":
                session["step"] = "wait_description"
                set_session(user_id, session)
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"已選擇：{text}\n請輸入物品的詳細描述："))
            else:
                session["step"] = "wait_location_button"
                set_session(user_id, session)
                line_bot_api.reply_message(reply_token, get_flex_message('lost_place.json', '請選擇地點'))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ 請從上方的選單點擊選擇一個分類喔！"))
            
    elif step == "wait_description":
        session["description"] = text
        session["step"] = "wait_photo"
        set_session(user_id, session)
        line_bot_api.reply_message(reply_token, get_flex_message('photo.json', '請上傳物品照片'))

    elif step == "wait_photo":
        line_bot_api.reply_message(reply_token, TextSendMessage(text="請直接上傳物品照片；照片為必填。若要中止，請輸入「取消」。"))
        
    elif step == "wait_location_button":
        line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ 請點擊上方的按鈕選擇地點喔！"))

    elif step == "wait_custom_location":
        session["location"] = text
        session["step"] = "wait_category"
        set_session(user_id, session)
        line_bot_api.reply_message(reply_token, get_category_menu("拾獲物品種類"))

    else:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="請從圖文選單選擇「查詢遺失物」、「查看所有遺失物」或「校園失物招領處」。"))

# 處理按鈕回傳 (防呆版)
def handle_postback_logic(user_id, data, reply_token, postback_params=None):
    params = parse_qs(data)
    action = params.get('action', [''])[0]
    session = get_session(user_id)
    postback_params = postback_params or {}

    if not is_db_ready():
        line_bot_api.reply_message(reply_token, TextSendMessage(text="資料庫暫時無法連線，請稍後再試。"))
        return
    
    if action == "set_found_datetime":
        if not is_admin(user_id) or session.get("step") != "wait_found_datetime":
            line_bot_api.reply_message(reply_token, TextSendMessage(text="操作順序有誤，請重新開始登記。"))
            return

        found_at = postback_params.get("datetime")
        if not found_at:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="日期時間資料有誤，請重新選擇。"))
            return

        session["found_at"] = found_at
        session["step"] = "wait_location_button"
        set_session(user_id, session)
        line_bot_api.reply_message(reply_token, get_flex_message('find_place.json', '請選擇拾獲地點'))

    elif action == "set_location":
        # [防呆重點] 如果使用者按了以前的舊按鈕，但現在根本不是選地點的步驟，直接擋下來！
        if not session or session.get("step") != "wait_location_button":
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ 操作順序有誤！請重新輸入「選單」開啟新流程喔！"))
            return
            
        loc = params.get('loc', [''])[0]
        if not loc:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="地點資料有誤，請重新點選一次。"))
            return
        if session.get("type") == "search":
            if loc == "其他":
                session["step"] = "wait_search_custom_location"
                set_session(user_id, session)
                line_bot_api.reply_message(reply_token, TextSendMessage(text="請輸入要查詢的拾獲地點。"))
                return
            try:
                items = search_open_items(location=loc)
                clear_session(user_id)
                reply_search_results(reply_token, items, f"{loc}查詢結果")
            except Exception as e:
                logger.exception("依地點查詢失敗: %s", e)
                line_bot_api.reply_message(reply_token, TextSendMessage(text="查詢失敗，請稍後再試。"))
        elif session.get("type") == "lost":
            session["location"] = loc
            session["detailed_location"] = "" # 直接給空字串
            final_data, saved = save_item_to_db(user_id, session)
            if not saved:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="登記時發生錯誤，資料尚未保存。請稍後再試一次。"))
                return
            clear_session(user_id) # 結案清空記憶
            
            messages = []
            
            try:
                match_docs = db.collection('items').where('type', '==', 'found').where('category', '==', final_data['category']).where('status', '==', 'open').limit(5).stream()
                matches = [{"doc_id": doc.id, **doc.to_dict()} for doc in match_docs]
                
                if matches:
                    messages.append(TextSendMessage(text="💡 系統自動為您比對出以下可能的結果："))
                    messages.append(generate_carousel_flex(matches, "系統配對結果"))
                else:
                    messages.append(TextSendMessage(text="系統目前尚未配對到符合的物品。您可以直接聯繫下方學校單位詢問，或隨時回來查看喔！👇"))
                    messages.append(get_flex_message('contact_places.json', '聯絡據點'))
            except Exception as e:
                logger.exception("自動配對失敗: %s", e)
            if messages:
                line_bot_api.reply_message(reply_token, messages)
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="搜尋時發生錯誤，請稍後再試。"))
        elif loc == "其他":
            session["step"] = "wait_custom_location"
            set_session(user_id, session)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="請輸入實際拾獲地點。"))
        else:
            session["location"] = loc
            session["step"] = "wait_category"
            set_session(user_id, session)
            line_bot_api.reply_message(reply_token, get_category_menu("拾獲物品種類"))
        
    elif action == "claim_item":
        if not is_admin(user_id):
            line_bot_api.reply_message(reply_token, TextSendMessage(text="此功能只開放軍訓室管理員使用。請至軍訓室確認領取。"))
            return

        item_id = normalize_official_id(params.get('item_id', [''])[0])
        if not item_id:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="物品資料有誤，請重新選擇一次。"))
            return
        try:
            item_ref = db.collection('items').document(item_id)
            item_doc = item_ref.get()
            if not item_doc.exists:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="找不到這筆物品資料，可能已被移除。"))
                return

            item = item_doc.to_dict()
            if item.get("status") != "open":
                line_bot_api.reply_message(reply_token, TextSendMessage(text="這個物品目前已不是待領取狀態囉。"))
                return

            item_ref.update({
                'status': 'closed',
                'claimed_by': user_id,
                'claimed_at': firestore.SERVER_TIMESTAMP
            })
            line_bot_api.reply_message(reply_token, TextSendMessage(text="🎉 太好了！已將此物品標記為「已尋回」，它不會再顯示於列表中囉。\n\n⚠️ 請依循校方或相關單位的規定前往領取/確認喔！"))
        except Exception as e:
            logger.exception("標記領回失敗: %s", e)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="Oops, 標記失敗，請稍後再試。"))

def handle_image_message_logic(user_id, message_id, reply_token):
    session = get_session(user_id)
    if session.get("step") == "wait_photo":
        if session.get("type") == "found" and not is_admin(user_id):
            clear_session(user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="你的管理權限已失效，請重新確認管理員身分。"))
            return

        line_bot_api.reply_message(reply_token, TextSendMessage(text="照片上傳中，請稍候..."))
        try:
            content = line_bot_api.get_message_content(message_id)
            image_url = cloudinary.uploader.upload(io.BytesIO(b''.join(content.iter_content()))).get("secure_url")
            if not image_url:
                line_bot_api.push_message(user_id, TextSendMessage(text="照片上傳失敗，請稍後再試。"))
                return
            session["photo_url"] = image_url

            final_data, saved = save_item_to_db(user_id, session)
            if not saved:
                line_bot_api.push_message(user_id, TextSendMessage(text="登記時發生錯誤，資料尚未保存。請稍後重新上傳照片。"))
                return

            clear_session(user_id)
            line_bot_api.push_message(user_id, build_saved_item_messages(final_data))
        except Exception as e:
            logger.exception("照片上傳失敗: %s", e)
            line_bot_api.push_message(user_id, TextSendMessage(text="照片上傳失敗，請稍後再試。"))
    else:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="目前不需要傳送照片喔！"))

# ============ 5. Flask Webhook 入口 ============
@app.route("/", methods=['GET'])
def index():
    return "Bot is running!"

@app.route("/health", methods=['GET'])
def health():
    return jsonify({
        "status": "ok" if is_db_ready() else "degraded",
        "firebase_ready": is_db_ready(),
        "missing_env_vars": missing_env_vars,
        "admin_bind_code_configured": bool(ADMIN_BIND_CODE),
        "official_id_format": "YYMMDD-CC-NN",
        "category_codes": CATEGORY_CODES,
    }), 200 if is_db_ready() else 503

@app.route("/callback", methods=['POST'])
def callback():
    try:
        signature = request.headers.get('X-Line-Signature')
        if not signature:
            abort(400)
        handler.handle(request.get_data(as_text=True), signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    handle_message_logic(event.source.user_id, event.message.text, event.reply_token)

@handler.add(PostbackEvent)
def handle_postback(event):
    handle_postback_logic(event.source.user_id, event.postback.data, event.reply_token, event.postback.params)

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    handle_image_message_logic(event.source.user_id, event.message.id, event.reply_token)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
