from prompt2midi.providers.secrets import REDACTED, mask_secrets

KEY = "sk-ant-api03-ABCDEFGHIJKLMNOP1234567890xyz"  # pragma: allowlist secret


def test_full_key_is_removed():
    assert KEY not in mask_secrets(f"bad key {KEY} here", [KEY]) and REDACTED in mask_secrets(f"bad key {KEY}", [KEY])


def test_fragments_of_six_or_more_characters_are_removed():
    for frag in (KEY[:14], KEY[-8:], KEY[10:20], "sk-ant-...xyz"):
        out = mask_secrets(f"Incorrect API key provided: {frag}", [KEY])
        assert KEY[:6] not in out or frag.startswith("sk-ant-...")  # prefix handled by the pattern rule below
        for i in range(len(KEY) - 5):
            if KEY[i:i + 6] in frag:
                assert KEY[i:i + 6] not in out, (frag, out)


def test_header_and_bearer_forms_are_removed_even_for_unknown_keys():
    for text in ("x-api-key: abcdefghijkl", "Authorization: Bearer abcdefgh12345678", "api-key=zzzzzzzzzz", "token sk-proj-abcdefghijklmn"):
        out = mask_secrets(text)
        assert "abcdefgh" not in out and "zzzzzz" not in out and "abcdefghijklmn" not in out, out


def test_short_or_missing_secrets_do_not_wipe_the_message():
    assert mask_secrets("hello world", [None, "", "abc"]) == "hello world"


def test_unrelated_text_is_untouched():
    assert mask_secrets("rate limit exceeded, retry in 20s", [KEY]) == "rate limit exceeded, retry in 20s"
