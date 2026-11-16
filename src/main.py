"""
QWDownloads — организатор папки загрузок для Windows.
Главный модуль приложения на PyQt6.

Возможности:
- выбор папки загрузок;
- автоматическая сортировка файлов по категориям;
- поиск дубликатов по хешу;
- очистка временных файлов;
- тёмная тема, анимации, прогресс-бар, лог с временными метками.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QThread,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QIcon, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

APP_NAME = "QWDownloads"
SETTINGS_FILE = "settings.json"
LOG_FILE = "qwdownloads.log"

DEFAULT_SETTINGS = {
    "downloads_folder": "",
    "dark_theme": True,
    "temp_cleanup_days": 7,
    "categories": {
        "images": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico"],
        "video": [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv"],
        "audio": [".mp3", ".wav", ".ogg", ".flac", ".aac", ".wma"],
        "documents": [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".rtf", ".odt"],
        "archives": [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"],
        "installers": [".exe", ".msi"],
    },
    "hash_algorithm": "sha256",
    "log_enabled": True,
    "max_log_lines": 1000,
}

CATEGORY_LABELS = {
    "images": "Изображения",
    "video": "Видео",
    "audio": "Аудио",
    "documents": "Документы",
    "archives": "Архивы",
    "installers": "Инсталяторы",
    "other": "Прочее",
}

EXTENSION_ORDER = [
    ("images", [".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".ico"]),
    ("video", [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv"]),
    ("audio", [".mp3", ".wav", ".ogg", ".flac", ".aac", ".wma"]),
    ("documents", [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt", ".rtf", ".odt"]),
    ("archives", [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2"]),
    ("installers", [".exe", ".msi"]),
]

TEMP_EXTENSIONS = {".tmp", ".log", ".temp", ".bak"}

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def resource_dir() -> Path:
    """Директория, где лежит программа (для frozen и для исходников)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def settings_path() -> Path:
    return resource_dir() / SETTINGS_FILE


def load_settings() -> dict:
    path = settings_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # сливаем с дефолтами, чтобы не потерять новые ключи
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            return merged
        except (json.JSONDecodeError, OSError):
            logging.exception("Не удалось загрузить settings.json, используются дефолты")
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    path = settings_path()
    try:
        path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        logging.exception("Не удалось сохранить settings.json")


