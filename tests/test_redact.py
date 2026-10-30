"""Tests for the PII redaction module."""
import pytest
from ghost.ingest.redact import redact, redact_dict


def test_redact_ip():
    assert "[IP]" in redact("Connection from 192.168.1.100 failed")
    assert "192.168.1.100" not in redact("Connection from 192.168.1.100 failed")


def test_redact_email():
    result = redact("user john.doe@example.com logged in")
    assert "[EMAIL]" in result
    assert "john.doe@example.com" not in result


def test_redact_jwt():
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    result = redact(f"Auth header: {jwt}")
    assert "[JWT]" in result
    assert "eyJhbG" not in result


def test_redact_api_key():
    result = redact("api_key=sk-abcdefghijklmnopqrstuvwxyz1234")
    assert "[REDACTED]" in result
    assert "sk-abcdefghijklmno" not in result


def test_redact_aws_key():
    result = redact("key=AKIAIOSFODNN7EXAMPLE")
    assert "[AWS_KEY]" in result


def test_no_false_positives():
    clean = "order_id=abc123 status=pending duration_ms=42.3"
    result = redact(clean)
    assert result == clean


def test_redact_dict():
    obj = {
        "message": "user 192.168.0.1 connected",
        "email": "admin@ghost.local",
        "count": 5,
        "nested": {"key": "api_key=supersecretkey1234567890"},
    }
    result = redact_dict(obj)
    assert "[IP]" in result["message"]
    assert "[EMAIL]" in result["email"]
    assert result["count"] == 5
    assert "[REDACTED]" in result["nested"]["key"]
