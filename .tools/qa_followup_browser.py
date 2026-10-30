"""Edge QA for booking continuation, safe GET and real Leaflet bounds (08–16).

Run with an isolated Django test settings module; refuses the application DB.
"""
import datetime
import os
from pathlib import Path
import sys
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
import django
from django.conf import settings
assert str(settings.DATABASES['default']['PORT']) == '55439', 'Isolated QA DB required.'
settings.DATABASES['default']['TEST']['NAME'] = 'test_followup_browser'
django.setup()

from django.contrib.gis.geos import Point
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import connections
from django.test import override_settings
from django.test.runner import DiscoverRunner
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import sync_playwright, expect

from core.geo import point_to_latlon
from core.models import BenhVien, LichKham, LichSuKhamBenh, PhieuKham
from core.test_exam_services import fixtures


@override_settings(ALLOWED_HOSTS=['localhost', '127.0.0.1', 'testserver'],
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class FollowupBrowser(StaticLiveServerTestCase):
    host = '127.0.0.1'

    def test_maps_login_continuation_and_gets(self):
        admin, patient, appointment, doctor, _, *_ = fixtures()
        for user in (patient, doctor):
            user.set_password('Browser-QA@2030')
            user.save()
        hospital = appointment.bac_si.benh_vien
        distant = BenhVien.objects.create(ten='Hanoi QA', dia_chi='Test', phuong='Test',
            vi_tri=Point(105.834, 21.028, srid=4326), gio_mo=datetime.time(7), gio_dong=datetime.time(17), loai_hinh='cong')
        missing = BenhVien.objects.create(ten='Missing QA', dia_chi='Test', phuong='Test',
            vi_tri=Point(0, 0, srid=4326), gio_mo=datetime.time(7), gio_dong=datetime.time(17), loai_hinh='cong')
        exam = PhieuKham.objects.get(lich_kham=appointment)
        LichSuKhamBenh.objects.filter(phieu_kham=exam).delete()
        exam.delete()
        LichKham.objects.filter(pk=appointment.pk).update(trang_thai='cho', ngay_kham=timezone.localdate() + datetime.timedelta(days=1))
        artifacts = root / '.vnpay-test' / 'followup-browser'
        artifacts.mkdir(exist_ok=True)

        def coordinates(point):
            return (None, None) if point and point.x == 0 and point.y == 0 else point_to_latlon(point)

        with patch('core.views.point_to_latlon', side_effect=coordinates), sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                page = browser.new_page()
                page.set_default_timeout(20000)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                # Real Leaflet loads from the existing CDN. Map tiles/fonts aren't needed for bounds assertions.
                page.route('**/*.tile.openstreetmap.org/**', lambda route: route.abort())
                for width in (1440, 768, 390):
                    page.set_viewport_size({'width': width, 'height': 900})
                    page.goto(self.live_server_url + '/?q=Hanoi&lat=10.77&lon=106.7', wait_until='networkidle')
                    page.wait_for_function('typeof L !== "undefined" && typeof map !== "undefined"')
                    assert page.evaluate('map.getBounds().contains([21.028, 105.834])')
                    assert abs(page.evaluate('map.getCenter().lat') - 21.028) < 0.5
                    assert page.evaluate('resultBounds.length') == 1
                    page.screenshot(path=str(artifacts / f'search-{width}.png'))
                    page.goto(self.live_server_url + reverse('hospital_detail', args=[missing.pk]), wait_until='networkidle')
                    assert page.evaluate('destination.lat === null && destination.lon === null')
                    assert page.evaluate('Object.values(map._layers).filter(layer => layer instanceof L.Marker).length') == 0
                    page.screenshot(path=str(artifacts / f'missing-{width}.png'))
                target = reverse('dat_lich', args=[hospital.pk]) + f'?khoa={appointment.bac_si.khoa_id}&bac_si={appointment.bac_si_id}'
                page.goto(self.live_server_url + target)
                expect(page.locator('[name=next]')).to_have_value(target)
                page.locator('[name=username]').fill(patient.username)
                page.locator('[name=password]').fill('Browser-QA@2030')
                page.locator('button[type=submit]').click()
                expect(page).to_have_url(self.live_server_url + target)
                expect(page.locator('[name=bac_si]')).to_have_value(str(appointment.bac_si_id))
                # New context ensures a genuinely different login/session for the doctor.
                page.close()
                context = browser.new_context()
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(self.live_server_url + reverse('login'))
                page.locator('[name=username]').fill(doctor.username)
                page.locator('[name=password]').fill('Browser-QA@2030')
                page.locator('button[type=submit]').click()
                expect(page).to_have_url(self.live_server_url + reverse('bac_si_home'))
                page.goto(self.live_server_url + reverse('doctor_exam', args=[appointment.pk]))
                page.reload()
                appointment.refresh_from_db()
                assert appointment.trang_thai == 'cho'
                expect(page.get_by_role('button', name='Bắt đầu khám', exact=True)).to_be_visible()
                assert page.locator('[name=chan_doan]').count() == 0
                assert not errors, errors
                print('PASS: 6 real Leaflet checks (desktop/tablet/mobile), login continuation, GET/refresh unchanged; no JS errors.', flush=True)
            finally:
                browser.close()
                connections.close_all()


if __name__ == '__main__':
    failures = DiscoverRunner(verbosity=1, interactive=False).run_tests([__name__ + '.FollowupBrowser'])
    raise SystemExit(bool(failures))
