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

# --- الإضافة الجديدة هنا ---
from skimage.metrics import structural_similarity as ssim
# ---------------------------
