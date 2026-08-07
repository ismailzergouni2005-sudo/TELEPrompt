import os
import io
import re
import math
import time
import logging
import threading
import asyncio
import urllib.request
import numpy as np
from http.server import HTTPServer, BaseHTTPRequestHandler
from google import genai
from google.genai import types
from PIL import Image, ImageEnhance
import cv2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
CHANNEL_ID = os.getenv("CHANNEL_ID")

SELF_URL = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("SELF_URL", "")
KEEP_ALIVE_INTERVAL = int(os.getenv("KEEP_ALIVE_INTERVAL", "600"))

raw_keys = os.getenv("API_KEYS", "")
API_KEYS = [key.strip() for key in raw_keys.split(",") if key.strip()]

WELCOME_IMAGE_URL = "https://ibb.co/hJ49q7y9"
WELCOME_STICKER_ID = "CAACAgIAAxkBAAEtNrJqciCsb_KyhKNta-pPJzCKUefSigACVAADQbVWDGq3-McIjQH6PQQ"

# الاعتماد الأساسي على النموذج السريع المستقر لتجنب خطأ 429
AVAILABLE_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
]

GENERATION_CONFIG = {
    "max_output_tokens": 1800,
    "temperature": 0.7,
    "top_p": 0.9,
}

STANDARD_RATIOS = [
    ("1:1", "1:1  (مربع / Square)"),
    ("16:9", "16:9  (عريض / Widescreen)"),
    ("9:16", "9:16  (عمودي / Portrait)"),
    ("4:3", "4:3  (كلاسيكي / Classic)"),
    ("3:4", "3:4  (عمودي كلاسيكي)"),
    ("21:9", "21:9  (سينمائي / Cinematic)"),
]

CLOCK_FRAMES = ["🕐", "إليك الكود المكتمل نهائياً. تم حل مشكلة التجميد وخطأ `429` عبر إزالة `time.sleep` المباشرة والاعتماد على التبديل السريع الفوري بين النماذج والمفاتيح المتعددة بدون توقف البوت:

```python
import os
import io
import re
import math
import time
import logging
import threading
import asyncio
import urllib.request
import numpy as np
from http.server import HTTPServer, BaseHTTPRequestHandler
from google import genai
from google.genai import types
from PIL import Image, ImageEnhance
import cv2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")
CHANNEL_ID = os.getenv("CHANNEL_ID")

SELF_URL = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("SELF_URL", "")
KEEP_ALIVE_INTERVAL = int(os.getenv("KEEP_ALIVE_INTERVAL", "600"))

raw_keys = os.getenv("API_KEYS", "")
API_KEYS = [key.strip() for key in raw_keys.split(",") if key.strip()]

WELCOME_IMAGE_URL = "[https://ibb.co/hJ49q7y9](https://ibb.co/hJ49q7y9)"
WELCOME_STICKER_ID = "CAACAgIAAxkBAAEtNrJqciCsb_KyhKNta-pPJzCKUefSigACVAADQbVWDGq3-McIjQH6PQQ"

# الترتيب حسب النماذج الأسرع والأعلى تحملاً للطلبات المجانية (Flash قبل Pro)
AVAILABLE_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-pro",
]

GENERATION_CONFIG = {
    "max_output_tokens": 1800,
    "temperature": 0.7,
    "top_p": 0.9,
}

STANDARD_RATIOS = [
    ("1:1", "1:1  (مربع / Square)"),
    ("16:9", "16:9  (عريض / Widescreen)"),
    ("9:16", "9:16  (عمودي / Portrait)"),
    ("4:3", "4:3  (كلاسيكي / Classic)"),
    ("3:4", "3:4  (عمودي كلاسيكي)"),
    ("21:9", "21:9  (سينمائي / Cinematic)"),
]

CLOCK_FRAMES = ["🕐", "
