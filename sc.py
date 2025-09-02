# =============================================================================
# Impor Library
# =============================================================================
import os
import sys
import json
import re
import traceback
import threading
import queue
import textwrap
from tkinter import Tk, filedialog, Button, Label, Text, Scrollbar, Frame, messagebox, StringVar, OptionMenu, Entry, Checkbutton, BooleanVar, Scale, IntVar, LabelFrame
from tkinter.ttk import Progressbar

# Third-party libraries (pastikan sudah di-install)
# pip install yt-dlp ffmpeg-python openai-whisper google-generativeai requests pycryptodome wmi machineid
import yt_dlp
import ffmpeg
import whisper
import google.generativeai as genai
import requests
import machineid

# ==============================================================================
# KONFIGURASI
# ==============================================================================
LICENSE_URL = "https://raw.githubusercontent.com/nezastore/clipper-config/refs/heads/main/licenses.txt?v=1"
CONFIG_URL = "https://raw.githubusercontent.com/nezastore/clipper-config/refs/heads/main/config.json"
OUTPUT_SUBFOLDER = "Hasil"
CUSTOM_FONT_FILE = 'ContrailOne-Regular.ttf'

# ==============================================================================
# FUNGSI-FUNGSI UTILITY & BACKEND
# ==============================================================================
def sanitize_filename(filename):
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F" "\U0001F300-\U0001F5FF" "\U0001F680-\U0001F6FF"
        "\U0001F1E0-\U0001F1FF" "\U00002702-\U000027B0" "\U000024C2-\U0001F251"
        "]+", flags=re.UNICODE)
    filename = emoji_pattern.sub(r"", filename)
    filename = filename.replace("#", "")
    return re.sub(r'[\\/*?:"<>|]', "", filename).strip()

def verify_license(logger_func=print):
    logger_func("🔑 Mengecek koneksi ke server lisensi...")
    try:
        device_id = machineid.id()
        response = requests.get(LICENSE_URL)
        response.raise_for_status()
        authorized_ids = response.text.strip().splitlines()
        if device_id in authorized_ids:
            logger_func("✅ Lisensi valid.")
            return True, device_id
        else:
            logger_func("⛔ Lisensi tidak valid untuk perangkat ini.")
            return False, device_id
    except Exception as e:
        logger_func(f"❌ Gagal memverifikasi lisensi: {e}")
        return False, None

def load_remote_config(logger_func):
    logger_func("🌍 Memuat konfigurasi online...")
    try:
        response = requests.get(CONFIG_URL)
        response.raise_for_status()
        config = response.json()
        logger_func("✅ Konfigurasi online berhasil dimuat."); return config
    except Exception:
        logger_func("❌ Gagal memuat file konfigurasi online."); return None

def configure_gemini(api_key, logger_func):
    if not api_key: logger_func("❌ ERROR: API Key tidak ditemukan."); return False
    try: genai.configure(api_key=api_key); logger_func("✅ Konfigurasi AI API berhasil."); return True
    except Exception as e: logger_func(f"❌ ERROR: Gagal mengkonfigurasi AI API. {e}"); return False

def download_video(url, output_path='downloaded_video.mp4', logger_func=print):
    if os.path.exists(output_path): os.remove(output_path)
    def my_progress_hook(d):
        if d['status'] == 'downloading':
            percent_str = d.get('_percent_str', '0%')
            cleaned_percent_str = re.sub(r'\x1b\[[0-9;]*m', '', percent_str).strip()
            logger_func(f"   Downloading... {cleaned_percent_str}")
    ydl_opts = {'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best', 'outtmpl': output_path, 'merge_output_format': 'mp4', 'quiet': True, 'progress_hooks': [my_progress_hook]}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl: info = ydl.extract_info(url, download=True)
        return output_path, info.get('uploader', 'Unknown Channel')
    except Exception as e:
        logger_func(f"❌ ERROR saat mengunduh video: {str(e)}"); return None, None

