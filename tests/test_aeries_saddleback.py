import asyncio
from unittest.mock import AsyncMock

import pytest

from scraper.portals.aeries import Aeries
from scraper.portals.base import PortalEngine


LEGACY = 'https://familyportal.svusd.org/ParentPortal/LoginParent.aspx'
CURRENT = 'https://saddlebackvalleyusd.aeries.net/student/LoginParent.aspx'


@pytest.mark.parametrize('url,expected', [
    (LEGACY, CURRENT),
    (CURRENT, CURRENT),
    (LEGACY.replace('familyportal.svusd.org', 'familyportal.svusd.org.evil.test'), None),
    (LEGACY.replace('https:', 'http:'), None),
    (LEGACY.replace('familyportal.svusd.org', 'familyportal.svusd.org:444'), None),
    (LEGACY.replace('familyportal.svusd.org', 'user@familyportal.svusd.org'), None),
])
def test_only_verified_saddleback_legacy_entry_is_replaced(monkeypatch, url, expected):
    login = AsyncMock()
    monkeypatch.setattr(PortalEngine, 'login', login)
    engine = Aeries(None, 'student', 'password', url)
    asyncio.run(engine.login(first_name='Student'))
    assert engine.login_url == (expected or url)
    login.assert_awaited_once_with(first_name='Student')
