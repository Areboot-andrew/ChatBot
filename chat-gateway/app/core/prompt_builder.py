from typing import List, Dict, Any
from app.models.tenant import BotSetting
from app.models.knowledge import QaPair

def build_system_prompt(settings: BotSetting, qa_facts: List[QaPair] = None) -> str:
    """
    Builds the final system prompt by injecting Business Rules, Marketing Protocols,
    Escalation Policies, and found Facts (Q&A/RAG).
    """
    base_prompt = settings.system_prompt if settings and settings.system_prompt else "Ти корисний асистент."
    
    parts = [base_prompt, ""]
    
    # 1. Inject Business Rules (Strict Constraints)
    if settings and settings.business_rules:
        parts.append("--- [БІЗНЕС ПРАВИЛА ТА ОБМЕЖЕННЯ] ---")
        parts.append("Ти ПОВИНЕН неухильно дотримуватись наступних правил. Ніколи не порушуй їх:")
        parts.append(settings.business_rules)
        parts.append("")
    
    # 2. Inject Marketing Protocols (Upsell)
    if settings and settings.marketing_rules:
        parts.append("--- [МАРКЕТИНГОВІ ПРОТОКОЛИ] ---")
        parts.append("Якщо це доречно і природно звучить у контексті діалогу, спробуй застосувати ці настанови (ненав'язливо):")
        parts.append(settings.marketing_rules)
        parts.append("")
    
    # 3. Inject Escalation Policy Context
    if settings and settings.escalation_prompt:
        parts.append("--- [ПОЛІТИКА ЕСКАЛАЦІЇ] ---")
        parts.append("Якщо у тебе немає інформації для відповіді на питання клієнта, не вигадуй. Замість цього використай цю настанову своїми словами:")
        parts.append(f"Настанова: {settings.escalation_prompt}")
        parts.append("")
        
    # 4. Inject Knowledge Base Facts (Q&A / RAG Context)
    if qa_facts and len(qa_facts) > 0:
        parts.append("--- [ФАКТИ ТА БАЗА ЗНАНЬ] ---")
        parts.append("Використай наступні факти для відповіді на запитання. Це сухі факти або приклади, не копіюй їх дослівно, а вплітай у свою відповідь природно:")
        for qa in qa_facts:
            parts.append(f"- Питання клієнта: {qa.question}")
            parts.append(f"  Факт/Інформація: {qa.answer}")
        parts.append("")
        
    parts.append("--- [КІНЕЦЬ СИСТЕМНИХ ІНСТРУКЦІЙ] ---")
    
    return "\n".join(parts)
