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

WELCOME_IMAGE_URL = "https://files.catbox.moe/phjs9e.png" 
WELCOME_STICKER_ID = "CAACAgIAAxkBAAEtNrJqciCsb_KyhKNta-pPJzCKUefSigACVAADQbVWDGq3-McIjQH6PQQ"

AVAILABLE_MODELS = [
    "gemini-3.6-flash",        # مجاني ضمن حدود، جودة عالية
    "gemini-3.5-flash-lite",   # مجاني، أسرع وأخف للاستخدام المكثف
]
GENERATION_CONFIG = {
    "max_output_tokens": 2500,
    "temperature": 0.7,
    "top_p": 0.9,
}

CLOCK_FRAMES = ["🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚", "🕛"]

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK - Bot is Alive")

    def log_message(self, format, *args):
        pass

def start_health_server():
    port = int(os.getenv("PORT", 10000))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logging.info(f"✅ Health check server listening on 0.0.0.0:{port}")
        server.serve_forever()
    except Exception as e:
        logging.error(f"❌ فشل تشغيل خادم فحص الصحة: {e}")

def keep_alive_ping():
    if not SELF_URL:
        return

    ping_url = SELF_URL.rstrip("/") + "/"
    while True:
        time.sleep(KEEP_ALIVE_INTERVAL)
        try:
            with urllib.request.urlopen(ping_url, timeout=15) as response:
                logging.info(f"✅ Self-ping ناجح ({response.status})")
        except Exception as e:
            logging.warning(f"⚠️ فشل self-ping: {e}")

