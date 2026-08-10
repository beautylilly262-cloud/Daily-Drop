import logging
import os
from io import BytesIO
from pypdf import PdfWriter, PdfReader
from pdf2image import convert_from_bytes
from PIL import Image
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

user_sessions = {}


def get_session(chat_id):
    if chat_id not in user_sessions:
        user_sessions[chat_id] = {"mode": None, "files": []}
    return user_sessions[chat_id]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📄 *PDF2Go Bot*\n\n"
        "Available commands:\n"
        "`/merge` – merge multiple PDFs into one\n"
        "`/topdf` – convert images into a single PDF\n"
        "`/toimages` – convert a PDF into images (one per page)\n"
        "`/totext` – extract text from a PDF\n"
        "`/done` – finish collecting files and process\n"
        "`/cancel` – cancel current operation\n\n"
        "Start with a command, then send your files.",
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*How to use:*\n\n"
        "*Merge PDFs:*\n`/merge` → send 2+ PDF files → `/done`\n\n"
        "*Images to PDF:*\n`/topdf` → send 1+ images → `/done`\n\n"
        "*PDF to images:*\n`/toimages` → send 1 PDF (auto-processes)\n\n"
        "*Extract text:*\n`/totext` → send 1 PDF (auto-processes)\n\n"
        "Use `/cancel` anytime to reset.",
        parse_mode="Markdown"
    )


async def merge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_chat.id)
    session["mode"] = "merge"
    session["files"] = []
    await update.message.reply_text("📎 Send me 2 or more PDF files, then type /done.")


async def topdf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_chat.id)
    session["mode"] = "topdf"
    session["files"] = []
    await update.message.reply_text("🖼 Send me one or more images, then type /done.")


async def toimages_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_chat.id)
    session["mode"] = "toimages"
    session["files"] = []
    await update.message.reply_text("📄 Send me a single PDF and I'll convert each page to an image.")


