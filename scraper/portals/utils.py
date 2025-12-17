from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any, Callable, Literal, Pattern
from playwright.async_api import Page, Frame, TimeoutError as PlaywrightTimeout, expect, Locator, Error as PlaywrightError
from bs4 import BeautifulSoup

from . import LoginError
from .base import PortalEngine
# ============================================================================
# RETRY DECORATORS
# ============================================================================

# Standard retry configurations
standard_login_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type(PlaywrightTimeout),
    reraise=True
)

standard_fetch_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(PlaywrightTimeout)
)

aggressive_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=3, max=15),
    retry=retry_if_exception_type(PlaywrightTimeout),
    reraise=True
)

# ============================================================================
# CONTEXT MANAGERS
# ============================================================================

@asynccontextmanager
async def tracing_context(page: Page):
    """
    Context manager for Playwright tracing.
    
    Usage:
        async with tracing_context(self.page):
            # login/fetch logic
    """
    await page.context.tracing.start(screenshots=True, snapshots=True)
    try:
        yield
    finally:
        await page.context.tracing.stop()

async def exists(elem: Locator):
    try:
        await expect(elem).to_be_visible(timeout=1000)
    except AssertionError: # this will raise when the elem DNE
        return False
    except PlaywrightError: # this will raise when the locator is something unexpected, like an empty string
        return False
    return True
# ============================================================================
# LOGIN FLOW HELPERS
# ============================================================================

