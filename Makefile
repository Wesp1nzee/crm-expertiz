.PHONY: lint format typecheck all run sync

# Запуск линтера
lint:
	uv run ruff check .

# Форматирование кода
format:
	uv run ruff format .

# Проверка типов
typecheck:
	uv run mypy .

# Запуск всего сразу
all: format lint typecheck

# Запуск FastAPI в режиме разработки
run:
	uv run uvicorn main:app --reload

# Синхронизация зависимостей
sync:
	uv sync