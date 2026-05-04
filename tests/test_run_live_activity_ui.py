from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_run_detail_template_includes_activity_animation_and_live_log_feed():
    template = (ROOT / "src/moonwing/api/templates/run_detail.html").read_text(encoding="utf-8")

    assert "run-live-panel" in template
    assert 'class="run-active-animation"' in template
    assert 'id="run-log-feed"' in template
    assert 'data-activity-url="/api/runs/{{ run.id }}/activity"' in template
    assert "fetch(activityUrl" in template
    assert "setTimeout(pollActivity" in template


def test_run_detail_template_shows_failure_reason_before_snapshot():
    template = (ROOT / "src/moonwing/api/templates/run_detail.html").read_text(encoding="utf-8")

    assert "run-failure-panel" in template
    assert "run.execution_snapshot.failure.message" in template
    assert template.index("run-failure-panel") < template.index("Execution Snapshot")


def test_dashboard_route_exposes_run_activity_endpoint():
    route = (ROOT / "src/moonwing/api/routes/dashboard.py").read_text(encoding="utf-8")

    assert "@router.get('/api/runs/{run_id}/activity')" in route
    assert "serialize_run_activity(run)" in route


def test_css_defines_run_activity_animation_and_log_feed():
    css = (ROOT / "src/moonwing/api/static/css/style.css").read_text(encoding="utf-8")

    assert ".run-live-panel" in css
    assert ".run-active-animation" in css
    assert "@keyframes run-scan-sweep" in css
    assert ".run-log-feed" in css
