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
import time
import math
from tkinter import Tk, filedialog, Button, Label, Text, Scrollbar, Frame, messagebox, StringVar, OptionMenu, Entry, Checkbutton, BooleanVar, Scale, IntVar, LabelFrame, Radiobutton, Canvas
from tkinter.ttk import Progressbar

# Third-party libraries (pastikan sudah di-install)
# pip install yt-dlp ffmpeg-python openai-whisper google-generativeai requests pycryptodome wmi
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
COOKIE_FILE = 'cookies.txt' # Nama file cookie yang akan kita gunakan
TEMP_THUMBNAIL_FILE = "_temp_thumbnail.jpg"

# ==============================================================================
# FUNGSI-FUNGSI UTILITY & BACKEND
# ==============================================================================
def sanitize_filename(filename):
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags (iOS)
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
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

def download_video(url, output_path, logger_func=print):
    if os.path.exists(output_path):
        try:
            os.remove(output_path)
        except OSError as e:
            logger_func(f"❌ Gagal menghapus file sementara yang ada: {e}")
            return None, None
            
    info_dict = None
    def my_progress_hook(d):
        nonlocal info_dict
        if d['status'] == 'finished':
            info_dict = d.get('info_dict', {})
        if d['status'] == 'downloading':
            percent_str = d.get('_percent_str', '0%')
            cleaned_percent_str = re.sub(r'\x1b\[[0-9;]*m', '', percent_str).strip()
            logger_func(f"   Downloading... {cleaned_percent_str}")

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_path,
        'merge_output_format': 'mp4',
        'quiet': True,
        'progress_hooks': [my_progress_hook]
    }

    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))
    cookie_path = os.path.join(base_path, COOKIE_FILE)

    if os.path.exists(cookie_path):
        logger_func(f"   🍪 File '{COOKIE_FILE}' ditemukan, mencoba download dengan autentikasi.")
        ydl_opts['cookiefile'] = cookie_path
    else:
        logger_func(f"   ⚠️ File '{COOKIE_FILE}' tidak ditemukan. Melanjutkan download tanpa autentikasi.")
        logger_func(f"     Jika download gagal, buat file '{COOKIE_FILE}' sesuai petunjuk di README.txt")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return output_path, info
    except Exception as e:
        logger_func(f"❌ ERROR saat mengunduh video: {str(e)}"); return None, None


def transcribe_audio(audio_path, whisper_model, model_name, logger_func=print):
    try:
        result = whisper_model.transcribe(audio_path, verbose=False, word_timestamps=True)
        return result
    except Exception as e:
        logger_func(f"❌ ERROR saat transkripsi: {e}"); return None

# --- PENAMBAHAN BARU: Fungsi untuk membuat file subtitle .srt ---
def generate_srt_file(transcription_result, output_srt_path, logger_func=print):
    logger_func("   📄 Membuat file subtitle (.srt)...")
    try:
        with open(output_srt_path, 'w', encoding='utf-8') as srt_file:
            for i, segment in enumerate(transcription_result['segments']):
                start_time = segment['start']
                end_time = segment['end']
                text = segment['text'].strip()

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
        logger_func(f"   ❌ Gagal membuat file subtitle: {e}")
        return False

def get_clips_from_gemini(transcript_text, gemini_model_name, logger_func=print):
    prompt = f"""
    Anda adalah seorang editor video profesional dan ahli strategi konten viral yang terobsesi dengan "hook" (kail pancing) di 3 detik pertama. Tugas Anda adalah menganalisis transkrip video di dalam tag `<transcript>` dan mengidentifikasi momen-momen emas yang paling berpotensi FYP.
    ATURAN UTAMA:
    1. HOOK ADАLAH SEGALANYA: Setiap klip yang Anda sarankan HARUS dimulai dengan hook yang sangat kuat. Jika segmen tidak memiliki hook, JANGAN JADIKAN KLIP.
    2. KUALITAS, BUKAN KUANTITAS: Fokus hanya pada momen viral. Lebih baik 2 klip sempurna daripada 7 klip biasa.
    3. DURASI IDEAL: 30-60 detik.
    4. OUTPUT JSON: Harus berupa format JSON valid `[ ... ]`. Setiap objek dalam array harus memiliki keys: "start_time", "end_time", "title", "hashtags", dan "editing_style".
    <transcript>{transcript_text}</transcript>
    INSTRUKSI SPESIFIK UNTUK SETIAP KLIP:
    1. Cari Hook: Identifikasi pertanyaan, pernyataan kontroversial, momen emosional, atau klimaks yang kuat sebagai titik awal.
    2. Tentukan Waktu (WAJIB):
       - "start_time": Harus TEPAT DI AWAL HOOK.
       - "end_time": Harus sekitar 30-60 detik setelah "start_time" untuk menyelesaikan poin atau gagasan.
       - Keduanya HARUS dalam format "HH:MM:SS".
    3. Buat Metadata: Buat "title" yang clickbait, 3 "hashtags" yang relevan, dan tentukan "editing_style" (pilih antara 'dynamic' atau 'informative').
    """
    try:
        model = genai.GenerativeModel(gemini_model_name)
        response = model.generate_content(prompt)
        json_match = re.search(r'```json\s*(\[.*\])\s*```', response.text, re.DOTALL) or re.search(r'\[.*\]', response.text, re.DOTALL)
        if not json_match:
            logger_func("❌ ERROR: AI (klip) tidak memberikan output JSON yang valid."); logger_func(f"   Jawaban AI: {response.text}"); return []
        json_str = json_match.group(1) if len(json_match.groups()) > 0 else json_match.group(0)
        clips = json.loads(json_str)
        logger_func(f"✅ AI (klip) merekomendasikan {len(clips)} klip dengan hook yang kuat."); return clips
    except Exception as e:
        logger_func(f"❌ ERROR saat analisis AI (klip): {e}");
        logger_func(f"   Jawaban AI yang menyebabkan error: {response.text if 'response' in locals() else 'Tidak ada respons'}")
        return []

def get_summary_clips_from_gemini(transcript_text, video_duration, gemini_model_name, logger_func=print):
    prompt = f"""
    Anda adalah seorang editor video ahli yang bertugas membuat ringkasan video yang menarik dari sebuah video panjang.
    Tugas Anda adalah menganalisis transkrip di dalam tag `<transcript>` dan membuat sebuah video ringkasan yang padat dan menarik.

    ATURAN UTAMA:
    1.  **Buat Hook Kuat**: Identifikasi momen paling menarik, kontroversial, atau puncak emosi dalam 3-5 detik pertama. Ini akan menjadi klip pembuka.
    2.  **Pilih Poin Inti**: Pilih 3-5 segmen paling penting yang merangkum keseluruhan isi video. Setiap segmen harus jelas dan memberikan nilai.
    3.  **Rekomendasi Thumbnail**: Pilih satu momen dalam video (waktu spesifik) yang visualnya paling menarik untuk dijadikan thumbnail.
    4.  **Judul Ringkasan**: Buat satu judul yang menarik untuk video ringkasan ini.
    5.  **OUTPUT JSON WAJIB**: Jawaban Anda HARUS dalam format JSON yang valid. Jangan tambahkan teks lain di luar blok JSON.

    Format JSON yang Diinginkan:
    ```json
    {{
      "title": "Judul Ringkasan Video yang Menarik",
      "hook": {{
        "start_time": "HH:MM:SS",
        "end_time": "HH:MM:SS"
      }},
      "main_clips": [
        {{
          "start_time": "HH:MM:SS",
          "end_time": "HH:MM:SS"
        }},
        {{
          "start_time": "HH:MM:SS",
          "end_time": "HH:MM:SS"
        }}
      ],
      "thumbnail_time": "HH:MM:SS"
    }}
    ```
    <transcript>{transcript_text}</transcript>
    """
    try:
        model = genai.GenerativeModel(gemini_model_name)
        response = model.generate_content(prompt)
        json_match = re.search(r'```json\s*(\{.*\})\s*```', response.text, re.DOTALL) or re.search(r'(\{.*\})', response.text, re.DOTALL)
        if not json_match:
            logger_func("❌ ERROR: AI (summary) tidak memberikan output JSON yang valid."); logger_func(f"   Jawaban AI: {response.text}"); return None
        json_str = json_match.group(1) if len(json_match.groups()) > 0 else json_match.group(0)
        summary_data = json.loads(json_str)
        logger_func("✅ AI (summary) berhasil membuat rencana video ringkasan."); return summary_data
    except Exception as e:
        logger_func(f"❌ ERROR saat analisis AI (summary): {e}");
        logger_func(f"   Jawaban AI yang menyebabkan error: {response.text if 'response' in locals() else 'Tidak ada respons'}")
        return None

