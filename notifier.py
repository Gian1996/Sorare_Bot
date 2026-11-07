"""Telegram: invio e modifica dei messaggi, comandi dalla chat."""
import logging

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, filters

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, token, chat_id, dry_run=False):
        self.dry_run = dry_run or not (token and chat_id)
        self.chat_id = int(chat_id) if chat_id else None
        self.app = None if self.dry_run else Application.builder().token(token).build()
        self.commands = []
        self.callbacks = {}
        if self.dry_run:
            log.warning("Modalità DRY RUN: le notifiche vengono solo stampate a schermo, comandi disattivati")

    def add_command(self, name, description, callback):
        """`callback(args) -> testo HTML` viene chiamato quando scrivi /name nella chat del bot."""
        self.commands.append(BotCommand(name, description))
        self.callbacks[name] = callback
        if not self.app:
            return

        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            try:
                reply = await callback(context.args or [])
            except Exception:
                log.exception("Errore nel comando /%s", name)
                reply = "⚠️ Errore interno, guarda i log del bot."
            await update.effective_message.reply_html(reply, disable_web_page_preview=True)

        # Risponde solo alla tua chat: nessun altro può comandare il bot.
        self.app.add_handler(CommandHandler(name, handler, filters=filters.Chat(chat_id=self.chat_id)))

    async def start(self):
        if not self.app:
            return
        await self.app.initialize()
        try:
            await self.app.bot.set_my_commands(self.commands)
        except TelegramError as e:
            log.warning("Impossibile impostare il menu dei comandi: %s", e)
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)

    async def stop(self):
        if not self.app:
            return
        if self.app.updater.running:
            await self.app.updater.stop()
        if self.app.running:
            await self.app.stop()
        await self.app.shutdown()

    async def send(self, html_text):
        """Restituisce l'id del messaggio (0 in DRY RUN), None se l'invio fallisce."""
        if self.dry_run:
            log.info("[DRY RUN] Notifica:\n%s", html_text)
            return 0
        try:
            message = await self.app.bot.send_message(
                chat_id=self.chat_id, text=html_text,
                parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            )
            return message.message_id
        except TelegramError as e:
            log.error("Errore Telegram: %s", e)
            return None

    async def edit(self, message_id, html_text):
        if self.dry_run or not message_id:
            log.info("[DRY RUN] Modifica notifica:\n%s", html_text)
            return
        try:
            await self.app.bot.edit_message_text(
                chat_id=self.chat_id, message_id=message_id, text=html_text,
                parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            )
        except TelegramError as e:
            log.warning("Impossibile aggiornare la notifica: %s", e)
