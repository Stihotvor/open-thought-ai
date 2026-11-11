import pytest
from model_env import ENDPOINTS as BASE

from ota_server.endpoints import ConfigError, load_roles


def env(**extra):
    return {**BASE, **extra}


def test_unset_role_is_skipped_and_default_endpoint_used():
    roles = load_roles(BASE)
    assert set(roles) == {"chat", "stt", "tts"}
    assert roles["stt"].base_url == "https://api.example.test/v1"
    assert roles["stt"].key == "default-key"


def test_own_base_url_without_own_key_fails():
    with pytest.raises(ConfigError, match="own key"):
        load_roles(env(OTA_STT_BASE_URL="https://stt.example.test/v1"))


def test_own_base_url_with_own_key():
    roles = load_roles(env(OTA_STT_BASE_URL="https://stt.example.test/v1", OTA_STT_KEY="k2"))
    assert roles["stt"].key == "k2"
    assert roles["chat"].key == "default-key"


def test_http_to_private_ip_needs_flag():
    lan = env(OTA_STT_BASE_URL="http://192.168.1.5:8000/v1", OTA_STT_KEY="k")
    with pytest.raises(ConfigError, match="http://"):
        load_roles(lan)
    roles = load_roles({**lan, "OTA_ALLOW_INSECURE_MODEL_ENDPOINT": "1"})
    assert roles["stt"].base_url == "http://192.168.1.5:8000/v1"


@pytest.mark.parametrize("host", ["8.8.8.8", "api.example.test"])
def test_http_to_public_host_fails_even_with_flag(host):
    bad = env(
        OTA_STT_BASE_URL=f"http://{host}/v1", OTA_STT_KEY="k", OTA_ALLOW_INSECURE_MODEL_ENDPOINT="1"
    )
    with pytest.raises(ConfigError, match="http://"):
        load_roles(bad)


def test_http_on_loopback_is_allowed():
    roles = load_roles(env(OTA_TTS_BASE_URL="http://127.0.0.1:5000/v1", OTA_TTS_KEY="k"))
    assert roles["tts"].host == "127.0.0.1"


@pytest.mark.parametrize(
    ("url", "match"),
    [
        ("https://user:pw@api.example.test/v1", "credentials"),
        ("https://api.example.test/v1?key=x", "query"),
        ("ftp://api.example.test/v1", "http"),
    ],
)
def test_bad_urls_are_rejected(url, match):
    with pytest.raises(ConfigError, match=match):
        load_roles(env(OTA_MODEL_BASE_URL=url))


@pytest.mark.parametrize("rate", ["abc", "nan", "inf", "0", "-1"])
def test_bad_rate_is_rejected(rate):
    with pytest.raises(ConfigError, match="rate"):
        load_roles(env(OTA_CHAT_RATE=rate, OTA_STT_RATE=rate, OTA_TTS_RATE=rate))


def test_burst_has_upper_bound():
    with pytest.raises(ConfigError, match="burst"):
        load_roles(env(OTA_CHAT_BURST="1000", OTA_STT_BURST="1000", OTA_TTS_BURST="1000"))


@pytest.mark.parametrize("host", ["[::ffff:8.8.8.8]", "169.254.169.254", "0.0.0.0"])
def test_http_flag_allows_only_lan_ranges(host):
    bad = env(
        OTA_STT_BASE_URL=f"http://{host}/v1", OTA_STT_KEY="k", OTA_ALLOW_INSECURE_MODEL_ENDPOINT="1"
    )
    with pytest.raises(ConfigError, match="http://"):
        load_roles(bad)


def test_missing_key_for_remote_endpoint_fails():
    with pytest.raises(ConfigError, match="no key"):
        load_roles({k: v for k, v in BASE.items() if k != "OTA_MODEL_KEY"})


def test_no_key_is_fine_on_loopback():
    local = {k: v for k, v in BASE.items() if k != "OTA_MODEL_KEY"}
    local["OTA_MODEL_BASE_URL"] = "http://127.0.0.1:11434/v1"
    assert load_roles(local)["chat"].key == ""


@pytest.mark.parametrize("port", ["99999", "0", "abc"])
def test_bad_port_is_rejected(port):
    with pytest.raises(ConfigError, match="port"):
        load_roles(env(OTA_MODEL_BASE_URL=f"https://api.example.test:{port}/v1"))


@pytest.mark.parametrize("key", ["klucz-ł", "a b", "a\nb"])
def test_bad_key_characters_are_rejected(key):
    with pytest.raises(ConfigError, match="ASCII"):
        load_roles(env(OTA_MODEL_KEY=key))


def test_key_not_in_repr():
    assert "default-key" not in repr(load_roles(BASE))
