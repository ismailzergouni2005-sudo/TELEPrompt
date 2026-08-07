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
from google import generativeai as genai
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

AVAILABLE_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

GENERATION_CONFIG = {
    "max_output_tokens": 2500,
    "temperature": 0.7,
    "top_p":
