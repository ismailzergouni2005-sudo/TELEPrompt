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

# رابط الخدمة نفسها لاستخدامه في الـ self-ping (Render يضبط RENDER_EXTERNAL_URL
# تلقائياً؛ ويمكن أيضاً ضبط SELF_URL يدوياً كخيار احتياطي)
SELF_URL = os.getenv("RENDER_EXTERNAL_URL") or os.getenv("SELF_URL", "")
# كل كم ثانية يتم عمل ping ذاتي (افتراضياً كل 10 دقائق، أقل من مهلة نوم Render وهي 15 دقيقة)
KEEP_ALIVE_INTERVAL = int(os.getenv("KEEP_ALIVE_INTERVAL", "600"))

# قراءة مفاتيح API من بيئة Render بأمان (تفصل بين المفاتيح الفاصلة ",")
raw_keys = os.getenv("API_KEYS", "")
API_KEYS = [key.strip() for key in raw_keys.split(",") if key.strip()]

WELCOME_IMAGE_URL = "https://ibb.co/hJ49q7y9" 
WELCOME_STICKER_ID = "CAACAgIAAxkBAAEtNrJqciCsb_KyhKNta-pPJzCKUefSigACVAADQbVWDGq3-McIjQH6PQQ"

# ملاحظة: نماذج 1.5 و2.0 متوقفة تماماً، ونماذج 2.5 صارت غير متاحة
# للمشاريع/المفاتيح الجديدة (رسالة "no longer available to new users").
# لذلك نستخدم جيل Gemini 3.x المستقر (GA) حالياً.
AVAILABLE_MODELS = [
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
]

