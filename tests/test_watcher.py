"""Tests for FormWatcher HTTP polling logic."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.watcher import FormWatcher
from tests.conftest import MockAsyncContextManager


def test_form_watcher_check_open_true():
    watcher = FormWatcher(url="https://docs.google.com/forms/d/e/test/viewform", poll_interval=1)

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.url = "https://docs.google.com/forms/d/e/test/viewform"
    mock_resp.text = AsyncMock(return_value='<html><body><div role="listitem">Question 1</div></body></html>')

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=MockAsyncContextManager(mock_resp))

    async def _mock_get_session():
        return mock_session

    with patch.object(watcher, "_get_session", _mock_get_session):
        is_open = asyncio.run(watcher._check_form_open())
        assert is_open is True


def test_form_watcher_check_closed_by_url():
    watcher = FormWatcher(url="https://docs.google.com/forms/d/e/test/viewform", poll_interval=1)

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.url = "https://docs.google.com/forms/d/e/test/closedform"
    mock_resp.text = AsyncMock(return_value="<html><body>The form is closed</body></html>")

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=MockAsyncContextManager(mock_resp))

    async def _mock_get_session():
        return mock_session

    with patch.object(watcher, "_get_session", _mock_get_session):
        is_open = asyncio.run(watcher._check_form_open())
        assert is_open is False


def test_form_watcher_check_closed_by_marker():
    watcher = FormWatcher(url="https://docs.google.com/forms/d/e/test/viewform", poll_interval=1)

    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.url = "https://docs.google.com/forms/d/e/test/viewform"
    mock_resp.text = AsyncMock(return_value="<html><body>Форма больше не принимает ответы</body></html>")

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=MockAsyncContextManager(mock_resp))

    async def _mock_get_session():
        return mock_session

    with patch.object(watcher, "_get_session", _mock_get_session):
        is_open = asyncio.run(watcher._check_form_open())
        assert is_open is False


def test_form_watcher_stop():
    watcher = FormWatcher(url="https://docs.google.com/forms/d/e/test/viewform", poll_interval=1)
    assert not watcher._stop_event.is_set()
    watcher.stop()
    assert watcher._stop_event.is_set()
