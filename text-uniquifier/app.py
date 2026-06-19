import os
import json
import time
import threading
import requests
from flask import Flask, render_template, request, jsonify, send_file
from datetime import datetime

app = Flask(__name__)

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")

state = {
    "processing": False,
    "paused": False,
    "progress": {"current": 0, "total": 0, "status": "idle"},
    "results": [],
}

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
FREE_MODELS = [
    "openai/gpt-oss-20b:free",
    "openai/gpt-oss-120b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "nvidia/nemotron-nano-9b-v2:free",
    "google/gemma-4-31b-it:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
]

PROMPTS = {
    "light": """Перепиши текст ниже так, чтобы он был уникальным. Сохрани смысл и суть полностью. Меняй структуру предложений, используй синонимы, перефразируй. Текст должен звучать естественно и профессионально. Не добавляй ничего лишнего. Верни ТОЛЬКО переписанный текст, без пояснений.

Текст:
{text}""",
    "medium": """Твоя задача — сделать этот текст полностью уникальным. Перепиши его так, чтобы:
1. Каждое предложение было перефразировано
2. Синонимы заменены где возможно
3. Структура предложений изменена
4. Смысл и ключевые факты сохранены
5. Текст читался естественно

Верни ТОЛЬКО готовый текст, без комментариев и пояснений.

Исходный текст:
{text}""",
    "heavy": """Создай полностью уникальную версию этого текста. Правила:
- Полностью перепиши каждое предложение
- Используй другие слова и конструкции
- Можешь менять порядок предложений
- Сохрани ВСЕ факты, цифры, названия, ссылки
- Текст должен быть того же размера (±20%)
- Стиль: профессиональный, понятный
- Запрещено копировать фразы дословно

Верни ТОЛЬКО новый текст.

Оригинал:
{text}""",
}


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"api_key": "", "model": "openai/gpt-oss-20b:free", "prompt_type": "medium"}


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def call_ai(api_key, model, text, prompt_type="medium"):
    prompt_template = PROMPTS.get(prompt_type, PROMPTS["medium"])
    prompt = prompt_template.format(text=text)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:5557",
    }

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2048,
        "temperature": 0.9,
        "top_p": 0.95,
    }

    last_err = None
    for attempt in range(3):
        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=60)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 5))
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            if "choices" in data and data["choices"]:
                content = data["choices"][0].get("message", {}).get("content", "")
                if content and content.strip():
                    return content.strip()
                raise Exception("Пустой ответ от модели")
            raise Exception(f"Ошибка API: {data.get('error', {}).get('message', 'Unknown')}")
        except requests.exceptions.HTTPError as e:
            last_err = e
            if attempt < 2:
                time.sleep(3)
    raise Exception(f"Не удалось получить ответ: {last_err}")


def generate_variations(api_key, model, text, count, prompt_type, delay=1.5):
    state["processing"] = True
    state["paused"] = False
    state["results"] = []
    state["progress"] = {"current": 0, "total": count, "status": "running", "started_at": datetime.now().isoformat()}

    for i in range(count):
        while state["paused"] and state["processing"]:
            state["progress"]["status"] = "paused"
            time.sleep(1)

        if not state["processing"]:
            state["progress"]["status"] = "stopped"
            break

        state["progress"]["current"] = i + 1
        state["progress"]["status"] = "running"

        try:
            result = call_ai(api_key, model, text, prompt_type)
            state["results"].append({"index": i + 1, "text": result, "status": "ok"})
        except Exception as e:
            state["results"].append({"index": i + 1, "text": "", "status": "error", "error": str(e)})

        if i < count - 1 and state["processing"]:
            time.sleep(delay)

    state["progress"]["status"] = "done" if state["processing"] else "stopped"
    state["progress"]["finished_at"] = datetime.now().isoformat()
    state["processing"] = False
    state["paused"] = False

    if state["results"]:
        filename = f"unique_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filepath = os.path.join(RESULTS_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            for r in state["results"]:
                if r["status"] == "ok" and r["text"]:
                    f.write(r["text"] + "\n===VARIANT===\n")
        state["last_file"] = filename


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET"])
def get_config():
    cfg = load_config()
    return jsonify({"api_key": cfg.get("api_key", ""), "model": cfg.get("model", FREE_MODELS[0]), "prompt_type": cfg.get("prompt_type", "medium"), "models": FREE_MODELS})


