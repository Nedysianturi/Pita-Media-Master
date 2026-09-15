"""
Unit & Integration tests untuk Facebook Fanspage Client dan PublisherAgent.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path
from tempfile import NamedTemporaryFile

from providers.facebook_client import FacebookClient
from agents.publisher.publisher import PublisherAgent


@pytest.fixture
def sample_temp_image():
    with NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"fake_image_data")
        temp_path = f.name
    yield temp_path
    Path(temp_path).unlink(missing_ok=True)


@pytest.fixture
def sample_temp_video():
    with NamedTemporaryFile(suffix=".mp4", delete=False) as f:
        f.write(b"fake_video_data")
        temp_path = f.name
    yield temp_path
    Path(temp_path).unlink(missing_ok=True)


def test_facebook_client_config_status():
    client_unconfigured = FacebookClient(page_id="", access_token="")
    assert not client_unconfigured.is_configured

    client_configured = FacebookClient(page_id="1234567890", access_token="EAABxyz123")
    assert client_configured.is_configured


@pytest.mark.asyncio
async def test_facebook_publish_multi_photo_carousel(sample_temp_image):
    client = FacebookClient(page_id="12345", access_token="token_xyz")

    # Mock httpx.AsyncClient post responses
    mock_photo_resp = MagicMock()
    mock_photo_resp.status_code = 200
    mock_photo_resp.json.return_value = {"id": "photo_101"}

    mock_feed_resp = MagicMock()
    mock_feed_resp.status_code = 200
    mock_feed_resp.json.return_value = {"id": "12345_post_999"}

    with patch("httpx.AsyncClient.post", side_effect=[mock_photo_resp, mock_feed_resp]):
        res = await client.publish_multi_photo_carousel(
            image_paths=[sample_temp_image],
            caption="Uji coba caption Pita Cerita Facebook",
        )
        assert res["platform"] == "facebook"
        assert res["post_id"] == "12345_post_999"
        assert "facebook.com/12345_post_999" in res["post_url"]
        assert res["type"] == "carousel"
        assert res["attached_photos"] == ["photo_101"]


@pytest.mark.asyncio
async def test_facebook_publish_video(sample_temp_video):
    client = FacebookClient(page_id="12345", access_token="token_xyz")

    mock_video_resp = MagicMock()
    mock_video_resp.status_code = 200
    mock_video_resp.json.return_value = {"id": "vid_888"}

    with patch("httpx.AsyncClient.post", return_value=mock_video_resp):
        res = await client.publish_video(
            video_path=sample_temp_video,
            title="Video Transformasi",
            description="Proses perubahan bertahap",
        )
        assert res["platform"] == "facebook"
        assert res["post_id"] == "vid_888"
        assert "videos/vid_888" in res["post_url"]
        assert res["type"] == "video"


@pytest.mark.asyncio
async def test_publisher_agent_dispatches_to_facebook(sample_temp_image):
    agent = PublisherAgent()
    agent.publishers["facebook"].publish = AsyncMock(return_value={
        "platform": "facebook",
        "status": "PUBLISHED",
        "external_post_id": "post_777",
        "post_id": "post_777",
        "permalink": "https://www.facebook.com/post_777",
        "response_metadata": {"simulated": True},
        "error_message": None,
    })

    payload = {
        "title": "Kisah Haru",
        "pilar": "pita_cerita",
        "media_paths": [sample_temp_image],
        "caption": "Cerita lengkap tentang perjuangan.",
    }

    result = await agent.publish_content(
        content_id="test_cnt_1",
        content_payload=payload,
        qc_verdict="PASSED",
        platform="facebook",
    )

    assert result["platform"] == "facebook"
    assert result["post_url"] == "https://www.facebook.com/post_777"
    assert result["remote_post_id"] == "post_777"
    assert result["publish_status"] in ["PUBLISHED", "VERIFIED", "SIMULATED", "LIVE_PUBLISHED", "LIVE_VERIFIED"]
