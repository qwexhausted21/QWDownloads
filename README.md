# QWDownloads

![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111.0-009688?logo=fastapi)
![pywebview](https://img.shields.io/badge/pywebview-Desktop-2C2C2C?logo=python)
![Python](https://img.shields.io/badge/Python-used-green)
![PyQT](https://img.shields.io/badge/PyQT-used-green)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

Desktop-инструмент для менеджмента папки загрузок. Автоматизирует сортировку скачанных файлов по категориям, помогая поддерживать порядок в `Downloads` без лишних усилий.

## 📖 Описание

**QWDownloads** — десктоп-утилита для управления содержимым папки загрузок. Приложение отслеживает указанную папку и перемещает файлы в соответствующие подпапки в зависимости от их расширения: изображения, документы, архивы, видео, аудио и прочие файлы распределяются автоматически.

Интерфейс реализован как веб-приложение внутри десктоп-окна (PyWebView), а бэкенд написан на Python с использованием FastAPI.

## ✨ Возможности

- 📂 **Автоматическая сортировка** — файлы распределяются по категориям согласно расширению
- 🖥️ **Десктоп-интерфейс** — современное окно приложения на базе PyWebView
- 🐍 **Python-стек** — FastAPI на бэкенде, PyWebView и PyQt для интерфейса
- ⚙️ **Гибкая настройка** — набор категорий и правил можно адаптировать под свои задачи
- 🪪 **Лицензия MIT** — свободное использование и модификация

## 🛠 Стек технологий

| Компонент | Технология |
|-----------|------------|
| Язык | Python 3.11+ |
| Бэкенд | FastAPI |
| Десктоп-оболочка | PyWebView |
| Интерфейс | PyQt, HTML |

## 📦 Установка

```bash
# Клонируйте репозиторий
git clone https://github.com/qwexhausted21/QWDownloads.git
cd QWDownloads

# Создайте виртуальное окружение
python -m venv venv

# Активируйте окружение
# Windows:
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate

# Установите зависимости
pip install -r requirements.txt
```

## 🚀 Быстрый старт

```bash
python app.py
```

После запуска откроется окно приложения. Укажите папку загрузок и запустите сортировку.

## 🤝 Вклад

Pull request'ы приветствуются! Перед большими изменениями, пожалуйста, откройте issue для обсуждения.

## 📄 Лицензия

Этот проект распространяется под лицензией [MIT](LICENSE).
