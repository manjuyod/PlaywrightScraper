"""
GPT-assisted helper: select a student card by first name using the mouse.
DEBUG version – prints every major step.
"""
from __future__ import annotations
import json, os, textwrap, asyncio
from typing import Optional, List
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout
import openai

openai.api_key = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

_SYSTEM = textwrap.dedent(
    """\
    You are an expert browser-automation strategist.
    You will be given a numbered list of student cards and a target first name.
    Respond with JSON **exactly**:  {"index": <number>}  where <number> is the
    zero-based index of the card whose text contains that first name (case-insensitive).
    If no card matches, respond with {}."""
)

async def click_student_card(page: Page, first_name: str) -> bool:
    """Return True if GPT successfully clicked the matching card."""
    if openai.api_key is None:
        print("[GPT-NAV]  No OPENAI_API_KEY – skipping GPT navigation.")
        return False

    cards: List[str] = await page.locator("app-student-summary-button").all_inner_texts()
    if not cards:
        print("[GPT-NAV]  No student buttons found – skipping GPT navigation.")
        return False

    numbered = "\n".join(f"{i}: {t.strip()}" for i, t in enumerate(cards))
    user_prompt = (
        f"Student cards:\n{numbered}\n\n"
        f"Target first name: {first_name!r}\n"
        "Choose the matching index."
    )

    print("[GPT-NAV]  Calling OpenAI …")
    try:
        rsp = await asyncio.to_thread(
            openai.chat.completions.create,
            model=MODEL,
            messages=[{"role": "system", "content": _SYSTEM},
                      {"role": "user", "content": user_prompt}],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        content = rsp.choices[0].message.content
        print("[GPT-NAV]  Raw model response:", content)
        data = json.loads(content)
        idx = data.get("index")
    except Exception as e:
        print("[GPT-NAV]  OpenAI call failed:", e)
        return False

    if idx is None or not (0 <= idx < len(cards)):
        print("[GPT-NAV]  Model did not return a valid index.")
        return False

    print(f"[GPT-NAV]  Clicking card index {idx} – text={cards[idx]!r}")
    target = page.locator("app-student-summary-button").nth(idx)
    try:
        box = await target.bounding_box()
        if box:
            await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            await page.mouse.down()
            await page.mouse.up()
        else:
            # fallback: direct click
            await target.click()
        await page.wait_for_url(lambda u: "personID=" in u, timeout=10_000)
        print("[GPT-NAV]  URL now has personID – success.")
        return True
    except PlaywrightTimeout:
        print("[GPT-NAV]  URL did not change – likely wrong click.")
        return False
