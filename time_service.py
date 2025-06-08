#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import sqlite3
import json
from datetime import datetime
from typing import List, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI

import feedparser
import aiohttp
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ======================================================
#  Инициализация FastAPI и CORS (по желанию)
# ======================================================

app = FastAPI(title="RSS Watcher Service")

# Если нужно открывать API извне (Postman, браузер), разрешаем CORS:
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],            # разрешить любые домены (в проде можно сузить)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================================================
#  Настройки и глобальные переменные
# ======================================================

DB_PATH = "news.db"             # файл sqlite3
FETCH_INTERVAL_SECONDS = 600     # 10 минут

# ======================================================
#  Pydantic-модели для запросов/ответов
# ======================================================

class FeedIn(BaseModel):
    url: str
    description: Optional[str] = ""

class FeedOut(BaseModel):
    id: int
    url: str
    description: Optional[str]

class KeywordIn(BaseModel):
    keyword: str

class KeywordOut(BaseModel):
    id: int
    keyword: str

class NewsItem(BaseModel):
    id: int
    feed_id: int
    title: str
    link: str
    summary: Optional[str]
    published: Optional[str]
    matched_keyword: str

# ======================================================
#  Инициализация и управление SQLite
# ======================================================

def get_db_connection():
    """
    Возвращает соединение SQLite (с отключенной проверкой thread)
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

db = get_db_connection()

def init_db():
    """
    Создаёт необходимые таблицы, если их ещё нет.
    """
    cursor = db.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS feeds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL UNIQUE,
        description TEXT
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS keywords (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword TEXT NOT NULL UNIQUE
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS found_news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        feed_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        link TEXT NOT NULL UNIQUE,
        summary TEXT,
        published TEXT,
        matched_keyword TEXT NOT NULL,
        FOREIGN KEY(feed_id) REFERENCES feeds(id)
    );
    """)
    db.commit()

# Однократно инициализируем БД при импорте модуля
init_db()

# ======================================================
#  Утилиты для работы с базой
# ======================================================

def fetch_all_feeds() -> List[sqlite3.Row]:
    """
    Возвращает список всех RSS-лент из таблицы feeds.
    """
    cursor = db.execute("SELECT id, url, description FROM feeds")
    return cursor.fetchall()

def fetch_all_keywords() -> List[str]:
    """
    Возвращает список всех ключевых слов (в нижнем регистре) из таблицы keywords.
    """
    cursor = db.execute("SELECT keyword FROM keywords")
    return [row["keyword"] for row in cursor.fetchall()]

def insert_news_item(feed_id: int, title: str, link: str, summary: str, published: str, matched_keyword: str) -> bool:
    """
    Вставляет одну новость в found_news, если такого link-а ещё нет.
    Возвращает True, если вставлено, False — если уже было в базе (UNIQUE link).
    """
    try:
        db.execute(
            "INSERT INTO found_news(feed_id, title, link, summary, published, matched_keyword) VALUES (?, ?, ?, ?, ?, ?)",
            (feed_id, title, link, summary, published, matched_keyword)
        )
        db.commit()
        return True
    except sqlite3.IntegrityError:
        # Например, если ссылка уже есть (UNIQUE constraint), игнорируем
        return False

# ======================================================
#  Асинхронная функция сбора RSS
# ======================================================

async def fetch_feed(session: aiohttp.ClientSession, feed_id: int, feed_url: str, keywords: List[str]) -> int:
    """
    Скачивает одну RSS-ленту, парсит её и ищет ключевые слова.
    Если находит совпадение — сохраняет новость в БД.

    Возвращает количество вставленных записей (новых новостей) из этой ленты.
    """
    new_items = 0
    try:
        async with session.get(feed_url, timeout=20) as response:
            content = await response.read()
    except Exception as e:
        # Невозможно скачать — возвращаем 0
        return 0

    parsed = feedparser.parse(content)
    if parsed.bozo:
        # Не RSS/ATOM или битый XML
        return 0

    for entry in parsed.entries:
        # У entry обычно есть: title, link, summary/value, published
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        summary = entry.get("summary", "").strip() if entry.get("summary") else ""
        published = ""
        if entry.get("published"):
            # Переводим к ISO-формату, если возможно
            try:
                dt = parser.parse(entry.published)
                published = dt.isoformat()
            except:
                published = entry.published

        # Проверяем каждое ключевое слово
        lower_title = title.lower()
        lower_summary = summary.lower()
        for kw in keywords:
            if kw in lower_title or kw in lower_summary:
                # найдено совпадение
                inserted = insert_news_item(feed_id, title, link, summary, published, kw)
                if inserted:
                    new_items += 1
                # Если одно слово совпало — не ищем дальше в этой записи
                break

    return new_items

async def do_fetch_rss_and_store() -> (int, int):
    """
    Пробегаем по всем лентам и запускаем fetch_feed для каждой.
    Возвращаем (количество_ленточек, количество_новых_новостей).
    """
    feeds = fetch_all_feeds()
    keywords = fetch_all_keywords()
    if not feeds or not keywords:
        return (len(feeds), 0)

    total_new = 0
    async with aiohttp.ClientSession() as session:
        tasks = []
        for row in feeds:
            feed_id = row["id"]
            feed_url = row["url"]
            tasks.append(fetch_feed(session, feed_id, feed_url, keywords))
        results = await asyncio.gather(*tasks)
        total_new = sum(results)
    return (len(feeds), total_new)

