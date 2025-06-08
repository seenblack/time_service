Сервис мониторинга RSS-лент

Сервис позволяет:
•	Периодически опрашивать заданные RSS-ленты
•	Фильтровать записи по ключевым словам
•	Сохранять найденные новости в базу данных (SQLite)
•	Управлять списком RSS-лент и ключевых слов через HTTP API
•	Просматривать и получать найденные новости через HTTP API

Требования
•	Python 3.8+
•	pip
•	SQLite (входит в состав Python)

Установка
1. Клонируйте репозиторий:
git clone <https://github.com/seenblack/time_service/tree/main>
cd <ПАПКА_ПРОЕКТА>
2. (Рекомендуется) Создайте виртуальное окружение:
python3 -m venv venv
source venv/bin/activate   # Linux/macOS
venv\Scripts\activate  # Windows
3. Установите зависимости:
pip install -r requirements.txt

Конфигурация
•	FETCH_INTERVAL_SECONDS — интервал опроса RSS-лент (секунды) в файле time_service.py (по умолчанию 600)
•	Если файл news.db не существует, он создаётся автоматически

Запуск сервиса
uvicorn time_service:app --host 0.0.0.0 --port 8000 --reload
– --reload автоматически перезагружает сервер при изменении кода
– Веб-документация Swagger доступна по адресу: http://localhost:8000/docs

API Endpoints
Метод	Путь	Описание
GET	/health	Проверка статуса сервиса
GET	/feeds	Список RSS-лент
POST	/feeds	Добавить новую RSS-ленту (JSON { "url": "..." })
DELETE	/feeds/{feed_id}	Удалить RSS-ленту по ID
GET	/keywords	Список ключевых слов
POST	/keywords	Добавить ключевое слово (JSON { "keyword": "..." })
DELETE	/keywords/{keyword_id}	Удалить ключевое слово по ID
POST	/fetch	Ручной запуск опроса RSS-лент
GET	/news	Список всех найденных новостей (фильтры: ?keyword=, ?feed_id=)
GET	/news/{news_id}	Получить детали одной новости по ID

Примеры использования (curl)

curl -X POST http://localhost:8000/feeds \
     -H "Content-Type: application/json" \
     -d '{"url":"https://example.com/rss"}'

curl -X POST http://localhost:8000/keywords \
     -H "Content-Type: application/json" \
     -d '{"keyword":"bitcoin"}'

curl http://localhost:8000/news

Логирование
При каждом опросе лент в консоль выводится:
[YYYY-MM-DDTHH:MM:SS.mmmmmmZ] Fetched X feeds, inserted Y new items.

Для сохранения лога в файл:
uvicorn time_service:app --host 0.0.0.0 --port 8000 | tee rss_log.txt

Зависимости
Содержимое requirements.txt:
fastapi
uvicorn[standard]
aiohttp
feedparser
python-dateutil
