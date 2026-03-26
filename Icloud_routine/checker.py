import asyncio
import email
import imaplib
import logging
from datetime import datetime, timedelta
from email.utils import getaddresses, parsedate_to_datetime
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


class ICloudChecker:
    @staticmethod
    async def check(hours: int = 24, keyword: str = "", max_results: int = 100):
        email_login = os.getenv("ICLOUD_EMAIL")
        app_password = os.getenv("ICLOUD_APP_PASSWORD")

        if not email_login or not app_password:
            return {"ok": False, "message": "ICLOUD_EMAIL / ICLOUD_APP_PASSWORD не настроены"}

        keyword = keyword.strip()
        cutoff = datetime.now() - timedelta(hours=hours)

        try:
            mail = imaplib.IMAP4_SSL("imap.mail.me.com", 993)
            mail.login(email_login, app_password)
            
            # Пробуем папку bitrue, если не найдёт — INBOX
            for folder in ['"bitrue"', "INBOX"]:
                try:
                    mail.select(folder)
                    logging.info(f"Checker: выбрана папка {folder}")
                    break
                except:
                    continue

            # Быстрый поиск через IMAP
            search_query = f'SINCE "{cutoff.strftime("%d-%b-%Y")}"'
            if keyword:
                search_query += f' TEXT "{keyword}"'

            logging.info(f"Checker запрос: {search_query} | Макс результатов: {max_results}")

            typ, data = mail.search(None, search_query)
            nums = data[0].split() if data and data[0] else []

            logging.info(f"IMAP нашёл потенциальных писем: {len(nums)}")

            results = []
            processed = 0

            for num in nums[:max_results * 3]:  # берём с запасом, т.к. могут быть ложные срабатывания
                if len(results) >= max_results:
                    break

                try:
                    # Быстрое получение только нужных заголовков + тела
                    _, msg_data = mail.fetch(num, "(BODY.PEEK[HEADER] BODY.PEEK[TEXT])")
                    if not msg_data or not msg_data[0]:
                        continue

                    raw = b""
                    for part in msg_data:
                        if isinstance(part, tuple) and len(part) > 1:
                            raw += part[1]

                    if not raw:
                        continue

                    msg = email.message_from_bytes(raw)

                    # Проверка даты
                    date_str = msg.get("Date")
                    if date_str:
                        try:
                            msg_date = parsedate_to_datetime(date_str)
                            if msg_date < cutoff:
                                continue
                        except:
                            pass

                    alias = _extract_alias(msg)
                    subject = _decode_mime(msg.get("Subject", ""))
                    body = _get_body_text(msg)

                    full_text = (subject + " " + body).lower()

                    if not keyword or keyword.lower() in full_text:
                        results.append({
                            "alias": alias,
                            "subject": subject,
                            "date": date_str or "",
                        })
                        processed += 1

                except Exception as e:
                    logging.debug(f"Ошибка обработки письма {num}: {e}")
                    continue

            mail.logout()

            logging.info(f"Checker завершён. Найдено релевантных писем: {len(results)}")

            return {
                "ok": True,
                "count": len(results),
                "hours": hours,
                "keyword": keyword,
                "results": results
            }

        except Exception as e:
            logging.exception(f"Checker error: {e}")
            return {"ok": False, "message": f"Ошибка: {str(e)}"}


def _decode_mime(value: str | None) -> str:
    if not value:
        return ""
    from email.header import decode_header
    out = []
    for part, enc in decode_header(value):
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", errors="ignore"))
        else:
            out.append(str(part))
    return "".join(out)


def _extract_alias(msg):
    for h in ("Delivered-To", "X-Original-To", "To"):
        v = msg.get(h)
        if v:
            addrs = [addr[1] for addr in getaddresses([v]) if "@icloud.com" in addr[1].lower()]
            if addrs:
                return addrs[0]
    return "unknown@icloud.com"


def _get_body_text(msg):
    texts = []
    try:
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
    except:
        pass
    return " ".join(texts)


if __name__ == "__main__":
    asyncio.run(ICloudChecker.check(hours=24, keyword="airdrop"))