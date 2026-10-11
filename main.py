import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import flet as ft


# --- КОНСТАНТЫ ПРИЛОЖЕНИЯ И ВЕРСИОНИРОВАНИЕ ---

APP_NAME: str = "CleanerDev"
APP_VERSION: str = "v1.0.0"
GITHUB_AUTHOR: str = "hasipii"
GITHUB_REPO_URL: str = f"https://github.com/{GITHUB_AUTHOR}/{APP_NAME}"
GITHUB_API_URL: str = f"https://api.github.com/repos/{GITHUB_AUTHOR}/{APP_NAME}/releases/latest"
EMPTY_PLACEHOLDER_KEY: str = "__empty_placeholder_key__"


# --- СИСТЕМНЫЕ ПУТИ И КОНФИГУРАЦИЯ (PERSISTENCE) ---

def get_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR: str = get_base_dir()


def get_config_dir() -> str:
    appdata = os.getenv("APPDATA")
    if appdata:
        cfg_dir = os.path.join(appdata, APP_NAME)
        old_dir = os.path.join(appdata, "ClenerDev")
        if os.path.exists(old_dir) and not os.path.exists(cfg_dir):
            try:
                shutil.move(old_dir, cfg_dir)
            except Exception:
                pass
    else:
        cfg_dir = os.path.join(os.path.expanduser("~"), f".{APP_NAME.lower()}")
        old_dir = os.path.join(os.path.expanduser("~"), ".clenerdev")
        if os.path.exists(old_dir) and not os.path.exists(cfg_dir):
            try:
                shutil.move(old_dir, cfg_dir)
            except Exception:
                pass

    os.makedirs(cfg_dir, exist_ok=True)
    return cfg_dir


def get_config_file_path() -> str:
    return os.path.join(get_config_dir(), "settings.json")


def resolve_asset_path(filename: str) -> Optional[str]:
    if not filename:
        return None
    if os.path.isabs(filename) and os.path.isfile(filename):
        return filename

    candidates = [
        os.path.join(BASE_DIR, filename),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), filename),
        os.getcwd(),
        os.path.join(os.getcwd(), filename),
        os.path.join(get_config_dir(), "custom_media", filename),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return os.path.abspath(path)
    return None


DEFAULT_STATE: Dict[str, Any] = {
    "lang": "ru",
    "theme": "burgundy",
    "auto_scan": False,
    "auto_select": True,
    "stale_threshold_days": 60,
    "is_scanning": False,
    "patterns": [
        "node_modules", ".venv", "venv", "target", ".gradle", "build",
        ".next", ".turbo", ".nuxt", "__pycache__", ".pytest_cache",
        "obj", "bin", "Pods", ".dart_tool", ".sass-cache"
    ],
    "groups": {},
    "banner_mode": "preset",
    "banner_preset": "mup.gif",
    "banner_custom_path": ""
}


