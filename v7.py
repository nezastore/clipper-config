import os
import sys
import json
import re
import traceback
import threading
import queue
import time
import math
import random
import logging
import subprocess
import glob
from logging.handlers import QueueHandler
from contextlib import redirect_stdout, redirect_stderr
from tkinter import Tk, filedialog, Button, Label, Text, Scrollbar, Frame, messagebox, StringVar, OptionMenu, Entry, Checkbutton, BooleanVar, Scale, IntVar, LabelFrame, Radiobutton, Canvas, Toplevel, DoubleVar, TclError, Misc, Listbox
from tkinter import simpledialog
import tkinter.ttk as ttk
from tkinter.ttk import Progressbar

# =======================[ SAFE TKINTER STATE SHIM ]=======================
# Mencegah error: TclError: unknown option "-state" saat .configure(state=...)
# Patch ini akan mengabaikan argumen 'state' untuk widget yang tidak mendukungnya
# tanpa mengubah fungsionalitas widget lain yang memang mendukung 'state'.
try:
    _orig_configure = Misc.configure
    _orig_config     = Misc.config
except Exception:
    _orig_configure = None
    _orig_config = None

def _safe_configure_dispatch(orig_func, self, *args, **kwargs):
    # Jika pemanggilan menyertakan 'state', coba dulu normal;
    # bila gagal karena "unknown option '-state'", buang opsi tersebut.
    if kwargs and 'state' in kwargs:
        try:
            return orig_func(self, *args, **kwargs)
        except TclError as e:
            if 'unknown option "-state"' in str(e):
                kwargs = dict(kwargs)
                kwargs.pop('state', None)
                return orig_func(self, *args, **kwargs)
            raise
    return orig_func(self, *args, **kwargs)

if _orig_configure and _orig_config:
    def _patched_configure(self, *args, **kwargs):
        return _safe_configure_dispatch(_orig_configure, self, *args, **kwargs)
    def _patched_config(self, *args, **kwargs):
        return _safe_configure_dispatch(_orig_config, self, *args, **kwargs)
    Misc.configure = _patched_configure
    Misc.config = _patched_config
# ========================================================================


# Third-party libraries
import yt_dlp
import ffmpeg
import requests
from PIL import Image, ImageTk
from openai import OpenAI  # Klien OpenAI kompatibel untuk OpenRouter
from urllib.parse import urlsplit, urlunsplit
import shutil
import tempfile


def _patch_subprocess_hide_windows_binaries():
    """
    Hindari jendela console "pop-up" dari proses CLI (ffmpeg/ffprobe) pada Windows.
    Aman untuk GUI app: hanya diterapkan pada binary tertentu.
    """
    if os.name != "nt":
        return
    if getattr(subprocess, "_ytclipper_popen_patched", False):
        return

    binaries = {"ffmpeg.exe", "ffprobe.exe", "ffplay.exe", "ffmpeg", "ffprobe", "ffplay"}
    orig_popen = subprocess.Popen

    def should_hide(args):
        try:
            if isinstance(args, (list, tuple)) and args:
                exe = str(args[0])
            else:
                exe = str(args).strip().split(" ")[0]
            exe = exe.strip('"').strip("'")
            exe = os.path.basename(exe).lower()
            return exe in binaries
        except Exception:
            return False

    def patched_popen(*popenargs, **kwargs):
        args = popenargs[0] if popenargs else kwargs.get("args")
        if should_hide(args):
            flags = kwargs.get("creationflags", 0)
            try:
                flags |= subprocess.CREATE_NO_WINDOW
            except Exception:
                pass
            kwargs["creationflags"] = flags
        return orig_popen(*popenargs, **kwargs)

    subprocess.Popen = patched_popen
    subprocess._ytclipper_popen_patched = True


_patch_subprocess_hide_windows_binaries()

# ==============================================================================
# KONFIGURASI
# ==============================================================================
LICENSE_URL = 'https://raw.githubusercontent.com/nezastore/clipper-config/refs/heads/main/licenses.txt'
CONFIG_URL = 'https://raw.githubusercontent.com/nezastore/clipper-config/refs/heads/main/config.json'
OUTPUT_SUBFOLDER = "Hasil"
COOKIE_FILE = 'cookies.txt'
COOKIES_STORE_DIR = "cookies_store"
APP_NAME = "YTCLIPERPRO"
SETTINGS_FILENAME = "settings.json"
TEMP_THUMBNAIL_FILE = "_temp_thumbnail.jpg"
LOG_FILE = 'autoclipper_log.txt' # File log permanen
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_AI_MODEL = "openai/gpt-4o-mini"
FALLBACK_AI_MODELS = [
    "openai/gpt-4o-mini",
    "openai/gpt-4.1-mini",
    "google/gemini-2.0-flash-001",
    "anthropic/claude-3.5-haiku",
    "meta-llama/llama-3.1-70b-instruct",
]
OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://openrouter.ai",
    "X-Title": "YTCLIPERPRO"
}


def get_user_config_dir(app_name: str = APP_NAME):
    """
    Direktori konfigurasi user (bisa ditulis) agar API key tersimpan tanpa mengganggu folder aplikasi.
    Windows: %APPDATA%\\<app_name>
    Fallback: ~\\AppData\\Roaming\\<app_name> atau folder aplikasi jika tidak bisa dibuat.
    """
    candidates = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(os.path.join(appdata, app_name))
    home = os.path.expanduser("~")
    if home:
        candidates.append(os.path.join(home, "AppData", "Roaming", app_name))
    candidates.append(os.path.join(get_app_base_path(), app_name))

    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            return path
        except Exception:
            continue
    return get_app_base_path()


def get_settings_path():
    return os.path.join(get_user_config_dir(), SETTINGS_FILENAME)


def load_settings():
    path = get_settings_path()
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(data: dict):
    path = get_settings_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        logging.warning(f"⚠️ Gagal menyimpan pengaturan: {e}")
        return False


def mask_secret(value: str, head: int = 6, tail: int = 4):
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= head + tail + 3:
        return "•" * len(value)
    return f"{value[:head]}…{value[-tail:]}"


def cleanup_ytdlp_artifacts(final_output_path: str, logger_func=logging.info):
    """
    Bersihkan file download sementara yt-dlp (mis. *.fXXX.mp4, *.fXXX.m4a, *.part) setelah merge berhasil.
    Aman dipanggil berulang; hanya menghapus file yang jelas turunan dari final_output_path.
    """
    try:
        final_output_path = os.path.abspath(final_output_path)
        base, _ = os.path.splitext(final_output_path)

        patterns = [
            f"{base}.f*.*",
            f"{base}.*.part",
            f"{base}.part",
            f"{final_output_path}.part",
            f"{base}.ytdl",
            f"{base}.temp.*",
        ]

        removed = 0
        for pat in patterns:
            for path in glob.glob(pat):
                try:
                    path = os.path.abspath(path)
                    if path == final_output_path:
                        continue
                    if os.path.isfile(path):
                        os.remove(path)
                        removed += 1
                except Exception:
                    continue

        if removed:
            logger_func(f"   🧹 Membersihkan {removed} file temp yt-dlp.")
    except Exception:
        pass

# ==============================================================================
# PENGATURAN LOGGING
# ==============================================================================

# Formatter dengan emoji agar log mudah dibaca
class EmojiFormatter(logging.Formatter):
    LEVEL_EMOJI = {
        "DEBUG": "🧪",
        "INFO": "ℹ️",
        "WARNING": "⚠️",
        "ERROR": "❌",
        "CRITICAL": "🧨",
    }

    def format(self, record):
        emoji = self.LEVEL_EMOJI.get(record.levelname, "•")
        base = super().format(record)
        return f"{emoji} {base}"


# 1. Stream class untuk mengarahkan stdout/stderr ke logging
class LogStream:
    def __init__(self, logger, level):
        self.logger = logger
        self.level = level
        self.linebuf = ''

    def write(self, buf):
        for line in buf.rstrip().splitlines():
            msg = line.rstrip()
            if not msg:
                continue

            # Downgrade "progress bar" dan warning umum yang sering keluar ke stderr
            # agar tidak terlihat seperti error fatal.
            lowered = msg.lower()
            is_tqdm_bar = ("|" in msg and "%" in msg and "/" in msg) or ("frames/s" in lowered)
            is_warning = lowered.startswith("warning") or "userwarning" in lowered
            is_trace = lowered.startswith("traceback") or "exception" in lowered

            if is_tqdm_bar:
                self.logger.log(logging.INFO, msg)
            elif is_warning:
                self.logger.log(logging.WARNING, msg)
            elif is_trace:
                self.logger.log(logging.ERROR, msg)
            else:
                self.logger.log(self.level, msg)

    def flush(self):
        pass

# 2. Handler untuk mengirim log ke GUI (Tkinter Text widget)
class GuiLogHandler(logging.Handler):
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(self.format(record))

# 3. Fungsi untuk mengatur logging
def setup_logging(log_queue):
    log_format = EmojiFormatter('%(asctime)s [%(levelname)-8s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Handler untuk file log permanen
    file_handler = logging.FileHandler(LOG_FILE, 'w', 'utf-8')
    file_handler.setFormatter(log_format)
    root_logger.addHandler(file_handler)

    # Handler untuk GUI
    gui_handler = GuiLogHandler(log_queue)
    gui_handler.setFormatter(log_format)
    root_logger.addHandler(gui_handler)

    # Mengarahkan stdout dan stderr ke logging
    sys.stdout = LogStream(root_logger, logging.INFO)
    sys.stderr = LogStream(root_logger, logging.ERROR)

    logging.info("🚀 Sistem logging dimulai. Log disimpan di " + LOG_FILE)

# ==============================================================================
# KELAS JENDELA CROP VISUAL
# ==============================================================================
class CropWindow(Toplevel):
    def __init__(self, parent_widget, app_instance, image_path=None):
        super().__init__(parent_widget)
        self.parent_app = app_instance

        self.title("Preview dan Atur Crop")
        self.geometry("450x520")
        self.resizable(False, False)
        self.transient(parent_widget)
        self.grab_set()
        try:
            icon_path = find_app_icon_ico()
            if icon_path:
                self.iconbitmap(icon_path)
        except Exception:
            pass

        self.start_x = None
        self.start_y = None
        self.crop_rect = None
        self.final_coords = None
        self.photo_image = None

        self.CANVAS_WIDTH = 400
        self.CANVAS_HEIGHT = int(self.CANVAS_WIDTH * 9 / 16)

        self.canvas = Canvas(self, width=self.CANVAS_WIDTH, height=self.CANVAS_HEIGHT, bg="grey")
        self.canvas.pack(pady=20, padx=20)

        if image_path and os.path.exists(image_path):
            try:
                img = Image.open(image_path)
                img = img.resize((self.CANVAS_WIDTH, self.CANVAS_HEIGHT), Image.Resampling.LANCZOS)
                self.photo_image = ImageTk.PhotoImage(img)
                self.canvas.create_image(0, 0, anchor='nw', image=self.photo_image)
            except Exception as e:
                logging.error(f"Error memuat gambar di canvas: {e}")
                self.canvas.config(bg="white")
        else:
            self.canvas.config(bg="white")

        Label(self, text="Klik dan seret pada gambar di atas untuk memilih area crop.\nRasio 9:16 akan dijaga secara otomatis.", justify="center").pack(pady=(0, 10))

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

        btn_frame = Frame(self)
        btn_frame.pack(pady=10)
        Button(btn_frame, text="Simpan Crop & Tutup", command=self.save_and_close).pack(side="left", padx=10)
        Button(btn_frame, text="Batal", command=self.destroy).pack(side="left", padx=10)

    def on_press(self, event):
        self.start_x = self.canvas.canvasx(event.x)
        self.start_y = self.canvas.canvasy(event.y)
        if self.crop_rect:
            self.canvas.delete(self.crop_rect)

    def on_drag(self, event):
        if self.start_x is None or self.start_y is None:
            return

        if self.crop_rect:
            self.canvas.delete(self.crop_rect)

        end_x = min(max(self.canvas.canvasx(event.x), 0), self.CANVAS_WIDTH)

        width = abs(end_x - self.start_x)
        height = width * 16 / 9

        x1 = min(self.start_x, end_x)
        y1 = self.start_y
        x2 = x1 + width
        y2 = y1 + height

        self.crop_rect = self.canvas.create_rectangle(x1, y1, x2, y2, outline="red", width=2, dash=(4, 2))

    def on_release(self, event):
        if not self.crop_rect:
            return

        coords = self.canvas.coords(self.crop_rect)
        if not coords: return

        x1, y1, x2, y2 = coords
        crop_width = x2 - x1

        self.final_coords = {
            'x_ratio': x1 / self.CANVAS_WIDTH,
            'y_ratio': y1 / self.CANVAS_HEIGHT,
            'w_ratio': crop_width / self.CANVAS_WIDTH,
        }

    def save_and_close(self):
        if self.final_coords:
            self.parent_app.manual_crop_coords = self.final_coords
            self.parent_app.manual_crop_status.set("Status: Sudah diatur.")
            logging.info("   ✅ Koordinat crop manual disimpan.")
        self.destroy()

# ==============================================================================
# FUNGSI-FUNGSI UTILITY & BACKEND
# ==============================================================================
def sanitize_filename(filename):
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"
        "\U0001F300-\U0001F5FF"
        "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF"
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "]+", flags=re.UNICODE)
    filename = emoji_pattern.sub(r"", filename)
    filename = filename.replace("#", "")
    return re.sub(r'[\\/*?:"<>|]', "", filename).strip()


def sanitize_cookie_profile_label(label: str) -> str:
    label = (label or "").strip()
    if not label:
        return ""
    label = label.replace("@", "_at_")
    label = re.sub(r"\s+", "_", label)
    label = re.sub(r"[^0-9A-Za-z._-]+", "_", label)
    return label.strip("._-")[:60]


def normalize_youtube_channel_shorts_url(channel_url: str) -> str:
    """
    Normalisasi URL channel agar pasti mengarah ke tab /shorts.
    Contoh input share: https://youtube.com/@user?si=...
    Output: https://youtube.com/@user/shorts
    """
    raw = (channel_url or "").strip()
    if not raw:
        return raw

    parts = urlsplit(raw)
    # Buang query/fragment (misal ?si=...)
    clean_parts = (parts.scheme, parts.netloc, parts.path, "", "")
    clean = urlunsplit(clean_parts).rstrip("/")

    if clean.endswith("/shorts"):
        return clean
    return f"{clean}/shorts"


def get_app_base_path():
    return os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))


def find_app_icon_ico():
    """
    Cari file icon 'pp.ico' baik saat run biasa maupun saat PyInstaller one-file.
    """
    candidates = [os.path.join(get_app_base_path(), "pp.ico")]
    if getattr(sys, "frozen", False):
        mei = getattr(sys, "_MEIPASS", "") or ""
        if mei:
            candidates.append(os.path.join(mei, "pp.ico"))
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def get_cookie_file_path():
    return os.path.join(get_app_base_path(), COOKIE_FILE)


def get_cookies_store_dir():
    path = os.path.join(get_app_base_path(), COOKIES_STORE_DIR)
    os.makedirs(path, exist_ok=True)
    return path


def list_cookie_profiles():
    store = get_cookies_store_dir()
    profiles = []
    try:
        for name in os.listdir(store):
            if name.lower().endswith(".txt"):
                profiles.append(name)
    except Exception:
        return []
    profiles.sort()
    return profiles


