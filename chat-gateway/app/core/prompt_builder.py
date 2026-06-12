from typing import List, Dict, Any
from app.models.tenant import BotSetting
from app.models.knowledge import QaPair

def build_system_prompt(settings: BotSetting, qa_facts: List[QaPair] = None, rag_docs: List[str] = None, include_grounding: bool = True) -> str:
    """
    Builds the final system prompt by injecting Business Rules, Marketing Protocols,
    Escalation Policies, and found Facts (Q&A/RAG).

    include_grounding=False (smalltalk/greetings, spec §9.5): persona and business
    rules only — no escalation/anti-hallucination blocks, so the model just talks.
    """
    base_prompt = settings.system_prompt if settings and settings.system_prompt else "Ти корисний асистент."
    
    parts = [base_prompt, ""]
    
    meta = settings.meta if settings and settings.meta else {}
    
    # 1. Inject Business Rules (Strict Constraints)
    if settings and settings.business_rules:
        tpl_business_header = meta.get("tpl_business_header", "--- [БІЗНЕС ПРАВИЛА ТА ОБМЕЖЕННЯ] ---\nТи ПОВИНЕН неухильно дотримуватись наступних правил. Ніколи не порушуй їх:")
        parts.append(tpl_business_header)
        parts.append(settings.business_rules)
        parts.append("")
    
    # 2. Inject Marketing Protocols (Upsell)
    if settings and settings.marketing_rules:
        tpl_marketing_header = meta.get("tpl_marketing_header", "--- [МАРКЕТИНГОВІ ПРОТОКОЛИ] ---\nЯкщо це доречно і природно звучить у контексті діалогу, спробуй застосувати ці настанови (ненав'язливо):")
        parts.append(tpl_marketing_header)
        parts.append(settings.marketing_rules)
        parts.append("")
    
    # 3. Inject Escalation Policy Context (only when answering from knowledge)
    if include_grounding and settings and settings.escalation_prompt:
        tpl_escalation_header = meta.get("tpl_escalation_header", "--- [ПОЛІТИКА ЕСКАЛАЦІЇ] ---\nЯкщо у тебе немає інформації для відповіді на питання клієнта, не вигадуй. Замість цього використай цю настанову своїми словами:")
        parts.append(tpl_escalation_header)
        parts.append(f"Настанова: {settings.escalation_prompt}")
        parts.append("")
        
    # 4. Inject Knowledge Base Facts (Q&A)
    if qa_facts and len(qa_facts) > 0:
        tpl_qa_header = meta.get("tpl_qa_header", "--- [ТОЧНІ ФАКТИ (Q&A)] ---\nВикористай ці затверджені відповіді:")
        parts.append(tpl_qa_header)
        for qa in qa_facts:
            parts.append(f"- Питання: {qa.question}")
            parts.append(f"  Відповідь: {qa.answer}")
        parts.append("")
        
    # 5. Inject RAG Documents
    if rag_docs and len(rag_docs) > 0:
        tpl_rag_header = meta.get("tpl_rag_header", "--- [ДОКУМЕНТИ / БАЗА ЗНАНЬ] ---\nВикористай наступні уривки документів для формування відповіді:")
        parts.append(tpl_rag_header)
        for i, doc in enumerate(rag_docs):
            parts.append(f"[Фрагмент {i+1}]: {doc}")
        parts.append("")

    # Anti-hallucination rules only make sense when factual context is in play.
    if include_grounding:
        tpl_evaluation_rules = meta.get("tpl_evaluation_rules", "--- [ABSOLUTE RULE: HALLUCINATION BAN] ---\n1. If the question is about technical specs, compatibility, availability, or prices - use ONLY the data from the blocks above (Web Search, Price, FAQ).\n2. If there is NO direct answer in the provided context - YOU ARE STRICTLY FORBIDDEN from inventing it from your own memory.\n3. If data is missing, answer in your style, something like: \"I don't have exact technical data on this hardware, I need to see it\" or ask the client to provide the exact model.\n4. NO assumptions about compatibility. Either 100% confirmation in context, or you don't know.")
        parts.append(tpl_evaluation_rules)
        parts.append("")
        
    tpl_footer = meta.get("tpl_footer", "--- [END OF SYSTEM INSTRUCTIONS] ---")
    parts.append(tpl_footer)
    
    return "\n".join(parts)
