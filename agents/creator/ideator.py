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

        from core.learning.knowledge_base import knowledge_base
        from core.learning.strategy_scoring import strategy_scoring
        from core.calendar import event_intelligence_engine

        # Ambil insight aktif dari Knowledge Base
        active_knowledge = knowledge_base.get_active_knowledge(min_confidence=0.6)
        knowledge_context = ""
        if active_knowledge:
            bullet_points = "\n".join(f"- {k['category'].upper()}: {k['insight_text']}" for k in active_knowledge[:3])
            knowledge_context = f"\nPelajari Pola Unggul Historis (Gunakan wawasan ini):\n{bullet_points}\n"

        # Ambil konteks event & momen spesial jika ada
        event_context = ""
        try:
            evt_ctx = await event_intelligence_engine.get_today_event_context(target_pillar=pilar)
            if evt_ctx and evt_ctx.event_detected:
                event_context = f"\n{evt_ctx.to_prompt_enrichment()}\n"
        except Exception:
            event_context = ""

        prompt = f"""
        Rencanakan satu ide konten unggulan untuk pilar: '{pilar}'.
        
        {exploration_note}
        {knowledge_context}
        {event_context}
        Topik yang sudah dibuat baru-baru ini (HINDARI REPETISI INI):
        [{recent_context}]
        
        Spesifikasi Pilar:
        - pita_transformasi : Timelapse/transformasi bertahap (awal -> proses -> detail -> reveal) dengan konsistensi visual.
        - pita_mini         : Konstruksi miniatur realistis & memuaskan dari material ke final reveal.
        - pita_cerita       : 3-5 gambar carousel statis + cerita bermakna 150-300 kata.
        - pita_kreasi       : Bahan sederhana menjadi karya menakjubkan tanpa klaim palsu pembuatan fisik dunia nyata.
        """

        if not self.gemini.is_configured():
            # Fallback ide mock melalui Strategy Scoring multi-candidate
            return self._get_scored_mock_idea(pilar, is_exploration)

        idea = await self.gemini.generate_structured(
            prompt=prompt,
            schema=ContentIdea,
            system_instruction=system_instruction,
            model=settings.GEMINI_PRO_MODEL,
        )
        if not getattr(idea, "title", None) or not getattr(idea, "concept", None):
            idea = self._get_scored_mock_idea(pilar, is_exploration)
        idea.pilar = pilar
        idea.is_exploration = is_exploration
        return idea

    def _get_scored_mock_idea(self, pilar: str, is_exploration: bool) -> ContentIdea:
        from core.learning.strategy_scoring import strategy_scoring
        
        candidate_pool = [
            {
                "title": f"Transformasi Restorasi Klasik #{pilar.split('_')[-1].upper()}",
                "concept": "Proses bertahap restorasi objek antik dengan detail mekanik presisi hingga penyelesaian memukau.",
                "is_exploration": is_exploration,
            },
            {
                "title": f"Kreasi Miniatur Diorama #{pilar.split('_')[-1].upper()}",
                "concept": "Konstruksi diorama mikroskopis bertekstur tinggi dengan pencahayaan sinematik hangat.",
                "is_exploration": is_exploration,
            },
            {
                "title": f"Narasi Visual Menggugah #{pilar.split('_')[-1].upper()}",
                "concept": "Rangkaian gambar bercerita mendalam tentang dedikasi dan keindahan kerajinan.",
                "is_exploration": is_exploration,
            },
        ]
        
        scored = strategy_scoring.score_candidate_ideas(pilar=pilar, candidates=candidate_pool)
        best = strategy_scoring.select_best_candidate(scored)
        
        selected_title = best.title if best else candidate_pool[0]["title"]
        selected_concept = best.concept if best else candidate_pool[0]["concept"]

        return ContentIdea(
            pilar=pilar,
            title=selected_title,
            concept=selected_concept,
            visual_theme="Cinematic Warm Lighting",
            is_exploration=is_exploration,
        )


ideator = Ideator()
