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
from logging.handlers import QueueHandler
from contextlib import redirect_stdout, redirect_stderr
from tkinter import Tk, filedialog, Button, Label, Text, Scrollbar, Frame, messagebox, StringVar, OptionMenu, Entry, Checkbutton, BooleanVar, Scale, IntVar, LabelFrame, Radiobutton, Canvas, Toplevel, DoubleVar, TclError, Misc
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
import whisper
import requests
import machineid
from PIL import Image, ImageTk
from openai import OpenAI  # Diubah dari genai ke OpenAI untuk DeepSeek

# ==============================================================================
# KONFIGURASI
# ==============================================================================
LICENSE_URL = 'https://raw.githubusercontent.com/nezastore/clipper-config/refs/heads/main/licenses.txt'
CONFIG_URL = 'https://raw.githubusercontent.com/nezastore/clipper-config/refs/heads/main/config.json'
OUTPUT_SUBFOLDER = "Hasil"
COOKIE_FILE = 'cookies.txt'
TEMP_THUMBNAIL_FILE = "_temp_thumbnail.jpg"
LOG_FILE = 'autoclipper_log.txt' # File log permanen

# ==============================================================================
# PENGATURAN LOGGING
# ==============================================================================

# 1. Stream class untuk mengarahkan stdout/stderr ke logging
class LogStream:
    def __init__(self, logger, level):
        self.logger = logger
        self.level = level
        self.linebuf = ''

    def write(self, buf):
        for line in buf.rstrip().splitlines():
            self.logger.log(self.level, line.rstrip())

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
    log_format = logging.Formatter(
        '%(asctime)s [%(levelname)-8s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
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

    logging.info("Sistem logging dimulai. Log akan disimpan di " + LOG_FILE)

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



def verify_license(logger_func=logging.info):
    logger_func("🔑 Mengecek koneksi ke server lisensi...")
    try:
        device_id = machineid.id()
    except Exception as e:
        logger_func(f"⚠️ Tidak bisa mengambil ID perangkat: {e}")
        device_id = None

    # 1) Remote GitHub raw (primary source)
    try:
        response = requests.get(LICENSE_URL, timeout=10)
        response.raise_for_status()
        authorized_ids = [line.strip() for line in response.text.strip().splitlines() if line.strip()]
        if device_id and device_id in authorized_ids:
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
                authorized_ids = [line.strip() for line in f if line.strip()]
            if device_id and device_id in authorized_ids:
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

def configure_deepseek(api_key, logger_func=logging.info):
    if not api_key:
        logger_func("❌ ERROR: API Key DeepSeek tidak ditemukan.")
        return None
    try:
        client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        logger_func("✅ Konfigurasi DeepSeek AI API berhasil.")
        return client
    except Exception as e:
        logger_func(f"❌ ERROR: Gagal mengkonfigurasi DeepSeek AI API. {e}")
        return None

def download_video(url, output_path, logger_func=logging.info):
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

            # Menggunakan print agar tidak di-log ganda oleh root logger
            print(f"   Downloading... {percent_str} | Speed: {speed_str} | ETA: {eta_str}")

    ydl_opts = {
        'format': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best',
        'outtmpl': output_path,
        'merge_output_format': 'mp4',
        'quiet': True,
        'progress_hooks': [my_progress_hook],
        'nocheckcertificate': True,
        'noplaylist': True,
        'logger': logging.getLogger('yt_dlp') # Arahkan log yt-dlp ke sistem logging
    }

    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    cookie_path = os.path.join(base_path, COOKIE_FILE)

    if os.path.exists(cookie_path):
        logger_func(f"   🍪 File '{COOKIE_FILE}' ditemukan, mencoba download dengan autentikasi.")
        ydl_opts['cookiefile'] = cookie_path
    else:
        logger_func(f"   ⚠️ File '{COOKIE_FILE}' tidak ditemukan. Melanjutkan download tanpa autentikasi.")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info_dict:
                info_dict = info
            return output_path, info_dict
    except Exception as e:
        logging.error(f"❌ ERROR saat mengunduh video: {str(e)}")
        logging.error("   Coba perbarui yt-dlp ke versi terbaru jika masalah berlanjut:")
        logging.error("   (pip install --upgrade yt-dlp)")
        return None, None

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

def get_clips_from_deepseek(transcript_text, deepseek_model_name, deepseek_client, logger_func=logging.info):
    prompt = f"""
    Anda adalah seorang editor video profesional dan ahli strategi konten viral yang terobsesi dengan "hook" (kail pancing) di 3 detik pertama. Tugas Anda adalah menganalisis transkrip video di dalam tag `<transcript>` dan mengidentifikasi momen-momen emas yang paling berpotensi FYP. ATURAN UTAMA: 1. HOOK ADALAH SEGALANYA: Setiap klip yang Anda sarankan HARUS dimulai dengan hook yang sangat kuat. Jika segmen tidak memiliki hook, JANGAN JADIKAN KLIP. 2. KUALITAS, BUKAN KUANTITAS: Fokus hanya pada momen viral. Lebih baik 2 klip sempurna daripada 7 klip biasa. 3. DURASI IDEAL: 30-60 detik. 4. OUTPUT JSON: Harus berupa format JSON valid `[ ... ]`. Setiap objek dalam array harus memiliki keys: "start_time", "end_time", "title", "hashtags", dan "editing_style". <transcript>{transcript_text}</transcript> INSTRUKSI SPESIFIK UNTUK SETIAP KLIP: 1. Cari Hook: Identifikasi pertanyaan, pernyataan kontroversial, momen emosional, atau klimaks yang kuat sebagai titik awal. 2. Tentukan Waktu (WAJIB): "start_time" harus TEPAT DI AWAL HOOK. "end_time" harus sekitar 30-60 detik setelah "start_time". Keduanya HARUS dalam format "HH:MM:SS". 3. Buat Metadata: Buat "title" yang clickbait, 3 "hashtags" yang relevan, dan tentukan "editing_style" (pilih antara 'dynamic' atau 'informative').
    """
    try:
        logger_func("   🤖 Menghubungi AI DeepSeek untuk rekomendasi klip...")
        response = deepseek_client.chat.completions.create(
            model=deepseek_model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=0.7,
            stream=False
        )
        content = response.choices[0].message.content
        logger_func("   🤖 AI DeepSeek telah merespons.")

        json_match = re.search(r'```json\s*(\[.*\])\s*```', content, re.DOTALL) or re.search(r'\[.*\]', content, re.DOTALL)
        if not json_match:
            logger_func(f"❌ ERROR: AI (klip) tidak memberikan output JSON yang valid.\n   Jawaban AI: {content}"); return []

        json_str = json_match.group(1) if len(json_match.groups()) > 0 else json_match.group(0)
        clips = json.loads(json_str)
        logger_func(f"✅ AI (klip) merekomendasikan {len(clips)} klip."); return clips
    except Exception as e:
        logging.error(f"❌ ERROR saat analisis AI (klip): {e}\n   Jawaban AI: {content if 'content' in locals() else 'Tidak ada respons'}")
        return []

def get_summary_clips_from_deepseek(transcript_text, video_duration, deepseek_model_name, deepseek_client, detail_level="SEDANG", logger_func=logging.info):
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
        logger_func("   🤖 Menghubungi AI DeepSeek untuk ringkasan video...")
        response = deepseek_client.chat.completions.create(
            model=deepseek_model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=0.7,
            response_format={ "type": "json_object" }
        )
        content = response.choices[0].message.content
        logger_func("   🤖 AI DeepSeek telah merespons.")

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

def get_paraphrased_title_from_deepseek(original_title, deepseek_model_name, deepseek_client, logger_func=logging.info):
    prompt = f"""
    Anda adalah seorang ahli branding media sosial yang jago membuat judul video viral. Tugas Anda adalah menulis ulang judul video ini: "{original_title}" agar terdengar lebih keren, menarik, dan kekinian, namun tetap menjaga makna aslinya. ATURAN: Gunakan bahasa yang santai dan memancing rasa ingin tahu. Boleh tambahkan 1-2 emoji yang relevan. Output HANYA judul barunya saja, tanpa tanda kutip atau teks tambahan apapun.
    """
    try:
        logger_func("   🤖 Menghubungi AI DeepSeek untuk judul baru...")
        response = deepseek_client.chat.completions.create(
            model=deepseek_model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=256,
            temperature=0.9
        )
        new_title = response.choices[0].message.content.strip().replace('"', '')
        logger_func("   🤖 AI DeepSeek telah memberikan judul baru.")
        return new_title if new_title else None
    except Exception as e:
        logger_func(f"   ❌ Gagal membuat judul dengan AI: {e}"); return None

def embed_thumbnail(video_path, thumb_path, logger_func=logging.info):
    if not os.path.exists(video_path):
        logger_func(f"   ⚠️ Melewati penyematan thumbnail karena file video tidak ditemukan: {os.path.basename(video_path)}")
        return
    try:
        logger_func("   📎 Menyematkan thumbnail ke video...")
        output_path = video_path.replace(".mp4", "_thumb.mp4")
        input_video = ffmpeg.input(video_path); input_thumb = ffmpeg.input(thumb_path)
        (ffmpeg.output(input_video, input_thumb, output_path, **{'c': 'copy', 'map': '0', 'map': '1', 'disposition:v:1': 'attached_pic'})
         .run(overwrite_output=True, quiet=True))
        os.remove(video_path); os.rename(output_path, video_path)
        logger_func("   ✅ Thumbnail berhasil disematkan.")
    except ffmpeg.Error as e:
        logger_func(f"   ❌ Gagal menyematkan thumbnail: Perintah ffmpeg gagal.")
        logging.error(f"   Stderr ffmpeg: {e.stderr.decode('utf-8', errors='ignore')}")
    except Exception as e:
        logging.error(f"   ❌ Gagal menyematkan thumbnail: {e}\n{traceback.format_exc()}")

def generate_thumbnail_from_video(video_path, timestamp, output_thumb_path, logger_func=logging.info):
    logger_func(f"   📸 Membuat thumbnail dari video pada {timestamp}...")
    try:
        (ffmpeg.input(video_path, ss=timestamp).output(output_thumb_path, vframes=1)
         .run(overwrite_output=True, quiet=True))
        logger_func(f"   ✅ Thumbnail berhasil dibuat: {os.path.basename(output_thumb_path)}"); return True
    except Exception as e:
        logger_func(f"   ❌ Gagal membuat thumbnail: {e}"); return False

def apply_subtitle_filter(video_stream, subtitle_file, font_filename):
    try: base_path = sys._MEIPASS
    except Exception: base_path = os.path.dirname(os.path.abspath(__file__))

    font_path = os.path.join(base_path, font_filename)
    escaped_subtitle_path = subtitle_file.replace('\\', '/').replace(':', '\\:')

    filter_kwargs = {'filename': escaped_subtitle_path}

    style_options = 'Fontsize=22,PrimaryColour=&H00FFFFFF,BorderStyle=3,Outline=1,Shadow=1'
    if os.path.exists(font_path):
        font_dir = os.path.dirname(font_path).replace('\\', '/').replace(':', '\\:')
        filter_kwargs['fontsdir'] = font_dir
        font_name = os.path.splitext(os.path.basename(font_filename))[0].replace('-', ' ')
        style_options = f'FontName={font_name},{style_options}'
    else:
        logging.warning(f"File font tidak ditemukan di: {font_path}. Menggunakan font default.")

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
            final_output = ffmpeg.output(processed_video, final_audio, output_filename, vcodec='libx264', acodec='aac', preset='fast', crf=23, shortest=None)
        else:
            final_output = ffmpeg.output(processed_video, output_filename, vcodec='libx264', preset='fast', crf=23)

        final_output.run(overwrite_output=True, quiet=True)
        logger_func("   ✅ Video akhir berhasil dirender.")

    except ffmpeg.Error as e:
        logging.error(f"❌ ERROR saat memproses klip: Perintah ffmpeg gagal.")
        logging.error(f"   Stderr ffmpeg: {e.stderr.decode('utf-8', errors='ignore')}")
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
            final_output = ffmpeg.output(processed_video, final_audio, output_filename, vcodec='libx264', acodec='aac', preset='fast', crf=23)
        else:
            final_output = ffmpeg.output(processed_video, output_filename, vcodec='libx264', preset='fast', crf=23)
        final_output.run(overwrite_output=True, quiet=True)
        logger_func(f"   ✅ Berhasil membuat: {os.path.basename(output_filename)}")
    except ffmpeg.Error as e:
        logging.error(f"❌ Gagal memproses klip {os.path.basename(output_filename)}.")
        logging.error(f"   Stderr ffmpeg: {e.stderr.decode('utf-8', errors='ignore')}")
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
        if final_audio: final_output = ffmpeg.output(processed_video, final_audio, output_filename, vcodec='libx264', acodec='aac', preset='fast', crf=23)
        else: final_output = ffmpeg.output(processed_video, output_filename, vcodec='libx264', preset='fast', crf=23)
        final_output.run(overwrite_output=True, quiet=True)
        logger_func(f"   ✅ Video ringkasan berhasil dibuat: {os.path.basename(output_filename)}")
    except ffmpeg.Error as e:
        logging.error(f"❌ ERROR saat memproses video ringkasan: Perintah ffmpeg gagal.")
        logging.error(f"   Stderr ffmpeg: {e.stderr.decode('utf-8', errors='ignore')}")
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

        # --- Setup Log Queue ---
        self.log_queue = queue.Queue()
        setup_logging(self.log_queue) # Menginisialisasi sistem logging

        # --- Variabel-variabel ---
        self.output_folder = StringVar()
        self.watermark_file = StringVar()
        self.music_file = StringVar()
        self.watermark_full_path = ""
        self.music_full_path = ""
        self.device_id_var = StringVar()
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
        self.font_map = {"Montserrat Bold": "Montserrat-Bold.ttf", "Bebas Neue": "BebasNeue-Regular.ttf", "Poppins Bold": "Poppins-Bold.ttf"}
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

        self.deepseek_client = None

        self.setup_ui()
        self.root.after(100, self.process_log_queue); self.root.after(200, self.process_license_queue); self.root.after(500, self._initial_license_check)

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
        main_frame = Frame(self.root, padx=10, pady=10); main_frame.pack(fill="both", expand=True)
        content_wrapper = Frame(main_frame); content_wrapper.pack(fill="both", expand=True)
        main_content_frame = Frame(content_wrapper); main_content_frame.pack()
        left_container = Frame(main_content_frame, width=420); left_container.pack(side="left", fill="y", padx=(0, 5)); left_container.pack_propagate(False)
        canvas = Canvas(left_container, highlightthickness=0); scrollbar = Scrollbar(left_container, orient="vertical", command=canvas.yview)
        scrollable_frame = Frame(canvas); scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        def _on_mousewheel(event): canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel); canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set); scrollbar.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
        right_column = Frame(main_content_frame); right_column.pack(side="left", fill="y")

        license_lf = LabelFrame(scrollable_frame, text="Manajemen Lisensi", font=("Helvetica", 10, "bold"), padx=10, pady=10)
        license_lf.pack(fill="x", pady=(5,10), padx=10)

        Label(license_lf, text="ID Perangkat:").grid(row=0, column=0, sticky="w")
        id_entry = Entry(license_lf, textvariable=self.device_id_var, state="readonly"); id_entry.grid(row=0, column=1, sticky="ew")
        Button(license_lf, text="Salin ID", command=self.get_and_copy_uuid).grid(row=0, column=2, padx=(10,0))
        Label(license_lf, text="Status Lisensi:").grid(row=1, column=0, sticky="w", pady=(5,0))
        self.license_status_label = Label(license_lf, text="- MENGECEK -", font=("Helvetica", 10, "bold"), fg="grey"); self.license_status_label.grid(row=1, column=1, sticky="w", pady=(5,0)); license_lf.columnconfigure(1, weight=1)

        api_lf = LabelFrame(scrollable_frame, text="Konfigurasi API Key DeepSeek", font=("Helvetica", 10, "bold"), padx=10, pady=10); api_lf.pack(fill="x", pady=(0,10), padx=10)
        Checkbutton(api_lf, text="Gunakan API Key Sendiri", variable=self.use_custom_api_key, command=lambda: self.api_key_entry.config(state="normal" if self.use_custom_api_key.get() else "disabled")).pack(anchor="w")
        Label(api_lf, text="Masukkan API Key DeepSeek Anda:").pack(anchor="w", pady=(5,0))
        self.api_key_entry = Entry(api_lf, textvariable=self.custom_api_key, state="disabled"); self.api_key_entry.pack(fill="x")

        long_simple_lf = LabelFrame(scrollable_frame, text="Mode Video Ringkasan", font=("Helvetica", 10, "bold"), padx=10, pady=10); long_simple_lf.pack(fill="x", pady=(0,10), padx=10)
        Checkbutton(long_simple_lf, text="Aktifkan Mode Video Ringkasan", variable=self.is_long_simple_mode_active, command=self.toggle_long_simple_options).pack(anchor="w")
        self.long_simple_options_frame = Frame(long_simple_lf, padx=15); self.long_simple_options_frame.pack(fill="x")
        Radiobutton(self.long_simple_options_frame, text="Ringkasan Cerdas AI", variable=self.long_simple_sub_mode, value="AI_SUMMARY", command=self.toggle_long_simple_options).pack(anchor="w")
        self.summary_detail_frame = Frame(self.long_simple_options_frame, padx=20); self.summary_detail_frame.pack(fill="x")
        Label(self.summary_detail_frame, text="Gaya Ringkasan:").pack(anchor="w", pady=(2,0))
        Radiobutton(self.summary_detail_frame, text="Cepat & Viral (±3-5 klip)", variable=self.summary_detail_level, value="CEPAT").pack(anchor="w")
        Radiobutton(self.summary_detail_frame, text="Informatif & Sedang (±5-7 klip)", variable=self.summary_detail_level, value="SEDANG").pack(anchor="w")
        Radiobutton(self.summary_detail_frame, text="Detail & Mendalam (±8-10 klip)", variable=self.summary_detail_level, value="DETAIL").pack(anchor="w")
        self.long_simple_source_cb = Checkbutton(self.long_simple_options_frame, text="Tambahkan Teks Sumber Video", variable=self.long_simple_add_source); self.long_simple_source_cb.pack(anchor="w", padx=20, pady=(5,0))
        Radiobutton(self.long_simple_options_frame, text="Potong Video per 1 Menit (Tanpa AI)", variable=self.long_simple_sub_mode, value="CUT_1_MIN", command=self.toggle_long_simple_options).pack(anchor="w", pady=(5,0))
        Radiobutton(self.long_simple_options_frame, text="Potong Video per 2 Menit (Tanpa AI)", variable=self.long_simple_sub_mode, value="CUT_2_MIN", command=self.toggle_long_simple_options).pack(anchor="w")
        Radiobutton(self.long_simple_options_frame, text="Potong Video per 3 Menit (Tanpa AI)", variable=self.long_simple_sub_mode, value="CUT_3_MIN", command=self.toggle_long_simple_options).pack(anchor="w")

        scraper_lf = LabelFrame(scrollable_frame, text="Mode Scraper Shorts", font=("Helvetica", 10, "bold"), padx=10, pady=10); scraper_lf.pack(fill="x", pady=(0,10), padx=10)
        Checkbutton(scraper_lf, text="Aktifkan Mode Scraper Shorts", variable=self.is_shorts_scraper_mode).pack(anchor="w")
        Checkbutton(scraper_lf, text="Gunakan AI untuk Judul Baru (lebih lambat)", variable=self.use_ai_for_shorts_title).pack(anchor="w")
        Label(scraper_lf, text="URL Channel YouTube:").pack(anchor="w", pady=(5,0))
        Entry(scraper_lf, textvariable=self.scrape_channel_url).pack(fill="x")
        count_frame = Frame(scraper_lf); count_frame.pack(fill="x", pady=(5,0))
        Label(count_frame, text="Jumlah Shorts:").pack(side="left"); Entry(count_frame, textvariable=self.scrape_count, width=5).pack(side="left", padx=5)
        self.scrape_button = Button(count_frame, text="🔎 Cari & Tempel Link", command=self.start_scraping_thread); self.scrape_button.pack(side="left", expand=True, fill="x")

        overlay_short_lf = LabelFrame(scraper_lf, text="Timpa Video Short", padx=5, pady=5)
        overlay_short_lf.pack(fill="x", padx=5, pady=(10, 5))
        Checkbutton(overlay_short_lf, text="Aktifkan Timpa Video Short", variable=self.overlay_short_var).pack(anchor="w")

        manual_crop_frame = Frame(overlay_short_lf)
        manual_crop_frame.pack(fill='x', padx=5, pady=(5,0))
        self.manual_crop_cb = Checkbutton(manual_crop_frame, text="Atur Crop Manual (Opsional)", variable=self.use_manual_crop, command=self.toggle_manual_crop_button)
        self.manual_crop_cb.pack(anchor="w")
        crop_btn_frame = Frame(manual_crop_frame, padx=20)
        crop_btn_frame.pack(fill='x')
        self.manual_crop_button = Button(crop_btn_frame, text="Atur Crop Manual...", command=self.open_crop_window, state="disabled")
        self.manual_crop_button.pack(side="left", pady=(0, 5))
        Label(crop_btn_frame, textvariable=self.manual_crop_status, fg="blue").pack(side="left", padx=10)

        Button(overlay_short_lf, text="Pilih Video Latar Untuk Shorts", command=self.select_short_background).pack(fill="x", pady=(0, 2))
        Label(overlay_short_lf, textvariable=self.short_background_file, fg="blue", wraplength=350).pack(anchor="w", padx=2)

        cut_mode_lf = LabelFrame(scrollable_frame, text="Mode Pemotongan Video (Long-to-Short)", font=("Helvetica", 10, "bold"), padx=10, pady=10)
        cut_mode_lf.pack(fill="x", pady=(0,10), padx=10)
        Radiobutton(cut_mode_lf, text="Otomatis (AI)", variable=self.cut_mode, value="otomatis", command=self.toggle_manual_cut_fields).pack(anchor="w")
        Radiobutton(cut_mode_lf, text="Manual (Custom Cut)", variable=self.cut_mode, value="manual", command=self.toggle_manual_cut_fields).pack(anchor="w")
        self.manual_fields_frame = Frame(cut_mode_lf, padx=15); self.manual_fields_frame.pack(fill="x")
        Label(self.manual_fields_frame, text="Waktu Mulai (HH:MM:SS):").pack(anchor="w", pady=(5,0)); self.start_entry = Entry(self.manual_fields_frame, textvariable=self.manual_start_time); self.start_entry.pack(fill="x")
        Label(self.manual_fields_frame, text="Waktu Selesai (HH:MM:SS):").pack(anchor="w", pady=(5,0)); self.end_entry = Entry(self.manual_fields_frame, textvariable=self.manual_end_time); self.end_entry.pack(fill="x")
        Checkbutton(cut_mode_lf, text="Tambahkan Teks Sumber Video", variable=self.long_to_short_add_source).pack(anchor="w", pady=(5,0))

        file_lf = LabelFrame(scrollable_frame, text="File & Aset", font=("Helvetica", 10, "bold"), padx=10, pady=10); file_lf.pack(fill="x", pady=(0,10), padx=10)
        Button(file_lf, text="Pilih Folder Output", command=self.select_output_folder).pack(fill="x")
        Label(file_lf, textvariable=self.output_folder, fg="blue", wraplength=350).pack(anchor="w", padx=2, pady=(0,5))
        Button(file_lf, text="Pilih Watermark (Opsional)", command=self.select_watermark).pack(fill="x")
        Label(file_lf, textvariable=self.watermark_file, fg="blue", wraplength=350).pack(anchor="w", padx=2)
        pos_frame = Frame(file_lf); pos_frame.pack(fill="x", pady=(2, 5)); Label(pos_frame, text="Posisi:").pack(side="left"); OptionMenu(pos_frame, self.watermark_position, *["Kanan Atas", "Kiri Atas", "Kanan Bawah", "Kiri Bawah", "Tengah", "Posisi Acak"]).pack(side="left", fill="x", expand=True)
        Button(file_lf, text="Pilih Gambar Thumbnail Kustom", command=self.select_thumbnail).pack(fill="x")
        Label(file_lf, textvariable=self.thumbnail_file, fg="blue", wraplength=350).pack(anchor="w", padx=2, pady=(0,5))
        Checkbutton(file_lf, text="Gunakan Thumbnail Kustom (untuk mode non-AI)", variable=self.use_custom_thumbnail).pack(anchor="w")

        audio_lf = LabelFrame(scrollable_frame, text="Pengaturan Audio", font=("Helvetica", 10, "bold"), padx=10, pady=10); audio_lf.pack(fill="x", pady=(0,10), padx=10)
        Checkbutton(audio_lf, text="Hapus Suara Asli Video", variable=self.remove_original_audio_var, command=self.toggle_original_audio_slider).pack(anchor="w")
        original_volume_frame = Frame(audio_lf); original_volume_frame.pack(fill="x", pady=2, padx=5); Label(original_volume_frame, text="Volume Asli:").pack(side="left")
        self.original_audio_slider = Scale(original_volume_frame, from_=0, to=100, orient="horizontal", variable=self.original_audio_volume_var, command=self.update_original_volume_label); self.original_audio_slider.pack(side="left", expand=True, fill="x", padx=5)
        Label(original_volume_frame, textvariable=self.original_volume_display_var, width=4).pack(side="left")
        Button(audio_lf, text="Pilih Musik Latar", command=self.select_music).pack(fill="x", pady=(5,0))
        Label(audio_lf, textvariable=self.music_file, fg="blue", wraplength=350).pack(anchor="w", padx=2)
        music_volume_frame = Frame(audio_lf); music_volume_frame.pack(fill="x", pady=2, padx=5); Label(music_volume_frame, text="Volume Musik:").pack(side="left")
        self.music_slider = Scale(music_volume_frame, from_=0, to=100, orient="horizontal", variable=self.music_volume_var, command=self.update_music_volume_label, state="disabled"); self.music_slider.pack(side="left", expand=True, fill="x", padx=5)
        Label(music_volume_frame, textvariable=self.volume_display_var, width=4).pack(side="left")

        subtitle_font_lf = LabelFrame(scrollable_frame, text="Pengaturan Subtitle (Opsional)", font=("Helvetica", 10, "bold"), padx=10, pady=10)
        subtitle_font_lf.pack(fill="x", pady=(0,10), padx=10)
        Checkbutton(subtitle_font_lf, text="Tambahkan Subtitle ke Video (Burn-in)", variable=self.burn_subtitles).pack(anchor="w")
        font_selection_frame = Frame(subtitle_font_lf)
        font_selection_frame.pack(fill="x", pady=(5,0), padx=15)
        Label(font_selection_frame, text="Pilih Font:").pack(side="left", padx=(0,10))
        OptionMenu(font_selection_frame, self.subtitle_font_selection, *self.font_map.keys()).pack(side="left", fill="x", expand=True)

        ai_lf = LabelFrame(right_column, text="Pengaturan AI & Transkripsi", font=("Helvetica", 10, "bold"), padx=10, pady=10); ai_lf.pack(fill="x", pady=(0, 10))
        ai_frame = Frame(ai_lf); ai_frame.pack(fill='x', pady=2)
        Label(ai_frame, text="Akurasi Transkripsi:").pack(side="left", padx=(0,10))
        OptionMenu(ai_frame, self.whisper_model_selection, *["base", "small", "medium"]).pack(side="left")

        effects_lf = LabelFrame(right_column, text="Efek Video (Berlaku untuk semua mode)", font=("Helvetica", 10, "bold"), padx=10, pady=10); effects_lf.pack(fill="x", pady=(0,10))
        Checkbutton(effects_lf, text="Mirror (Cermin Horizontal)", variable=self.effects_vars['mirror']).pack(anchor="w"); Checkbutton(effects_lf, text="Grayscale (Hitam Putih)", variable=self.effects_vars['grayscale']).pack(anchor="w")
        Checkbutton(effects_lf, text="Sepia", variable=self.effects_vars['sepia']).pack(anchor="w"); Checkbutton(effects_lf, text="Negate (Warna Negatif)", variable=self.effects_vars['negate']).pack(anchor="w")
        Checkbutton(effects_lf, text="Color Boost (Saturasi Tinggi)", variable=self.effects_vars['color_boost']).pack(anchor="w")

        zoom_frame = Frame(effects_lf)
        zoom_frame.pack(fill="x", pady=2)
        Checkbutton(zoom_frame, text="Zoom Statis", variable=self.effects_vars['static_zoom'], command=self.toggle_zoom_slider).pack(side="left")
        self.zoom_slider = Scale(zoom_frame, from_=1.0, to=2.0, orient="horizontal", variable=self.zoom_level_var, command=self.update_zoom_label, resolution=0.05, state="disabled", length=150)
        self.zoom_slider.pack(side="left", expand=True, fill="x", padx=5)
        Label(zoom_frame, textvariable=self.zoom_display_var, width=5).pack(side="left")

        url_lf = LabelFrame(right_column, text="Masukkan Link Video (satu per baris)", font=("Helvetica", 10, "bold"), padx=10, pady=10); url_lf.pack(fill="x")
        self.url_text = Text(url_lf, relief="solid", borderwidth=1, font=("Courier", 10), height=5); self.url_text.pack(fill="both", expand=True, pady=2)

        action_lf = LabelFrame(right_column, text="Kontrol & Log Proses", font=("Helvetica", 10, "bold"), padx=10, pady=10); action_lf.pack(fill="both", expand=True, pady=(10,0))
        control_frame = Frame(action_lf); control_frame.pack(fill="x")
        self.start_button = Button(control_frame, text="🚀 Mulai Proses Video", command=self.start_processing_thread, bg="#28a745", fg="white", font=("Helvetica", 12, "bold"), relief="raised"); self.start_button.pack(side="left", fill="x", expand=True, ipady=8)
        self.stop_button = Button(control_frame, text="❌ Stop Proses", command=self.stop_processing, bg="#dc3545", fg="white", font=("Helvetica", 12, "bold"), relief="raised")
        self.clear_log_button = Button(action_lf, text="🗑️ Bersihkan Log (Hanya di GUI)", command=self.clear_log, font=("Helvetica", 8)); self.clear_log_button.pack(fill="x", pady=4)
        self.progress_bar = Progressbar(action_lf, orient="horizontal", length=100, mode="determinate"); self.progress_bar.pack(fill="x", pady=8)
        log_frame = Frame(action_lf); log_frame.pack(fill="both", expand=True)
        self.log_text = Text(log_frame, state='disabled', wrap='word', relief="solid", borderwidth=1); scrollbar = Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=scrollbar.set); scrollbar.pack(side="right", fill="y"); self.log_text.pack(side="left", fill="both", expand=True)

        self.toggle_manual_cut_fields(); self.toggle_original_audio_slider(); self.toggle_long_simple_options(); self.toggle_zoom_slider()

    def toggle_manual_crop_button(self):
        state = "normal" if self.use_manual_crop.get() else "disabled"
        self.manual_crop_button.config(state=state)
        if not self.use_manual_crop.get():
            self.manual_crop_status.set("Status: Belum diatur.")
            self.manual_crop_coords = None

    def open_crop_window(self):
        urls = [url for url in self.url_text.get("1.0", "end-1c").strip().splitlines() if url.strip()]
        if not urls:
            messagebox.showerror("Error", "Masukkan setidaknya satu URL video di kotak teks sebelum mengatur crop.")
            return

        video_url = urls[0]
        logging.info(f"🖼️ Mengambil thumbnail dari: {video_url}")

        video_id_match = re.search(r"(?:v=|\/shorts\/|youtu\.be\/|embed\/)([^#\&\?]{11})", video_url)
        if not video_id_match:
            messagebox.showerror("Error", "URL YouTube pertama tidak valid atau tidak dapat menemukan Video ID.")
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
            messagebox.showerror("Error", f"Gagal mengambil thumbnail dari YouTube. Mungkin video tidak memiliki thumbnail standar.\n\nError: {e}")
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

    def clear_log(self): self.log_text.config(state='normal'); self.log_text.delete('1.0', 'end'); self.log_text.config(state='disabled')
    def stop_processing(self): logging.warning("\n🛑 PERINTAH STOP DITERIMA! Menghentikan proses..."); self.stop_event.set(); self.stop_button.pack_forget()
    def start_scraping_thread(self):
        if not self.scrape_channel_url.get(): messagebox.showerror("Error", "Masukkan URL Channel YouTube."); return
        self.scrape_button.config(state="disabled", text="Mencari..."); threading.Thread(target=self.scrape_shorts_from_channel, daemon=True).start()
    def scrape_shorts_from_channel(self):
        channel_url = self.scrape_channel_url.get(); count = self.scrape_count.get()
        logging.info(f"\n🔎 Mulai mencari {count} shorts dari channel: {channel_url}")

        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'force_generic_extractor': True,
            'playlistend': count
        }

        if getattr(sys, 'frozen', False):
            base_path = os.path.dirname(sys.executable)
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
        cookie_path = os.path.join(base_path, COOKIE_FILE)

        if os.path.exists(cookie_path):
            logging.info(f"   🍪 Menggunakan '{COOKIE_FILE}' untuk scraping.")
            ydl_opts['cookiefile'] = cookie_path
        else:
            logging.warning(f"   ⚠️ File '{COOKIE_FILE}' tidak ditemukan. Scraping tanpa autentikasi.")

        found_urls = []
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(f"{channel_url}/shorts", download=False)
                if 'entries' in result:
                    logging.info(f"   Menganalisis {len(result['entries'])} video terbaru...")
                    for entry in result['entries']:
                        if len(found_urls) >= count: break
                        if entry:
                            video_id = entry.get('id')
                            if video_id:
                                found_urls.append(f"https://www.youtube.com/watch?v={video_id}")
                                logging.info(f"   ✅ Ditemukan Short: {entry.get('title', video_id)}")
                else: logging.warning("   ❌ Tidak ada video ditemukan. Pastikan URL channel benar.")

            def update_ui():
                self.url_text.delete("1.0", "end"); self.url_text.insert("1.0", "\n".join(found_urls))
                logging.info(f"✅ Berhasil menempelkan {len(found_urls)} link video Shorts.")
                self.scrape_button.config(state="normal", text="🔎 Cari & Tempel Link")
            self.root.after(0, update_ui)
        except Exception as e:
            logging.error(f"❌ Gagal scraping: {e}");
            logging.error("   Pastikan URL channel benar dan coba gunakan cookie jika channel bersifat privat.")
            self.root.after(0, lambda: self.scrape_button.config(state="normal", text="🔎 Cari & Tempel Link"))

    def toggle_manual_cut_fields(self):
        self.start_entry.config(state="normal" if self.cut_mode.get() == "manual" else "disabled")
        self.end_entry.config(state="normal" if self.cut_mode.get() == "manual" else "disabled")
    def update_music_volume_label(self, val): self.volume_display_var.set(f"{int(float(val))}%")
    def update_original_volume_label(self, val): self.original_volume_display_var.set(f"{int(float(val))}%")
    def get_and_copy_uuid(self):
        try:
            device_id = machineid.id(); self.device_id_var.set(device_id)
            self.root.clipboard_clear(); self.root.clipboard_append(device_id)
            messagebox.showinfo("ID Disalin", "ID Perangkat Anda telah disalin ke clipboard.")
        except Exception as e: messagebox.showerror("Error", f"Gagal mendapatkan ID Perangkat: {e}")
    def _initial_license_check(self): threading.Thread(target=lambda:self.license_queue.put(verify_license(logging.info)),daemon=True).start()
    def process_license_queue(self):
        try:
            is_valid, device_id = self.license_queue.get_nowait()
            if device_id: self.device_id_var.set(device_id)
            self.license_status_label.config(text="TERVALIDASI" if is_valid else "TIDAK VALID", fg="green" if is_valid else "red")
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
        if not self.url_text.get("1.0", "end-1c").strip(): messagebox.showerror("Error", "Masukkan setidaknya satu URL video."); return
        if not self.output_folder.get(): messagebox.showerror("Error", "Pilih folder untuk menyimpan video."); return

        if self.overlay_short_var.get() and not self.short_background_full_path:
            messagebox.showerror("Error", "Anda mengaktifkan 'Timpa Video Short', silakan pilih video latar terlebih dahulu."); return

        self.start_button.config(state="disabled", text="⏳ Sedang Memproses..."); self.stop_button.pack(side="left", fill="x", expand=True, ipady=8, padx=(5,0))
        self.progress_bar['value'] = 0; self.stop_event.clear()
        threading.Thread(target=self.run_processing_logic, daemon=True).start()

    def update_progress(self, val, total, msg): 
        logging.info(f"\n[LANGKAH {val}/{total}] {msg}")
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

            DEEPSEEK_API_KEY = self.custom_api_key.get() if self.use_custom_api_key.get() else None
            DEEPSEEK_MODEL = "deepseek-chat"
            if not DEEPSEEK_API_KEY:
                config = load_effective_config(logging.info)
                if not config: return
                DEEPSEEK_API_KEY = config.get("deepseek_api_key", config.get("api_key"))
                DEEPSEEK_MODEL = config.get("deepseek_model", "deepseek-chat")

            self.deepseek_client = configure_deepseek(DEEPSEEK_API_KEY, logging.info)
            if not self.deepseek_client:
                logging.error("   ❌ Gagal mengkonfigurasi API DeepSeek. Proses AI dibatalkan.")
                return

            output_folder_base = self.output_folder.get().replace("Folder Output: ", "")
            output_folder = os.path.join(output_folder_base, OUTPUT_SUBFOLDER); os.makedirs(output_folder, exist_ok=True)
            video_urls = [url for url in self.url_text.get("1.0", "end-1c").strip().splitlines() if url.strip()]

            long_simple_mode_choice = self.long_simple_sub_mode.get()

            transcription_is_needed = self.burn_subtitles.get() or (is_long_to_short_mode and self.cut_mode.get() == 'otomatis') or (is_long_simple_mode and long_simple_mode_choice == "AI_SUMMARY")
            whisper_model = None
            if transcription_is_needed:
                self.update_progress(3, 10, "Memuat Model Transkripsi...")
                selected_whisper_model = self.whisper_model_selection.get()
                logging.info(f"   Memuat model AI Whisper ({selected_whisper_model})... Ini mungkin butuh waktu saat pertama kali.")
                try:
                    whisper_model = whisper.load_model(selected_whisper_model)
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
                    with yt_dlp.YoutubeDL({'quiet': True, 'nocheckcertificate': True}) as ydl:
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

                        summary_data = get_summary_clips_from_deepseek(
                            transcript_text=transcription_result['text'],
                            video_duration=video_duration,
                            deepseek_model_name=DEEPSEEK_MODEL,
                            deepseek_client=self.deepseek_client,
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
                        new_title = get_paraphrased_title_from_deepseek(final_title, DEEPSEEK_MODEL, self.deepseek_client, logging.info)
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

                        all_ai_clips = get_clips_from_deepseek(
                            transcript_text=transcription_result['text'],
                            deepseek_model_name=DEEPSEEK_MODEL,
                            deepseek_client=self.deepseek_client,
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

                    if subtitle_path and os.path.exists(subtitle_path): os.remove(subtitle_path)

                if os.path.exists(video_path):
                    try:
                        os.remove(video_path)
                        logging.info(f"   🗑️ File video asli ({os.path.basename(video_path)}) telah dihapus.")
                    except OSError as e:
                        logging.warning(f"   ⚠️ Gagal menghapus file video asli: {e}")

            if self.stop_event.is_set(): logging.warning("\n🛑 Proses dihentikan oleh pengguna.")
            else: self.update_progress(10, 10, "SEMPURNA! SEMUA VIDEO TELAH DIPROSES!"); logging.info("\n🎉🎉🎉 SEMPURNA! SEMUA VIDEO TELAH DIPROSES! 🎉🎉🎉")
        except Exception as e:
            logging.error(f"\n❌ TERJADI ERROR FATAL PADA SCRIPT ❌")
            logging.error(traceback.format_exc())
            messagebox.showerror("Error Fatal", f"Terjadi error yang tidak terduga. Silakan cek file 'autoclipper_log.txt' untuk detail.\n\nError: {e}")
        finally:
            self.start_button.config(state="normal", text="🚀 Mulai Proses Video")
            self.stop_button.pack_forget()
            self.progress_bar['value'] = 0
            self.deepseek_client = None

if __name__ == "__main__":
    # Setup logging darurat jika GUI gagal
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
            messagebox.showerror("Fatal Error", f"Gagal memulai aplikasi:\n{traceback.format_exc()}\n\nCek 'autoclipper_log.txt' untuk detail.")
        except Exception as e2:
            print(f"Gagal menulis log fatal: {e2}") # Fallback ke console
            print(f"Gagal memulai aplikasi:\n{traceback.format_exc()}\nTekan Enter untuk keluar...")
            input()
