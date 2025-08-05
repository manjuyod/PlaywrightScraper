# utils/captcha_guard.py
import asyncio
from playwright.async_api import Page

class CaptchaError(RuntimeError):
    """Raised when a CAPTCHA or bot-check page is detected."""

CAPTCHA_MARKERS = [
    "please verify you are a human",
    "are you a robot",
    "captcha",
    "recaptcha",
    "cloudflare"
]

async def ensure_not_captcha(page: Page) -> None:
    """Throw CaptchaError if typical CAPTCHA markers are present."""
    html = (await page.content()).lower()
    if any(token in html for token in CAPTCHA_MARKERS):
        await page.screenshot(path="captcha.png")
        raise CaptchaError("CAPTCHA detected")
