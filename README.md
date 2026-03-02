# Flask + MVC (упрощённый) — свечи T‑Invest

Это учебный пример: один Flask-файл `app.py`, один файл с моделями (`models.py`) и один файл с сервисами (`services.py`).

## Установка

### 1) Создайте виртуальное окружение
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate
```

### 2) Установите SDK T‑Invest (Python 3.12)
По документации T‑Bank:
```bash
pip install t-tech-investments --index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple
```

### 3) Остальные зависимости сайта
```bash
pip install -r requirements.txt
```

## Токен

Скопируйте пример и вставьте ваш токен:
```bash
cp app_secrets.py.example app_secrets.py
```

Файл `app_secrets.py` добавлен в `.gitignore`.

## Запуск

```bash
python app.py
```

Откройте: http://127.0.0.1:5000/

## Что здесь “MVC”

- **Model**: `models.Candle` (dataclass)
- **Controller**: роуты в `app.py`
- **Service**: функции в `services.py` (получение свечей, подготовка DataFrame, построение графика)
- **View**: шаблоны в `templates/`
