from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_base_template_exposes_persistent_three_mode_theme_switcher():
    template = (ROOT / "src/moonwing/api/templates/base.html").read_text(encoding="utf-8")

    assert "moonwing_theme" in template
    assert "data-theme" in template
    assert 'id="theme-select"' in template
    assert 'value="dark"' in template
    assert 'value="light"' in template
    assert 'value="cloudy"' in template
    assert 'value="rainy"' in template
    assert 'value="sunset"' in template
    assert 'value="full_moon"' in template


def test_css_defines_light_and_cloudy_theme_variable_sets():
    css = (ROOT / "src/moonwing/api/static/css/style.css").read_text(encoding="utf-8")

    assert 'html[data-theme="light"]' in css
    assert 'html[data-theme="cloudy"]' in css
    assert 'html[data-theme="rainy"]' in css
    assert 'html[data-theme="sunset"]' in css
    assert 'html[data-theme="full_moon"]' in css
    assert ".theme-switcher" in css


def test_light_is_soft_cloudy_is_lighter_rainy_is_dark_and_sunset_is_colorful():
    css = (ROOT / "src/moonwing/api/static/css/style.css").read_text(encoding="utf-8")

    light_block = css.split('html[data-theme="light"] {', 1)[1].split("}", 1)[0]
    cloudy_block = css.split('html[data-theme="cloudy"] {', 1)[1].split("}", 1)[0]
    rainy_block = css.split('html[data-theme="rainy"] {', 1)[1].split("}", 1)[0]
    sunset_block = css.split('html[data-theme="sunset"] {', 1)[1].split("}", 1)[0]
    full_moon_block = css.split('html[data-theme="full_moon"] {', 1)[1].split("}", 1)[0]

    assert "--bg:         #edf2f6;" in light_block
    assert "--accent:       #3970c8;" in light_block
    assert "--bg:         #d8dbdf;" in cloudy_block
    assert "--bg-surface: #e6e8eb;" in cloudy_block
    assert "--accent:       #6f7d90;" in cloudy_block
    assert "--bg:         #242629;" in rainy_block
    assert "--bg-surface: #303236;" in rainy_block
    assert "--accent:       #7f8ea3;" in rainy_block
    assert "--bg:         #24151d;" in sunset_block
    assert "--accent:       #ff7a45;" in sunset_block
    assert "--success:  #6ee7a5;" in sunset_block
    assert "--bg:         #0f1420;" in full_moon_block
    assert "--accent:       #c8d7ff;" in full_moon_block
