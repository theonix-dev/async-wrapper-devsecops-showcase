import json
import logging
import abc
import aiohttp
from typing import Dict, Any

logger = logging.getLogger(__name__)

class BaseAIClient(abc.ABC):
    """
    Abstract Base Class defining the contract for AI/LLM providers.
    Implements the Strategy Pattern, allowing different AI models
    to be swapped seamlessly at runtime.
    """
    
    @abc.abstractmethod
    async def analyze_offer(self, title: str, description: str, price: str, tag: str, lang: str = "uk") -> Dict[str, Any]:
        """
        Analyzes a scraped OLX listing to evaluate deal value and scam risk.
        
        Args:
            title: The title of the OLX listing.
            description: The description text.
            price: The price of the listing.
            tag: The query tag the listing matched.
            lang: The target language for the text analysis fields.
            
        Returns:
            A dictionary containing structured analysis results (fits_tag, condition, scam_risk, rating, analysis).
        """
        pass


class MockAIClient(BaseAIClient):
    """
    Mock AI Provider used for local development, fallback, and automated testing
    to avoid incurring API token costs.
    """
    async def analyze_offer(self, title: str, description: str, price: str, tag: str, lang: str = "uk") -> Dict[str, Any]:
        logger.info(f"[MockAI] Performing heuristics analysis for tag: '{tag}'")
        fits = tag.lower() in title.lower() or tag.lower() in description.lower()
        is_suspicious = any(w in description.lower() for w in ("предоплата", "передплата", "аванс", "prepayment"))
        
        if lang == "en":
            scam_risk = "High" if is_suspicious else "Low"
            condition = "Used"
            if "new" in description.lower() or "sealed" in description.lower():
                condition = "New"
            analysis = "Prepayment warning detected. Proceed with caution." if is_suspicious else "Looks clean. No red flags found."
        else:
            scam_risk = "Високий" if is_suspicious else "Низький"
            condition = "Вживаний"
            if "новий" in description.lower() or "sealed" in description.lower():
                condition = "Новий"
            analysis = "Виявлено вимогу передоплати! Ризик шахрайства." if is_suspicious else "Опис виглядає безпечним, передоплати немає."

        return {
            "fits_tag": fits,
            "condition": condition,
            "scam_risk": scam_risk,
            "rating": "8/10" if not is_suspicious else "3/10",
            "analysis": analysis
        }


class OpenAIClient(BaseAIClient):
    """
    OpenAI API Strategy implementation utilizing chat completions with structured JSON formatting.
    """
    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url or "https://api.openai.com/v1"
        self.model = model or "gpt-4o-mini"

    async def analyze_offer(self, title: str, description: str, price: str, tag: str, lang: str = "uk") -> Dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        prompt = (
            f"Analyze this OLX ad for the query tag: \"{tag}\".\n"
            f"Title: {title}\n"
            f"Description: {description}\n"
            f"Price: {price}\n\n"
            f"IMPORTANT: You MUST write all textual fields in this language: {lang}.\n"
            f"Return ONLY a JSON object with these keys: fits_tag (bool), condition (str), scam_risk (str), rating (str), analysis (str)."
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a professional OLX deal analyst. You respond only with a valid JSON object."},
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.3
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload, timeout=15) as response:
                    if response.status == 200:
                        result = await response.json()
                        content = result["choices"][0]["message"]["content"]
                        return json.loads(content)
        except Exception as e:
            logger.error(f"OpenAI Client Error: {e}")
            
        # Graceful fallback to Mock Client on connection failures or timeouts
        return await MockAIClient().analyze_offer(title, description, price, tag, lang)