def load_persistent_settings() -> Dict[str, Any]:
    cfg_file = get_config_file_path()
    result = dict(DEFAULT_STATE)
    if os.path.isfile(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    for k, v in saved.items():
                        if k in result and k not in ("groups", "is_scanning"):
                            result[k] = v
        except Exception:
            pass
    return result


def save_persistent_settings(state: Dict[str, Any]) -> None:
    cfg_file = get_config_file_path()
    to_save = {
        "lang": state.get("lang", "ru"),
        "theme": state.get("theme", "burgundy"),
        "auto_scan": state.get("auto_scan", False),
        "auto_select": state.get("auto_select", True),
        "stale_threshold_days": state.get("stale_threshold_days", 60),
        "patterns": state.get("patterns", DEFAULT_STATE["patterns"]),
        "banner_mode": state.get("banner_mode", "preset"),
        "banner_preset": state.get("banner_preset", "mup.gif"),
        "banner_custom_path": state.get("banner_custom_path", "")
    }
    try:
        with open(cfg_file, "w", encoding="utf-8") as f:
            json.dump(to_save, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def parse_semantic_version(ver_text: str) -> Tuple[int, ...]:
    cleaned = ver_text.strip().lstrip("vV")
    numbers: List[int] = []
    for chunk in cleaned.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if digits:
            numbers.append(int(digits))
    return tuple(numbers) if numbers else (0,)


def is_remote_version_newer(latest_tag: str, current_version: str) -> bool:
    parsed_latest = parse_semantic_version(latest_tag)
    parsed_current = parse_semantic_version(current_version)
    if parsed_latest != parsed_current:
        return parsed_latest > parsed_current
    return latest_tag.strip().lower() != current_version.strip().lower()


# --- СИСТЕМНЫЕ ИСКЛЮЧЕНИЯ И КАТЕГОРИИ ---

IGNORED_SYSTEM_DIRS: Set[str] = {
    ".git", ".svn", ".hg", "$recycle.bin", "system volume information",
    "appdata", "local settings", "application data", "windows",
    "program files", "program files (x86)", ".rustup", ".cargo", ".nuget",
    ".docker", ".conda", ".cache", ".config", ".npm"
}

CATEGORY_META: Dict[str, Dict[str, Any]] = {
    "node_modules": {
        "tech": "Node.js / Web",
        "icon": ft.Icons.FOLDER_ROUNDED,
        "warn": False
    },
    ".venv": {
        "tech": "Python Virtualenv",
        "icon": ft.Icons.TERMINAL_ROUNDED,
        "warn": False
    },
    "venv": {
        "tech": "Python Virtualenv",
        "icon": ft.Icons.TERMINAL_ROUNDED,
        "warn": False
    },
    "__pycache__": {
        "tech": "Python Bytecode",
        "icon": ft.Icons.DATA_OBJECT_ROUNDED,
        "warn": False
    },
    ".pytest_cache": {
        "tech": "Pytest Cache",
        "icon": ft.Icons.BUG_REPORT_ROUNDED,
        "warn": False
    },
    "target": {
        "tech": "Rust / Cargo",
        "icon": ft.Icons.BUILD_ROUNDED,
        "warn": False
    },
    ".gradle": {
        "tech": "Gradle Cache",
        "icon": ft.Icons.ALL_INCLUSIVE_ROUNDED,
        "warn": False
    },
    "build": {
        "tech": "Build Output",
        "icon": ft.Icons.FOLDER_ZIP_ROUNDED,
        "warn": False
    },
    ".next": {
        "tech": "Next.js Build",
        "icon": ft.Icons.WEB_ROUNDED,
        "warn": False
    },
    ".turbo": {
        "tech": "Turborepo Cache",
        "icon": ft.Icons.SPEED_ROUNDED,
        "warn": False
    },
    ".nuxt": {
        "tech": "Nuxt.js Cache",
        "icon": ft.Icons.WINDOW_ROUNDED,
        "warn": False
    },
    "obj": {
        "tech": ".NET Intermediate",
        "icon": ft.Icons.CODE_ROUNDED,
        "warn": False
    },
    "bin": {
        "tech": ".NET Binaries",
        "icon": ft.Icons.STORAGE_ROUNDED,
        "warn": False
    },
    "Pods": {
        "tech": "iOS CocoaPods",
        "icon": ft.Icons.APPLE_ROUNDED,
        "warn": False
    },
    ".dart_tool": {
        "tech": "Flutter / Dart",
        "icon": ft.Icons.PHONE_ANDROID_ROUNDED,
        "warn": False
    },
    ".sass-cache": {
        "tech": "Sass Cache",
        "icon": ft.Icons.BRUSH_ROUNDED,
        "warn": False
    }
}

THEMES: Dict[str, Dict[str, str]] = {
    "burgundy": {
        "name": "Burgundy Dark (Material You)",
        "bg": "#1A1113",
        "sidebar": "#130B0D",
        "card": "#261719",
        "card_border": "#472427",
        "active_nav": "#38171B",
        "primary": "#C93B3B",
        "primary_dark": "#802126",
        "text_primary": "#FFFFFF",
        "text_muted": "#B59295",
        "accent": "#FF5A43",
        "input_bg": "#221315",
        "badge_bg": "#3B181B",
        "stop_btn": "#B71C1C",
        "gif_border": "#3D1E22",
        "shadow_color": "#000000"
    },
    "white": {
        "name": "Clean Snow White (Светлая)",
        "bg": "#F8FAFC",
        "sidebar": "#F1F5F9",
        "card": "#FFFFFF",
        "card_border": "#E2E8F0",
        "active_nav": "#FEE2E2",
        "primary": "#DC2626",
        "primary_dark": "#B91C1C",
        "text_primary": "#0F172A",
        "text_muted": "#64748B",
        "accent": "#DC2626",
        "input_bg": "#FFFFFF",
        "badge_bg": "#FEE2E2",
        "stop_btn": "#EF4444",
        "gif_border": "#CBD5E1",
        "shadow_color": "#E2E8F0"
    },
    "purple_black": {
        "name": "Cyber Purple & Black",
        "bg": "#0A0612",
        "sidebar": "#06030B",
        "card": "#140B24",
        "card_border": "#2D174E",
        "active_nav": "#220F3D",
        "primary": "#8B5CF6",
        "primary_dark": "#6D28D9",
        "text_primary": "#F9F8FE",
        "text_muted": "#B49FE6",
        "accent": "#A855F7",
        "input_bg": "#120821",
        "badge_bg": "#261042",
        "stop_btn": "#9333EA",
        "gif_border": "#281242",
        "shadow_color": "#000000"
    },
    "amethyst": {
        "name": "Amethyst Violet",
        "bg": "#1E132B",
        "sidebar": "#160C21",
        "card": "#2B1B3D",
        "card_border": "#4B3269",
        "active_nav": "#3A2352",
        "primary": "#A259FF",
        "primary_dark": "#7C3AED",
        "text_primary": "#FFFFFF",
        "text_muted": "#C4B5FD",
        "accent": "#C084FC",
        "input_bg": "#251736",
        "badge_bg": "#432863",
        "stop_btn": "#A21CAF",
        "gif_border": "#3A1F5B",
        "shadow_color": "#000000"
    },
    "monochrome": {
        "name": "Monochrome B&W (Чёрно-белая)",
        "bg": "#0D0D0D",
        "sidebar": "#050505",
        "card": "#171717",
        "card_border": "#2E2E2E",
        "active_nav": "#242424",
        "primary": "#E5E5E5",
        "primary_dark": "#404040",
        "text_primary": "#FFFFFF",
        "text_muted": "#9E9E9E",
        "accent": "#CCCCCC",
        "input_bg": "#121212",
        "badge_bg": "#262626",
        "stop_btn": "#4B5563",
        "gif_border": "#292929",
        "shadow_color": "#000000"
    }
}

TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "ru": {
        "scan_nav": "Поиск директорий",
        "history_nav": "История очистки",
        "exclusions_nav": "Исключения и правила",
        "settings_nav": "Настройки",
        "recoverable": "можно освободить",
        "clean_selected": "Очистить выбранное",
        "rescan": "Сканировать",
        "stop_scan": "Остановить",
        "scanning_status": "Идёт проверка...",
        "stopped_status": "Сканирование прервано пользователем",
        "idle_status": "Готов к сканированию",
        "projects_count": "проектов",
        "details_btn": "Подробнее",
        "open_explorer": "Открыть в проводнике",
        "details_title": "Обнаруженные цели:",
        "confirm_clean_title": "Подтверждение удаления",
        "confirm_clean_text": "Выбранные папки зависимостей и кэша ({size}) будут удалены безвозвратно. Исходные файлы проекта защищены.",
        "delete_forever": "Удалить безвозвратно",
        "cancel": "Отмена",
        "close": "Закрыть",
        "history_empty": "История очистки пуста.",
        "history_deleted": "Удалено",
        "clear_history": "Очистить историю",
        "theme_label": "Цветовая тема интерфейса",
        "lang_label": "Язык интерфейса",
        "auto_scan_label": "Автосканирование при старте приложения",
        "auto_select_label": "Автоматически отмечать галочками все найденные папки",
        "stale_days_label": "Подсвечивать неактивные проекты старше (дней):",
        "check_updates": "Проверить обновления",
        "checking_updates_status": "Связь с GitHub...",
        "up_to_date": "У вас последняя версия CleanerDev ({version})",
        "update_available": "Доступна новая версия на GitHub!",
        "update_dlg_title": "Новое обновление CleanerDev",
        "update_dlg_btn": "Перейти к скачиванию",
        "update_err_network": "Ошибка сети: проверьте подключение к интернету",
        "update_err_not_found": "Релизы в репозитории пока не опубликованы",
        "update_err_rate_limit": "Превышен лимит запросов к GitHub API. Повторите позже",
        "author_profile": "Мой GitHub: @hasipii",
        "patterns_title": "Отслеживаемые папки сборки и кэшей",
        "add_pattern_hint": "Имя директории (например: .cache)",
        "add_btn": "Добавить",
        "error_dir": "Указанная папка не существует на диске",
        "cleaned_success": "Успешно освобождено {size} дискового пространства!",
        "stale_badge": "Не активен",
        "search_hint": "Поиск проектов в результатах...",
        "no_targets": "Не выбрано ни одной папки для очистки",
        "banner_settings_title": "Боковая анимация и фон",
        "banner_preset_label": "Выберите готовую анимацию:",
        "banner_custom_btn": "Выбрать свой файл с ПК (GIF/PNG/JPG)",
        "banner_custom_hint": "Пользовательский файл:",
        "banner_reset_preset": "Вернуть стандартную гифку"
    },
    "en": {
        "scan_nav": "Scan Directories",
        "history_nav": "Trash History",
        "exclusions_nav": "Exclusions & Rules",
        "settings_nav": "Settings",
        "recoverable": "recoverable",
        "clean_selected": "Clean selected",
        "rescan": "Rescan",
        "stop_scan": "Stop",
        "scanning_status": "Scanning in progress...",
        "stopped_status": "Scan aborted by user",
        "idle_status": "Ready to scan",
        "projects_count": "projects",
        "details_btn": "Details",
        "open_explorer": "Open in File Explorer",
        "details_title": "Discovered targets:",
        "confirm_clean_title": "Confirm Cleanup",
        "confirm_clean_text": "Selected cache directories ({size}) will be permanently deleted. Your repository code is protected.",
        "delete_forever": "Delete Permanently",
        "cancel": "Cancel",
        "close": "Close",
        "history_empty": "Cleaning history is empty.",
        "history_deleted": "Deleted",
        "clear_history": "Clear History",
        "theme_label": "Visual Theme",
        "lang_label": "Interface Language",
        "auto_scan_label": "Auto-scan on application startup",
        "auto_select_label": "Automatically select all discovered caches",
        "stale_days_label": "Highlight stale projects older than (days):",
        "check_updates": "Check for updates",
        "checking_updates_status": "Connecting to GitHub...",
        "up_to_date": "You are on latest CleanerDev ({version})",
        "update_available": "New release available on GitHub!",
        "update_dlg_title": "New CleanerDev Update",
        "update_dlg_btn": "Download Update",
        "update_err_network": "Network error: check internet connection",
        "update_err_not_found": "No releases published on GitHub yet",
        "update_err_rate_limit": "GitHub API rate limit exceeded. Try again later",
        "author_profile": "Developer GitHub: @hasipii",
        "patterns_title": "Monitored build & cache directory names",
        "add_pattern_hint": "Directory name (e.g. .cache)",
        "add_btn": "Add",
        "error_dir": "Selected folder does not exist",
        "cleaned_success": "Successfully recovered {size}!",
        "stale_badge": "Stale",
        "search_hint": "Filter results...",
        "no_targets": "No folders selected for cleanup",
        "banner_settings_title": "Sidebar Animation & Background",
        "banner_preset_label": "Select preset animation:",
        "banner_custom_btn": "Choose file from PC (GIF/PNG/JPG)",
        "banner_custom_hint": "Custom file:",
        "banner_reset_preset": "Reset to preset animation"
    }
}


@dataclass
class DiscoveredFolder:
    path: str
    size_bytes: int
    mtime: float
    selected: bool = True

    @property
    def formatted_date(self) -> str:
        return datetime.fromtimestamp(self.mtime).strftime("%d.%m.%Y")

    def is_stale(self, threshold_days: int) -> bool:
        if self.mtime <= 0:
            return False
        age_days = (datetime.now().timestamp() - self.mtime) / (24 * 3600)
        return age_days > threshold_days


@dataclass
class CategoryGroup:
    key: str
    display_name: str
    is_warn: bool
    icon: str
    tech: str
    items: List[DiscoveredFolder]
    selected: bool = True

    @property
    def total_size(self) -> int:
        return sum(f.size_bytes for f in self.items if f.selected)

    @property
    def selected_count(self) -> int:
        return sum(1 for f in self.items if f.selected)

    @property
    def raw_count(self) -> int:
        return len(self.items)


def format_size(bytes_val: int) -> str:
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 ** 2:
        return f"{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 ** 3:
        return f"{bytes_val / (1024 ** 2):.1f} MB"
    else:
        return f"{bytes_val / (1024 ** 3):.2f} GB"


def open_in_file_explorer(target_path: str) -> None:
    try:
        norm_path = os.path.normpath(target_path)
        if not os.path.exists(norm_path):
            norm_path = os.path.dirname(norm_path)

        if sys.platform == "win32":
            os.startfile(norm_path)
        elif sys.platform == "darwin":
            subprocess.run(["open", norm_path], check=False)
        else:
            subprocess.run(["xdg-open", norm_path], check=False)
    except Exception:
        pass


def safe_dir_measure(folder_path: str, cancel_token: threading.Event) -> Tuple[int, float]:
    total_size = 0
    newest_mtime = 0.0

    try:
        newest_mtime = os.path.getmtime(folder_path)
    except OSError:
        pass

    stack: List[str] = [folder_path]
    visited_paths: Set[str] = set()

    while stack and not cancel_token.is_set():
        current_dir = stack.pop()
        try:
            real_path = os.path.realpath(current_dir)
            if real_path in visited_paths:
                continue
            visited_paths.add(real_path)

            with os.scandir(current_dir) as iterator:
                for entry in iterator:
                    if cancel_token.is_set():
                        break
                    try:
                        if entry.is_file(follow_symlinks=False):
                            stat_res = entry.stat(follow_symlinks=False)
                            total_size += stat_res.st_size
                            if stat_res.st_mtime > newest_mtime:
                                newest_mtime = stat_res.st_mtime
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                    except (PermissionError, FileNotFoundError, OSError):
                        continue
        except (PermissionError, FileNotFoundError, OSError):
            continue
        except Exception:
            continue

    return total_size, newest_mtime


def hard_delete_tree(path: str, allowed_names: Set[str]) -> Tuple[bool, str]:
    dir_name = os.path.basename(os.path.normpath(path)).lower()
    if dir_name not in allowed_names:
        return False, f"Защита исходного кода: каталог '{dir_name}' не в белом списке."

    def on_remove_readonly(func, file_path, _=None):
        try:
            os.chmod(file_path, stat.S_IWRITE | stat.S_IWUSR)
            func(file_path)
        except Exception:
            pass

    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=lambda fn, p, exc: on_remove_readonly(fn, p, exc))
        else:
            shutil.rmtree(path, onerror=lambda fn, p, exc: on_remove_readonly(fn, p, exc))
        return True, ""
    except Exception as e:
        return False, str(e)