async def periodic_fetch():
    """
    Фоновая задача, которая каждые FETCH_INTERVAL_SECONDS секунд запускает fetch.
    """
    await asyncio.sleep(5)  # Дайте 5 секунд на старте приложения
    while True:
        fetched_count, new_items = await do_fetch_rss_and_store()
        ts = datetime.utcnow().isoformat()
        print(f"[{ts}] Fetched {fetched_count} feeds, inserted {new_items} new items.")
        await asyncio.sleep(FETCH_INTERVAL_SECONDS)

# ======================================================
#  FastAPI: события старта/шутдауна и роутеры
# ======================================================

app = FastAPI()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Здесь код для старта (в вашем случае — создать фоновую задачу)
    import asyncio
    loop = asyncio.get_event_loop()
    loop.create_task(periodic_fetch())

    yield

    # (здесь можно разместить cleanup, если нужно)
    # Например:
    # await some_background_task.shutdown()
    
app.router.lifespan_context = lifespan

@app.get("/health")
async def health_check():
    """
    Простейшая проверка «сервис жив».
    """
    return {"status": "ok"}

# ------------------------------------------------------
#  CRUD для RSS-лент (/feeds)
# ------------------------------------------------------

@app.get("/feeds", response_model=List[FeedOut])
async def list_feeds():
    """
    Вернуть список всех RSS-лент.
    """
    rows = db.execute("SELECT id, url, description FROM feeds").fetchall()
    return [{"id": r["id"], "url": r["url"], "description": r["description"]} for r in rows]

@app.post("/feeds", response_model=FeedOut, status_code=201)
async def create_feed(feed: FeedIn):
    """
    Добавить новую RSS-ленту.
    Тело:
      { "url": "...", "description": "..." }
    """
    if not feed.url:
        raise HTTPException(status_code=400, detail="URL is required")
    try:
        cursor = db.execute(
            "INSERT INTO feeds(url, description) VALUES (?, ?)",
            (feed.url.strip(), feed.description.strip())
        )
        db.commit()
        feed_id = cursor.lastrowid
        return {"id": feed_id, "url": feed.url.strip(), "description": feed.description.strip()}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Feed already exists")

@app.delete("/feeds/{feed_id}", status_code=204)
async def delete_feed(feed_id: int):
    """
    Удалить RSS-ленту по её ID.
    """
    cursor = db.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
    db.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Feed not found")
    return Response(status_code=204)

# ------------------------------------------------------
#  CRUD для ключевых слов (/keywords)
# ------------------------------------------------------

@app.get("/keywords", response_model=List[KeywordOut])
async def list_keywords():
    """
    Вернуть список всех ключевых слов.
    """
    rows = db.execute("SELECT id, keyword FROM keywords").fetchall()
    return [{"id": r["id"], "keyword": r["keyword"]} for r in rows]

@app.post("/keywords", response_model=KeywordOut, status_code=201)
async def create_keyword(kw: KeywordIn):
    """
    Добавить новое ключевое слово.
    Тело:
      { "keyword": "bitcoin" }
    """
    word = kw.keyword.strip().lower()
    if not word:
        raise HTTPException(status_code=400, detail="Keyword is required")
    try:
        cursor = db.execute("INSERT INTO keywords(keyword) VALUES (?)", (word,))
        db.commit()
        keyword_id = cursor.lastrowid
        return {"id": keyword_id, "keyword": word}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Keyword already exists")

@app.delete("/keywords/{keyword_id}", status_code=204)
async def delete_keyword(keyword_id: int):
    """
    Удалить ключевое слово по его ID.
    """
    cursor = db.execute("DELETE FROM keywords WHERE id = ?", (keyword_id,))
    db.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Keyword not found")
    return Response(status_code=204)

# ------------------------------------------------------
#  Ручной триггер опроса RSS (/fetch)
# ------------------------------------------------------

@app.post("/fetch")
async def manual_fetch():
    """
    Ручной запуск сбора RSS: пройтись по всем лентам и найти новости.
    Возвращает, сколько лент было опрошено и сколько новых записей вставлено.
    """
    fetched_count, new_items = await do_fetch_rss_and_store()
    return {"fetched_feeds": fetched_count, "new_items": new_items}

# ------------------------------------------------------
#  Просмотр найденных новостей (/news)
# ------------------------------------------------------

@app.get("/news", response_model=List[NewsItem])
async def list_news(keyword: Optional[str] = None, feed_id: Optional[int] = None):
    """
    Вернуть все найденные новости.
    Можно фильтровать по ключевому слову (?keyword=bitcoin) и/или по ID ленты (?feed_id=2).
    """
    sql = "SELECT id, feed_id, title, link, summary, published, matched_keyword FROM found_news"
    params = []
    conditions = []
    if keyword:
        conditions.append("matched_keyword = ?")
        params.append(keyword.strip().lower())
    if feed_id:
        conditions.append("feed_id = ?")
        params.append(feed_id)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY published DESC"
    rows = db.execute(sql, params).fetchall()
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "feed_id": r["feed_id"],
            "title": r["title"],
            "link": r["link"],
            "summary": r["summary"],
            "published": r["published"],
            "matched_keyword": r["matched_keyword"]
        })
    return result

@app.get("/news/{news_id}", response_model=NewsItem)
async def get_news_item(news_id: int):
    """
    Вернуть одну новость по её ID.
    """
    row = db.execute(
        "SELECT id, feed_id, title, link, summary, published, matched_keyword FROM found_news WHERE id = ?",
        (news_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="News item not found")
    return {
        "id": row["id"],
        "feed_id": row["feed_id"],
        "title": row["title"],
        "link": row["link"],
        "summary": row["summary"],
        "published": row["published"],
        "matched_keyword": row["matched_keyword"]
    }

