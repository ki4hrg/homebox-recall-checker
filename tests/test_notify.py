from recall_checker.notify.apprise_ import AppriseNotifier


def test_unconfigured_reports_clearly():
    ok, detail = AppriseNotifier([]).test()
    assert not ok and "APPRISE_URLS" in detail


def test_failure_includes_apprise_reason():
    # Nothing listens on port 1, so the connection is refused immediately.
    ok, detail = AppriseNotifier(["json://127.0.0.1:1/notify"]).test()
    assert not ok
    assert detail.startswith("Delivery failed.") and "error" in detail.lower()
