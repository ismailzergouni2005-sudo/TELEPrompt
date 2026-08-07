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

WELCOME_IMAGE_URL = "https://ibb.co/hJ49q7y9" 
WELCOME_STICKER_ID = "CAACAgIAAxkBAAEtNrJqciCsb_KyhKNta-pPJzCKUefSigACVAADQbVWDGq3-McIjQH6PQQ"

AVAILABLE_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

GENERATION_CONFIG = {
    "max_output_tokens": 2500,  # تم رفع الحد الأقصى ليستوعب البرومبت التفصيلي (+250 كلمة)
    "temperature": 0.7,
    "top_p": 0.9,
}

CLOCK_FRAMES = ["🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚", "```python
# ... (نفس قسم استيرادات المكتبات وإعدادات Logging وإعدادات السيرفر دون تغيير)

TEXTS = {
    "ar": {
        "choose_main_mode": "🎯 **اختر الخدمة المطلوبة للصورة:**",
        "btn_extract_prompt": "📝 استخراج البرومبت (Prompt)",
        "btn_upscale_image": "🚀 تحسين الجودة والحدة (Free HD Upscale)",
        "choose_prompt_lang": "🌐 **الخطوة 1/3:** اختر لغة البرومبت المطلوب:",
        "choose_detail": "⚙️ **الخطوة 2/3:** اختر مستوى تفصيل البرومبت والطول المطلوب:",
        "btn_short": "⚡ قصير (100-120 كلمة)",
        "btn_medium": "⚖️ متوسط (120-170 كلمة)",
        "btn_detailed": "🔍 تفصيلي ممتد (+250 كلمة)",
        "choose_format": "📋 **الخطوة 3/3:** اختر تنسيق وشكل عرض البرومبت:",
        "btn_format_paragraph": "📄 فقرة واحدة متصلة (Paragraph)",
        "btn_format_bullet": "📌 نقاط تفصيلية محترفة (Bullet Points)",
        "btn_back": "🔙 رجوع",
        "btn_cancel": "❌ إلغاء",
        "cancelled": "🚫 تم إلغاء العملية. أرسل صورة جديدة في أي وقت.",
        "session_expired": "⚠️ انتهت الجلسة. يرجى إعادة إرسال الصورة.",
        "analyzing": "جاري تحليل عناصر الصورة واستخراج البرومبت بالحجم والشكل المحددين...",
        "enhancing": "جاري رفع دقة الصورة وتحسين تفاصيلها...",
        "success_title": "✅ **تم استخراج البرومبت بنجاح!**\n*(اضغط على النص أدناه لنسخه)*\n\n",
        "success_enhance": "✨ **تم رفع دقة الصورة وتحسين التفاصيل بنجاح!**",
        "btn_retry": "🔄 استخراج بمستوى/لغة/شكل آخر",
        "btn_new_photo": "📸 أرسل صورة جديدة",
        "error_generation": "❌ حدث خطأ أثناء المعالجة: ",
        "quota_error": "⚠️ تم تجاوز كافة حدود الطلبات المتاحة حالياً.",
        "ready_for_new": "📸 مرحباً بك! يمكنك إرسال صورة جديدة الآن.",
    },
    "en": {
        "choose_main_mode": "🎯 **Choose the service for your image:**",
        "btn_extract_prompt": "📝 Extract Prompt",
        "btn_upscale_image": "🚀 Ultra HD Upscale (Free)",
        "choose_prompt_lang": "🌐 **Step 1/3:** Choose prompt language:",
        "choose_detail": "⚙️ **Step 2/3:** Choose prompt length and detail:",
        "btn_short": "⚡ Short (100-120 words)",
        "btn_medium": "⚖️ Medium (120-170 words)",
        "btn_detailed": "🔍 Detailed Extended (+250 words)",
        "choose_format": "📋 **Step 3/3:** Choose prompt output format:",
        "btn_format_paragraph": "📄 Continuous Paragraph",
        "btn_format_bullet": "📌 Structured Bullet Points",
        "btn_back": "🔙 Back",
        "btn_cancel": "❌ Cancel",
        "cancelled": "🚫 Operation cancelled. Send a new image anytime.",
        "session_expired": "⚠️ Session expired. Please resend the image.",
        "analyzing": "Analyzing image and generating custom format prompt...",
        "enhancing": "Boosting image resolution...",
        "success_title": "✅ **Prompt extracted successfully!**\n*(Tap below to copy)*\n\n",
        "success_enhance": "✨ **Image scaled up & details enhanced!**",
        "btn_retry": "🔄 Extract with other options",
        "btn_new_photo": "📸 Send a new image",
        "error_generation": "❌ An error occurred: ",
        "quota_error": "⚠️ API limit reached for all keys.",
        "ready_for_new": "📸 Ready for a new photo! Send your image.",
    },
}

def t(context: ContextTypes.DEFAULT_TYPE, key: str) -> str:
    ui_lang = context.user_data.get("ui_lang", "ar")
    return TEXTS[ui_lang][key]

# ... (نفس دالتي animate_loading و compute_image_ratio و send_welcome_payload و show_ui_language_menu)

async def show_prompt_language_menu(context, query):
    keyboard = [
        [InlineKeyboardButton("🇩🇿 العربية", callback_data="lang_ar"), InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")],
        [InlineKeyboardButton(t(context, "btn_back"), callback_data="back_to_main_mode"), InlineKeyboardButton(t(context, "btn_cancel"), callback_data="cancel")],
    ]
    await query.edit_message_text(t(context, "choose_prompt_lang"), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_detail_menu(context, query):
    keyboard = [
        [InlineKeyboardButton(t(context, "btn_short"), callback_data="detail_short"), InlineKeyboardButton(t(context, "btn_medium"), callback_data="detail_medium")],
        [InlineKeyboardButton(t(context, "btn_detailed"), callback_data="detail_detailed")],
        [InlineKeyboardButton(t(context, "btn_back"), callback_data="back_to_lang"), InlineKeyboardButton(t(context, "btn_cancel"), callback_data="cancel")],
    ]
    await query.edit_message_text(t(context, "choose_detail"), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def show_format_menu(context, query):
    keyboard = [
        [InlineKeyboardButton(t(context, "btn_format_paragraph"), callback_data="format_paragraph")],
        [InlineKeyboardButton(t(context, "btn_format_bullet"), callback_data="format_bullet")],
        [InlineKeyboardButton(t(context, "btn_back"), callback_data="back_to_detail"), InlineKeyboardButton(t(context, "btn_cancel"), callback_data="cancel")],
    ]
    await query.edit_message_text(t(context, "choose_format"), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# ... (نفس دالتي process_upscale و _run_genai)

def clean_generated_prompt(text: str, fmt: str = "paragraph") -> str:
    if not text:
        return text
    
    # تنظيف العناوين العامة إن وجدت
    lines = text.strip().splitlines()
    kept = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("---"):
            continue
        kept.append(stripped)
    
    if fmt == "paragraph":
        # إذا كان المطلوب فقرة، نجمع الأسطر في فقرة واحدة متصلة
        result = " ".join(kept)
    else:
        # إذا كان المطلوب نقاط، نحافظ على التنسيق والأسطر السطرية
        result = "\n".join(kept)
        
    result = re.sub(r"\*\*(.*?)\*\*", r"\1", result)
    result = re.sub(r"\*(.*?)\*", r"\1", result)
    return result.strip("`>* \n")

async def generate_and_send_prompt(query, context: ContextTypes.DEFAULT_TYPE, chat_id, user):
    selected_lang = context.user_data.get("selected_lang", "en")
    selected_length = context.user_data.get("selected_length", "medium")
    selected_format = context.user_data.get("selected_format", "paragraph")
    selected_ratio = context.user_data.get("selected_ratio")
    photo_bytes = context.user_data.get("photo_bytes")
    photo_message_id = context.user_data.get("photo_message_id")

    if not photo_bytes:
        await query.edit_message_text(t(context, "session_expired"))
        return

    loading_task = asyncio.create_task(animate_loading(query, context, "analyzing"))

    # تحديد قواعد الشكل والتنسيق والتنسيقات النقطية
    if selected_format == "bullet":
        bullet_template_ar = (
            "قم بإنشاء البرومبت على شكل نقاط احترافية مفصلة مستخدماً الرؤوس التالية بالتحديد:\n"
            "SUBJECT:\nPOSE:\nFACE:\nCLOTHING:\nBACKGROUND:\nCOMPOSITION:\nCAMERA:\nLIGHTING:\nCOLOR:\nSTYLE:\nDETAILS:\n"
            "اكتب المحتوى باللغة العربية بأسلوب احترافي جداً ومفصل."
        )
        bullet_template_en = (
            "Create the prompt as highly professional bullet points structured precisely using these headers:\n"
            "SUBJECT:\nPOSE:\nFACE:\nCLOTHING:\nBACKGROUND:\nCOMPOSITION:\nCAMERA:\nLIGHTING:\nCOLOR:\nSTYLE:\nDETAILS:\n"
            "Provide highly descriptive visual details under each category."
        )
        fmt_instruction_ar = bullet_template_ar
        fmt_instruction_en = bullet_template_en
    else:
        fmt_instruction_ar = "اكتب البرومبت كفقرة نصية متصلة واحدة بدون أي نقاط أو تقسيمات."
        fmt_instruction_en = "Write the prompt as a single continuous descriptive paragraph without bullet points or headings."

    # تحديد الشروط الدقيقة للعدد الأدنى والأقصى للكلمات لكل مستوى
    length_rules = {
        ("ar", "short"): f"يجب أن يكون البرومبت قصيراً وموجزاً بحيث يتراوح طوله بدقة بين 100 و 120 كلمة باللغة العربية. {fmt_instruction_ar}",
        ("ar", "medium"): f"يجب أن يكون البرومبت متوسط الطول بحيث يتراوح طوله بدقة بين 120 و 170 كلمة باللغة العربية، مغطياً كل التفاصيل والإضاءة والزاوية. {fmt_instruction_ar}",
        ("ar", "detailed"): f"قم بتحليل كافة عناصر الصورة بعمق واكتب برومبت تفصيلي متمز وممتد جداً بشرط أن يتجاوز طوله 250 كلمة على الأقل باللغة العربية. {fmt_instruction_ar}",
        
        ("en", "short"): f"The prompt MUST strictly be between 100 and 120 words long in English. {fmt_instruction_en}",
        ("en", "medium"): f"The prompt MUST strictly be between 120 and 170 words long in English covering depth, composition, and lighting. {fmt_instruction_en}",
        ("en", "detailed"): f"Analyze every single element in ultra-high detail and write an extended comprehensive prompt that is STRICTLY MORE THAN 250 words long in English. {fmt_instruction_en}",
    }

    instruction = length_rules.get((selected_lang, selected_length), "")
    if selected_ratio:
        instruction += f" --ar {selected_ratio}"

    image = Image.open(io.BytesIO(photo_bytes))

    try:
        generated_prompt = await generate_prompt_with_fallback(instruction, image)
        loading_task.cancel()

        generated_prompt = clean_generated_prompt(generated_prompt, selected_format)
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(context, "btn_retry"), callback_data="back_to_lang")],
            [InlineKeyboardButton(t(context, "btn_new_photo"), callback_data="new_photo_request")]
        ])

        await context.bot.send_message(
            chat_id=chat_id, text=t(context, "success_title") + f"```\n{generated_prompt}\n```",
            parse_mode="Markdown", reply_markup=reply_markup, reply_to_message_id=photo_message_id
        )
        await query.delete_message()
        await notify_channel(user, f"قام باستخراج برومبت ({selected_format} - {selected_length}) 📝", context)
    except Exception as e:
        loading_task.cancel()
        logging.error(f"فشل المعالجة: {e}")
        await query.message.reply_text(t(context, "error_generation") + str(e))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("uilang_"):
        context.user_data["ui_lang"] = data.split("_")[1]
        if context.user_data.get("photo_bytes"):
            await show_main_mode_menu(context, query.edit_message_text)
        else:
            await query.delete_message()
            await send_welcome_payload(update.effective_chat.id, update.effective_user, context)
        return

    if data == "mode_prompt":
        await show_prompt_language_menu(context, query)
        return

    if data == "mode_upscale":
        await process_upscale(query, context, update.effective_chat.id, update.effective_user)
        return

    if data == "back_to_main_mode":
        await show_main_mode_menu(context, query.edit_message_text)
        return

    if data == "new_photo_request":
        context.user_data.clear()
        await context.bot.send_message(chat_id=update.effective_chat.id, text=t(context, "ready_for_new"))
        return

    if data == "cancel":
        context.user_data.pop("photo_bytes", None)
        await query.edit_message_text(t(context, "cancelled"))
        return

    if data == "back_to_lang":
        await show_prompt_language_menu(context, query)
        return

    if data == "back_to_detail":
        await show_detail_menu(context, query)
        return

    if data.startswith("lang_"):
        context.user_data["selected_lang"] = data.split("_")[1]
        await show_detail_menu(context, query)
        return

    if data.startswith("detail_"):
        context.user_data["selected_length"] = data.split("_")[1]
        await show_format_menu(context, query)
        return

    if data.startswith("format_"):
        context.user_data["selected_format"] = data.split("_")[1]
        photo_bytes = context.user_data.get("photo_bytes")
        if not photo_bytes:
            await query.edit_message_text(t(context, "session_expired"))
            return
        context.user_data["selected_ratio"] = compute_image_ratio(photo_bytes)
        await generate_and_send_prompt(query, context, update.effective_chat.id, update.effective_user)
        return

# ... (باقي كود error_handler و main دون تغيير)