def log_message(message: str, settings: dict | None = None) -> None:
    """Записывает строку в файл лога и выводит в logging."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}"
    logging.info(message)
    if settings is None:
        settings = load_settings()
    if not settings.get("log_enabled", True):
        return
    try:
        log_path = resource_dir() / LOG_FILE
        lines: list[str] = []
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").splitlines()
        lines.append(line)
        max_lines = int(settings.get("max_log_lines", 1000))
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        logging.exception("Не удалось записать лог")


def categorize_file(path: Path, settings: dict) -> str:
    """Возвращает ключ категории для файла."""
    ext = path.suffix.lower()
    categories = settings.get("categories", {})
    for key, exts in categories.items():
        if ext in [e.lower() for e in exts]:
            return key
    return "other"


def category_folder_name(key: str) -> str:
    return CATEGORY_LABELS.get(key, key)


def file_hash(path: Path, algorithm: str = "sha256") -> str | None:
    """Вычисляет хеш файла. Возвращает None, если файл недоступен."""
    try:
        h = hashlib.new(algorithm)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, ValueError):
        return None


def is_file_locked(path: Path) -> bool:
    """Проверяет, не занят ли файл другим процессом (Windows)."""
    try:
        with open(path, "r+b"):
            pass
        return False
    except OSError:
        return True


# ---------------------------------------------------------------------------
# Рабочие потоки (QThread)
# ---------------------------------------------------------------------------


class SortWorker(QThread):
    """Поток сортировки файлов по категориям."""

    progress = pyqtSignal(int, int)  # current, total
    file_done = pyqtSignal(str, str)  # filename, category
    finished_signal = pyqtSignal(dict)  # stats
    error = pyqtSignal(str)

    def __init__(self, folder: Path, settings: dict, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.settings = settings
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            files = [p for p in self.folder.iterdir() if p.is_file() and not p.name.startswith(".")]
            total = len(files)
            stats = {"moved": 0, "skipped": 0, "errors": 0, "by_category": {}}
            for idx, path in enumerate(files, start=1):
                if self._cancel:
                    break
                try:
                    if is_file_locked(path):
                        stats["skipped"] += 1
                        continue
                    category = categorize_file(path, self.settings)
                    target_dir = self.folder / category_folder_name(category)
                    target_dir.mkdir(parents=True, exist_ok=True)
                    target = target_dir / path.name
                    # обработка имён с коллизиями
                    if target.exists():
                        stem = path.stem
                        suffix = path.suffix
                        counter = 1
                        while target.exists() and counter < 1000:
                            target = target_dir / f"{stem} ({counter}){suffix}"
                            counter += 1
                    shutil.move(str(path), str(target))
                    stats["moved"] += 1
                    stats["by_category"][category] = stats["by_category"].get(category, 0) + 1
                    self.file_done.emit(path.name, category)
                except Exception:
                    stats["errors"] += 1
                    logging.exception("Ошибка перемещения %s", path)
                finally:
                    self.progress.emit(idx, total)
            self.finished_signal.emit(stats)
        except Exception as exc:
            self.error.emit(str(exc))


class DuplicateWorker(QThread):
    """Поток поиска дубликатов по хешу."""

    found = pyqtSignal(list)  # группы дублей: список списков путей
    progress = pyqtSignal(int, int)
    finished_signal = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, folder: Path, settings: dict, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.settings = settings
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            files = [p for p in self.folder.rglob("*") if p.is_file()]
            total = len(files)
            groups: dict[str, list[Path]] = {}
            for idx, path in enumerate(files, start=1):
                if self._cancel:
                    return
                if is_file_locked(path):
                    self.progress.emit(idx, total)
                    continue
                digest = file_hash(path, self.settings.get("hash_algorithm", "sha256"))
                if digest:
                    groups.setdefault(digest, []).append(path)
                self.progress.emit(idx, total)
            duplicates = [g for g in groups.values() if len(g) > 1]
            self.found.emit(duplicates)
            self.finished_signal.emit()
        except Exception as exc:
            self.error.emit(str(exc))


class CleanupWorker(QThread):
    """Поток очистки временных файлов старше N дней."""

    progress = pyqtSignal(int, int)
    file_deleted = pyqtSignal(str)
    finished_signal = pyqtSignal(dict)  # stats
    error = pyqtSignal(str)

    def __init__(self, folder: Path, settings: dict, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.settings = settings
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        try:
            days = int(self.settings.get("temp_cleanup_days", 7))
            cutoff = datetime.now().timestamp() - days * 86400
            files = [
                p
                for p in self.folder.rglob("*")
                if p.is_file() and p.suffix.lower() in TEMP_EXTENSIONS
            ]
            total = len(files)
            stats = {"deleted": 0, "skipped": 0, "errors": 0}
            for idx, path in enumerate(files, start=1):
                if self._cancel:
                    break
                try:
                    if is_file_locked(path):
                        stats["skipped"] += 1
                        continue
                    mtime = path.stat().st_mtime
                    if mtime < cutoff:
                        path.unlink()
                        stats["deleted"] += 1
                        self.file_deleted.emit(path.name)
                    else:
                        stats["skipped"] += 1
                except Exception:
                    stats["errors"] += 1
                    logging.exception("Ошибка удаления %s", path)
                finally:
                    self.progress.emit(idx, total)
            self.finished_signal.emit(stats)
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# UI-компоненты
# ---------------------------------------------------------------------------


class CategoryCard(QFrame):
    """Карточка категории на главном экране с иконкой и счётчиком."""

    clicked = pyqtSignal(str)

    def __init__(self, key: str, label: str, count: int = 0, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("CategoryCard")
        self.setFixedSize(170, 100)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self.icon_label = QLabel("📁")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.icon_label.setStyleSheet("font-size: 26px; background: transparent; border: none;")
        layout.addWidget(self.icon_label)

        self.title_label = QLabel(label)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.title_label.setStyleSheet("font-size: 14px; font-weight: 600; background: transparent; border: none;")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.count_label = QLabel(f"{count} файлов")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.count_label.setStyleSheet("font-size: 11px; color: #888; background: transparent; border: none;")
        self.count_label.setWordWrap(True)
        layout.addWidget(self.count_label)

        layout.addStretch()

        self.set_count(count)

    def set_count(self, count: int) -> None:
        self.count_label.setText(f"{count} файлов")

    def enterEvent(self, event):  # noqa: N802
        self._animate_border("#4a90e2")
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._animate_border("#3d3d3d")
        super().leaveEvent(event)

    def _animate_border(self, color: str) -> None:
        self.setStyleSheet(
            f"""
            QFrame#CategoryCard {{
                background: #2d2d2d;
                border: 2px solid {color};
                border-radius: 12px;
            }}
            """
        )

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)


class LogPanel(QWidget):
    """Панель лога с временными метками."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QLabel("Лог действий")
        header.setStyleSheet("font-size: 14px; font-weight: 600; color: #ccc;")
        layout.addWidget(header)

        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setStyleSheet(
            """
            QListWidget {
                background: #252525;
                border: 1px solid #3d3d3d;
                border-radius: 8px;
                color: #ddd;
                font-family: Consolas, 'Courier New', monospace;
                font-size: 12px;
            }
            QListWidget::item {
                padding: 4px 8px;
                border-bottom: 1px solid #333;
            }
            QListWidget::item:alternate {
                background: #2a2a2a;
            }
            """
        )
        layout.addWidget(self.list_widget)

    def add_entry(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        item = QListWidgetItem(f"[{ts}] {message}")
        self.list_widget.addItem(item)
        self.list_widget.scrollToBottom()
        # ограничиваем количество строк
        max_lines = 200
        while self.list_widget.count() > max_lines:
            self.list_widget.takeItem(0)

    def clear(self) -> None:
        self.list_widget.clear()


class MainWindow(QMainWindow):
    """Главное окно приложения."""

    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.current_worker: QThread | None = None

        self.setWindowTitle(APP_NAME)
        self.resize(1300, 850)
        self.setMinimumSize(1300, 850)

        self._setup_logging()
        self._apply_theme()
        self._build_ui()
        self._refresh_counts()

        log_message("Приложение запущено", self.settings)

    # ------------------------------------------------------------------
    # Логирование и тема
    # ------------------------------------------------------------------

    def _setup_logging(self) -> None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)],
        )

    def _apply_theme(self) -> None:
        dark = bool(self.settings.get("dark_theme", True))
        app = QApplication.instance()
        if app is None:
            return
        palette = QPalette()
        if dark:
            palette.setColor(QPalette.ColorRole.Window, QColor("#1e1e1e"))
            palette.setColor(QPalette.ColorRole.WindowText, QColor("#e0e0e0"))
            palette.setColor(QPalette.ColorRole.Base, QColor("#252525"))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#2d2d2d"))
            palette.setColor(QPalette.ColorRole.Text, QColor("#e0e0e0"))
            palette.setColor(QPalette.ColorRole.Button, QColor("#3d3d3d"))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e0e0e0"))
        else:
            palette.setColor(QPalette.ColorRole.Window, QColor("#f5f5f5"))
            palette.setColor(QPalette.ColorRole.WindowText, QColor("#222222"))
            palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#eeeeee"))
            palette.setColor(QPalette.ColorRole.Text, QColor("#222222"))
            palette.setColor(QPalette.ColorRole.Button, QColor("#e0e0e0"))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor("#222222"))
        app.setPalette(palette)
        self.setStyleSheet(self._styles(dark))

    def _styles(self, dark: bool) -> str:
        bg = "#1e1e1e" if dark else "#f5f5f5"
        surface = "#252525" if dark else "#ffffff"
        border = "#3d3d3d" if dark else "#cccccc"
        text = "#e0e0e0" if dark else "#222222"
        muted = "#888888" if dark else "#666666"
        accent = "#4a90e2"
        accent_hover = "#3a78d2"

        return f"""
        QMainWindow {{
            background: {bg};
        }}
        QWidget {{
            background: {bg};
            color: {text};
            font-family: 'Segoe UI', sans-serif;
            font-size: 13px;
        }}
        QFrame#Sidebar {{
            background: {surface};
            border-right: 1px solid {border};
            border-top-right-radius: 16px;
            border-bottom-right-radius: 16px;
        }}
        QLabel#Title {{
            font-size: 24px;
            font-weight: 700;
            color: {text};
            padding: 8px 0;
        }}
        QLabel#Subtitle {{
            font-size: 13px;
            color: {muted};
        }}
        QPushButton#NavButton {{
            background: transparent;
            border: none;
            text-align: left;
            padding: 12px 20px;
            font-size: 14px;
            font-weight: 500;
            color: {text};
            border-radius: 8px;
        }}
        QPushButton#NavButton:hover {{
            background: rgba(74, 144, 226, 0.15);
        }}
        QPushButton#NavButton:checked {{
            background: {accent};
            color: #ffffff;
        }}
        QPushButton#ActionButton {{
            background: {accent};
            color: #ffffff;
            border: none;
            border-radius: 10px;
            padding: 10px 20px;
            font-size: 14px;
            font-weight: 600;
        }}
        QPushButton#ActionButton:hover {{
            background: {accent_hover};
        }}
        QPushButton#ActionButton:pressed {{
            background: #2c6db0;
        }}
        QPushButton#SecondaryButton {{
            background: {surface};
            color: {text};
            border: 1px solid {border};
            border-radius: 8px;
            padding: 8px 16px;
            font-size: 13px;
        }}
        QPushButton#SecondaryButton:hover {{
            border-color: {accent};
        }}
        QProgressBar {{
            border: 1px solid {border};
            border-radius: 8px;
            text-align: center;
            background: {surface};
            color: {text};
            height: 20px;
        }}
        QProgressBar::chunk {{
            background: {accent};
            border-radius: 7px;
        }}
        QFrame#Card {{
            background: {surface};
            border: 1px solid {border};
            border-radius: 12px;
        }}
        QScrollArea {{
            border: none;
            background: transparent;
        }}
        """

    # ------------------------------------------------------------------
    # Построение интерфейса
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Боковая панель навигации
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 20, 0, 20)
        sidebar_layout.setSpacing(6)

        logo = QLabel("QWDownloads")
        logo.setObjectName("Title")
        logo.setStyleSheet("color: #4a90e2; font-weight: 800; padding: 0 20px;")
        sidebar_layout.addWidget(logo)

        subtitle = QLabel("Организатор загрузок")
        subtitle.setObjectName("Subtitle")
        subtitle.setStyleSheet("padding: 0 20px 20px 20px;")
        sidebar_layout.addWidget(subtitle)

        self.nav_buttons: dict[str, QPushButton] = {}
        nav_items = [
            ("dashboard", "Главная", "🏠"),
            ("sort", "Сортировка", "📦"),
            ("duplicates", "Дубликаты", "🔍"),
            ("cleanup", "Очистка", "🧹"),
            ("settings", "Настройки", "⚙️"),
        ]
        for key, label, emoji in nav_items:
            btn = QPushButton(f"{emoji}  {label}")
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, k=key: self._switch_screen(k))
            self.nav_buttons[key] = btn
            sidebar_layout.addWidget(btn)

        sidebar_layout.addStretch()

        self.status_label = QLabel("Готово")
        self.status_label.setStyleSheet("color: #888; font-size: 12px; padding: 0 20px 10px 20px;")
        sidebar_layout.addWidget(self.status_label)

        root.addWidget(sidebar)

        # Стек экранов (без прокрутки — всё помещается в окне)
        self.stack = QWidget()
        self.stack.setObjectName("MainStack")
        root.addWidget(self.stack, stretch=1)
        self._stack_layout = QVBoxLayout(self.stack)
        self._stack_layout.setContentsMargins(0, 0, 0, 0)
        self._stack_layout.setSpacing(0)

        self.screens: dict[str, QWidget] = {}
        self._create_dashboard_screen()
        self._create_sort_screen()
        self._create_duplicates_screen()
        self._create_cleanup_screen()
        self._create_settings_screen()

        self._switch_screen("dashboard")

    def _set_stack_widget(self, widget: QWidget) -> None:
        # Экраны кэшируются в self.screens и не удаляются, чтобы не было
        # доступа к удалённому виджета при возврате на экран (краш).
        old = self._stack_layout.takeAt(0)
        if old is not None and old.widget() is not None:
            old.widget().setParent(None)
        self._stack_layout.addWidget(widget)

    def _switch_screen(self, key: str) -> None:
        for k, btn in self.nav_buttons.items():
            btn.setChecked(k == key)
        self.status_label.setText(
            {
                "dashboard": "Главная",
                "sort": "Автоматическая сортировка",
                "duplicates": "Поиск дубликатов",
                "cleanup": "Очистка временных файлов",
                "settings": "Настройки",
            }.get(key, "")
        )
        widget = self.screens.get(key)
        if widget is None:
            return
        # сначала добавляем виджет в layout, потом анимируем
        self._set_stack_widget(widget)
        # анимация переключения
        self._animate_screen(widget)
        if hasattr(widget, "refresh"):
            widget.refresh()

    def _animate_screen(self, widget: QWidget) -> None:
        widget.setGraphicsEffect(None)
        anim = QPropertyAnimation(widget, b"geometry")
        rect = widget.geometry()
        anim.setStartValue(QRect(rect.x(), rect.y() + 30, rect.width(), rect.height()))
        anim.setEndValue(rect)
        anim.setDuration(300)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        # сохраняем ссылку, чтобы не собирался сборщиком мусора
        self._current_anim = anim

    # ------------------------------------------------------------------
    # Экраны
    # ------------------------------------------------------------------

    def _create_dashboard_screen(self) -> None:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(24)

        title = QLabel("Главная")
        title.setObjectName("Title")
        layout.addWidget(title)

        layout.addWidget(QLabel("Выберите папку загрузок и запустите одну из операций."))

        # Папка загрузок (отдельная строка, всегда видна)
        folder_row = QHBoxLayout()
        self.dashboard_folder_label = QLabel(
            self.settings.get("downloads_folder") or "Папка не выбрана"
        )
        self.dashboard_folder_label.setStyleSheet("color: #888; font-style: italic;")
        self.dashboard_folder_label.setWordWrap(True)
        folder_row.addWidget(self.dashboard_folder_label, stretch=1)

        btn_dashboard_choose = QPushButton("Выбрать папку загрузок")
        btn_dashboard_choose.setObjectName("SecondaryButton")
        btn_dashboard_choose.clicked.connect(self._choose_folder)
        folder_row.addWidget(btn_dashboard_choose)
        layout.addLayout(folder_row)

        # Кнопки быстрого действия
        btn_row = QHBoxLayout()
        btn_sort = QPushButton("Сортировать файлы")
        btn_sort.setObjectName("ActionButton")
        btn_sort.clicked.connect(lambda: self._switch_screen("sort"))
        btn_row.addWidget(btn_sort)

        btn_dup = QPushButton("Найти дубликаты")
        btn_dup.setObjectName("SecondaryButton")
        btn_dup.clicked.connect(lambda: self._switch_screen("duplicates"))
        btn_row.addWidget(btn_dup)

        btn_clean = QPushButton("Очистить временные")
        btn_clean.setObjectName("SecondaryButton")
        btn_clean.clicked.connect(lambda: self._switch_screen("cleanup"))
        btn_row.addWidget(btn_clean)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Карточки категорий (располагаются в несколько рядов, чтобы помещаться в окно)
        layout.addWidget(QLabel("Файлов по категориям:"))
        cards_container = QWidget()
        cards_container_layout = QVBoxLayout(cards_container)
        cards_container_layout.setContentsMargins(0, 0, 0, 0)
        cards_container_layout.setSpacing(12)
        self.category_cards: dict[str, CategoryCard] = {}
        keys_order = ["images", "video", "audio", "documents", "archives", "installers", "other"]
        row = QHBoxLayout()
        row.setSpacing(16)
        for i, key in enumerate(keys_order):
            card = CategoryCard(key, CATEGORY_LABELS[key], 0)
            card.clicked.connect(lambda k=key: self._switch_screen("sort"))
            self.category_cards[key] = card
            row.addWidget(card)
            if (i + 1) % 4 == 0 or (i + 1) == len(keys_order):
                row.addStretch()
                cards_container_layout.addLayout(row)
                row = QHBoxLayout()
                row.setSpacing(16)
        layout.addWidget(cards_container)

        # Лог
        self.dashboard_log = LogPanel()
        layout.addWidget(self.dashboard_log, stretch=1)

        def _dashboard_refresh() -> None:
            self._refresh_counts()

        w.refresh = _dashboard_refresh
        self.screens["dashboard"] = w

    def _create_sort_screen(self) -> None:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        title = QLabel("Сортировка файлов")
        title.setObjectName("Title")
        layout.addWidget(title)

        # Выбор папки
        folder_row = QHBoxLayout()
        self.folder_label = QLabel(self.settings.get("downloads_folder") or "Папка не выбрана")
        self.folder_label.setStyleSheet("color: #888; font-style: italic;")
        self.folder_label.setWordWrap(True)
        folder_row.addWidget(self.folder_label, stretch=1)

        btn_choose = QPushButton("Выбрать папку")
        btn_choose.setObjectName("SecondaryButton")
        btn_choose.clicked.connect(self._choose_folder)
        folder_row.addWidget(btn_choose)
        layout.addLayout(folder_row)

        # Прогресс
        self.sort_progress = QProgressBar()
        self.sort_progress.setRange(0, 100)
        self.sort_progress.setValue(0)
        self.sort_progress.hide()
        layout.addWidget(self.sort_progress)

        self.sort_status = QLabel("")
        self.sort_status.setStyleSheet("color: #888;")
        layout.addWidget(self.sort_status)

        # Кнопки управления
        btn_row = QHBoxLayout()
        self.btn_start_sort = QPushButton("Начать сортировку")
        self.btn_start_sort.setObjectName("ActionButton")
        self.btn_start_sort.clicked.connect(self._start_sort)
        btn_row.addWidget(self.btn_start_sort)

        self.btn_cancel_sort = QPushButton("Отмена")
        self.btn_cancel_sort.setObjectName("SecondaryButton")
        self.btn_cancel_sort.clicked.connect(self._cancel_worker)
        self.btn_cancel_sort.hide()
        btn_row.addWidget(self.btn_cancel_sort)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()

        self.screens["sort"] = w

    def _create_duplicates_screen(self) -> None:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        title = QLabel("Поиск дубликатов")
        title.setObjectName("Title")
        layout.addWidget(title)

        layout.addWidget(QLabel("Поиск файлов с одинаковым содержимым по хешу."))

        self.dup_progress = QProgressBar()
        self.dup_progress.setRange(0, 100)
        self.dup_progress.setValue(0)
        self.dup_progress.hide()
        layout.addWidget(self.dup_progress)

        self.dup_status = QLabel("")
        self.dup_status.setStyleSheet("color: #888;")
        layout.addWidget(self.dup_status)

        btn_row = QHBoxLayout()
        self.btn_start_dup = QPushButton("Найти дубликаты")
        self.btn_start_dup.setObjectName("ActionButton")
        self.btn_start_dup.clicked.connect(self._start_duplicates)
        btn_row.addWidget(self.btn_start_dup)

        self.btn_cancel_dup = QPushButton("Отмена")
        self.btn_cancel_dup.setObjectName("SecondaryButton")
        self.btn_cancel_dup.clicked.connect(self._cancel_worker)
        self.btn_cancel_dup.hide()
        btn_row.addWidget(self.btn_cancel_dup)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.dup_list = QListWidget()
        self.dup_list.setStyleSheet(
            """
            QListWidget {
                background: #252525;
                border: 1px solid #3d3d3d;
                border-radius: 8px;
            }
            QListWidget::item {
                padding: 6px 10px;
                border-bottom: 1px solid #333;
            }
            """
        )
        layout.addWidget(self.dup_list, stretch=1)

        btn_del = QPushButton("Удалить выбранные дубликаты (оставить один)")
        btn_del.setObjectName("SecondaryButton")
        btn_del.clicked.connect(self._delete_selected_duplicates)
        layout.addWidget(btn_del)

        self.screens["duplicates"] = w

    def _create_cleanup_screen(self) -> None:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        title = QLabel("Очистка временных файлов")
        title.setObjectName("Title")
        layout.addWidget(title)

        layout.addWidget(QLabel("Удаление .tmp, .log, .temp, .bak старше указанного количества дней."))

        days_row = QHBoxLayout()
        days_row.addWidget(QLabel("Старше дней:"))
        from PyQt6.QtWidgets import QSpinBox

        self.cleanup_spin = QSpinBox()
        self.cleanup_spin.setRange(1, 365)
        self.cleanup_spin.setValue(int(self.settings.get("temp_cleanup_days", 7)))
        self.cleanup_spin.setFixedWidth(80)
        days_row.addWidget(self.cleanup_spin)
        days_row.addStretch()
        layout.addLayout(days_row)

        self.cleanup_progress = QProgressBar()
        self.cleanup_progress.setRange(0, 100)
        self.cleanup_progress.setValue(0)
        self.cleanup_progress.hide()
        layout.addWidget(self.cleanup_progress)

        self.cleanup_status = QLabel("")
        self.cleanup_status.setStyleSheet("color: #888;")
        layout.addWidget(self.cleanup_status)

        btn_row = QHBoxLayout()
        self.btn_start_cleanup = QPushButton("Начать очистку")
        self.btn_start_cleanup.setObjectName("ActionButton")
        self.btn_start_cleanup.clicked.connect(self._start_cleanup)
        btn_row.addWidget(self.btn_start_cleanup)

        self.btn_cancel_cleanup = QPushButton("Отмена")
        self.btn_cancel_cleanup.setObjectName("SecondaryButton")
        self.btn_cancel_cleanup.clicked.connect(self._cancel_worker)
        self.btn_cancel_cleanup.hide()
        btn_row.addWidget(self.btn_cancel_cleanup)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()

        self.screens["cleanup"] = w

    def _create_settings_screen(self) -> None:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        title = QLabel("Настройки")
        title.setObjectName("Title")
        layout.addWidget(title)

        self.theme_btn = QPushButton("Переключить тему")
        self.theme_btn.setObjectName("SecondaryButton")
        self.theme_btn.clicked.connect(self._toggle_theme)
        layout.addWidget(self.theme_btn)

        self.save_log_cb = None  # упрощено — логи через checkbox можно добавить позже

        btn_save = QPushButton("Сохранить настройки")
        btn_save.setObjectName("ActionButton")
        btn_save.clicked.connect(self._save_settings_from_ui)
        layout.addWidget(btn_save)

        layout.addStretch()

        self.screens["settings"] = w

    # ------------------------------------------------------------------
    # Действия
    # ------------------------------------------------------------------

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку загрузок")
        if folder:
            path = Path(folder)
            self.settings["downloads_folder"] = str(path)
            save_settings(self.settings)
            text = str(path)
            if hasattr(self, "folder_label"):
                self.folder_label.setText(text)
            if hasattr(self, "dashboard_folder_label"):
                self.dashboard_folder_label.setText(text)
            log_message(f"Выбрана папка загрузок: {path}", self.settings)
            self._refresh_counts()

    def _refresh_counts(self) -> None:
        folder_str = self.settings.get("downloads_folder", "")
        if hasattr(self, "dashboard_folder_label"):
            self.dashboard_folder_label.setText(folder_str or "Папка не выбрана")
        folder = Path(folder_str) if folder_str else None
        if not folder or not folder.exists():
            for card in self.category_cards.values():
                card.set_count(0)
            return
        counts = {key: 0 for key in self.category_cards}
        try:
            for p in folder.iterdir():
                if not p.is_file():
                    continue
                cat = categorize_file(p, self.settings)
                counts[cat] = counts.get(cat, 0) + 1
        except Exception:
            logging.exception("Не удалось подсчитать файлы")
        for key, card in self.category_cards.items():
            card.set_count(counts.get(key, 0))

    def _toggle_theme(self) -> None:
        self.settings["dark_theme"] = not bool(self.settings.get("dark_theme", True))
        save_settings(self.settings)
        self._apply_theme()
        log_message(f"Тема изменена: {'тёмная' if self.settings['dark_theme'] else 'светлая'}", self.settings)

    def _save_settings_from_ui(self) -> None:
        save_settings(self.settings)
        QMessageBox.information(self, "Настройки", "Настройки сохранены в settings.json")
        log_message("Настройки сохранены пользователем", self.settings)

    def _busy(self, busy: bool) -> None:
        self.btn_start_sort.setEnabled(not busy)
        self.btn_start_dup.setEnabled(not busy)
        self.btn_start_cleanup.setEnabled(not busy)
        self.btn_cancel_sort.setVisible(busy and self.current_worker is not None)
        self.btn_cancel_dup.setVisible(busy and self.current_worker is not None)
        self.btn_cancel_cleanup.setVisible(busy and self.current_worker is not None)

    def _cancel_worker(self) -> None:
        if self.current_worker is not None:
            self.current_worker.cancel()
            self.status_label.setText("Отмена...")

    # --- сортировка ---

    def _start_sort(self) -> None:
        folder = self.settings.get("downloads_folder")
        if not folder:
            QMessageBox.warning(self, "Папка не выбрана", "Сначала выберите папку загрузок.")
            return
        path = Path(folder)
        if not path.exists():
            QMessageBox.warning(self, "Папка не найдена", "Указанная папка не существует.")
            return

        self.sort_progress.show()
        self.sort_progress.setValue(0)
        self.sort_status.setText("Сортировка...")
        self._busy(True)

        worker = SortWorker(path, self.settings)
        worker.progress.connect(self._on_sort_progress)
        worker.file_done.connect(self._on_sort_file_done)
        worker.finished_signal.connect(self._on_sort_finished)
        worker.error.connect(self._on_worker_error)
        self.current_worker = worker
        worker.start()

    def _on_sort_progress(self, current: int, total: int) -> None:
        if total:
            self.sort_progress.setValue(int(current * 100 / total))
        self.sort_status.setText(f"Обработано {current} из {total}")

    def _on_sort_file_done(self, name: str, category: str) -> None:
        self.dashboard_log.add_entry(f"Перемещён: {name} → {CATEGORY_LABELS.get(category, category)}")
        self._refresh_counts()

    def _on_sort_finished(self, stats: dict) -> None:
        self._busy(False)
        self.sort_progress.hide()
        self.sort_status.setText(
            f"Готово. Перемещено: {stats['moved']}, пропущено: {stats['skipped']}, ошибок: {stats['errors']}"
        )
        log_message(
            f"Сортировка завершена: перемещено {stats['moved']}, пропущено {stats['skipped']}, ошибок {stats['errors']}",
            self.settings,
        )
        self.current_worker = None

    # --- дубликаты ---

    def _start_duplicates(self) -> None:
        folder = self.settings.get("downloads_folder")
        if not folder:
            QMessageBox.warning(self, "Папка не выбрана", "Сначала выберите папку загрузок.")
            return
        path = Path(folder)
        if not path.exists():
            QMessageBox.warning(self, "Папка не найдена", "Указанная папка не существует.")
            return

        self.dup_list.clear()
        self.dup_progress.show()
        self.dup_progress.setValue(0)
        self.dup_status.setText("Поиск дубликатов...")
        self._busy(True)

        worker = DuplicateWorker(path, self.settings)
        worker.progress.connect(self._on_dup_progress)
        worker.found.connect(self._on_duplicates_found)
        worker.finished_signal.connect(self._on_dup_finished)
        worker.error.connect(self._on_worker_error)
        self.current_worker = worker
        worker.start()

    def _on_dup_progress(self, current: int, total: int) -> None:
        if total:
            self.dup_progress.setValue(int(current * 100 / total))
        self.dup_status.setText(f"Просканировано {current} из {total}")

    def _on_duplicates_found(self, groups: list[list[Path]]) -> None:
        self.dup_list.clear()
        self.dup_groups = groups
        for group in groups:
            header = QListWidgetItem(f"Группа ({len(group)} копий):")
            header.setFlags(header.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.dup_list.addItem(header)
            for p in group:
                item = QListWidgetItem(str(p))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                self.dup_list.addItem(item)

    def _on_dup_finished(self) -> None:
        self._busy(False)
        self.dup_progress.hide()
        self.dup_status.setText(f"Найдено групп дубликатов: {len(getattr(self, 'dup_groups', []))}")
        log_message(f"Поиск дубликатов завершён: групп {len(getattr(self, 'dup_groups', []))}", self.settings)
        self.current_worker = None

    def _delete_selected_duplicates(self) -> None:
        groups = getattr(self, "dup_groups", [])
        if not groups:
            QMessageBox.information(self, "Нет данных", "Сначала выполните поиск дубликатов.")
            return
        to_delete: list[Path] = []
        for group in groups:
            # оставляем первый файл, остальные — Kandidaten на удаление
            for p in group[1:]:
                to_delete.append(p)
        if not to_delete:
            QMessageBox.information(self, "Нет дубликатов", "Дубликаты не найдены.")
            return
        reply = QMessageBox.question(
            self,
            "Удаление дубликатов",
            f"Удалить {len(to_delete)} дубликат(ов), оставив по одному экземпляру в каждой группе?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        deleted = 0
        errors = 0
        for p in to_delete:
            try:
                if is_file_locked(p):
                    errors += 1
                    continue
                p.unlink()
                deleted += 1
                log_message(f"Удалён дубликат: {p}", self.settings)
            except Exception:
                errors += 1
                logging.exception("Не удалось удалить дубликат %s", p)
        QMessageBox.information(self, "Готово", f"Удалено: {deleted}, ошибок: {errors}")
        log_message(f"Удалено дубликатов: {deleted}, ошибок: {errors}", self.settings)
        self.dup_list.clear()

    # --- очистка ---

    def _start_cleanup(self) -> None:
        folder = self.settings.get("downloads_folder")
        if not folder:
            QMessageBox.warning(self, "Папка не выбрана", "Сначала выберите папку загрузок.")
            return
        path = Path(folder)
        if not path.exists():
            QMessageBox.warning(self, "Папка не найдена", "Указанная папка не существует.")
            return

        days = self.cleanup_spin.value()
        self.settings["temp_cleanup_days"] = days
        save_settings(self.settings)

        self.cleanup_progress.show()
        self.cleanup_progress.setValue(0)
        self.cleanup_status.setText("Очистка...")
        self._busy(True)

        worker = CleanupWorker(path, self.settings)
        worker.progress.connect(self._on_cleanup_progress)
        worker.file_deleted.connect(self._on_cleanup_file_deleted)
        worker.finished_signal.connect(self._on_cleanup_finished)
        worker.error.connect(self._on_worker_error)
        self.current_worker = worker
        worker.start()

    def _on_cleanup_progress(self, current: int, total: int) -> None:
        if total:
            self.cleanup_progress.setValue(int(current * 100 / total))
        self.cleanup_status.setText(f"Проверено {current} из {total}")

    def _on_cleanup_file_deleted(self, name: str) -> None:
        self.dashboard_log.add_entry(f"Удалён временный файл: {name}")

    def _on_cleanup_finished(self, stats: dict) -> None:
        self._busy(False)
        self.cleanup_progress.hide()
        self.cleanup_status.setText(
            f"Готово. Удалено: {stats['deleted']}, пропущено: {stats['skipped']}, ошибок: {stats['errors']}"
        )
        log_message(
            f"Очистка завершена: удалено {stats['deleted']}, пропущено {stats['skipped']}, ошибок {stats['errors']}",
            self.settings,
        )
        self.current_worker = None

    def _on_worker_error(self, message: str) -> None:
        self._busy(False)
        self.sort_progress.hide()
        self.dup_progress.hide()
        self.cleanup_progress.hide()
        QMessageBox.critical(self, "Ошибка", message)
        log_message(f"Ошибка операции: {message}", self.settings)
        self.current_worker = None


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
