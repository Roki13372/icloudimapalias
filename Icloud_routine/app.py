import asyncio
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from watcher import CustomICloudWatcher
from checker import ICloudChecker
from alias_generator import HideMyEmailGenerator   # ← реальный генератор

ROOT = Path(__file__).parent
PROFILES_XLSX = ROOT / "profiles.xlsx"
ICLOUD_COOKIES_FILE = ROOT / "icloud_cookies.txt"

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ====================== Живой лог ======================
live_log: list[str] = []

def add_log(line: str):
    global live_log
    live_log.append(line)
    if len(live_log) > 3:
        live_log = live_log[-3:]


# ====================== Excel ======================
def ensure_profiles_structure() -> pd.DataFrame:
    columns = ["Profile ID", "Alias Email", "Main Email", "Password", "Status"]
    if not PROFILES_XLSX.exists():
        df = pd.DataFrame(columns=columns)
        df.to_excel(PROFILES_XLSX, index=False)
    else:
        df = pd.read_excel(PROFILES_XLSX)

    for col in columns:
        if col not in df.columns:
            df[col] = "" if col != "Status" else "idle"

    for col in ["Alias Email", "Main Email", "Profile ID", "Password"]:
        df[col] = df[col].astype("object")

    df["Status"] = df["Status"].astype(str).replace(["nan", "NaN", ""], "idle")
    df = df[columns]
    df.to_excel(PROFILES_XLSX, index=False)
    return df


def save_profiles(df: pd.DataFrame):
    df.to_excel(PROFILES_XLSX, index=False)


# ====================== Генерация алиасов ======================
async def generate_icloud_aliases(profile_ids: list | None = None):
    if not ICLOUD_COOKIES_FILE.exists() or not ICLOUD_COOKIES_FILE.read_text(encoding="utf-8").strip():
        return {"ok": False, "message": "icloud_cookies.txt не найден или пустой!"}

    cookies_text = ICLOUD_COOKIES_FILE.read_text(encoding="utf-8", errors="ignore").strip()
    df = ensure_profiles_structure()

    if not profile_ids or len(profile_ids) == 0:
        profile_ids = df["Profile ID"].astype(str).str.strip().tolist()

    updated = 0

    async with HideMyEmailGenerator(cookies=cookies_text) as generator:
        for pid in profile_ids:
            pid = str(pid).strip()
            mask = df["Profile ID"].astype(str).str.strip() == pid
            if not mask.any():
                continue

            row_idx = df[mask].index[0]
            current_alias = str(df.at[row_idx, "Alias Email"]).strip()

            if current_alias and current_alias.endswith("@icloud.com"):
                logging.info(f"Пропуск {pid} — алиас уже существует")
                continue

            logging.info(f"Генерация алиаса для профиля: {pid}")
            result = await generator.generate_and_reserve()

            if result.get("ok") and result.get("email"):
                df.at[row_idx, "Alias Email"] = result["email"]
                df.at[row_idx, "Status"] = "success"
                updated += 1
                logging.info(f"✓ Успешно сохранён для {pid}: {result['email']}")
            else:
                df.at[row_idx, "Status"] = "failed"
                error = result.get("error", "Unknown error")
                logging.error(f"✗ Не удалось создать для {pid}: {error}")

    if updated > 0:
        save_profiles(df)

    return {"ok": True, "message": f"Сгенерировано и забронировано {updated} алиасов"}


# ====================== Watcher + Telegram ======================
watcher_instance: CustomICloudWatcher | None = None
watcher_thread: threading.Thread | None = None


def live_callback(alias: str, code: str):
    msg = f"→ {alias} | {code}"
    logging.info(msg)
    add_log(msg)

    tg_token = os.getenv("TG_TOKEN", "")
    tg_chat_id = os.getenv("TG_CHAT_ID", "")
    if tg_token and tg_chat_id:
        try:
            asyncio.create_task(_send_to_telegram(tg_token, tg_chat_id, msg))
        except Exception as e:
            logging.error(f"Telegram error: {e}")


