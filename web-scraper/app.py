import os
import json
import warnings
from flask import Flask, render_template, request, jsonify
from scraper import scrape_website

warnings.filterwarnings("ignore")

app = Flask(__name__)
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    data = request.get_json()
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL не указана"}), 400
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    result = scrape_website(url)

    filename = url.replace("https://", "").replace("http://", "")
    filename = "".join(c if c.isalnum() or c in "-_." else "_" for c in filename)
    filename = filename[:100] + ".json"
    filepath = os.path.join(RESULTS_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    result["saved_as"] = filepath
    return jsonify(result)


@app.route("/api/history")
def api_history():
    files = []
    for f in sorted(os.listdir(RESULTS_DIR), reverse=True):
        if f.endswith(".json"):
            fp = os.path.join(RESULTS_DIR, f)
            stat = os.stat(fp)
            files.append({
                "filename": f,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })
    return jsonify(files[:50])


@app.route("/api/file/<filename>")
def api_file(filename):
    safe_name = os.path.basename(filename)
    fp = os.path.join(RESULTS_DIR, safe_name)
    if not os.path.abspath(fp).startswith(os.path.abspath(RESULTS_DIR)):
        return jsonify({"error": "Недопустимый путь"}), 400
    if not os.path.exists(fp):
        return jsonify({"error": "Файл не найден"}), 404
    with open(fp, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


if __name__ == "__main__":
    print("=" * 60)
    print("  ВЕБ-СКРАПЕР СЕРВИС")
    print("  Откройте: http://localhost:5555")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5555, debug=False)
