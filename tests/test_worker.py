"""Celery is configured off the app's Redis database. No broker connection."""

from __future__ import annotations

from backend.config import celery_broker_url, celery_result_url


def test_celery_urls_share_the_app_redis_host(monkeypatch):
    monkeypatch.setenv(
        "PARTNERDESK_REDIS_URL", "redis://:s3cret@redis.internal:6379/0?socket_timeout=2",
    )
    assert celery_broker_url() == "redis://:s3cret@redis.internal:6379/1?socket_timeout=2"
    assert celery_result_url() == "redis://:s3cret@redis.internal:6379/2?socket_timeout=2"


def test_celery_app_uses_those_urls_and_the_ping_task():
    from backend.tasks import ping
    from backend.worker import celery

    assert celery.conf.broker_url == celery_broker_url()
    assert celery.conf.result_backend == celery_result_url()
    assert celery_broker_url().endswith("/1")
    assert celery_result_url().endswith("/2")
    assert ping.name == "partnerdesk.ping"
    assert "partnerdesk.ping" in celery.tasks