def get_paraphrased_title_from_gemini(original_title, gemini_model_name, logger_func=print):
    prompt = f"""
    Anda adalah seorang ahli branding media sosial yang jago membuat judul video viral.
    Tugas Anda adalah menulis ulang judul video ini agar terdengar lebih keren, menarik, dan kekinian, namun tetap menjaga makna aslinya.
    Buat agar judul cocok untuk konten video pendek yang akan di-upload ulang.
    JUDUL ASLI: "{original_title}"
    ATURAN:
    - Gunakan bahasa yang santai dan memancing rasa ingin tahu.
    - Boleh tambahkan 1-2 emoji yang relevan.
    - Output HANYA judul barunya saja, tanpa tanda kutip atau teks tambahan apapun.
    """
    try:
        model = genai.GenerativeModel(gemini_model_name)
        response = model.generate_content(prompt)
        new_title = response.text.strip().replace('"', '')
        return new_title if new_title else None
    except Exception as e:
        logger_func(f"   ❌ Gagal membuat judul dengan AI: {e}"); return None

def embed_thumbnail(video_path, thumb_path, logger_func):
    try:
        logger_func("   📎 Menyematkan thumbnail ke video...")
        output_path = video_path.replace(".mp4", "_thumb.mp4")

        input_video = ffmpeg.input(video_path)
        input_thumb = ffmpeg.input(thumb_path)

        (
            ffmpeg
            .output(input_video, input_thumb, output_path, **{'c': 'copy', 'map': '0', 'map': '1', 'disposition:v:1': 'attached_pic'})
            .run(overwrite_output=True, quiet=True)
        )

        os.remove(video_path)
        os.rename(output_path, video_path)
        logger_func("   ✅ Thumbnail berhasil disematkan.")
    except Exception as e:
        logger_func(f"   ❌ Gagal menyematkan thumbnail: {e}\n{traceback.format_exc()}")

def generate_thumbnail_from_video(video_path, timestamp, output_thumb_path, logger_func):
    logger_func(f"   📸 Membuat thumbnail dari video pada {timestamp}...")
    try:
        (
            ffmpeg
            .input(video_path, ss=timestamp)
            .output(output_thumb_path, vframes=1)
            .run(overwrite_output=True, quiet=True)
        )
        logger_func(f"   ✅ Thumbnail berhasil dibuat: {os.path.basename(output_thumb_path)}")
        return True
    except ffmpeg.Error as e:
        logger_func(f"   ❌ Gagal membuat thumbnail: Perintah ffmpeg gagal.")
        logger_func(e.stderr.decode('utf-8'))
        return False
    except Exception as e:
        logger_func(f"   ❌ Gagal membuat thumbnail: {e}")
        return False

# --- MODIFIKASI: Menambahkan parameter subtitle_file ---
def process_clip(source_video, start_time, end_time, watermark_file, watermark_position, source_text, output_filename, style, music_file, music_volume, effects, remove_original_audio, original_audio_volume, is_short_mode=False, subtitle_file=None, logger_func=print):
    try:
        main_video = ffmpeg.input(source_video, ss=start_time, to=end_time)

        if is_short_mode:
            probe = ffmpeg.probe(source_video)
            video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
            if not video_stream:
                logger_func("❌ ERROR: Tidak dapat menemukan stream video dalam file sumber.")
                return
            w, h = int(video_stream['width']), int(video_stream['height'])
            if abs((w / h) - (9 / 16)) < 0.01:
                processed_video = main_video.video
            else:
                target_h = h; target_w = int(target_h * 9 / 16)
                if target_w > w:
                    target_w = w; target_h = int(target_w * 16/9)
                crop_x = (w - target_w) // 2; crop_y = (h - target_h) // 2
                processed_video = main_video.video.filter('crop', w=target_w, h=target_h, x=crop_x, y=crop_y)
        else:
            probe = ffmpeg.probe(source_video)
            video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
            if not video_stream:
                logger_func("❌ ERROR: Tidak dapat menemukan stream video dalam file sumber.")
                return
            height = int(video_stream['height']); crop_width = int(height * 9 / 16)
            if crop_width % 2 != 0: crop_width -= 1
            crop_x = int((int(video_stream['width']) - crop_width) / 2)
            processed_video = main_video.video.filter('crop', w=crop_width, h=height, x=crop_x, y=0)

        if style == "dynamic":
            processed_video = processed_video.zoompan(z='min(zoom+0.0015,1.15)', d=12*25).filter('eq', contrast=1.1, saturation=1.3).filter('unsharp', luma_msize_x=5, luma_msize_y=5, luma_amount=1.0)
        else:
            processed_video = processed_video.filter('eq', contrast=1.1, saturation=1.25).filter('unsharp', luma_msize_x=5, luma_msize_y=5, luma_amount=0.8)

        if effects.get('mirror'): processed_video = processed_video.hflip()
        if effects.get('grayscale'): processed_video = processed_video.filter('hue', s=0)
        if effects.get('sepia'):
            processed_video = processed_video.filter('colorchannelmixer', rr=0.393, rg=0.769, rb=0.189, gr=0.349, gg=0.686, gb=0.168, br=0.272, bg=0.534, bb=0.131)
        if effects.get('negate'): processed_video = processed_video.filter('negate')
        if effects.get('color_boost'): processed_video = processed_video.filter('eq', saturation=1.8)

        # --- PENAMBAHAN BARU: Logika untuk menambahkan subtitle ---
        if subtitle_file and os.path.exists(subtitle_file):
            logger_func("   ✍️ Menambahkan subtitle ke video klip...")
            escaped_path = subtitle_file.replace('\\', '/').replace(':', '\\:')
            processed_video = processed_video.filter('subtitles', filename=escaped_path, force_style='Fontsize=16,PrimaryColour=&H00FFFFFF,BorderStyle=3,Outline=1,Shadow=1')

        if not is_short_mode and source_text:
            processed_video = processed_video.drawtext(text=source_text, x='(w-text_w)/2', y='h-th-20', fontsize=20, fontcolor='white', box=1, boxcolor='black@0.5', boxborderw=5)

        if watermark_file:
            watermark = ffmpeg.input(watermark_file)
            pos_map = {"Kanan Atas": {'x': 'main_w-overlay_w-10', 'y': '10'}, "Kiri Atas": {'x': '10', 'y': '10'}, "Kanan Bawah": {'x': 'main_w-overlay_w-10', 'y': 'main_h-overlay_h-10'}, "Kiri Bawah": {'x': '10', 'y': 'main_h-overlay_h-10'}, "Tengah": {'x': '(main_w-overlay_w)/2', 'y': '(main_h-overlay_h)/2'}}
            pos = pos_map.get(watermark_position, pos_map["Kanan Atas"])
            processed_video = ffmpeg.overlay(processed_video, watermark, x=pos['x'], y=pos['y'])

        audio_inputs = []
        if not remove_original_audio:
            audio_inputs.append(main_video.audio.filter('volume', original_audio_volume / 100.0))
        if music_file:
            audio_inputs.append(ffmpeg.input(music_file).audio.filter('volume', music_volume / 100.0))
        
        final_audio = None
        if len(audio_inputs) > 1:
            final_audio = ffmpeg.filter(audio_inputs, 'amix', duration='first', dropout_transition=0)
        elif audio_inputs:
            final_audio = audio_inputs[0]

        if is_short_mode:
            speed = 1.15; processed_video = processed_video.filter('setpts', f'{1/speed}*PTS')
            if final_audio: final_audio = final_audio.filter('atempo', speed)

        if final_audio:
            final_output = ffmpeg.output(processed_video, final_audio, output_filename, vcodec='libx264', acodec='aac', preset='fast', crf=23)
        else:
            final_output = ffmpeg.output(processed_video, output_filename, vcodec='libx264', preset='fast', crf=23)

        final_output.run(overwrite_output=True, quiet=True)

    except ffmpeg.Error as e:
        logger_func(f"❌ ERROR saat memproses klip: Perintah ffmpeg gagal."); logger_func(e.stderr.decode('utf-8'))
    except Exception as e:
        logger_func(f"❌ TERJADI ERROR LAIN saat memproses klip: {e}\n{traceback.format_exc()}")