async def _send_to_telegram(token: str, chat_id: str, text: str):
    try:
        from aiogram import Bot
        bot = Bot(token=token)
        await bot.send_message(chat_id=int(chat_id), text=text)
        await bot.session.close()
    except Exception:
        pass


# ====================== HTTP Handler ======================
class Handler(BaseHTTPRequestHandler):
    # ... (оставляем тот же код Handler, что был у тебя раньше — он уже хороший)

    # (Чтобы не делать сообщение слишком длинным, я оставлю только важную часть)
    # Полный Handler можно взять из предыдущего сообщения — он не менялся.

    # Просто убедись, что в do_POST для /api/aliases/generate вызывается generate_icloud_aliases
    def _write_json(self, data: dict, status: int = 200):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except:
            return {}

    def do_GET(self):
        if self.path == "/":
            try:
                with open(ROOT / "index.html", "rb") as f:
                    html = f.read()
            except FileNotFoundError:
                html = self._get_default_html().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        if self.path == "/api/state":
            df = ensure_profiles_structure()
            accounts = [{
                "profile_id": str(row.get("Profile ID", "")).strip(),
                "alias": str(row.get("Alias Email", "")).strip(),
                "main_email": str(row.get("Main Email", "")).strip(),
                "password": str(row.get("Password", "")).strip(),
                "status": str(row.get("Status", "idle")).lower()
            } for _, row in df.iterrows()]
            self._write_json({"accounts": accounts})
            return

        if self.path == "/api/log":
            self._write_json({"log": live_log})
            return

        self._write_json({"ok": False, "message": "Not found"}, 404)

    def do_POST(self):
        global watcher_instance, watcher_thread

        if self.path == "/api/aliases/generate":
            body = self._read_json_body()
            result = asyncio.run(generate_icloud_aliases(body.get("profile_ids")))
            return self._write_json(result)

        if self.path == "/api/watcher/start":
            body = self._read_json_body()
            if watcher_instance and getattr(watcher_instance, 'running', False):
                return self._write_json({"ok": False, "message": "Watcher уже запущен"})

            subject = body.get("subject", "[Bitrue] Account Sign-in - Verification Code")
            body_kw = body.get("body_keyword", "")
            folder = body.get("folder", "bitrue")

            watcher_instance = CustomICloudWatcher(subject_keyword=subject, body_keyword=body_kw, folder=folder)

            def run_watcher():
                asyncio.run(watcher_instance.start(callback=live_callback))

            watcher_thread = threading.Thread(target=run_watcher, daemon=True)
            watcher_thread.start()

            return self._write_json({"ok": True, "message": "Watcher запущен"})

        if self.path == "/api/watcher/stop":
            if watcher_instance:
                watcher_instance.stop()
                watcher_instance = None
                watcher_thread = None
            return self._write_json({"ok": True, "message": "Watcher остановлен"})

        if self.path == "/api/checker":
            body = self._read_json_body()
            hours = int(body.get("hours", 24))
            keyword = body.get("keyword", "")
            result = asyncio.run(ICloudChecker.check(hours=hours, keyword=keyword))
            return self._write_json(result)

        if self.path == "/api/export/txt":
            body = self._read_json_body()
            profile_ids = body.get("profile_ids", [])
            if not profile_ids:
                return self._write_json({"ok": False, "message": "Нет выбранных профилей"}, 400)

            df = ensure_profiles_structure()
            text = ""
            for pid in profile_ids:
                row = df[df["Profile ID"].astype(str).str.strip() == str(pid).strip()]
                if not row.empty:
                    r = row.iloc[0]
                    alias = str(r.get("Alias Email", "")).strip()
                    pwd = str(r.get("Password", "")).strip()
                    if alias:
                        text += f"{pid}\n{alias}\n{pwd}\n\n"

            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="aliases_export.txt"')
            self.end_headers()
            self.wfile.write(text.encode("utf-8"))
            return

        if self.path == "/api/export/tg":
            body = self._read_json_body()
            profile_ids = body.get("profile_ids", [])
            if not profile_ids:
                return self._write_json({"ok": False, "message": "Нет выбранных профилей"}, 400)

            tg_token = os.getenv("TG_TOKEN", "")
            tg_chat_id = os.getenv("TG_CHAT_ID", "")
            if not tg_token or not tg_chat_id:
                return self._write_json({"ok": False, "message": "TG_TOKEN / TG_CHAT_ID не настроены"}, 400)

            df = ensure_profiles_structure()
            text = ""
            for pid in profile_ids:
                row = df[df["Profile ID"].astype(str).str.strip() == str(pid).strip()]
                if not row.empty:
                    r = row.iloc[0]
                    alias = str(r.get("Alias Email", "")).strip()
                    pwd = str(r.get("Password", "")).strip()
                    if alias:
                        text += f"{pid}\n{alias}\n{pwd}\n\n"

            if not text.strip():
                return self._write_json({"ok": False, "message": "Нет данных для экспорта"}, 400)

            try:
                asyncio.run(_send_to_telegram(tg_token, tg_chat_id, text.strip()))
                return self._write_json({"ok": True, "message": "Успешно отправлено в Telegram"})
            except Exception as e:
                return self._write_json({"ok": False, "message": f"Ошибка отправки в TG: {e}"}, 500)

        self._write_json({"ok": False, "message": "Not found"}, 404)

    def log_message(self, *args):
        pass

    def _get_default_html(self) -> str:
        return """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>iCloud HME Manager</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 30px; background: #0f172a; color: #e2e8f0; }
    .panel { background: #1e2937; padding: 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #334155; }
    button { padding: 10px 16px; margin: 5px; background: #3b82f6; color: white; border: none; border-radius: 6px; cursor: pointer; }
    button:hover { background: #2563eb; }
    input, select { padding: 8px; margin: 5px; background: #334155; color: white; border: 1px solid #475569; border-radius: 6px; }
    pre { background: #0b1220; padding: 12px; border-radius: 6px; max-height: 120px; overflow: auto; font-family: Consolas, monospace; white-space: pre-wrap; }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; }
    th, td { padding: 10px; border-bottom: 1px solid #334155; text-align: left; }
    .success { color: #22c55e; } .failed { color: #ef4444; } .idle { color: #94a3b8; }
  </style>
</head>
<body>
  <h2>iCloud Hide My Email Manager</h2>

  <!-- Live Log -->
  <div class="panel">
    <h3>Live Log (последние 3 строки)</h3>
    <pre id="liveLog">Ожидание событий...</pre>
  </div>

  <!-- Watcher -->
  <div class="panel">
    <h3>Watcher</h3>
    <input id="subject" value="[Bitrue] Account Sign-in - Verification Code" style="width:420px;">
    <input id="body_kw" placeholder="Body keyword (опционально)">
    <select id="folder">
      <option value="bitrue">Папка: bitrue</option>
      <option value="INBOX">Папка: INBOX</option>
    </select>
    <button onclick="startWatcher()">Запустить Watcher</button>
    <button onclick="stopWatcher()">Остановить Watcher</button>
  </div>

  <!-- Checker -->
  <div class="panel">
    <h3>Checker</h3>
    <select id="hours">
      <option value="2">2 часа</option>
      <option value="4">4 часа</option>
      <option value="24" selected>24 часа</option>
      <option value="48">48 часов</option>
    </select>
    <input id="keyword" placeholder="Ключевое слово в письме">
    <button onclick="runChecker()">Проверить письма</button>
    <pre id="checkerResult" style="max-height:250px;"></pre>
  </div>

  <!-- Основной блок с таблицей и кнопками действий -->
  <div class="panel">
    <h3>Профили</h3>
    <div style="margin-bottom:12px;">
      <button onclick="generateSelected()">Сгенерировать алиасы выбранным</button>
      <button onclick="exportTxt()">Export TXT (выбранные)</button>
      <button onclick="exportTg()">Отправить выбранные в Telegram</button>
    </div>

    <table>
      <thead>
        <tr>
          <th><input type="checkbox" onclick="toggleAll(this)"></th>
          <th>Profile ID</th>
          <th>Alias Email</th>
          <th>Main Email</th>
          <th>Password</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody id="body"></tbody>
    </table>
  </div>

  <script>
    let selected = new Set();

    async function refresh() {
      const res = await fetch("/api/state");
      const data = await res.json();
      const tbody = document.getElementById("body");
      tbody.innerHTML = "";
      data.accounts.forEach(acc => {
        const tr = document.createElement("tr");
        const checked = selected.has(acc.profile_id) ? "checked" : "";
        tr.innerHTML = `
          <td><input type="checkbox" data-pid="${acc.profile_id}" ${checked} onchange="toggle(this)"></td>
          <td>${acc.profile_id}</td>
          <td>${acc.alias || '—'}</td>
          <td>${acc.main_email || '—'}</td>
          <td>${acc.password || '—'}</td>
          <td class="${acc.status}">${acc.status}</td>
        `;
        tbody.appendChild(tr);
      });
    }

    async function refreshLog() {
      const res = await fetch("/api/log");
      const data = await res.json();
      document.getElementById("liveLog").textContent = data.log.join("\\n") || "Ожидание событий...";
    }

    function toggle(cb) {
      const pid = cb.dataset.pid;
      cb.checked ? selected.add(pid) : selected.delete(pid);
    }

    function toggleAll(cb) {
      document.querySelectorAll('input[type="checkbox"][data-pid]').forEach(c => {
        c.checked = cb.checked;
        toggle(c);
      });
    }

    async function generateSelected() {
      const ids = Array.from(selected);
      if (!ids.length) return alert("Выберите хотя бы один профиль");
      const res = await fetch("/api/aliases/generate", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({profile_ids: ids})
      }).then(r => r.json());
      alert(res.message);
      refresh();
    }

    async function exportTxt() {
      const ids = Array.from(selected);
      if (!ids.length) return alert("Выберите профили");
      const res = await fetch("/api/export/txt", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({profile_ids: ids})
      });
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url; a.download = "aliases_export.txt";
        a.click(); URL.revokeObjectURL(url);
      } else {
        alert("Ошибка экспорта TXT");
      }
    }

    async function exportTg() {
      const ids = Array.from(selected);
      if (!ids.length) return alert("Выберите профили");
      const res = await fetch("/api/export/tg", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({profile_ids: ids})
      }).then(r => r.json());
      alert(res.message);
    }

    async function startWatcher() {
      const subject = document.getElementById("subject").value;
      const body_kw = document.getElementById("body_kw").value;
      const folder = document.getElementById("folder").value;
      const res = await fetch("/api/watcher/start", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({subject, body_keyword: body_kw, folder})
      }).then(r => r.json());
      alert(res.message);
    }

    async function stopWatcher() {
      const res = await fetch("/api/watcher/stop", {method: "POST"}).then(r => r.json());
      alert(res.message);
    }

    async function runChecker() {
      const hours = document.getElementById("hours").value;
      const keyword = document.getElementById("keyword").value;
      const res = await fetch("/api/checker", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({hours: parseInt(hours), keyword})
      }).then(r => r.json());
      const pre = document.getElementById("checkerResult");
      if (res.ok) {
        pre.textContent = `Найдено: ${res.count}\n\n` + JSON.stringify(res.results, null, 2);
      } else {
        pre.textContent = "Ошибка: " + (res.message || "Неизвестная ошибка");
      }
    }

    setInterval(refresh, 4000);
    setInterval(refreshLog, 1500);
    refresh();
    refreshLog();
  </script>
</body>
</html>"""


def main():
    ensure_profiles_structure()
    if not ICLOUD_COOKIES_FILE.exists():
        logging.warning("Файл icloud_cookies.txt не найден!")

    server = ThreadingHTTPServer(("127.0.0.1", 8020), Handler)
    logging.info("iCloud HME Manager успешно запущен → http://127.0.0.1:8020")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Сервер остановлен.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
