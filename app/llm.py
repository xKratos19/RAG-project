from __future__ import annotations

from google import genai
from google.genai import types

from app.config import settings
from app.models import Chunk, ConversationTurn, StyleHints, Usage

_client: genai.Client | None = None

_SYSTEM = (
    "Ești un asistent juridic specializat în legislația românească. "
    "Răspunde DOAR pe baza fragmentelor furnizate. "
    "Formulează răspunsul în română, text simplu fără Markdown, fără liste, fără bold. "
    "Nu inventa informații absente din fragmente. "
    "Dacă informația nu există în fragmente, spune că nu ai informații suficiente."
)

# Gemini 2.5 Flash approximate pricing (USD per 1M tokens)
_PRICE_IN = 0.075
_PRICE_OUT = 0.30


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


async def generate_answer(
    question: str,
    chunks: list[Chunk],
    history: list[ConversationTurn],
    hints: StyleHints,
) -> tuple[str, Usage]:
    """Call Gemini 2.5 Flash to produce a Romanian prose answer with inline citations."""
    context = "\n\n".join(f"[{i + 1}] {c.content}" for i, c in enumerate(chunks))

    cite_instr = (
        "Inserează referințele [1], [2] etc. inline, la finalul frazei în care folosești informația."
        if hints.cite_inline
        else ""
    )
    tone_instr = "Ton formal, juridic." if hints.tone == "formal" else "Ton simplu, accesibil."

    history_block = ""
    if history:
        lines = [f"{t.role.capitalize()}: {t.content}" for t in history]
        history_block = "\nIstoricul conversației:\n" + "\n".join(lines) + "\n"

    prompt = (
        f"{_SYSTEM}\n\n"
        f"Fragmente relevante:\n{context}\n"
        f"{history_block}\n"
        f"Întrebare: {question}\n\n"
        f"{tone_instr} {cite_instr} "
        f"Răspunsul nu trebuie să depășească {hints.answer_max_chars} caractere."
    )

    response = await _get_client().aio.models.generate_content(
        model=settings.llm_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=2048,
        ),
    )

    answer = (response.text or "").strip()
    if answer and not answer.endswith("."):
        answer += "."

    meta = response.usage_metadata
    in_tok = (meta.prompt_token_count or 0) if meta else 0
    out_tok = (meta.candidates_token_count or 0) if meta else 0
    cost = round((in_tok * _PRICE_IN + out_tok * _PRICE_OUT) / 1_000_000, 6)

    return answer, Usage(
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost,
        model_id=settings.llm_model,
    )
