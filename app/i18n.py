"""UI / server message translations (Russian and English).

The language of a request is the user's own choice (the `lang` cookie set by
the language switch, kept for a year), else `defaultlang` from config.ini:
`en` / `ru` fixed, or `auto` = the browser's Accept-Language, falling back to
the OS language. Work that isn't tied to a request (IPP jobs) uses
DEFAULT_LANG: `defaultlang`, where auto = the OS language.

Keys starting with "web." are also sent to the browser (see js_messages)
for strings built in JavaScript. Placeholders use str.format syntax on the
server and the same {name} syntax in JS."""

import locale
from contextvars import ContextVar

from .config import settings

LANGS = ("ru", "en")
COOKIE = "lang"


def _system_lang() -> str:
    try:
        name = (locale.getlocale()[0] or "").lower()
    except ValueError:
        name = ""
    return "ru" if name.startswith(("ru", "russian")) else "en"


DEFAULT_LANG = settings.defaultlang if settings.defaultlang in LANGS else _system_lang()
current_lang: ContextVar[str] = ContextVar("current_lang", default=DEFAULT_LANG)


def pick_lang(cookie: str | None, accept_language: str | None) -> str:
    if cookie in LANGS:
        return cookie
    if settings.defaultlang in LANGS:
        return settings.defaultlang  # fixed default: the browser's language doesn't matter
    for part in (accept_language or "").split(","):
        tag = part.split(";")[0].strip().lower()
        if tag.startswith("ru"):
            return "ru"
        if tag.startswith("en"):
            return "en"
    return DEFAULT_LANG


def t(key: str, **params) -> str:
    entry = MESSAGES.get(key)
    if entry is None:
        return key
    text = entry.get(current_lang.get()) or entry["en"]
    return text.format(**params) if params else text


def js_messages() -> dict[str, str]:
    lang = current_lang.get()
    return {k[4:]: v.get(lang) or v["en"] for k, v in MESSAGES.items() if k.startswith("web.")}