GENERATION_CONFIG = {
    "max_output_tokens": 1500,
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

# رموز الساعة المتحركة المستخدمة كمؤشر انتظار بدل الساعة الرملية الثابتة ⏳
CLOCK_FRAMES = ["🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚", "🕛"]

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK - Bot is Alive")

    def do_HEAD(self):
        # بعض أدوات المراقبة (Better Stack، UptimeRobot، إلخ) ترسل HEAD بدل GET
        # للفحص الدوري؛ بدون هذا الميثود يرد BaseHTTPRequestHandler تلقائياً
        # بخطأ 501 Unsupported method فتظهر الخدمة "Down" رغم أنها تعمل فعلياً.
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()

    def log_message(self, format, *args):
        pass

def start_health_server():
    port = int(os.getenv("PORT", 10000))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
        logging.info(f"✅ Health check server listening on 0.0.0.0:{port}")
        server.serve_forever()
    except Exception as e:
        logging.error(f"❌ فشل تشغيل خادم فحص الصحة على البورت {port}: {e}")

def keep_alive_ping():
    """يرسل طلب HTTP دوري لرابط الخدمة نفسها (self-ping) كل KEEP_ALIVE_INTERVAL
    ثانية، لمنع Render (في الخطة المجانية) من تعليق الخدمة (Sleep) بسبب الخمول —
    بدون الحاجة للاعتماد فقط على خدمة خارجية مثل UptimeRobot."""
    if not SELF_URL:
        logging.warning(
            "⚠️ لم يتم العثور على رابط الخدمة (RENDER_EXTERNAL_URL/SELF_URL)؛ "
            "تم تعطيل خاصية إبقاء الخدمة نشطة ذاتياً (self-ping)."
        )
        return

    ping_url = SELF_URL.rstrip("/") + "/"
    logging.info(f"🔁 تفعيل self-ping كل {KEEP_ALIVE_INTERVAL} ثانية على: {ping_url}")

    while True:
        time.sleep(KEEP_ALIVE_INTERVAL)
        try:
            with urllib.request.urlopen(ping_url, timeout=15) as response:
                logging.info(f"✅ Self-ping ناجح ({response.status}) إلى {ping_url}")
        except Exception as e:
            logging.warning(f"⚠️ فشل self-ping إلى {ping_url}: {e}")

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

def local_upscale_image(photo_bytes: bytearray) -> io.BytesIO:
    """رفع دقة الصورة محلياً: تكبير حقيقي عالي الجودة + تنظيف خفيف للضوضاء +
    حدة معتدلة تحافظ على الملامح الأصلية للوجه والتفاصيل دون تشويهها."""
    image_np = np.frombuffer(photo_bytes, np.uint8)
    img = cv2.imdecode(image_np, cv2.IMREAD_COLOR)

    height, width = img.shape[:2]
    scale_factor = 2
    scaled_img = cv2.resize(
        img, (width * scale_factor, height * scale_factor), interpolation=cv2.INTER_LANCZOS4
    )

    # إزالة ضوضاء خفيفة فقط (قيمة منخفضة) حتى لا تُفقد التفاصيل الدقيقة للوجه
    denoised = cv2.fastNlMeansDenoisingColored(scaled_img, None, 3, 3, 7, 21)

    # تباين تكيفي لطيف جداً على قناة الإضاءة فقط، ممزوج جزئياً مع الأصل لتفادي أي مبالغة
    lab = cv2.cvtColor(denoised, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=1.2, tileGridSize=(8, 8))
    l_clahe = clahe.apply(l_channel)
    l_blended = cv2.addWeighted(l_channel, 0.6, l_clahe, 0.4, 0)
    lab_merged = cv2.merge((l_blended, a_channel, b_channel))
    contrast_boosted = cv2.cvtColor(lab_merged, cv2.COLOR_LAB2BGR)

    # حدة معتدلة عبر Unsharp Masking لإبراز الحواف دون خلق هالات أو مظهر مصطنع
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
        "choose_detail": "⚙️ **الخطوة 2/3:** اختر مستوى تفصيل البرومبت:",
        "choose_ratio": "📐 **الخطوة 3/3:** اختر مقاس/نسبة أبعاد الصورة:",
        "choose_standard_ratio": "📐 اختر النسبة القياسية المطلوبة:",
        "btn_short": "⚡ قصير وموجز",
        "btn_medium": "⚖️ متوسط",
        "btn_detailed": "🔍 تفصيلي وفائق الدقة (شامل جداً)",
        "btn_ratio_same": "🖼️ نفس مقاس الصورة المرسلة",
        "btn_ratio_standard": "📏 مقاس عام (اختيار نسبة قياسية)",
        "btn_back": "🔙 رجوع",
        "btn_back_detail": "🔙 رجوع لمستوى التفصيل",
        "btn_cancel": "❌ إلغاء",
        "cancelled": "🚫 تم إلغاء العملية الحالية. أرسل صورة جديدة في أي وقت.",
        "session_expired": "⚠️ انتهت الجلسة. يرجى إعادة إرسال الصورة من جديد.",
        "analyzing": "جاري تحليل عناصر الصورة واستخراج البرومبت...",
        "enhancing": "جاري رفع دقة الصورة وتحسين تفاصيلها بقوة مضاعفة محلياً...",
        "success_title": "✅ **تم استخراج البرومبت بنجاح!**\n*(اضغط على النص أدناه لنسخه فوراً)*\n\n",
        "success_enhance": "✨ **تم رفع دقة الصورة وتحسين وضوح التفاصيل بقوة مضاعفة بنجاح!**",
        "btn_retry": "🔄 استخراج بمستوى/لغة أخرى",
        "btn_new_photo": "📸 أرسل صورة جديدة",
        "error_generation": "❌ حدث خطأ أثناء المعالجة: ",
        "quota_error": "⚠️ تم تجاوز كافة حدود الطلبات المتاحة حالياً. يرجى المحاولة لاحقاً.",
        "ready_for_new": "📸 مرحباً بك مجدداً! يمكنك إرسال صورة جديدة الآن.",
    },
    "en": {
        "choose_main_mode": "🎯 **Choose the service for your image:**",
        "btn_extract_prompt": "📝 Extract Prompt",
        "btn_upscale_image": "🚀 Ultra HD Upscale (Free)",
        "choose_prompt_lang": "🌐 **Step 1/3:** Choose prompt language:",
        "choose_detail": "⚙️ **Step 2/3:** Choose detail level:",
        "choose_ratio": "📐 **Step 3/3:** Choose aspect ratio:",
        "choose_standard_ratio": "📐 Choose standard aspect ratio:",
        "btn_short": "⚡ Short & Concise",
        "btn_medium": "⚖️ Medium",
        "btn_detailed": "🔍 Detailed & Ultra-Precise",
        "btn_ratio_same": "🖼️ Same as sent image",
        "btn_ratio_standard": "📏 Standard ratio",
        "btn_back": "🔙 Back",
        "btn_back_detail": "🔙 Back to detail level",
        "btn_cancel": "❌ Cancel",
        "cancelled": "🚫 Operation cancelled. Send a new image anytime.",
        "session_expired": "⚠️ Session expired. Please resend the image.",
        "analyzing": "Analyzing image and extracting prompt...",
        "enhancing": "Boosting image resolution and sharpening details at maximum strength...",
        "success_title": "✅ **Prompt extracted successfully!**\n*(Tap below to copy)*\n\n",
        "success_enhance": "✨ **Image scaled up & details enhanced at maximum strength!**",
        "btn_retry": "🔄 Extract with other options",
        "btn_new_photo": "📸 Send a new image",
        "error_generation": "❌ An error occurred: ",
        "quota_error": "⚠️ API limit reached for all available keys. Please wait a moment.",
        "ready_for_new": "📸 Ready for a new photo! Send your image.",
    },
}

