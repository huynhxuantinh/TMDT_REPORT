"""Run the actual popup-building template snippets in Edge with hostile text.

No DB, no network, no real hospital information. Run with the project venv.
"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


root = Path(__file__).resolve().parents[1]
about = (root / 'core/templates/core/about.html').read_text(encoding='utf-8')
detail = (root / 'core/templates/core/hospital_detail.html').read_text(encoding='utf-8')
about_code = about.split('const bounds = [];', 1)[1].split('map.fitBounds(', 1)[0]
detail_code = detail.split('const marker = L.marker([destLat, destLon]).addTo(map);', 1)[1].split('map.setView(', 1)[0]
attack = '<img src=x onerror="window.p1Xss=1"><svg onload="window.p1Xss=1"> & \' " Tiếng Việt'
stub = """
    window.p1Xss = 0;
    const map = {}, bounds = [];
    const marker = {
        addTo() {return this;},
        bindPopup(value) {
            if (typeof value === 'string') document.body.innerHTML = value;
            else document.body.append(value);
            return this;
        },
        openPopup() {return this;}
    };
    const L = {marker() {return marker;}};
"""
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel='msedge', headless=True)
    try:
        count = 0
        for width in (1440, 768, 390):
            page = browser.new_page(viewport={'width': width, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/*', lambda route: route.abort())
            for code in (
                'const mapData = ' + json.dumps([{'name': attack, 'phuong': attack, 'lat': 10, 'lon': 106}]) + ';' + about_code,
                'const hospitalName = ' + json.dumps(attack) + ';' + detail_code,
            ):
                page.set_content('<!doctype html><meta charset="utf-8"><body></body>')
                page.add_script_tag(content='{ ' + stub + code + ' }')
                assert attack in page.locator('body').inner_text()
                assert page.locator('img, svg').count() == 0
                assert page.evaluate('window.p1Xss') == 0
                assert not errors, errors
                count += 1
            page.close()
        print(f'PASS: {count} Edge popup checks at desktop/tablet/mobile sizes; hostile HTML stays text.')
    finally:
        browser.close()