def transcribe_audio(audio_path, whisper_model, model_name, logger_func=print):
    try:
        result = whisper_model.transcribe(audio_path, verbose=False, word_timestamps=True)
        return result
    except Exception as e:
        logger_func(f"❌ ERROR saat transkripsi: {e}"); return None

def get_clips_from_gemini(transcript_text, gemini_model_name, logger_func=print):
    prompt = f"""
    Anda adalah API pemroses teks otomatis. Tugas Anda menganalisis teks di dalam tag `<transcript>` dan mengubahnya menjadi format JSON yang valid.
    ATURAN: Output HARUS berupa JSON valid, dimulai dengan `[` dan diakhiri `]`. Jangan menulis teks lain.
    <transcript>{transcript_text}</transcript>
    INSTRUKSI: Identifikasi 5-7 klip viral (30-60 detik). Untuk setiap klip, buat objek JSON dengan kunci: "start_time" & "end_time" (HH:MM:SS), "title" (judul clickbait maks 70 karakter dengan 1-2 emoji), "hashtags" (list 3 string tagar), "editing_style" (pilih satu: "dynamic", "emotional", atau "informative").
    """
    try:
        model = genai.GenerativeModel(gemini_model_name)
        response = model.generate_content(prompt)
        json_match = re.search(r'\[.*\]', response.text, re.DOTALL)
        if not json_match: logger_func("❌ ERROR: Format JSON tidak ditemukan."); return []
        return json.loads(json_match.group(0))
    except Exception as e:
        logger_func(f"❌ ERROR saat analisis AI: {e}"); return []

def process_clip(source_video, start_time, end_time, watermark_file, source_text, output_filename, style, music_file, music_volume, word_segments, effects, logger_func=print):
    try:
        main_video = ffmpeg.input(source_video, ss=start_time, to=end_time)
        watermark = ffmpeg.input(watermark_file)
        probe = ffmpeg.probe(source_video)
        video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
        if not video_stream: return
        height = int(video_stream['height'])
        crop_width = int(height * 9 / 16)
        if crop_width % 2 != 0: crop_width -= 1
        crop_x = int((int(video_stream['width']) - crop_width) / 2)
        processed_video = main_video.video.filter('crop', w=crop_width, h=height, x=crop_x, y=0)
        if style == "dynamic":
            processed_video = processed_video.zoompan(z='min(zoom+0.0015,1.15)', d=12*25, s=f'{crop_width}x{height}').filter('eq', contrast=1.1, saturation=1.3).filter('unsharp', luma_msize_x=5, luma_msize_y=5, luma_amount=1.0)
        else:
            processed_video = processed_video.filter('eq', contrast=1.1, saturation=1.25).filter('unsharp', luma_msize_x=5, luma_msize_y=5, luma_amount=0.8)
        if effects.get('mirror'): processed_video = processed_video.hflip()
        if effects.get('grayscale'): processed_video = processed_video.filter('hue', s=0)
        if effects.get('sepia'): processed_video = processed_video.filter('colorchannelmixer', '.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131')
        if effects.get('negate'): processed_video = processed_video.filter('negate')
        if effects.get('color_boost'): processed_video = processed_video.filter('eq', saturation=1.8)
        subtitle_phrases, current_phrase, phrase_start_time, max_line_length = [], "", -1, 22
        for word_info in word_segments:
            word = word_info['word']
            if phrase_start_time == -1: phrase_start_time = word_info['start']
            if len(current_phrase) + len(word) + 1 > max_line_length:
                subtitle_phrases.append({'text': current_phrase.strip(), 'start': phrase_start_time, 'end': word_info['start']})
                current_phrase, phrase_start_time = word, word_info['start']
            else: current_phrase += " " + word
        if current_phrase: subtitle_phrases.append({'text': current_phrase.strip(), 'start': phrase_start_time, 'end': word_segments[-1]['end']})
        start_clip_time = sum(x * float(t) for x, t in zip([3600, 60, 1], start_time.split(":")))
        for phrase in subtitle_phrases:
            start_sub, end_sub = phrase['start'] - start_clip_time, phrase['end'] - start_clip_time
            processed_video = processed_video.drawtext(text=phrase['text'].upper(), x='(w-text_w)/2', y='h*0.70', fontsize='42', fontcolor='white', fontfile=CUSTOM_FONT_FILE, borderw=3, bordercolor='black@0.8', enable=f'between(t,{start_sub},{end_sub})')
        processed_video = ffmpeg.overlay(processed_video, watermark, x='main_w-overlay_w-10', y='10')
        processed_video = processed_video.drawtext(text=source_text, x='(w-text_w)/2', y='h-th-20', fontsize=20, fontcolor='white', box=1, boxcolor='black@0.5', boxborderw=5)
        original_audio = main_video.audio
        if music_file:
            music_audio = ffmpeg.input(music_file).audio
            volume_level = music_volume / 100.0
            mixed_audio = ffmpeg.filter([original_audio.filter('volume', 1.0), music_audio.filter('volume', volume_level)], 'amix', duration='first')
        else: mixed_audio = original_audio
        final_output = ffmpeg.output(processed_video, mixed_audio, output_filename, vcodec='libx264', acodec='aac', preset='fast', crf=23)
        final_output.run(overwrite_output=True, quiet=True)
    except Exception as e:
        logger_func(f"❌ ERROR saat memproses klip: {e}")

