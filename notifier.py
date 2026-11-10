"""Telegram: invio e modifica dei messaggi, comandi dalle chat private."""
import logging

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.error import Conflict, TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, filters

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, token, admin_chat_id, dry_run=False):
        self.dry_run = dry_run or not (token and admin_chat_id)
        self.admin_chat_id = str(admin_chat_id or 0)
        self.app = None if self.dry_run else Application.builder().token(token).build()
        self.commands = []
        self.callbacks = {}
        self.on_conflict = None  # chiamata se un'altra copia del bot usa lo stesso token
        if self.dry_run:
            log.warning("Modalità DRY RUN: le notifiche vengono solo stampate a schermo, comandi disattivati")

    def add_command(self, name, description, callback, menu=True):
        """`callback(chat_id, nome_utente, args) -> testo HTML`, chiamato quando qualcuno scrive /name.

        Il bot risponde solo nelle chat private; chi può usare cosa lo decide `callback`.
        `menu=False` nasconde il comando dal menu di Telegram (comandi da amministratore).
        """
        if menu:
            self.commands.append(BotCommand(name, description))
        self.callbacks[name] = callback
        if not self.app:
            return

        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            user = update.effective_user
            try:
                reply = await callback(str(update.effective_chat.id),
                                       user.full_name if user else "?", context.args or [])
            except Exception:
                log.exception("Errore nel comando /%s", name)
                reply = "⚠️ Errore interno, guarda i log del bot."
            if reply:
                await update.effective_message.reply_html(reply, disable_web_page_preview=True)

        self.app.add_handler(CommandHandler(name, handler, filters=filters.ChatType.PRIVATE))

    async def start(self):
        if not self.app:
            return
        await self.app.initialize()
        try:
            await self.app.bot.set_my_commands(self.commands)
        except TelegramError as e:
            log.warning("Impossibile impostare il menu dei comandi: %s", e)
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True, error_callback=self._polling_error)

    def _polling_error(self, error):
        if isinstance(error, Conflict):
            log.warning("Un'altra copia del bot usa lo stesso token Telegram")
            if self.on_conflict:
                self.on_conflict()
        else:
            log.warning("Errore nel ricevere i comandi da Telegram: %s", error)

    async def stop(self):
        if not self.app:
            return
        if self.app.updater.running:
            await self.app.updater.stop()
        if self.app.running:
            await self.app.stop()
        await self.app.shutdown()

    async def send(self, html_text, chat_id=None):
        """Restituisce l'id del messaggio (0 in DRY RUN), None se l'invio fallisce."""
        chat_id = str(chat_id or self.admin_chat_id)
        if self.dry_run:
            log.info("[DRY RUN] Notifica per %s:\n%s", chat_id, html_text)
            return 0
        try:
            message = await self.app.bot.send_message(
                chat_id=int(chat_id), text=html_text,
                parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            )
            return message.message_id
        except TelegramError as e:
            log.error("Errore Telegram verso %s: %s", chat_id, e)
            return None

    async def edit(self, chat_id, message_id, html_text):
        if self.dry_run or not message_id:
            log.info("[DRY RUN] Modifica notifica per %s:\n%s", chat_id, html_text)
            return
        try:
            await self.app.bot.edit_message_text(
                chat_id=int(chat_id), message_id=message_id, text=html_text,
                parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            )
        except TelegramError as e:
            log.warning("Impossibile aggiornare la notifica: %s", e)
