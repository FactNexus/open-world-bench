from owrb.url_safety import check_url


def resolve_public(host: str) -> list[str]:
    return ["93.184.216.34"]


def test_public_https_url_is_allowed() -> None:
    assert check_url("https://example.com/page", resolve_public) is None


def test_non_http_schemes_are_rejected() -> None:
    for url in ("ftp://example.com/x", "file:///etc/passwd", "gopher://example.com"):
        assert check_url(url, resolve_public) is not None


def test_literal_private_and_loopback_addresses_are_rejected() -> None:
    for url in (
        "http://127.0.0.1/admin",
        "http://10.0.0.5/internal",
        "http://172.16.3.2/",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[fd00::1]/",
        "http://0.0.0.0/",
    ):
        assert check_url(url, resolve_public) is not None, url


def test_hostname_resolving_to_private_address_is_rejected() -> None:
    assert check_url("https://internal.example", lambda host: ["10.1.2.3"]) is not None
    # One bad address among several poisons the host.
    assert (
        check_url("https://mixed.example", lambda host: ["93.184.216.34", "127.0.0.1"])
        is not None
    )


def test_unresolvable_host_is_rejected() -> None:
    assert check_url("https://nowhere.example", lambda host: []) is not None


def test_embedded_credentials_are_rejected() -> None:
    assert check_url("https://user:pass@example.com/", resolve_public) is not None