# --- ОСНОВНОЙ ИНТЕРФЕЙС ПРИЛОЖЕНИЯ ---

def main(page: ft.Page) -> None:
    page.title = APP_NAME
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 16
    page.assets_dir = BASE_DIR

    state: Dict[str, Any] = load_persistent_settings()
    action_history: List[str] = []
    active_nav = "scan"
    cancel_scan_event = threading.Event()
    grid_cards_registry: Dict[str, ft.Container] = {}

    custom_icon_path = resolve_asset_path("icon.ico") or resolve_asset_path("icon.png")
    if custom_icon_path:
        try:
            if hasattr(page, "window") and page.window is not None:
                page.window.icon = custom_icon_path
            else:
                page.window_icon = custom_icon_path
        except Exception:
            pass

    try:
        if hasattr(page, "window") and page.window is not None:
            page.window.width = 1240
            page.window.height = 800
            page.window.min_width = 1000
            page.window.min_height = 670
        else:
            page.window_width = 1240
            page.window_height = 800
            page.window_min_width = 1000
            page.window_min_height = 670
    except Exception:
        pass

    def t(key: str, **kwargs) -> str:
        text = TRANSLATIONS.get(state["lang"], TRANSLATIONS["ru"]).get(key, key)
        return text.format(**kwargs) if kwargs else text

    def c(key: str) -> str:
        current_theme = THEMES.get(state["theme"], THEMES["burgundy"])
        return current_theme.get(key, "#FFFFFF")

    def show_dialog(dlg: ft.AlertDialog) -> None:
        if hasattr(page, "open"):
            page.open(dlg)
        else:
            page.dialog = dlg
            dlg.open = True
            page.update()

    def dismiss_dialog(dlg: ft.AlertDialog) -> None:
        if hasattr(page, "close"):
            page.close(dlg)
        else:
            dlg.open = False
            page.update()

    def show_notification(message: str, bg_color: Optional[str] = None) -> None:
        snack = ft.SnackBar(
            content=ft.Text(message, color="#FFFFFF", size=13),
            bgcolor=bg_color or c("primary_dark")
        )
        if hasattr(page, "open"):
            page.open(snack)
        else:
            page.snack_bar = snack
            snack.open = True
            page.update()

    def launch_browser(url: str) -> None:
        try:
            page.launch_url(url)
        except Exception:
            import webbrowser
            webbrowser.open(url)

    page.bgcolor = c("bg")

    default_dir = "D:\\Projects"
    if not os.path.exists(default_dir):
        home_projects = os.path.join(os.path.expanduser("~"), "Projects")
        default_dir = home_projects if os.path.exists(home_projects) else os.path.expanduser("~")

    path_input = ft.TextField(
        value=default_dir,
        border=ft.InputBorder.NONE,
        color=c("text_primary"),
        text_size=14,
        expand=True,
        content_padding=ft.padding.only(left=8, top=10, bottom=10)
    )

    summary_size_label = ft.Text("0 B", size=42, weight=ft.FontWeight.BOLD, color=c("accent"))
    summary_subtitle = ft.Text(t("recoverable"), size=14, color=c("text_muted"))

    clean_btn_text = ft.Text(t("clean_selected"), color="#FFFFFF", size=15, weight=ft.FontWeight.W_600)
    clean_btn = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.DELETE_OUTLINE_ROUNDED, color="#FFFFFF", size=20),
                clean_btn_text
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=8
        ),
        bgcolor=c("primary"),
        border_radius=18,
        height=52,
        alignment=ft.alignment.center,
        shadow=ft.BoxShadow(spread_radius=1, blur_radius=14, color=c("shadow_color"), offset=ft.Offset(0, 4)),
        animate=ft.Animation(220, ft.AnimationCurve.EASE_OUT),
        animate_scale=ft.Animation(180, ft.AnimationCurve.EASE_OUT_CUBIC)
    )

    def on_clean_btn_hover(e: ft.ControlEvent) -> None:
        if clean_btn.opacity is not None and clean_btn.opacity > 0.5:
            clean_btn.scale = 1.03 if e.data == "true" else 1.0
            try:
                clean_btn.update()
            except Exception:
                pass

    clean_btn.on_hover = on_clean_btn_hover

    def get_current_active_media_path() -> Optional[str]:
        if state["banner_mode"] == "custom" and state["banner_custom_path"]:
            if os.path.isfile(state["banner_custom_path"]):
                return state["banner_custom_path"]
        return resolve_asset_path(state.get("banner_preset", "mup.gif"))

    gif_image = ft.Image(
        src="",
        fit=ft.ImageFit.COVER,
        repeat=ft.ImageRepeat.NO_REPEAT,
        border_radius=ft.border_radius.all(14),
        expand=True
    )

    gif_placeholder_title = ft.Text(state.get("banner_preset", "mup.gif"), size=13, weight=ft.FontWeight.W_600, color=c("text_muted"))
    gif_placeholder = ft.Column(
        [
            ft.Icon(ft.Icons.ANIMATION_ROUNDED, size=38, color=c("text_muted")),
            gif_placeholder_title,
            ft.Text("Поместите файл в папку программы", size=11, color=c("text_muted"), text_align=ft.TextAlign.CENTER)
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=4
    )

    gif_container = ft.Container(
        content=gif_placeholder,
        expand=True,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        border=ft.border.all(1.2, c("gif_border")),
        border_radius=18,
        bgcolor=c("input_bg"),
        alignment=ft.alignment.center,
        margin=ft.margin.symmetric(vertical=8),
        animate=ft.Animation(180, ft.AnimationCurve.EASE_OUT)
    )

    def refresh_banner_view() -> None:
        resolved = get_current_active_media_path()
        if resolved and os.path.isfile(resolved):
            gif_image.src = resolved
            gif_container.content = gif_image
        else:
            gif_placeholder_title.value = os.path.basename(state.get("banner_custom_path") or state.get("banner_preset", "mup.gif"))
            gif_container.content = gif_placeholder
        try:
            gif_container.update()
        except Exception:
            page.update()

    refresh_banner_view()

    status_ring = ft.ProgressRing(width=14, height=14, stroke_width=2, color=c("accent"), visible=False)
    status_label = ft.Text(t("idle_status"), size=12, color=c("text_muted"), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)

    cards_grid = ft.GridView(
        expand=True,
        max_extent=320,
        child_aspect_ratio=1.75,
        spacing=14,
        run_spacing=14
    )

    content_container = ft.Container(expand=True)

    def on_dir_selected(e: ft.FilePickerResultEvent) -> None:
        if e.path:
            path_input.value = e.path
            page.update()

    def on_custom_media_selected(e: ft.FilePickerResultEvent) -> None:
        if e.files and len(e.files) > 0:
            selected_file = e.files[0]
            src_file_path = selected_file.path
            if src_file_path and os.path.isfile(src_file_path):
                media_store_dir = os.path.join(get_config_dir(), "custom_media")
                os.makedirs(media_store_dir, exist_ok=True)
                ext = os.path.splitext(src_file_path)[1]
                target_file_name = f"banner_user_{int(time.time())}{ext}"
                target_dest = os.path.join(media_store_dir, target_file_name)
                try:
                    shutil.copy2(src_file_path, target_dest)
                    state["banner_custom_path"] = target_dest
                except Exception:
                    state["banner_custom_path"] = src_file_path

                state["banner_mode"] = "custom"
                save_persistent_settings(state)
                refresh_banner_view()
                if active_nav == "settings":
                    switch_tab("settings")
                show_notification("Пользовательский баннер успешно сохранён!", c("card"))

    dir_picker = ft.FilePicker(on_result=on_dir_selected)
    custom_media_picker = ft.FilePicker(on_result=on_custom_media_selected)
    page.overlay.append(dir_picker)
    page.overlay.append(custom_media_picker)

    def recalculate_summary() -> None:
        total_bytes = sum(grp.total_size for grp in state["groups"].values())
        summary_size_label.value = format_size(total_bytes)
        summary_subtitle.value = t("recoverable")
        clean_btn.opacity = 1.0 if total_bytes > 0 else 0.4
        page.update()

    def show_group_inspector(grp: CategoryGroup) -> None:
        search_filter = ft.TextField(
            hint_text=t("search_hint"),
            dense=True,
            border_color=c("card_border"),
            bgcolor=c("input_bg"),
            color=c("text_primary")
        )

        items_list = ft.ListView(spacing=6, expand=True)

        def populate_items(filter_query: str = "") -> None:
            items_list.controls.clear()
            filter_query = filter_query.lower()

            for itm in grp.items:
                if filter_query and filter_query not in itm.path.lower():
                    continue

                def on_toggle_single(e: ft.ControlEvent, current: DiscoveredFolder = itm) -> None:
                    current.selected = bool(e.control.value)
                    grp.selected = any(i.selected for i in grp.items)
                    recalculate_summary()
                    update_card_widget(grp)

                is_stale = itm.is_stale(state["stale_threshold_days"])
                stale_badge = ft.Container(
                    content=ft.Text(t("stale_badge"), size=10, color="#FFA726", weight=ft.FontWeight.BOLD),
                    bgcolor=c("badge_bg"),
                    padding=ft.padding.symmetric(horizontal=6, vertical=2),
                    border_radius=6,
                    visible=is_stale
                )

                items_list.controls.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Checkbox(
                                    value=itm.selected,
                                    active_color=c("primary"),
                                    on_change=lambda e, curr=itm: on_toggle_single(e, curr)
                                ),
                                ft.Column(
                                    [
                                        ft.Text(
                                            itm.path,
                                            size=12,
                                            weight=ft.FontWeight.W_500,
                                            color=c("text_primary"),
                                            max_lines=1,
                                            overflow=ft.TextOverflow.ELLIPSIS
                                        ),
                                        ft.Row(
                                            [
                                                ft.Text(f"{itm.formatted_date}", size=11, color=c("text_muted")),
                                                stale_badge
                                            ],
                                            spacing=6
                                        )
                                    ],
                                    expand=True,
                                    spacing=2
                                ),
                                ft.Row(
                                    [
                                        ft.Text(format_size(itm.size_bytes), size=12, weight=ft.FontWeight.BOLD, color=c("accent")),
                                        ft.IconButton(
                                            icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                                            icon_color=c("accent"),
                                            icon_size=18,
                                            tooltip=t("open_explorer"),
                                            on_click=lambda _, p=itm.path: open_in_file_explorer(p)
                                        )
                                    ],
                                    spacing=6
                                )
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                        ),
                        bgcolor=c("input_bg"),
                        padding=ft.padding.symmetric(horizontal=10, vertical=6),
                        border_radius=10
                    )
                )

        def on_filter_change(_: ft.ControlEvent) -> None:
            populate_items(search_filter.value or "")
            page.update()

        search_filter.on_change = on_filter_change
        populate_items()

        def on_close_dialog(_: ft.ControlEvent) -> None:
            dismiss_dialog(dlg)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                [
                    ft.Icon(grp.icon, color=c("accent")),
                    ft.Text(f"{t('details_title')} {grp.display_name}", color=c("text_primary"))
                ]
            ),
            content=ft.Container(
                content=ft.Column([search_filter, items_list], spacing=10),
                width=580,
                height=390
            ),
            actions=[ft.TextButton(t("close"), on_click=on_close_dialog)],
            bgcolor=c("card")
        )
        show_dialog(dlg)

    def build_card_container(grp: CategoryGroup) -> ft.Container:
        def on_group_toggle(e: ft.ControlEvent, current_group: CategoryGroup = grp) -> None:
            current_group.selected = bool(e.control.value)
            for i in current_group.items:
                i.selected = current_group.selected
            recalculate_summary()
            update_card_widget(current_group)

        chk = ft.Checkbox(
            value=grp.selected,
            active_color=c("primary"),
            on_change=on_group_toggle
        )

        size_txt = ft.Text(
            format_size(grp.total_size),
            size=14,
            color=c("text_primary"),
            weight=ft.FontWeight.W_600
        )
        count_txt = ft.Text(
            f"{grp.selected_count}/{grp.raw_count} {t('projects_count')}",
            size=11,
            color=c("text_muted")
        )

        icon_badge = ft.Container(
            content=ft.Icon(grp.icon, size=18, color=c("accent")),
            bgcolor=c("badge_bg"),
            padding=6,
            border_radius=8
        )

        card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Row(
                                [
                                    chk,
                                    icon_badge,
                                    ft.Column(
                                        [
                                            ft.Text(grp.display_name, size=14, weight=ft.FontWeight.BOLD, color=c("text_primary")),
                                            ft.Text(grp.tech, size=10, color=c("text_muted"))
                                        ],
                                        spacing=1
                                    )
                                ],
                                spacing=8
                            ),
                            ft.IconButton(
                                icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                                icon_color=c("text_muted"),
                                icon_size=16,
                                tooltip=t("details_btn"),
                                on_click=lambda _, g=grp: show_group_inspector(g)
                            )
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                    ),
                    ft.Container(
                        content=ft.Row(
                            [
                                size_txt,
                                count_txt
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                        ),
                        padding=ft.padding.only(left=44, right=4)
                    )
                ],
                spacing=6
            ),
            bgcolor=c("card"),
            border=ft.border.all(1.2, c("card_border")),
            border_radius=16,
            padding=12,
            opacity=0.0,
            scale=0.90,
            offset=ft.Offset(0, 0.05),
            animate_opacity=ft.Animation(300, ft.AnimationCurve.EASE_OUT_CUBIC),
            animate_scale=ft.Animation(300, ft.AnimationCurve.EASE_OUT_BACK),
            animate_offset=ft.Animation(300, ft.AnimationCurve.EASE_OUT_CUBIC)
        )

        def on_card_hover(e: ft.ControlEvent) -> None:
            is_hovered = e.data == "true"
            card.scale = 1.02 if is_hovered else 1.0
            card.border = ft.border.all(1.2, c("accent") if is_hovered else c("card_border"))
            try:
                card.update()
            except Exception:
                pass

        card.on_hover = on_card_hover

        card.data = {
            "size_txt": size_txt,
            "count_txt": count_txt,
            "chk": chk,
            "group": grp
        }
        return card

    def update_card_widget(grp: CategoryGroup) -> None:
        if grp.key in grid_cards_registry:
            card = grid_cards_registry[grp.key]
            card.data["size_txt"].value = format_size(grp.total_size)
            card.data["count_txt"].value = f"{grp.selected_count}/{grp.raw_count} {t('projects_count')}"
            card.data["chk"].value = grp.selected
            page.update()

    def render_empty_state() -> None:
        cards_grid.controls.clear()
        grid_cards_registry.clear()
        cards_grid.controls.append(
            ft.Container(
                data=EMPTY_PLACEHOLDER_KEY,
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.SEARCH_ROUNDED, size=46, color=c("text_muted")),
                        ft.Text(f"Нажмите «{t('rescan')}» для проверки папок", size=13, color=c("text_muted"))
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=6
                ),
                alignment=ft.alignment.center
            )
        )
        recalculate_summary()

    def add_card_with_popup_animation(grp: CategoryGroup) -> None:
        if cards_grid.controls and getattr(cards_grid.controls[0], "data", None) == EMPTY_PLACEHOLDER_KEY:
            cards_grid.controls.clear()

        card = build_card_container(grp)
        grid_cards_registry[grp.key] = card
        cards_grid.controls.append(card)
        page.update()

        time.sleep(0.01)
        card.opacity = 1.0
        card.scale = 1.0
        card.offset = ft.Offset(0, 0)
        page.update()

    def scan_worker(target_dir: str) -> None:
        state["is_scanning"] = True
        cancel_scan_event.clear()
        rescan_btn.visible = False
        stop_btn.visible = True
        status_ring.visible = True
        status_label.value = t("scanning_status")

        cards_grid.controls.clear()
        grid_cards_registry.clear()
        state["groups"] = {}
        page.update()

        patterns_map: Dict[str, str] = {p.strip().lower(): p.strip() for p in state["patterns"] if p.strip()}
        last_ui_update_time = time.time()

        try:
            for root, dirs, _ in os.walk(target_dir, topdown=True):
                if cancel_scan_event.is_set():
                    break

                dirs[:] = [d for d in dirs if d.lower() not in IGNORED_SYSTEM_DIRS]

                matched_indices: List[int] = []
                for idx, dir_name in enumerate(dirs):
                    if cancel_scan_event.is_set():
                        break

                    dir_lower = dir_name.lower()
                    if dir_lower in patterns_map:
                        canonical_pattern = patterns_map[dir_lower]
                        full_p = os.path.join(root, dir_name)

                        now = time.time()
                        if now - last_ui_update_time > 0.08:
                            status_label.value = f"Анализ: {dir_name} в {os.path.basename(root)}"
                            page.update()
                            last_ui_update_time = now

                        sz, mt = safe_dir_measure(full_p, cancel_scan_event)
                        item = DiscoveredFolder(
                            path=full_p,
                            size_bytes=sz,
                            mtime=mt,
                            selected=state["auto_select"]
                        )

                        group_key = ".venv" if canonical_pattern in ("venv", ".venv") else canonical_pattern

                        if group_key not in state["groups"]:
                            meta = CATEGORY_META.get(group_key, {
                                "tech": "Зависимости",
                                "icon": ft.Icons.FOLDER_ROUNDED,
                                "warn": False
                            })
                            grp = CategoryGroup(
                                key=group_key,
                                display_name=group_key,
                                is_warn=meta["warn"],
                                icon=meta["icon"],
                                tech=meta["tech"],
                                items=[item],
                                selected=state["auto_select"]
                            )
                            state["groups"][group_key] = grp
                            add_card_with_popup_animation(grp)
                        else:
                            grp = state["groups"][group_key]
                            grp.items.append(item)
                            update_card_widget(grp)

                        recalculate_summary()
                        matched_indices.append(idx)

                for idx in reversed(matched_indices):
                    del dirs[idx]

            if cancel_scan_event.is_set():
                status_label.value = t("stopped_status")
            else:
                total_found = sum(len(g.items) for g in state["groups"].values())
                cats_count = len(state["groups"])
                status_label.value = f"Готово: найдено {total_found} папок в {cats_count} категориях"

        except Exception as ex:
            status_label.value = f"Ошибка сканирования: {str(ex)}"
        finally:
            state["is_scanning"] = False
            rescan_btn.visible = True
            stop_btn.visible = False
            status_ring.visible = False

            if not state["groups"]:
                render_empty_state()

            page.update()

    def start_scan(_: ft.ControlEvent) -> None:
        if state["is_scanning"]:
            return
        raw_path = path_input.value.strip().strip('"').strip("'")
        if not raw_path or not os.path.exists(raw_path):
            show_notification(t("error_dir"), c("primary_dark"))
            return
        threading.Thread(target=scan_worker, args=(raw_path,), daemon=True).start()

    def stop_scan(_: ft.ControlEvent) -> None:
        if state["is_scanning"]:
            cancel_scan_event.set()
            status_label.value = "Остановка процесса..."
            page.update()

    rescan_btn = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, color="#FFFFFF", size=18),
                ft.Text(t("rescan"), color="#FFFFFF", weight=ft.FontWeight.W_600, size=14)
            ],
            spacing=4,
            alignment=ft.MainAxisAlignment.CENTER
        ),
        bgcolor=c("primary_dark"),
        border_radius=22,
        padding=ft.padding.symmetric(horizontal=20, vertical=8),
        alignment=ft.alignment.center,
        on_click=start_scan,
        animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT)
    )

    stop_btn = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.STOP_ROUNDED, color="#FFFFFF", size=18),
                ft.Text(t("stop_scan"), color="#FFFFFF", weight=ft.FontWeight.W_600, size=14)
            ],
            spacing=4,
            alignment=ft.MainAxisAlignment.CENTER
        ),
        bgcolor=c("stop_btn"),
        border_radius=22,
        padding=ft.padding.symmetric(horizontal=20, vertical=8),
        alignment=ft.alignment.center,
        visible=False,
        on_click=stop_scan,
        animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT)
    )

    def delete_worker() -> None:
        targets: List[DiscoveredFolder] = []
        for g in state["groups"].values():
            for item in g.items:
                if item.selected:
                    targets.append(item)

        if not targets:
            return

        freed_bytes = 0
        deleted_paths: Set[str] = set()

        valid_patterns = {p.strip().lower() for p in state["patterns"] if p.strip()}
        if ".venv" in valid_patterns:
            valid_patterns.add("venv")

        for itm in targets:
            ok, err_msg = hard_delete_tree(itm.path, valid_patterns)
            if ok:
                freed_bytes += itm.size_bytes
                deleted_paths.add(itm.path)
                action_history.append(
                    f"{datetime.now().strftime('%H:%M:%S')} — {t('history_deleted')}: {itm.path} ({format_size(itm.size_bytes)})"
                )
            else:
                action_history.append(
                    f"{datetime.now().strftime('%H:%M:%S')} — Ошибка удаления {itm.path}: {err_msg}"
                )

        for key in list(state["groups"].keys()):
            g = state["groups"][key]
            g.items = [i for i in g.items if i.path not in deleted_paths]
            if not g.items:
                if key in grid_cards_registry:
                    cards_grid.controls.remove(grid_cards_registry[key])
                    del grid_cards_registry[key]
                del state["groups"][key]
            else:
                update_card_widget(g)

        if not state["groups"]:
            render_empty_state()

        recalculate_summary()
        show_notification(t("cleaned_success", size=format_size(freed_bytes)), c("card"))

    def confirm_cleaning(_: ft.ControlEvent) -> None:
        total_selected = sum(g.total_size for g in state["groups"].values())
        if total_selected == 0:
            show_notification(t("no_targets"), c("primary_dark"))
            return

        def on_ok(_: ft.ControlEvent) -> None:
            dismiss_dialog(dlg)
            threading.Thread(target=delete_worker, daemon=True).start()

        def on_cancel(_: ft.ControlEvent) -> None:
            dismiss_dialog(dlg)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([ft.Icon(ft.Icons.WARNING_ROUNDED, color=c("accent")), ft.Text(t("confirm_clean_title"), color=c("text_primary"))]),
            content=ft.Text(t("confirm_clean_text", size=format_size(total_selected)), color=c("text_muted"), size=13),
            actions=[
                ft.TextButton(t("cancel"), on_click=on_cancel),
                ft.ElevatedButton(t("delete_forever"), bgcolor=c("primary"), color="#FFFFFF", on_click=on_ok)
            ],
            bgcolor=c("card")
        )
        show_dialog(dlg)

    clean_btn.on_click = confirm_cleaning

    update_label = ft.Text(t("up_to_date", version=APP_VERSION), size=13, color=c("text_muted"))

    # --- ПРОВЕРКА ОБНОВЛЕНИЙ ЧЕРЕЗ GITHUB API ---

    def check_updates_worker() -> None:
        update_btn.disabled = True
        update_label.value = t("checking_updates_status")
        update_label.color = c("text_muted")
        page.update()

        try:
            req = urllib.request.Request(
                GITHUB_API_URL,
                headers={
                    "User-Agent": f"{APP_NAME}-App",
                    "Accept": "application/vnd.github.v3+json"
                }
            )
            with urllib.request.urlopen(req, timeout=5) as res:
                if res.status == 200:
                    data = json.loads(res.read().decode("utf-8"))
                    latest_tag = data.get("tag_name", "").strip()
                    release_url = data.get("html_url", f"{GITHUB_REPO_URL}/releases")
                    release_body = data.get("body", "Новые функции и исправления ошибок.").strip()

                    if latest_tag and is_remote_version_newer(latest_tag, APP_VERSION):
                        update_label.value = f"{t('update_available')} ({latest_tag})"
                        update_label.color = c("accent")

                        def on_download_click(_: ft.ControlEvent) -> None:
                            dismiss_dialog(update_dialog)
                            launch_browser(release_url)

                        def on_later_click(_: ft.ControlEvent) -> None:
                            dismiss_dialog(update_dialog)

                        notes_text = (release_body[:280] + "...") if len(release_body) > 280 else release_body

                        update_dialog = ft.AlertDialog(
                            modal=True,
                            title=ft.Row(
                                [
                                    ft.Icon(ft.Icons.SYSTEM_UPDATE_ROUNDED, color=c("accent")),
                                    ft.Text(f"{t('update_dlg_title')} {latest_tag}", color=c("text_primary"), size=16, weight=ft.FontWeight.BOLD)
                                ],
                                spacing=8
                            ),
                            content=ft.Container(
                                content=ft.Column(
                                    [
                                        ft.Text(
                                            f"Установлена версия: {APP_VERSION}\nДоступна версия: {latest_tag}",
                                            size=13,
                                            color=c("text_primary")
                                        ),
                                        ft.Container(height=4),
                                        ft.Text("Список изменений:", size=12, weight=ft.FontWeight.W_600, color=c("text_muted")),
                                        ft.Container(
                                            content=ft.Text(notes_text if notes_text else "Описание отсутствует.", size=12, color=c("text_muted")),
                                            bgcolor=c("input_bg"),
                                            padding=8,
                                            border_radius=8
                                        )
                                    ],
                                    spacing=6,
                                    tight=True
                                ),
                                width=440
                            ),
                            actions=[
                                ft.TextButton(t("cancel"), on_click=on_later_click),
                                ft.ElevatedButton(t("update_dlg_btn"), bgcolor=c("primary"), color="#FFFFFF", on_click=on_download_click)
                            ],
                            bgcolor=c("card")
                        )
                        show_dialog(update_dialog)
                    else:
                        update_label.value = t("up_to_date", version=APP_VERSION)
                        update_label.color = c("text_muted")
                        show_notification(t("up_to_date", version=APP_VERSION), c("card"))

        except urllib.error.HTTPError as http_err:
            if http_err.code == 404:
                update_label.value = t("update_err_not_found")
            elif http_err.code == 403:
                update_label.value = t("update_err_rate_limit")
            else:
                update_label.value = f"Ошибка сервера GitHub ({http_err.code})"
            update_label.color = c("accent")
        except (urllib.error.URLError, TimeoutError):
            update_label.value = t("update_err_network")
            update_label.color = c("accent")
        except Exception as ex:
            update_label.value = f"Сбой проверки: {type(ex).__name__}"
            update_label.color = c("accent")
        finally:
            update_btn.disabled = False
            page.update()

    update_btn = ft.ElevatedButton(
        t("check_updates"),
        icon=ft.Icons.SYNC_ROUNDED,
        bgcolor=c("primary_dark"),
        color="#FFFFFF",
        on_click=lambda _: threading.Thread(target=check_updates_worker, daemon=True).start()
    )

    sidebar_controls: Dict[str, Dict[str, Any]] = {}

    def create_nav_button(key: str, icon_data: str, label_key: str) -> ft.Container:
        indicator = ft.Container(
            width=4,
            height=18,
            bgcolor=c("accent"),
            border_radius=2,
            visible=(key == active_nav),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT)
        )
        icon = ft.Icon(
            icon_data,
            color=c("accent") if key == active_nav else c("text_muted"),
            size=18
        )
        text = ft.Text(
            t(label_key),
            color=c("text_primary") if key == active_nav else c("text_muted"),
            size=13,
            weight=ft.FontWeight.W_600 if key == active_nav else ft.FontWeight.W_500
        )
        container = ft.Container(
            content=ft.Row([indicator, icon, text], spacing=8),
            padding=ft.padding.symmetric(horizontal=8, vertical=10),
            border_radius=10,
            bgcolor=c("active_nav") if key == active_nav else None,
            on_click=lambda _: switch_tab(key),
            animate=ft.Animation(150, ft.AnimationCurve.EASE_OUT)
        )
        sidebar_controls[key] = {
            "container": container,
            "indicator": indicator,
            "icon": icon,
            "text": text,
            "label_key": label_key
        }
        return container

    nav_scan = create_nav_button("scan", ft.Icons.SEARCH_ROUNDED, "scan_nav")
    nav_hist = create_nav_button("history", ft.Icons.DELETE_OUTLINE_ROUNDED, "history_nav")
    nav_excl = create_nav_button("exclusions", ft.Icons.BLOCK_ROUNDED, "exclusions_nav")
    nav_sett = create_nav_button("settings", ft.Icons.SETTINGS_OUTLINED, "settings_nav")

    def switch_tab(tab_name: str) -> None:
        nonlocal active_nav
        active_nav = tab_name

        for k, item in sidebar_controls.items():
            is_active = (k == active_nav)
            item["indicator"].visible = is_active
            item["indicator"].bgcolor = c("accent")
            item["icon"].color = c("accent") if is_active else c("text_muted")
            item["text"].color = c("text_primary") if is_active else c("text_muted")
            item["text"].weight = ft.FontWeight.W_600 if is_active else ft.FontWeight.W_500
            item["container"].bgcolor = c("active_nav") if is_active else None

        if tab_name == "scan":
            content_container.content = scan_view
        elif tab_name == "history":
            def clear_history_act(_: ft.ControlEvent) -> None:
                action_history.clear()
                switch_tab("history")

            content_container.content = ft.Container(
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text(t("history_nav"), size=20, weight=ft.FontWeight.BOLD, color=c("text_primary")),
                                ft.ElevatedButton(t("clear_history"), bgcolor=c("primary_dark"), color="#FFFFFF", on_click=clear_history_act)
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                        ),
                        ft.Divider(color=c("card_border")),
                        ft.ListView(
                            [
                                ft.Text(h, size=13, color=c("text_muted"))
                                for h in (action_history if action_history else [t("history_empty")])
                            ],
                            expand=True,
                            spacing=8
                        )
                    ],
                    spacing=14
                ),
                padding=20
            )
        elif tab_name == "exclusions":
            new_pattern_input = ft.TextField(
                hint_text=t("add_pattern_hint"),
                dense=True,
                expand=True,
                bgcolor=c("input_bg"),
                border_color=c("card_border"),
                color=c("text_primary")
            )
            patterns_col = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)

            def build_patterns_ui() -> None:
                patterns_col.controls.clear()
                for pat in state["patterns"]:
                    def remove_pat(p_name: str = pat) -> None:
                        if p_name in state["patterns"]:
                            state["patterns"].remove(p_name)
                            save_persistent_settings(state)
                        build_patterns_ui()
                        page.update()

                    patterns_col.controls.append(
                        ft.Container(
                            content=ft.Row(
                                [
                                    ft.Text(pat, size=14, color=c("text_primary"), weight=ft.FontWeight.W_500),
                                    ft.IconButton(ft.Icons.DELETE_OUTLINE_ROUNDED, icon_color=c("text_muted"), on_click=lambda _, p=pat: remove_pat(p))
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN
                            ),
                            bgcolor=c("input_bg"),
                            border_radius=10,
                            padding=ft.padding.symmetric(horizontal=12, vertical=4)
                        )
                    )

            def add_new_pattern(_: ft.ControlEvent) -> None:
                v = new_pattern_input.value.strip()
                if v and v not in state["patterns"]:
                    state["patterns"].append(v)
                    save_persistent_settings(state)
                    new_pattern_input.value = ""
                    build_patterns_ui()
                    page.update()

            build_patterns_ui()

            content_container.content = ft.Container(
                content=ft.Column(
                    [
                        ft.Text(t("exclusions_nav"), size=20, weight=ft.FontWeight.BOLD, color=c("text_primary")),
                        ft.Text(t("patterns_title"), size=13, color=c("text_muted")),
                        ft.Row([new_pattern_input, ft.ElevatedButton(t("add_btn"), bgcolor=c("primary"), color="#FFFFFF", on_click=add_new_pattern)]),
                        ft.Divider(color=c("card_border")),
                        patterns_col
                    ],
                    spacing=12
                ),
                padding=20
            )
        elif tab_name == "settings":
            def on_lang_change(e: ft.ControlEvent) -> None:
                state["lang"] = e.control.value
                save_persistent_settings(state)
                apply_full_styles()

            def on_theme_change(e: ft.ControlEvent) -> None:
                state["theme"] = e.control.value
                save_persistent_settings(state)
                apply_full_styles()

            def on_auto_scan_toggle(e: ft.ControlEvent) -> None:
                state["auto_scan"] = bool(e.control.value)
                save_persistent_settings(state)

            def on_auto_select_toggle(e: ft.ControlEvent) -> None:
                state["auto_select"] = bool(e.control.value)
                save_persistent_settings(state)

            def on_stale_days_change(e: ft.ControlEvent) -> None:
                try:
                    state["stale_threshold_days"] = int(e.control.value)
                    save_persistent_settings(state)
                except ValueError:
                    pass

            def on_preset_gif_selected(preset_name: str) -> None:
                state["banner_mode"] = "preset"
                state["banner_preset"] = preset_name
                save_persistent_settings(state)
                refresh_banner_view()
                switch_tab("settings")

            preset_candidates = ["mup.gif", "mup2.gif", "mup3.gif"]
            try:
                for file_in_dir in os.listdir(BASE_DIR):
                    if file_in_dir.lower().endswith(".gif") and file_in_dir not in preset_candidates:
                        preset_candidates.append(file_in_dir)
            except Exception:
                pass

            preset_previews_row = ft.Row(spacing=12, scroll=ft.ScrollMode.AUTO)
            for p_name in preset_candidates:
                p_path = resolve_asset_path(p_name)
                is_selected = (state["banner_mode"] == "preset" and state["banner_preset"] == p_name)

                preview_img = ft.Image(
                    src=p_path if p_path else "",
                    width=90,
                    height=70,
                    fit=ft.ImageFit.COVER,
                    border_radius=8
                ) if p_path else ft.Container(
                    width=90,
                    height=70,
                    bgcolor=c("input_bg"),
                    alignment=ft.alignment.center,
                    content=ft.Icon(ft.Icons.IMAGE_NOT_SUPPORTED_ROUNDED, color=c("text_muted"))
                )

                preset_previews_row.controls.append(
                    ft.Container(
                        content=ft.Column(
                            [
                                preview_img,
                                ft.Text(p_name, size=11, color=c("text_primary") if is_selected else c("text_muted"), weight=ft.FontWeight.W_600 if is_selected else ft.FontWeight.NORMAL)
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=4
                        ),
                        bgcolor=c("active_nav") if is_selected else c("card"),
                        border=ft.border.all(2 if is_selected else 1, c("accent") if is_selected else c("card_border")),
                        border_radius=12,
                        padding=8,
                        on_click=lambda _, name=p_name: on_preset_gif_selected(name)
                    )
                )

            custom_file_label = ft.Text(
                f"{t('banner_custom_hint')} {os.path.basename(state['banner_custom_path'])}" if (state['banner_mode'] == 'custom' and state['banner_custom_path']) else "",
                size=12,
                color=c("accent")
            )

            content_container.content = ft.Container(
                content=ft.Column(
                    [
                        ft.Text(t("settings_nav"), size=20, weight=ft.FontWeight.BOLD, color=c("text_primary")),
                        ft.Divider(color=c("card_border")),
                        ft.Text(t("theme_label"), size=14, weight=ft.FontWeight.W_600, color=c("text_primary")),
                        ft.Dropdown(
                            value=state["theme"],
                            options=[
                                ft.dropdown.Option("burgundy", "Burgundy Dark (Material You)"),
                                ft.dropdown.Option("white", "Clean Snow White (Светлая)"),
                                ft.dropdown.Option("purple_black", "Cyber Purple & Black"),
                                ft.dropdown.Option("amethyst", "Amethyst Violet"),
                                ft.dropdown.Option("monochrome", "Monochrome B&W (Чёрно-белая)")
                            ],
                            on_change=on_theme_change,
                            width=320,
                            border_color=c("card_border")
                        ),
                        ft.Container(height=4),
                        ft.Text(t("lang_label"), size=14, weight=ft.FontWeight.W_600, color=c("text_primary")),
                        ft.Dropdown(
                            value=state["lang"],
                            options=[
                                ft.dropdown.Option("ru", "Русский"),
                                ft.dropdown.Option("en", "English")
                            ],
                            on_change=on_lang_change,
                            width=320,
                            border_color=c("card_border")
                        ),
                        ft.Container(height=6),
                        ft.Divider(color=c("card_border")),
                        ft.Text(t("banner_settings_title"), size=15, weight=ft.FontWeight.BOLD, color=c("text_primary")),
                        ft.Text(t("banner_preset_label"), size=13, color=c("text_muted")),
                        preset_previews_row,
                        ft.Container(height=4),
                        ft.Row(
                            [
                                ft.ElevatedButton(
                                    t("banner_custom_btn"),
                                    icon=ft.Icons.UPLOAD_FILE_ROUNDED,
                                    bgcolor=c("primary"),
                                    color="#FFFFFF",
                                    on_click=lambda _: custom_media_picker.pick_files(
                                        dialog_title="Выберите баннер (GIF, PNG, JPG)",
                                        allowed_extensions=["gif", "png", "jpg", "jpeg"]
                                    )
                                ),
                                ft.OutlinedButton(
                                    t("banner_reset_preset"),
                                    on_click=lambda _: on_preset_gif_selected("mup.gif")
                                )
                            ],
                            spacing=12
                        ),
                        custom_file_label,
                        ft.Divider(color=c("card_border")),
                        ft.Switch(
                            label=t("auto_scan_label"),
                            value=state["auto_scan"],
                            active_color=c("primary"),
                            on_change=on_auto_scan_toggle
                        ),
                        ft.Switch(
                            label=t("auto_select_label"),
                            value=state["auto_select"],
                            active_color=c("primary"),
                            on_change=on_auto_select_toggle
                        ),
                        ft.Container(height=6),
                        ft.Row(
                            [
                                ft.Text(t("stale_days_label"), size=13, color=c("text_primary")),
                                ft.Dropdown(
                                    value=str(state["stale_threshold_days"]),
                                    options=[
                                        ft.dropdown.Option("30", "30 дней"),
                                        ft.dropdown.Option("60", "60 дней"),
                                        ft.dropdown.Option("90", "90 дней"),
                                        ft.dropdown.Option("180", "180 дней")
                                    ],
                                    on_change=on_stale_days_change,
                                    width=130,
                                    dense=True,
                                    border_color=c("card_border")
                                )
                            ],
                            spacing=12
                        ),
                        ft.Container(height=14),
                        ft.Row(
                            [
                                update_btn,
                                update_label
                            ],
                            spacing=12
                        ),
                        ft.Container(height=10),
                        ft.ElevatedButton(
                            t("author_profile"),
                            icon=ft.Icons.CODE_ROUNDED,
                            bgcolor=c("card"),
                            color=c("text_primary"),
                            on_click=lambda _: launch_browser(f"https://github.com/{GITHUB_AUTHOR}")
                        )
                    ],
                    spacing=8,
                    scroll=ft.ScrollMode.AUTO
                ),
                padding=20
            )

        page.update()

    def apply_full_styles() -> None:
        page.theme_mode = ft.ThemeMode.LIGHT if state["theme"] == "white" else ft.ThemeMode.DARK
        page.bgcolor = c("bg")
        path_input.color = c("text_primary")
        path_panel.bgcolor = c("input_bg")
        path_panel.border = ft.border.all(1.2, c("card_border"))
        folder_browse_btn.icon_color = c("text_muted")
        summary_panel.bgcolor = c("card")
        summary_panel.border = ft.border.all(1.2, c("card_border"))
        summary_size_label.color = c("accent")
        clean_btn.bgcolor = c("primary")
        if clean_btn.shadow:
            clean_btn.shadow.color = c("shadow_color")
        clean_btn_text.value = t("clean_selected")
        rescan_btn.bgcolor = c("primary_dark")
        rescan_btn.content.controls[1].value = t("rescan")
        stop_btn.bgcolor = c("stop_btn")
        stop_btn.content.controls[1].value = t("stop_scan")
        sidebar_panel.bgcolor = c("sidebar")
        author_badge.bgcolor = c("card")
        author_badge.border = ft.border.all(1, c("card_border"))
        author_badge.content.controls[0].color = c("accent")
        author_text.color = c("text_muted")
        logo_title.color = c("text_primary")
        status_ring.color = c("accent")

        gif_container.bgcolor = c("input_bg")
        gif_container.border = ft.border.all(1.2, c("gif_border"))

        for item in sidebar_controls.values():
            item["text"].value = t(item["label_key"])

        update_btn.text = t("check_updates")
        update_btn.bgcolor = c("primary_dark")

        refresh_banner_view()
        switch_tab(active_nav)

        if state["groups"]:
            cards_grid.controls.clear()
            grid_cards_registry.clear()
            for grp in state["groups"].values():
                card = build_card_container(grp)
                card.opacity = 1.0
                card.scale = 1.0
                card.offset = ft.Offset(0, 0)
                grid_cards_registry[grp.key] = card
                cards_grid.controls.append(card)
            page.update()
        else:
            render_empty_state()

    author_text = ft.Text(f"github.com/{GITHUB_AUTHOR}", size=11, color=c("text_muted"), weight=ft.FontWeight.W_500)
    author_badge = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.CODE_ROUNDED, color=c("accent"), size=16),
                author_text
            ],
            spacing=6,
            alignment=ft.MainAxisAlignment.CENTER
        ),
        bgcolor=c("card"),
        border=ft.border.all(1, c("card_border")),
        padding=ft.padding.symmetric(horizontal=10, vertical=8),
        border_radius=10,
        tooltip="Открыть профиль автора",
        on_click=lambda _: launch_browser(f"https://github.com/{GITHUB_AUTHOR}")
    )

    png_logo_path = resolve_asset_path("icon.png")
    logo_icon_widget = ft.Image(
        src=png_logo_path,
        width=28,
        height=28,
        fit=ft.ImageFit.CONTAIN
    ) if png_logo_path else ft.Icon(
        ft.Icons.CLEANING_SERVICES_ROUNDED,
        size=26,
        color=c("accent")
    )

    logo_title = ft.Text(APP_NAME, size=20, weight=ft.FontWeight.BOLD, color=c("text_primary"))

    sidebar_panel = ft.Container(
        content=ft.Column(
            [
                ft.Container(
                    content=ft.Row(
                        [
                            logo_icon_widget,
                            logo_title
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER
                    ),
                    padding=ft.padding.only(left=8, top=6, bottom=16)
                ),
                nav_scan,
                nav_hist,
                nav_excl,
                nav_sett,
                ft.Container(expand=True),
                author_badge
            ],
            spacing=4
        ),
        width=190,
        padding=ft.padding.only(right=12)
    )

    folder_browse_btn = ft.IconButton(
        icon=ft.Icons.FOLDER_OUTLINED,
        icon_color=c("text_muted"),
        tooltip="Выбрать папку",
        on_click=lambda _: dir_picker.get_directory_path(dialog_title="Выберите директорию")
    )

    path_panel = ft.Container(
        content=ft.Row(
            [
                folder_browse_btn,
                path_input,
                ft.Row([rescan_btn, stop_btn], spacing=6)
            ],
            spacing=8,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN
        ),
        bgcolor=c("input_bg"),
        border=ft.border.all(1.2, c("card_border")),
        border_radius=22,
        padding=ft.padding.symmetric(horizontal=12, vertical=4)
    )

    summary_panel = ft.Container(
        content=ft.Column(
            [
                ft.Container(
                    content=ft.Column([summary_size_label, summary_subtitle], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=ft.padding.only(top=14, bottom=4)
                ),
                gif_container,
                clean_btn
            ],
            spacing=0,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER
        ),
        bgcolor=c("card"),
        border=ft.border.all(1.2, c("card_border")),
        border_radius=22,
        width=250,
        padding=16
    )

    status_bar = ft.Container(
        content=ft.Row(
            [
                status_ring,
                status_label
            ],
            spacing=8,
            alignment=ft.MainAxisAlignment.CENTER
        ),
        padding=ft.padding.symmetric(vertical=4)
    )

    scan_view = ft.Column(
        [
            path_panel,
            ft.Row([ft.Container(content=cards_grid, expand=True), summary_panel], expand=True, spacing=16),
            status_bar
        ],
        expand=True,
        spacing=14
    )

    content_container.content = scan_view

    page.add(
        ft.Row(
            [
                sidebar_panel,
                ft.Container(content=content_container, expand=True)
            ],
            expand=True,
            spacing=10
        )
    )

    render_empty_state()

    if state["auto_scan"]:
        threading.Thread(target=scan_worker, args=(default_dir,), daemon=True).start()


if __name__ == "__main__":
    try:
        ft.app(target=main, assets_dir=BASE_DIR)
    except TypeError:
        ft.app(target=main)