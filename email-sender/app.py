import smtplib
import ssl
import time
import json
import os
import re
import csv
import random
import threading
import io
import requests as http_requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta

app = Flask(__name__)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

state = {
    "sending": False,
    "paused": False,
    "stop_requested": False,
    "thread": None,
    "progress": {"current": 0, "total": 0, "sent": 0, "failed": 0, "skipped": 0, "status": "idle"},
    "live_log": [],
    "log_lock": threading.Lock(),
}

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def parse_emails(text):
    raw = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for sep in [",", ";", "\t"]:
            if sep in line:
                raw.extend(p.strip() for p in line.split(sep) if "@" in p)
                line = ""
                break
        if line and "@" in line:
            raw.append(line)
    seen = set()
    result = []
    for e in raw:
        e = e.lower().strip()
        if e not in seen and EMAIL_REGEX.match(e):
            seen.add(e)
            result.append(e)
    return result


def validate_email(email):
    return bool(EMAIL_REGEX.match(email))


UNIQUIZER_URL = "http://localhost:5557"


def fetch_variants():
    try:
        r = http_requests.get(f"{UNIQUIZER_URL}/api/files", timeout=5)
        files = r.json()
        if files:
            r2 = http_requests.get(f"{UNIQUIZER_URL}/api/file/{files[0]['filename']}", timeout=10)
            data = r2.json()
            variants = data.get("variants", [])
            if variants:
                return variants
    except Exception:
        pass
    return []


def send_one_email(smtp_host, smtp_port, smtp_user, smtp_pass, from_name, to_addr, subject, body, is_html, reply_to=None, retries=2):
    msg = MIMEMultipart("alternative")
    msg["From"] = formataddr((from_name, smtp_user)) if from_name else smtp_user
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["X-Mailer"] = "MailerService/2.0"
    if reply_to:
        msg["Reply-To"] = reply_to

    if is_html:
        msg.attach(MIMEText(body, "html", "utf-8"))
        plain = re.sub(r"<[^>]+>", "", body)
        msg.attach(MIMEText(plain, "plain", "utf-8"))
    else:
        msg.attach(MIMEText(body, "plain", "utf-8"))

    context = ssl.create_default_context()
    last_err = None
    for attempt in range(retries + 1):
        try:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
                server.starttls(context=context)
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, to_addr, msg.as_string())
            return True, None
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(3)
    return False, str(last_err)


def send_loop(cfg, emails, subject, body, is_html, delay_min, delay_max, from_name, reply_to, start_at, variants=None):
    if start_at:
        try:
            target = datetime.fromisoformat(start_at)
            wait_sec = (target - datetime.now()).total_seconds()
            if wait_sec > 0:
                state["progress"]["status"] = f"Ожидание до {target.strftime('%H:%M')}"
                while wait_sec > 0 and state["sending"]:
                    time.sleep(min(wait_sec, 5))
                    wait_sec = (target - datetime.now()).total_seconds()
        except Exception:
            pass

    variant_count = len(variants) if variants else 0
    base_body = body if body.strip() else ""

    state["progress"] = {
        "current": 0, "total": len(emails), "sent": 0, "failed": 0, "skipped": 0,
        "status": "running", "started_at": datetime.now().isoformat(),
    }
    state["live_log"] = []

    for i, email in enumerate(emails):
        while state["paused"] and state["sending"]:
            state["progress"]["status"] = "paused"
            time.sleep(1)

        if state["stop_requested"] or not state["sending"]:
            state["progress"]["status"] = "stopped"
            break

        state["progress"]["current"] = i + 1
        state["progress"]["status"] = "running"
        state["progress"]["current_email"] = email

        if not validate_email(email):
            state["progress"]["skipped"] += 1
            entry = {"email": email, "status": "skipped", "reason": "invalid", "time": datetime.now().isoformat()}
            with state["log_lock"]:
                state["live_log"].append(entry)
            continue

        email_body = base_body
        if variants:
            email_body = variants[i % variant_count]

        ok, err = send_one_email(
            cfg["smtp_host"], cfg["smtp_port"], cfg["smtp_user"], cfg["smtp_pass"],
            from_name, email, subject, email_body, is_html, reply_to, retries=2,
        )
        if ok:
            state["progress"]["sent"] += 1
            entry = {"email": email, "status": "ok", "time": datetime.now().isoformat()}
        else:
            state["progress"]["failed"] += 1
            entry = {"email": email, "status": "error", "error": err, "time": datetime.now().isoformat()}

        with state["log_lock"]:
            state["live_log"].append(entry)
            if len(state["live_log"]) > 200:
                state["live_log"] = state["live_log"][-200:]

        log_file = state.get("log_file", "")
        if log_file:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        if i < len(emails) - 1 and state["sending"]:
            delay = random.uniform(delay_min, delay_max)
            state["progress"]["next_in"] = round(delay)
            time.sleep(delay)

    state["progress"]["status"] = "done" if state["sending"] else "stopped"
    state["progress"]["finished_at"] = datetime.now().isoformat()
    state["progress"].pop("current_email", None)
    state["progress"].pop("next_in", None)
    state["sending"] = False
    state["paused"] = False
    state["stop_requested"] = False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET"])
def get_config():
    cfg = load_config()
    return jsonify({
        "smtp_host": cfg.get("smtp_host", "smtp-mail.outlook.com"),
        "smtp_port": cfg.get("smtp_port", 587),
        "smtp_user": cfg.get("smtp_user", ""),
        "smtp_pass": cfg.get("smtp_pass", ""),
        "from_name": cfg.get("from_name", ""),
        "reply_to": cfg.get("reply_to", ""),
    })