def get_active_cookie_profile_name():
    cfg_path = os.path.join(get_cookies_store_dir(), "active.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        name = (data.get("active") or "").strip()
        return name if name else None
    except Exception:
        return None


def set_active_cookie_profile(profile_filename: str, logger_func=logging.info):
    """
    Set cookie profile aktif: copy ke cookies.txt agar seluruh alur tetap konsisten.
    """
    if not profile_filename:
        return False
    store = get_cookies_store_dir()
    src = os.path.join(store, profile_filename)
    if not os.path.exists(src):
        return False

    dst = get_cookie_file_path()
    try:
        shutil.copyfile(src, dst)
        with open(os.path.join(store, "active.json"), "w", encoding="utf-8") as f:
            json.dump({"active": profile_filename, "updated_at": int(time.time())}, f)
        logger_func(f"✅ Cookies aktif: {profile_filename}")
        return True
    except Exception as e:
        logger_func(f"⚠️ Gagal set cookies aktif: {e}")
        return False


def save_cookie_profile_from_file(source_cookie_txt: str, profile_filename: str, set_active: bool = True, logger_func=logging.info):
    store = get_cookies_store_dir()
    if not profile_filename.lower().endswith(".txt"):
        profile_filename += ".txt"
    dst = os.path.join(store, profile_filename)
    try:
        shutil.copyfile(source_cookie_txt, dst)
        logger_func(f"✅ Cookies disimpan sebagai profil: {profile_filename}")
        if set_active:
            set_active_cookie_profile(profile_filename, logger_func=logger_func)
        return True
    except Exception as e:
        logger_func(f"⚠️ Gagal menyimpan profil cookies: {e}")
        return False


def delete_cookie_profile(profile_filename: str, logger_func=logging.info):
    store = get_cookies_store_dir()
    path = os.path.join(store, profile_filename)
    try:
        if os.path.exists(path):
            os.remove(path)
            logger_func(f"🗑️ Profil cookies dihapus: {profile_filename}")
        active = get_active_cookie_profile_name()
        if active and active == profile_filename:
            maybe_delete_cookie_file(get_cookie_file_path(), logger_func=logger_func, reason="active profile deleted")
            try:
                os.remove(os.path.join(store, "active.json"))
            except Exception:
                pass
        return True
    except Exception as e:
        logger_func(f"⚠️ Gagal hapus profil cookies: {e}")
        return False


def rotate_cookie_profile(logger_func=logging.info):
    """
    Auto switch cookies: pilih profil berikutnya lalu copy ke cookies.txt.
    Return True jika berhasil switch.
    """
    profiles = list_cookie_profiles()
    if not profiles:
        return False
    active = get_active_cookie_profile_name()
    if active in profiles:
        idx = profiles.index(active)
        next_profile = profiles[(idx + 1) % len(profiles)]
    else:
        next_profile = profiles[0]
    if active == next_profile and len(profiles) == 1:
        return False
    return set_active_cookie_profile(next_profile, logger_func=logger_func)


def cookie_file_has_any_live_cookie(cookie_path: str) -> bool:
    """
    Heuristik sederhana untuk mendeteksi cookie "masih hidup" dari file Netscape cookies.txt.
    - Jika ada session cookie (expiry 0/blank), anggap masih hidup.
    - Jika ada expiry di masa depan, anggap masih hidup.
    """
    if not cookie_path or not os.path.exists(cookie_path):
        return False

    now = int(time.time())
    try:
        with open(cookie_path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                expiry_raw = parts[4].strip()
                if not expiry_raw:
                    return True
                try:
                    expiry = int(expiry_raw)
                except ValueError:
                    continue
                if expiry == 0 or expiry > now:
                    return True
    except Exception:
        return True
    return False


def maybe_delete_cookie_file(cookie_path: str, logger_func=logging.info, reason: str = ""):
    try:
        if cookie_path and os.path.exists(cookie_path):
            os.remove(cookie_path)
            logger_func(f"   ⚠️ cookies.txt dihapus{f' ({reason})' if reason else ''}. Silakan import ulang cookies.")
    except Exception as e:
        logger_func(f"   ⚠️ Gagal menghapus cookies.txt: {e}")


def export_cookies_from_chrome(cookie_path: str, logger_func=logging.info):
    """
    Export cookies dari Chrome profil normal ke format Netscape `cookies.txt`.
    Catatan: Incognito tidak didukung.
    """
    from yt_dlp.cookies import extract_cookies_from_browser, YoutubeDLCookieJar

    jar = extract_cookies_from_browser("chrome")
    out = YoutubeDLCookieJar(cookie_path)
    try:
        for c in jar:
            out.set_cookie(c)
    except TypeError:
        pass
    out.save(cookie_path, ignore_discard=True, ignore_expires=False)
    return True


def _selenium_cookies_have_youtube_auth(cookies) -> bool:
    auth_names = {
        "SID", "HSID", "SSID", "SAPISID", "APISID",
        "__Secure-1PSID", "__Secure-3PSID", "__Secure-1PAPISID", "__Secure-3PAPISID",
    }
    for c in cookies or []:
        name = (c.get("name") or "").strip()
        domain = (c.get("domain") or "").lower()
        if name in auth_names and ("youtube." in domain or "google." in domain):
            return True
    return False


def _write_netscape_cookies_txt(cookie_path: str, cookies) -> None:
    lines = [
        "# Netscape HTTP Cookie File",
        "# This file is generated by YoutubeCliperBOT",
        "",
    ]
    for c in cookies or []:
        name = c.get("name")
        value = c.get("value")
        domain = c.get("domain")
        if not name or value is None or not domain:
            continue
        domain = domain.strip()
        if not domain:
            continue
        if not domain.startswith("."):
            domain = "." + domain
        include_subdomains = "TRUE"
        path = (c.get("path") or "/").strip() or "/"
        secure = "TRUE" if c.get("secure") else "FALSE"
        expiry = c.get("expiry")
        try:
            expiry = str(int(expiry)) if expiry is not None else "0"
        except Exception:
            expiry = "0"
        lines.append("\t".join([domain, include_subdomains, path, secure, expiry, name, str(value)]))

    with open(cookie_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")


def find_chrome_executable():
    candidates = []

    which_chrome = shutil.which("chrome")
    if which_chrome:
        candidates.append(which_chrome)

    program_files = os.environ.get("ProgramFiles")
    program_files_x86 = os.environ.get("ProgramFiles(x86)")
    local_app_data = os.environ.get("LOCALAPPDATA")

    for base in (program_files, program_files_x86, local_app_data):
        if not base:
            continue
        candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))

    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


_BUNDLED_BINARIES_READY = False


def ensure_bundled_binaries_on_path(logger_func=logging.info):
    """
    Pastikan ffmpeg/ffprobe yang dibundel (jika ada) bisa ditemukan oleh:
    - ffmpeg-python (memanggil 'ffmpeg' dari PATH)
    - yt-dlp (merge/convert)
    """
    global _BUNDLED_BINARIES_READY
    if _BUNDLED_BINARIES_READY:
        return

    base_path = get_app_base_path()
    ffmpeg_exe = os.path.join(base_path, "ffmpeg.exe")
    ffprobe_exe = os.path.join(base_path, "ffprobe.exe")

    if os.path.exists(ffmpeg_exe) and os.path.exists(ffprobe_exe):
        os.environ["PATH"] = base_path + os.pathsep + os.environ.get("PATH", "")
        logger_func(f"✅ FFmpeg lokal terdeteksi: {ffmpeg_exe}")
        _BUNDLED_BINARIES_READY = True
        return

    _BUNDLED_BINARIES_READY = True
    logger_func("⚠️ FFmpeg lokal tidak ditemukan. Jalankan `InstallDulu.bat` atau install FFmpeg ke PATH.")


def get_ffmpeg_cmd():
    local = os.path.join(get_app_base_path(), "ffmpeg.exe")
    return local if os.path.exists(local) else "ffmpeg"


def run_ffmpeg_stream(stream, logger_func=logging.info):
    """
    Jalankan ffmpeg-python stream dengan capture stderr agar error jelas di log GUI.
    Return True jika sukses.
    """
    ensure_bundled_binaries_on_path(logger_func)
    try:
        stream.run(cmd=get_ffmpeg_cmd(), overwrite_output=True, capture_stdout=True, capture_stderr=True, quiet=True)
        return True
    except ffmpeg.Error as e:
        stderr = getattr(e, "stderr", b"") or b""
        if isinstance(stderr, (bytes, bytearray)):
            stderr = stderr.decode("utf-8", "ignore").strip()
        else:
            stderr = str(stderr).strip()
        logger_func(f"❌ FFmpeg gagal: {stderr if stderr else 'Perintah gagal tanpa stderr.'}")
        logging.error(f"❌ FFmpeg gagal: {e}\n{traceback.format_exc()}")
        return False


def mp4_output_kwargs():
    """
    Opsi output MP4 agar kompatibel diputar di banyak device/player.
    """
    return {
        "movflags": "faststart",
        "pix_fmt": "yuv420p",
        "acodec": "aac",
        "ar": 44100,
        "ac": 2,
    }


def get_device_id():
    """
    Ambil ID perangkat tanpa dependensi eksternal.
    Format utama: <hostname>-<mac12> (MAC dipadatkan 12 hex).
    """
    return get_device_id_candidates()[0]


def get_device_id_candidates():
    """
    Kembalikan kandidat ID perangkat untuk kompatibilitas:
    - primary: host + MAC 12-hex (padded)
    - legacy : host + MAC hex (unpadded)
    """
    import uuid
    import platform

    host = (
        os.environ.get("COMPUTERNAME")
        or os.environ.get("HOSTNAME")
        or platform.node()
        or "unknown-host"
    ).strip()
    host = re.sub(r"\s+", "-", host) or "unknown-host"

    mac = uuid.getnode()
    return [f"{host}-{mac:012x}", f"{host}-{mac:x}"]


def verify_license(logger_func=logging.info):
    logger_func("🔑 Mengecek koneksi ke server lisensi...")
    try:
        candidates = get_device_id_candidates()
        device_id = candidates[0]
    except Exception as e:
        logger_func(f"⚠️ Tidak bisa mengambil ID perangkat: {e}")
        device_id = None
        candidates = []

    # 1) Remote GitHub raw (primary source)
    try:
        response = requests.get(LICENSE_URL, timeout=10)
        response.raise_for_status()
        authorized_ids = {line.strip().lower() for line in response.text.strip().splitlines() if line.strip()}
        if candidates and any(c.lower() in authorized_ids for c in candidates):
            logger_func("✅ Lisensi valid.")
            return True, device_id
        else:
            logger_func("⛔ ID perangkat tidak ada di daftar lisensi online.")
    except Exception as e:
        logger_func(f"⚠️ Gagal memuat daftar lisensi online: {e}")

    # 2) Fallback: file lokal 'licenses.txt' (opsional)
    try:
        base_path = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        local_lic_path = os.path.join(base_path, "licenses.txt")
        if os.path.exists(local_lic_path):
            with open(local_lic_path, "r", encoding="utf-8") as f:
                authorized_ids = {line.strip().lower() for line in f if line.strip()}
            if candidates and any(c.lower() in authorized_ids for c in candidates):
                logger_func("✅ Lisensi valid (fallback lokal).")
                return True, device_id
    except Exception as e:
        logger_func(f"⚠️ Gagal membaca licenses.txt lokal: {e}")

    logger_func("⛔ Lisensi tidak valid untuk perangkat ini.")
    return False, device_id

# --- [FIX] Menambahkan fungsi load_remote_config yang hilang ---
def load_remote_config(logger_func=logging.info):
    """Muat konfigurasi dari URL remote."""
    try:
        logger_func("🌍 Mencoba memuat konfigurasi remote...")
        response = requests.get(CONFIG_URL, timeout=10)
        response.raise_for_status()
        remote_cfg = response.json()
        logger_func("✅ Konfigurasi remote berhasil dimuat.")
        return remote_cfg
    except requests.exceptions.RequestException as e:
        logger_func(f"⚠️ Gagal memuat konfigurasi remote: {e}")
        return None
    except json.JSONDecodeError as e:
        logger_func(f"⚠️ Gagal mem-parsing konfigurasi remote (bukan JSON valid): {e}")
        return None
    except Exception as e:
        logger_func(f"⚠️ Error tidak terduga saat memuat config remote: {e}")
        return None
# --- [END FIX] ---

# --- [FIX] Menghapus fungsi duplikat dan memperbaiki bug rekursi ---
def load_effective_config(logger_func=logging.info):
    """
    Muat konfigurasi efektif:
    - Remote CONFIG_URL (jika ada)
    - Override oleh config.json lokal (jika ada)
    - Mengabaikan kunci terkait lisensi di config.json agar lisensi tetap terpisah.
    """
    remote = None
    local = None
    try:
        # Memanggil fungsi remote yang sudah diperbaiki
        remote = load_remote_config(logger_func)
    except Exception:
        remote = None

    try:
        base_path = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        local_path = os.path.join(base_path, "config.json")
        if os.path.exists(local_path):
            with open(local_path, "r", encoding="utf-8") as f:
                local = json.load(f)
            # buang kunci lisensi bila ada (strict separation)
            for k in ("license_allow_all", "license_whitelist", "allowed_ids"):
                local.pop(k, None)
            logger_func("🗂️ Memuat konfigurasi lokal config.json (override).")
    except Exception as e:
        logger_func(f"⚠️ Gagal membaca config.json lokal: {e}")
        local = None

    cfg = {}
    if isinstance(remote, dict):
        cfg.update(remote)
    if isinstance(local, dict):
        cfg.update(local)
    if not cfg:
        logger_func("⚠️ Tidak ada konfigurasi yang bisa dimuat (remote & lokal gagal).")
        return {}
    return cfg
# --- [END FIX] ---

def _hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def blend_colors(color_a, color_b, t):
    """Blend two hex colors with factor t (0-1)."""
    t = max(0.0, min(1.0, float(t)))
    ra, ga, ba = _hex_to_rgb(color_a)
    rb, gb, bb = _hex_to_rgb(color_b)
    r = int(ra + (rb - ra) * t)
    g = int(ga + (gb - ga) * t)
    b = int(ba + (bb - ba) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def load_whisper_module(logger_func=logging.info):
    """
    Muat modul whisper secara malas dan beri pesan jelas jika paket salah/kurang.
    """
    try:
        import whisper  # type: ignore
        if not hasattr(whisper, "load_model"):
            raise ImportError("Modul 'whisper' yang terpasang bukan openai-whisper (load_model tidak ada).")
        if getattr(sys, "frozen", False):
            base = getattr(sys, "_MEIPASS", "")
            if base:
                assets = os.path.join(base, "whisper", "assets", "mel_filters.npz")
                if not os.path.exists(assets):
                    logger_func("❌ Asset whisper tidak ditemukan di build (.exe).")
                    logger_func("   Solusi: rebuild dengan PyInstaller `--collect-data whisper` (mel_filters.npz wajib).")
                    raise FileNotFoundError(assets)
        return whisper
    except Exception as e:
        logger_func("❌ Gagal memuat modul whisper (transkripsi).")
        logger_func("   Pastikan memasang paket resmi: pip install -U openai-whisper")
        logger_func(f"   Detail: {e}")
        return None


def configure_ai_client(api_key, logger_func=logging.info, base_url=OPENROUTER_BASE_URL):
    if not api_key:
        logger_func("❌ ERROR: API Key OpenRouter tidak ditemukan.")
        return None
    try:
        client = OpenAI(api_key=api_key, base_url=base_url, default_headers=OPENROUTER_HEADERS)
        logger_func("✅ Konfigurasi OpenRouter AI berhasil.")
        return client
    except Exception as e:
        logger_func(f"❌ ERROR: Gagal mengkonfigurasi OpenRouter AI API. {e}")
        return None


def get_ai_model_candidates(primary_model: str):
    seen = set()
    ordered = [primary_model, DEFAULT_AI_MODEL] + list(FALLBACK_AI_MODELS)
    result = []
    for m in ordered:
        if not m or not isinstance(m, str):
            continue
        m = m.strip()
        if not m or m in seen:
            continue
        seen.add(m)
        result.append(m)
    return result


def openrouter_chat_completion(ai_client, model_candidates, messages, logger_func=logging.info, **kwargs):
    last_error = None
    for model in model_candidates:
        try:
            return ai_client.chat.completions.create(model=model, messages=messages, **kwargs), model
        except Exception as e:
            last_error = e
            msg = str(e).lower()
            retryable = any(k in msg for k in ("model", "not found", "unsupported", "invalid", "does not exist", "no such"))
            logger_func(f"⚠️ Model '{model}' gagal: {e}")
            if not retryable:
                break
            continue
    raise last_error

def download_video(url, output_path, logger_func=logging.info):
    ensure_bundled_binaries_on_path(logger_func)
    if os.path.exists(output_path):
        try: os.remove(output_path)
        except OSError as e:
            logger_func(f"❌ Gagal menghapus file sementara yang ada: {e}"); return None, None

    info_dict = None
    def my_progress_hook(d):
        nonlocal info_dict
        if d['status'] == 'finished':
            info_dict = d.get('info_dict', {})
            logger_func(f"   ✅ Download selesai: {d.get('filename')}")
        if d['status'] == 'downloading':
            percent_str = d.get('_percent_str', 'N/A')
            speed_str = d.get('_speed_str', 'N/A')
            eta_str = d.get('_eta_str', 'N/A')

            percent_str = re.sub(r'\x1b\[[0-9;]*m', '', percent_str).strip()
            speed_str = re.sub(r'\x1b\[[0-9;]*m', '', speed_str).strip()
            eta_str = re.sub(r'\x1b\[[0-9;]*m', '', eta_str).strip()

            now = time.time()
            last = getattr(my_progress_hook, "_last_log_t", 0.0)
            if (now - last) >= 1.2:
                my_progress_hook._last_log_t = now
                logger_func(f"   ⬇️ Downloading... {percent_str} | Speed: {speed_str} | ETA: {eta_str}")

    ydl_opts = {
        'format': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best',
        'outtmpl': output_path,
        'merge_output_format': 'mp4',
        'quiet': True,
        'progress_hooks': [my_progress_hook],
        'nocheckcertificate': True,
        'noplaylist': True,
        # Enable remote EJS components for YouTube JS challenges (fix 403 / n-challenge issues)
        'remote_components': ['ejs:github'],
        'ffmpeg_location': get_app_base_path(),
        'logger': logging.getLogger('yt_dlp') # Arahkan log yt-dlp ke sistem logging
    }

    js_runtimes = get_yt_dlp_js_runtimes(logger_func)
    if js_runtimes:
        ydl_opts['js_runtimes'] = js_runtimes

    cookie_path = get_cookie_file_path()
    used_cookies = False

    if os.path.exists(cookie_path) and not cookie_file_has_any_live_cookie(cookie_path):
        maybe_delete_cookie_file(cookie_path, logger_func=logger_func, reason="expired")

    if os.path.exists(cookie_path):
        logger_func(f"   🍪 File '{COOKIE_FILE}' ditemukan, mencoba download dengan autentikasi.")
        ydl_opts['cookiefile'] = cookie_path
        used_cookies = True
    else:
        logger_func(f"   ⚠️ File '{COOKIE_FILE}' tidak ditemukan. Melanjutkan download tanpa autentikasi.")

    for attempt in range(2):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info_dict:
                    info_dict = info
                # Jika merge berhasil, bersihkan file per-format (.fXXX) dan *.part
                if os.path.exists(output_path):
                    cleanup_ytdlp_artifacts(output_path, logger_func=logger_func)
                return output_path, info_dict
        except Exception as e:
            logging.error(f"❌ ERROR saat mengunduh video: {str(e)}")
            # Bersihkan file .part/artefak temp jika ada
            cleanup_ytdlp_artifacts(output_path, logger_func=logger_func)
            msg = str(e).lower()
            cookie_bad = used_cookies and ("403" in msg or "forbidden" in msg or "sign in" in msg or "challenge" in msg)
            if cookie_bad:
                maybe_delete_cookie_file(cookie_path, logger_func=logging.warning, reason="cookies invalid/expired")
                active_profile = get_active_cookie_profile_name()
                if active_profile:
                    delete_cookie_profile(active_profile, logger_func=logging.warning)
                if attempt == 0 and rotate_cookie_profile(logger_func=logging.warning) and os.path.exists(get_cookie_file_path()):
                    ydl_opts['cookiefile'] = get_cookie_file_path()
                    used_cookies = True
                    logging.warning("🔁 Mencoba ulang download dengan profil cookies lain...")
                    continue
            return None, None


_YTDLP_JS_RUNTIMES_CACHE = None
_YTDLP_JS_RUNTIMES_LOGGED = False


def get_yt_dlp_js_runtimes(logger_func=logging.info):
    """
    yt-dlp YouTube extraction (EJS) butuh JS runtime (node/deno/bun).
    Return format dict sesuai yt-dlp: {runtime: {config}}.
    """
    global _YTDLP_JS_RUNTIMES_CACHE, _YTDLP_JS_RUNTIMES_LOGGED

    if _YTDLP_JS_RUNTIMES_CACHE is None:
        candidates = [("node", "node"), ("deno", "deno"), ("bun", "bun")]
        detected = None
        for runtime, exe in candidates:
            path = shutil.which(exe)
            if path:
                detected = {runtime: {"path": path}}
                break
        _YTDLP_JS_RUNTIMES_CACHE = detected or {}

    if not _YTDLP_JS_RUNTIMES_LOGGED:
        _YTDLP_JS_RUNTIMES_LOGGED = True
        if _YTDLP_JS_RUNTIMES_CACHE:
            runtime = next(iter(_YTDLP_JS_RUNTIMES_CACHE.keys()))
            path = _YTDLP_JS_RUNTIMES_CACHE[runtime].get("path")
            logger_func(f"   ✅ JS runtime yt-dlp terdeteksi: {runtime} ({path})")
        else:
            logger_func("   ⚠️ JS runtime untuk yt-dlp tidak ditemukan (node/deno/bun). Jika download YouTube gagal, install Node.js atau Deno.")

    return _YTDLP_JS_RUNTIMES_CACHE or None

def transcribe_audio(audio_path, whisper_model, model_name, logger_func=logging.info):
    try:
        logger_func("   Memulai transkripsi audio... (Ini mungkin lama)")
        result = whisper_model.transcribe(audio_path, verbose=False, word_timestamps=True)
        logger_func("   Transkripsi audio selesai.")
        return result
    except Exception as e:
        logging.error(f"❌ ERROR saat transkripsi: {e}"); return None

def generate_srt_file(transcription_result, output_srt_path, logger_func=logging.info):
    logger_func("   📄 Membuat file subtitle (.srt)...")
    try:
        with open(output_srt_path, 'w', encoding='utf-8') as srt_file:
            for i, segment in enumerate(transcription_result['segments']):
                start_time = segment['start']; end_time = segment['end']
                text = segment['text'].strip()
                if not text: continue
                start_hms = time.strftime('%H:%M:%S', time.gmtime(start_time))
                start_ms = f"{int((start_time % 1) * 1000):03d}"
                end_hms = time.strftime('%H:%M:%S', time.gmtime(end_time))
                end_ms = f"{int((end_time % 1) * 1000):03d}"
                srt_file.write(f"{i + 1}\n")
                srt_file.write(f"{start_hms},{start_ms} --> {end_hms},{end_ms}\n")
                srt_file.write(f"{text}\n\n")
        logger_func(f"   ✅ File subtitle berhasil dibuat: {os.path.basename(output_srt_path)}")
        return True
    except Exception as e:
        logger_func(f"   ❌ Gagal membuat file subtitle: {e}"); return False

def get_clips_from_ai(transcript_text, ai_model_name, ai_client, logger_func=logging.info):
    prompt = f"""
    Anda adalah seorang editor video profesional dan ahli strategi konten viral yang terobsesi dengan "hook" (kail pancing) di 3 detik pertama. Tugas Anda adalah menganalisis transkrip video di dalam tag `<transcript>` dan mengidentifikasi momen-momen emas yang paling berpotensi FYP. ATURAN UTAMA: 1. HOOK ADALAH SEGALANYA: Setiap klip yang Anda sarankan HARUS dimulai dengan hook yang sangat kuat. Jika segmen tidak memiliki hook, JANGAN JADIKAN KLIP. 2. KUALITAS, BUKAN KUANTITAS: Fokus hanya pada momen viral. Lebih baik 2 klip sempurna daripada 7 klip biasa. 3. DURASI IDEAL: 30-60 detik. 4. OUTPUT JSON: Harus berupa format JSON valid `[ ... ]`. Setiap objek dalam array harus memiliki keys: "start_time", "end_time", "title", "hashtags", dan "editing_style". <transcript>{transcript_text}</transcript> INSTRUKSI SPESIFIK UNTUK SETIAP KLIP: 1. Cari Hook: Identifikasi pertanyaan, pernyataan kontroversial, momen emosional, atau klimaks yang kuat sebagai titik awal. 2. Tentukan Waktu (WAJIB): "start_time" harus TEPAT DI AWAL HOOK. "end_time" harus sekitar 30-60 detik setelah "start_time". Keduanya HARUS dalam format "HH:MM:SS". 3. Buat Metadata: Buat "title" yang clickbait, 3 "hashtags" yang relevan, dan tentukan "editing_style" (pilih antara 'dynamic' atau 'informative').
    """
    try:
        logger_func("   🤖 Menghubungi AI OpenRouter untuk rekomendasi klip...")
        response, used_model = openrouter_chat_completion(
            ai_client,
            get_ai_model_candidates(ai_model_name),
            [{"role": "user", "content": prompt}],
            logger_func=logger_func,
            max_tokens=4096,
            temperature=0.7,
            stream=False,
        )
        content = response.choices[0].message.content
        logger_func("   🤖 AI OpenRouter telah merespons.")
        if used_model != ai_model_name:
            logger_func(f"   ℹ️ Fallback model dipakai: {used_model}")

        json_match = re.search(r'```json\s*(\[.*\])\s*```', content, re.DOTALL) or re.search(r'\[.*\]', content, re.DOTALL)
        if not json_match:
            logger_func(f"❌ ERROR: AI (klip) tidak memberikan output JSON yang valid.\n   Jawaban AI: {content}"); return []

        json_str = json_match.group(1) if len(json_match.groups()) > 0 else json_match.group(0)
        clips = json.loads(json_str)
        logger_func(f"✅ AI (klip) merekomendasikan {len(clips)} klip."); return clips
    except Exception as e:
        logging.error(f"❌ ERROR saat analisis AI (klip): {e}\n   Jawaban AI: {content if 'content' in locals() else 'Tidak ada respons'}")
        return []

def get_summary_clips_from_ai(transcript_text, video_duration, ai_model_name, ai_client, detail_level="SEDANG", logger_func=logging.info):
    detail_instructions = {
        "CEPAT": { "clip_count_instruction": "sekitar 3-4 klip paling viral dan menarik" },
        "SEDANG": { "clip_count_instruction": "sekitar 5-7 klip yang merangkum poin utama" },
        "DETAIL": { "clip_count_instruction": "sekitar 8-10 klip untuk cakupan mendalam" }
    }
    selected_instruction = detail_instructions.get(detail_level, detail_instructions["SEDANG"])

    prompt = f"""
    Anda adalah asisten AI yang bertugas mengekstrak klip-klip kunci dari transkrip video untuk membuat ringkasan yang padat dan menarik. ATURAN UTAMA: 1. FOKUS PADA INTI: Identifikasi dan pilih hanya bagian-bagian terpenting dari transkrip yang mewakili ide utama, argumen kunci, atau momen puncak. 2. JUMLAH KLIP: Berdasarkan tingkat detail '{detail_level}', hasilkan {selected_instruction['clip_count_instruction']}. 3. ALUR LOGIS: Urutan klip harus masuk akal dan mudah diikuti. Klip pertama harus menjadi "hook" yang kuat. 4. OUTPUT JSON WAJIB: Respons Anda HARUS HANYA berupa blok JSON yang valid, tanpa teks atau penjelasan lain di luarnya. <transcript>{transcript_text}</transcript> INSTRUKSI JSON: - "title": Buat judul ringkasan yang menarik dan singkat berdasarkan isi transkrip. - "clips": Buat sebuah array berisi objek-objek klip. Setiap objek HARUS memiliki "start_time" dan "end_time" dalam format "HH:MM:SS". - "thumbnail_time": Pilih satu timestamp "HH:MM:SS" dari momen paling visual atau representatif di seluruh video. CONTOH FORMAT JSON WAJIB: ```json {{ "title": "Judul Ringkasan Video yang Menarik", "clips": [ {{ "start_time": "00:01:23", "end_time": "00:01:55" }}, {{ "start_time": "00:05:10", "end_time": "00:06:02" }} ], "thumbnail_time": "00:05:15" }} ```
    """
    try:
        logger_func("   🤖 Menghubungi AI OpenRouter untuk ringkasan video...")
        response, used_model = openrouter_chat_completion(
            ai_client,
            get_ai_model_candidates(ai_model_name),
            [{"role": "user", "content": prompt}],
            logger_func=logger_func,
            max_tokens=4096,
            temperature=0.7,
            response_format={ "type": "json_object" },
        )
        content = response.choices[0].message.content
        logger_func("   🤖 AI OpenRouter telah merespons.")
        if used_model != ai_model_name:
            logger_func(f"   ℹ️ Fallback model dipakai: {used_model}")

        json_match = re.search(r'```json\s*(\{.*\})\s*```', content, re.DOTALL) or re.search(r'(\{.*\})', content, re.DOTALL)
        if not json_match:
            # Kadang AI hanya mengembalikan JSON bersih
            try:
                summary_data = json.loads(content)
                logger_func("✅ AI (summary) berhasil membuat rencana video ringkasan (JSON murni)."); return summary_data
            except json.JSONDecodeError:
                logger_func(f"❌ ERROR: AI (summary) tidak memberikan output JSON yang valid.\n   Jawaban AI: {content}"); return None

        json_str = json_match.group(1) if len(json_match.groups()) > 0 else json_match.group(0)
        summary_data = json.loads(json_str)
        logger_func("✅ AI (summary) berhasil membuat rencana video ringkasan."); return summary_data
    except Exception as e:
        logging.error(f"❌ ERROR saat analisis AI (summary): {e}\n   Jawaban AI: {content if 'content' in locals() else 'Tidak ada respons'}")
        return None

def get_paraphrased_title_from_ai(original_title, ai_model_name, ai_client, logger_func=logging.info):
    prompt = f"""
    Anda adalah seorang ahli branding media sosial yang jago membuat judul video viral. Tugas Anda adalah menulis ulang judul video ini: "{original_title}" agar terdengar lebih keren, menarik, dan kekinian, namun tetap menjaga makna aslinya. ATURAN: Gunakan bahasa yang santai dan memancing rasa ingin tahu. Boleh tambahkan 1-2 emoji yang relevan. Output HANYA judul barunya saja, tanpa tanda kutip atau teks tambahan apapun.
    """
    try:
        logger_func("   🤖 Menghubungi AI OpenRouter untuk judul baru...")
        response, used_model = openrouter_chat_completion(
            ai_client,
            get_ai_model_candidates(ai_model_name),
            [{"role": "user", "content": prompt}],
            logger_func=logger_func,
            max_tokens=256,
            temperature=0.9,
        )
        new_title = response.choices[0].message.content.strip().replace('"', '')
        logger_func("   🤖 AI OpenRouter telah memberikan judul baru.")
        if used_model != ai_model_name:
            logger_func(f"   ℹ️ Fallback model dipakai: {used_model}")
        return new_title if new_title else None
    except Exception as e:
        logger_func(f"   ❌ Gagal membuat judul dengan AI: {e}"); return None

def embed_thumbnail(video_path, thumb_path, logger_func=logging.info):
    if not os.path.exists(video_path):
        logger_func(f"   ⚠️ Melewati penyematan thumbnail karena file video tidak ditemukan: {os.path.basename(video_path)}")
        return False
    try:
        logger_func("   📎 Menyematkan thumbnail ke video...")
        output_path = video_path.replace(".mp4", "_thumb.mp4")
        input_video = ffmpeg.input(video_path); input_thumb = ffmpeg.input(thumb_path)
        ok = run_ffmpeg_stream(
            ffmpeg.output(input_video, input_thumb, output_path, **{'c': 'copy', 'map': '0', 'map': '1', 'disposition:v:1': 'attached_pic'}),
            logger_func=logger_func,
        )
        if not ok:
            return False
        os.remove(video_path); os.rename(output_path, video_path)
        logger_func("   ✅ Thumbnail berhasil disematkan.")
        return True
    except ffmpeg.Error as e:
        stderr = getattr(e, "stderr", b"") or b""
        if isinstance(stderr, (bytes, bytearray)):
            stderr = stderr.decode('utf-8', errors='ignore').strip()
        logger_func(f"   ❌ Gagal menyematkan thumbnail (ffmpeg). {stderr if stderr else ''}".strip())
        logging.error(f"   ❌ Gagal menyematkan thumbnail: {e}\n{traceback.format_exc()}")
        return False
    except Exception as e:
        logging.error(f"   ❌ Gagal menyematkan thumbnail: {e}\n{traceback.format_exc()}")
        return False

def generate_thumbnail_from_video(video_path, timestamp, output_thumb_path, logger_func=logging.info):
    logger_func(f"   📸 Membuat thumbnail dari video pada {timestamp}...")
    try:
        ok = run_ffmpeg_stream(
            ffmpeg.input(video_path, ss=timestamp).output(output_thumb_path, vframes=1),
            logger_func=logger_func,
        )
        if not ok:
            return False
        logger_func(f"   ✅ Thumbnail berhasil dibuat: {os.path.basename(output_thumb_path)}"); return True
    except Exception as e:
        logger_func(f"   ❌ Gagal membuat thumbnail: {e}"); return False

def apply_subtitle_filter(video_stream, subtitle_file, font_filename):
    try: base_path = sys._MEIPASS
    except Exception: base_path = os.path.dirname(os.path.abspath(__file__))

    if not subtitle_file or not os.path.exists(subtitle_file):
        logging.error(f"Subtitle file tidak ditemukan: {subtitle_file}")
        return video_stream

    font_path = os.path.join(base_path, font_filename) if font_filename else ""
    escaped_subtitle_path = os.path.abspath(subtitle_file).replace('\\', '/')
    # Untuk Windows/libass: drive letter butuh escape jadi C\:/...
    escaped_subtitle_path = escaped_subtitle_path.replace(':', '\\:')
    # Escape koma untuk filtergraph
    escaped_subtitle_path = escaped_subtitle_path.replace(',', '\\,')

    filter_kwargs = {'filename': escaped_subtitle_path}

    style_options = 'Fontsize=22,PrimaryColour=&H00FFFFFF,BorderStyle=3,Outline=2,Shadow=1,MarginV=36,Bold=1'

    # 1) Jika font adalah file (.ttf/.otf) dan tersedia, arahkan fontsdir + FontName dari file tsb
    if font_filename and isinstance(font_filename, str) and font_filename.lower().endswith((".ttf", ".otf")) and os.path.exists(font_path):
        font_dir = os.path.dirname(font_path).replace('\\', '/').replace(':', '\\:')
        filter_kwargs['fontsdir'] = font_dir
        font_name = os.path.splitext(os.path.basename(font_filename))[0].replace('-', ' ')
        style_options = f'FontName={font_name},{style_options}'
    # 2) Jika font adalah nama family Windows (contoh: "Segoe UI"), pakai langsung
    elif font_filename and isinstance(font_filename, str) and font_filename.strip():
        style_options = f'FontName={font_filename.strip()},{style_options}'

    filter_kwargs['force_style'] = style_options
    return video_stream.filter('subtitles', **filter_kwargs)

# ==============================================================================
# FUNGSI PEMROSESAN VIDEO
# ==============================================================================

def process_clip(self, source_video, start_time, end_time, watermark_file, watermark_position, source_text, output_filename, style, music_file, music_volume, effects, remove_original_audio, original_audio_volume, is_short_mode=False, subtitle_file=None, font_filename=None,
                 shorts_background_video=None, presenter_overlay_video=None,
                 logger_func=logging.info):
    try:
        duration_seconds = sum(x * float(t) for x, t in zip([3600, 60, 1], end_time.split(":"))) - sum(x * float(t) for x, t in zip([3600, 60, 1], start_time.split(":")))
        main_video_input = ffmpeg.input(source_video, ss=start_time, to=end_time)

        main_video_stream = main_video_input.video

        if is_short_mode:
            probe = ffmpeg.probe(source_video)
            video_info = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
            main_w = int(video_info['width'])
            main_h = int(video_info['height'])

            if self.use_manual_crop.get() and self.manual_crop_coords:
                logger_func("   -> Menerapkan Crop Manual...")
                r = self.manual_crop_coords
                main_video_stream = main_video_stream.filter('crop',
                    w=f"iw*{r['w_ratio']}",
                    h=f"iw*{r['w_ratio']}*16/9",
                    x=f"iw*{r['x_ratio']}",
                    y=f"ih*{r['y_ratio']}"
                )
            else:
                logger_func("   -> Menerapkan Crop Otomatis...")
                target_h = main_h
                target_w = int(target_h * 9 / 16)
                if target_w > main_w:
                    target_w = main_w
                    target_h = int(target_w * 16 / 9)

                crop_x = (main_w - target_w) // 2
                crop_y = (main_h - target_h) // 2
                main_video_stream = main_video_stream.filter('crop', w=target_w, h=target_h, x=crop_x, y=crop_y)

        if is_short_mode and shorts_background_video:
            logger_func("   🔄 Mode Timpa Video Short aktif...")
            SHORT_BG_WIDTH = 1080
            SHORT_BG_HEIGHT = 1920
            OVERLAY_SCALE = 0.85
            SHORT_SPEED = 1.15

            clip_duration = duration_seconds

            background_input = ffmpeg.input(shorts_background_video, stream_loop=-1, t=clip_duration / SHORT_SPEED)
            background_video = background_input.video.filter('scale', w=SHORT_BG_WIDTH, h=SHORT_BG_HEIGHT)

            if style == "dynamic":
                main_video_stream = main_video_stream.zoompan(z='min(zoom+0.0015,1.25)', d=300)

            main_video_stream = main_video_stream.filter('eq', contrast=1.1, saturation=1.25).filter('unsharp', luma_msize_x=5, luma_msize_y=5, luma_amount=0.8)
            main_video_scaled = main_video_stream.filter('scale', w=f'{SHORT_BG_WIDTH*OVERLAY_SCALE}', h=-1)

            base_video = ffmpeg.overlay(background_video, main_video_scaled, x='(main_w-overlay_w)/2', y='(main_h-overlay_h)/2')
            base_video = base_video.filter('setpts', f'{1/SHORT_SPEED}*PTS')
        else:
            base_video = main_video_stream

        if not (is_short_mode and shorts_background_video):
            if style == "dynamic": base_video = base_video.zoompan(z='min(zoom+0.0015,1.15)', d=300)

        base_video = base_video.filter('eq', contrast=1.1, saturation=1.25).filter('unsharp', luma_msize_x=5, luma_msize_y=5, luma_amount=0.8)

        if effects.get('static_zoom'):
            zoom_level = self.zoom_level_var.get()
            logger_func(f"   -> Menerapkan Zoom Statis: {zoom_level:.2f}x...")
            base_video = base_video.filter('scale', f'iw*{zoom_level}', -1).filter('crop', 'iw', 'ih')

        if effects.get('mirror'): base_video = base_video.hflip()
        if effects.get('grayscale'): base_video = base_video.filter('hue', s=0)
        if effects.get('sepia'): base_video = base_video.filter('colorchannelmixer', rr=0.393, rg=0.769, rb=0.189, gr=0.349, gg=0.686, gb=0.168, br=0.272, bg=0.534, bb=0.131)
        if effects.get('negate'): base_video = base_video.filter('negate')
        if effects.get('color_boost'): base_video = base_video.filter('eq', saturation=1.8)

        if subtitle_file and os.path.exists(subtitle_file):
            logger_func("   ✍️ Memulai proses penambahan subtitle...")
            base_video = apply_subtitle_filter(base_video, subtitle_file, font_filename)
            logger_func("   ✅ Subtitle berhasil ditambahkan.")
        if source_text:
            base_video = base_video.drawtext(text=source_text, x='(w-text_w)/2', y='h-th-20', fontsize=20, fontcolor='white', box=1, boxcolor='black@0.5', boxborderw=5)

        processed_video = base_video

        if watermark_file:
            logger_func("   💧 Menambahkan watermark...")
            watermark_input = ffmpeg.input(watermark_file)
            pos_map = {"Kanan Atas":"x=main_w-overlay_w-10:y=10", "Kiri Atas":"x=10:y=10", "Kanan Bawah":"x=main_w-overlay_w-10:y=main_h-overlay_h-10", "Kiri Bawah":"x=10:y=main_h-overlay_h-10", "Tengah":"x=(main_w-overlay_w)/2:y=(main_h-overlay_h)/2"}
            pos_str = pos_map.get(watermark_position, pos_map["Kanan Atas"])
            if watermark_position == "Posisi Acak":
                pos_str = "x=if(lt(mod(t,10),5),10,main_w-overlay_w-10):y=if(lt(mod(t,20),10),10,main_h-overlay_h-10)"

            pos_kwargs = dict(item.split('=') for item in pos_str.split(':'))
            processed_video = ffmpeg.overlay(processed_video, watermark_input, **pos_kwargs)
            logger_func("   ✅ Watermark berhasil ditambahkan.")

        audio_inputs = []
        SHORT_SPEED = 1.15
        if is_short_mode and shorts_background_video:
            if not remove_original_audio:
                original_audio_stream = main_video_input.audio.filter('volume', original_audio_volume / 100.0).filter('atempo', SHORT_SPEED)
                audio_inputs.append(original_audio_stream)
            if music_file:
                music_clip_duration = duration_seconds / SHORT_SPEED
                music_audio_stream = ffmpeg.input(music_file, stream_loop=-1, t=music_clip_duration).audio.filter('volume', music_volume / 100.0)
                audio_inputs.append(music_audio_stream)
        else:
            if not remove_original_audio:
                audio_inputs.append(main_video_input.audio.filter('volume', original_audio_volume/100.0))
            if music_file:
                audio_inputs.append(ffmpeg.input(music_file, stream_loop=-1, t=duration_seconds).audio.filter('volume', music_volume/100.0))

        final_audio = None
        if len(audio_inputs) > 1:
            final_audio = ffmpeg.filter(audio_inputs, 'amix', duration='longest', dropout_transition=0)
        elif audio_inputs:
            final_audio = audio_inputs[0]

        logger_func("   🔨 Merender video akhir...")
        if final_audio:
            # NOTE: `-shortest` adalah flag (tanpa nilai). Pada ffmpeg-python gunakan `shortest=None`,
            # jika diberi angka akan berpotensi dianggap sebagai nama output file ("1") oleh ffmpeg.
            final_output = ffmpeg.output(
                processed_video, final_audio, output_filename,
                vcodec='libx264', preset='fast', crf=23, shortest=None,
                **mp4_output_kwargs(),
            )
        else:
            final_output = ffmpeg.output(
                processed_video, output_filename,
                vcodec='libx264', preset='fast', crf=23,
                **mp4_output_kwargs(),
            )

        if not run_ffmpeg_stream(final_output, logger_func=logger_func):
            return
        logger_func("   ✅ Video akhir berhasil dirender.")

    except ffmpeg.Error as e:
        stderr = getattr(e, "stderr", b"") or b""
        if isinstance(stderr, (bytes, bytearray)):
            stderr = stderr.decode('utf-8', errors='ignore').strip()
        logging.error(f"❌ ERROR saat memproses klip: Perintah ffmpeg gagal.")
        if stderr:
            logging.error(f"   Stderr ffmpeg: {stderr}")
    except Exception as e:
        logging.error(f"❌ TERJADI ERROR LAIN saat memproses klip: {e}\n{traceback.format_exc()}")

def process_single_clip_16x9(self, source_video, start_time, end_time, watermark_file, watermark_position, output_filename, music_file, music_volume, effects, remove_original_audio, original_audio_volume, subtitle_file=None, font_filename=None,
                             presenter_overlay_video=None, logger_func=logging.info):
    try:
        # Menerima start_time/end_time sebagai detik (float/int)
        duration_seconds = float(end_time) - float(start_time)

        main_video = ffmpeg.input(source_video, ss=start_time, t=duration_seconds)
        base_video = main_video.video

        if effects.get('static_zoom'):
            zoom_level = self.zoom_level_var.get()
            logger_func(f"   -> Menerapkan Zoom Statis: {zoom_level:.2f}x...")
            base_video = base_video.filter('scale', f'iw*{zoom_level}', -1).filter('crop', 'iw', 'ih')

        for effect, enabled in effects.items():
            if enabled:
                if effect == 'static_zoom': continue
                if effect == 'mirror': base_video = base_video.hflip()
                elif effect == 'grayscale': base_video = base_video.filter('hue', s=0)
                elif effect == 'sepia': base_video = base_video.filter('colorchannelmixer', rr=0.393, rg=0.769, rb=0.189, gr=0.349, gg=0.686, gb=0.168, br=0.272, bg=0.534, bb=0.131)
                elif effect == 'negate': base_video = base_video.filter('negate')
                elif effect == 'color_boost': base_video = base_video.filter('eq', saturation=1.8)

        if subtitle_file and os.path.exists(subtitle_file):
            logger_func("   ✍️ Memulai proses penambahan subtitle...")
            base_video = apply_subtitle_filter(base_video, subtitle_file, font_filename)
            logger_func("   ✅ Subtitle berhasil ditambahkan.")

        processed_video = base_video

        if presenter_overlay_video:
             logger_func("   ⚠️ Peringatan: Overlay presenter diabaikan untuk mode ini.")

        if watermark_file:
            logger_func("   💧 Menambahkan watermark...")
            watermark_input = ffmpeg.input(watermark_file)
            pos_map = {"Kanan Atas":"x=main_w-overlay_w-10:y=10", "Kiri Atas":"x=10:y=10", "Kanan Bawah":"x=main_w-overlay_w-10:y=main_h-overlay_h-10", "Kiri Bawah":"x=10:y=main_h-overlay_h-10", "Tengah":"x=(main_w-overlay_w)/2:y=(main_h-overlay_h)/2"}
            pos_str = pos_map.get(watermark_position, pos_map["Kanan Atas"])
            if watermark_position == "Posisi Acak":
                pos_str = "x=if(lt(mod(t,10),5),10,main_w-overlay_w-10):y=if(lt(mod(t,20),10),10,main_h-overlay_h-10)"
            pos_kwargs = dict(item.split('=') for item in pos_str.split(':'))
            processed_video = ffmpeg.overlay(processed_video, watermark_input, **pos_kwargs)
            logger_func("   ✅ Watermark berhasil ditambahkan.")

        audio_inputs = []
        if not remove_original_audio: audio_inputs.append(main_video.audio.filter('volume', original_audio_volume/100.0))
        if music_file:
            audio_inputs.append(ffmpeg.input(music_file, stream_loop=-1, t=duration_seconds).audio.filter('volume', music_volume/100.0))

        final_audio = None
        if len(audio_inputs) > 1:
            final_audio = ffmpeg.filter(audio_inputs, 'amix', duration='longest', dropout_transition=0)
        elif audio_inputs:
            final_audio = audio_inputs[0]

        logger_func("   🔨 Merender video akhir...")
        if final_audio:
            final_output = ffmpeg.output(
                processed_video, final_audio, output_filename,
                vcodec='libx264', preset='fast', crf=23,
                **mp4_output_kwargs(),
            )
        else:
            final_output = ffmpeg.output(
                processed_video, output_filename,
                vcodec='libx264', preset='fast', crf=23,
                **mp4_output_kwargs(),
            )
        if not run_ffmpeg_stream(final_output, logger_func=logger_func):
            return
        logger_func(f"   ✅ Berhasil membuat: {os.path.basename(output_filename)}")
    except ffmpeg.Error as e:
        stderr = getattr(e, "stderr", b"") or b""
        if isinstance(stderr, (bytes, bytearray)):
            stderr = stderr.decode('utf-8', errors='ignore').strip()
        logging.error(f"❌ Gagal memproses klip {os.path.basename(output_filename)}.")
        if stderr:
            logging.error(f"   Stderr ffmpeg: {stderr}")
    except Exception as e:
        logging.error(f"❌ ERROR LAIN saat memproses klip {os.path.basename(output_filename)}: {e}")

def process_long_simple_video(self, source_video, all_clips, watermark_file, watermark_position, output_filename, style, music_file, music_volume, effects, remove_original_audio, original_audio_volume, source_text, transcription_result=None, font_filename=None,
                              presenter_overlay_video=None, logger_func=logging.info):
    temp_srt_path = None
    try:
        logger_func("   🎬 Memulai proses penggabungan klip ringkasan...")
        clip_streams = [ffmpeg.input(source_video, ss=c['start_time'], to=c['end_time']) for c in all_clips]
        if not clip_streams: logger_func("   ❌ Tidak ada klip untuk digabungkan."); return

        total_duration = 0.0
        for c in all_clips:
            start_s = sum(x * float(t) for x, t in zip([3600, 60, 1], c['start_time'].split(":")))
            end_s = sum(x * float(t) for x, t in zip([3600, 60, 1], c['end_time'].split(":")))
            total_duration += (end_s - start_s)
        logger_func(f"   Total durasi video ringkasan: {time.strftime('%H:%M:%S', time.gmtime(total_duration))}")

        concatenated_video = ffmpeg.concat(*[s.video for s in clip_streams], v=1, a=0).filter('setpts', 'PTS-STARTPTS')
        concatenated_audio = ffmpeg.concat(*[s.audio for s in clip_streams], v=0, a=1).filter('asetpts', 'PTS-STARTPTS')
        logger_func("   ✅ Klip berhasil digabungkan.")

        base_video = concatenated_video
        if style == "dynamic": base_video = base_video.zoompan(z='min(zoom+0.0015,1.15)', d=12*25).filter('eq', contrast=1.1, saturation=1.3).filter('unsharp', luma_msize_x=5, luma_msize_y=5, luma_amount=1.0)
        else: base_video = base_video.filter('eq', contrast=1.1, saturation=1.25).filter('unsharp', luma_msize_x=5, luma_msize_y=5, luma_amount=0.8)

        if effects.get('static_zoom'):
            zoom_level = self.zoom_level_var.get()
            logger_func(f"   -> Menerapkan Zoom Statis: {zoom_level:.2f}x...")
            base_video = base_video.filter('scale', f'iw*{zoom_level}', -1).filter('crop', 'iw', 'ih')

        for effect, enabled in effects.items():
            if enabled:
                if effect == 'static_zoom': continue
                if effect == 'mirror': base_video = base_video.hflip()
                elif effect == 'grayscale': base_video = base_video.filter('hue', s=0)
                elif effect == 'sepia': base_video = base_video.filter('colorchannelmixer', rr=0.393, rg=0.769, rb=0.189, gr=0.349, gg=0.686, gb=0.168, br=0.272, bg=0.534, bb=0.131)
                elif effect == 'negate': base_video = base_video.filter('negate')
                elif effect == 'color_boost': base_video = base_video.filter('eq', saturation=1.8)

        if transcription_result and self.burn_subtitles.get():
            logger_func("   ✍️ Menyesuaikan subtitle untuk video ringkasan...")
            temp_srt_path = os.path.join(os.path.dirname(output_filename), f"_temp_sub_{int(time.time())}.srt")
            time_offset, total_duration_map = 0.0, {}
            for i, clip in enumerate(all_clips):
                start_s = sum(x * float(t) for x, t in zip([3600, 60, 1], clip['start_time'].split(":")))
                end_s = sum(x * float(t) for x, t in zip([3600, 60, 1], clip['end_time'].split(":")))
                total_duration_map[i] = {'start_s': start_s, 'end_s': end_s, 'offset': time_offset}
                time_offset += (end_s - start_s)
            adjusted_segments = []
            for seg in transcription_result['segments']:
                for i, clip_info in total_duration_map.items():
                    if seg['start'] >= clip_info['start_s'] and seg['end'] <= clip_info['end_s']:
                        new_seg = {**seg,
                                   'start': seg['start'] - clip_info['start_s'] + clip_info['offset'],
                                   'end': seg['end'] - clip_info['start_s'] + clip_info['offset']
                                  }
                        adjusted_segments.append(new_seg);
                        break

            if generate_srt_file({'segments': adjusted_segments}, temp_srt_path, logger_func):
                base_video = apply_subtitle_filter(base_video, temp_srt_path, font_filename)
                logger_func("   ✅ Subtitle berhasil ditambahkan.")
            else:
                logger_func("   ❌ Gagal membuat file subtitle untuk ringkasan.")
                temp_srt_path = None

        if source_text: base_video = base_video.drawtext(text=source_text, x='(w-text_w)/2', y='h-th-20', fontsize=24, fontcolor='white', box=1, boxcolor='black@0.5', boxborderw=5)

        processed_video = base_video

        if presenter_overlay_video:
            logger_func("   ⚠️ Peringatan: Overlay presenter diabaikan untuk mode ini.")

        if watermark_file:
            logger_func("   💧 Menambahkan watermark...")
            watermark_input = ffmpeg.input(watermark_file)
            pos_map = {"Kanan Atas":"x=main_w-overlay_w-10:y=10", "Kiri Atas":"x=10:y=10", "Kanan Bawah":"x=main_w-overlay_w-10:y=main_h-overlay_h-10", "Kiri Bawah":"x=10:y=main_h-overlay_h-10","Tengah":"x=(main_w-overlay_w)/2:y=(main_h-overlay_h)/2"}
            pos_str = pos_map.get(watermark_position, pos_map["Kanan Atas"])
            if watermark_position == "Posisi Acak":
                pos_str = "x=if(lt(mod(t,10),5),10,main_w-overlay_w-10):y=if(lt(mod(t,20),10),10,main_h-overlay_h-10)"
            pos_kwargs = dict(item.split('=') for item in pos_str.split(':'))
            processed_video = ffmpeg.overlay(processed_video, watermark_input, **pos_kwargs)
            logger_func("   ✅ Watermark berhasil ditambahkan.")

        audio_inputs = []
        if not remove_original_audio: audio_inputs.append(concatenated_audio.filter('volume', original_audio_volume/100.0))
        if music_file:
            audio_inputs.append(ffmpeg.input(music_file, stream_loop=-1, t=total_duration).audio.filter('volume', music_volume/100.0))

        final_audio = None
        if len(audio_inputs) > 1: final_audio = ffmpeg.filter(audio_inputs, 'amix', duration='longest', dropout_transition=0)
        elif audio_inputs: final_audio = audio_inputs[0]

        logger_func("   🔨 Merender video akhir...")
        if final_audio:
            final_output = ffmpeg.output(
                processed_video, final_audio, output_filename,
                vcodec='libx264', preset='fast', crf=23,
                **mp4_output_kwargs(),
            )
        else:
            final_output = ffmpeg.output(
                processed_video, output_filename,
                vcodec='libx264', preset='fast', crf=23,
                **mp4_output_kwargs(),
            )
        if not run_ffmpeg_stream(final_output, logger_func=logger_func):
            return
        logger_func(f"   ✅ Video ringkasan berhasil dibuat: {os.path.basename(output_filename)}")
    except ffmpeg.Error as e:
        stderr = getattr(e, "stderr", b"") or b""
        if isinstance(stderr, (bytes, bytearray)):
            stderr = stderr.decode('utf-8', errors='ignore').strip()
        logging.error(f"❌ ERROR saat memproses video ringkasan: Perintah ffmpeg gagal.")
        if stderr:
            logging.error(f"   Stderr ffmpeg: {stderr}")
    except Exception as e:
        logging.error(f"❌ TERJADI ERROR LAIN saat memproses video ringkasan: {e}\n{traceback.format_exc()}")
    finally:
        if temp_srt_path and os.path.exists(temp_srt_path):
            try:
                os.remove(temp_srt_path)
                logger_func("   🗑️ File subtitle sementara untuk ringkasan telah dihapus.")
            except Exception as e:
                logger_func(f"   ⚠️ Gagal menghapus file subtitle sementara: {e}")

# ==============================================================================
# KELAS UTAMA APLIKASI GUI
# ==============================================================================
class VideoClipperApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Youtube Video Auto Clipper (Telegram : @nezastore)")
        self.root.geometry("950x880")
        self.root.resizable(True, True)
        self.root.minsize(900, 850)

        self._init_theme()
        self._try_set_window_icon(self.root)

        # --- Setup Log Queue ---
        self.log_queue = queue.Queue()
        setup_logging(self.log_queue) # Menginisialisasi sistem logging
        ensure_bundled_binaries_on_path(logging.info)

        # --- Variabel-variabel ---
        self.output_folder = StringVar()
        self.watermark_file = StringVar()
        self.music_file = StringVar()
        self.watermark_full_path = ""
        self.music_full_path = ""
        self.device_id_var = StringVar(value=get_device_id())

        cookie_path = get_cookie_file_path()
        if os.path.exists(cookie_path) and not cookie_file_has_any_live_cookie(cookie_path):
            maybe_delete_cookie_file(cookie_path, logger_func=logging.warning, reason="expired")
        self.cookies_status_var = StringVar()
        self.cookie_accounts_window = None
        # Jika ada profil cookies aktif tersimpan, pastikan cookies.txt terisi
        active_profile = get_active_cookie_profile_name()
        if active_profile and not os.path.exists(cookie_path):
            set_active_cookie_profile(active_profile, logger_func=logging.info)
        self.refresh_cookie_status()
        self.license_queue = queue.Queue()
        self.whisper_model_selection = StringVar(value="base")
        self.effects_vars = {
            'mirror': BooleanVar(), 'grayscale': BooleanVar(),
            'sepia': BooleanVar(), 'negate': BooleanVar(),
            'color_boost': BooleanVar(),
            'static_zoom': BooleanVar(value=False)
        }
        self.zoom_level_var = DoubleVar(value=1.10)
        self.zoom_display_var = StringVar(value="1.10x")

        self.auto_open_output_var = BooleanVar(value=True)
        self.last_output_folder = ""

        self.music_volume_var = IntVar(value=15)
        self.volume_display_var = StringVar(value="15%")
        self.remove_original_audio_var = BooleanVar(value=False)
        self.original_audio_volume_var = IntVar(value=100)
        self.original_volume_display_var = StringVar(value="100%")
        self.cut_mode = StringVar(value="manual")
        self.manual_start_time = StringVar(value="00:00:00")
        self.manual_end_time = StringVar(value="00:01:00")
        self.scrape_channel_url = StringVar()
        self.scrape_count = IntVar(value=5)
        self.is_shorts_scraper_mode = BooleanVar(value=False)
        self.use_ai_for_shorts_title = BooleanVar(value=False)
        self.use_custom_api_key = BooleanVar(value=False)
        self.custom_api_key = StringVar()
        self.api_key_hint_var = StringVar(value="API Key: (belum tersimpan)")
        self._settings_save_job = None
        self.stop_event = threading.Event()
        self.watermark_position = StringVar(value="Kanan Atas")
        self.use_custom_thumbnail = BooleanVar(value=False)
        self.thumbnail_file = StringVar(value="Thumbnail: (belum dipilih)")
        self.thumbnail_full_path = ""
        self.burn_subtitles = BooleanVar(value=False)
        self.is_long_simple_mode_active = BooleanVar(value=False)
        self.long_simple_sub_mode = StringVar(value="AI_SUMMARY")
        self.long_simple_add_source = BooleanVar(value=False)
        self.summary_detail_level = StringVar(value="SEDANG")
        self.font_map = {
            "Segoe UI (Windows)": "Segoe UI",
            "Arial (Windows)": "Arial",
            "Calibri (Windows)": "Calibri",
            "Tahoma (Windows)": "Tahoma",
            "Verdana (Windows)": "Verdana",
            "Impact (Windows)": "Impact",
            "Montserrat Bold (Bundled)": "Montserrat-Bold.ttf",
            "Bebas Neue (Bundled)": "BebasNeue-Regular.ttf",
            "Poppins Bold (Bundled)": "Poppins-Bold.ttf",
        }
        self.subtitle_font_selection = StringVar(value="Montserrat Bold")
        self.long_to_short_add_source = BooleanVar(value=False)

        self.overlay_short_var = BooleanVar(value=False)
        self.short_background_file = StringVar(value="Video Latar: (belum dipilih)")
        self.short_background_full_path = ""
        self.presenter_overlay_var = BooleanVar(value=False)
        self.presenter_overlay_file = StringVar(value="Video Presenter: (belum dipilih)")
        self.presenter_overlay_full_path = ""

        self.use_manual_crop = BooleanVar(value=False)
        self.manual_crop_status = StringVar(value="Status: Belum diatur.")
        self.manual_crop_coords = None

        self.ai_client = None

        self.setup_ui()

        # Load settings (API key) + autosave
        self._load_user_settings()
        self._toggle_api_key_state()
        self._update_api_key_hint()
        try:
            self.custom_api_key.trace_add("write", lambda *_: (self._update_api_key_hint(), self._schedule_save_settings()))
            self.use_custom_api_key.trace_add("write", lambda *_: (self._toggle_api_key_state(), self._schedule_save_settings()))
        except Exception:
            pass

        self.start_ui_animations()
        self.root.after(100, self.process_log_queue); self.root.after(200, self.process_license_queue); self.root.after(500, self._initial_license_check)
        self.cookies_auto_thread = None
        self.cookies_auto_stop = threading.Event()

    def _init_theme(self):
        # Palet warna modern (dark) dengan aksen neon
        self.theme = {
            "bg": "#0b1220",
            "panel": "#101a2d",
            "panel_border": "#223455",
            "fg": "#e6edf7",
            "muted": "#9aa8c2",
            "accent": "#7c3aed",
            "input_bg": "#0a1326",
            "button_bg": "#16243f",
            "button_hover": "#1c2e4d",
            "link": "#38bdf8",
            "success": "#22c55e",
            "danger": "#ef4444",
            "warning": "#f59e0b",
        }
        self.accent_palette = ["#7c3aed", "#22d3ee", "#ff7b54", "#10b981"]
        self.accent_index = 0
        self.accent_mix = 0.0
        self.glow_phase = 0
        self.glow_panels = []
        self.pulse_targets = []

        self.root.configure(bg=self.theme["bg"])
        self.root.option_add("*Background", self.theme["panel"])
        self.root.option_add("*Foreground", self.theme["fg"])
        self.root.option_add("*Label.Background", self.theme["panel"])
        self.root.option_add("*Label.Foreground", self.theme["fg"])
        self.root.option_add("*Button.Background", self.theme["button_bg"])
        self.root.option_add("*Button.Foreground", self.theme["fg"])
        self.root.option_add("*Checkbutton.Background", self.theme["panel"])
        self.root.option_add("*Checkbutton.Foreground", self.theme["fg"])
        self.root.option_add("*Radiobutton.Background", self.theme["panel"])
        self.root.option_add("*Radiobutton.Foreground", self.theme["fg"])
        self.root.option_add("*Entry.Background", self.theme["input_bg"])
        self.root.option_add("*Entry.Foreground", self.theme["fg"])
        self.root.option_add("*Text.Background", self.theme["input_bg"])
        self.root.option_add("*Text.Foreground", self.theme["fg"])
        self.root.option_add("*Entry.insertBackground", self.theme["accent"])
        self.root.option_add("*Text.insertBackground", self.theme["accent"])
        self.root.option_add("*Menubutton.Background", self.theme["input_bg"])
        self.root.option_add("*Menubutton.Foreground", self.theme["fg"])
        self.root.option_add("*Menu.Background", self.theme["panel"])
        self.root.option_add("*Menu.Foreground", self.theme["fg"])

        self.progress_style = ttk.Style()
        try:
            self.progress_style.theme_use("clam")
        except Exception:
            pass
        self.progress_style.configure(
            "Accent.Horizontal.TProgressbar",
            troughcolor=self.theme["panel"],
            bordercolor=self.theme["panel"],
            background=self.theme["accent"],
            lightcolor=self.theme["accent"],
            darkcolor=self.theme["accent"]
        )

    def _try_set_window_icon(self, window):
        try:
            icon_path = find_app_icon_ico()
            if icon_path:
                window.iconbitmap(icon_path)
        except Exception:
            pass

    def _load_user_settings(self):
        data = load_settings()
        try:
            api_key = str(data.get("custom_api_key", "") or "")
            use_custom = bool(data.get("use_custom_api_key", False))
            if api_key:
                self.custom_api_key.set(api_key)
            self.use_custom_api_key.set(use_custom)
        except Exception:
            pass

    def _save_user_settings_now(self):
        data = {
            "use_custom_api_key": bool(self.use_custom_api_key.get()),
            "custom_api_key": (self.custom_api_key.get() or "").strip(),
        }
        save_settings(data)

    def _schedule_save_settings(self):
        try:
            if self._settings_save_job:
                self.root.after_cancel(self._settings_save_job)
        except Exception:
            pass
        self._settings_save_job = self.root.after(600, self._save_user_settings_now)

    def _toggle_api_key_state(self):
        try:
            if hasattr(self, "api_key_entry"):
                self.api_key_entry.config(state="normal" if self.use_custom_api_key.get() else "disabled")
        except Exception:
            pass

    def _update_api_key_hint(self):
        key = (self.custom_api_key.get() or "").strip()
        self.api_key_hint_var.set(f"API Key tersimpan: {mask_secret(key)}" if key else "API Key: (belum tersimpan)")

    def clear_saved_api_key(self):
        self.custom_api_key.set("")
        self._update_api_key_hint()
        self._save_user_settings_now()
        self.popup_info("API Key", "API Key tersimpan telah dihapus.")

    def _center_window(self, window):
        window.update_idletasks()
        w = window.winfo_width()
        h = window.winfo_height()
        if w <= 1 or h <= 1:
            w = window.winfo_reqwidth()
            h = window.winfo_reqheight()

        try:
            px = self.root.winfo_rootx()
            py = self.root.winfo_rooty()
            pw = self.root.winfo_width()
            ph = self.root.winfo_height()
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 2)
        except Exception:
            sw = window.winfo_screenwidth()
            sh = window.winfo_screenheight()
            x = (sw - w) // 2
            y = (sh - h) // 2

        window.geometry(f"{w}x{h}+{x}+{y}")

    def _styled_dialog(self, title: str, message: str, kind: str = "info", buttons=None, default=None):
        """
        Popup modal bertema (mengganti messagebox).
        kind: info | warning | error | confirm
        buttons: list of (text, value, style) where style in: accent|danger|warning|neutral
        """
        if buttons is None:
            buttons = [("OK", True, "accent")]

        win = Toplevel(self.root)
        win.title(title)
        win.configure(bg=self.theme["bg"])
        win.resizable(False, False)
        win.transient(self.root)
        self._try_set_window_icon(win)

        # Container
        card = Frame(win, bg=self.theme["panel"], highlightthickness=1, highlightbackground=self.theme["panel_border"])
        card.pack(fill="both", expand=True, padx=14, pady=14)

        # Header
        header = Frame(card, bg=self.theme["panel"])
        header.pack(fill="x", padx=14, pady=(14, 8))

        if kind == "error":
            bar = self.theme["danger"]
            glyph = "✖"
        elif kind == "warning":
            bar = self.theme["warning"]
            glyph = "⚠"
        elif kind == "confirm":
            bar = self.theme["accent"]
            glyph = "?"
        else:
            bar = self.theme["accent"]
            glyph = "ⓘ"

        Frame(header, bg=bar, width=6, height=24).pack(side="left", padx=(0, 10), pady=2)
        Label(header, text=glyph, bg=self.theme["panel"], fg=bar, font=("Segoe UI", 16, "bold")).pack(side="left")
        Label(header, text=title, bg=self.theme["panel"], fg=self.theme["fg"], font=("Segoe UI", 12, "bold")).pack(side="left", padx=(10, 0))

        # Body
        body = Frame(card, bg=self.theme["panel"])
        body.pack(fill="both", expand=True, padx=14, pady=(0, 10))

        msg = Label(
            body,
            text=message,
            bg=self.theme["panel"],
            fg=self.theme["muted"],
            justify="left",
            wraplength=520,
        )
        msg.pack(fill="x", pady=(0, 8))

        # Footer buttons
        footer = Frame(card, bg=self.theme["panel"])
        footer.pack(fill="x", padx=14, pady=(0, 14))

        result = {"value": default}

        def _close(value=None):
            result["value"] = value
            try:
                win.grab_release()
            except Exception:
                pass
            win.destroy()

        def _btn_colors(style_key: str):
            if style_key == "danger":
                return self.theme["danger"], "#ffffff"
            if style_key == "warning":
                return self.theme["warning"], "#111827"
            if style_key == "neutral":
                return self.theme["button_bg"], self.theme["fg"]
            return self.theme["accent"], "#ffffff"

        # Create buttons right-aligned
        for text, value, style_key in reversed(buttons):
            bgc, fgc = _btn_colors(style_key)
            Button(
                footer,
                text=text,
                command=lambda v=value: _close(v),
                bg=bgc,
                fg=fgc,
                activebackground=bgc,
                activeforeground=fgc,
                relief="flat",
                padx=14,
                pady=6,
            ).pack(side="right", padx=(8, 0))

        win.protocol("WM_DELETE_WINDOW", lambda: _close(default))

        # Modal behavior
        win.update_idletasks()
        self._center_window(win)
        win.grab_set()
        win.focus_force()
        self.root.wait_window(win)
        return result["value"]

    def popup_info(self, title: str, message: str):
        return self._styled_dialog(title, message, kind="info", buttons=[("OK", True, "accent")], default=True)

    def popup_warning(self, title: str, message: str):
        return self._styled_dialog(title, message, kind="warning", buttons=[("OK", True, "warning")], default=True)

    def popup_error(self, title: str, message: str):
        return self._styled_dialog(title, message, kind="error", buttons=[("OK", True, "danger")], default=True)

    def popup_confirm(self, title: str, message: str, yes_text="Ya", no_text="Tidak"):
        return bool(self._styled_dialog(
            title,
            message,
            kind="confirm",
            buttons=[(no_text, False, "neutral"), (yes_text, True, "accent")],
            default=False,
        ))

    def update_zoom_label(self, val):
        self.zoom_display_var.set(f"{float(val):.2f}x")

    def toggle_zoom_slider(self):
        state = "normal" if self.effects_vars['static_zoom'].get() else "disabled"
        self.zoom_slider.config(state=state)

    def toggle_long_simple_options(self):
        state = "normal" if self.is_long_simple_mode_active.get() else "disabled"
        for widget in self.long_simple_options_frame.winfo_children():
            if isinstance(widget, (Radiobutton, Frame, Checkbutton)):
                if isinstance(widget, Radiobutton):
                    widget.configure(state=state)
                else:
                    for child_widget in widget.winfo_children():
                        if widget == self.summary_detail_frame and self.long_simple_sub_mode.get() == "AI_SUMMARY":
                             child_widget.configure(state="normal" if self.is_long_simple_mode_active.get() else "disabled")
                        else:
                            child_widget.configure(state=state)

        is_ai_mode = self.long_simple_sub_mode.get() == "AI_SUMMARY"
        source_state = "disabled"
        detail_state = "disabled"

        if self.is_long_simple_mode_active.get():
            if is_ai_mode:
                source_state = "normal"
                detail_state = "normal"

        self.long_simple_source_cb.config(state=source_state)
        for widget in self.summary_detail_frame.winfo_children():
            widget.configure(state=detail_state)

    def setup_ui(self):
        def style_option_menu(option_menu):
            try:
                option_menu.configure(
                    bg=self.theme["input_bg"],
                    fg=self.theme["fg"],
                    activebackground=self.theme["panel"],
                    activeforeground=self.theme["fg"],
                    relief="flat",
                    bd=0,
                    highlightthickness=1,
                    highlightbackground=self.theme["panel_border"],
                )
                option_menu["menu"].configure(
                    bg=self.theme["panel"],
                    fg=self.theme["fg"],
                    activebackground=self.theme["accent"],
                    activeforeground=self.theme["bg"],
                    bd=0,
                )
            except Exception:
                pass

        def make_panel(parent, title):
            lf = LabelFrame(parent, text=title, font=("Helvetica", 10, "bold"), padx=10, pady=10,
                            bg=self.theme["panel"], fg=self.theme["fg"],
                            bd=0, highlightthickness=1, highlightbackground=self.theme["panel_border"])
            self.glow_panels.append(lf)
            return lf

        main_frame = Frame(self.root, padx=10, pady=10, bg=self.theme["bg"]); main_frame.pack(fill="both", expand=True)
        self.accent_canvas = Canvas(main_frame, height=6, highlightthickness=0, bd=0, bg=self.theme["accent"])
        self.accent_canvas.pack(fill="x", pady=(0,8))

        content_wrapper = Frame(main_frame, bg=self.theme["bg"]); content_wrapper.pack(fill="both", expand=True)
        main_content_frame = Frame(content_wrapper, bg=self.theme["bg"]); main_content_frame.pack(fill="both", expand=True)
        left_container = Frame(main_content_frame, width=420, bg=self.theme["bg"]); left_container.pack(side="left", fill="y", padx=(0, 5)); left_container.pack_propagate(False)
        canvas = Canvas(left_container, highlightthickness=0, bg=self.theme["bg"], bd=0); scrollbar = Scrollbar(left_container, orient="vertical", command=canvas.yview)
        scrollable_frame = Frame(canvas, bg=self.theme["bg"]); scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        def _on_mousewheel(event): canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        scroll_window_id = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(scroll_window_id, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set); scrollbar.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
        right_column = Frame(main_content_frame, bg=self.theme["bg"]); right_column.pack(side="left", fill="both", expand=True)

        license_lf = make_panel(scrollable_frame, "Manajemen Lisensi")
        license_lf.pack(fill="x", pady=(5,10), padx=10)

        Label(license_lf, text="ID Perangkat:").grid(row=0, column=0, sticky="w")
        id_entry = Entry(
            license_lf,
            textvariable=self.device_id_var,
            state="readonly",
            readonlybackground=self.theme["input_bg"],
            fg=self.theme["fg"],
            relief="solid",
            borderwidth=1,
            highlightthickness=1,
            highlightbackground=self.theme["panel_border"],
            highlightcolor=self.theme["accent"],
        )
        id_entry.grid(row=0, column=1, sticky="ew")
        Button(license_lf, text="Salin ID", command=self.get_and_copy_uuid).grid(row=0, column=2, padx=(10,0))
        Label(license_lf, text="Status Lisensi:").grid(row=1, column=0, sticky="w", pady=(5,0))
        self.license_status_label = Label(license_lf, text="- MENGECEK -", font=("Helvetica", 10, "bold"), fg=self.theme["muted"]); self.license_status_label.grid(row=1, column=1, sticky="w", pady=(5,0)); license_lf.columnconfigure(1, weight=1)

        api_lf = make_panel(scrollable_frame, "Konfigurasi API Key OpenRouter"); api_lf.pack(fill="x", pady=(0,10), padx=10)
        Checkbutton(api_lf, text="Gunakan API Key Sendiri", variable=self.use_custom_api_key, command=self._toggle_api_key_state).pack(anchor="w")
        Label(api_lf, text="Masukkan API Key OpenRouter Anda:").pack(anchor="w", pady=(5,0))
        self.api_key_entry = Entry(api_lf, textvariable=self.custom_api_key, state="disabled", show="•"); self.api_key_entry.pack(fill="x")
        Label(api_lf, textvariable=self.api_key_hint_var, fg=self.theme["muted"], bg=self.theme["panel"], justify="left", wraplength=380).pack(anchor="w", padx=2, pady=(4, 0))
        Button(api_lf, text="🗑️ Hapus API Key Tersimpan", command=self.clear_saved_api_key).pack(fill="x", pady=(6, 0))

        cookies_lf = make_panel(scrollable_frame, "Cookies YouTube"); cookies_lf.pack(fill="x", pady=(0,10), padx=10)
        self.cookies_help_label = Label(
            cookies_lf,
            text=(
                "Cookies digunakan untuk mengunduh video yang memerlukan login.\n\n"
                "Klik Auto Setup, login Google, buka YouTube, lalu tutup browser. "
                "Cookies akan disimpan per akun dan bot dapat berganti otomatis jika cookies tidak valid."
            ),
            justify="left",
            fg=self.theme["muted"],
            bg=self.theme["panel"],
            wraplength=380,
        )
        self.cookies_help_label.pack(anchor="w", fill="x", padx=2)
        Button(cookies_lf, text="🌐 Auto Setup Cookies (Chrome Bersih)", command=self.start_auto_setup_cookies).pack(fill="x", pady=(8, 0))
        btn_row = Frame(cookies_lf, bg=self.theme["panel"]); btn_row.pack(fill="x", pady=(6, 0))
        Button(btn_row, text="👥 Kelola Akun Cookies", command=self.show_cookie_accounts).pack(side="left", expand=True, fill="x")
        Button(btn_row, text="🗑️ Hapus Akun Cookies Aktif", command=self.delete_active_cookie_profile).pack(side="left", expand=True, fill="x", padx=(6, 0))
        self.cookies_status_label = Label(cookies_lf, textvariable=self.cookies_status_var, fg=self.theme["muted"], justify="left", wraplength=380, bg=self.theme["panel"])
        self.cookies_status_label.pack(anchor="w", fill="x", padx=2, pady=(4,0))

        def _sync_cookie_wrap(_event=None):
            try:
                width = max(240, cookies_lf.winfo_width() - 24)
                self.cookies_help_label.configure(wraplength=width)
                self.cookies_status_label.configure(wraplength=width)
            except Exception:
                pass

        cookies_lf.bind("<Configure>", _sync_cookie_wrap)
        self.root.after(0, _sync_cookie_wrap)

        long_simple_lf = make_panel(scrollable_frame, "Mode Video Ringkasan"); long_simple_lf.pack(fill="x", pady=(0,10), padx=10)
        Checkbutton(long_simple_lf, text="Aktifkan Mode Video Ringkasan", variable=self.is_long_simple_mode_active, command=self.toggle_long_simple_options).pack(anchor="w")
        self.long_simple_options_frame = Frame(long_simple_lf, padx=15, bg=self.theme["panel"]); self.long_simple_options_frame.pack(fill="x")
        Radiobutton(self.long_simple_options_frame, text="Ringkasan Cerdas AI", variable=self.long_simple_sub_mode, value="AI_SUMMARY", command=self.toggle_long_simple_options).pack(anchor="w")
        self.summary_detail_frame = Frame(self.long_simple_options_frame, padx=20, bg=self.theme["panel"]); self.summary_detail_frame.pack(fill="x")
        Label(self.summary_detail_frame, text="Gaya Ringkasan:").pack(anchor="w", pady=(2,0))
        Radiobutton(self.summary_detail_frame, text="Cepat & Viral (~3-5 klip)", variable=self.summary_detail_level, value="CEPAT").pack(anchor="w")
        Radiobutton(self.summary_detail_frame, text="Informatif & Sedang (~5-7 klip)", variable=self.summary_detail_level, value="SEDANG").pack(anchor="w")
        Radiobutton(self.summary_detail_frame, text="Detail & Mendalam (~8-10 klip)", variable=self.summary_detail_level, value="DETAIL").pack(anchor="w")
        self.long_simple_source_cb = Checkbutton(self.long_simple_options_frame, text="Tambahkan Teks Sumber Video", variable=self.long_simple_add_source); self.long_simple_source_cb.pack(anchor="w", padx=20, pady=(5,0))
        Radiobutton(self.long_simple_options_frame, text="Potong Video per 1 Menit (Tanpa AI)", variable=self.long_simple_sub_mode, value="CUT_1_MIN", command=self.toggle_long_simple_options).pack(anchor="w", pady=(5,0))
        Radiobutton(self.long_simple_options_frame, text="Potong Video per 2 Menit (Tanpa AI)", variable=self.long_simple_sub_mode, value="CUT_2_MIN", command=self.toggle_long_simple_options).pack(anchor="w")
        Radiobutton(self.long_simple_options_frame, text="Potong Video per 3 Menit (Tanpa AI)", variable=self.long_simple_sub_mode, value="CUT_3_MIN", command=self.toggle_long_simple_options).pack(anchor="w")

        scraper_lf = make_panel(scrollable_frame, "Mode Scraper Shorts"); scraper_lf.pack(fill="x", pady=(0,10), padx=10)
        Checkbutton(scraper_lf, text="Aktifkan Mode Scraper Shorts", variable=self.is_shorts_scraper_mode).pack(anchor="w")
        Checkbutton(scraper_lf, text="Gunakan AI untuk Judul Baru (lebih lambat)", variable=self.use_ai_for_shorts_title).pack(anchor="w")
        Label(scraper_lf, text="URL Channel YouTube:").pack(anchor="w", pady=(5,0))
        Entry(scraper_lf, textvariable=self.scrape_channel_url).pack(fill="x")
        count_frame = Frame(scraper_lf, bg=self.theme["panel"]); count_frame.pack(fill="x", pady=(5,0))
        Label(count_frame, text="Jumlah Shorts:").pack(side="left"); Entry(count_frame, textvariable=self.scrape_count, width=5).pack(side="left", padx=5)
        self.scrape_button = Button(count_frame, text="Cari & Tempel Link", command=self.start_scraping_thread); self.scrape_button.pack(side="left", expand=True, fill="x", padx=(6,0))

        overlay_short_lf = LabelFrame(scraper_lf, text="Timpa Video Short", padx=5, pady=5, bg=self.theme["panel"], fg=self.theme["fg"], bd=0, highlightthickness=1, highlightbackground=self.theme["panel_border"])
        overlay_short_lf.pack(fill="x", padx=5, pady=(10, 5)); self.glow_panels.append(overlay_short_lf)
        Checkbutton(overlay_short_lf, text="Aktifkan Timpa Video Short", variable=self.overlay_short_var).pack(anchor="w")

        manual_crop_frame = Frame(overlay_short_lf, bg=self.theme["panel"])
        manual_crop_frame.pack(fill='x', padx=5, pady=(5,0))
        self.manual_crop_cb = Checkbutton(manual_crop_frame, text="Atur Crop Manual (Opsional)", variable=self.use_manual_crop, command=self.toggle_manual_crop_button)
        self.manual_crop_cb.pack(anchor="w")
        crop_btn_frame = Frame(manual_crop_frame, padx=20, bg=self.theme["panel"])
        crop_btn_frame.pack(fill='x')
        self.manual_crop_button = Button(crop_btn_frame, text="Atur Crop Manual...", command=self.open_crop_window, state="disabled")
        self.manual_crop_button.pack(side="left", pady=(0, 5))
        Label(crop_btn_frame, textvariable=self.manual_crop_status, fg=self.theme["link"]).pack(side="left", padx=10)

        Button(overlay_short_lf, text="📼 Pilih Video Latar Untuk Shorts", command=self.select_short_background).pack(fill="x", pady=(0, 2))
        Label(overlay_short_lf, textvariable=self.short_background_file, fg=self.theme["link"], wraplength=350).pack(anchor="w", padx=2)

        cut_mode_lf = make_panel(scrollable_frame, "Mode Pemotongan Video (Long-to-Short)")
        cut_mode_lf.pack(fill="x", pady=(0,10), padx=10)
        Radiobutton(cut_mode_lf, text="Otomatis (AI)", variable=self.cut_mode, value="otomatis", command=self.toggle_manual_cut_fields).pack(anchor="w")
        Radiobutton(cut_mode_lf, text="Manual (Custom Cut)", variable=self.cut_mode, value="manual", command=self.toggle_manual_cut_fields).pack(anchor="w")
        self.manual_fields_frame = Frame(cut_mode_lf, padx=15, bg=self.theme["panel"]); self.manual_fields_frame.pack(fill="x")
        Label(self.manual_fields_frame, text="Waktu Mulai (HH:MM:SS):").pack(anchor="w", pady=(5,0)); self.start_entry = Entry(self.manual_fields_frame, textvariable=self.manual_start_time); self.start_entry.pack(fill="x")
        Label(self.manual_fields_frame, text="Waktu Selesai (HH:MM:SS):").pack(anchor="w", pady=(5,0)); self.end_entry = Entry(self.manual_fields_frame, textvariable=self.manual_end_time); self.end_entry.pack(fill="x")
        Checkbutton(cut_mode_lf, text="Tambahkan Teks Sumber Video", variable=self.long_to_short_add_source).pack(anchor="w", pady=(5,0))

        file_lf = make_panel(scrollable_frame, "File & Aset"); file_lf.pack(fill="x", pady=(0,10), padx=10)
        Button(file_lf, text="📁 Pilih Folder Output", command=self.select_output_folder).pack(fill="x")
        Label(file_lf, textvariable=self.output_folder, fg=self.theme["link"], wraplength=350).pack(anchor="w", padx=2, pady=(0,5))
        Button(file_lf, text="🖼️ Pilih Watermark (Opsional)", command=self.select_watermark).pack(fill="x")
        Label(file_lf, textvariable=self.watermark_file, fg=self.theme["link"], wraplength=350).pack(anchor="w", padx=2)
        pos_frame = Frame(file_lf, bg=self.theme["panel"])
        pos_frame.pack(fill="x", pady=(2, 5))
        Label(pos_frame, text="Posisi:").pack(side="left")
        watermark_menu = OptionMenu(pos_frame, self.watermark_position, *["Kanan Atas", "Kiri Atas", "Kanan Bawah", "Kiri Bawah", "Tengah", "Posisi Acak"])
        style_option_menu(watermark_menu)
        watermark_menu.pack(side="left", fill="x", expand=True)
        Button(file_lf, text="🖼️ Pilih Gambar Thumbnail Kustom", command=self.select_thumbnail).pack(fill="x")
        Label(file_lf, textvariable=self.thumbnail_file, fg=self.theme["link"], wraplength=350).pack(anchor="w", padx=2, pady=(0,5))
        Checkbutton(file_lf, text="Gunakan Thumbnail Kustom (untuk mode non-AI)", variable=self.use_custom_thumbnail).pack(anchor="w")

        audio_lf = make_panel(scrollable_frame, "Pengaturan Audio"); audio_lf.pack(fill="x", pady=(0,10), padx=10)
        Checkbutton(audio_lf, text="Hapus Suara Asli Video", variable=self.remove_original_audio_var, command=self.toggle_original_audio_slider).pack(anchor="w")
        original_volume_frame = Frame(audio_lf, bg=self.theme["panel"]); original_volume_frame.pack(fill="x", pady=2, padx=5); Label(original_volume_frame, text="Volume Asli:").pack(side="left")
        self.original_audio_slider = Scale(original_volume_frame, from_=0, to=100, orient="horizontal", variable=self.original_audio_volume_var, command=self.update_original_volume_label); self.original_audio_slider.pack(side="left", expand=True, fill="x", padx=5)
        Label(original_volume_frame, textvariable=self.original_volume_display_var, width=4).pack(side="left")
        Button(audio_lf, text="🎵 Pilih Musik Latar", command=self.select_music).pack(fill="x", pady=(5,0))
        Label(audio_lf, textvariable=self.music_file, fg=self.theme["link"], wraplength=350).pack(anchor="w", padx=2)
        music_volume_frame = Frame(audio_lf, bg=self.theme["panel"]); music_volume_frame.pack(fill="x", pady=2, padx=5); Label(music_volume_frame, text="Volume Musik:").pack(side="left")
        self.music_slider = Scale(music_volume_frame, from_=0, to=100, orient="horizontal", variable=self.music_volume_var, command=self.update_music_volume_label, state="disabled"); self.music_slider.pack(side="left", expand=True, fill="x", padx=5)
        Label(music_volume_frame, textvariable=self.volume_display_var, width=4).pack(side="left")

        subtitle_font_lf = make_panel(scrollable_frame, "Pengaturan Subtitle (Opsional)")
        subtitle_font_lf.pack(fill="x", pady=(0,10), padx=10)
        Checkbutton(subtitle_font_lf, text="Tambahkan Subtitle ke Video (Burn-in)", variable=self.burn_subtitles).pack(anchor="w")
        font_selection_frame = Frame(subtitle_font_lf, bg=self.theme["panel"])
        font_selection_frame.pack(fill="x", pady=(5,0), padx=15)
        Label(font_selection_frame, text="Pilih Font:").pack(side="left", padx=(0,10))
        subtitle_font_menu = OptionMenu(font_selection_frame, self.subtitle_font_selection, *self.font_map.keys())
        style_option_menu(subtitle_font_menu)
        subtitle_font_menu.pack(side="left", fill="x", expand=True)

        ai_lf = make_panel(right_column, "Pengaturan AI & Transkripsi"); ai_lf.pack(fill="x", pady=(0, 10))
        ai_frame = Frame(ai_lf, bg=self.theme["panel"]); ai_frame.pack(fill='x', pady=2)
        Label(ai_frame, text="Akurasi Transkripsi:").pack(side="left", padx=(0,10))
        whisper_menu = OptionMenu(ai_frame, self.whisper_model_selection, *["base", "small", "medium"])
        style_option_menu(whisper_menu)
        whisper_menu.pack(side="left")

        effects_lf = make_panel(right_column, "Efek Video (Berlaku untuk semua mode)"); effects_lf.pack(fill="x", pady=(0,10))
        Checkbutton(effects_lf, text="Mirror (Cermin Horizontal)", variable=self.effects_vars['mirror']).pack(anchor="w"); Checkbutton(effects_lf, text="Grayscale (Hitam Putih)", variable=self.effects_vars['grayscale']).pack(anchor="w")
        Checkbutton(effects_lf, text="Sepia", variable=self.effects_vars['sepia']).pack(anchor="w"); Checkbutton(effects_lf, text="Negate (Warna Negatif)", variable=self.effects_vars['negate']).pack(anchor="w")
        Checkbutton(effects_lf, text="Color Boost (Saturasi Tinggi)", variable=self.effects_vars['color_boost']).pack(anchor="w")

        zoom_frame = Frame(effects_lf, bg=self.theme["panel"])
        zoom_frame.pack(fill="x", pady=2)
        Checkbutton(zoom_frame, text="Zoom Statis", variable=self.effects_vars['static_zoom'], command=self.toggle_zoom_slider).pack(side="left")
        self.zoom_slider = Scale(zoom_frame, from_=1.0, to=2.0, orient="horizontal", variable=self.zoom_level_var, command=self.update_zoom_label, resolution=0.05, state="disabled", length=150)
        self.zoom_slider.pack(side="left", expand=True, fill="x", padx=5)
        Label(zoom_frame, textvariable=self.zoom_display_var, width=5).pack(side="left")

        url_lf = make_panel(right_column, "Masukkan Link Video (satu per baris)"); url_lf.pack(fill="x")
        self.url_text = Text(url_lf, relief="solid", borderwidth=1, font=("Courier", 10), height=5,
                             bg=self.theme["input_bg"], fg=self.theme["fg"], insertbackground=self.theme["accent"],
                             highlightthickness=1, highlightbackground=self.theme["panel_border"], highlightcolor=self.theme["accent"])
        self.url_text.pack(fill="both", expand=True, pady=2)
        self.pulse_targets.append(self.url_text)

        action_lf = make_panel(right_column, "Kontrol & Log Proses"); action_lf.pack(fill="both", expand=True, pady=(10,0))
        control_frame = Frame(action_lf, bg=self.theme["panel"]); control_frame.pack(fill="x")
        self.start_button = Button(control_frame, text="Mulai Proses Video", command=self.start_processing_thread, bg=self.theme["accent"], fg=self.theme["bg"], font=("Helvetica", 12, "bold"), relief="flat", activebackground=self.theme["accent"], activeforeground=self.theme["bg"])
        self.start_button.pack(side="left", fill="x", expand=True, ipady=8)
        self.stop_button = Button(control_frame, text="Stop Proses", command=self.stop_processing, bg=self.theme["danger"], fg="white", font=("Helvetica", 12, "bold"), relief="flat", activebackground="#dc2626", activeforeground="white")
        self.clear_log_button = Button(action_lf, text="Bersihkan Log (Hanya di GUI)", command=self.clear_log, font=("Helvetica", 8)); self.clear_log_button.pack(fill="x", pady=4)
        Checkbutton(action_lf, text="Buka folder hasil otomatis saat selesai", variable=self.auto_open_output_var).pack(anchor="w", padx=2, pady=(0,4))
        self.progress_bar = Progressbar(action_lf, orient="horizontal", length=100, mode="determinate", style="Accent.Horizontal.TProgressbar"); self.progress_bar.pack(fill="x", pady=8)
        log_frame = Frame(action_lf, bg=self.theme["panel"]); log_frame.pack(fill="both", expand=True)
        self.log_text = Text(log_frame, state='disabled', wrap='word', relief="solid", borderwidth=1,
                             bg=self.theme["input_bg"], fg=self.theme["fg"], insertbackground=self.theme["accent"],
                             highlightthickness=1, highlightbackground=self.theme["panel_border"], highlightcolor=self.theme["accent"])
        self.pulse_targets.append(self.log_text)
        scrollbar = Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=scrollbar.set); scrollbar.pack(side="right", fill="y"); self.log_text.pack(side="left", fill="both", expand=True)

        self.toggle_manual_cut_fields(); self.toggle_original_audio_slider(); self.toggle_long_simple_options(); self.toggle_zoom_slider()

    def start_ui_animations(self):
        self._animate_accent_bar()
        self._animate_glow()

    def _animate_accent_bar(self):
        if not hasattr(self, "accent_canvas"): return
        base = self.accent_palette[self.accent_index]
        target = self.accent_palette[(self.accent_index + 1) % len(self.accent_palette)]
        self.accent_mix += 0.02
        if self.accent_mix >= 1.0:
            self.accent_mix = 0.0
            self.accent_index = (self.accent_index + 1) % len(self.accent_palette)
        blended = blend_colors(base, target, self.accent_mix)
        self.accent_canvas.configure(bg=blended)
        if hasattr(self, "start_button"):
            self.start_button.configure(bg=blended, activebackground=blended)
        try:
            self.progress_style.configure("Accent.Horizontal.TProgressbar", background=blended, lightcolor=blended, darkcolor=blended)
        except Exception:
            pass
        self.root.after(90, self._animate_accent_bar)

    def _animate_glow(self):
        wave = (math.sin(self.glow_phase / 8) + 1) / 2
        glow_color = blend_colors(self.theme["panel_border"], self.theme["accent"], wave)
        for panel in self.glow_panels:
            try:
                panel.configure(highlightbackground=glow_color, highlightcolor=glow_color)
            except Exception:
                continue
        for widget in self.pulse_targets:
            try:
                widget.configure(highlightbackground=glow_color, highlightcolor=glow_color)
            except Exception:
                continue
        self.glow_phase += 1
        self.root.after(140, self._animate_glow)

    def toggle_manual_crop_button(self):
        state = "normal" if self.use_manual_crop.get() else "disabled"
        self.manual_crop_button.config(state=state)
        if not self.use_manual_crop.get():
            self.manual_crop_status.set("Status: Belum diatur.")
            self.manual_crop_coords = None

    def open_crop_window(self):
        urls = [url for url in self.url_text.get("1.0", "end-1c").strip().splitlines() if url.strip()]
        if not urls:
            self.popup_error("Error", "Masukkan setidaknya satu URL video di kotak teks sebelum mengatur crop.")
            return

        video_url = urls[0]
        logging.info(f"🖼️ Mengambil thumbnail dari: {video_url}")

        video_id_match = re.search(r"(?:v=|\/shorts\/|youtu\.be\/|embed\/)([^#\&\?]{11})", video_url)
        if not video_id_match:
            self.popup_error("Error", "URL YouTube pertama tidak valid atau tidak dapat menemukan Video ID.")
            return

        video_id = video_id_match.group(1)
        thumbnail_url = f"https://img.youtube.com/vi/{video_id}/sddefault.jpg"
        temp_thumb_path = "_temp_crop_thumb.jpg"

        try:
            response = requests.get(thumbnail_url, stream=True)
            response.raise_for_status()
            with open(temp_thumb_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            logging.info("   ✅ Thumbnail berhasil diunduh.")

            CropWindow(self.root, self, image_path=temp_thumb_path)

        except Exception as e:
            logging.error(f"   ❌ Gagal mengambil thumbnail: {e}")
            self.popup_error("Error", f"Gagal mengambil thumbnail dari YouTube.\n\nDetail: {e}")
            CropWindow(self.root, self)
        finally:
            self.root.after(1000, self.cleanup_temp_thumb)

    def cleanup_temp_thumb(self):
        temp_thumb_path = "_temp_crop_thumb.jpg"
        if os.path.exists(temp_thumb_path):
            try:
                os.remove(temp_thumb_path)
                logging.info("   🗑️ File thumbnail sementara telah dihapus.")
            except OSError as e:
                logging.warning(f"   ⚠️ Gagal menghapus thumbnail sementara: {e}")

    def toggle_original_audio_slider(self): self.original_audio_slider.config(state="disabled" if self.remove_original_audio_var.get() else "normal")
    def select_thumbnail(self):
        file = filedialog.askopenfilename(title="Pilih Gambar Thumbnail", filetypes=[("Image Files", "*.png;*.jpg;*.jpeg")])
        if file: self.thumbnail_full_path = file; self.thumbnail_file.set(f"Thumbnail: {os.path.basename(file)}")

    def select_short_background(self):
        file = filedialog.askopenfilename(title="Pilih Video Latar", filetypes=[("Video Files", "*.mp4;*.mov")])
        if file: self.short_background_full_path = file; self.short_background_file.set(f"Video Latar: {os.path.basename(file)}")

    def refresh_cookie_status(self):
        cookie_path = get_cookie_file_path()
        active = get_active_cookie_profile_name()
        profiles = list_cookie_profiles()
        active_text = active if active else "(belum ada)"
        total_text = str(len(profiles))
        if os.path.exists(cookie_path) and cookie_file_has_any_live_cookie(cookie_path):
            status = f"Aktif ({COOKIE_FILE})"
        elif os.path.exists(cookie_path):
            status = f"Ada, kemungkinan kedaluwarsa ({COOKIE_FILE})"
        else:
            status = "Belum ada (opsional)"
        self.cookies_status_var.set(f"Akun aktif : {active_text}\nTersimpan  : {total_text} akun\nStatus     : {status}")

    def show_cookie_accounts(self):
        if self.cookie_accounts_window and self.cookie_accounts_window.winfo_exists():
            self.cookie_accounts_window.lift()
            return

        win = Toplevel(self.root)
        win.title("Kelola Akun Cookies")
        win.geometry("520x420")
        win.configure(bg=self.theme["bg"])
        self.cookie_accounts_window = win
        self._try_set_window_icon(win)

        info = Label(
            win,
            text=f"Lokasi penyimpanan: {get_cookies_store_dir()}\nCatatan: Anda dapat memberi label akun saat Auto Setup (mis. email Gmail).",
            bg=self.theme["bg"],
            fg=self.theme["muted"],
            justify="left",
            wraplength=500,
        )
        info.pack(fill="x", padx=10, pady=(10, 6))

        list_frame = Frame(win, bg=self.theme["bg"])
        list_frame.pack(fill="both", expand=True, padx=10)

        lb = Listbox(list_frame, height=12, bg=self.theme["input_bg"], fg=self.theme["fg"], selectbackground=self.theme["accent"])
        sb = Scrollbar(list_frame, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def refresh_list(select_active=True):
            lb.delete(0, "end")
            profiles = list_cookie_profiles()
            active = get_active_cookie_profile_name()
            active_idx = None
            for idx, name in enumerate(profiles):
                label = f"{name}{'  ✅' if active and name == active else ''}"
                lb.insert("end", label)
                if active and name == active:
                    active_idx = idx
            if select_active and active_idx is not None:
                lb.selection_clear(0, "end")
                lb.selection_set(active_idx)
                lb.see(active_idx)

        def selected_profile_filename():
            sel = lb.curselection()
            if not sel:
                return None
            text = lb.get(sel[0])
            return text.replace("  ✅", "").strip()

        btns = Frame(win, bg=self.theme["bg"])
        btns.pack(fill="x", padx=10, pady=10)

        def on_set_active():
            name = selected_profile_filename()
            if not name:
                self.popup_info("Cookies", "Pilih salah satu akun cookies terlebih dahulu.")
                return
            set_active_cookie_profile(name, logger_func=logging.info)
            self.refresh_cookie_status()
            refresh_list()

        def on_delete():
            name = selected_profile_filename()
            if not name:
                self.popup_info("Cookies", "Pilih salah satu akun cookies terlebih dahulu.")
                return
            if not self.popup_confirm("Hapus Akun", f"Yakin ingin menghapus akun cookies ini?\n\n{name}", yes_text="Hapus", no_text="Batal"):
                return
            delete_cookie_profile(name, logger_func=logging.warning)
            self.refresh_cookie_status()
            refresh_list(select_active=False)

        Button(btns, text="✅ Jadikan Aktif", command=on_set_active).pack(side="left", expand=True, fill="x")
        Button(btns, text="🗑️ Hapus Akun", command=on_delete).pack(side="left", expand=True, fill="x", padx=(6, 0))
        Button(btns, text="Tutup", command=win.destroy).pack(side="left", expand=True, fill="x", padx=(6, 0))

        refresh_list(select_active=True)

    def delete_active_cookie_profile(self):
        active = get_active_cookie_profile_name()
        if not active:
            self.popup_info("Cookies", "Belum ada cookies aktif.")
            return
        if not self.popup_confirm("Hapus Cookies Aktif", f"Yakin ingin menghapus akun cookies aktif?\n\n{active}", yes_text="Hapus", no_text="Batal"):
            return
        delete_cookie_profile(active, logger_func=logging.warning)
        self.refresh_cookie_status()

    def start_auto_setup_cookies(self):
        if self.cookies_auto_thread and self.cookies_auto_thread.is_alive():
            self.popup_info("Cookies", "Auto Setup cookies sedang berjalan.")
            return

        label_hint = simpledialog.askstring(
            "Nama Akun Cookies",
            "Masukkan Gmail / nama akun untuk cookies ini (opsional).\nContoh: email_kamu@gmail.com",
        )
        label_hint = sanitize_cookie_profile_label(label_hint or "")

        def ensure_selenium():
            try:
                import selenium  # noqa: F401
                return True
            except Exception:
                if not self.popup_confirm(
                    "Install Selenium",
                    "Fitur Auto Setup butuh modul 'selenium'.\n\nInstall otomatis sekarang? (butuh internet)",
                    yes_text="Install",
                    no_text="Batal",
                ):
                    return False
                try:
                    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "selenium"])
                    return True
                except Exception as e:
                    self.popup_error("Gagal Install Selenium", f"Gagal install selenium otomatis.\n\nDetail: {e}")
                    return False

        if not ensure_selenium():
            return

        def worker():
            try:
                from selenium import webdriver
                from selenium.webdriver.chrome.options import Options

                cookie_path = get_cookie_file_path()
                if os.path.exists(cookie_path) and not cookie_file_has_any_live_cookie(cookie_path):
                    maybe_delete_cookie_file(cookie_path, logger_func=logging.warning, reason="expired")

                chrome_path = find_chrome_executable()
                profile_dir = tempfile.mkdtemp(prefix="ytclipper_chrome_")

                opts = Options()
                if chrome_path:
                    opts.binary_location = chrome_path
                opts.add_argument(f"--user-data-dir={profile_dir}")
                opts.add_argument("--no-first-run")
                opts.add_argument("--no-default-browser-check")
                opts.add_argument("--disable-features=Translate,ChromeWhatsNewUI")
                opts.add_argument("--disable-sync")

                self.root.after(0, lambda: self.cookies_status_var.set("Cookies: SILAKAN LOGIN di Chrome (browser bersih) ..."))

                driver = webdriver.Chrome(options=opts)
                try:
                    driver.get("https://www.youtube.com/")
                    start = time.time()
                    timeout_s = 300
                    while (time.time() - start) < timeout_s:
                        cookies = driver.get_cookies()
                        if _selenium_cookies_have_youtube_auth(cookies):
                            _write_netscape_cookies_txt(cookie_path, cookies)
                            suffix = int(time.time())
                            base = label_hint or f"auto_{suffix}"
                            profile_name = f"{base}_{suffix}.txt" if label_hint else f"auto_{suffix}.txt"
                            save_cookie_profile_from_file(cookie_path, profile_name, set_active=True, logger_func=logging.info)
                            self.root.after(0, self.refresh_cookie_status)
                            return
                        time.sleep(2)

                    self.root.after(0, self.refresh_cookie_status)
                    self.root.after(0, lambda: self.popup_warning("Cookies", "Timeout. Pastikan login berhasil lalu coba Auto Setup lagi."))
                finally:
                    try:
                        driver.quit()
                    except Exception:
                        pass
                    try:
                        shutil.rmtree(profile_dir, ignore_errors=True)
                    except Exception:
                        pass
            except Exception as e:
                self.root.after(0, self.refresh_cookie_status)
                self.root.after(0, lambda: self.popup_error("Auto Setup Cookies Gagal", f"Gagal Auto Setup cookies.\n\nDetail: {e}"))

        self.cookies_auto_thread = threading.Thread(target=worker, daemon=True)
        self.cookies_auto_thread.start()

    def clear_log(self): self.log_text.config(state='normal'); self.log_text.delete('1.0', 'end'); self.log_text.config(state='disabled')
    def stop_processing(self): logging.warning("\n🛑 PERINTAH STOP DITERIMA! Menghentikan proses..."); self.stop_event.set(); self.stop_button.pack_forget()
    def start_scraping_thread(self):
        if not self.scrape_channel_url.get(): self.popup_error("Error", "Masukkan URL Channel YouTube."); return
        self.scrape_button.config(state="disabled", text="Mencari..."); threading.Thread(target=self.scrape_shorts_from_channel, daemon=True).start()
    def scrape_shorts_from_channel(self):
        channel_url = self.scrape_channel_url.get(); count = self.scrape_count.get()
        logging.info(f"\n🔎 Mulai mencari {count} shorts dari channel: {channel_url}")

        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'remote_components': ['ejs:github'],
            'playlistend': count
        }

        js_runtimes = get_yt_dlp_js_runtimes(logging.info)
        if js_runtimes:
            ydl_opts['js_runtimes'] = js_runtimes

        cookie_path = get_cookie_file_path()
        if os.path.exists(cookie_path) and not cookie_file_has_any_live_cookie(cookie_path):
            maybe_delete_cookie_file(cookie_path, logger_func=logging.warning, reason="expired")

        if os.path.exists(cookie_path):
            logging.info(f"   🍪 Menggunakan '{COOKIE_FILE}' untuk scraping.")
            ydl_opts['cookiefile'] = cookie_path
        else:
            logging.warning(f"   ⚠️ File '{COOKIE_FILE}' tidak ditemukan. Scraping tanpa autentikasi.")

        found_urls = []
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                shorts_url = normalize_youtube_channel_shorts_url(channel_url)
                result = ydl.extract_info(shorts_url, download=False)
                if 'entries' in result:
                    logging.info(f"   Menganalisis {len(result['entries'])} video terbaru...")
                    for entry in result['entries']:
                        if len(found_urls) >= count: break
                        if not entry:
                            continue

                        url = entry.get('url') or entry.get('webpage_url')
                        if isinstance(url, str) and url:
                            normalized = url if url.startswith(("http://", "https://")) else f"https://www.youtube.com/{url.lstrip('/')}"
                            if ("watch?v=" in normalized) or ("/shorts/" in normalized):
                                if normalized not in found_urls:
                                    found_urls.append(normalized)
                                    logging.info(f"   ✅ Ditemukan Short: {entry.get('title', normalized)}")
                            continue

                        video_id = entry.get('id')
                        if isinstance(video_id, str) and re.fullmatch(r"[0-9A-Za-z_-]{11}", video_id):
                            video_url = f"https://www.youtube.com/watch?v={video_id}"
                            if video_url not in found_urls:
                                found_urls.append(video_url)
                                logging.info(f"   ✅ Ditemukan Short: {entry.get('title', video_id)}")
                else: logging.warning("   ❌ Tidak ada video ditemukan. Pastikan URL channel benar.")

            def update_ui():
                self.url_text.delete("1.0", "end"); self.url_text.insert("1.0", "\n".join(found_urls))
                logging.info(f"✅ Berhasil menempelkan {len(found_urls)} link video Shorts.")
                self.scrape_button.config(state="normal", text="Cari & Tempel Link")
            self.root.after(0, update_ui)
        except Exception as e:
            logging.error(f"❌ Gagal scraping: {e}");
            logging.error("   Pastikan URL channel benar dan coba gunakan cookie jika channel bersifat privat.")
            self.root.after(0, lambda: self.scrape_button.config(state="normal", text="Cari & Tempel Link"))

    def toggle_manual_cut_fields(self):
        self.start_entry.config(state="normal" if self.cut_mode.get() == "manual" else "disabled")
        self.end_entry.config(state="normal" if self.cut_mode.get() == "manual" else "disabled")
    def update_music_volume_label(self, val): self.volume_display_var.set(f"{int(float(val))}%")
    def update_original_volume_label(self, val): self.original_volume_display_var.set(f"{int(float(val))}%")
    def get_and_copy_uuid(self):
        try:
            device_id = get_device_id()
            self.device_id_var.set(device_id)
            self.root.clipboard_clear(); self.root.clipboard_append(device_id)
            self.popup_info("ID Disalin", "ID Perangkat Anda telah disalin ke clipboard.")
        except Exception as e: self.popup_error("Error", f"Gagal mendapatkan ID Perangkat.\n\nDetail: {e}")
    def _initial_license_check(self): threading.Thread(target=lambda:self.license_queue.put(verify_license(logging.info)),daemon=True).start()
    def process_license_queue(self):
        try:
            is_valid, device_id = self.license_queue.get_nowait()
            if device_id: self.device_id_var.set(device_id)
            self.license_status_label.config(text="TERVALIDASI" if is_valid else "TIDAK VALID", fg=self.theme["success"] if is_valid else self.theme["danger"])
        except queue.Empty: self.root.after(200, self.process_license_queue)
    def select_output_folder(self):
        folder = filedialog.askdirectory(title="Pilih Folder Output")
        if folder: self.output_folder.set(f"Folder Output: {folder}")
    def select_watermark(self):
        file = filedialog.askopenfilename(title="Pilih File Watermark", filetypes=[("Image Files", "*.png;*.jpg;*.jpeg")])
        if file: self.watermark_full_path = file; self.watermark_file.set(f"Watermark: {os.path.basename(file)}")
    def select_music(self):
        file = filedialog.askopenfilename(title="Pilih File Musik", filetypes=[("Audio Files", "*.mp3;*.wav;*.m4a")])
        if file: self.music_full_path = file; self.music_file.set(f"Musik: {os.path.basename(file)}"); self.music_slider.config(state="normal")
    
    def process_log_queue(self):
        try:
            while True:
                message = self.log_queue.get_nowait()
                self.log_text.config(state='normal'); self.log_text.insert('end', message + '\n'); self.log_text.see('end'); self.log_text.config(state='disabled')
        except queue.Empty:
            self.root.after(100, self.process_log_queue)
            
    def start_processing_thread(self):
        if not self.url_text.get("1.0", "end-1c").strip(): self.popup_error("Error", "Masukkan setidaknya satu URL video."); return
        if not self.output_folder.get(): self.popup_error("Error", "Pilih folder untuk menyimpan video."); return

        if self.overlay_short_var.get() and not self.short_background_full_path:
            self.popup_error("Error", "Anda mengaktifkan 'Timpa Video Short'. Pilih video latar terlebih dahulu."); return

        self.start_button.config(state="disabled", text="Sedang Memproses..."); self.stop_button.pack(side="left", fill="x", expand=True, ipady=8, padx=(5,0))
        self.progress_bar['value'] = 0; self.stop_event.clear()
        threading.Thread(target=self.run_processing_logic, daemon=True).start()

    def open_output_folder(self, folder_path):
        try:
            if folder_path and os.path.isdir(folder_path):
                os.startfile(folder_path)  # Windows
        except Exception as e:
            logging.warning(f"⚠️ Gagal membuka folder hasil: {e}")

    def update_progress(self, val, total, msg): 
        logging.info(f"\n🧩 [LANGKAH {val}/{total}] {msg}")
        self.progress_bar['value'] = (val/total)*100

    def is_valid_clip(self, clip, logger_func=logging.warning):
        try:
            if not re.match(r'^\d{2}:\d{2}:\d{2}(\.\d+)?$', clip['start_time']) or \
               not re.match(r'^\d{2}:\d{2}:\d{2}(\.\d+)?$', clip['end_time']):
                logger_func(f"   ⚠️ Melewati klip tidak valid dari AI: Format waktu salah. Data: {clip}")
                return False

            start_s = sum(x * float(t) for x, t in zip([3600, 60, 1], re.split(':', clip['start_time'])))
            end_s = sum(x * float(t) for x, t in zip([3600, 60, 1], re.split(':', clip['end_time'])))

            if start_s >= end_s:
                logger_func(f"   ⚠️ Melewati klip tidak valid dari AI: Waktu Selesai ({clip['end_time']}) lebih awal dari Waktu Mulai ({clip['start_time']}).")
                return False
            return True
        except (ValueError, KeyError, AttributeError, TypeError):
            logger_func(f"   ⚠️ Melewati klip tidak valid dari AI: Data tidak lengkap atau format salah. Data: {clip}")
            return False

    def run_processing_logic(self):
        is_long_simple_mode = self.is_long_simple_mode_active.get()
        is_shorts_mode = self.is_shorts_scraper_mode.get() and not is_long_simple_mode
        is_long_to_short_mode = not is_shorts_mode and not is_long_simple_mode

        try:
            self.update_progress(1, 10, "Memverifikasi Lisensi...");
            is_valid, _ = verify_license(logging.info)
            if not is_valid: logging.error("   Silakan hubungi admin Telegram : @nezastore."); return

            self.update_progress(2, 10, "Memuat Konfigurasi & Model AI...")

            ai_api_key = self.custom_api_key.get() if self.use_custom_api_key.get() else None
            ai_model = DEFAULT_AI_MODEL
            if not ai_api_key:
                config = load_effective_config(logging.info)
                if not config: return
                ai_api_key = config.get("openrouter_api_key") or config.get("api_key") or config.get("deepseek_api_key")
                ai_model = config.get("openrouter_model") or config.get("model") or config.get("deepseek_model") or DEFAULT_AI_MODEL

            self.ai_client = configure_ai_client(ai_api_key, logging.info)
            if not self.ai_client:
                logging.error("   ❌ Gagal mengkonfigurasi API OpenRouter. Proses AI dibatalkan.")
                return

            output_folder_base = self.output_folder.get().replace("Folder Output: ", "")
            output_folder = os.path.join(output_folder_base, OUTPUT_SUBFOLDER); os.makedirs(output_folder, exist_ok=True)
            self.last_output_folder = output_folder
            success_outputs = 0
            failed_outputs = 0
            video_urls = [url for url in self.url_text.get("1.0", "end-1c").strip().splitlines() if url.strip()]

            long_simple_mode_choice = self.long_simple_sub_mode.get()

            transcription_is_needed = self.burn_subtitles.get() or (is_long_to_short_mode and self.cut_mode.get() == 'otomatis') or (is_long_simple_mode and long_simple_mode_choice == "AI_SUMMARY")
            whisper_model = None
            if transcription_is_needed:
                self.update_progress(3, 10, "Memuat Model Transkripsi...")
                selected_whisper_model = self.whisper_model_selection.get()
                logging.info(f"   Memuat model AI Whisper ({selected_whisper_model})... Ini mungkin butuh waktu saat pertama kali.")
                whisper_module = load_whisper_module(logging.info)
                if not whisper_module:
                    logging.error("❌ Modul whisper tidak tersedia. Transkripsi dibatalkan.")
                    return
                try:
                    whisper_model = whisper_module.load_model(selected_whisper_model)
                except Exception as e:
                    logging.error(f"❌ GAGAL MEMUAT MODEL WHISPER: {e}")
                    raise e
                logging.info("   Model Whisper berhasil dimuat.")

            selected_font_name = self.subtitle_font_selection.get()
            font_filename_to_use = self.font_map.get(selected_font_name)

            short_bg_video_path = self.short_background_full_path if self.overlay_short_var.get() else None

            for index, video_url in enumerate(video_urls):
                if self.stop_event.is_set(): break
                logging.info(f"\n{'='*20} MEMPROSES VIDEO {index+1}/{len(video_urls)} {'='*20}")
                self.update_progress(4, 10, f"Mengunduh Video ({index+1}/{len(video_urls)})...")
                temp_video_filename = f"temp_{int(time.time())}_{index}.mp4"
                temp_download_path = os.path.join(output_folder_base, temp_video_filename)

                pre_info = None
                try:
                    pre_opts = {'quiet': True, 'nocheckcertificate': True, 'remote_components': ['ejs:github'], 'ffmpeg_location': get_app_base_path()}
                    js_runtimes = get_yt_dlp_js_runtimes(logging.info)
                    if js_runtimes:
                        pre_opts['js_runtimes'] = js_runtimes
                    with yt_dlp.YoutubeDL(pre_opts) as ydl:
                        pre_info = ydl.extract_info(video_url, download=False)
                except Exception as e:
                    logging.error(f"   ❌ Gagal mendapatkan info video: {e}. Melewati video ini.")
                    continue

                if pre_info.get('duration', 0) > 3600 and self.is_shorts_scraper_mode.get():
                     logging.warning(f"   ⚠️ Video terlalu panjang ({pre_info.get('duration_string', 'N/A')}) dan mode Scraper Short aktif. Melewati video ini.")
                     continue

                video_path, info = download_video(video_url, output_path=temp_download_path, logger_func=logging.info)
                if not video_path: continue

                if not info: info = pre_info

                video_title = info.get('title', f"video_{index}")
                safe_base_filename = sanitize_filename(video_title)
                video_duration = info.get('duration', 0)
                if video_duration == 0:
                     logging.error("   ❌ Gagal mendapatkan durasi video. Melewati video ini.")
                     if os.path.exists(video_path): os.remove(video_path)
                     continue

                transcription_result = None
                produced_any_for_video = False
                if transcription_is_needed:
                    self.update_progress(5, 10, f"Transkripsi Audio ({index+1}/{len(video_urls)})...")
                    try:
                        transcription_result = transcribe_audio(video_path, whisper_model, self.whisper_model_selection.get(), logging.info)
                    except Exception as e:
                        logging.error(f"❌ TERJADI ERROR FATAL SAAT TRANSKRIPSI: {e}")
                        transcription_result = None
                    if not transcription_result:
                        logging.error("   ❌ Gagal transkripsi, proses AI atau Subtitle untuk video ini dibatalkan.")
                        if (is_long_to_short_mode and self.cut_mode.get() == 'otomatis') or (is_long_simple_mode and long_simple_mode_choice == "AI_SUMMARY"):
                            logging.warning("   Mode AI diaktifkan tapi transkripsi gagal. Melewati video ini.")
                            if os.path.exists(video_path): os.remove(video_path)
                            continue

                if is_long_simple_mode:
                    if long_simple_mode_choice == "AI_SUMMARY":
                        if not transcription_result:
                            logging.error("   ❌ Transkripsi tidak tersedia untuk AI Summary. Melewati video ini.")
                            if os.path.exists(video_path): os.remove(video_path)
                            continue
                        self.update_progress(6, 10, f"Membuat Rencana Video Ringkasan ({index+1}/{len(video_urls)})...")

                        summary_data = get_summary_clips_from_ai(
                            transcript_text=transcription_result['text'],
                            video_duration=video_duration,
                            ai_model_name=ai_model,
                            ai_client=self.ai_client,
                            detail_level=self.summary_detail_level.get(),
                            logger_func=logging.info
                        )

                        if not summary_data:
                            if os.path.exists(video_path): os.remove(video_path)
                            continue

                        all_clips_to_process = [c for c in summary_data.get('clips', []) if c and self.is_valid_clip(c, logging.warning)]

                        if not all_clips_to_process: logging.warning("   ❌ AI tidak memberikan klip yang valid."); os.remove(video_path); continue

                        safe_filename = sanitize_filename(summary_data.get('title', f"Ringkasan_{safe_base_filename}"))
                        output_file = os.path.join(output_folder, f"{safe_filename}.mp4")
                        self.update_progress(7, 10, f"Membuat Video Ringkasan ({index+1}/{len(video_urls)})...")
                        process_long_simple_video(self=self, source_video=video_path, all_clips=all_clips_to_process, watermark_file=self.watermark_full_path, watermark_position=self.watermark_position.get(), output_filename=output_file, style='informative', music_file=self.music_full_path, music_volume=self.music_volume_var.get(), effects={k:v.get() for k,v in self.effects_vars.items()}, remove_original_audio=self.remove_original_audio_var.get(), original_audio_volume=self.original_audio_volume_var.get(), source_text=f"Sumber: {info.get('uploader', '')}" if self.long_simple_add_source.get() else "", transcription_result=transcription_result, font_filename=font_filename_to_use,
                                                     logger_func=logging.info)

                        self.update_progress(8, 10, f"Membuat & Menyematkan Thumbnail ({index+1}/{len(video_urls)})...")
                        temp_thumb_path = os.path.join(output_folder_base, TEMP_THUMBNAIL_FILE)
                        thumb_time = summary_data.get('thumbnail_time', '00:00:05')

                        if not re.match(r'^\d{2}:\d{2}:\d{2}(\.\d+)?$', thumb_time):
                            logging.warning(f"   ⚠️ Timestamp thumbnail dari AI tidak valid ({thumb_time}). Menggunakan 00:00:05.")
                            thumb_time = '00:00:05'

                        if generate_thumbnail_from_video(video_path, thumb_time, temp_thumb_path, logging.info):
                            embed_thumbnail(output_file, temp_thumb_path, logging.info)
                            if os.path.exists(temp_thumb_path): os.remove(temp_thumb_path)
                    else:
                        self.update_progress(6, 10, f"Memulai Proses Potong Otomatis ({index+1}/{len(video_urls)})...")
                        chunk_map = {"CUT_1_MIN": 60, "CUT_2_MIN": 120, "CUT_3_MIN": 180}
                        chunk_length = chunk_map.get(long_simple_mode_choice)

                        num_clips = math.ceil(video_duration / chunk_length)
                        logging.info(f"   Video akan dipotong menjadi {num_clips} bagian (durasi per bagian: {chunk_length} detik).")

                        subtitle_path = None
                        if self.burn_subtitles.get() and transcription_result:
                            subtitle_path = os.path.join(output_folder_base, f"temp_sub_{int(time.time())}.srt")
                            generate_srt_file(transcription_result, subtitle_path, logging.info)

                        for i in range(num_clips):
                            if self.stop_event.is_set(): break
                            start_time_s = i * chunk_length
                            end_time_s = min((i + 1) * chunk_length, video_duration)

                            start_time_str = time.strftime('%H:%M:%S', time.gmtime(start_time_s))
                            end_time_str = time.strftime('%H:%M:%S', time.gmtime(end_time_s))

                            output_file = os.path.join(output_folder, f"{safe_base_filename}_Part_{i+1}.mp4")
                            logging.info(f"   Memproses Bagian {i+1}/{num_clips} ({start_time_str} - {end_time_str})...")
                            process_single_clip_16x9(self=self, source_video=video_path,
                                                         start_time=start_time_s,
                                                         end_time=end_time_s,
                                                         watermark_file=self.watermark_full_path, watermark_position=self.watermark_position.get(), output_filename=output_file, music_file=self.music_full_path, music_volume=self.music_volume_var.get(), effects={k:v.get() for k,v in self.effects_vars.items()}, remove_original_audio=self.remove_original_audio_var.get(), original_audio_volume=self.original_audio_volume_var.get(), subtitle_file=subtitle_path, font_filename=font_filename_to_use,
                                                         logger_func=logging.info)

                        if subtitle_path and os.path.exists(subtitle_path): os.remove(subtitle_path)

                elif is_shorts_mode:
                    subtitle_path = None
                    if self.burn_subtitles.get() and transcription_result:
                        subtitle_path = os.path.join(output_folder_base, f"temp_sub_{int(time.time())}.srt")
                        generate_srt_file(transcription_result, subtitle_path, logging.info)

                    final_title = info.get('title', f'Short_{index+1}')
                    if self.use_ai_for_shorts_title.get():
                        new_title = get_paraphrased_title_from_ai(final_title, ai_model, self.ai_client, logging.info)
                        if new_title: final_title = new_title

                    output_file = os.path.join(output_folder, f"{sanitize_filename(final_title)}.mp4")
                    self.update_progress(7, 10, f"Memproses Ulang Short ({index+1}/{len(video_urls)})...")

                    process_clip(self=self, source_video=video_path,
                                 start_time="00:00:00",
                                 end_time=time.strftime('%H:%M:%S', time.gmtime(video_duration)),
                                 watermark_file=self.watermark_full_path, watermark_position=self.watermark_position.get(), source_text="", output_filename=output_file, style='informative', music_file=self.music_full_path, music_volume=self.music_volume_var.get(), effects={k:v.get() for k,v in self.effects_vars.items()}, remove_original_audio=self.remove_original_audio_var.get(), original_audio_volume=self.original_audio_volume_var.get(), is_short_mode=True, subtitle_file=subtitle_path, font_filename=font_filename_to_use,
                                 shorts_background_video=short_bg_video_path,
                                 logger_func=logging.info)

                    if subtitle_path and os.path.exists(subtitle_path): os.remove(subtitle_path)
                    if self.use_custom_thumbnail.get() and self.thumbnail_full_path: embed_thumbnail(output_file, self.thumbnail_full_path, logging.info)

                elif is_long_to_short_mode:
                    subtitle_path = None
                    if self.burn_subtitles.get() and transcription_result:
                        subtitle_path = os.path.join(output_folder_base, f"temp_sub_{int(time.time())}.srt")
                        generate_srt_file(transcription_result, subtitle_path, logging.info)

                    ai_clips = []
                    source_text_lts = f"Sumber: {info.get('uploader', '')}" if self.long_to_short_add_source.get() else ""

                    if self.cut_mode.get() == "otomatis":
                        if not transcription_result:
                            logging.error("   ❌ Transkripsi tidak tersedia untuk AI Long-to-Short. Melewati video ini.")
                            if os.path.exists(video_path): os.remove(video_path)
                            continue
                        self.update_progress(6, 10, f"Mencari Klip Viral dengan AI ({index+1}/{len(video_urls)})...")

                        all_ai_clips = get_clips_from_ai(
                            transcript_text=transcription_result['text'],
                            ai_model_name=ai_model,
                            ai_client=self.ai_client,
                            logger_func=logging.info
                        )

                        ai_clips = [c for c in all_ai_clips if self.is_valid_clip(c, logging.warning)]
                    else:
                        start_t, end_t = self.manual_start_time.get(), self.manual_end_time.get()
                        if self.is_valid_clip({'start_time': start_t, 'end_time': end_t}, logging.warning):
                            ai_clips = [{"start_time": start_t, "end_time": end_t, "title": f"Klip Manual {start_t} - {end_t}"}]
                        else:
                            logging.error(f"   ❌ Waktu klip manual tidak valid ({start_t} - {end_t}). Melewati video ini.")

                    if not ai_clips: logging.warning("   🔴 Tidak ada klip untuk diproses.");
                    else:
                        self.update_progress(7, 10, f"Membuat Klip Video ({index+1}/{len(video_urls)})...")
                        for i, clip in enumerate(ai_clips):
                            if self.stop_event.is_set(): break
                            output_file = os.path.join(output_folder, f"{sanitize_filename(clip.get('title', f'Klip {i+1}'))}.mp4")

                            logging.info(f"   Memproses Klip {i+1}/{len(ai_clips)}: {clip.get('title', 'N/A')} ({clip['start_time']} - {clip['end_time']})")

                            process_clip(self=self, source_video=video_path, start_time=clip['start_time'], end_time=clip['end_time'], watermark_file=self.watermark_full_path, watermark_position=self.watermark_position.get(), source_text=source_text_lts, output_filename=output_file, style=clip.get('editing_style', 'informative'), music_file=self.music_full_path, music_volume=self.music_volume_var.get(), effects={k:v.get() for k,v in self.effects_vars.items()}, remove_original_audio=self.remove_original_audio_var.get(), original_audio_volume=self.original_audio_volume_var.get(), subtitle_file=subtitle_path, font_filename=font_filename_to_use, is_short_mode=True,
                                         shorts_background_video=short_bg_video_path,
                                         logger_func=logging.info)
                            if self.use_custom_thumbnail.get() and self.thumbnail_full_path: embed_thumbnail(output_file, self.thumbnail_full_path, logging.info)

                            if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                                success_outputs += 1
                                produced_any_for_video = True
                            else:
                                failed_outputs += 1
                                logging.error(f"❌ Output tidak terbentuk: {output_file}")

                    if subtitle_path and os.path.exists(subtitle_path): os.remove(subtitle_path)

                if os.path.exists(video_path) and produced_any_for_video:
                    try:
                        os.remove(video_path)
                        logging.info(f"   🗑️ File video asli ({os.path.basename(video_path)}) telah dihapus.")
                    except OSError as e:
                        logging.warning(f"   ⚠️ Gagal menghapus file video asli: {e}")

            if self.stop_event.is_set():
                logging.warning("\n🛑 Proses dihentikan oleh pengguna.")
            else:
                self.update_progress(10, 10, f"SELESAI! Berhasil: {success_outputs} | Gagal: {failed_outputs}")
                logging.info(f"\n✅ SELESAI! Output berhasil: {success_outputs} | Output gagal: {failed_outputs}")
                if success_outputs > 0 and self.auto_open_output_var.get():
                    self.root.after(0, lambda: self.open_output_folder(self.last_output_folder))
        except Exception as e:
            logging.error(f"\n❌ TERJADI ERROR FATAL PADA SCRIPT ❌")
            logging.error(traceback.format_exc())
            self.root.after(
                0,
                lambda: self.popup_error(
                    "Error Fatal",
                    "Terjadi error yang tidak terduga.\nSilakan cek file 'autoclipper_log.txt' untuk detail.\n\n"
                    f"Detail: {e}",
                ),
            )
        finally:
            self.start_button.config(state="normal", text="Mulai Proses Video")
            self.stop_button.pack_forget()
            self.progress_bar['value'] = 0
            self.ai_client = None

