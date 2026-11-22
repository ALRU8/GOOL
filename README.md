# GOOL

Веб-сервис для архива «Летописей природы» заповедника «Пасвик» (Заповедный хакатон, кейс 1). Из сканов и документов извлекает наблюдения (вид, дата, место, фенофаза, значение, автор, цитата и страница источника), хранит их в базе, ищет, строит ряды по годам, выгружает в Excel/CSV и показывает страницу оригинала для проверки.

## Стек

Python, FastAPI, SQLAlchemy, PostgreSQL 16 (SQLite в тестах), PyMuPDF, python-docx, openpyxl, pymorphy3, rapidfuzz. Yandex Cloud: Vision OCR, YandexGPT Pro (извлечение), YandexGPT Lite (проверка), Qwen 3.6 (ИИ-зрение). Файлы: диск или Object Storage (S3). Фронтенд: JavaScript без фреймворков. Docker Compose, pytest.

## Цикл работы

1. Загрузка PDF, Word, Excel или CSV в разделе «Документы»; фоновая очередь, прогресс по стадиям.
2. Текст: текстовый слой PDF либо Yandex Vision OCR (развороты режутся по корешку).
3. Таблицы, графики, рисунки и страницы с ошибками OCR: картинкой в ИИ-зрение.
4. Извлечение записей: правила (календарные таблицы, даты, единицы) и YandexGPT Pro по тексту; в запрос добавляются примеры из правок экспертов.
5. Проверка: YandexGPT Lite сверяет запись с цитатой, сомнительные теряют достоверность.
6. Нормализация: виды (справочник, леммы), единицы, неточные даты, места с координатами, темы, достоверность 0–1.
7. PostgreSQL, кэш всех вызовов Yandex, карта покрытия страниц.
8. Поиск по виду, автору, фенологии, месту с фильтрами; ряды по годам; экспорт; просмотр источника с подтверждением, правкой и заметками.

## Запуск

```bash
cp .env.example .env
docker compose up -d --build
```

Откройте http://localhost:8000. Впишите `YC_FOLDER_ID` и `YC_API_KEY` в `.env`; без ключей работают поиск и правила для PDF с текстовым слоем. Готовые тома лежат в `Летопись природы/` (кнопка «Импорт из папки»).

Остановить: `docker compose down`. Пересобрать: `docker compose up -d --build`. Тесты: `.venv/bin/pytest`.

Без Docker: `python -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/uvicorn app.main:app`.

## Переменные окружения

| Переменная | Назначение | По умолчанию |
|---|---|---|
| `YC_FOLDER_ID`, `YC_API_KEY` | доступ к Yandex Cloud | пусто |
| `LLM_MODEL_EXTRACT` / `LLM_MODEL_CHEAP` | модели извлечения и проверки | `yandexgpt/latest` / `yandexgpt-lite/latest` |
| `VISION_MODEL` | модель ИИ-зрения | `qwen3.6-35b-a3b/latest` |
| `LLM_ENABLED`, `OCR_ENABLED`, `VISION_ENABLED`, `VERIFY_ENABLED` | включение этапов | 1 |
| `FORCE_OCR` | весь текст через OCR | 0 |
| `WORKERS` | параллельные запросы к Yandex | 4 |
| `DATABASE_URL` | база | SQLite в `storage/` |
| `STORAGE_BACKEND`, `S3_*` | `local` или `s3` | `local` |

Полный список: `.env.example`.

## Структура

```
app/            api.py, search.py, export.py, quality.py, worker.py, yc.py, recommend.py
app/pipeline/   ingest, scan, elements, llm_extract, verify, build, species, toponyms, dates, units
app/static/     интерфейс
reference/      стартовые справочники видов и мест
tests/          pytest
```

## Перенос в Yandex Cloud

ВМ с Docker (`docker compose up -d --build`); `STORAGE_BACKEND=s3` для Object Storage; Managed PostgreSQL через `DATABASE_URL`; секреты в Lockbox.

## Ограничения

Качество зависит от скана; нет аккаунтов и ролей (закройте доступ перед публикацией в интернет); у части мест нет координат; .doc и .xls не поддерживаются.
