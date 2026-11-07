"""Invio dei messaggi su Telegram."""
import logging

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, token, chat_id, dry_run=False):
        self.chat_id = chat_id
        self.dry_run = dry_run or not (token and chat_id)
        self.bot = None if self.dry_run else Bot(token=token)
        if self.dry_run:
            log.warning("Modalità DRY RUN: le notifiche vengono solo stampate a schermo")

    async def start(self):
        if self.bot:
            await self.bot.initialize()

    async def stop(self):
        if self.bot:
            await self.bot.shutdown()

    async def send(self, html_text):
        if self.dry_run:
            log.info("[DRY RUN] Notifica:\n%s", html_text)
            return True
        try:
            await self.bot.send_message(
                chat_id=self.chat_id, text=html_text,
                parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            )
            return True
        except TelegramError as e:
            log.error("Errore Telegram: %s", e)
            return False
