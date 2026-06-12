from typing import List, Dict, Any
from app.models.tenant import BotSetting
from app.models.knowledge import QaPair

def build_system_prompt(settings: BotSetting, qa_facts: List[QaPair] = None, rag_docs: List[str] = None) -> str:
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
        
    # 4. Inject Knowledge Base Facts (Q&A)
    if qa_facts and len(qa_facts) > 0:
        parts.append("--- [ТОЧНІ ФАКТИ (Q&A)] ---")
        parts.append("Використай ці затверджені відповіді:")
        for qa in qa_facts:
            parts.append(f"- Питання: {qa.question}")
            parts.append(f"  Відповідь: {qa.answer}")
        parts.append("")
        
    # 5. Inject RAG Documents
    if rag_docs and len(rag_docs) > 0:
        parts.append("--- [ДОКУМЕНТИ / БАЗА ЗНАНЬ] ---")
        parts.append("Використай наступні уривки документів для формування відповіді:")
        for i, doc in enumerate(rag_docs):
            parts.append(f"[Фрагмент {i+1}]: {doc}")
        parts.append("")
        
        parts.append("")
        
    parts.append("--- [RULES FOR CONTEXT EVALUATION] ---")
    parts.append("Critically evaluate any data provided from the internet or knowledge base.")
    parts.append("If the injected context does not contain the specific answer or technical specs needed to fulfill the user's request, YOU MUST state that the information is missing. DO NOT hallucinate missing details.")
    parts.append("")
        
    parts.append("--- [КІНЕЦЬ СИСТЕМНИХ ІНСТРУКЦІЙ] ---")
    
    return "\n".join(parts)
