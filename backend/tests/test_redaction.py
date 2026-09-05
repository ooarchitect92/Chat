from northstar_api.services.redaction import mask_sensitive_text


def test_masks_common_sensitive_values_without_masking_normal_numbers() -> None:
    value = (
        "Email me at person@example.com; password: hunter2; "
        "key nvapi-abcdefghijklmnopqrst; card 4242 4242 4242 4242; order 1234567890123."
    )

    masked = mask_sensitive_text(value)

    assert "person@example.com" not in masked
    assert "hunter2" not in masked
    assert "nvapi-" not in masked
    assert "4242 4242 4242 4242" not in masked
    assert "1234567890123" in masked
