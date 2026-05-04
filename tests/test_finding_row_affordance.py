from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_findings_rows_include_right_edge_detail_affordance():
    template = (ROOT / "src/moonwing/api/templates/findings.html").read_text(encoding="utf-8")

    assert 'class="finding-row-affordance-heading"' in template
    assert 'class="finding-row-affordance"' in template
    assert 'class="finding-row-open"' in template
    assert 'href="/findings/{{ f.id }}"' in template
    assert 'aria-label="Open finding details for {{ f.title }}"' in template


def test_finding_row_affordance_is_revealed_on_hover_and_focus():
    css = (ROOT / "src/moonwing/api/static/css/style.css").read_text(encoding="utf-8")

    assert ".finding-row-open" in css
    assert ".finding-row-open::before" in css
    assert 'content: "->";' in css
    assert ".findings-table-card tbody tr:hover .finding-row-open" in css
    assert ".findings-table-card tbody tr:focus-within .finding-row-open" in css
    assert "opacity: 0;" in css
    assert "transform: translateX(-4px);" in css
