"""Schema names (alembic-migrations T1): DEVICE_ID check and the quoted schema name."""

import pytest

from ota_server.db.names import quoted_schema_name, schema_name


@pytest.mark.parametrize("value", ["", "a" * 41, "Abc", 'a"b'])
def test_invalid_device_id_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        schema_name(value)


def test_valid_device_id_gives_quoted_schema_name() -> None:
    assert quoted_schema_name("abc_1").as_string() == '"ota_abc_1"'


def test_forty_character_device_id_is_accepted() -> None:
    device_id = "abcdefghijklmnopqrstuvwxyz0123456789_abc"
    assert len(device_id) == 40
    assert schema_name(device_id) == "ota_" + device_id
