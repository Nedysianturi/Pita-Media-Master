"""
Pita Media Enterprise Security - Secret Redaction Filter.
Ensures no API keys, bearer tokens, passwords, or sensitive credentials
are ever leaked into application logs, error traces, or console outputs.
"""

import re
import logging
from typing import List, Pattern

# Common secret regex patterns
SECRET_PATTERNS: List[Pattern] = [
    # Authorization header: Bearer xxxx
    re.compile(r'(?i)(bearer\s+)([A-Za-z0-9_\-\.\:\=\+]{8,})'),
    # Generic access_token or token query/json param
    re.compile(r'(?i)(access_token["\']?\s*[:=]\s*["\']?)([A-Za-z0-9_\-\.\:\=\+]{8,})["\']?'),
    # Generic api_key / secret query/json param
    re.compile(r'(?i)((?:api[_-]?key|client[_-]?secret|secret[_-]?key|password)["\']?\s*[:=]\s*["\']?)([A-Za-z0-9_\-\.\:\=\+]{6,})["\']?'),
    # Google Gemini API key: AIzaSy...
    re.compile(r'(AIzaSy[A-Za-z0-9_\-]{33})'),
    # Meta Graph API Token: EAA...
    re.compile(r'(EAA[A-Za-z0-9_\-]{50,})'),
    # xAI Grok Key: xai-...
    re.compile(r'(xai-[A-Za-z0-9_\-]{30,})'),
    # Telegram Bot Token: 123456789:ABC...
    re.compile(r'(bot\d{8,12}:[A-Za-z0-9_\-]{30,})'),
    re.compile(r'(\b\d{8,12}:[A-Za-z0-9_\-]{30,}\b)'),
]

_DYNAMIC_SECRETS: List[str] = []

def register_global_secret(secret: str):
    """Dynamically registers a secret string to be scrubbed from all logs."""
    if secret and len(secret) >= 4 and secret not in _DYNAMIC_SECRETS:
        _DYNAMIC_SECRETS.append(secret)

def redact_text(text: str) -> str:
    """Redacts any sensitive tokens found within a string."""
    if not isinstance(text, str):
        return str(text)
    
    sanitized = text
    for pattern in SECRET_PATTERNS:
        if pattern.groups == 2:
            sanitized = pattern.sub(r'\1****REDACTED****', sanitized)
        elif pattern.groups == 1:
            sanitized = pattern.sub('****REDACTED****', sanitized)
        else:
            sanitized = pattern.sub('****REDACTED****', sanitized)
            
    for sec in _DYNAMIC_SECRETS:
        if sec in sanitized:
            sanitized = sanitized.replace(sec, f"[REDACTED_SECRET:••••{sec[-4:]}]")
            
    return sanitized

class SecretRedactionFilter(logging.Filter):
    """Logging filter that scrubs sensitive strings from LogRecords."""
    def register_secret(self, secret: str):
        register_global_secret(secret)

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: (redact_text(v) if isinstance(v, str) else v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple((redact_text(v) if isinstance(v, str) else v) for v in record.args)
        return True

def apply_global_redaction_filter():
    """Applies the secret redaction filter to the root logger and all handlers."""
    redaction_filter = SecretRedactionFilter()
    root_logger = logging.getLogger()
    root_logger.addFilter(redaction_filter)
    for handler in root_logger.handlers:
        handler.addFilter(redaction_filter)

