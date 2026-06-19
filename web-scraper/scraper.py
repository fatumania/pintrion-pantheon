import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import json
import re
import os
from datetime import datetime


def scrape_website(url, depth=1):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15, verify=False)
        resp.encoding = resp.apparent_encoding or "utf-8"
    except Exception as e:
        return {"error": str(e), "url": url}

    try:
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception:
        soup = BeautifulSoup(resp.text, "html.parser")
    parsed = urlparse(url)
    base_domain = f"{parsed.scheme}://{parsed.netloc}"

    structured_data = extract_structured_data(soup)

    result = {
        "url": url,
        "final_url": resp.url,
        "status_code": resp.status_code,
        "scraped_at": datetime.now().isoformat(),
        "page_info": extract_page_info(soup, resp),
        "meta_tags": extract_meta(soup),
        "headings": extract_headings(soup),
        "links": extract_links(soup, base_domain),
        "images": extract_images(soup, base_domain),
        "forms": extract_forms(soup),
        "scripts": extract_scripts(soup),
        "text_content": extract_text(soup),
        "structured_data": structured_data,
        "social_links": extract_social_links(soup),
        "contact_info": extract_contact_info(resp.text),
        "open_graph": extract_open_graph(soup),
        "twitter_cards": extract_twitter_cards(soup),
        "raw_html": resp.text[:50000],
    }

    return result


def extract_page_info(soup, resp):
    info = {
        "title": soup.title.string.strip() if soup.title and soup.title.string else "",
        "charset": "",
        "language": "",
        "content_length": len(resp.text),
        "headers": dict(resp.headers),
    }
    charset_tag = soup.find("meta", charset=True)
    if charset_tag:
        info["charset"] = charset_tag.get("charset", "")
    html_tag = soup.find("html")
    if html_tag:
        info["language"] = html_tag.get("lang", "")
    return info


def extract_meta(soup):
    tags = []
    for meta in soup.find_all("meta"):
        tag = {}
        for attr in ["name", "property", "http-equiv", "content", "charset"]:
            val = meta.get(attr)
            if val:
                tag[attr] = val
        if tag:
            tags.append(tag)
    return tags


def extract_headings(soup):
    headings = {}
    for level in range(1, 7):
        tag = f"h{level}"
        found = soup.find_all(tag)
        if found:
            headings[tag] = [h.get_text(strip=True) for h in found]
    return headings


def extract_links(soup, base_domain):
    internal = []
    external = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(base_domain, href)
        text = a.get_text(strip=True)[:200]
        link = {"href": full, "text": text, "original": href}
        if urlparse(full).netloc == urlparse(base_domain).netloc:
            internal.append(link)
        else:
            external.append(link)
    return {
        "internal": internal[:500],
        "external": external[:500],
        "total_internal": len(internal),
        "total_external": len(external),
    }


def extract_images(soup, base_domain):
    images = []
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src:
            full = urljoin(base_domain, src)
        else:
            full = ""
        images.append({
            "src": full,
            "alt": img.get("alt", ""),
            "title": img.get("title", ""),
            "width": img.get("width", ""),
            "height": img.get("height", ""),
            "original": src,
        })
    return images[:200]


def extract_forms(soup):
    forms = []
    for form in soup.find_all("form"):
        inputs = []
        for inp in form.find_all(["input", "textarea", "select"]):
            inputs.append({
                "tag": inp.name,
                "type": inp.get("type", ""),
                "name": inp.get("name", ""),
                "id": inp.get("id", ""),
                "placeholder": inp.get("placeholder", ""),
                "required": inp.get("required") is not None,
            })
        forms.append({
            "action": form.get("action", ""),
            "method": form.get("method", "GET"),
            "id": form.get("id", ""),
            "inputs": inputs,
        })
    return forms


def extract_scripts(soup):
    scripts = []
    for script in soup.find_all("script"):
        src = script.get("src", "")
        if src:
            scripts.append({"type": "external", "src": src})
        else:
            text = script.string or ""
            if text.strip():
                scripts.append({"type": "inline", "preview": text[:500]})
    return scripts


def extract_text(soup):
    for tag in soup(["script", "style", "meta", "link", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)[:100000]


def extract_structured_data(soup):
    data = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            content = json.loads(script.string)
            data.append(content)
        except (json.JSONDecodeError, TypeError):
            pass
    return data


def extract_social_links(soup):
    social = {}
    patterns = {
        "facebook": r"facebook\.com|fb\.com|fb\.me",
        "twitter": r"twitter\.com|x\.com",
        "instagram": r"instagram\.com",
        "linkedin": r"linkedin\.com",
        "youtube": r"youtube\.com|youtu\.be",
        "tiktok": r"tiktok\.com",
        "telegram": r"t\.me|telegram\.me|telegram\.org",
        "whatsapp": r"wa\.me|whatsapp\.com",
        "vkontakte": r"vk\.com|vkontakte\.ru",
        "odnoklassniki": r"ok\.ru|odnoklassniki\.ru",
    }
    all_hrefs = [a.get("href", "") for a in soup.find_all("a", href=True)]
    all_text = " ".join(all_hrefs)
    for name, pattern in patterns.items():
        matches = [h for h in all_hrefs if re.search(pattern, h, re.I)]
        if matches:
            social[name] = list(set(matches))[:5]
    return social


def extract_contact_info(html):
    emails = list(set(re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", html)))
    phones = list(set(re.findall(r"[\+]?[\d\s\-\(\)]{7,20}", html)))
    phones = [p.strip() for p in phones if len(re.sub(r"\D", "", p)) >= 7]
    return {"emails": emails[:20], "phones": phones[:20]}


def extract_open_graph(soup):
    og = {}
    for meta in soup.find_all("meta", property=re.compile(r"^og:")):
        prop = meta.get("property", "")
        content = meta.get("content", "")
        if prop and content:
            og[prop] = content
    return og


def extract_twitter_cards(soup):
    tc = {}
    for meta in soup.find_all("meta", attrs={"name": re.compile(r"^twitter:")}):
        name = meta.get("name", "")
        content = meta.get("content", "")
        if name and content:
            tc[name] = content
    return tc
