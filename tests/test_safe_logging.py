from scraper.safe_logging import suppress_portal_output


def test_portal_output_is_discarded_at_the_worker_boundary(capsys):
    with suppress_portal_output():
        print("portal-user portal-password raw-html")

    captured = capsys.readouterr()
    assert "portal-user" not in captured.out + captured.err
    assert "portal-password" not in captured.out + captured.err
    assert "raw-html" not in captured.out + captured.err