async def totext_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session(update.effective_chat.id)
    session["mode"] = "totext"
    session["files"] = []
    await update.message.reply_text("📄 Send me a single PDF and I'll extract its text.")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_sessions[chat_id] = {"mode": None, "files": []}
    await update.message.reply_text("❌ Cancelled. Send a command to start again.")


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if session["mode"] not in ("merge", "topdf"):
        await update.message.reply_text("⚠️ Nothing to finish. Start with /merge or /topdf first.")
        return

    if session["mode"] == "merge":
        await process_merge(update, session["files"])
    elif session["mode"] == "topdf":
        await process_images_to_pdf(update, session["files"])

    user_sessions[chat_id] = {"mode": None, "files": []}


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    doc = update.message.document

    if session["mode"] is None:
        await update.message.reply_text("⚠️ Please start with a command first: /merge, /topdf, /toimages, or /totext")
        return

    try:
        file_bytes = await (await doc.get_file()).download_as_bytearray()
    except Exception as e:
        logger.error(f"Download error: {e}")
        await update.message.reply_text("❌ Couldn't download that file. Please try sending it again.")
        return

    if session["mode"] == "merge":
        if doc.mime_type != "application/pdf":
            await update.message.reply_text("⚠️ Please send a PDF file.")
            return
        session["files"].append(bytes(file_bytes))
        await update.message.reply_text(f"✅ Added ({len(session['files'])} PDFs so far). Send more or /done.")

    elif session["mode"] == "toimages":
        if doc.mime_type != "application/pdf":
            await update.message.reply_text("⚠️ Please send a PDF file.")
            return
        await process_pdf_to_images(update, bytes(file_bytes))
        user_sessions[chat_id] = {"mode": None, "files": []}

    elif session["mode"] == "totext":
        if doc.mime_type != "application/pdf":
            await update.message.reply_text("⚠️ Please send a PDF file.")
            return
        await process_pdf_to_text(update, bytes(file_bytes))
        user_sessions[chat_id] = {"mode": None, "files": []}

    elif session["mode"] == "topdf":
        if not doc.mime_type or not doc.mime_type.startswith("image/"):
            await update.message.reply_text("⚠️ Please send an image file.")
            return
        session["files"].append(bytes(file_bytes))
        await update.message.reply_text(f"✅ Added ({len(session['files'])} images so far). Send more or /done.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    if session["mode"] != "topdf":
        await update.message.reply_text("⚠️ Start with /topdf first if you want to convert images to a PDF.")
        return

    try:
        photo = update.message.photo[-1]
        file_bytes = await (await photo.get_file()).download_as_bytearray()
        session["files"].append(bytes(file_bytes))
        await update.message.reply_text(f"✅ Added ({len(session['files'])} images so far). Send more or /done.")
    except Exception as e:
        logger.error(f"Photo download error: {e}")
        await update.message.reply_text("❌ Couldn't process that photo. Please try again.")


async def process_merge(update: Update, files: list):
    if len(files) < 2:
        await update.message.reply_text("⚠️ Need at least 2 PDFs to merge. Try /merge again.")
        return

    status_msg = await update.message.reply_text("🔗 Merging PDFs...")
    try:
        writer = PdfWriter()
        for f in files:
            reader = PdfReader(BytesIO(f))
            for page in reader.pages:
                writer.add_page(page)

        output = BytesIO()
        writer.write(output)
        output.seek(0)
        output.name = "merged.pdf"

        await update.message.reply_document(document=output, filename="merged.pdf", caption="✅ Merged PDF")
        await status_msg.delete()
    except Exception as e:
        logger.error(f"Merge error: {e}")
        await status_msg.edit_text("❌ Failed to merge PDFs. Make sure all files are valid, unencrypted PDFs.")


async def process_images_to_pdf(update: Update, files: list):
    if len(files) < 1:
        await update.message.reply_text("⚠️ No images received. Try /topdf again.")
        return

    status_msg = await update.message.reply_text("🖼 Converting images to PDF...")
    try:
        images = [Image.open(BytesIO(f)).convert("RGB") for f in files]
        output = BytesIO()
        images[0].save(output, format="PDF", save_all=True, append_images=images[1:])
        output.seek(0)
        output.name = "converted.pdf"

        await update.message.reply_document(document=output, filename="converted.pdf", caption="✅ Converted to PDF")
        await status_msg.delete()
    except Exception as e:
        logger.error(f"Image to PDF error: {e}")
        await status_msg.edit_text("❌ Failed to convert images. Please check the files and try again.")


async def process_pdf_to_images(update: Update, pdf_bytes: bytes):
    status_msg = await update.message.reply_text("📄 Converting PDF pages to images...")
    try:
        pages = convert_from_bytes(pdf_bytes, dpi=150)

        if len(pages) > 20:
            await status_msg.edit_text("⚠️ This PDF has too many pages (max 20). Please use a shorter file.")
            return

        for i, page in enumerate(pages, start=1):
            img_buffer = BytesIO()
            page.save(img_buffer, format="PNG")
            img_buffer.seek(0)
            img_buffer.name = f"page_{i}.png"
            await update.message.reply_document(document=img_buffer, filename=f"page_{i}.png")

        await status_msg.edit_text(f"✅ Converted {len(pages)} page(s) to images.")
    except Exception as e:
        logger.error(f"PDF to images error: {e}")
        await status_msg.edit_text("❌ Failed to convert PDF. Please make sure it's a valid PDF file.")


async def process_pdf_to_text(update: Update, pdf_bytes: bytes):
    status_msg = await update.message.reply_text("📄 Extracting text...")
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        text_parts = []
        for i, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text() or ""
            text_parts.append(f"--- Page {i} ---\n{page_text}")

        full_text = "\n\n".join(text_parts).strip()

        if not full_text:
            await status_msg.edit_text("⚠️ No extractable text found (this may be a scanned/image-based PDF).")
            return

        if len(full_text) < 3500:
            await status_msg.edit_text(f"📝 *Extracted Text:*\n\n{full_text[:3500]}", parse_mode="Markdown")
        else:
            text_file = BytesIO(full_text.encode("utf-8"))
            text_file.name = "extracted_text.txt"
            await update.message.reply_document(document=text_file, filename="extracted_text.txt", caption="✅ Extracted text")
            await status_msg.delete()
    except Exception as e:
        logger.error(f"Text extraction error: {e}")
        await status_msg.edit_text("❌ Failed to extract text. Please make sure it's a valid PDF file.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ Please use a command first: /merge, /topdf, /toimages, or /totext\nSee /help for details."
    )


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable is not set")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("merge", merge_command))
    app.add_handler(CommandHandler("topdf", topdf_command))
    app.add_handler(CommandHandler("toimages", toimages_command))
    app.add_handler(CommandHandler("totext", totext_command))
    app.add_handler(CommandHandler("done", done_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("PDF2Go bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