def t(context: ContextTypes.DEFAULT_TYPE, key: str) -> str:
    ui_lang = context.user_data.get("ui_lang", "ar")
    return TEXTS[ui_lang][key]

async def animate_loading(query, context: ContextTypes.DEFAULT_TYPE, text_key: str, interval: float = 0.6):
    """يعرض ساعة متحركة (تدور بدل عقارب الساعة) كمؤشر انتظار مميز أثناء المعالجة،
    بدلاً من رمز الساعة الرملية الثابت ⏳."""
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
    except Exception as e:
        logging.warning(f"تعذر حساب نسبة أبعاد الصورة: {e}")
        return "1:1"

async def send_welcome_payload(chat_id, user, context):
    ui_lang = context.user_data.get("ui_lang", "ar")
    welcome_msg = get_welcome_text(user, ui_lang)
    
    try:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=WELCOME_IMAGE_URL,
            caption=welcome_msg,
            parse_mode="Markdown"
        )
    except Exception as e:
        await context.bot.send_message(
            chat_id=chat_id, text=welcome_msg, parse_mode="Markdown"
        )
        logging.warning(f"تعذر إرسال صورة الترحيب: {e}")

    try:
        if WELCOME_STICKER_ID:
            await context.bot.send_sticker(chat_id=chat_id, sticker=WELCOME_STICKER_ID)
    except Exception as e:
        logging.warning(f"تعذر إرسال الملصق: {e}")