async def notify_channel(user, action: str, context: ContextTypes.DEFAULT_TYPE):
    target_id = CHANNEL_ID or ADMIN_ID
    if not target_id:
        return

    def clean_md(text):
        if not text:
            return ""
        for char in ['_', '*', '`', '[']:
            text = str(text).replace(char, f"\\{char}")
        return text

    username = f"@{clean_md(user.username)}" if user.username else "لا يوجد"
    first_name = clean_md(user.first_name) or "غير معروف"
    last_name = clean_md(user.last_name) or ""
    full_name = f"{first_name} {last_name}".strip()
    
    channel_message = (
        "🔔 **إشعار جديد في البوت:**\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"📌 **الحدث:** {action}\n"
        f"👤 **الاسم:** {full_name}\n"
        f"🏷️ **اليوزر:** {username}\n"
        f"🆔 **المعرف:** `{user.id}`\n"
        f"🔗 **الرابط:** [{first_name}](tg://user?id={user.id})\n"
    )

    try:
        chat_id_val = int(target_id) if target_id.startswith("-") or target_id.isdigit() else target_id
        await context.bot.send_message(
            chat_id=chat_id_val,
            text=channel_message,
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.warning(f"تعذر إرسال الإشعار للقناة: {e}")

async def log_prompt_to_channel(user, photo_bytes, prompt_text, extra_info, context: ContextTypes.DEFAULT_TYPE):
    target_id = CHANNEL_ID or ADMIN_ID
    if not target_id:
        return

    def clean_md(text):
        if not text:
            return ""
        for char in ['_', '*', '`', '[']:
            text = str(text).replace(char, f"\\{char}")
        return text

    username = f"@{clean_md(user.username)}" if user.username else "لا يوجد"
    first_name = clean_md(user.first_name) or "غير معروف"

    header = (
        "📝 **برومبت مستخرج جديد**\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"👤 **الاسم:** {first_name}\n"
        f"🏷️ **اليوزر:** {username}\n"
        f"🆔 **المعرف:** `{user.id}`\n"
        f"🔗 **الرابط:** [{first_name}](tg://user?id={user.id})\n"
        f"⚙️ **الإعدادات:** {extra_info}\n"
    )
    prompt_block = f"\n📄 **البرومبت:**\n```\n{prompt_text}\n```"

    try:
        chat_id_val = int(target_id) if target_id.startswith("-") or target_id.isdigit() else target_id
        photo_stream = io.BytesIO(photo_bytes)
        photo_stream.name = "source.jpg"

        # كابشن الصورة بتيليجرام محدود بـ 1024 حرف، فلو البرومبت طويل نرسله كرسالة منفصلة بعد الصورة
        if len(header) + len(prompt_block) <= 1024:
            await context.bot.send_photo(
                chat_id=chat_id_val,
                photo=photo_stream,
                caption=header + prompt_block,
                parse_mode="Markdown",
            )
        else:
            await context.bot.send_photo(
                chat_id=chat_id_val,
                photo=photo_stream,
                caption=header,
                parse_mode="Markdown",
            )
            chunk_size = 3500
            for i in range(0, len(prompt_text), chunk_size):
                chunk = prompt_text[i:i + chunk_size]
                await context.bot.send_message(
                    chat_id=chat_id_val,
                    text=f"```\n{chunk}\n```",
                    parse_mode="Markdown",
                )
    except Exception as e:
        logging.warning(f"تعذر إرسال سجل البرومبت للقناة: {e}")

def local_upscale_image(photo_bytes: bytearray) -> io.BytesIO:
    image_np = np.frombuffer(photo_bytes, np.uint8)
    img = cv2.imdecode(image_np, cv2.IMREAD_COLOR)

    height, width = img.shape[:2]
    scale_factor = 2
    scaled_img = cv2.resize(
        img, (width * scale_factor, height * scale_factor), interpolation=cv2.INTER_LANCZOS4
    )

    denoised = cv2.fastNlMeansDenoisingColored(scaled_img, None, 3, 3, 7, 21)

    lab = cv2.cvtColor(denoised, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=1.2, tileGridSize=(8, 8))
    l_clahe = clahe.apply(l_channel)
    l_blended = cv2.addWeighted(l_channel, 0.6, l_clahe, 0.4, 0)
    lab_merged = cv2.merge((l_blended, a_channel, b_channel))
    contrast_boosted = cv2.cvtColor(lab_merged, cv2.COLOR_LAB2BGR)

    gaussian = cv2.GaussianBlur(contrast_boosted, (0, 0), sigmaX=2)
    sharpened = cv2.addWeighted(contrast_boosted, 1.25, gaussian, -0.25, 0)

    pil_img = Image.fromarray(cv2.cvtColor(sharpened, cv2.COLOR_BGR2RGB))
    pil_img = ImageEnhance.Sharpness(pil_img).enhance(1.25)
    pil_img = ImageEnhance.Contrast(pil_img).enhance(1.05)
    pil_img = ImageEnhance.Color(pil_img).enhance(1.02)

    output_stream = io.BytesIO()
    pil_img.save(output_stream, format="JPEG", quality=98)
    output_stream.seek(0)
    return output_stream

def get_welcome_text(user, ui_lang="ar"):
    user_mention = f"[{user.first_name}](tg://user?id={user.id})"
    if ui_lang == "ar":
        return (
            f"✨ أهلاً بك يا {user_mention} في **بوت استخراج البرومبت وتحسين الصور**! ✨\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🛠️ **كيف يعمل هذا البوت؟**\n"
            "1️⃣ **أرسل أي صورة:** سيتعرف البوت عليها تلقائياً.\n"
            "2️⃣ **اختر الخدمة:** استخراج البرومبت بالذكاء الاصطناعي أو تحسين الجودة والحدة مجاناً.\n"
            "3️⃣ **استلم النتيجة:** احصل على البرومبت أو الصورة المحسنة فوراً!\n\n"
            "👇 **ابدأ الآن بإرسال صورتك!**"
        )
    else:
        return (
            f"✨ Welcome {user_mention} to **AI Prompt Extractor & HD Upscaler Bot**! ✨\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🛠️ **How does this bot work?**\n"
            "1️⃣ **Send an Image:** The bot will analyze it automatically.\n"
            "2️⃣ **Choose Action:** Extract AI prompt or Upscale image quality to HD.\n"
            "3️⃣ **Get Results:** Copy your prompt or download your high-res image!\n\n"
            "👇 **Start now by sending your image!**"
        )

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

async def animate_loading(query, context: ContextTypes.DEFAULT_TYPE, text_key: str, interval: float = 0.6):
    base_text = t(context, text_key)
    idx = 0
    try:
        while True:
            frame = CLOCK_FRAMES[idx % len(CLOCK_FRAMES)]
            try:
                await query.edit_message_text(f"{frame} {base_text}")
            except Exception:
                pass
            idx += 1
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass

def compute_image_ratio(photo_bytes) -> str:
    try:
        image = Image.open(io.BytesIO(photo_bytes))
        width, height = image.size
        divisor = math.gcd(width, height) or 1
        return f"{width // divisor}:{height // divisor}"
    except Exception:
        return "1:1"

async def send_welcome_payload(chat_id, user, context):
    ui_lang = context.user_data.get("ui_lang", "ar")
    welcome_msg = get_welcome_text(user, ui_lang)
    try:
        await context.bot.send_photo(chat_id=chat_id, photo=WELCOME_IMAGE_URL, caption=welcome_msg, parse_mode="Markdown")
    except Exception:
        await context.bot.send_message(chat_id=chat_id, text=welcome_msg, parse_mode="Markdown")

    try:
        if WELCOME_STICKER_ID:
            await context.bot.send_sticker(chat_id=chat_id, sticker=WELCOME_STICKER_ID)
    except Exception:
        pass

async def show_ui_language_menu(send_func):
    keyboard = [
        [InlineKeyboardButton("🇩🇿 العربية", callback_data="uilang_ar"), InlineKeyboardButton("🇬🇧 English", callback_data="uilang_en")],
    ]
    await send_func("🌐 اختر لغة واجهة البوت / Choose language:", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_main_mode_menu(context, send_func):
    keyboard = [
        [InlineKeyboardButton(t(context, "btn_extract_prompt"), callback_data="mode_prompt")],
        [InlineKeyboardButton(t(context, "btn_upscale_image"), callback_data="mode_upscale")],
        [InlineKeyboardButton(t(context, "btn_cancel"), callback_data="cancel")],
    ]
    await send_func(t(context, "choose_main_mode"), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await notify_channel(update.effective_user, "قام بتشغيل البوت (/start)", context)
    if "ui_lang" not in context.user_data:
        await show_ui_language_menu(update.message.reply_text)
        return
    await send_welcome_payload(update.effective_chat.id, update.effective_user, context)

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_ui_language_menu(update.message.reply_text)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()
    context.user_data["photo_bytes"] = photo_bytes
    context.user_data["photo_message_id"] = update.message.message_id

    await notify_channel(update.effective_user, "قام بإرسال صورة جديدة 📸", context)
    try:
        await context.bot.set_message_reaction(chat_id=update.effective_chat.id, message_id=update.message.message_id, reaction=[ReactionTypeEmoji(emoji="❤")])
    except Exception:
        pass

    if "ui_lang" not in context.user_data:
        await show_ui_language_menu(update.message.reply_text)
        return

    await show_main_mode_menu(context, update.message.reply_text)

async def process_upscale(query, context: ContextTypes.DEFAULT_TYPE, chat_id, user):
    photo_bytes = context.user_data.get("photo_bytes")
    photo_message_id = context.user_data.get("photo_message_id")

    if not photo_bytes:
        await query.edit_message_text(t(context, "session_expired"))
        return

    loading_task = asyncio.create_task(animate_loading(query, context, "enhancing"))
    try:
        loop = asyncio.get_running_loop()
        enhanced_stream = await loop.run_in_executor(None, local_upscale_image, photo_bytes)
        loading_task.cancel()

        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(t(context, "btn_new_photo"), callback_data="new_photo_request")]])
        await context.bot.send_document(
            chat_id=chat_id, document=enhanced_stream, filename="enhanced_hd_image.jpg",
            caption=t(context, "success_enhance"), parse_mode="Markdown", reply_markup=reply_markup, reply_to_message_id=photo_message_id
        )
        await query.delete_message()
        await notify_channel(user, "قام بزيادة دقة صورة (HD Upscale) 🚀", context)
    except Exception as e:
        loading_task.cancel()
        logging.error(f"خطأ تحسين الصورة: {e}")
        await query.message.reply_text(t(context, "error_generation") + str(e))

