from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runs_rows_include_right_edge_detail_affordance():
    template = (ROOT / "src/moonwing/api/templates/runs.html").read_text(encoding="utf-8")

    assert "runs-table-card" in template
    assert 'class="list-row-affordance-heading"' in template
    assert 'class="list-row-affordance"' in template
    assert 'class="list-row-open"' in template
    assert 'href="/runs/{{ r.id }}"' in template
    assert 'aria-label="Open run details for {{ r.id }}"' in template


def test_artifacts_rows_include_right_edge_detail_affordance():
    template = (ROOT / "src/moonwing/api/templates/artifacts.html").read_text(encoding="utf-8")

    assert "artifacts-table-card" in template
    assert 'class="list-row-affordance-heading"' in template
    assert 'class="list-row-affordance"' in template
    assert 'class="list-row-open"' in template
    assert 'href="/artifacts/{{ a.id }}"' in template
    assert 'aria-label="Open artifact details for {{ a.id }}"' in template


def test_shared_list_row_affordance_css_reveals_on_hover_and_focus():
    css = (ROOT / "src/moonwing/api/static/css/style.css").read_text(encoding="utf-8")

    assert ".list-row-open" in css
    assert ".list-row-open::before" in css
    assert 'content: "->";' in css
    assert ".table-card tbody tr:hover .list-row-open" in css
    assert ".table-card tbody tr:focus-within .list-row-open" in css
    assert ".runs-table-card" in css
    assert ".artifacts-table-card" in css


def test_settings_rows_include_right_edge_detail_affordances():
    template = (ROOT / "src/moonwing/api/templates/settings.html").read_text(encoding="utf-8")

    assert 'class="list-row-affordance-heading"' in template
    assert 'class="list-row-affordance"' in template
    assert 'href="/targets/{{ t.id }}"' in template
    assert 'href="/credentials/{{ c.id }}"' in template
    assert 'href="/runtime-profiles/{{ p.id }}"' in template
    assert 'aria-label="Open target details for {{ t.id }}"' in template
    assert 'aria-label="Open credential details for {{ c.id }}"' in template
    assert 'aria-label="Open runtime profile details for {{ p.id }}"' in template


def test_settings_resource_detail_pages_expose_edit_and_delete_actions():
    target = (ROOT / "src/moonwing/api/templates/target_detail.html").read_text(encoding="utf-8")
    credential = (ROOT / "src/moonwing/api/templates/credential_detail.html").read_text(encoding="utf-8")
    profile = (ROOT / "src/moonwing/api/templates/profile_detail.html").read_text(encoding="utf-8")

    assert 'href="/targets/{{ target.id }}/edit"' in target
    assert 'action="/targets/{{ target.id }}/delete"' in target
    assert 'href="/credentials/{{ credential.id }}/edit"' in credential
    assert 'action="/credentials/{{ credential.id }}/delete"' in credential
    assert 'href="/runtime-profiles/{{ profile.id }}/edit"' in profile
    assert 'action="/runtime-profiles/{{ profile.id }}/delete"' in profile


def test_target_scan_action_is_separated_from_edit_delete_actions():
    template = (ROOT / "src/moonwing/api/templates/target_detail.html").read_text(encoding="utf-8")
    css = (ROOT / "src/moonwing/api/static/css/style.css").read_text(encoding="utf-8")

    assert 'class="page-actions page-actions-split"' in template
    assert 'class="page-actions-secondary"' in template
    assert template.index('href="/runs/new?target={{ target.id }}"') < template.index('class="page-actions-secondary"')
    assert ".page-actions-split" in css
    assert ".page-actions-secondary" in css