async def universal_login_flow(
    page: Page,
    login_url: str,
    sid: str,
    pw: str,
    username_selector: str,
    password_selector: str,
    *,
    microsoft_callback: Optional[Callable] = None,
    google_callback: Optional[Callable] = None,
    alt_sso_callback: Optional[Callable] = None,
    sso_login_selector: str = None,
    pre_fill_wait: int = 500,
    post_fill_wait: int = 1000,
) -> None:
    """
    Universal login flow for all portals.
    Default: Username / Password input
    Handles SSO Logins (must provide a callback to a login function for the target SSO)

    Args:
        page: Playwright Page object
        login_url: URL to navigate to
        sid: Student/User ID
        pw: Password
        username_selector: CSS selector for username input
        password_selector: CSS selector for password input
        sso_login_selector: CSS selector for an SSO button (Used if it's necessary to click to nav to a new login screen. i.e. 'Sign in with Google')
        microsoft_callback: Microsoft login flow
        google_callback: Google login flow
        alt_sso_callback: Miscellaneous SSO login flow
        pre_fill_wait: Milliseconds to wait before filling form
        post_fill_wait: Milliseconds to wait after filling form
    """
    print('Entered login flow')
    if login_url != page.url: # Only nav if we are not at the target page
        await page.goto(login_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(pre_fill_wait)

    username_field = page.locator(username_selector)
    if await exists(username_field):
        await username_field.fill(sid)
        await page.wait_for_timeout(post_fill_wait)
        # unable to click enter here because that may cause a failed login attempt and clear fields

    password_field = page.locator(password_selector)
    if await exists(password_field):
        await password_field.fill(pw)
        await page.wait_for_timeout(post_fill_wait)
        await password_field.press("Enter")
    else: # either the Username and Password fields are on different screens, or we may have reached an alternate login page (google/microsoft/misc)

        if await exists(username_field): # We may not have moved on from the Username field yet, try to submit on it now
            await username_field.press("Enter")
            await page.wait_for_timeout(post_fill_wait)

        if await exists(password_field): # try the password again after submitting Username field
            await password_field.fill(pw)
            await page.wait_for_timeout(post_fill_wait)
            await password_field.press("Enter")
            return # exit here if we were able to fill the password field and submit

        # otherwise attempt sso
        async def try_sso_login():
            print('try SSO login')
            await wait_after_nav(page)  # just in case we nav'ed, do not continue until the page is populated
            if await use_sso_login(page, microsoft_login_callback=microsoft_callback, google_login_callback=google_callback, check_microsoft=microsoft_callback is not None, check_google=google_callback is not None):
                return
            else:
                raise LoginError('Could not find a suitable SSO login option for student. Maybe you forgot to select an SSO button, or it does not exist')

        if sso_login_selector and await exists(page.locator(sso_login_selector)):
            print(f'attempt alternate login with {sso_login_selector}')
            await page.locator(sso_login_selector).click()
            await wait_after_nav(page)
        try:
            assert (microsoft_callback is not None) or (google_callback is not None)
            await try_sso_login()
        except PlaywrightError: # Normal SSO didn't work, at this point we may need to try to use the alt_sso_callback
            await alt_sso_callback()


async def use_sso_login(
        page: Page,
        check_microsoft: bool,
        check_google: bool,
        microsoft_login_callback: Optional[Callable] = None,
        google_login_callback: Optional[Callable] = None,
) -> bool:
    """
    Apply the appropriate login method based on URL (handles SSO delegation).

    Args:
        page: Playwright Page object
        microsoft_login_callback: Optional async function for Microsoft login
        google_login_callback: Optional async function for Google login
        check_microsoft: If True, detect Microsoft SSO
        check_google: If True, detect Google SSO

    Returns:
        True if a SSO was used, otherwise False
    """
    current_url = page.url.lower()
    if check_microsoft and 'microsoft' in current_url:
        if microsoft_login_callback:
            await microsoft_login_callback()
            return True
        else:
            raise ValueError("Microsoft SSO detected but no microsoft_login_callback provided")
    elif check_google and 'google' in current_url:
        if google_login_callback:
            await google_login_callback()
            return True
        else:
            raise ValueError("Google SSO detected but no google_login_callback provided")
    else:
        return False

async def wait_after_nav(
    page: Page,
    *,
    pattern: str | Pattern[str] | Callable[[str], bool] | None = None,
    timeout: int = 15_000,
    wait_after_load: int = 1000,
    wait_until: Literal['load', 'domcontentloaded', 'networkidle'] = 'load'
) -> None:
    """
    Wait for page load, or successful login redirect.

    Args:
        page: Playwright Page object
        pattern: Pattern to match in the URL
        timeout: Maximum milliseconds to wait for URL change
        wait_after_load: Additional milliseconds to wait after URL matches
        wait_until: Defines what state the page is waiting on ('load', 'networkidle', 'domcontentloaded', 'commit')
    """
    if pattern is not None:
        # print(f"wait for pattern {pattern} in url {page.url}")
        await page.wait_for_url(
            pattern,
            timeout=timeout,
            wait_until=wait_until
        )
        # print("pattern found")
    else:
        await page.wait_for_load_state(wait_until)

    await page.wait_for_timeout(wait_after_load)

async def check_login_errors( 
    page: Page,
    login_error_class,
    *,
    error_texts: Optional[List[str]] = None,
    error_url_patterns: Optional[List[str]] = None,
    success_url_patterns: Optional[List[str]] = None
) -> None:
    """
    Comprehensive login error detection.
    
    Args:
        page: Playwright Page object
        login_error_class: Exception class to raise on login error
        error_texts: List of text strings that indicate login failure
        error_url_patterns: List of URL patterns that indicate login failure
        success_url_patterns: List of URL patterns that indicate login success
    
    Raises:
        login_error_class: If any error condition is detected
    """
    # Check for error text
    if error_texts:
        for text in error_texts:
            if await page.get_by_text(text).count() > 0:
                raise login_error_class(f"Login failed: {text}")
    
    # Check URL patterns
    current_url = page.url
    
    if error_url_patterns:
        for pattern in error_url_patterns:
            if pattern.lower() in current_url.lower():
                raise login_error_class(f"Login failed: still on {pattern} page")
    
    if success_url_patterns:
        if not any(pattern.lower() in current_url.lower() for pattern in success_url_patterns):
            raise login_error_class(f"Login failed: did not reach expected page")


# ============================================================================
# STUDENT SELECTION HELPERS
# ============================================================================

async def select_student_from_dropdown(
    page: Page,
    first_name: str,
    dropdown_selector: str,
    items_selector: str,
    name_selector: str,
    *,
    case_sensitive: bool = False,
    wait_after_click: int = 500
) -> bool:
    """
    Select a student from a dropdown by first name match.
    
    Args:
        page: Playwright Page object
        first_name: First name to search for
        dropdown_selector: CSS selector for dropdown button
        items_selector: CSS selector for dropdown items
        name_selector: CSS selector for student name within each item
        case_sensitive: If True, perform case-sensitive matching
        wait_after_click: Milliseconds to wait after selection
    
    Returns:
        bool: True if student was found and selected, False otherwise
    """
    await page.click(dropdown_selector)
    await page.wait_for_selector(items_selector)
    
    items = page.locator(items_selector)
    n = await items.count()
    
    search_name = first_name if case_sensitive else first_name.lower()
    
    for i in range(n):
        item = items.nth(i)
        name_elem = item.locator(name_selector)
        name = await name_elem.inner_text()
        compare_name = name.strip() if case_sensitive else name.strip().lower()
        
        if search_name in compare_name:
            await item.click()
            await page.wait_for_timeout(wait_after_click)
            return True
    
    return False


# ============================================================================
# PARSING HELPERS
# ============================================================================

async def parse_table_to_dict(
    page: Page,
    table_selector: str,
    row_selector: str,
    key_selector: str,
    value_selector: str,
    *,
    use_soup: bool = True,
    strip_whitespace: bool = True
) -> Dict[str, str]:
    """
    Generic table parser for grade extraction.
    
    Args:
        page: Playwright Page object
        table_selector: CSS selector for the table container
        row_selector: CSS selector for table rows
        key_selector: CSS selector for the key column (e.g., course name)
        value_selector: CSS selector for the value column (e.g., grade)
        use_soup: If True, use BeautifulSoup; if False, use Playwright locators
        strip_whitespace: If True, strip whitespace from extracted text
    
    Returns:
        Dict mapping keys to values
    """
    if use_soup:
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        container = soup.select_one(table_selector)
        if not container:
            return {}
        rows = container.select(row_selector)
        
        parsed = {}
        for row in rows:
            key_elem = row.select_one(key_selector)
            value_elem = row.select_one(value_selector)
            if key_elem and value_elem:
                key = key_elem.text.strip() if strip_whitespace else key_elem.text
                value = value_elem.text.strip() if strip_whitespace else value_elem.text
                parsed[key] = value
    else:
        # Playwright locator version
        rows = await page.locator(f"{table_selector} {row_selector}").all()
        
        parsed = {}
        for row in rows:
            try:
                key = await row.locator(key_selector).text_content()
                value = await row.locator(value_selector).text_content()
                if key and value:
                    if strip_whitespace:
                        key = key.strip()
                        value = value.strip()
                    parsed[key] = value
            except: # TODO specify exception
                continue
    
    return parsed

def percent_from_letter_grade(letter_grade: str) -> int:
    minus = letter_grade.endswith("-")
    plus = letter_grade.endswith("+")
    modifier = -5 if minus else 5 if plus else 0
    grade = 95
    if modifier != 0:
        letter_grade = letter_grade.replace("-", "")
        letter_grade = letter_grade.replace("+", "")
    match letter_grade:
        case 'A':
            pass
        case 'B':
            grade -= 11  # 89
        case 'C':
            grade -= 21  # 79
        case 'D':
            grade -= 31  # 69
        case 'F':
            grade -= 40  # 60
        case _:
            grade = -1
    return grade + modifier if grade > 0 else grade

def canonicalize_grade(grade_text: str) -> float | None:
    """
    Convert any grade format (%, letter, number) to float percentage.
    
    Args:
        grade_text: Grade string in any format (e.g., "93.4%", "A", "93")
    
    Returns:
        float: Numeric percentage grade
    """
    grade_text = grade_text.strip()
    # Remove % sign if present
    if "%" or "(" or ")" in grade_text.strip():
        return float(grade_text
                     .replace("%", "")
                     .replace("(", "")
                     .replace(")", ""))
    
    # Check if it's a number
    try:
        return float(grade_text)
    except ValueError:
        pass
    
    # Must be a letter grade
    grade = percent_from_letter_grade(grade_text)
    return float(grade) if grade >= 0 else None


# ============================================================================
# IFRAME HELPERS
# ============================================================================

async def get_frame_by_url_pattern(
    page: Page,
    iframe_selector: str,
    url_pattern: str,
    *,
    timeout: int = 15_000
) -> Optional[Frame]:
    """
    Find and return an iframe matching a URL pattern.
    
    Args:
        page: Playwright Page object
        iframe_selector: CSS selector for the iframe element
        url_pattern: String pattern to match in the iframe URL
        timeout: Maximum milliseconds to wait for iframe
    
    Returns:
        Frame object if found, None otherwise
    
    Example:
        frame = await get_frame_by_url_pattern(
            self.page,
            "iframe#main-workspace",
            "/grades",
            timeout=15_000
        )
        if frame:
            html = await frame.content()
    """
    try:
        await page.wait_for_selector(iframe_selector, timeout=timeout)
        frame = page.frame(
            url=lambda u: url_pattern in u if u else False
        )
        return frame
    except PlaywrightTimeout:
        return None


async def get_frame_content_as_soup(
    page: Page,
    iframe_selector: str,
    url_pattern: str,
    *,
    timeout: int = 15_000
) -> Optional[BeautifulSoup]:
    """
    Get BeautifulSoup object from an iframe's content.
    
    Args:
        page: Playwright Page object
        iframe_selector: CSS selector for the iframe element
        url_pattern: String pattern to match in the iframe URL
        timeout: Maximum milliseconds to wait for iframe
    
    Returns:
        BeautifulSoup object if frame found, None otherwise
    
    Example:
        soup = await get_frame_content_as_soup(
            self.page,
            "iframe#grades-frame",
            "/studentgrades",
            timeout=10_000
        )
        if soup:
            grades = soup.select(".grade-item")
    """
    frame = await get_frame_by_url_pattern(page, iframe_selector, url_pattern, timeout=timeout)
    if frame:
        html = await frame.content()
        return BeautifulSoup(html, "html.parser")
    return None