MESSAGES: dict[str, dict[str, str]] = {
    # --- Page (template) -------------------------------------------------------
    "web.title": {"ru": "MFP Print & Scan Server", "en": "MFP Print & Scan Server"},
    "web.tab_print": {"ru": "Печать", "en": "Print"},
    "web.tab_scan": {"ru": "Сканирование", "en": "Scan"},
    "web.language": {"ru": "Язык", "en": "Language"},
    "web.dropzone": {"ru": "Перетащите файл сюда или нажмите, чтобы выбрать", "en": "Drop a file here or click to choose"},
    "web.printer": {"ru": "Принтер", "en": "Printer"},
    "web.copies": {"ru": "Копий", "en": "Copies"},
    "web.copies_short": {"ru": "Коп.", "en": "Qty"},
    "web.preview": {"ru": "Предпросмотр", "en": "Preview"},
    "web.print": {"ru": "Печать", "en": "Print"},
    "web.hide": {"ru": "Скрыть", "en": "Hide"},
    "web.maintenance": {"ru": "Обслуживание", "en": "Maintenance"},
    "web.recent_jobs": {"ru": "Последние задания", "en": "Recent jobs"},
    "web.clear": {"ru": "Очистить", "en": "Clear"},
    "web.refresh": {"ru": "Обновить", "en": "Refresh"},
    "web.col_file": {"ru": "Файл", "en": "File"},
    "web.col_printer": {"ru": "Принтер", "en": "Printer"},
    "web.col_options": {"ru": "Настройки", "en": "Settings"},
    "web.col_status": {"ru": "Статус", "en": "Status"},
    "web.col_time": {"ru": "Время", "en": "Time"},
    "web.no_jobs": {"ru": "Пока нет заданий", "en": "No jobs yet"},
    "web.scan_hint": {
        "ru": "Положите документ лицом вниз в угол стекла и нажмите «Предпросмотр»",
        "en": "Place the document face down in the corner of the glass and press “Preview”",
    },
    "web.scan_caption": {
        "ru": "Выделите мышью область для сканирования. Без выделения сканируется всё стекло.",
        "en": "Drag on the preview to select an area. Without a selection the whole glass is scanned.",
    },
    "web.scanner": {"ru": "Сканер", "en": "Scanner"},
    "web.area": {"ru": "Область", "en": "Area"},
    "web.area_all": {"ru": "Всё стекло", "en": "Whole glass"},
    "web.area_photo": {"ru": "Фото 10×15", "en": "Photo 4×6"},
    "web.area_card": {"ru": "Визитка (90×50)", "en": "Business card (90×50)"},
    "web.area_custom": {"ru": "Своя область", "en": "Custom area"},
    "web.mode": {"ru": "Режим", "en": "Mode"},
    "web.resolution": {"ru": "Разрешение", "en": "Resolution"},
    "web.brightness": {"ru": "Яркость", "en": "Brightness"},
    "web.contrast": {"ru": "Контраст", "en": "Contrast"},
    "web.format": {"ru": "Формат", "en": "Format"},
    "web.scan": {"ru": "Сканировать", "en": "Scan"},
    "web.scans": {"ru": "Сканы", "en": "Scans"},
    "web.merge_pdf": {"ru": "Собрать PDF из выбранных", "en": "Merge selected into PDF"},
    "web.no_scans": {"ru": "Пока нет сканов", "en": "No scans yet"},

    # --- Login -------------------------------------------------------------------
    "web.login_title": {"ru": "Вход", "en": "Sign in"},
    "web.username": {"ru": "Имя пользователя", "en": "User name"},
    "web.password": {"ru": "Пароль", "en": "Password"},
    "web.remember": {"ru": "Запомнить меня", "en": "Remember me"},
    "web.login_button": {"ru": "Войти", "en": "Sign in"},
    "web.logout": {"ru": "Выйти", "en": "Sign out"},
    "auth.failed": {"ru": "Неверное имя пользователя или пароль", "en": "Wrong user name or password"},
    "auth.locked": {
        "ru": "Слишком много неудачных попыток — попробуйте через несколько минут",
        "en": "Too many failed attempts — try again in a few minutes",
    },
    "auth.required": {"ru": "Нужно войти", "en": "Sign-in required"},

    # --- Page (JavaScript) -----------------------------------------------------
    "web.status_queued": {"ru": "В очереди", "en": "Queued"},
    "web.status_sent": {"ru": "Отправлено", "en": "Sent"},
    "web.status_failed": {"ru": "Ошибка", "en": "Failed"},
    "web.no_printers": {"ru": "Принтеры не найдены", "en": "No printers found"},
    "web.default_suffix": {"ru": "{name} (по умолчанию)", "en": "{name} (default)"},
    "web.default_printer": {"ru": "по умолчанию", "en": "default"},
    "web.printers_failed": {"ru": "Не удалось загрузить список принтеров", "en": "Couldn't load the printer list"},
    "web.limit_hint": {
        "ru": "{label}: «{from}» здесь не подходит — выбрано «{to}».",
        "en": "{label}: “{from}” doesn't fit here — switched to “{to}”.",
    },
    "web.error": {"ru": "Ошибка", "en": "Error"},
    "web.error_with": {"ru": "Ошибка: {error}", "en": "Error: {error}"},
    "web.action_running": {"ru": "{action}...", "en": "{action}..."},
    "web.action_sent": {"ru": "{action}: отправлено на принтер", "en": "{action}: sent to the printer"},
    "web.preview_loading": {"ru": "Готовлю предпросмотр...", "en": "Preparing preview..."},
    "web.preview_error": {"ru": "Ошибка предпросмотра", "en": "Preview failed"},
    "web.preview_info": {"ru": "Лист {w}×{h} мм · страниц: {pages}", "en": "Sheet {w}×{h} mm · pages: {pages}"},
    "web.preview_first": {"ru": " (показаны первые {shown})", "en": " (first {shown} shown)"},
    "web.preview_approx": {
        "ru": " · поля принтера неизвестны, показано примерно",
        "en": " · printer margins unknown, approximate",
    },
    "web.page_n": {"ru": "Страница {n}", "en": "Page {n}"},
    "web.delete_from_history": {"ru": "Удалить из истории", "en": "Remove from history"},
    "web.sending": {"ru": "Отправка...", "en": "Sending..."},
    "web.print_error": {"ru": "Ошибка печати", "en": "Print failed"},
    "web.print_sent": {"ru": "Файл отправлен на печать", "en": "File sent to the printer"},
    "web.confirm_clear": {
        "ru": "Удалить всю историю печати? Задания, которые ещё печатаются, останутся.",
        "en": "Clear the whole print history? Jobs still printing are kept.",
    },
    "web.searching_scanners": {"ru": "Поиск сканеров...", "en": "Looking for scanners..."},
    "web.scanners_failed": {"ru": "Не удалось получить список сканеров", "en": "Couldn't get the scanner list"},
    "web.no_scanners": {"ru": "Сканеры не найдены", "en": "No scanners found"},
    "web.scanner_missing": {"ru": "Сканер не найден — он включён и подключён?", "en": "No scanner found — is it on and connected?"},
    "web.caps_failed": {"ru": "Не удалось получить возможности сканера", "en": "Couldn't get the scanner's capabilities"},
    "web.estimate": {"ru": "{w}×{h} мм → {pw}×{ph} px", "en": "{w}×{h} mm → {pw}×{ph} px"},
    "web.estimate_raw": {"ru": " (≈{mb} МБ без сжатия)", "en": " (≈{mb} MB uncompressed)"},
    "web.scan_previewing": {"ru": "Предпросмотр... (несколько секунд)", "en": "Previewing... (a few seconds)"},
    "web.scanning": {
        "ru": "Сканирование... при высоком разрешении это может занять до минуты",
        "en": "Scanning... at high resolution this can take up to a minute",
    },
    "web.scan_error": {"ru": "Ошибка сканирования", "en": "Scan failed"},
    "web.scan_done": {"ru": "Готово: {name}", "en": "Done: {name}"},
    "web.mb": {"ru": "{n} МБ", "en": "{n} MB"},
    "web.kb": {"ru": "{n} КБ", "en": "{n} KB"},
    "web.pages_n": {"ru": "{n} стр.", "en": "{n} pages"},
    "web.select_for_pdf": {"ru": "Выбрать для сборки PDF", "en": "Select for merging into a PDF"},
    "web.download": {"ru": "Скачать", "en": "Download"},
    "web.print_actual_size": {"ru": "Напечатать в натуральную величину", "en": "Print at actual size"},
    "web.delete": {"ru": "Удалить", "en": "Delete"},
    "web.confirm_delete": {"ru": "Удалить {name}?", "en": "Delete {name}?"},
    "web.confirm_print_scan": {"ru": "Напечатать {name} на «{printer}»?", "en": "Print {name} on “{printer}”?"},
    "web.scan_printed": {"ru": "{name} отправлен на печать", "en": "{name} sent to the printer"},
    "web.merge_error": {"ru": "Ошибка сборки PDF", "en": "Couldn't merge into PDF"},
    "web.merged": {"ru": "Собран {name} ({pages} стр.)", "en": "Created {name} ({pages} pages)"},

    # --- Print options -----------------------------------------------------------
    "opt.quality": {"ru": "Качество печати", "en": "Print quality"},
    "opt.quality.draft": {"ru": "Черновик", "en": "Draft"},
    "opt.quality.normal": {"ru": "Обычное", "en": "Normal"},
    "opt.quality.high": {"ru": "Высокое", "en": "High"},
    "opt.color": {"ru": "Цветность", "en": "Color"},
    "opt.color.color": {"ru": "Цветная", "en": "Color"},
    "opt.color.mono": {"ru": "Чёрно-белая", "en": "Black & white"},
    "opt.duplex": {"ru": "Двусторонняя печать", "en": "Two-sided"},
    "opt.duplex.simplex": {"ru": "Односторонняя", "en": "One-sided"},
    "opt.duplex.duplex_long": {"ru": "Двусторонняя (длинный край)", "en": "Two-sided (long edge)"},
    "opt.duplex.duplex_short": {"ru": "Двусторонняя (короткий край)", "en": "Two-sided (short edge)"},
    "opt.paper_size": {"ru": "Размер бумаги", "en": "Paper size"},
    "opt.media_type": {"ru": "Тип бумаги", "en": "Paper type"},

    # --- Scan modes -----------------------------------------------------------------
    "scan.mode.color": {"ru": "Цветной", "en": "Color"},
    "scan.mode.gray": {"ru": "Оттенки серого", "en": "Grayscale"},
    "scan.mode.lineart": {"ru": "Чёрно-белый (текст)", "en": "Black & white (text)"},

    # --- Maintenance --------------------------------------------------------------
    "mnt.test_page": {"ru": "Пробная страница", "en": "Test page"},
    "mnt.test_page.desc": {"ru": "Стандартная пробная страница ОС для этого принтера.", "en": "The OS's standard test page for this printer."},
    "mnt.test_page.confirm": {"ru": "Напечатать пробную страницу? Будет использован 1 лист.", "en": "Print a test page? Uses 1 sheet."},
    "mnt.nozzle_check": {"ru": "Проверка дюз", "en": "Nozzle check"},
    "mnt.nozzle_check.desc": {
        "ru": "Печатает шаблон проверки дюз: если в нём есть пропуски, нужна очистка головки.",
        "en": "Prints the nozzle check pattern: gaps in it mean the head needs cleaning.",
    },
    "mnt.nozzle_check.confirm": {
        "ru": "Напечатать шаблон проверки дюз? Будет использован 1 лист.",
        "en": "Print the nozzle check pattern? Uses 1 sheet.",
    },
    "mnt.head_cleaning": {"ru": "Очистка головки", "en": "Head cleaning"},
    "mnt.head_cleaning.desc": {
        "ru": "Прочистка печатающей головки. Расходует чернила — делайте, только если проверка дюз показала пропуски.",
        "en": "Cleans the print head. Uses ink — only do it if the nozzle check shows gaps.",
    },
    "mnt.head_cleaning.confirm": {
        "ru": "Запустить очистку печатающей головки? Это расходует чернила, принтер будет занят около минуты.",
        "en": "Start print head cleaning? It uses ink and keeps the printer busy for about a minute.",
    },

    # --- Server errors & notes ---------------------------------------------------
    "err.bad_options": {"ru": "Некорректный формат параметров печати", "en": "Invalid print options format"},
    "err.file_too_large": {"ru": "Файл слишком большой", "en": "File is too large"},
    "err.bad_copies": {"ru": "Некорректное количество копий", "en": "Invalid number of copies"},
    "err.action_unsupported": {"ru": "Это действие не поддерживается для этого принтера", "en": "This action isn't supported by this printer"},
    "err.no_preview": {
        "ru": "Для файлов {ext} предпросмотр недоступен. Он есть для PDF и изображений, а для документов и текста — если на сервере установлен LibreOffice.",
        "en": "No preview for {ext} files. Preview works for PDFs and images, and for documents and text if LibreOffice is installed on the server.",
    },
    "err.this_type": {"ru": "этого типа", "en": "this type"},
    "err.ipp_windows_only": {
        "ru": "IPP доступен только на Windows (на Linux используйте общий доступ CUPS)",
        "en": "IPP is only served on Windows (on Linux, share the printer via CUPS)",
    },
    "err.expect_ipp": {"ru": "Ожидается Content-Type: application/ipp", "en": "Expected Content-Type: application/ipp"},
    "err.job_not_found": {"ru": "Задание не найдено", "en": "Job not found"},
    "err.job_printing": {"ru": "Задание ещё печатается", "en": "The job is still printing"},
    "err.interrupted": {"ru": "Сервер был перезапущен во время печати", "en": "The server was restarted while printing"},
    "err.no_print_app": {
        "ru": "Нет программы, умеющей печатать файлы {ext} ({error})",
        "en": "No installed program can print {ext} files ({error})",
    },
    "err.print_failed": {"ru": "Ошибка печати: {error}", "en": "Print failed: {error}"},
    "err.printer_unavailable": {"ru": "Принтер «{printer}» недоступен: {error}", "en": "Printer “{printer}” is unavailable: {error}"},
    "err.printer_not_found": {"ru": "Принтер «{printer}» не найден", "en": "Printer “{printer}” not found"},
    "err.apply_settings": {"ru": "Не удалось применить настройки печати: {error}", "en": "Couldn't apply print settings: {error}"},
    "err.open_printer": {"ru": "Не удалось открыть принтер: {error}", "en": "Couldn't open the printer: {error}"},
    "err.open_pdf": {"ru": "Не удалось открыть PDF: {error}", "en": "Couldn't open the PDF: {error}"},
    "err.open_image": {"ru": "Не удалось открыть изображение: {error}", "en": "Couldn't open the image: {error}"},
    "err.test_page_code": {
        "ru": "Windows не смогла напечатать пробную страницу (код {code})",
        "en": "Windows couldn't print the test page (code {code})",
    },
    "err.test_page_admin": {
        "ru": "Windows печатает пробную страницу только для администратора — запустите сервер от имени администратора",
        "en": "Windows prints its test page only for an administrator — run the server as administrator",
    },
    "err.test_page": {"ru": "Ошибка пробной страницы: {error}", "en": "Test page failed: {error}"},
    "err.send_command": {"ru": "Не удалось отправить команду принтеру: {error}", "en": "Couldn't send the command to the printer: {error}"},
    "err.cups_missing": {
        "ru": "Команда '{cmd}' не найдена — установите CUPS: sudo apt install cups",
        "en": "Command '{cmd}' not found — install CUPS: sudo apt install cups",
    },
    "err.cmd_failed": {"ru": "{cmd} завершился с ошибкой", "en": "{cmd} failed"},
    "err.cups_test_page_missing": {"ru": "Не найдена пробная страница CUPS ({path})", "en": "CUPS test page not found ({path})"},
    "err.not_pwg": {"ru": "не PWG Raster (нет сигнатуры RaS2)", "en": "not PWG Raster (no RaS2 signature)"},
    "err.pwg_unsupported": {
        "ru": "неподдерживаемый PWG Raster: {bits} бит, цветовое пространство {space}",
        "en": "unsupported PWG Raster: {bits} bits, color space {space}",
    },
    "note.media_replaced": {
        "ru": "Тип бумаги «{media}» не поддерживает размер «{size}» — использован «{replacement}»",
        "en": "Paper type “{media}” doesn't support size “{size}” — used “{replacement}”",
    },
    "err.unknown_format": {"ru": "Неизвестный формат", "en": "Unknown format"},
    "err.scan_not_found": {"ru": "Скан не найден", "en": "Scan not found"},
    "err.no_scans_selected": {"ru": "Не выбраны сканы", "en": "No scans selected"},
    "job.scan_suffix": {"ru": "{name} (скан)", "en": "{name} (scan)"},
    "err.scanner_not_found": {"ru": "Сканер не найден — он выключен или отключён?", "en": "Scanner not found — is it off or disconnected?"},
    "err.scanner_busy": {
        "ru": "Сканер занят — дождитесь окончания текущего сканирования",
        "en": "The scanner is busy — wait for the current scan to finish",
    },
    "err.sane_missing": {"ru": "scanimage не найден — установите sane-utils", "en": "scanimage not found — install sane-utils"},
    "err.scanner_timeout": {"ru": "Сканер не ответил вовремя", "en": "The scanner didn't respond in time"},
    "err.scanimage_failed": {"ru": "Ошибка scanimage", "en": "scanimage failed"},
    "ipp.job_default_name": {"ru": "IPP-задание", "en": "IPP job"},
    "ipp.empty_document": {"ru": "пустой документ", "en": "empty document"},
    "ipp.canceled": {"ru": "отменено", "en": "canceled"},
}
