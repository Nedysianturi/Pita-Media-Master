"""
Script Validasi Konfigurasi .env Sistem Pita Media.
Memeriksa kelengkapan dan kevalidan format variabel lingkungan TANPA mencetak/membocorkan nilai rahasia.
"""

import sys
import os
import re
from pathlib import Path
import httpx
from dotenv import dotenv_values

# Pastikan UTF-8 untuk stdout Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"


def mask_secret(val: str) -> str:
    if not val:
        return "[KOSONG]"
    if len(val) <= 8:
        return "****"
    return f"{val[:4]}...{val[-4:]} (panjang: {len(val)} karakter)"


def check_env():
    print("=" * 60)
    print("[*] MEMERIKSA KONFIGURASI FILE .env PITA MEDIA")
    print("=" * 60)

    if not ENV_PATH.exists():
        print("[-] ERROR: File '.env' TIDAK DITEMUKAN!")
        print(f"    Silakan salin template: copy .env.example .env di folder {BASE_DIR}")
        return False

    env_vars = dotenv_values(ENV_PATH)
    all_ok = True

    # 1. Periksa GEMINI_API_KEY
    gemini_key = env_vars.get("GEMINI_API_KEY", "").strip()
    print("\n1. Google Gemini API Key:")
    if not gemini_key:
        print("   [-] GEMINI_API_KEY masih kosong.")
        all_ok = False
    elif gemini_key == "your_gemini_api_key_here":
        print("   [-] GEMINI_API_KEY masih menggunakan teks placeholder contoh.")
        all_ok = False
    elif gemini_key.startswith("AIza") and len(gemini_key) >= 35:
        print(f"   [+] Format Valid: {mask_secret(gemini_key)}")
        # Cek koneksi live ke Gemini API
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={gemini_key}"
            res = httpx.get(url, timeout=10.0)
            if res.status_code == 200:
                print("   [+] Koneksi Google Gemini API: BERHASIL & AKTIF!")
            else:
                err_msg = res.json().get('error', {}).get('message', 'Key mungkin tidak valid/dibatasi')
                print(f"   [!] Gemini API mengembalikan status {res.status_code}: {err_msg}")
                all_ok = False
        except Exception as e:
            print(f"   [!] Tidak dapat menghubungi server Gemini API: {e}")
    else:
        print(f"   [!] Format tidak biasa (umumnya berawalan 'AIza...'): {mask_secret(gemini_key)}")

    # 2. Periksa TELEGRAM_BOT_TOKEN
    tg_token = env_vars.get("TELEGRAM_BOT_TOKEN", "").strip()
    print("\n2. Telegram Bot Token:")
    if not tg_token:
        print("   [-] TELEGRAM_BOT_TOKEN masih kosong.")
        all_ok = False
    elif tg_token == "your_telegram_bot_token_here":
        print("   [-] TELEGRAM_BOT_TOKEN masih menggunakan teks placeholder contoh.")
        all_ok = False
    elif re.match(r"^[0-9]{8,11}:[a-zA-Z0-9_-]{35}$", tg_token):
        print(f"   [+] Format Valid: {mask_secret(tg_token)}")
        # Cek koneksi live ke Telegram Bot API
        try:
            url = f"https://api.telegram.org/bot{tg_token}/getMe"
            res = httpx.get(url, timeout=10.0)
            if res.status_code == 200 and res.json().get("ok"):
                bot_info = res.json().get("result", {})
                bot_username = bot_info.get("username", "Unknown")
                print(f"   [+] Koneksi Telegram Bot: BERHASIL! Terhubung ke bot: @{bot_username}")
            else:
                print(f"   [-] Token Telegram tidak valid (Status: {res.status_code})")
                all_ok = False
        except Exception as e:
            print(f"   [!] Tidak dapat menghubungi server Telegram API: {e}")
    else:
        print(f"   [!] Format token bot Telegram tidak standar (seharusnya '<id_angka>:<hash>'): {mask_secret(tg_token)}")
        all_ok = False

    # 3. Periksa TELEGRAM_ADMIN_IDS
    admin_ids = env_vars.get("TELEGRAM_ADMIN_IDS", "").strip()
    print("\n3. Telegram Admin IDs:")
    if not admin_ids:
        print("   [-] TELEGRAM_ADMIN_IDS masih kosong.")
        all_ok = False
    elif admin_ids == "123456789":
        print("   [!] Peringatan: Masih menggunakan ID contoh '123456789'. Pastikan sudah diubah ke ID asli Anda.")
    else:
        valid_digits = [item.strip() for item in admin_ids.split(",") if item.strip().isdigit()]
        if valid_digits:
            print(f"   [+] Format Valid: {len(valid_digits)} ID Admin terdaftar ({', '.join([mask_secret(i) for i in valid_digits])})")
        else:
            print("   [-] TELEGRAM_ADMIN_IDS harus berupa angka ID akun Telegram.")
            all_ok = False

    # 4. Periksa TELEGRAM_ALERT_CHAT_ID
    alert_chat_id = env_vars.get("TELEGRAM_ALERT_CHAT_ID", "").strip()
    print("\n4. Telegram Alert Chat ID:")
    if not alert_chat_id:
        print("   [-] TELEGRAM_ALERT_CHAT_ID masih kosong.")
        all_ok = False
    elif alert_chat_id == "123456789":
        print("   [!] Peringatan: Masih menggunakan Chat ID contoh '123456789'.")
    elif alert_chat_id.lstrip("-").isdigit():
        print(f"   [+] Format Valid: {mask_secret(alert_chat_id)}")
    else:
        print("   [-] Format Chat ID harus berupa angka/ID numerik.")
        all_ok = False

    # 5. Periksa DASHBOARD_SECRET_KEY
    dash_key = env_vars.get("DASHBOARD_SECRET_KEY", "").strip()
    print("\n5. Dashboard Secret Key:")
    if not dash_key:
        print("   [-] DASHBOARD_SECRET_KEY masih kosong.")
        all_ok = False
    elif dash_key == "change_this_to_a_random_secure_string_for_session_auth":
        print("   [!] Menggunakan secret default. Disarankan diganti dengan kata kunci rahasia buatan Anda.")
    else:
        print(f"   [+] Terkonfigurasi: {mask_secret(dash_key)}")

    print("\n" + "=" * 60)
    if all_ok:
        print("[+] SEMUA KONFIGURASI UTAMA .env DINYATAKAN VALID & SIAP DIGUNAKAN!")
    else:
        print("[!] DITEMUKAN BEBERAPA KONFIGURASI YANG PERLU DIPERBAIKI.")
    print("=" * 60)
    return all_ok


if __name__ == "__main__":
    check_env()
