"""
Pita Media Enterprise AI Engine - Provider Registry
Central dynamic catalog for AI Providers.
Enables '+ ADD PROVIDER' from Dashboard without modifying source code.
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from providers.base_provider import BaseAIProvider, AICapability
from providers.gemini_provider import gemini_provider
from providers.xai_provider import xai_provider

logger = logging.getLogger("pita.providers.registry")

CUSTOM_PROVIDERS_FILE = Path("storage/custom_providers.json")

class GenericCustomProvider(BaseAIProvider):
    """Generic OpenAI-compatible / Custom API Provider adapter."""
    async def generate_text(self, prompt: str, system_instruction: Optional[str] = None, model: Optional[str] = None, db_session = None, job_id = None) -> str:
        import httpx
        target_model = model or self.default_model
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        url = self.base_url.rstrip("/") + "/chat/completions"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json={"model": target_model, "messages": messages})
            if resp.status_code != 200:
                raise RuntimeError(f"Custom Provider '{self.display_name}' Error (HTTP {resp.status_code}): {resp.text}")
            return resp.json()["choices"][0]["message"]["content"]

    async def generate_structured(self, prompt: str, schema, system_instruction = None, model = None, db_session = None, job_id = None):
        import re, json
        schema_json = json.dumps(schema.model_json_schema())
        enforced = f"{prompt}\n\nKEMBALIKAN OUTPUT HANYA SEBAGAI JSON VALID SESUAI SKEMA BERIKUT:\n{schema_json}"
        raw = await self.generate_text(enforced, system_instruction, model, db_session, job_id)
        cleaned = raw.strip().replace("```json", "").replace("```", "").strip()
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        return schema.model_validate(json.loads(match.group(0) if match else cleaned))

    def test_connection(self) -> Dict[str, Any]:
        import httpx, time
        start_t = time.time()
        try:
            url = self.base_url.rstrip("/") + "/models"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            with httpx.Client(timeout=8.0) as client:
                resp = client.get(url, headers=headers)
            lat = round((time.time() - start_t) * 1000)
            if resp.status_code == 200:
                return {"status": "VALID", "latency_ms": lat, "message": f"Provider '{self.display_name}' Aktif."}
            return {"status": "INVALID", "latency_ms": lat, "message": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"status": "NEEDS_ATTENTION", "message": f"Connection error: {e}"}


class ProviderRegistry:
    def __init__(self):
        self._providers: Dict[str, BaseAIProvider] = {}
        self._register_defaults()
        self._load_custom_providers()

    def _register_defaults(self):
        self._providers["gemini"] = gemini_provider
        self._providers["xai"] = xai_provider

    def register_provider(self, provider: BaseAIProvider):
        self._providers[provider.provider_id] = provider

    def get_provider(self, provider_id: str) -> Optional[BaseAIProvider]:
        return self._providers.get(provider_id)

    def list_providers(self) -> List[BaseAIProvider]:
        return sorted(list(self._providers.values()), key=lambda p: p.priority)

    def list_active_providers(self) -> List[BaseAIProvider]:
        return [p for p in self.list_providers() if p.enabled]

    def get_providers_by_capability(self, capability: AICapability) -> List[BaseAIProvider]:
        return [p for p in self.list_providers() if p.supports(capability)]

    def add_custom_provider(
        self,
        provider_name: str,
        base_url: str,
        api_key: str,
        default_model: str,
        secondary_model: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
        cost_per_1m: float = 0.20,
        rate_limit_rpm: int = 20,
        priority: int = 5
    ) -> BaseAIProvider:
        """Adds and persists a new custom AI provider."""
        import re
        provider_id = re.sub(r'[^a-zA-Z0-9_]', '', provider_name.lower().replace(" ", "_"))
        caps = [AICapability(c) for c in (capabilities or ["TEXT"])]

        custom_prov = GenericCustomProvider(
            provider_id=provider_id,
            display_name=provider_name,
            api_key=api_key,
            base_url=base_url,
            default_model=default_model,
            secondary_model=secondary_model,
            capabilities=caps,
            cost_per_1m_tokens_usd=cost_per_1m,
            rate_limit_rpm=rate_limit_rpm,
            enabled=True,
            priority=priority
        )

        self.register_provider(custom_prov)
        self._save_custom_providers()
        return custom_prov

    def _save_custom_providers(self):
        CUSTOM_PROVIDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        custom_data = []
        for pid, p in self._providers.items():
            if pid not in ["gemini", "xai"]:
                custom_data.append({
                    "provider_id": p.provider_id,
                    "display_name": p.display_name,
                    "api_key": p.api_key,
                    "base_url": p.base_url,
                    "default_model": p.default_model,
                    "secondary_model": p.secondary_model,
                    "capabilities": [c.value for c in p.capabilities],
                    "cost_per_1m_tokens_usd": p.cost_per_1m_tokens_usd,
                    "rate_limit_rpm": p.rate_limit_rpm,
                    "enabled": p.enabled,
                    "priority": p.priority
                })
        try:
            with open(CUSTOM_PROVIDERS_FILE, "w", encoding="utf-8") as f:
                json.dump(custom_data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed saving custom providers: {e}")

    def _load_custom_providers(self):
        if not CUSTOM_PROVIDERS_FILE.exists():
            return
        try:
            with open(CUSTOM_PROVIDERS_FILE, "r", encoding="utf-8") as f:
                items = json.load(f)
            for item in items:
                caps = [AICapability(c) for c in item.get("capabilities", ["TEXT"])]
                prov = GenericCustomProvider(
                    provider_id=item["provider_id"],
                    display_name=item["display_name"],
                    api_key=item.get("api_key", ""),
                    base_url=item.get("base_url", ""),
                    default_model=item.get("default_model", ""),
                    secondary_model=item.get("secondary_model"),
                    capabilities=caps,
                    cost_per_1m_tokens_usd=item.get("cost_per_1m_tokens_usd", 0.20),
                    rate_limit_rpm=item.get("rate_limit_rpm", 20),
                    enabled=item.get("enabled", True),
                    priority=item.get("priority", 5)
                )
                self.register_provider(prov)
        except Exception as e:
            logger.warning(f"Could not load custom providers: {e}")

provider_registry = ProviderRegistry()