def _run_genai(instruction, image, models_order=None, generation_config=None):
    models_to_try = models_order or AVAILABLE_MODELS
    gen_config = generation_config or GENERATION_CONFIG

    if not API_KEYS:
        raise RuntimeError("لا توجد مفاتيح API_KEYS.")

    last_error = None
    for api_key in API_KEYS:
        genai.configure(api_key=api_key)
        for model_name in models_to_try:
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content([instruction, image], generation_config=gen_config, request_options={"timeout": 45})
                if response and response.text:
                    return response.text.strip()
            except Exception as e:
                last_error = e
                continue
    raise last_error or RuntimeError("فشل الاتصال بـ API.")

async def generate_prompt_with_fallback(instruction, image, models_order=None, generation_config=None):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run_genai, instruction, image, models_order, generation_config)

def clean_generated_prompt(text: str, fmt: str = "paragraph") -> str:
    if not text:
        return text
    
    lines = text.strip().splitlines()
    kept = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("---"):
            continue
        kept.append(stripped)
    
    if fmt == "paragraph":
        result = " ".join(kept)
    else:
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

        if selected_ratio:
            generated_prompt = f"{generated_prompt.rstrip()} --ar {selected_ratio}"

        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(context, "btn_retry"), callback_data="back_to_lang")],
            [InlineKeyboardButton(t(context, "btn_new_photo"), callback_data="new_photo_request")]
        ])

        await context.bot.send_message(
            chat_id=chat_id, text=t(context, "success_title") + f"```\n{generated_prompt}\n```",
            parse_mode="Markdown", reply_markup=reply_markup, reply_to_message_id=photo_message_id
        )
        await query.delete_message()

        settings_summary = f"{selected_lang} | {selected_length} | {selected_format}"
        await log_prompt_to_channel(user, photo_bytes, generated_prompt, settings_summary, context)
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

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error("حدث استثناء:", exc_info=context.error)

def main():
    threading.Thread(target=start_health_server, daemon=True).start()
    threading.Thread(target=keep_alive_ping, daemon=True).start()

    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    app = Application.builder().token(TELEGRAM_TOKEN).request(request).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_error_handler(error_handler)

    while True:
        try:
            app.run_polling(drop_pending_updates=True, close_loop=False)
            break
        except Exception as e:
            logging.error(f"إعادة تشغيل البوت بسبب خطأ: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