if __name__ == "__main__":
    # Setup logging darurat jika GUI gagal
    def _startup_popup(title: str, message: str):
        try:
            r = Tk()
            r.withdraw()
            r.configure(bg="#0b1220")

            win = Toplevel(r)
            win.title(title)
            win.configure(bg="#0b1220")
            win.resizable(False, False)

            try:
                icon_path = find_app_icon_ico()
                if icon_path:
                    win.iconbitmap(icon_path)
            except Exception:
                pass

            card = Frame(win, bg="#101a2d", highlightthickness=1, highlightbackground="#223455")
            card.pack(fill="both", expand=True, padx=14, pady=14)

            header = Frame(card, bg="#101a2d")
            header.pack(fill="x", padx=14, pady=(14, 8))
            Frame(header, bg="#ef4444", width=6, height=24).pack(side="left", padx=(0, 10), pady=2)
            Label(header, text="✖", bg="#101a2d", fg="#ef4444", font=("Segoe UI", 16, "bold")).pack(side="left")
            Label(header, text=title, bg="#101a2d", fg="#e6edf7", font=("Segoe UI", 12, "bold")).pack(side="left", padx=(10, 0))

            body = Frame(card, bg="#101a2d")
            body.pack(fill="both", expand=True, padx=14, pady=(0, 10))
            Label(body, text=message, bg="#101a2d", fg="#9aa8c2", justify="left", wraplength=520).pack(fill="x", pady=(0, 8))

            footer = Frame(card, bg="#101a2d")
            footer.pack(fill="x", padx=14, pady=(0, 14))
            Button(
                footer,
                text="OK",
                command=win.destroy,
                bg="#ef4444",
                fg="#ffffff",
                activebackground="#ef4444",
                activeforeground="#ffffff",
                relief="flat",
                padx=14,
                pady=6,
            ).pack(side="right")

            win.update_idletasks()
            w = win.winfo_width() or win.winfo_reqwidth()
            h = win.winfo_height() or win.winfo_reqheight()
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            x = (sw - w) // 2
            y = (sh - h) // 2
            win.geometry(f"{w}x{h}+{x}+{y}")

            win.protocol("WM_DELETE_WINDOW", win.destroy)
            win.transient(r)
            win.grab_set()
            win.focus_force()
            r.wait_window(win)
            r.destroy()
        except Exception:
            try:
                messagebox.showerror(title, message)
            except Exception:
                pass

    try:
        root = Tk()
        app = VideoClipperApp(root)
        root.mainloop()
    except Exception as e:
        # Menulis error fatal ke file log JIKA GUI gagal
        try:
            logging.basicConfig(filename=LOG_FILE, level=logging.ERROR, format='%(asctime)s [%(levelname)-8s] %(message)s')
            logging.error("❌ GAGAL MEMULAI APLIKASI GUI ❌")
            logging.error(traceback.format_exc())
            _startup_popup("Fatal Error", f"Gagal memulai aplikasi:\n{traceback.format_exc()}\n\nCek 'autoclipper_log.txt' untuk detail.")
        except Exception as e2:
            print(f"Gagal menulis log fatal: {e2}") # Fallback ke console
            print(f"Gagal memulai aplikasi:\n{traceback.format_exc()}\nTekan Enter untuk keluar...")
            input()
