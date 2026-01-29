
---

### 📋 Содержание

- [Обзор проекта](#-обзор-проекта)
- [Структура проекта](#-структура-проекта)
- [Технологии](#-технологии)
- [Установка и запуск](#-установка-и-запуск)
- [Настройка окружения](#-настройка-окружения)
- [Разработка](#-разработка)
- [Миграции базы данных](#-миграции-базы-данных)
- [Тестирование](#-тестирование)
- [Гайд для контрибьютеров](#-гайд-для-контрибьютеров)
- [Стиль кода](#-стиль-кода)
- [Соглашения по именованию](#-соглашения-по-именованию)
- [Команды Make](#-команды-make)
- [Лицензия](#-лицензия)

---

### 🧩 Обзор проекта

`wesp1nzee-crm-expertiz` — это веб-приложение, предназначенное для управления делами, клиентами, документами, календарем и почтой. Проект разработан с использованием асинхронного Python-фреймворка FastAPI, ORM SQLAlchemy и базы данных PostgreSQL. Для хранения сессий используется Redis, а для файлов — S3-совместимое хранилище.

**Основные компоненты (реализованные на данный момент):**

- **Пользователи**: Регистрация, аутентификация, сессии, RBAC.
- **Клиенты**: Управление юридическими и физическими лицами.
- **Дела (Cases)**: Связь с клиентами, статусы, примечания.
- **Документы**: Загрузка, хранение, привязка к делам, работа с файловой системой.
- **Календарь**: События и задачи.
- **Компании**: Регистрация новых компаний и владельцев.
- **Безопасность**: Сессии, RBAC, хеширование паролей.

---

### 🗂️ Структура проекта

```text
wesp1nzee-crm-expertiz/
├── README.md                    # Этот файл
├── alembic.ini                  # Конфигурация Alembic
├── Makefile                     # Утилиты для разработки
├── pyproject.toml               # Зависимости проекта
├── .env.example                 # Пример файла переменных окружения
├── .pre-commit-config.yaml      # Настройки pre-commit
├── .python-version              # Версия Python
├── alembic/                     # Скрипты миграций
│   ├── README
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
├── src/
│   ├── main.py                  # Точка входа в приложение
│   └── app/
│       ├── core/                # Ядро приложения
│       │   ├── auth/            # Аутентификация, сессии, RBAC
│       │   ├── config/          # Конфигурация приложения (settings)
│       │   ├── database/        # Подключение и сессии к БД
│       │   ├── redis/           # Подключение к Redis
│       │   └── storage/         # Работа с S3-хранилищем
│       └── services/            # Бизнес-логика, разделенная на модули
│           ├── calendar/        # Календарь (события, задачи)
│           ├── case/            # Дела
│           ├── client/          # Клиенты
│           ├── company/         # Компании
│           ├── document/        # Документы, папки
│           ├── mail/            # (Заглушка, модели)
│           └── user/            # Пользователи
└── tests/                       # Тесты
    ├── conftest.py
    └── services/
        └── client/
            └── test_endpoints.py
```

---

### 🔧 Технологии

- **Язык программирования:** Python 3.14
- **Веб-фреймворк:** [FastAPI](https://fastapi.tiangolo.com/)
- **ORM:** [SQLAlchemy](https://www.sqlalchemy.org/) (асинхронный режим)
- **База данных:** PostgreSQL (с использованием `asyncpg`)
- **Кэш/Сессии:** Redis
- **Хранилище файлов:** S3-совместимое (MinIO, AWS S3)
- **Миграции БД:** [Alembic](https://alembic.sqlalchemy.org/)
- **Виртуальное окружение:** [uv](https://github.com/astral-sh/uv)
- **Форматирование кода:** [Ruff](https://docs.astral.sh/ruff/)
- **Проверка типов:** [mypy](https://mypy.readthedocs.io/en/stable/)
- **Pre-commit hooks:** Для автоматического форматирования и проверки

---

### 🚀 Установка и запуск

> **Примечание:** Для работы требуется `Python >= 3.14`, `uv`, `docker`, `docker-compose`.

1. **Клонируйте репозиторий:**

   ```bash
   git clone <your-repo-url>
   cd wesp1nzee-crm-expertiz
   ```

2. **Создайте `.env` файл:**

   ```bash
   cp .env.example .env
   ```
   Отредактируйте `.env` под свои нужды (см. [Настройка окружения](#-настройка-окружения)).

3. **Установите зависимости:**

   ```bash
   uv sync
   ```

4. **Запустите инфраструктуру (PostgreSQL, Redis):**


Если вы предпочитаете запускать контейнеры инфраструктуры (PostgreSQL, Redis, MinIO) по отдельности с помощью `docker run`, следуйте этим шагам. Это может быть полезно для лучшего понимания зависимостей или отладки.

#### 1. Запуск PostgreSQL

```bash
docker run --name crm-postgres \
  -e POSTGRES_DB=crm_db \
  -e POSTGRES_USER=your_postgres_user \
  -e POSTGRES_PASSWORD=your_postgres_password \
  -p 5432:5432 \
  -d postgres:17-alpine
```

> **Важно:** Замените `your_postgres_user` и `your_postgres_password` на реальные учетные данные. Эти же данные нужно будет указать в `DB_URL` в файле `.env`:
> `DB_URL=postgresql+asyncpg://your_postgres_user:your_postgres_password@localhost:5432/crm_db`

#### 2. Запуск Redis

```bash
docker run --name crm-redis \
  -p 6379:6379 \
  -d redis:latest
```

> **Важно:** Убедитесь, что `REDIS_URL` в `.env` указывает на `redis://localhost:6379`.

#### 3. Запуск MinIO (S3-совместимое хранилище)

```bash
docker run --name crm-minio \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin123 \
  -p 9000:9000 \
  -p 9001:9001 \
  -v minio_data:/data \
  -d minio/minio:latest server /data --console-address ":9001"
```

> **Важно:** Замените `MINIO_ROOT_PASSWORD` на надежный пароль. Обновите `.env` соответствующе:
> ```
> S3_ENDPOINT_URL=http://localhost:9000
> S3_ACCESS_KEY=minioadmin
> S3_SECRET_KEY=minioadmin123 # <-- тот самый пароль
> S3_BUCKET_NAME=crm-bucket # Убедитесь, что бакет существует
> S3_REGION=us-east-1
> ```
> После запуска откройте `http://localhost:9001` в браузере (с логином `minioadmin` / `minioadmin123`), создайте бакет с именем `crm-bucket`.

#### 4. Подготовка приложения

1.  Убедитесь, что вы выполнили шаги из раздела **"Установка и запуск"** до команды `docker-compose up -d`, включая создание `.env` файла и установку зависимостей (`uv sync`).
2.  Примените миграции к PostgreSQL, который теперь запущен отдельно:
    ```bash
    make migrate
    ```
    *(Эта команда использует `uv run alembic upgrade head`, которое читает `DB_URL` из `.env`.)*

#### 5. Запуск приложения

После того как все зависимости (PostgreSQL, Redis, MinIO) будут запущены и миграции применены, вы можете запустить само приложение стандартной командой:

```bash
make run
```
или
```bash
uv run python -m src.main
```

Приложение будет использовать настройки из `.env` для подключения к запущенным контейнерам.

#### Остановка контейнеров

Когда закончите работу, остановите и удалите запущенные контейнеры:

```bash
docker stop crm-postgres crm-redis crm-minio
docker rm crm-postgres crm-redis crm-minio
# Необязательно: удалить том MinIO
docker volume rm minio_data
```

5. **Примените последние миграции базы данных:**

   ```bash
   make migrate
   ```

6. **Запустите приложение:**

   ```bash
   make run
   ```
   Приложение будет доступно по адресу `http://localhost:8000`.

---

### ⚙️ Настройка окружения

Создайте файл `.env` и задайте следующие переменные:

```env
DB_URL=postgresql://username:password@localhost:5432/crm_db
REDIS_URL=redis://localhost:6379
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_BUCKET_NAME=crm-bucket
S3_REGION=us-east-1
ADMIN_EMAIL=admin@example.com
ADMIN_FULL_NAME=Admin User
ADMIN_PASSWORD=supersecret
```

---

### 👨‍💻 Разработка

#### Запуск приложения

```bash
make run
```

#### Запуск с отладкой

```bash
make dev
```

#### Установка зависимостей

```bash
# Установить все зависимости
uv sync

# Установить зависимости для разработки
uv sync --dev
```

---

### 🗃️ Миграции базы данных

Миграции управляются с помощью Alembic.

- **Создать новую миграцию:**

  ```bash
  make revision m="Описание миграции"
  ```

- **Применить миграции:**

  ```bash
  make migrate
  ```

- **Откатить последнюю миграцию:**

  ```bash
  make rollback
  ```

- **Посмотреть историю миграций:**

  ```bash
  make history
  ```

- **Посмотреть текущий статус:**

  ```bash
  make current
  ```

---

### 🧪 Тестирование

Тесты написаны с использованием `pytest`.

- **Запустить все тесты:**

  ```bash
  make test
  ```

- **Запустить тесты с покрытием:**

  ```bash
  make test-cov
  ```

---

### 🤝 Гайд для контрибьютеров

Благодарим вас за интерес к нашему проекту! Мы рады любому вкладу.

#### 1. Подготовка рабочего места

- Убедитесь, что у вас установлены все зависимости (см. [Установка и запуск](#-установка-и-запуск)).
- Установите `pre-commit` хуки:

  ```bash
  pre-commit install
  ```

  Это обеспечит автоматическое форматирование и проверку кода перед каждым коммитом.

#### 2. Рабочий процесс

1. Создайте новую ветку от `main`:

   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Вносите изменения, пишите тесты, следуйте [стилю кода](#-стиль-кода).

3. Убедитесь, что все тесты проходят:

   ```bash
   make test
   ```

4. Зафиксируйте изменения (`git add`, `git commit`). `pre-commit` выполнит проверки.

5. Отправьте ветку в репозиторий:

   ```bash
   git push origin feature/your-feature-name
   ```

6. Создайте Pull Request (PR) в GitHub.

#### 3. Обзор PR
- После одобрения PR может быть слит в `main`.

---

### 🧼 Стиль кода

- **Форматирование:** Код форматируется с помощью `ruff format`. Запускается автоматически через `pre-commit`.
- **Проверка ошибок:** `ruff check`. Также запускается через `pre-commit`.
- **Проверка типов:** `mypy`. Проверяется через `pre-commit`.

---

### 🔤 Соглашения по именованию

- **Имена файлов:** `snake_case.py` (например, `user_service.py`).
- **Имена классов:** `PascalCase` (например, `UserService`).
- **Имена функций и переменных:** `snake_case` (например, `get_user`).
- **Константы:** `UPPER_CASE` (например, `DEFAULT_PAGE_SIZE`).
- **Имена веток Git:** `feature/...`, `bugfix/...`, `hotfix/...`.

---

### 🛠️ Команды Make

Файл `Makefile` содержит удобные команды для разработки:

```makefile
# Запустить приложение
run:
	uv run python -m src.main

# Запустить с перезагрузкой (dev)
dev:
	uv run watchfiles --filter python "python -m src.main"

# Запустить тесты
test:
	uv run python -m pytest

# Запустить тесты с покрытием
test-cov:
	uv run python -m pytest --cov=src --cov-report=html

# Создать пустую миграцию
revision:
	uv run alembic revision -m "$(m)"

# Применить миграции
migrate:
	uv run alembic upgrade head

# Откатить последнюю миграцию
rollback:
	uv run alembic downgrade -1

# История миграций
history:
	uv run alembic history --verbose

# Текущий статус
current:
	uv run alembic current
```

---