# ==============================================================================
# KELAS UTAMA APLIKASI GUI
# ==============================================================================
class VideoClipperApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Youtube Video Auto Clipper (Telegram : @nezastore)")
        self.root.geometry("950x800")
        self.root.resizable(True, True)
        self.root.minsize(900, 750)

        # Inisialisasi semua variabel
        self.output_folder, self.watermark_file, self.music_file = StringVar(), StringVar(), StringVar()
        self.watermark_full_path, self.music_full_path, self.device_id_var = "", "", StringVar()
        self.log_queue, self.license_queue = queue.Queue(), queue.Queue()
        self.whisper_model_selection = StringVar(value="base")
        self.effects_vars = { 'mirror': BooleanVar(), 'grayscale': BooleanVar(), 'sepia': BooleanVar(), 'negate': BooleanVar(), 'color_boost': BooleanVar() }
        self.music_volume_var = IntVar(value=20)
        self.volume_display_var = StringVar(value="20%")

        self.setup_ui()
        self.root.after(100, self.process_log_queue)
        self.root.after(200, self.process_license_queue)
        self.root.after(500, self._initial_license_check)

    def setup_ui(self):
        main_frame = Frame(self.root, padx=10, pady=10)
        main_frame.pack(fill="both", expand=True)

        middle_frame = Frame(main_frame)
        middle_frame.pack(fill="both", expand=True, pady=5)

        left_column = Frame(middle_frame, width=400)
        left_column.pack(side="left", fill="y", padx=(0, 10))
        left_column.pack_propagate(False)

        right_column = Frame(middle_frame)
        right_column.pack(side="left", fill="both", expand=True)
        
        license_lf = LabelFrame(left_column, text="Manajemen Lisensi", font=("Helvetica", 10, "bold"), padx=10, pady=10)
        license_lf.pack(fill="x", pady=(0,10))
        id_entry = Entry(license_lf, textvariable=self.device_id_var, state="readonly", width=30)
        id_entry.grid(row=0, column=0, columnspan=2, sticky="ew")
        Button(license_lf, text="Salin ID", command=self.get_and_copy_uuid).grid(row=0, column=2, padx=(10,0))
        Label(license_lf, text="Status:").grid(row=1, column=0, sticky="w", pady=(5,0))
        self.license_status_label = Label(license_lf, text="- MENGECEK -", font=("Helvetica", 10, "bold"), fg="grey")
        self.license_status_label.grid(row=1, column=1, sticky="w", pady=(5,0))
        license_lf.columnconfigure(1, weight=1)

        settings_lf = LabelFrame(left_column, text="Pengaturan File & AI", font=("Helvetica", 10, "bold"), padx=10, pady=10)
        settings_lf.pack(fill="x", pady=(0,10))
        Button(settings_lf, text="Pilih Folder Output", command=self.select_output_folder).pack(fill="x", pady=2, anchor="w")
        Label(settings_lf, textvariable=self.output_folder, fg="blue").pack(anchor="w")
        Button(settings_lf, text="Pilih Watermark", command=self.select_watermark).pack(fill="x", pady=(8,2), anchor="w")
        Label(settings_lf, textvariable=self.watermark_file, fg="blue").pack(anchor="w")
        Button(settings_lf, text="Pilih Musik Latar", command=self.select_music).pack(fill="x", pady=(8,2), anchor="w")
        Label(settings_lf, textvariable=self.music_file, fg="blue").pack(anchor="w")
        volume_frame = Frame(settings_lf); volume_frame.pack(fill="x", pady=5)
        Label(volume_frame, text="Volume:").pack(side="left")
        Scale(volume_frame, from_=0, to=100, orient="horizontal", variable=self.music_volume_var, command=self.update_volume_label).pack(side="left", expand=True, fill="x", padx=5)
        Label(volume_frame, textvariable=self.volume_display_var, width=4).pack(side="left")
        model_frame = Frame(settings_lf); model_frame.pack(fill='x', pady=5, anchor="w")
        Label(model_frame, text="Akurasi:").pack(side="left", padx=(0,10))
        OptionMenu(model_frame, self.whisper_model_selection, *["base", "small", "medium"]).pack(side="left")

        effects_lf = LabelFrame(left_column, text="Efek Video (Opsional)", font=("Helvetica", 10, "bold"), padx=10, pady=10)
        effects_lf.pack(fill="x", pady=(0,10))
        Checkbutton(effects_lf, text="Mirror (Cermin Horizontal)", variable=self.effects_vars['mirror']).pack(anchor="w")
        Checkbutton(effects_lf, text="Grayscale (Hitam Putih)", variable=self.effects_vars['grayscale']).pack(anchor="w")
        Checkbutton(effects_lf, text="Sepia", variable=self.effects_vars['sepia']).pack(anchor="w")
        Checkbutton(effects_lf, text="Negate (Warna Negatif)", variable=self.effects_vars['negate']).pack(anchor="w")
        Checkbutton(effects_lf, text="Color Boost (Saturasi Tinggi)", variable=self.effects_vars['color_boost']).pack(anchor="w")

        url_lf = LabelFrame(right_column, text="Masukkan Link Video (satu per baris)", font=("Helvetica", 10, "bold"), padx=10, pady=10)
        url_lf.pack(fill="x")
        self.url_text = Text(url_lf, relief="solid", borderwidth=1, font=("Courier", 10), height=5)
        self.url_text.pack(fill="both", expand=True, pady=2)
        
        action_lf = LabelFrame(right_column, text="Kontrol & Log Proses", font=("Helvetica", 10, "bold"), padx=10, pady=10)
        action_lf.pack(fill="both", expand=True, pady=(10,0))
        self.start_button = Button(action_lf, text="🚀 Mulai Proses Video", command=self.start_processing_thread, bg="#28a745", fg="white", font=("Helvetica", 12, "bold"), relief="raised")
        self.start_button.pack(fill="x", ipady=8)
        self.progress_bar = Progressbar(action_lf, orient="horizontal", length=100, mode="determinate")
        self.progress_bar.pack(fill="x", pady=8)
        self.log_text = Text(action_lf, state='disabled', wrap='word', relief="solid", borderwidth=1)
        scrollbar = Scrollbar(action_lf, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

    def update_volume_label(self, value): self.volume_display_var.set(f"{int(float(value))}%")
    def get_and_copy_uuid(self):
        try:
            device_id = machineid.id()
            self.device_id_var.set(device_id)
            self.root.clipboard_clear()
            self.root.clipboard_append(device_id)
            messagebox.showinfo("ID Disalin", "ID Perangkat Anda telah disalin ke clipboard.")
        except Exception as e: messagebox.showerror("Error", f"Gagal mendapatkan ID Perangkat: {e}")
    def _initial_license_check(self):
        threading.Thread(target=lambda: self.license_queue.put(verify_license(lambda msg: None)), daemon=True).start()
    def process_license_queue(self):
        try:
            is_valid, device_id = self.license_queue.get_nowait()
            if device_id: self.device_id_var.set(device_id)
            if is_valid: self.license_status_label.config(text="TERVALIDASI", fg="green")
            else: self.license_status_label.config(text="TIDAK VALID | Hubungi: @nezastore", fg="red")
        except queue.Empty: self.root.after(200, self.process_license_queue)
    def select_output_folder(self):
        folder = filedialog.askdirectory(title="Pilih Folder Output")
        if folder: self.output_folder.set(f"Folder Output: {folder}")
    
    # --- PERBAIKAN BUG DISINI ---
    def select_watermark(self):
        file = filedialog.askopenfilename(title="Pilih File Watermark", filetypes=[("Image Files", "*.png;*.jpg;*.jpeg")])
        if file:
            self.watermark_full_path = file
            self.watermark_file.set(f"Watermark: {os.path.basename(file)}")

    def select_music(self):
        file = filedialog.askopenfilename(title="Pilih File Musik", filetypes=[("Audio Files", "*.mp3;*.wav;*.m4a")])
        if file:
            self.music_full_path = file
            self.music_file.set(f"Musik: {os.path.basename(file)}")
    # --- AKHIR PERBAIKAN ---

    def log(self, message): self.log_queue.put(message)
    def process_log_queue(self):
        try:
            while True:
                message = self.log_queue.get_nowait()
                self.log_text.config(state='normal'); self.log_text.insert('end', message + '\n'); self.log_text.see('end'); self.log_text.config(state='disabled')
        except queue.Empty: self.root.after(100, self.process_log_queue)
    def start_processing_thread(self):
        if not (self.url_text.get("1.0", "end-1c").strip()): messagebox.showerror("Error", "Masukkan setidaknya satu URL video."); return
        if not self.output_folder.get(): messagebox.showerror("Error", "Pilih folder untuk menyimpan video."); return
        if not self.watermark_full_path: messagebox.showerror("Error", "Pilih file gambar untuk watermark."); return
        self.start_button.config(state="disabled", text="⏳ Sedang Memproses...")
        self.progress_bar['value'] = 0
        threading.Thread(target=self.run_processing_logic, daemon=True).start()
    def update_progress(self, step, total_steps, message):
        self.log(f"\n[LANGKAH {step}/{total_steps}] {message}")
        self.progress_bar['value'] = (step / total_steps) * 100
    def run_processing_logic(self):
        total_steps = 7
        try:
            self.update_progress(1, total_steps, "Memverifikasi Lisensi...")
            is_valid, _ = verify_license(self.log)
            if not is_valid: self.log("   Silakan hubungi admin Telegram : @nezastore untuk aktivasi lisensi."); return
            self.update_progress(2, total_steps, "Memuat Konfigurasi & Model AI...")
            config = load_remote_config(self.log)
            if not config: return
            GEMINI_API_KEY, GEMINI_MODEL = config.get("api_key"), config.get("gemini_model", "gemini-1.5-flash")
            if not configure_gemini(GEMINI_API_KEY, self.log): return
            selected_whisper_model = self.whisper_model_selection.get()
            self.log(f"   Memuat model AI Whisper ({selected_whisper_model})...")
            whisper_model = whisper.load_model(selected_whisper_model)
            self.log("   Model Whisper berhasil dimuat.")
            output_folder_base = self.output_folder.get().replace("Folder Output: ", "")
            output_folder = os.path.join(output_folder_base, OUTPUT_SUBFOLDER)
            if not os.path.exists(output_folder): os.makedirs(output_folder)
            video_urls = [url for url in self.url_text.get("1.0", "end-1c").strip().splitlines() if url.strip()]
            for index, video_url in enumerate(video_urls):
                self.log(f"\n{'='*20} MEMPROSES VIDEO {index+1}/{len(video_urls)} {'='*20}")
                self.update_progress(3, total_steps, f"Mengunduh Video ({index+1}/{len(video_urls)})...")
                video_path, channel_name = download_video(video_url, logger_func=self.log)
                if not video_path: continue
                self.update_progress(4, total_steps, f"Transkripsi Audio ({index+1}/{len(video_urls)})...")
                transcription_result = transcribe_audio(video_path, whisper_model, selected_whisper_model, self.log)
                if not transcription_result: continue
                self.update_progress(5, total_steps, f"Mencari Momen Penting dengan AI ({index+1}/{len(video_urls)})...")
                ai_clips = get_clips_from_gemini(transcription_result['text'], GEMINI_MODEL, self.log)
                if not ai_clips: self.log("   🔴 AI tidak memberikan rekomendasi klip."); continue
                self.update_progress(6, total_steps, f"Membuat Klip Video ({index+1}/{len(video_urls)})...")
                self.log(f"   Ditemukan {len(ai_clips)} potensi klip. Memulai rendering...")
                selected_effects = {key: var.get() for key, var in self.effects_vars.items()}
                music_volume_value = self.music_volume_var.get()
                for i, clip in enumerate(ai_clips):
                    try:
                        self.log(f"   - Merender klip {i+1} dari {len(ai_clips)}...")
                        start_time_str, end_time_str = clip['start_time'], clip['end_time']
                        start_sec = sum(x * float(t) for x, t in zip([3600, 60, 1], start_time_str.split(":")))
                        end_sec = sum(x * float(t) for x, t in zip([3600, 60, 1], end_time_str.split(":")))
                        clip_words = [word for s in transcription_result.get('segments',[]) for word in s.get('words',[]) if word['start'] < end_sec and word['end'] > start_sec]
                        if not clip_words: continue
                        safe_filename = sanitize_filename(f"{clip.get('title', f'Klip {i+1}')} {' '.join(clip.get('hashtags', []))}")
                        output_file = os.path.join(output_folder, f"{safe_filename}.mp4")
                        process_clip(
                            source_video=video_path, start_time=start_time_str, end_time=end_time_str,
                            watermark_file=self.watermark_full_path, source_text=f"Sumber: {channel_name}",
                            output_filename=output_file, style=clip.get('editing_style', 'informative'),
                            music_file=self.music_full_path, music_volume=music_volume_value,
                            word_segments=clip_words, effects=selected_effects, logger_func=self.log
                        )
                    except KeyError as e:
                        self.log(f"   ❌ ERROR: Kunci {e} tidak ada pada data klip AI.")
                os.remove(video_path)
                self.log(f"   🗑️ File video asli ({os.path.basename(video_path)}) telah dihapus.")
            self.update_progress(7, total_steps, "Semua proses telah selesai!")
            self.log("\n🎉🎉🎉 SEMPURNA! SEMUA VIDEO TELAH DIPROSES! 🎉🎉🎉")
        except Exception:
            self.log(f"\n❌ TERJADI ERROR YANG TIDAK DI DUGA PADA SCRIPT ❌\n{traceback.format_exc()}")
        finally:
            self.start_button.config(state="normal", text="🚀 Mulai Proses Video")

if __name__ == "__main__":
    try:
        root = Tk()
        app = VideoClipperApp(root)
        root.mainloop()
    except Exception:
        input(f"Gagal memulai aplikasi:\n{traceback.format_exc()}\nTekan Enter untuk keluar...")
