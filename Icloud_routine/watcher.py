import asyncio
import email
import imaplib
import logging
import os
import re
from email.header import decode_header
from email.message import Message
from email.utils import getaddresses
from typing import Callable, Any

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

CODE_RE = re.compile(r"\b\d{6}\b")


def _decode_mime(value: str | None) -> str:
    if not value:
        return ""
    out = []
    for part, enc in decode_header(value):
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", errors="ignore"))
        else:
            out.append(part)
    return "".join(out)


def _extract_alias(msg: Message) -> str:
    hme = msg.get("X-ICLOUD-HME", "")
    if hme:
        for part in hme.split(";"):
            part = part.strip()
            if part.startswith("p="):
                addr = part[2:].strip()
                if addr:
                    return addr.lower()

    candidates = []
    for h in ("Delivered-To", "X-Original-To", "To"):
        v = msg.get(h)
        if v:
            candidates.extend(addr for _, addr in getaddresses([v]))
    for addr in candidates:
        a = addr.strip().lower()
        if a.endswith("@icloud.com"):
            return a
    return candidates[0].strip().lower() if candidates else "unknown@icloud.com"


def _extract_code(msg: Message) -> str | None:
    texts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                texts.append(payload.decode(charset, errors="ignore"))
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        texts.append(payload.decode(charset, errors="ignore"))
    for t in texts:
        m = CODE_RE.search(t)
        if m:
            return m.group(0)
    return None


def _fetch_raw(mail: imaplib.IMAP4_SSL, num: bytes) -> bytes | None:
    for spec in ("(RFC822)", "(BODY[])"):
        try:
            typ, msg_data = mail.fetch(num, spec)
            if typ != "OK" or not msg_data:
                continue
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    if len(response_part) > 1 and isinstance(response_part[1], bytes):
                        data = response_part[1]
                        if len(data) > 200:
                            return data
                elif isinstance(response_part, bytes):
                    if len(response_part) > 200:
                        return response_part
        except Exception as e:
            logging.debug(f"fetch {spec} error: {e}")
            continue
    return None


class CustomICloudWatcher:
    def __init__(self, subject_keyword: str = "[Bitrue] Account Sign-in - Verification Code",
                 body_keyword: str = "",
                 folder: str = "bitrue"):
        self.subject_keyword = subject_keyword
        self.body_keyword = body_keyword
        self.folder = folder
        self.running = False
        self.email = os.getenv("ICLOUD_EMAIL")
        self.app_password = os.getenv("ICLOUD_APP_PASSWORD")

    async def start(self, callback: Callable[[str, str], Any] | None = None):
        self.running = True
        logging.info(f"Watcher запущен | Folder: {self.folder} | Subject: '{self.subject_keyword}'")

        while self.running:
            mail = None
            try:
                mail = imaplib.IMAP4_SSL("imap.mail.me.com", 993)
                mail.login(self.email, self.app_password)
                mail.select(f'"{self.folder}"')

                search = f'UNSEEN SUBJECT "{self.subject_keyword}"'
                typ, data = mail.search(None, search)
                nums = (data[0].split() if data and data[0] else []) if typ == "OK" else []

                if nums:
                    logging.info(f"Найдено новых писем: {len(nums)}")

                for num in nums:
                    raw = _fetch_raw(mail, num)
                    if not raw:
                        logging.warning(f"Не удалось получить тело письма #{num.decode() if isinstance(num, bytes) else num}")
                        mail.store(num, "+FLAGS", "\\Seen")
                        continue

                    msg = email.message_from_bytes(raw)
                    subj = _decode_mime(msg.get("Subject", ""))
                    logging.info(f"Получено письмо: {subj}")

                    code = _extract_code(msg)
                    alias = _extract_alias(msg)

                    if code:
                        logging.info(f">>> УСПЕШНО: alias={alias} | code={code}")
                        if callback:
                            if asyncio.iscoroutinefunction(callback):
                                await callback(alias, code)
                            else:
                                callback(alias, code)
                    else:
                        logging.warning(f"Код не найден в письме (alias={alias})")

                    mail.store(num, "+FLAGS", "\\Seen")

                await asyncio.sleep(8)

            except Exception as e:
                logging.exception(f"Watcher error: {e}")
                await asyncio.sleep(15)
            finally:
                try:
                    if mail:
                        mail.logout()
                except Exception:
                    pass

    def stop(self):
        self.running = False
        logging.info("Watcher остановлен")