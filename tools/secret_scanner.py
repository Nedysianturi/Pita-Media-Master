"""
Tool Secret Scanner untuk Pita Media.
Memindai seluruh direktori proyek untuk memastikan tidak ada kunci API, token Telegram,
atau rahasia sensitif yang tertinggal atau berisiko ter-commit ke Git.
"""

import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent

# Pola regex untuk deteksi token rahasia
SECRET_PATTERNS = [
    (r"AIza[0-9A-Za-z-_]{35}", "Google API Key"),
    (r"[0-9]{8,10}:[a-zA-Z0-9_-]{35}", "Telegram Bot Token"),
    (r"ghp_[0-9a-zA-Z]{36}", "GitHub Personal Access Token"),
    (r"sk-[a-zA-Z0-9]{48}", "OpenAI / Generic Secret Key"),
    (r"xox[baprs]-[0-9a-zA-Z]{10,48}", "Slack Token"),
    (r"(?i)api[_-]?key\s*=\s*['\"][0-9a-zA-Z]{20,}['\"]", "Hardcoded API Key Variable"),
]

IGNORED_DIRS = {".git", ".venv", "venv", "__pycache__", "storage", ".pytest_cache", ".idea", ".vscode"}
IGNORED_FILES = {".env", ".env.example", "secret_scanner.py"}


def scan_file_for_secrets(file_path: Path) -> List[Tuple[int, str, str]]:
    findings = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line_no, line in enumerate(f, start=1):
                if "your_gemini_api_key_here" in line or "your_telegram_bot_token_here" in line:
                    continue
                for pattern, desc in SECRET_PATTERNS:
                    if re.search(pattern, line):
                        findings.append((line_no, desc, line.strip()[:60]))
    except Exception:
        pass
    return findings


def run_secret_scan() -> bool:
    print(f"[*] Memulai Secret Scan pada direktori commit: {BASE_DIR}")
    total_files_scanned = 0
    all_findings = []

    for root, dirs, files in os.walk(BASE_DIR):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for file in files:
            if file in IGNORED_FILES:
                continue
            fpath = Path(root) / file
            total_files_scanned += 1
            findings = scan_file_for_secrets(fpath)
            if findings:
                all_findings.append((fpath, findings))

    # Cek apakah file .env ada di .gitignore
    gitignore_path = BASE_DIR / ".gitignore"
    if gitignore_path.exists():
        with open(gitignore_path, "r", encoding="utf-8") as f:
            gi_content = f.read()
            if ".env" not in gi_content:
                print("[-] PERINGATAN: '.env' tidak ditemukan di .gitignore!")
                return False
    else:
        print("[-] PERINGATAN: File .gitignore tidak ditemukan!")
        return False

    print(f"[+] Pemindaian selesai. Total file sumber diperiksa: {total_files_scanned}")
    if all_findings:
        print("[-] DITEMUKAN POTENSI KEBOCORAN RAHASIA (SECRETS DETECTED):")
        for fpath, finds in all_findings:
            rel_path = fpath.relative_to(BASE_DIR)
            print(f"  • File: {rel_path}")
            for lno, desc, snippet in finds:
                print(f"    - Baris {lno} ({desc})")
        return False

    print("[+] BERSIH: Tidak ada hardcoded credential atau API key sensitif pada file yang akan di-commit.")
    return True


if __name__ == "__main__":
    success = run_secret_scan()
    sys.exit(0 if success else 1)
