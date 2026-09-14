"""
Creator Agent Package: Menangani perencanaan dan eksekusi kreasi 4 pilar konten.
"""

from typing import Dict, Any, Optional, List
from sqlalchemy.ext.asyncio import AsyncSession

from agents.creator.ideator import ideator, ContentIdea
from agents.creator.pita_transformasi import pita_transformasi_creator
from agents.creator.pita_mini import pita_mini_creator
from agents.creator.pita_cerita import pita_cerita_creator
from agents.creator.pita_kreasi import pita_kreasi_creator


class CreatorAgent:
    def __init__(self):
        self.ideator = ideator
        self.creators = {
            "pita_transformasi": pita_transformasi_creator,
            "pita_mini": pita_mini_creator,
            "pita_cerita": pita_cerita_creator,
            "pita_kreasi": pita_kreasi_creator,
        }

    async def produce_content(
        self,
        pilar: str,
        job_id: str,
        recent_topics: Optional[List[str]] = None,
        is_exploration: bool = False,
        custom_idea: Optional[ContentIdea] = None,
        db_session: Optional[AsyncSession] = None,
    ) -> Dict[str, Any]:
        """
        Alur kerja penuh Creator Agent:
        1. Menghasilkan/memilih ide konten
        2. Mendispatch ke sub-creator pilar yang tepat
        3. Menghasilkan aset media, prompt provenance, dan caption
        """
        if pilar not in self.creators:
            raise ValueError(f"Pilar '{pilar}' tidak dikenal. Pilihan valid: {list(self.creators.keys())}")

        # 1. Dapatkan ide konten
        idea = custom_idea or await self.ideator.generate_idea(
            pilar=pilar,
            recent_topics=recent_topics,
            is_exploration=is_exploration,
        )

        # 2. Jalankan creator pilar terkait
        creator = self.creators[pilar]
        content_payload = await creator.create(
            idea=idea,
            job_id=job_id,
            db_session=db_session,
        )

        content_payload["pilar"] = pilar
        content_payload["idea"] = idea
        return content_payload


creator_agent = CreatorAgent()

__all__ = [
    "creator_agent",
    "CreatorAgent",
    "ideator",
    "ContentIdea",
    "pita_transformasi_creator",
    "pita_mini_creator",
    "pita_cerita_creator",
    "pita_kreasi_creator",
]