# --- MODIFIKASI: Menambahkan parameter transcription_result ---
def process_long_simple_video(source_video, all_clips, watermark_file, watermark_position, output_filename, style, music_file, music_volume, effects, remove_original_audio, original_audio_volume, source_text, transcription_result=None, logger_func=print):
    try:
        logger_func("   🎬 Memulai proses penggabungan klip ringkasan...")
        clip_streams = []
        for clip in all_clips:
            clip_streams.append(ffmpeg.input(source_video, ss=clip['start_time'], to=clip['end_time']))

        if not clip_streams:
            logger_func("   ❌ Tidak ada klip untuk digabungkan."); return

        concatenated_video = ffmpeg.concat(*[s.video for s in clip_streams], v=1, a=0).filter('setpts', 'PTS-STARTPTS')
        concatenated_audio = ffmpeg.concat(*[s.audio for s in clip_streams], v=0, a=1).filter('asetpts', 'PTS-STARTPTS')

        if style == "dynamic":
            processed_video = concatenated_video.zoompan(z='min(zoom+0.0015,1.15)', d=12*25).filter('eq', contrast=1.1, saturation=1.3).filter('unsharp', luma_msize_x=5, luma_msize_y=5, luma_amount=1.0)
        else:
            processed_video = concatenated_video.filter('eq', contrast=1.1, saturation=1.25).filter('unsharp', luma_msize_x=5, luma_msize_y=5, luma_amount=0.8)

        if effects.get('mirror'): processed_video = processed_video.hflip()
        if effects.get('grayscale'): processed_video = processed_video.filter('hue', s=0)
        if effects.get('sepia'):
            processed_video = processed_video.filter('colorchannelmixer', rr=0.393, rg=0.769, rb=0.189, gr=0.349, gg=0.686, gb=0.168, br=0.272, bg=0.534, bb=0.131)
        if effects.get('negate'): processed_video = processed_video.filter('negate')
        if effects.get('color_boost'): processed_video = processed_video.filter('eq', saturation=1.8)
        
        # --- PENAMBAHAN BARU: Logika subtitle untuk video ringkasan ---
        temp_srt_path = None
        if transcription_result:
            logger_func("   ✍️ Menyesuaikan subtitle untuk video ringkasan...")
            # Buat file srt baru dengan timestamp yang disesuaikan
            temp_srt_path = os.path.join(os.path.dirname(output_filename), f"_temp_sub_{int(time.time())}.srt")
            time_offset = 0.0
            total_duration_map = {}
            for i, clip in enumerate(all_clips):
                start_s = sum(x * float(t) for x, t in zip([3600, 60, 1], clip['start_time'].split(":")))
                end_s = sum(x * float(t) for x, t in zip([3600, 60, 1], clip['end_time'].split(":")))
                total_duration_map[i] = {'start_s': start_s, 'end_s': end_s, 'offset': time_offset}
                time_offset += (end_s - start_s)
            
            adjusted_segments = []
            for seg in transcription_result['segments']:
                for i, clip_info in total_duration_map.items():
                    if seg['start'] >= clip_info['start_s'] and seg['end'] <= clip_info['end_s']:
                        new_seg = seg.copy()
                        new_seg['start'] = seg['start'] - clip_info['start_s'] + clip_info['offset']
                        new_seg['end'] = seg['end'] - clip_info['start_s'] + clip_info['offset']
                        adjusted_segments.append(new_seg)
                        break
            
            adjusted_result = {'segments': adjusted_segments}
            if generate_srt_file(adjusted_result, temp_srt_path, logger_func):
                escaped_path = temp_srt_path.replace('\\', '/').replace(':', '\\:')
                processed_video = processed_video.filter('subtitles', filename=escaped_path, force_style='Fontsize=22,PrimaryColour=&H00FFFFFF,BorderStyle=3,Outline=1,Shadow=1')
            else:
                temp_srt_path = None # Gagal membuat srt

        if source_text:
            processed_video = processed_video.drawtext(text=source_text, x='(w-text_w)/2', y='h-th-20', fontsize=24, fontcolor='white', box=1, boxcolor='black@0.5', boxborderw=5)

        if watermark_file:
            watermark = ffmpeg.input(watermark_file)
            pos_map = {"Kanan Atas": {'x': 'main_w-overlay_w-10', 'y': '10'}, "Kiri Atas": {'x': '10', 'y': '10'}, "Kanan Bawah": {'x': 'main_w-overlay_w-10', 'y': 'main_h-overlay_h-10'}, "Kiri Bawah": {'x': '10', 'y': 'main_h-overlay_h-10'},"Tengah": {'x': '(main_w-overlay_w)/2', 'y': '(main_h-overlay_h)/2'}}
            pos = pos_map.get(watermark_position, pos_map["Kanan Atas"])
            processed_video = ffmpeg.overlay(processed_video, watermark, x=pos['x'], y=pos['y'])

        audio_inputs = []
        if not remove_original_audio:
            audio_inputs.append(concatenated_audio.filter('volume', original_audio_volume / 100.0))
        if music_file:
            audio_inputs.append(ffmpeg.input(music_file).audio.filter('volume', music_volume / 100.0))

        final_audio = None
        if len(audio_inputs) > 1:
            final_audio = ffmpeg.filter(audio_inputs, 'amix', duration='longest', dropout_transition=0)
        elif audio_inputs:
            final_audio = audio_inputs[0]

        if final_audio:
            final_output = ffmpeg.output(processed_video, final_audio, output_filename, vcodec='libx264', acodec='aac', preset='fast', crf=23)
        else:
            final_output = ffmpeg.output(processed_video, output_filename, vcodec='libx264', preset='fast', crf=23)

        final_output.run(overwrite_output=True, quiet=True)
        logger_func(f"   ✅ Video ringkasan berhasil dibuat: {os.path.basename(output_filename)}")

    except ffmpeg.Error as e:
        logger_func(f"❌ ERROR saat memproses video ringkasan: Perintah ffmpeg gagal."); logger_func(e.stderr.decode('utf-8'))
    except Exception as e:
        logger_func(f"❌ TERJADI ERROR LAIN saat memproses video ringkasan: {e}\n{traceback.format_exc()}")
    finally:
        if temp_srt_path and os.path.exists(temp_srt_path):
            os.remove(temp_srt_path)

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

        self.output_folder, self.watermark_file, self.music_file = StringVar(), StringVar(), StringVar()
        self.watermark_full_path, self.music_full_path, self.device_id_var = "", "", StringVar()
        self.log_queue, self.license_queue = queue.Queue(), queue.Queue()
        self.whisper_model_selection = StringVar(value="base")
        self.effects_vars = { 'mirror': BooleanVar(), 'grayscale': BooleanVar(), 'sepia': BooleanVar(), 'negate': BooleanVar(), 'color_boost': BooleanVar() }
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
        self.is_long_simple_mode = BooleanVar(value=False)
        self.long_simple_add_source = BooleanVar(value=False)
        self.enable_transcription = BooleanVar(value=False)
        # --- PENAMBAHAN BARU: Variabel untuk burn-in subtitle ---
        self.burn_subtitles = BooleanVar(value=False)

        self.setup_ui()
        self.root.after(100, self.process_log_queue)
        self.root.after(200, self.process_license_queue)
        self.root.after(500, self._initial_license_check)

    def setup_ui(self):
        main_frame = Frame(self.root, padx=10, pady=10); main_frame.pack(fill="both", expand=True)
        content_wrapper = Frame(main_frame); content_wrapper.pack(fill="both", expand=True)
        main_content_frame = Frame(content_wrapper); main_content_frame.pack()
        left_container = Frame(main_content_frame, width=420); left_container.pack(side="left", fill="y", padx=(0, 5))
        left_container.pack_propagate(False)
        canvas = Canvas(left_container, highlightthickness=0)
        scrollbar = Scrollbar(left_container, orient="vertical", command=canvas.yview)
        scrollable_frame = Frame(canvas)
        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        right_column = Frame(main_content_frame); right_column.pack(side="left", fill="y")

        license_lf = LabelFrame(scrollable_frame, text="Manajemen Lisensi", font=("Helvetica", 10, "bold"), padx=10, pady=10); license_lf.pack(fill="x", pady=(5,10), padx=10)
        id_entry = Entry(license_lf, textvariable=self.device_id_var, state="readonly"); id_entry.grid(row=0, column=0, columnspan=2, sticky="ew")
        Button(license_lf, text="Salin ID", command=self.get_and_copy_uuid).grid(row=0, column=2, padx=(10,0))
        Label(license_lf, text="Status:").grid(row=1, column=0, sticky="w", pady=(5,0))
        self.license_status_label = Label(license_lf, text="- MENGECEK -", font=("Helvetica", 10, "bold"), fg="grey"); self.license_status_label.grid(row=1, column=1, sticky="w", pady=(5,0))
        license_lf.columnconfigure(1, weight=1)

        api_lf = LabelFrame(scrollable_frame, text="Konfigurasi API Key", font=("Helvetica", 10, "bold"), padx=10, pady=10); api_lf.pack(fill="x", pady=(0,10), padx=10)
        Checkbutton(api_lf, text="Gunakan API Key Sendiri", variable=self.use_custom_api_key, command=self.toggle_api_key_entry).pack(anchor="w")
        Label(api_lf, text="Masukkan API Key Gemini Anda:").pack(anchor="w", pady=(5,0))
        self.api_key_entry = Entry(api_lf, textvariable=self.custom_api_key, state="disabled"); self.api_key_entry.pack(fill="x")

        long_simple_lf = LabelFrame(scrollable_frame, text="Mode Long-Simple (Video Ringkasan)", font=("Helvetica", 10, "bold"), padx=10, pady=10); long_simple_lf.pack(fill="x", pady=(0,10), padx=10)
        self.long_simple_cb = Checkbutton(long_simple_lf, text="Aktifkan Mode Long-Simple", variable=self.is_long_simple_mode)
        self.long_simple_cb.pack(anchor="w")
        self.long_simple_source_cb = Checkbutton(long_simple_lf, text="Tambahkan Teks Sumber Video", variable=self.long_simple_add_source)
        self.long_simple_source_cb.pack(anchor="w", padx=15)
        Label(long_simple_lf, text="Fitur ini akan mempersingkat video panjang menjadi\nringkasan 16:9 secara otomatis oleh AI.", justify="left", fg="gray50").pack(anchor="w", padx=2, pady=(5,5))

        scraper_lf = LabelFrame(scrollable_frame, text="Mode Scraper Shorts", font=("Helvetica", 10, "bold"), padx=10, pady=10); scraper_lf.pack(fill="x", pady=(0,10), padx=10)
        Checkbutton(scraper_lf, text="Aktifkan Mode Scraper Shorts", variable=self.is_shorts_scraper_mode).pack(anchor="w")
        Checkbutton(scraper_lf, text="Gunakan AI untuk Judul Baru (lebih lambat)", variable=self.use_ai_for_shorts_title).pack(anchor="w")
        Label(scraper_lf, text="URL Channel YouTube:").pack(anchor="w", pady=(5,0))
        Entry(scraper_lf, textvariable=self.scrape_channel_url).pack(fill="x")
        count_frame = Frame(scraper_lf); count_frame.pack(fill="x", pady=(5,0))
        Label(count_frame, text="Jumlah Shorts:").pack(side="left")
        Entry(count_frame, textvariable=self.scrape_count, width=5).pack(side="left", padx=5)
        self.scrape_button = Button(count_frame, text="🔎 Cari & Tempel Link", command=self.start_scraping_thread); self.scrape_button.pack(side="left", expand=True, fill="x")

        file_lf = LabelFrame(scrollable_frame, text="File & Aset", font=("Helvetica", 10, "bold"), padx=10, pady=10); file_lf.pack(fill="x", pady=(0,10), padx=10)
        Button(file_lf, text="Pilih Folder Output", command=self.select_output_folder).pack(fill="x")
        Label(file_lf, textvariable=self.output_folder, fg="blue", wraplength=350).pack(anchor="w", padx=2, pady=(0,5))
        Button(file_lf, text="Pilih Watermark", command=self.select_watermark).pack(fill="x")
        Label(file_lf, textvariable=self.watermark_file, fg="blue", wraplength=350).pack(anchor="w", padx=2)
        pos_frame = Frame(file_lf); pos_frame.pack(fill="x", pady=(2, 5))
        Label(pos_frame, text="Posisi:").pack(side="left")
        OptionMenu(pos_frame, self.watermark_position, *["Kanan Atas", "Kiri Atas", "Kanan Bawah", "Kiri Bawah", "Tengah"]).pack(side="left", fill="x", expand=True)
        Button(file_lf, text="Pilih Gambar Thumbnail Kustom", command=self.select_thumbnail).pack(fill="x")
        Label(file_lf, textvariable=self.thumbnail_file, fg="blue", wraplength=350).pack(anchor="w", padx=2, pady=(0,5))
        Checkbutton(file_lf, text="Gunakan Thumbnail Kustom (untuk mode non-AI)", variable=self.use_custom_thumbnail).pack(anchor="w")

        audio_lf = LabelFrame(scrollable_frame, text="Pengaturan Audio", font=("Helvetica", 10, "bold"), padx=10, pady=10); audio_lf.pack(fill="x", pady=(0,10), padx=10)
        Checkbutton(audio_lf, text="Hapus Suara Asli Video", variable=self.remove_original_audio_var, command=self.toggle_original_audio_slider).pack(anchor="w")
        original_volume_frame = Frame(audio_lf); original_volume_frame.pack(fill="x", pady=2, padx=5)
        Label(original_volume_frame, text="Volume Asli:").pack(side="left")
        self.original_audio_slider = Scale(original_volume_frame, from_=0, to=100, orient="horizontal", variable=self.original_audio_volume_var, command=self.update_original_volume_label); self.original_audio_slider.pack(side="left", expand=True, fill="x", padx=5)
        Label(original_volume_frame, textvariable=self.original_volume_display_var, width=4).pack(side="left")
        Button(audio_lf, text="Pilih Musik Latar", command=self.select_music).pack(fill="x", pady=(5,0))
        Label(audio_lf, textvariable=self.music_file, fg="blue", wraplength=350).pack(anchor="w", padx=2)
        music_volume_frame = Frame(audio_lf); music_volume_frame.pack(fill="x", pady=2, padx=5)
        Label(music_volume_frame, text="Volume Musik:").pack(side="left")
        self.music_slider = Scale(music_volume_frame, from_=0, to=100, orient="horizontal", variable=self.music_volume_var, command=self.update_music_volume_label, state="disabled"); self.music_slider.pack(side="left", expand=True, fill="x", padx=5)
        Label(music_volume_frame, textvariable=self.volume_display_var, width=4).pack(side="left")
        
        transcription_lf = LabelFrame(right_column, text="Pengaturan Transkripsi & Subtitle", font=("Helvetica", 10, "bold"), padx=10, pady=10); transcription_lf.pack(fill="x", pady=(0, 10))
        Checkbutton(transcription_lf, text="Aktifkan Transkripsi (Wajib untuk Mode AI & Subtitle)", variable=self.enable_transcription, command=self.toggle_ai_modes).pack(anchor="w")
        self.transcription_options_frame = Frame(transcription_lf); self.transcription_options_frame.pack(fill='x', pady=2, padx=15)
        Label(self.transcription_options_frame, text="Akurasi Transkripsi:").pack(side="left", padx=(0,10))
        self.model_option_menu = OptionMenu(self.transcription_options_frame, self.whisper_model_selection, *["base", "small", "medium"]); self.model_option_menu.pack(side="left")
        # --- PENAMBAHAN BARU: Checkbox untuk burn-in subtitle ---
        self.burn_subtitle_cb = Checkbutton(self.transcription_options_frame, text="Tambahkan Subtitle ke Video (Burn-in)", variable=self.burn_subtitles); self.burn_subtitle_cb.pack(side='left', padx=(20,0))
        
        cut_mode_lf = LabelFrame(right_column, text="Mode Pemotongan Video (Long-to-Short)", font=("Helvetica", 10, "bold"), padx=10, pady=10); cut_mode_lf.pack(fill="x", pady=(0, 10))
        self.cut_mode_auto_rb = Radiobutton(cut_mode_lf, text="Otomatis (AI)", variable=self.cut_mode, value="otomatis", command=self.toggle_manual_cut_fields); self.cut_mode_auto_rb.pack(anchor="w")
        Radiobutton(cut_mode_lf, text="Manual (Custom Cut)", variable=self.cut_mode, value="manual", command=self.toggle_manual_cut_fields).pack(anchor="w")
        self.manual_fields_frame = Frame(cut_mode_lf, padx=15); self.manual_fields_frame.pack(fill="x")
        Label(self.manual_fields_frame, text="Waktu Mulai (HH:MM:SS):").pack(anchor="w", pady=(5,0))
        self.start_entry = Entry(self.manual_fields_frame, textvariable=self.manual_start_time); self.start_entry.pack(fill="x")
        Label(self.manual_fields_frame, text="Waktu Selesai (HH:MM:SS):").pack(anchor="w", pady=(5,0))
        self.end_entry = Entry(self.manual_fields_frame, textvariable=self.manual_end_time); self.end_entry.pack(fill="x")

        effects_lf = LabelFrame(right_column, text="Efek Video (Berlaku untuk semua mode)", font=("Helvetica", 10, "bold"), padx=10, pady=10); effects_lf.pack(fill="x", pady=(0,10))
        Checkbutton(effects_lf, text="Mirror (Cermin Horizontal)", variable=self.effects_vars['mirror']).pack(anchor="w")
        Checkbutton(effects_lf, text="Grayscale (Hitam Putih)", variable=self.effects_vars['grayscale']).pack(anchor="w")
        Checkbutton(effects_lf, text="Sepia", variable=self.effects_vars['sepia']).pack(anchor="w")
        Checkbutton(effects_lf, text="Negate (Warna Negatif)", variable=self.effects_vars['negate']).pack(anchor="w")
        Checkbutton(effects_lf, text="Color Boost (Saturasi Tinggi)", variable=self.effects_vars['color_boost']).pack(anchor="w")

        url_lf = LabelFrame(right_column, text="Masukkan Link Video (satu per baris)", font=("Helvetica", 10, "bold"), padx=10, pady=10); url_lf.pack(fill="x")
        self.url_text = Text(url_lf, relief="solid", borderwidth=1, font=("Courier", 10), height=5); self.url_text.pack(fill="both", expand=True, pady=2)

        action_lf = LabelFrame(right_column, text="Kontrol & Log Proses", font=("Helvetica", 10, "bold"), padx=10, pady=10); action_lf.pack(fill="both", expand=True, pady=(10,0))
        control_frame = Frame(action_lf); control_frame.pack(fill="x")
        self.start_button = Button(control_frame, text="🚀 Mulai Proses Video", command=self.start_processing_thread, bg="#28a745", fg="white", font=("Helvetica", 12, "bold"), relief="raised"); self.start_button.pack(side="left", fill="x", expand=True, ipady=8)
        self.stop_button = Button(control_frame, text="❌ Stop Proses", command=self.stop_processing, bg="#dc3545", fg="white", font=("Helvetica", 12, "bold"), relief="raised")
        self.clear_log_button = Button(action_lf, text="🗑️ Bersihkan Log", command=self.clear_log, font=("Helvetica", 8)); self.clear_log_button.pack(fill="x", pady=4)
        self.progress_bar = Progressbar(action_lf, orient="horizontal", length=100, mode="determinate"); self.progress_bar.pack(fill="x", pady=8)
        log_frame = Frame(action_lf); log_frame.pack(fill="both", expand=True)
        self.log_text = Text(log_frame, state='disabled', wrap='word', relief="solid", borderwidth=1)
        scrollbar = Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=scrollbar.set); scrollbar.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)
        
        self.toggle_manual_cut_fields(); self.toggle_original_audio_slider()
        self.toggle_ai_modes()

    def toggle_ai_modes(self):
        is_enabled = self.enable_transcription.get()
        state = "normal" if is_enabled else "disabled"

        self.model_option_menu.config(state=state)
        self.burn_subtitle_cb.config(state=state) # --- MODIFIKASI ---
        if not is_enabled: self.burn_subtitles.set(False) # --- MODIFIKASI ---

        for child in self.transcription_options_frame.winfo_children():
            if isinstance(child, Label): child.config(state=state)

        self.long_simple_cb.config(state=state)
        self.long_simple_source_cb.config(state=state)
        if not is_enabled: self.is_long_simple_mode.set(False)

        self.cut_mode_auto_rb.config(state=state)
        if not is_enabled and self.cut_mode.get() == "otomatis":
            self.cut_mode.set("manual")
        
        self.toggle_manual_cut_fields()

    def toggle_api_key_entry(self):
        self.api_key_entry.config(state="normal" if self.use_custom_api_key.get() else "disabled")

    def toggle_original_audio_slider(self):
        self.original_audio_slider.config(state="disabled" if self.remove_original_audio_var.get() else "normal")

    def select_thumbnail(self):
        file = filedialog.askopenfilename(title="Pilih Gambar Thumbnail", filetypes=[("Image Files", "*.png;*.jpg;*.jpeg")])
        if file: self.thumbnail_full_path = file; self.thumbnail_file.set(f"Thumbnail: {os.path.basename(file)}")

    def clear_log(self):
        self.log_text.config(state='normal'); self.log_text.delete('1.0', 'end'); self.log_text.config(state='disabled')

    def stop_processing(self):
        self.log("\n🛑 PERINTAH STOP DITERIMA! Menghentikan proses..."); self.stop_event.set(); self.stop_button.pack_forget()
    
    def start_scraping_thread(self):
        if not self.scrape_channel_url.get(): messagebox.showerror("Error", "Masukkan URL Channel YouTube."); return
        self.scrape_button.config(state="disabled", text="Mencari..."); threading.Thread(target=self.scrape_shorts_from_channel, daemon=True).start()

    def scrape_shorts_from_channel(self):
        channel_url = self.scrape_channel_url.get(); count = self.scrape_count.get()
        self.log(f"\n🔎 Mulai mencari {count} shorts dari channel: {channel_url}")
        ydl_opts = {'quiet': True, 'extract_flat': True, 'force_generic_extractor': True, 'playlistend': count * 3 }
        found_urls = []
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(f"{channel_url}/shorts", download=False)
                if 'entries' in result:
                    self.log(f"   Menganalisis {len(result['entries'])} video terbaru...")
                    for entry in result['entries']:
                        if len(found_urls) >= count: break
                        found_urls.append(f"https://www.youtube.com/watch?v={entry['id']}")
                        self.log(f"   ✅ Ditemukan Short: {entry['title']}")
                else: self.log("   ❌ Tidak ada video ditemukan. Pastikan URL channel benar.")
            def update_ui():
                self.url_text.delete("1.0", "end"); self.url_text.insert("1.0", "\n".join(found_urls))
                self.log(f"✅ Berhasil menempelkan {len(found_urls)} link video Shorts.")
                self.scrape_button.config(state="normal", text="🔎 Cari & Tempel Link")
            self.root.after(0, update_ui)
        except Exception as e:
            self.log(f"❌ Gagal scraping: {e}"); self.scrape_button.config(state="normal", text="🔎 Cari & Tempel Link")

    def toggle_manual_cut_fields(self):
        self.start_entry.config(state="normal" if self.cut_mode.get() == "manual" else "disabled")
        self.end_entry.config(state="normal" if self.cut_mode.get() == "manual" else "disabled")
    
    def update_music_volume_label(self, value): self.volume_display_var.set(f"{int(float(value))}%")
    def update_original_volume_label(self, value): self.original_volume_display_var.set(f"{int(float(value))}%")
    
    def get_and_copy_uuid(self):
        try:
            device_id = machineid.id()
            self.device_id_var.set(device_id); self.root.clipboard_clear(); self.root.clipboard_append(device_id)
            messagebox.showinfo("ID Disalin", "ID Perangkat Anda telah disalin ke clipboard.")
        except Exception as e: messagebox.showerror("Error", f"Gagal mendapatkan ID Perangkat: {e}")

    def _initial_license_check(self):
        threading.Thread(target=lambda: self.license_queue.put(verify_license(lambda msg: None)), daemon=True).start()
    
    def process_license_queue(self):
        try:
            is_valid, device_id = self.license_queue.get_nowait()
            if device_id: self.device_id_var.set(device_id)
            self.license_status_label.config(text="TERVALIDASI" if is_valid else "TIDAK VALID | Hubungi: @nezastore", fg="green" if is_valid else "red")
        except queue.Empty: self.root.after(200, self.process_license_queue)

    def select_output_folder(self):
        folder = filedialog.askdirectory(title="Pilih Folder Output")
        if folder: self.output_folder.set(f"Folder Output: {folder}")
    
    def select_watermark(self):
        file = filedialog.askopenfilename(title="Pilih File Watermark", filetypes=[("Image Files", "*.png;*.jpg;*.jpeg")])
        if file: self.watermark_full_path = file; self.watermark_file.set(f"Watermark: {os.path.basename(file)}")

    def select_music(self):
        file = filedialog.askopenfilename(title="Pilih File Musik", filetypes=[("Audio Files", "*.mp3;*.wav;*.m4a")])
        if file: 
            self.music_full_path = file; self.music_file.set(f"Musik: {os.path.basename(file)}")
            self.music_slider.config(state="normal")

    def log(self, message): self.log_queue.put(message)
    def process_log_queue(self):
        try:
            while True:
                message = self.log_queue.get_nowait()
                self.log_text.config(state='normal'); self.log_text.insert('end', message + '\n'); self.log_text.see('end'); self.log_text.config(state='disabled')
        except queue.Empty: self.root.after(100, self.process_log_queue)
    
    def start_processing_thread(self):
        if self.burn_subtitles.get() and not self.enable_transcription.get():
            messagebox.showerror("Error", "Untuk menambahkan subtitle, Transkripsi harus diaktifkan terlebih dahulu.")
            return
        if self.is_long_simple_mode.get() and not self.enable_transcription.get():
            messagebox.showerror("Error", "Mode Long-Simple memerlukan Transkripsi.\nSilakan aktifkan Transkripsi terlebih dahulu.")
            return
        if self.cut_mode.get() == 'otomatis' and not self.enable_transcription.get():
            messagebox.showerror("Error", "Mode Otomatis (AI) memerlukan Transkripsi.\nSilakan aktifkan Transkripsi terlebih dahulu.")
            return
        if not self.url_text.get("1.0", "end-1c").strip(): messagebox.showerror("Error", "Masukkan setidaknya satu URL video."); return
        if not self.output_folder.get(): messagebox.showerror("Error", "Pilih folder untuk menyimpan video."); return
        if not self.watermark_full_path: messagebox.showerror("Error", "Pilih file gambar untuk watermark."); return
        if self.use_custom_api_key.get() and not self.custom_api_key.get().strip(): messagebox.showerror("Error", "Kolom API Key masih kosong."); return
        if self.use_custom_thumbnail.get() and not self.thumbnail_full_path and not self.is_long_simple_mode.get(): messagebox.showerror("Error", "Pilih file gambar untuk thumbnail kustom, atau nonaktifkan."); return

        self.start_button.config(state="disabled", text="⏳ Sedang Memproses..."); self.stop_button.pack(side="left", fill="x", expand=True, ipady=8, padx=(5,0))
        self.progress_bar['value'] = 0
        self.stop_event.clear()
        threading.Thread(target=self.run_processing_logic, daemon=True).start()
    
    def update_progress(self, step, total_steps, message):
        self.log(f"\n[LANGKAH {step}/{total_steps}] {message}"); self.progress_bar['value'] = (step / total_steps) * 100

    def run_processing_logic(self):
        is_shorts_mode = self.is_shorts_scraper_mode.get()
        is_long_simple_mode = self.is_long_simple_mode.get()
        total_steps = 6

        if is_long_simple_mode: total_steps = 7
        elif is_shorts_mode:
            total_steps = 3
            if self.burn_subtitles.get(): total_steps += 1
            if self.use_custom_thumbnail.get(): total_steps += 1
        else: # Long-to-short
             if self.burn_subtitles.get() and self.cut_mode.get() == 'manual': total_steps += 1

        try:
            step = 1; self.update_progress(step, total_steps, "Memverifikasi Lisensi..."); step+=1
            if self.stop_event.is_set(): return
            is_valid, _ = verify_license(self.log); 
            if not is_valid: self.log("   Silakan hubungi admin Telegram : @nezastore."); return
            
            step = 2; self.update_progress(step, total_steps, "Memuat Konfigurasi & Model AI..."); step+=1
            if self.stop_event.is_set(): return
            GEMINI_API_KEY = self.custom_api_key.get() if self.use_custom_api_key.get() else None
            GEMINI_MODEL = "gemini-1.5-flash"
            if not GEMINI_API_KEY:
                config = load_remote_config(self.log)
                if not config: return
                GEMINI_API_KEY, GEMINI_MODEL = config.get("api_key"), config.get("gemini_model", "gemini-1.5-flash")
            if not configure_gemini(GEMINI_API_KEY, self.log): return

            output_folder_base = self.output_folder.get().replace("Folder Output: ", "")
            output_folder = os.path.join(output_folder_base, OUTPUT_SUBFOLDER); os.makedirs(output_folder, exist_ok=True)
            video_urls = [url for url in self.url_text.get("1.0", "end-1c").strip().splitlines() if url.strip()]
            
            whisper_model = None
            if self.enable_transcription.get():
                self.update_progress(step, total_steps, "Memuat Model Transkripsi..."); step+=1
                if self.stop_event.is_set(): return
                selected_whisper_model = self.whisper_model_selection.get()
                self.log(f"   Memuat model AI Whisper ({selected_whisper_model})..."); whisper_model = whisper.load_model(selected_whisper_model); self.log("   Model Whisper berhasil dimuat.")
            
            current_step = step
            if is_long_simple_mode:
                self.log("\n🚀 MENJALANKAN MODE LONG-SIMPLE (VIDEO RINGKASAN) 🚀")
                for index, video_url in enumerate(video_urls):
                    if self.stop_event.is_set(): return
                    self.log(f"\n{'='*20} MEMPROSES VIDEO {index+1}/{len(video_urls)} {'='*20}")
                    self.update_progress(current_step, total_steps, f"Mengunduh Video ({index+1}/{len(video_urls)})...")
                    temp_video_filename = f"temp_{int(time.time())}_{index}.mp4"
                    temp_download_path = os.path.join(output_folder_base, temp_video_filename)
                    video_path, info = download_video(video_url, output_path=temp_download_path, logger_func=self.log)
                    if not video_path: continue
                    
                    channel_name = info.get('uploader', 'Unknown Channel')
                    source_text_to_add = f"Sumber: {channel_name}" if self.long_simple_add_source.get() else ""

                    if self.stop_event.is_set(): os.remove(video_path); return
                    self.update_progress(current_step+1, total_steps, f"Transkripsi Audio ({index+1}/{len(video_urls)})...")
                    transcription_result = transcribe_audio(video_path, whisper_model, selected_whisper_model, self.log)
                    if not transcription_result: os.remove(video_path); continue

                    if self.stop_event.is_set(): os.remove(video_path); return
                    self.update_progress(current_step+2, total_steps, f"Membuat Rencana Video Ringkasan dengan AI ({index+1}/{len(video_urls)})...")
                    summary_data = get_summary_clips_from_gemini(transcription_result['text'], info.get('duration', 0), GEMINI_MODEL, self.log)
                    if not summary_data or 'hook' not in summary_data or 'main_clips' not in summary_data:
                        self.log("   🔴 AI gagal membuat rencana ringkasan. Melewati video ini."); os.remove(video_path); continue

                    all_clips_to_process = [summary_data['hook']] + summary_data['main_clips']
                    safe_filename = sanitize_filename(summary_data.get('title', f"Video Ringkasan {index+1}"))
                    output_file = os.path.join(output_folder, f"{safe_filename}.mp4")

                    if self.stop_event.is_set(): os.remove(video_path); return
                    self.update_progress(current_step+3, total_steps, f"Membuat Video Ringkasan ({index+1}/{len(video_urls)})...")
                    process_long_simple_video(source_video=video_path, all_clips=all_clips_to_process, watermark_file=self.watermark_full_path, watermark_position=self.watermark_position.get(), output_filename=output_file, style='informative', music_file=self.music_full_path, music_volume=self.music_volume_var.get(), effects={k: v.get() for k, v in self.effects_vars.items()}, remove_original_audio=self.remove_original_audio_var.get(), original_audio_volume=self.original_audio_volume_var.get(), source_text=source_text_to_add, transcription_result=transcription_result if self.burn_subtitles.get() else None, logger_func=self.log)

                    if self.stop_event.is_set(): os.remove(video_path); return
                    self.update_progress(current_step+4, total_steps, f"Membuat & Menyematkan Thumbnail AI ({index+1}/{len(video_urls)})...")
                    temp_thumb_path = os.path.join(output_folder_base, TEMP_THUMBNAIL_FILE)
                    thumb_time = summary_data.get('thumbnail_time', '00:00:05')
                    if generate_thumbnail_from_video(video_path, thumb_time, temp_thumb_path, self.log):
                        embed_thumbnail(output_file, temp_thumb_path, self.log)
                        os.remove(temp_thumb_path)

                    os.remove(video_path); self.log(f"   🗑️ File video asli ({os.path.basename(video_path)}) telah dihapus.")
                self.update_progress(total_steps, total_steps, "Semua proses Video Ringkasan selesai!")

            elif is_shorts_mode:
                self.log("\n🚀 MENJALANKAN MODE SCRAPER SHORTS 🚀")
                for index, video_url in enumerate(video_urls):
                    if self.stop_event.is_set(): return
                    self.log(f"\n{'='*20} MEMPROSES SHORT {index+1}/{len(video_urls)} {'='*20}")
                    self.update_progress(current_step, total_steps, f"Mengunduh Short ({index+1}/{len(video_urls)})...")
                    temp_video_filename = f"temp_{int(time.time())}_{index}.mp4"
                    temp_download_path = os.path.join(output_folder_base, temp_video_filename)
                    video_path, info = download_video(video_url, output_path=temp_download_path, logger_func=self.log)
                    if not video_path: continue
                    
                    subtitle_path = None
                    if self.burn_subtitles.get():
                        self.update_progress(current_step+1, total_steps, f"Transkripsi Short ({index+1}/{len(video_urls)})...")
                        transcription_result = transcribe_audio(video_path, whisper_model, self.whisper_model_selection.get(), self.log)
                        if transcription_result:
                            subtitle_path = os.path.join(output_folder_base, f"temp_sub_{int(time.time())}.srt")
                            generate_srt_file(transcription_result, subtitle_path, self.log)
                        else: subtitle_path = None
                    
                    original_title = info.get('title', f'Short_{index+1}')
                    final_title = original_title
                    if self.use_ai_for_shorts_title.get():
                        if self.stop_event.is_set(): os.remove(video_path); return
                        self.log("   🤖 Meminta AI untuk membuat judul baru...")
                        new_title = get_paraphrased_title_from_gemini(original_title, GEMINI_MODEL, self.log)
                        if new_title: final_title = new_title; self.log(f"   ✅ Judul baru dari AI: {final_title}")
                        else: self.log("   ⚠️ Gagal mendapatkan judul dari AI, menggunakan judul asli.")
                    safe_filename = sanitize_filename(final_title)
                    output_file = os.path.join(output_folder, f"{safe_filename}.mp4")

                    if self.stop_event.is_set(): os.remove(video_path); return
                    process_clip(source_video=video_path, start_time="00:00:00", end_time=time.strftime('%H:%M:%S', time.gmtime(info.get('duration', 60))), watermark_file=self.watermark_full_path, watermark_position=self.watermark_position.get(), source_text="", output_filename=output_file, style='informative', music_file=self.music_full_path, music_volume=self.music_volume_var.get(), effects={k: v.get() for k, v in self.effects_vars.items()}, remove_original_audio=self.remove_original_audio_var.get(), original_audio_volume=self.original_audio_volume_var.get(), is_short_mode=True, subtitle_file=subtitle_path, logger_func=self.log)
                    
                    if subtitle_path and os.path.exists(subtitle_path): os.remove(subtitle_path)
                    
                    if self.use_custom_thumbnail.get() and self.thumbnail_full_path:
                        embed_thumbnail(output_file, self.thumbnail_full_path, self.log)
                    os.remove(video_path); self.log(f"   🗑️ File short asli ({os.path.basename(video_path)}) telah dihapus.")
                self.update_progress(total_steps, total_steps, "Semua proses Shorts selesai!")
            
            else: # Mode Long-to-Short
                self.log("\n🚀 MENJALANKAN MODE PEMOTONGAN VIDEO (LONG-TO-SHORT) 🚀")
                for index, video_url in enumerate(video_urls):
                    if self.stop_event.is_set(): return
                    self.log(f"\n{'='*20} MEMPROSES VIDEO {index+1}/{len(video_urls)} {'='*20}")
                    self.update_progress(current_step, total_steps, f"Mengunduh Video ({index+1}/{len(video_urls)})...")
                    temp_video_filename = f"temp_{int(time.time())}_{index}.mp4"
                    temp_download_path = os.path.join(output_folder_base, temp_video_filename)
                    video_path, info = download_video(video_url, output_path=temp_download_path, logger_func=self.log)
                    if not video_path: continue
                    channel_name = info.get('uploader', 'Unknown Channel')
                    
                    transcription_result = None
                    subtitle_path = None
                    if self.cut_mode.get() == "otomatis" or self.burn_subtitles.get():
                        self.update_progress(current_step+1, total_steps, f"Transkripsi Audio ({index+1}/{len(video_urls)})...")
                        transcription_result = transcribe_audio(video_path, whisper_model, self.whisper_model_selection.get(), self.log)
                        if not transcription_result:
                            if self.cut_mode.get() == "otomatis": os.remove(video_path); continue
                        if self.burn_subtitles.get() and transcription_result:
                            subtitle_path = os.path.join(output_folder_base, f"temp_sub_{int(time.time())}.srt")
                            generate_srt_file(transcription_result, subtitle_path, self.log)

                    ai_clips = []
                    if self.cut_mode.get() == "otomatis":
                        if self.stop_event.is_set(): os.remove(video_path); return
                        self.update_progress(current_step+2, total_steps, f"Mencari Klip Viral dengan AI ({index+1}/{len(video_urls)})...")
                        ai_clips = get_clips_from_gemini(transcription_result['text'], GEMINI_MODEL, self.log)
                    else: # Manual
                        start_t, end_t = self.manual_start_time.get(), self.manual_end_time.get()
                        ai_clips = [{"start_time": start_t, "end_time": end_t, "title": f"Klip Manual {start_t} - {end_t}", "hashtags": ["#customclip", "#manualcut"], "editing_style": "informative"}]

                    if not ai_clips: self.log("   🔴 Tidak ada klip untuk diproses."); os.remove(video_path); continue
                    if self.stop_event.is_set(): os.remove(video_path); return
                    
                    self.log(f"   Ditemukan {len(ai_clips)} potensi klip. Memulai rendering...")

                    for i, clip in enumerate(ai_clips):
                        if self.stop_event.is_set(): break
                        safe_filename = sanitize_filename(f"{clip.get('title', f'Klip {i+1}')} {' '.join(clip.get('hashtags', []))}")
                        output_file = os.path.join(output_folder, f"{safe_filename}.mp4")
                        process_clip(source_video=video_path, start_time=clip['start_time'], end_time=clip['end_time'], watermark_file=self.watermark_full_path, watermark_position=self.watermark_position.get(), source_text=f"Sumber: {channel_name}", output_filename=output_file, style=clip.get('editing_style', 'informative'), music_file=self.music_full_path, music_volume=self.music_volume_var.get(), effects={k: v.get() for k, v in self.effects_vars.items()}, remove_original_audio=self.remove_original_audio_var.get(), original_audio_volume=self.original_audio_volume_var.get(), is_short_mode=False, subtitle_file=subtitle_path, logger_func=self.log)
                        if self.use_custom_thumbnail.get() and self.thumbnail_full_path:
                            embed_thumbnail(output_file, self.thumbnail_full_path, self.log)

                    if subtitle_path and os.path.exists(subtitle_path): os.remove(subtitle_path)
                    os.remove(video_path)
                    self.log(f"   🗑️ File video asli ({os.path.basename(video_path)}) telah dihapus.")
                self.update_progress(total_steps, total_steps, "Semua proses video selesai!")
            
            if self.stop_event.is_set(): self.log("\n🛑 Proses dihentikan oleh pengguna.")
            else: self.log("\n🎉🎉🎉 SEMPURNA! SEMUA VIDEO TELAH DIPROSES! 🎉🎉🎉")
        except Exception:
            self.log(f"\n❌ TERJADI ERROR YANG TIDAK DI DUGA PADA SCRIPT ❌\n{traceback.format_exc()}")
        finally:
            self.start_button.config(state="normal", text="🚀 Mulai Proses Video")
            self.stop_button.pack_forget()
            self.progress_bar['value'] = 0

if __name__ == "__main__":
    try:
        root = Tk()
        app = VideoClipperApp(root)
        root.mainloop()
    except Exception as e:
        messagebox.showerror("Fatal Error", f"Gagal memulai aplikasi:\n{traceback.format_exc()}")
        input(f"Gagal memulai aplikasi:\n{traceback.format_exc()}\nTekan Enter untuk keluar...")
