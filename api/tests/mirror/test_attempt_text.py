from api.mirror.attempt_text import digest, normalize


def test_attempt_text_identity_ignores_markup_whitespace_and_urls():
    assert normalize("<p>Good&nbsp;work</p><p>https://example.invalid/a</p>") == "Good work"
    assert digest("<p>Good&nbsp;work</p>") == digest("Good\n work https://example.invalid/a")