@app.route("/api/config", methods=["POST"])
def set_config():
    data = request.get_json()
    cfg = load_config()
    cfg.update(data)
    save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json()
    cfg = load_config()

    api_key = data.get("api_key") or cfg.get("api_key", "")
    if not api_key:
        return jsonify({"error": "Введите API ключ OpenRouter"}), 400

    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "Введите текст для уникализации"}), 400

    count = min(500, max(1, int(data.get("count", 100))))
    model = data.get("model") or cfg.get("model", FREE_MODELS[0])
    prompt_type = data.get("prompt_type") or cfg.get("prompt_type", "medium")
    delay = max(1, int(data.get("delay", 2)))

    if state["processing"]:
        return jsonify({"error": "Процесс уже запущен"}), 400

    t = threading.Thread(target=generate_variations, args=(api_key, model, text, count, prompt_type, delay), daemon=True)
    t.start()

    return jsonify({"ok": True, "total": count})


@app.route("/api/status")
def api_status():
    resp = {
        **state["progress"],
        "generated": sum(1 for r in state["results"] if r["status"] == "ok"),
        "errors": sum(1 for r in state["results"] if r["status"] == "error"),
        "paused": state["paused"],
    }
    return jsonify(resp)


@app.route("/api/pause", methods=["POST"])
def api_pause():
    state["paused"] = not state["paused"]
    return jsonify({"paused": state["paused"]})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    state["processing"] = False
    state["paused"] = False
    return jsonify({"ok": True})


@app.route("/api/results")
def api_results():
    return jsonify(state["results"][-100:])


@app.route("/api/download/<filename>")
def api_download(filename):
    safe_name = os.path.basename(filename)
    fp = os.path.join(RESULTS_DIR, safe_name)
    if not os.path.abspath(fp).startswith(os.path.abspath(RESULTS_DIR)):
        return jsonify({"error": "Недопустимый путь"}), 400
    if not os.path.exists(fp):
        return jsonify({"error": "Файл не найден"}), 404
    return send_file(fp, as_attachment=True, download_name=safe_name)


@app.route("/api/download-latest")
def api_download_latest():
    last = state.get("last_file")
    if not last:
        return jsonify({"error": "Нет файла"}), 404
    fp = os.path.join(RESULTS_DIR, last)
    if not os.path.exists(fp):
        return jsonify({"error": "Файл не найден"}), 404
    return send_file(fp, as_attachment=True, download_name=last)


@app.route("/api/files")
def api_files():
    files = []
    for f in sorted(os.listdir(RESULTS_DIR), reverse=True):
        if f.endswith(".txt"):
            fp = os.path.join(RESULTS_DIR, f)
            with open(fp, "r", encoding="utf-8") as fh:
                content = fh.read()
            count = len([b for b in content.split("===VARIANT===") if b.strip()])
            files.append({"filename": f, "variants": count})
    return jsonify(files[:20])


@app.route("/api/file/<filename>")
def api_file(filename):
    safe_name = os.path.basename(filename)
    fp = os.path.join(RESULTS_DIR, safe_name)
    if not os.path.abspath(fp).startswith(os.path.abspath(RESULTS_DIR)):
        return jsonify({"error": "Недопустимый путь"}), 400
    if not os.path.exists(fp):
        return jsonify({"error": "not found"}), 404
    with open(fp, "r", encoding="utf-8") as fh:
        content = fh.read()
    variants = [v.strip() for v in content.split("===VARIANT===") if v.strip()]
    return jsonify({"filename": safe_name, "variants": variants, "count": len(variants)})


@app.route("/api/test-model", methods=["POST"])
def api_test_model():
    cfg = load_config()
    api_key = cfg.get("api_key", "")
    if not api_key:
        return jsonify({"error": "Введите API ключ"}), 400
    try:
        result = call_ai(api_key, cfg.get("model", FREE_MODELS[0]), "Привет, как дела?", "light")
        return jsonify({"ok": True, "model": cfg.get("model"), "response": result[:200]})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


if __name__ == "__main__":
    print("=" * 60)
    print("  УНИКАЛИЗАТОР ТЕКСТОВ v1.1")
    print("  Откройте: http://localhost:5557")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5557, debug=False)
