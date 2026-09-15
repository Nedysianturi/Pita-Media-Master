"""
Data Models and Types for Calendar & Event Intelligence Engine.
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import List, Dict, Any, Optional


@dataclass
class EventDefinition:
    event_id: str
    event_name: str
    event_type: str  # OFFICIAL_NATIONAL, CURATED_CULTURE, AWARENESS, CUSTOM_BRAND, SEASONAL
    start_date: str  # MM-DD or YYYY-MM-DD
    end_date: Optional[str] = None
    timezone: str = "Asia/Jakarta"
    country: str = "ID"
    region: str = "National"
    audience_scope: str = "General Indonesian Audience"
    sensitivity_level: str = "LOW"  # LOW, MEDIUM, HIGH
    default_relevance: float = 85.0
    source: str = "OFFICIAL_CALENDAR"
    verification_status: str = "VERIFIED"
    suggested_pillars: List[str] = field(default_factory=lambda: ["PITA_CERITA", "PITA_MINI"])
    tone: str = "Inspiratif, Bermakna & Positif"
    visual_context: str = ""
    avoid_guidelines: List[str] = field(default_factory=list)
    lead_days: int = 2
    notes: str = ""
    enabled: bool = True


@dataclass
class RelevanceScore:
    score: float  # 0 - 100
    confidence: str  # HIGH, MEDIUM, LOW
    timing_phase: str  # TEASER, PRE_EVENT, MAIN_EVENT, FOLLOW_UP
    is_eligible: bool
    reasons: List[str] = field(default_factory=list)
    suggested_pillars: List[str] = field(default_factory=list)


@dataclass
class EventContext:
    status: str  # EVENT_ACTIVE, NO_RELEVANT_EVENT, CALENDAR_UNAVAILABLE, SKIP_EVENT
    event_detected: bool
    event_id: str = ""
    event_name: str = ""
    event_type: str = ""
    target_date: str = ""
    phase: str = "MAIN_EVENT"
    relevance_score: float = 0.0
    confidence: str = "HIGH"
    sensitivity_level: str = "LOW"
    suggested_pillars: List[str] = field(default_factory=list)
    tone: str = ""
    visual_context: str = ""
    avoid_guidelines: List[str] = field(default_factory=list)
    historical_lesson: str = ""
    notes: str = ""

    def to_prompt_enrichment(self) -> str:
        """Returns structured markdown context for AI Strategy and Creator prompts."""
        if not self.event_detected or self.status != "EVENT_ACTIVE":
            return ""
        
        avoid_str = "\n".join(f"- {a}" for a in self.avoid_guidelines) if self.avoid_guidelines else "- Tidak ada isu sensitif khusus."
        pillars_str = ", ".join(self.suggested_pillars) if self.suggested_pillars else "Semua Pilar Resmi"
        
        return f"""
### 📅 KONTEKS EVENT & MOMEN SPESIAL (Pita Media Calendar Intelligence):
- **Nama Event / Momen**: {self.event_name} ({self.phase})
- **Tingkat Relevansi & Keyakinan**: {self.relevance_score:.1f}/100 ({self.confidence})
- **Tingkat Sensitivitas**: {self.sensitivity_level}
- **Pilar yang Sangat Disarankan**: {pillars_str}
- **Nuansa & Tone Narasi**: {self.tone}
- **Konteks Visual & Estetika**: {self.visual_context or 'Visual sinematik bernuansa budaya/momen lokal yang elegan.'}
- **Panduan Kehati-hatian (Avoid)**:
{avoid_str}
*(Catatan: Tetap jaga keaslian narasi, jangan copy-paste konsep tahun lalu, dan pastikan nilai utama pilar tetap menjadi inti cerita)*
""".strip()
