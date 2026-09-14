"""
Modul Circuit Breaker untuk melindungi sistem dari kegagalan API eksternal berkelanjutan.
Mencegah spam request dan double-post saat API penyedia mengalami gangguan atau rate-limit.
"""

import time
from typing import Callable, Any
from enum import Enum


class CircuitState(Enum):
    CLOSED = "CLOSED"      # Normal: request diizinkan
    OPEN = "OPEN"          # Gangguan: request diblokir seketika
    HALF_OPEN = "HALF_OPEN"# Uji coba: izinkan 1 request untuk verifikasi pemulihan


class CircuitBreakerOpenException(Exception):
    pass


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout_seconds: float = 60.0,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0

    def record_success(self):
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            # Periksa apakah waktu pemulihan sudah berlalu
            if time.time() - self.last_failure_time > self.recovery_timeout_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        if self.state == CircuitState.HALF_OPEN:
            return True
        return False


gemini_circuit_breaker = CircuitBreaker()
