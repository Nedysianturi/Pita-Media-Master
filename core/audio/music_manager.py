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
    """

    SUPPORTED_MOODS = [
        "sedih", "horror", "lucu", "inspiratif",
        "nostalgia", "satisfying", "dramatic", "wholesome"
    ]

    def __init__(self, catalog_path: Optional[str] = None):
        base_dir = Path(__file__).resolve().parent.parent.parent
        self.catalog_path = catalog_path or str(base_dir / "storage" / "music_catalog.json")
        self.tracks: List[MusicTrack] = []
        self._load_catalog()

    def _load_catalog(self):
        """Load audio catalog from disk or initialize default curated library."""
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
            MusicTrack("track_01", "Echoes of Yesterday", "Pita Ambient Lab", "sedih", 68, 120, "Creative Commons 0", tags=["piano", "melancholy", "reflection"]),
            MusicTrack("track_02", "Midnight Shadow", "Pita Ambient Lab", "horror", 80, 95, "Creative Commons 0", tags=["suspense", "drone", "dark"]),
            MusicTrack("track_03", "Sunny Whistle", "Pita Acoustic", "lucu", 118, 75, "Creative Commons 0", tags=["cheerful", "ukulele", "playful"]),
            MusicTrack("track_04", "Rising Horizon", "Pita Cinematic", "inspiratif", 95, 140, "Creative Commons 0", tags=["orchestral", "uplifting", "epic"]),
            MusicTrack("track_05", "Cassette Memories", "Pita Retro", "nostalgia", 84, 110, "Creative Commons 0", tags=["lofi", "vintage", "tape_crackle"]),
            MusicTrack("track_06", "Crystal Streams", "Pita ASMR", "satisfying", 72, 180, "Creative Commons 0", tags=["ambient", "foley", "relaxing"]),
            MusicTrack("track_07", "The Turning Point", "Pita Dramatic", "dramatic", 124, 90, "Creative Commons 0", tags=["sub_bass", "ticking", "tension"]),
            MusicTrack("track_08", "Warm Hug", "Pita Acoustic", "wholesome", 76, 135, "Creative Commons 0", tags=["guitar", "calm", "heartwarming"])
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
            mood = "inspiratif" if pilar in ["pita_waktu", "pita_transformasi"] else "wholesome"

        matched = [t for t in self.tracks if t.mood == mood]
        if matched:
            return matched[0].to_dict()
        return self.tracks[0].to_dict()


music_manager = MusicManager()
