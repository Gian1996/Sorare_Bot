"""Telegram: invio e modifica dei messaggi, comandi dalle chat private."""
import logging
import os

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import Conflict, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, filters

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, token, admin_chat_id, dry_run=False):
        self.dry_run = dry_run or not (token and admin_chat_id)
        self.admin_chat_id = str(admin_chat_id or 0)
        self.app = None if self.dry_run else Application.builder().token(token).build()
        self.commands = []
        self.callbacks = {}
        self.on_conflict = None  # chiamata se un'altra copia del bot usa lo stesso token
        self.buttons = {}  # prefisso -> callback dei pulsanti sotto i messaggi
        if self.app:
            self.app.add_handler(CallbackQueryHandler(self._on_button))
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

    def add_button(self, prefix, callback):
        """`callback(chat_id, message_id, args) -> dict` quando qualcuno tocca un pulsante "prefisso|args".

        Il dict può avere: "toast" (scritta breve in sovrimpressione), "reply" (messaggio HTML di risposta),
        "buttons" (nuovi pulsanti per il messaggio toccato).
        """
        self.buttons[prefix] = callback

    @staticmethod
    def markup(buttons):
        """[[(etichetta, "https://..." oppure "dati|del|pulsante"), ...], ...] -> tastiera Telegram."""
        if not buttons:
            return None
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(label, url=value) if value.startswith("https://")
             else InlineKeyboardButton(label, callback_data=value[:64]) for label, value in row]
            for row in buttons])

    async def _on_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        prefix, _, rest = (query.data or "").partition("|")
        callback = self.buttons.get(prefix)
        try:
            if callback is None:
                result = {"toast": "Pulsante non più valido"}
            else:
                result = await callback(str(query.message.chat.id), query.message.message_id, rest.split("|"))
        except Exception:
            log.exception("Errore nel pulsante %s", query.data)
            result = {"toast": "⚠️ Errore interno, guarda i log del bot."}
        try:
            await query.answer(result.get("toast"))
            if "buttons" in result:
                await query.edit_message_reply_markup(self.markup(result["buttons"]))
            if result.get("reply"):
                await query.message.reply_html(result["reply"], disable_web_page_preview=True)
        except TelegramError as e:
            log.warning("Risposta al pulsante non riuscita: %s", e)

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

    async def send(self, html_text, chat_id=None, buttons=None):
        """Restituisce l'id del messaggio (0 in DRY RUN), None se l'invio fallisce."""
        chat_id = str(chat_id or self.admin_chat_id)
        if self.dry_run:
            extra = f"\n[pulsanti: {' | '.join(l for row in buttons for l, _ in row)}]" if buttons else ""
            log.info("[DRY RUN] Notifica per %s:\n%s%s", chat_id, html_text, extra)
            return 0
        try:
            message = await self.app.bot.send_message(
                chat_id=int(chat_id), text=html_text, reply_markup=self.markup(buttons),
                parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            )
            return message.message_id
        except TelegramError as e:
            log.error("Errore Telegram verso %s: %s", chat_id, e)
            return None

    async def send_file(self, path, caption="", chat_id=None):
        """Manda un file come documento. True se riuscito."""
        chat_id = str(chat_id or self.admin_chat_id)
        if self.dry_run:
            log.info("[DRY RUN] File per %s: %s (%s)", chat_id, path, caption)
            return True
        try:
            with open(path, "rb") as f:
                await self.app.bot.send_document(chat_id=int(chat_id), document=f, caption=caption,
                                                 filename=os.path.basename(path))
            return True
        except (TelegramError, OSError) as e:
            log.error("Invio del file %s non riuscito: %s", path, e)
            return False

    async def edit(self, chat_id, message_id, html_text, buttons=None):
        if self.dry_run or not message_id:
            log.info("[DRY RUN] Modifica notifica per %s:\n%s", chat_id, html_text)
            return
        try:
            await self.app.bot.edit_message_text(
                chat_id=int(chat_id), message_id=message_id, text=html_text, reply_markup=self.markup(buttons),
                parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            )
        except TelegramError as e:
            log.warning("Impossibile aggiornare la notifica: %s", e)
