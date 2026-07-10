from __future__ import annotations

import io

from scripts import operator_credentials


def test_operator_cli_reads_credentials_from_stdin_without_echoing_them(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(
        operator_credentials,
        "put_alternate_credentials",
        lambda student_id, payload: captured.update(
            {"student_id": student_id, "payload": payload}
        ),
    )
    secret_input = io.StringIO(
        '{"portal_url":"https://school.example.test/login",'
        '"username":"alternate-user","password":"alternate-password"}'
    )

    assert operator_credentials.main(["set", "42", "--stdin"], stdin=secret_input) == 0

    assert captured["student_id"] == 42
    assert captured["payload"]["username"] == "alternate-user"
    output = capsys.readouterr()
    assert "alternate-user" not in output.out + output.err
    assert "alternate-password" not in output.out + output.err


def test_operator_cli_delete_sends_no_credential_payload(monkeypatch):
    deleted = []
    monkeypatch.setattr(
        operator_credentials,
        "delete_alternate_credentials",
        lambda student_id: deleted.append(student_id),
    )

    assert operator_credentials.main(["delete", "42"]) == 0
    assert deleted == [42]
