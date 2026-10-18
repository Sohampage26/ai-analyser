from analyser.log_extractor import error_lines, extract_relevant, fingerprint


def test_short_log_returned_whole():
    log = "line1\nERROR: boom\nline3"
    assert extract_relevant(log, max_chars=1000) == log


def test_long_log_keeps_error_context_and_tail():
    lines = [f"noise {i}" for i in range(20_000)]
    lines[10_000] = "[ERROR] cannot find symbol Foo"
    out = extract_relevant("\n".join(lines), max_chars=20_000, tail=50, context=3)
    assert "cannot find symbol Foo" in out
    assert "noise 19999" in out
    assert "noise 5000\n" not in out
    assert len(out) <= 20_100


def test_ansi_stripped():
    assert error_lines("\x1b[31mERROR: red\x1b[0m") == ["ERROR: red"]


def test_fingerprint_ignores_volatile_tokens():
    a = "2024-01-01T10:00:00Z ERROR: Connection refused to 10.0.0.12:8080 in /home/a/ws/build/x.c"
    b = "2024-02-03T11:22:33Z ERROR: Connection refused to 10.0.0.99:8081 in /var/lib/ws/build/x.c"
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) != fingerprint("ERROR: No space left on device")