async def show_ui_language_menu(send_func):
    keyboard = [
        [
            InlineKeyboardButton("🇩🇿 العربية", callback_data="uilang_ar"),
            InlineKeyboardButton("🇬🇧 English", callback_data="uilang_en"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_func(
        "🌐 مرحباً! الرجاء اختيار لغة واجهة البوت:\n🌐 Welcome! Please choose the bot's interface language:",
        reply_markup=reply_markup,
    )

async def show_main_mode_menu(context, send_func):
    keyboard = [
        [InlineKeyboardButton(t(context, "btn_extract_prompt"), callback_data="mode_prompt")],
        [InlineKeyboardButton(t(context, "btn_upscale_image"), callback_data="mode_upscale")],
        [InlineKeyboardButton(t(context, "btn_cancel"), callback_data="cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_func(t(context, "choose_main_mode"), reply_markup=reply_markup, parse_mode="Markdown")

async def show_prompt_language_menu(context, query):
    keyboard = [
        [
            InlineKeyboardButton("🇩🇿 العربية", callback_data="lang_ar"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
        ],
        [
            InlineKeyboardButton(t(context, "btn_back"), callback_data="back_to_main_mode"),
            InlineKeyboardButton(t(context, "btn_cancel"), callback_data="cancel")
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(t(context, "choose_prompt_lang"), reply_markup=reply_markup, parse_mode="Markdown")

async def show_detail_menu(context, query):
    keyboard = [
        [
            InlineKeyboardButton(t(context, "btn_short"), callback_data="detail_short"),
            InlineKeyboardButton(t(context, "btn_medium"), callback_data="detail_medium"),
        ],
        [InlineKeyboardButton(t(context, "btn_detailed"), callback_data="detail_detailed")],
        [
            InlineKeyboardButton(t(context, "btn_back"), callback_data="back_to_lang"),
            InlineKeyboardButton(t(context, "btn_cancel"), callback_data="cancel"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(t(context, "choose_detail"), reply_markup=reply_markup, parse_mode="Markdown")

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
        await context.bot.set_message_reaction(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
            reaction=[ReactionTypeEmoji(emoji="❤")],
        )
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
        try:
            await loading_task
        except asyncio.CancelledError:
            pass

        post_action_keyboard = [
            [InlineKeyboardButton(t(context, "btn_new_photo"), callback_data="new_photo_request")],
        ]
        reply_markup = InlineKeyboardMarkup(post_action_keyboard)

        await context.bot.send_document(
            chat_id=chat_id,
            document=enhanced_stream,
            filename="enhanced_hd_image.jpg",
            caption=t(context, "success_enhance"),
            parse_mode="Markdown",
            reply_markup=reply_markup,
            reply_to_message_id=photo_message_id,
        )
        await query.delete_message()
        
        await notify_channel(user, "قام بزيادة دقة صورة (HD Upscale) 🚀", context)

    except Exception as e:
        loading_task.cancel()
        try:
            await loading_task
        except asyncio.CancelledError:
            pass
        logging.error(f"خطأ أثناء تحسين الصورة: {e}")
        await query.message.reply_text(t(context, "error_generation") + str(e))

def _run_genai(instruction, image, models_order=None, generation_config=None):
    """الدالة الداخلية للتنقل بين المفاتيح والنماذج بأسلوب متزامن متوافق مع run_in_executor"""
    last_error = None
    models_to_try = models_order or AVAILABLE_MODELS
    gen_config = generation_config or GENERATION_CONFIG

    if not API_KEYS:
        raise RuntimeError("لا توجد أي مفاتيح API صالحة في متغير API_KEYS.")

    for api_key in API_KEYS:
        genai.configure(api_key=api_key)
        for model_name in models_to_try:
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(
                    [instruction, image],
                    generation_config=gen_config,
                    request_options={"timeout": 45},
                )
                if response and response.text:
                    return response.text.strip()
            except Exception as e:
                # نسجل كل خطأ على حدة حتى نعرف بالضبط أي نموذج/مفتاح فشل ولماذا
                masked_key = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "***"
                logging.warning(f"فشل النموذج {model_name} بالمفتاح {masked_key}: {e}")
                last_error = e
                continue

    raise last_error or RuntimeError("فشل الاتصال بكل المفاتيح والنماذج.")

async def generate_prompt_with_fallback(instruction, image, models_order=None, generation_config=None):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run_genai, instruction, image, models_order, generation_config)

def clean_generated_prompt(text: str) -> str:
    """شبكة أمان: تزيل أي عناوين/تحليل/تنسيق Markdown قد يفلت من النموذج
    رغم التعليمات، وتُبقي فقرة البرومبت النهائية فقط."""
    if not text:
        return text

    lines = text.strip().splitlines()
    kept = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        # تجاهل عناوين Markdown أو خطوط فاصلة أو نقاط ترقيم أو أسطر تشير لـ "التحليل/البرومبت"
        if stripped.startswith("#") or stripped.startswith("---") or stripped.startswith("==="):
            continue
        if re.match(r"^(\*|-|\d+[\.\)])\s+", stripped) and len(stripped) < 60:
            continue
        if re.match(r"^(البرومبت|Prompt|تحليل|Analysis)\s*[:：]?\s*$", stripped, re.IGNORECASE):
            continue
        kept.append(stripped)

    result = " ".join(kept) if kept else text.strip()

    # إزالة تنسيق Markdown الشائع (عريض/مائل/اقتباس)
    result = re.sub(r"\*\*(.*?)\*\*", r"\1", result)
    result = re.sub(r"\*(.*?)\*", r"\1", result)
    result = result.strip("`>* \n")

    return result.strip()

async def generate_and_send_prompt(query, context: ContextTypes.DEFAULT_TYPE, chat_id, user):
    selected_lang = context.user_data.get("selected_lang", "en")
    selected_length = context.user_data.get("selected_length", "medium")
    selected_ratio = context.user_data.get("selected_ratio")
    photo_bytes = context.user_data.get("photo_bytes")
    photo_message_id = context.user_data.get("photo_message_id")

    if not photo_bytes:
        await query.edit_message_text(t(context, "session_expired"))
        return

    loading_task = asyncio.create_task(animate_loading(query, context, "analyzing"))

    output_rules_ar = (
        "مهم جداً: أعطني البرومبت فقط، كفقرة نصية واحدة متصلة تبدأ مباشرة بوصف الصورة، "
        "بدون أي مقدمات أو عناوين أو تحليل منفصل أو نقاط مرقمة أو عناصر Markdown (لا تستخدم ** ولا # ولا -)، "
        "وبدون أي كلام جانبي قبل أو بعد الوصف. "
        "مهم جداً أيضاً: اكتب نص البرومبت نفسه بالكامل باللغة العربية الفصحى فقط، ولا تستخدم أي "
        "كلمة إنجليزية إطلاقاً (حتى لو كانت العادة في مواقع توليد الصور استخدام الإنجليزية)، "
        "فالمطلوب هنا بالتحديد برومبت مكتوب بالعربية."
    )
    output_rules_en = (
        "Important: give me only the prompt itself, as a single continuous paragraph starting directly "
        "with the image description — no preamble, no headings, no separate analysis, no numbered lists, "
        "no Markdown formatting (no **, no #, no -), and no extra commentary before or after."
    )

    system_instructions = {
        ("ar", "short"): (
            "اكتب برومبت قصير باللغة العربية لا يقل عن 70 كلمة ولا يزيد عن 80 كلمة لوصف هذه الصورة "
            "لاستخدامه في الذكاء الاصطناعي، بحيث يذكر الموضوع الرئيسي وأهم تفاصيله الظاهرة، والإضاءة، "
            "والألوان العامة، والأسلوب البصري، بأسلوب مكثف لكنه غني بالتفاصيل المهمة وليس مجرد جملة عابرة. "
            f"{output_rules_ar}"
        ),
        ("ar", "medium"): (
            "اكتب برومبت متوسط الطول باللغة العربية لا يقل عن 80 كلمة ولا يزيد عن 120 كلمة لوصف هذه الصورة "
            "لاستخدامه في توليد الصور، بحيث يغطي الموضوع الرئيسي وتفاصيله، نوع اللقطة والزاوية تقريباً، "
            "الإضاءة ومصدرها، الألوان السائدة، الخلفية، والأسلوب الفني العام. "
            f"{output_rules_ar}"
        ),
        ("ar", "detailed"): (
            "قم بتحليل هذه الصورة بأقصى درجة ممكنة من الدقة والعمق، ثم حوّل هذا التحليل إلى برومبت واحد "
            "تفصيلي جداً باللغة العربية لا يقل عن 220 كلمة (ويفضل أن يتجاوزها)، بحيث يشرح بدقة متناهية ضمن "
            "نفس الفقرة: الموضوع الرئيسي وكل تفاصيله الدقيقة (الملابس، تعابير الوجه أو الشكل، الوضعية، "
            "الحركة)، نوع اللقطة وزاوية الكاميرا وبعد العدسة، مصدر الإضاءة واتجاهها ولونها وشدتها والظلال "
            "الناتجة عنها، الألوان السائدة ودرجاتها والتباين بينها، الملمس الدقيق للأسطح والمواد (قماش، "
            "جلد، فراء، معدن...)، الخلفية وكل العناصر المحيطة وعمق المجال، الجو العام والمزاج والقصة "
            "الضمنية التي توحي بها الصورة، وأخيراً الأسلوب الفني أو نوع التصوير (سينمائي، واقعي، لوحة "
            "رقمية، خيال علمي...الخ). اشرح كل عنصر من هذه العناصر بجملة أو أكثر ولا تكتفِ بذكره فقط. "
            f"{output_rules_ar}"
        ),
        ("en", "short"): (
            "Write a short image generation prompt of at least 70 words and no more than 80 words "
            "describing this image, mentioning the main subject and its key visible details, the lighting, "
            "the overall colors, and the visual style — dense but rich in meaningful detail, not just a "
            f"single passing sentence. {output_rules_en}"
        ),
        ("en", "medium"): (
            "Write a medium-length image generation prompt of at least 80 words and no more than 120 words "
            "describing this image, covering the main subject and its details, roughly the shot type and "
            f"angle, the lighting and its source, dominant colors, background, and overall artistic style. {output_rules_en}"
        ),
        ("en", "detailed"): (
            "Analyze this image with maximum depth and precision, then turn that analysis into a single "
            "ultra-detailed image generation prompt of at least 220 words (preferably more), explaining "
            "with extreme precision within the same paragraph: the main subject and every fine detail "
            "(clothing, expression or shape, pose, motion), the shot type, camera angle and lens focal "
            "length, the lighting source, direction, color, intensity and the shadows it creates, the "
            "dominant colors, their shades and the contrast between them, the fine texture of surfaces and "
            "materials (fabric, leather, fur, metal...), the background and every surrounding element and "
            "depth of field, the overall mood and atmosphere and the implicit story the image suggests, and "
            "finally the artistic or cinematographic style (cinematic, photorealistic, digital painting, "
            "sci-fi, etc). Explain each of these elements in a sentence or more, don't just name it. "
            f"{output_rules_en}"
        ),
    }

    instruction = system_instructions.get((selected_lang, selected_length), "")

    if selected_ratio:
        instruction += f"\nInclude aspect ratio: --ar {selected_ratio}"

    image = Image.open(io.BytesIO(photo_bytes))

    # للمستوى التفصيلي: نبدأ بالنموذج الأقوى (gemini-3.6-flash) بدل الأخف (flash-lite)،
    # ونرفع سقف عدد الكلمات المسموح به حتى لا يُقطع الرد قبل اكتماله.
    if selected_length == "detailed":
        models_order = ["gemini-3.6-flash"] + [m for m in AVAILABLE_MODELS if m != "gemini-3.6-flash"]
        generation_config = {**GENERATION_CONFIG, "max_output_tokens": 2500, "temperature": 0.9}
    else:
        models_order = None
        generation_config = None

    try:
        generated_prompt = await generate_prompt_with_fallback(
            instruction, image, models_order=models_order, generation_config=generation_config
        )
    except Exception as e:
        loading_task.cancel()
        try:
            await loading_task
        except asyncio.CancelledError:
            pass
        logging.error(f"فشل التوليد عبر كل المفاتيح: {e}")
        err_str = str(e)
        if "429" in err_str or "quota" in err_str.lower():
            await query.message.reply_text(t(context, "quota_error"))
        else:
            await query.message.reply_text(t(context, "error_generation") + err_str)
        return

    loading_task.cancel()
    try:
        await loading_task
    except asyncio.CancelledError:
        pass

    try:
        generated_prompt = clean_generated_prompt(generated_prompt)
        if len(generated_prompt) > 3800:
            generated_prompt = generated_prompt[:3800] + "..."

        post_action_keyboard = [
            [InlineKeyboardButton(t(context, "btn_retry"), callback_data="back_to_lang")],
            [InlineKeyboardButton(t(context, "btn_new_photo"), callback_data="new_photo_request")],
        ]
        reply_markup = InlineKeyboardMarkup(post_action_keyboard)
        result_message = t(context, "success_title") + f"```\n{generated_prompt}\n```"

        await context.bot.send_message(
            chat_id=chat_id,
            text=result_message,
            parse_mode="Markdown",
            reply_markup=reply_markup,
            reply_to_message_id=photo_message_id,
        )
        await query.delete_message()

        await notify_channel(user, "قام باستخراج برومبت من صورة 📝", context)

    except Exception as e:
        logging.error(f"خطأ إرسال الرسالة: {e}")
        await query.message.reply_text(t(context, "error_generation") + str(e))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("uilang_"):
        ui_lang = data.split("_")[1]
        context.user_data["ui_lang"] = ui_lang

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
        context.user_data.pop("photo_bytes", None)
        context.user_data.pop("selected_lang", None)
        context.user_data.pop("selected_length", None)
        context.user_data.pop("selected_ratio", None)
        context.user_data.pop("photo_message_id", None)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=t(context, "ready_for_new")
        )
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
        photo_bytes = context.user_data.get("photo_bytes")
        if not photo_bytes:
            await query.edit_message_text(t(context, "session_expired"))
            return
        # نأخذ نفس مقاس/نسبة الصورة المرسلة تلقائياً بدون سؤال المستخدم
        context.user_data["selected_ratio"] = compute_image_ratio(photo_bytes)
        await generate_and_send_prompt(query, context, update.effective_chat.id, update.effective_user)
        return

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """معالج أخطاء عام: يسجل أي استثناء يحدث داخل معالجة رسالة أو زر، بدل أن
    يتسبب في توقف البوت بالكامل (وهو ما يجعل خدمة Render/UptimeRobot تظهر Down)."""
    logging.error("حدث استثناء غير متوقع أثناء معالجة تحديث:", exc_info=context.error)

def main():
    if not TELEGRAM_TOKEN:
        logging.error("❌ لم يتم تعيين TELEGRAM_TOKEN في متغيرات البيئة! لن يعمل البوت.")
    if not API_KEYS:
        logging.warning("⚠️ لم يتم العثور على أي مفتاح في متغير API_KEYS!")

    threading.Thread(target=start_health_server, daemon=True).start()
    threading.Thread(target=keep_alive_ping, daemon=True).start()

    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )

    app = Application.builder().token(TELEGRAM_TOKEN).request(request).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_error_handler(error_handler)

    print("🤖 البوت يعمل بنجاح مع تدوير المفاتيح التلقائي...")

    # حلقة تشغيل مستمرة: أي عطل غير متوقع (انقطاع شبكة، خطأ داخلي، إلخ) لا يوقف
    # العملية بالكامل، بل يُعاد تشغيل البوت تلقائياً بعد مهلة قصيرة، حتى تبقى
    # خدمة Render (وخادم فحص الصحة معها) شغّالة طول الوقت بدون تدخل يدوي.
    retry_delay = 5
    while True:
        try:
            app.run_polling(drop_pending_updates=True, close_loop=False)
            # إذا خرج run_polling بشكل طبيعي (إيقاف يدوي نظيف) لا داعي لإعادة التشغيل
            break
        except (KeyboardInterrupt, SystemExit):
            logging.info("🛑 تم إيقاف البوت يدوياً.")
            break
        except Exception as e:
            logging.error(
                f"❌ توقف البوت بسبب خطأ غير متوقع، سيُعاد التشغيل خلال {retry_delay} ثانية: {e}",
                exc_info=True,
            )
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)

if __name__ == "__main__":
    main()
