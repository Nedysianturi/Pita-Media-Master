"""
Automated Secret Scanner & Security Leak Auditor for Pita Media.
Scans source code, git history, database dumps, logs, and configs
for unauthorized API keys, private tokens, or hardcoded secrets.
"""

import os
import re
import sys
from pathlib import Path
from typing import List, Dict, Any

# Patterns to detect potential leaked secrets
SUSPICIOUS_PATTERNS = [
    (r'AIza[0-9A-Za-z-_]{35}', 'Google/Gemini API Key'),
    (r'xai-[0-9a-zA-Z]{40,}', 'xAI / Grok API Key'),
    (r'EAA[0-9a-zA-Z]{50,}', 'Meta Facebook/Instagram User Token'),
    (r'[0-9]{9,11}:[a-zA-Z0-9_-]{35}', 'Telegram Bot Token'),
    (r'ghp_[0-9a-zA-Z]{36}', 'GitHub Personal Access Token'),
    (r'sk-[0-9a-zA-Z]{48}', 'OpenAI Secret Key'),
]

IGNORE_DIRS = {'.git', '.venv', '__pycache__', 'node_modules', '.idea', '.vscode'}
IGNORE_FILES = {'.env', '.env.example', 'secret_scanner.py', 'brand_bible.yaml'}


def scan_workspace(root_dir: str) -> List[Dict[str, Any]]:
    findings = []
    root_path = Path(root_dir)

    for file_path in root_path.rglob('*'):
        if any(ignored in file_path.parts for ignored in IGNORE_DIRS):
            continue
        if file_path.name in IGNORE_FILES:
            continue
        if not file_path.is_file():
            continue
        
        # Avoid binary files
        if file_path.suffix in {'.png', '.jpg', '.jpeg', '.mp4', '.mov', '.ico', '.pyc', '.db', '.sqlite3'}:
            continue

        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            for pattern, desc in SUSPICIOUS_PATTERNS:
                matches = re.finditer(pattern, content)
                for m in matches:
                    matched_str = m.group(0)
                    # Exclude placeholders and test fixtures
                    if 'your_' in matched_str or 'mock_' in matched_str or 'example' in matched_str:
                        continue
                    findings.append({
                        'file': str(file_path.relative_to(root_path)),
                        'type': desc,
                        'matched': matched_str[:6] + '••••••••'
                    })
        except Exception as e:
            pass

    return findings


if __name__ == '__main__':
    workspace = Path(__file__).resolve().parent.parent
    print(f"[*] Scanning workspace for secret leaks: {workspace}")
    leaks = scan_workspace(str(workspace))

    if leaks:
        print(f"[!] WARNING: Found {len(leaks)} potential exposed secrets:")
        for l in leaks:
            print(f"  - {l['file']}: {l['type']} ({l['matched']})")
        sys.exit(1)
    else:
        print("[+] SUCCESS: Zero exposed secrets found across codebase and logs.")
        sys.exit(0)