@app.route("/api/config", methods=["POST"])
def set_config():
    data = request.get_json()
    cfg = load_config()
    cfg.update(data)
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/send", methods=["POST"])
def api_send():
    data = request.get_json()
    cfg = load_config()
    if not cfg.get("smtp_user") or not cfg.get("smtp_pass"):
        return jsonify({"error": "Настройте SMTP в разделе Настройки"}), 400

    emails_text = data.get("emails", "")
    emails = parse_emails(emails_text)
    if not emails:
        return jsonify({"error": "Нет валидных email адресов"}), 400

    subject = data.get("subject", "").strip()
    body = data.get("body", "")
    is_html = data.get("is_html", False)
    delay_min = max(10, int(data.get("delay_min", 120)))
    delay_max = max(delay_min, int(data.get("delay_max", delay_min + 30)))
    from_name = data.get("from_name", cfg.get("from_name", ""))
    reply_to = data.get("reply_to", cfg.get("reply_to", ""))
    start_at = data.get("start_at", "")

    if not subject:
        return jsonify({"error": "Введите тему письма"}), 400
    if not body.strip() and not data.get("use_variants"):
        return jsonify({"error": "Введите текст письма"}), 400
    if state["sending"]:
        return jsonify({"error": "Рассылка уже идёт"}), 400

    variants = None
    use_variants = data.get("use_variants", False)
    if use_variants:
        variants = fetch_variants()
        if not variants:
            return jsonify({"error": "Нет вариантов из уникализатора. Сначала сгенерируйте их."}), 400

    log_file = os.path.join(LOGS_DIR, f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")
    state["log_file"] = log_file
    state["sending"] = True
    state["paused"] = False
    state["stop_requested"] = False

    t = threading.Thread(
        target=send_loop,
        args=(cfg, emails, subject, body, is_html, delay_min, delay_max, from_name, reply_to, start_at or None, variants),
        daemon=True,
    )
    t.start()
    state["thread"] = t

    return jsonify({"ok": True, "total": len(emails), "delay_min": delay_min, "delay_max": delay_max, "unique": use_variants})


@app.route("/api/status")
def api_status():
    return jsonify(state["progress"])


@app.route("/api/live-log")
def api_live_log():
    with state["log_lock"]:
        return jsonify(state["live_log"][-50:])


@app.route("/api/pause", methods=["POST"])
def api_pause():
    state["paused"] = not state["paused"]
    return jsonify({"paused": state["paused"]})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    state["stop_requested"] = True
    state["sending"] = False
    state["paused"] = False
    return jsonify({"ok": True})


@app.route("/api/validate", methods=["POST"])
def api_validate():
    data = request.get_json()
    emails = parse_emails(data.get("emails", ""))
    invalid = [e for e in emails if not validate_email(e)]
    return jsonify({"total": len(emails), "valid": len(emails) - len(invalid), "invalid": invalid[:20]})


@app.route("/api/variants-count")
def api_variants_count():
    try:
        r = http_requests.get(f"{UNIQUIZER_URL}/api/files", timeout=5)
        files = r.json()
        if files:
            return jsonify({"count": files[0]["variants"], "filename": files[0]["filename"], "available": True})
    except Exception:
        pass
    return jsonify({"count": 0, "available": False})


@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "Файл не загружен"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Файл не выбран"}), 400

    content = f.read().decode("utf-8", errors="ignore")
    emails = []

    if f.filename.endswith(".csv"):
        reader = csv.reader(io.StringIO(content))
        for row in reader:
            for cell in row:
                cell = cell.strip().strip('"')
                if "@" in cell:
                    emails.append(cell)
    elif f.filename.endswith(".txt"):
        emails = parse_emails(content)
    else:
        emails = parse_emails(content)

    emails = list(dict.fromkeys(e.lower() for e in emails if validate_email(e)))
    return jsonify({"emails": emails, "count": len(emails)})


@app.route("/api/logs")
def api_logs():
    files = []
    for f in sorted(os.listdir(LOGS_DIR), reverse=True):
        if f.endswith(".jsonl"):
            fp = os.path.join(LOGS_DIR, f)
            with open(fp, "r", encoding="utf-8") as fh:
                count = sum(1 for _ in fh)
            files.append({"filename": f, "entries": count})
    return jsonify(files[:30])


@app.route("/api/log/<filename>")
def api_log(filename):
    safe_name = os.path.basename(filename)
    fp = os.path.join(LOGS_DIR, safe_name)
    if not os.path.abspath(fp).startswith(os.path.abspath(LOGS_DIR)):
        return jsonify({"error": "Недопустимый путь"}), 400
    if not os.path.exists(fp):
        return jsonify({"error": "not found"}), 404
    entries = []
    with open(fp, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return jsonify(entries)


@app.route("/api/test", methods=["POST"])
def api_test():
    cfg = load_config()
    if not cfg.get("smtp_user") or not cfg.get("smtp_pass"):
        return jsonify({"error": "Настройте SMTP"}), 400
    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=15) as server:
            server.starttls(context=context)
            server.login(cfg["smtp_user"], cfg["smtp_pass"])
        return jsonify({"ok": True, "message": "Подключение успешно"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


if __name__ == "__main__":
    print("=" * 60)
    print("  РАССЫЛЬЩИК ПОЧТЫ v2.1")
    print("  Откройте: http://localhost:5556")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5556, debug=False)
