import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.watcher import FormWatcher


class MockAsyncContextManager:
    def __init__(self, return_value):
        self.return_value = return_value

    async def __aenter__(self):
        return self.return_value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


def test_form_watcher_check_open_true():
    watcher = FormWatcher(url="https://docs.google.com/forms/d/e/test/viewform", poll_interval=1)
    
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.url = "https://docs.google.com/forms/d/e/test/viewform"
    mock_resp.text = AsyncMock(return_value='<html><body><div role="listitem">Question 1</div></body></html>')
    
    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=MockAsyncContextManager(mock_resp))
    
    with patch("aiohttp.ClientSession", return_value=MockAsyncContextManager(mock_session)):
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
    
    with patch("aiohttp.ClientSession", return_value=MockAsyncContextManager(mock_session)):
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
    
    with patch("aiohttp.ClientSession", return_value=MockAsyncContextManager(mock_session)):
        is_open = asyncio.run(watcher._check_form_open())
        assert is_open is False


def test_form_watcher_stop():
    watcher = FormWatcher(url="https://docs.google.com/forms/d/e/test/viewform", poll_interval=1)
    assert not watcher._stop_event.is_set()
    watcher.stop()
    assert watcher._stop_event.is_set()
