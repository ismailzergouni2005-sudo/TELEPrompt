import io
import logging
from google import generativeai as genai
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest

# إعداد التسجيل (Logging)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# مفاتيح التشغيل
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"

# إعداد نموذج Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.5-flash")

# رسالة البدء
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "👋 **أهلاً بك في بوت استخراج البرومبت الاحترافي!**\n\n"
        "أرسل لي أي صورة، وسأقوم باستخراج البرومبت الخاص بها مباشرة وبطريقة مجهزة للنسخ الفوري."
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

# استقبال الصورة وعرض الخطوة الأولى (اختيار اللغة)
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()

    context.user_data["photo_bytes"] = photo_bytes

    await show_language_menu(update.message.reply_text)

# عرض قائمة اختيار اللغة
async def show_language_menu(send_func):
    keyboard = [
        [
            InlineKeyboardButton("🇸🇦 اللغة العربية", callback_data="lang_ar"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
        ],
        [
            InlineKeyboardButton("❌ إلغاء", callback_data="cancel"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_func("🌐 **الخطوة 1/2:** اختر لغة البرومبت المطلوب:", reply_markup=reply_markup, parse_mode="Markdown")

# عرض قائمة اختيار مستوى التفاصيل
async def show_detail_menu(query):
    keyboard = [
        [
            InlineKeyboardButton("⚡ قصير وموجز", callback_data="detail_short"),
            InlineKeyboardButton("⚖️ متوسط", callback_data="detail_medium"),
        ],
        [
            InlineKeyboardButton("🔍 تفصيلي وطويل جداً", callback_data="detail_detailed"),
        ],
        [
            InlineKeyboardButton("🔙 رجوع للغة", callback_data="back_to_lang"),
            InlineKeyboardButton("❌ إلغاء", callback_data="cancel"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("⚙️ **الخطوة 2/2:** اختر مستوى تفصيل البرومبت:", reply_markup=reply_markup, parse_mode="Markdown")

# معالجة تفاعلات الأزرار
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # زر الإلغاء
    if data == "cancel":
        context.user_data.clear()
        await query.edit_message_text("🚫 تم إلغاء العملية. يمكنك إرسال صورة جديدة في أي وقت.")
        return

    # زر الرجوع لاختيار اللغة
    if data == "back_to_lang":
        await show_language_menu(query.edit_message_text)
        return

    # اختيار اللغة
    if data.startswith("lang_"):
        selected_lang = data.split("_")[1]
        context.user_data["selected_lang"] = selected_lang
        await show_detail_menu(query)
        return

    # اختيار مستوى التفصيل وتوليد البرومبت
    if data.startswith("detail_"):
        selected_length = data.split("_")[1]
        selected_lang = context.user_data.get("selected_lang", "en")
        photo_bytes = context.user_data.get("photo_bytes")

        if not photo_bytes:
            await query.edit_message_text("⚠️ انتهت الجلسة. يرجى إعادة إرسال الصورة من جديد.")
            return

        await query.edit_message_text("⏳ جاري تحليل الصورة واستخراج البرومبت الموسّع...")

        # تعليمات مخصصة لإنتاج برومبت أطول وأكثر تفصيلاً
        system_instructions = {
            ("ar", "short"): "اكتب برومبت قصير وموجز باللغة العربية يصف الأجزاء الرئيسية لهذه الصورة لتوليد صورة مماثلة. أرجع نص البرومبت فقط بدون أية مقدمات.",
            ("ar", "medium"): "اكتب برومبت متوسط التفاصيل باللغة العربية يصف موضوع الصورة والنمط والألوان والإضاءة. أرجع نص البرومبت فقط بدون أية مقدمات.",
            ("ar", "detailed"): (
                "قم بكتابة برومبت طويل جداً وتفصيلي للغاية باللغة العربية لإعادة توليد هذه الصورة بالذكاء الاصطناعي. "
                "اشرح كافة الأركان: العناصر المركزية والخلفية، دقة وتفاصيل الإضاءة والظلال، التكوين والزاوية، نوع الكاميرا والعدسة، الألوان والدرجات، والأسلوب الفني أو محرك العرض. "
                "أرجع نص البرومبت فقط دون أية مقدمات أو شروحات."
            ),
            ("en", "short"): "Write a concise image generation prompt in English to recreate this photo. Output ONLY the raw prompt text.",
            ("en", "medium"): "Write a medium-detailed image generation prompt in English describing style, lighting, composition, and colors. Output ONLY the raw prompt text.",
            ("en", "detailed"): (
                "Write an extremely long, highly detailed, comprehensive image generation prompt in English to recreate this image perfectly. "
                "Cover every single detail: subject descriptions, background elements, art style or rendering engine (e.g., Unreal Engine 5, Octane Render), camera lens and shot angle, lighting direction and volumetric atmosphere, color palette, textures, and quality tags (e.g., 8k, cinematic, ultra-realistic). "
                "Output ONLY the raw prompt text without any intro, labels, or extra comments."
            ),
        }

        instruction = system_instructions.get((selected_lang, selected_length))

        try:
            image = Image.open(io.BytesIO(photo_bytes))
            response = model.generate_content([instruction, image])
            generated_prompt = response.text.strip()

            # إعداد أزرار التحكم بعد النتيجة
            post_action_keyboard = [
                [
                    InlineKeyboardButton("🔄 استخراج بمستوى/لغة أخرى", callback_data="back_to_lang"),
                ],
                [
                    InlineKeyboardButton("📸 أرسل صورة جديدة", callback_data="cancel"),
                ],
            ]
            reply_markup = InlineKeyboardMarkup(post_action_keyboard)

            result_message = (
                "✅ **تم استخراج البرومبت بنجاح!**\n"
                "*(اضغط على النص أدناه لنسخه فوراً)*\n\n"
                f"```\n{generated_prompt}\n```"
            )

            await query.message.reply_text(result_message, parse_mode="Markdown", reply_markup=reply_markup)
            await query.delete_message()

        except Exception as e:
            logging.error(f"خطأ أثناء التوليد: {e}")
            await query.message.reply_text(f"❌ حدث خطأ أثناء تحليل الصورة: {str(e)}")

# تشغيل البوت
def main():
    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )

    app = Application.builder().token(TELEGRAM_TOKEN).request(request).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🤖 البوت يعمل الآن بنجاح...")
    app.run_polling()

if __name__ == "__main__":
    main()
