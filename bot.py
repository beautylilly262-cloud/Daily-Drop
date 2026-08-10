import logging
import os
from io import BytesIO
from PyPDF2 import PdfMerger, PdfReader
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

    file_bytes = await (await doc.get_file()).download_as_bytearray()

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
        await update.message.reply_text(f"✅ Added
