"""
Modul Ideator untuk Sistem Agen Creator Pita Media.
Merencanakan ide konten berdasarkan pilar, tema, dan preferensi novelty/eksplorasi.
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from providers.gemini_client import gemini_client
from config.settings import settings


class ContentIdea(BaseModel):
    pilar: str = Field(..., description="Salah satu dari 4 pilar: pita_transformasi, pita_mini, pita_cerita, pita_kreasi")
    title: str = Field(..., description="Judul konten yang memikat dan relevan")
    concept: str = Field(..., description="Deskripsi konsep visual dan inti cerita/karya")
    target_audience: str = Field(default="Pecinta estetika visual & seni kreatif", description="Target audiens")
    visual_theme: str = Field(default="Cinematic Warm Lighting", description="Tema visual/mood")
    is_exploration: bool = Field(default=False, description="Apakah ide ini merupakan variasi eksperimen 20-30%")


class Ideator:
    def __init__(self):
        self.gemini = gemini_client

    async def generate_idea(
        self,
        pilar: str,
        recent_topics: Optional[List[str]] = None,
        is_exploration: bool = False,
    ) -> ContentIdea:
        """
        Menghasilkan ide konten orisinal yang menghindari kelelahan topik (fatigue)
        berdasarkan topik historis 7-30 hari terakhir.
        """
        recent_context = ", ".join(recent_topics or []) if recent_topics else "Belum ada riwayat baru."

        system_instruction = (
            "Anda adalah Lead Creative Director untuk kanal media 'Pita Media'. "
            "Pita Media berfokus pada konten estetika tinggi, ketelitian karya, narasi mendalam, dan kreasi memuaskan. "
            "Hindari klise, hindari repetisi tema yang sudah sering dibuat, dan jaga orisinalitas tinggi."
        )

        exploration_note = (
            "KONTEN INI ADALAH EKSPERIMEN (20-30% NOVELTY QUOTA). "
            "Eksplorasi sudut pandang berani, material baru yang belum lazim, atau gaya narasi segar."
            if is_exploration
            else "Fokus pada konsistensi pilar standar dengan eksekusi visual dan narasi kelas atas."
        )

        prompt = f"""
        Rencanakan satu ide konten baru untuk pilar: '{pilar}'.
        
        {exploration_note}
        
        Topik yang sudah dibuat baru-baru ini (HINDARI REPETISI INI):
        [{recent_context}]
        
        Spesifikasi Pilar:
        - pita_transformasi : Timelapse/transformasi bertahap (awal -> proses -> detail -> reveal) dengan konsistensi visual.
        - pita_mini         : Konstruksi miniatur realistis & memuaskan dari material ke final reveal.
        - pita_cerita       : 3-5 gambar carousel statis + cerita bermakna 150-300 kata.
        - pita_kreasi       : Bahan sederhana menjadi karya menakjubkan tanpa klaim palsu pembuatan fisik dunia nyata.
        """

        if not self.gemini.is_configured():
            # Fallback ide mock jika offline
            return self._get_mock_idea(pilar, is_exploration)

        idea = await self.gemini.generate_structured(
            prompt=prompt,
            schema=ContentIdea,
            system_instruction=system_instruction,
            model=settings.GEMINI_PRO_MODEL,
        )
        if not getattr(idea, "title", None) or not getattr(idea, "concept", None):
            idea = self._get_mock_idea(pilar, is_exploration)
        idea.pilar = pilar
        idea.is_exploration = is_exploration
        return idea

    def _get_mock_idea(self, pilar: str, is_exploration: bool) -> ContentIdea:
        mock_data = {
            "pita_transformasi": ContentIdea(
                pilar="pita_transformasi",
                title="Transformasi Jam Dinding Kuno Berkarat Menjadi Arloji Meja Steampunk",
                concept="Proses timelapse restorasi dan modifikasi jam antik dengan roda gigi kuningan bertahap hingga reveal akhir berputar sempurna.",
                visual_theme="Warm Vintage Workshop with Amber Lighting",
                is_exploration=is_exploration,
            ),
            "pita_mini": ContentIdea(
                pilar="pita_mini",
                title="Pembangunan Toko Buku Miniatur Klasik di Sudut Rak Kayu",
                concept="Konstruksi miniatur rak buku, tangga putar kayu balsa, buku mikroskopis, dan lampu gantung hangat micro-LED.",
                visual_theme="Cozy Diorama Studio Lighting",
                is_exploration=is_exploration,
            ),
            "pita_cerita": ContentIdea(
                pilar="pita_cerita",
                title="Garis Waktu Sang Penjaga Mercusuar Tua",
                concept="Narasi 4 slide tentang seorang penjaga mercusuar yang menyaksikan perubahan zaman dari badai samudera hingga fajar kedamaian.",
                visual_theme="Cinematic Ocean Twilight & Oil Painting Tone",
                is_exploration=is_exploration,
            ),
            "pita_kreasi": ContentIdea(
                pilar="pita_kreasi",
                title="Kardus Bekas & Pasir Pantai Menjadi Relief Kastil Megah",
                concept="Eksplorasi seni konsep menggunakan material karton bergelombang dan butiran pasir menjadi arsitektur kastil dramatis.",
                visual_theme="Dramatic Sunlight & Macro Texture",
                is_exploration=is_exploration,
            ),
        }
        return mock_data.get(pilar, mock_data["pita_transformasi"])


ideator = Ideator()
