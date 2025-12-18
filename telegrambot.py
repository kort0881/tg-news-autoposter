import os
import re
import json
import asyncio
import random
from datetime import datetime
from typing import List, Dict, Optional

import requests
import feedparser
import urllib.parse
from aiogram import Bot
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile
from openai import OpenAI

# ---------------- CONFIG (Все данные через ENV) ----------------

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
# По умолчанию пусто, можно задать в секретах GitHub если нужно
FOOTER_TEXT = os.getenv("FOOTER_TEXT", "") 

if not all([OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, CHANNEL_ID]):
    raise ValueError("❌ Не все необходимые ENV переменные установлены!")

bot = Bot(
    token=TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

POSTED_FILE = "posted_articles.json"
RETENTION_DAYS = 7

# Источник GitHub Trending
GITHUB_RSS = "https://mshibanami.github.io/GitHubTrendingRSS/github_trending_all_daily.xml"

# ============ КЛЮЧЕВЫЕ СЛОВА (ТЕМАТИКА) ============

REQUIRE_KEYWORDS = [
    "vpn", "прокси", "туннель", "proxy", "tunnel", "шифрование", "encrypt", 
    "приватность", "privacy", "безопасность", "security", "защита данных",
    "интернет", "internet", "сеть", "network", "протокол", "protocol",
    "анонимность", "anonymous", "скрытие", "incognito", "скрытый", "hidden",
    "цензура", "блокировка", "blocking", "censorship", "restrict", "ограничение",
    "dns", "dpi", "фильтр", "filter", "обход", "bypass", "роскомнадзор", "ркн",
    "трафик", "traffic", "пакет", "packet", "соединение", "connection",
    "tor", "darknet", "wireguard", "openvpn", "shadowsocks", "обфускация",
    "нейросеть", "ии", "ai", "llm", "gpt", "claude", "chatgpt",
    "уязвимость", "vulnerability", "эксплойт", "exploit", "zero-day",
    "malware", "вредонос", "кибератака", "взлом", "security patch", "notepad++"
]

EXCLUDE_KEYWORDS = [
    "теннис", "футбол", "хоккей", "баскетбол", "спорт", "матч", "команда",
    "игра", "геймплей", "gameplay", "dungeon", "playstation", "xbox", "steam",
    "кино", "фильм", "сериал", "музыка", "концерт", "актер", "режиссер",
    "coca-cola", "pepsi", "tesla", "акции", "биржа", "инвестор", "выручка",
    "выборы", "президент", "парламент", "закон", "болезнь", "вирус", "covid",
    "биткойн", "bitcoin", "крипто", "crypto", "блокчейн", "автомобиль", "машина"
]

RUSSIA_KEYWORDS = ["россия", "рф", "российск", "москв", "ркн"]

# ---------------- STATE ----------------

if os.path.exists(POSTED_FILE):
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        try:
            posted_data = json.load(f)
            posted_articles = {item["id"]: item.get("timestamp") for item in posted_data}
        except Exception:
            posted_articles = {}
else:
    posted_articles = {}

def save_posted_articles():
    data = [{"id": i, "timestamp": ts} for i, ts in posted_articles.items()]
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def clean_old_posts():
    global posted_articles
    cutoff = datetime.now().timestamp() - (RETENTION_DAYS * 86400)
    posted_articles = {i: ts for i, ts in posted_articles.items() if ts is None or ts > cutoff}
    save_posted_articles()

def save_posted(article_id: str):
    posted_articles[article_id] = datetime.now().timestamp()
    save_posted_articles()

# ---------------- HELPERS / PARSERS ----------------

def safe_get(url: str) -> Optional[str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        return resp.text if resp.status_code == 200 else None
    except: return None

def clean_text(text: str) -> str:
    return " ".join(text.replace("\n", " ").replace("\r", " ").split())

def load_3dnews() -> List[Dict]:
    html = safe_get("https://3dnews.ru/")
    if not html: return []
    articles = []
    for part in html.split('<a href="/')[1:15]:
        try:
            href = part[:part.find('"')]
            title_chunk = part[part.find(">") + 1 : part.find("</a>")]
            title = clean_text(title_chunk)
            link = "https://3dnews.ru/" + href.lstrip("/")
            articles.append({"id": link, "title": title, "summary": "", "link": link, "source": "3DNews", "published_parsed": datetime.now()})
        except: continue
    return articles

def load_rss(url: str, source: str) -> List[Dict]:
    articles = []
    feed = feedparser.parse(url)
    for entry in feed.entries[:30]:
        link = entry.get("link", "")
        title = clean_text(entry.get("title") or "")
        summary = clean_text(entry.get("summary") or entry.get("description") or "")[:500]
        if link and title:
            articles.append({"id": link, "title": title, "summary": summary, "link": link, "source": source, "published_parsed": datetime.now()})
    return articles

def load_vc_new() -> List[Dict]:
    html = safe_get("https://vc.ru/new")
    if not html: return []
    articles = []
    for match in re.finditer(r'href="(/[^"]+)"[^>]*>\s*<span[^>]*>([^<]+)</span>', html):
        link = "https://vc.ru" + match.group(1).lstrip("/")
        articles.append({"id": link, "title": clean_text(match.group(2)), "summary": "", "link": link, "source": "VC.ru New", "published_parsed": datetime.now()})
        if len(articles) >= 15: break
    return articles

def load_articles_from_sites() -> List[Dict]:
    all_arts = []
    all_arts.extend(load_3dnews())
    all_arts.extend(load_vc_new())
    all_arts.extend(load_rss("https://xakep.ru/feed/", "Xakep.ru"))
    all_arts.extend(load_rss(GITHUB_RSS, "GitHub Trending"))
    return all_arts

# ============ FILTERING ============

def check_require_keywords(text: str) -> bool:
    text_lower = text.lower()
    score = sum(1 for kw in REQUIRE_KEYWORDS if kw in text_lower)
    return score >= 2 

def filter_articles(articles: List[Dict]) -> List[Dict]:
    suitable_ru, suitable_world = [], []
    for e in articles:
        if e["id"] in posted_articles: continue
        text = f"{e['title']} {e['summary']}".lower()
        if any(kw in text for kw in EXCLUDE_KEYWORDS): continue
        if not check_require_keywords(text): continue
        
        if any(kw in text for kw in RUSSIA_KEYWORDS): suitable_ru.append(e)
        else: suitable_world.append(e)

    target = suitable_ru if suitable_ru else suitable_world
    target.sort(key=lambda x: x["published_parsed"], reverse=True)
    return target

# ============ OPENAI ============

def short_summary(title: str, summary: str, link: str) -> Optional[str]:
    prompt = (
        f"Статья: {title}. {summary}\n\n"
        "Сделай новостной пост для Telegram на русском:\n"
        "- Объём: 400-500 символов.\n"
        "- Дай 2-4 конкретных практических совета для читателя.\n"
        "- Удали рекламу и лишние слова.\n"
        "- В конце добавь 2-3 релевантных хештега.\n"
        "- Не упоминай никакие каналы или внешние ссылки."
    )
    try:
        res = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5
        )
        core_text = res.choices[0].message.content.strip()
        
        # Сборка финального текста с источником и опциональным подвалом из ENV
        final_text = f"{core_text}\n\nИсточник: {link}{FOOTER_TEXT}"
        
        return final_text[:1024]
    except: return None

# ============ IMAGE & POSTING ============

def generate_image(title: str) -> Optional[str]:
    prompt = f"abstract technology concept about: {title[:100]}, clean minimal style, no text"
    try:
        encoded = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?seed={random.randint(1,99999)}"
        resp = requests.get(url, timeout=60)
        if resp.status_code == 200:
            fname = f"img_{random.randint(1,999)}.jpg"
            with open(fname, "wb") as f: f.write(resp.content)
            return fname
    except: return None

async def autopost():
    clean_old_posts()
    candidates = filter_articles(load_articles_from_sites())
    if not candidates: return

    for art in candidates[:5]:
        text = short_summary(art["title"], art["summary"], art["link"])
        if not text: continue

        try:
            img = generate_image(art["title"])
            if img:
                await bot.send_photo(chat_id=CHANNEL_ID, photo=FSInputFile(img), caption=text)
                os.remove(img)
            else:
                await bot.send_message(chat_id=CHANNEL_ID, text=text)
            
            save_posted(art["id"])
            break 
        except Exception as e:
            print(f"❌ Ошибка: {e}")

async def main():
    try: await autopost()
    finally: await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
