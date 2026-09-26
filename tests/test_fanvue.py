from unittest.mock import MagicMock, patch

import pytest

from posting.fanvue import FanvueApiError, FanvueClient, build_post_url


def _mock_response(status_code=200, json_data=None, content=b"{}", headers=None):
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = json_data or {}
    response.content = content
    response.text = str(json_data)
    response.headers = headers or {}
    return response


@pytest.fixture
def client():
    return FanvueClient(api_token="test-token")


def test_client_sets_auth_headers(client):
    assert client._session.headers["Authorization"] == "Bearer test-token"
    assert client._session.headers["X-Fanvue-API-Version"] == "2025-06-26"


def test_get_me_calls_correct_endpoint(client):
    with patch.object(client._session, "request", return_value=_mock_response(json_data={"id": "u1"})) as mocked:
        result = client.get_me()

    assert result == {"id": "u1"}
    mocked.assert_called_once_with("GET", "https://api.fanvue.com/users/me")


def test_request_raises_on_error_response(client):
    with patch.object(client._session, "request", return_value=_mock_response(status_code=401)):
        with pytest.raises(FanvueApiError):
            client.get_me()


def test_upload_media_single_part(client, tmp_path):
    file_path = tmp_path / "look.jpg"
    file_path.write_bytes(b"small-file-content")

    init_response = _mock_response(json_data={"uploadId": "up1"})
    part_url_response = _mock_response(json_data={"url": "https://s3.example.com/part1"})
    finalize_response = _mock_response(json_data={"mediaUuid": "media-uuid-1"})

    put_response = _mock_response(status_code=200, headers={"ETag": "etag-1"})

    with patch.object(
        client._session, "request", side_effect=[init_response, part_url_response, finalize_response]
    ) as mocked_request, patch("posting.fanvue.requests.put", return_value=put_response) as mocked_put:
        media_uuid = client.upload_media(file_path, media_type="image")

    assert media_uuid == "media-uuid-1"
    assert mocked_request.call_count == 3
    mocked_put.assert_called_once_with("https://s3.example.com/part1", data=b"small-file-content")


def test_upload_media_multiple_parts(client, tmp_path):
    from posting.fanvue import PART_SIZE_BYTES

    file_path = tmp_path / "big.mp4"
    file_path.write_bytes(b"x" * (PART_SIZE_BYTES + 100))

    init_response = _mock_response(json_data={"uploadId": "up2"})
    part1_url = _mock_response(json_data={"url": "https://s3.example.com/part1"})
    part2_url = _mock_response(json_data={"url": "https://s3.example.com/part2"})
    finalize_response = _mock_response(json_data={"mediaUuid": "media-uuid-2"})
    put_response = _mock_response(status_code=200, headers={"ETag": "etag"})

    with patch.object(
        client._session,
        "request",
        side_effect=[init_response, part1_url, part2_url, finalize_response],
    ), patch("posting.fanvue.requests.put", return_value=put_response) as mocked_put:
        media_uuid = client.upload_media(file_path, media_type="video")

    assert media_uuid == "media-uuid-2"
    assert mocked_put.call_count == 2


def test_upload_media_raises_when_part_upload_fails(client, tmp_path):
    file_path = tmp_path / "look.jpg"
    file_path.write_bytes(b"content")

    init_response = _mock_response(json_data={"uploadId": "up3"})
    part_url_response = _mock_response(json_data={"url": "https://s3.example.com/part1"})
    failed_put = _mock_response(status_code=500)

    with patch.object(
        client._session, "request", side_effect=[init_response, part_url_response]
    ), patch("posting.fanvue.requests.put", return_value=failed_put):
        with pytest.raises(FanvueApiError):
            client.upload_media(file_path, media_type="image")


def test_wait_for_media_ready_returns_true_when_ready(client):
    responses = [_mock_response(json_data={"status": "processing"}), _mock_response(json_data={"status": "ready"})]
    with patch.object(client._session, "request", side_effect=responses):
        result = client.wait_for_media_ready("media-1", timeout_seconds=10, poll_interval_seconds=0)

    assert result is True


def test_wait_for_media_ready_times_out(client):
    with patch.object(client._session, "request", return_value=_mock_response(json_data={"status": "processing"})):
        result = client.wait_for_media_ready("media-1", timeout_seconds=0, poll_interval_seconds=0)

    assert result is False


def test_create_post_builds_expected_body(client):
    with patch.object(client._session, "request", return_value=_mock_response(json_data={"id": "post-1"})) as mocked:
        result = client.create_post(
            audience="subscribers",
            text="hello",
            media_uuids=["m1"],
            price_cents=499,
        )

    assert result == {"id": "post-1"}
    _, kwargs = mocked.call_args
    assert kwargs["json"] == {
        "audience": "subscribers",
        "text": "hello",
        "mediaUuids": ["m1"],
        "price": 499,
    }


def test_build_post_url():
    url = build_post_url("https://www.fanvue.com/{handle}", handle="mycreator", uuid="abc123")
    assert url == "https://www.fanvue.com/mycreator"
