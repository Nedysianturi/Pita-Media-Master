"""
Music & Emotion Audio Manager for Pita Media.
Categorizes background audio and soundtracks by emotional mood, tempo (BPM),
copyright safety status, and pillar compatibility.
Supports: SEDIH, HORROR, LUCU, INSPIRATIF, NOSTALGIA, SATISFYING, DRAMATIC, WHOLESOME.
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path

logger = logging.getLogger("pita_media.audio.music_manager")


class MusicTrack:
    def __init__(
        self,
        track_id: str,
        title: str,
        artist: str,
        mood: str,
        bpm: int,
        duration_sec: int,
        license_type: str,
        file_path: Optional[str] = None,
        tags: Optional[List[str]] = None
    ):
        self.track_id = track_id
        self.title = title
        self.artist = artist
        self.mood = mood.lower()
        self.bpm = bpm
        self.duration_sec = duration_sec
        self.license_type = license_type
        self.file_path = file_path
        self.tags = tags or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "track_id": self.track_id,
            "title": self.title,
            "artist": self.artist,
            "mood": self.mood,
            "bpm": self.bpm,
            "duration_sec": self.duration_sec,
            "license_type": self.license_type,
            "file_path": self.file_path,
            "tags": self.tags
        }


class MusicManager:
    """
    Catalog and selector for emotion-matched background audio.
    Provides procedural audio synthesis and ambient soundtrack mixing.
    """

    SUPPORTED_MOODS = [
        "sedih", "horror", "lucu", "inspiratif",
        "nostalgia", "satisfying", "dramatic", "wholesome"
    ]

    # Formula nada & chord progression procedural untuk 8 mood emosi (Frekuensi Harmonis Hz)
    MOOD_AUDIO_EXPRESSIONS: Dict[str, str] = {
        # Inspiratif: Akor Mayor C-G-Am-F megah dengan sapuan nada hangat
        "inspiratif": (
            "0.18*sin(2*PI*261.63*t) + 0.14*sin(2*PI*329.63*t) + "
            "0.14*sin(2*PI*392.00*t) + 0.10*sin(2*PI*523.25*t) + "
            "0.06*sin(2*PI*659.25*t*(1+0.03*sin(2*PI*0.5*t)))"
        ),
        # Wholesome: Akor G Mayor hangat dengan petikan akustik menenangkan
        "wholesome": (
            "0.20*sin(2*PI*196.00*t) + 0.15*sin(2*PI*246.94*t) + "
            "0.15*sin(2*PI*293.66*t) + 0.12*sin(2*PI*392.00*t) + "
            "0.08*sin(2*PI*493.88*t)"
        ),
        # Satisfying: Frekuensi ASMR 432Hz terapeutik & deep warm resonance
        "satisfying": (
            "0.22*sin(2*PI*108.00*t) + 0.18*sin(2*PI*216.00*t) + "
            "0.15*sin(2*PI*432.00*t) + 0.08*sin(2*PI*864.00*t*(1+0.01*sin(2*PI*0.2*t)))"
        ),
        # Nostalgia: Nuansa Lofi Dm7 & pita kaset vintage lembut
        "nostalgia": (
            "0.18*sin(2*PI*146.83*t) + 0.14*sin(2*PI*220.00*t) + "
            "0.12*sin(2*PI*261.63*t) + 0.10*sin(2*PI*349.23*t) + "
            "0.05*sin(2*PI*440.00*t)"
        ),
        # Sedih: Minor melankolis Am - Em reflektif
        "sedih": (
            "0.20*sin(2*PI*110.00*t) + 0.16*sin(2*PI*220.00*t) + "
            "0.14*sin(2*PI*261.63*t) + 0.12*sin(2*PI*329.63*t) + "
            "0.06*sin(2*PI*440.00*t)"
        ),
        # Dramatic: Cinematic low sub-bass pulse & tension
        "dramatic": (
            "0.25*sin(2*PI*65.41*t) + 0.18*sin(2*PI*130.81*t) + "
            "0.14*sin(2*PI*196.00*t) + 0.10*sin(2*PI*293.66*t*(1+0.05*sin(2*PI*2*t)))"
        ),
        # Lucu: Nada staccato riang & playful acoustic
        "lucu": (
            "0.18*sin(2*PI*293.66*t) + 0.16*sin(2*PI*369.99*t) + "
            "0.14*sin(2*PI*440.00*t) + 0.10*sin(2*PI*587.33*t)"
        ),
        # Horror: Drone misterius dengan disonansi gelap
        "horror": (
            "0.22*sin(2*PI*55.00*t) + 0.16*sin(2*PI*77.78*t) + "
            "0.12*sin(2*PI*110.00*t) + 0.08*sin(2*PI*155.56*t)"
        )
    }

    def __init__(self, catalog_path: Optional[str] = None):
        base_dir = Path(__file__).resolve().parent.parent.parent
        self.audio_dir = base_dir / "storage" / "audio"
        self.catalog_path = catalog_path or str(base_dir / "storage" / "music_catalog.json")
        self.tracks: List[MusicTrack] = []
        self._load_catalog()

    def _load_catalog(self):
        """Load audio catalog from disk or initialize default curated library."""
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        if os.path.exists(self.catalog_path):
            try:
                with open(self.catalog_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.tracks = [MusicTrack(**item) for item in data]
                    return
            except Exception as e:
                logger.error(f"Failed to load music catalog: {e}")

        # Initialize default copyright-free ambient library
        self.tracks = [
            MusicTrack("track_01", "Echoes of Yesterday", "Pita Ambient Lab", "sedih", 68, 120, "Creative Commons 0", file_path="storage/audio/track_01.mp3", tags=["piano", "melancholy", "reflection"]),
            MusicTrack("track_02", "Midnight Shadow", "Pita Ambient Lab", "horror", 80, 95, "Creative Commons 0", file_path="storage/audio/track_02.mp3", tags=["suspense", "drone", "dark"]),
            MusicTrack("track_03", "Sunny Whistle", "Pita Acoustic", "lucu", 118, 75, "Creative Commons 0", file_path="storage/audio/track_03.mp3", tags=["cheerful", "ukulele", "playful"]),
            MusicTrack("track_04", "Rising Horizon", "Pita Cinematic", "inspiratif", 95, 140, "Creative Commons 0", file_path="storage/audio/track_04.mp3", tags=["orchestral", "uplifting", "epic"]),
            MusicTrack("track_05", "Cassette Memories", "Pita Retro", "nostalgia", 84, 110, "Creative Commons 0", file_path="storage/audio/track_05.mp3", tags=["lofi", "vintage", "tape_crackle"]),
            MusicTrack("track_06", "Crystal Streams", "Pita ASMR", "satisfying", 72, 180, "Creative Commons 0", file_path="storage/audio/track_06.mp3", tags=["ambient", "foley", "relaxing"]),
            MusicTrack("track_07", "The Turning Point", "Pita Dramatic", "dramatic", 124, 90, "Creative Commons 0", file_path="storage/audio/track_07.mp3", tags=["sub_bass", "ticking", "tension"]),
            MusicTrack("track_08", "Warm Hug", "Pita Acoustic", "wholesome", 76, 135, "Creative Commons 0", file_path="storage/audio/track_08.mp3", tags=["guitar", "calm", "heartwarming"])
        ]
        self._save_catalog()

    def _save_catalog(self):
        """Persist catalog to disk."""
        try:
            os.makedirs(os.path.dirname(self.catalog_path), exist_ok=True)
            with open(self.catalog_path, "w", encoding="utf-8") as f:
                json.dump([t.to_dict() for t in self.tracks], f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save music catalog: {e}")

    def list_tracks(self, mood: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all tracks or filter by mood."""
        if mood:
            mood_lower = mood.lower()
            return [t.to_dict() for t in self.tracks if t.mood == mood_lower]
        return [t.to_dict() for t in self.tracks]

    def select_track_for_content(self, pilar: str, tone: str) -> Dict[str, Any]:
        """
        Auto-select optimal copyright-safe soundtrack based on content pilar and emotional tone.
        """
        mood = tone.lower()
        if mood not in self.SUPPORTED_MOODS:
            mood = "inspiratif" if pilar in ["pita_transformasi", "pita_cerita", "pita_kreasi"] else "wholesome"

        matched = [t for t in self.tracks if t.mood == mood]
        if matched:
            return matched[0].to_dict()
        return self.tracks[0].to_dict()

    def get_audio_expression_for_mood(self, mood: str) -> str:
        """Mengembalikan ekspresi sintesis frekuensi FFmpeg aevalsrc untuk mood yang dipilih."""
        m = (mood or "inspiratif").lower()
        return self.MOOD_AUDIO_EXPRESSIONS.get(m, self.MOOD_AUDIO_EXPRESSIONS["inspiratif"])


music_manager = MusicManager()
