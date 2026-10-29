"""Optional Edge QA, isolated PostgreSQL only; one Mailtrap Testing receipt."""
import os
import sys
import json
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'hospital.settings')
assert os.environ.get('DB_PORT') == '55439', 'Only the isolated QA PostgreSQL is allowed.'
# Playwright's sync bridge runs an event loop on this single QA thread.
os.environ['DJANGO_ALLOW_ASYNC_UNSAFE'] = 'true'
import django
django.setup()
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import connections
from django.test import override_settings
from django.test.runner import DiscoverRunner
from django.urls import reverse
from playwright.sync_api import sync_playwright, expect
from core.models import BenhNhan, EmailPhieuThanhToan, HoaDon, LichKham, PhieuKham
from core.test_workflow import WorkflowFixture
from core.test_receipts import MAIL_SETTINGS
from django.core.mail.backends.locmem import EmailBackend

settings.DATABASES['default']['TEST']['NAME'] = 'test_task08_browser'
smtp_settings = {key: getattr(settings, key) for key in (
    'EMAIL_BACKEND', 'EMAIL_HOST', 'EMAIL_PORT', 'EMAIL_HOST_USER', 'EMAIL_HOST_PASSWORD',
    'EMAIL_USE_TLS', 'EMAIL_USE_SSL', 'EMAIL_TIMEOUT', 'DEFAULT_FROM_EMAIL')}
assert smtp_settings['EMAIL_HOST'] == 'sandbox.smtp.mailtrap.io', 'Mailtrap Testing only.'
live_mailtrap = '--mailtrap' in sys.argv
if not live_mailtrap:
    smtp_settings.update(MAIL_SETTINGS)
artifacts = Path('.vnpay-test')
artifacts.mkdir(exist_ok=True)


@override_settings(**smtp_settings, ALLOWED_HOSTS=['localhost', '127.0.0.1', 'testserver'])
class BrowserWorkflow(WorkflowFixture, StaticLiveServerTestCase):
    host = '127.0.0.1'

    def test_full_cash_workflow_in_edge(self):
        self.prepare_workflow()
        if not live_mailtrap:
            mocked_smtp = patch('core.receipts.delivery.get_connection', side_effect=lambda **kw: EmailBackend())
            mocked_smtp.start()
            self.addCleanup(mocked_smtp.stop)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
            page = context.new_page()
            page.set_default_timeout(20000)
            js_errors = []
            page.on('pageerror', lambda error: js_errors.append(str(error)))

            def visit(route, *args):
                response = page.goto(self.live_server_url + reverse(route, args=args), wait_until='domcontentloaded')
                assert response.status == 200, (route, response.status)

            def submit(button):
                with page.expect_navigation(wait_until='domcontentloaded'):
                    page.get_by_role('button', name=button, exact=True).click()

            def login(username):
                page.goto(self.live_server_url + reverse('logout'), wait_until='domcontentloaded')
                visit('login')
                page.locator('[name=username]').fill(username)
                page.locator('[name=password]').fill(self.password)
                with page.expect_navigation(wait_until='domcontentloaded'):
                    page.locator('button[type=submit]').click()

            try:
                visit('register')
                for name, value in dict(name='Nguyễn Thị Thử Nghiệm', username='qaPatient123', phone='0901000002',
                    email='qa-patient@example.invalid', password=self.password).items():
                    page.locator(f'[name={name}]').fill(value)
                submit('Đăng ký')
                expect(page).to_have_url(self.live_server_url + reverse('login'))
                login('qaPatient123')
                expect(page.locator('body')).to_contain_text('Bệnh viện QA')
                expect(page.locator('.leaflet-container')).to_be_visible()
                expect(page.locator('.leaflet-marker-icon').first).to_be_visible()
                print('PASS browser: registration/login + GIS Leaflet marker', flush=True)
                visit('profile')
                page.locator('[name=ngay_sinh]').fill('2000-01-01')
                page.locator('[name=ma_bhyt]').fill('QA-BROWSER-CARD')
                page.locator('[name=ngay_cap]').fill('2026-01-01')
                page.locator('[name=ngay_het_han]').fill('2030-12-31')
                submit('Lưu thay đổi')
                visit('hospital_detail', self.hospital.pk)
                visit('dat_lich', self.hospital.pk)
                with page.expect_navigation(wait_until='domcontentloaded'):
                    page.locator('[name=khoa]').select_option(str(self.department.pk))
                page.locator('[name=bac_si]').select_option(str(self.doctor.pk))
                page.locator('[name=ngay_kham]').fill(self.date.isoformat())
                page.locator('[name=gio_kham]').fill('09:00')
                submit('Xác nhận đặt lịch')
                appointment = LichKham.objects.get(benh_nhan__user__username='qaPatient123')
                assert not HoaDon.objects.filter(lich_kham=appointment).exists()
                print('PASS browser: profile/BHYT + hospital/department/doctor booking', flush=True)
                login('qaDoctor123')
                with page.expect_navigation(wait_until='domcontentloaded'):
                    page.locator(f'form[action="{reverse("start_exam", args=[appointment.pk])}"] button').click()
                visit('doctor_exam', appointment.pk)
                for name in ('trieu_chung', 'chan_doan', 'huong_dieu_tri'):
                    page.locator(f'[name={name}]').fill('Nội dung khám thử nghiệm')
                page.locator('#service-search').fill('QA_KHAM')
                page.locator('#service-search-results button').first.click()
                page.locator('[name="services-0-so_luong"]').fill('2')
                expect(page.locator('#service-estimated-total')).to_contain_text('200.000')
                page.screenshot(path=str(artifacts / 'task08-browser-exam.png'), full_page=True)
                submit('Hoàn thành khám')
                invoice = HoaDon.objects.get(lich_kham=appointment)
                assert invoice.so_tien == 40000 and invoice.bhyt_ho_tro == 160000
                print('PASS browser: service search/quantity JS + completed exam + invoice/BHYT', flush=True)
                login('qaCashier123')
                visit('payment_detail', appointment.pk)
                page.locator('[name=received_full]').check()
                submit('Xác nhận đã nhận tiền')
                invoice.refresh_from_db()
                assert invoice.trang_thai == 'paid'
                row = EmailPhieuThanhToan.objects.get(hoa_don=invoice)
                smtp_result = {'status': row.trang_thai, 'error_code': row.ma_loi,
                    'attempts': row.so_lan_thu, 'message_id': f'<receipt-{row.ma_thu}@qlbenhvien.local>'}
                if live_mailtrap:
                    (artifacts / 'task08-mailtrap-result.json').write_text(json.dumps(smtp_result))
                print('Email result (' + ('actual Mailtrap SMTP' if live_mailtrap else 'mock SMTP') + '):', row.trang_thai, row.ma_loi, flush=True)
                page.screenshot(path=str(artifacts / 'task08-browser-paid.png'), full_page=True)
                with page.expect_download() as download:
                    page.locator(f'a[href="{reverse("receipt_download", args=[invoice.pk])}"]').click()
                download.value.save_as(str(artifacts / 'task08-browser-receipt.pdf'))
                visit('receipt_print', invoice.pk)
                expect(page.locator('object[type="application/pdf"]')).to_be_visible()
                response = context.request.get(self.live_server_url + reverse('receipt_view', args=[invoice.pk]))
                assert response.status == 200 and response.body().startswith(b'%PDF-')
                print('PASS browser: cash confirmation + PDF view/download/print page', flush=True)
                login('qaPatient123')
                visit('medical_history')
                expect(page.locator('body')).to_contain_text('Bệnh viện QA')
                login('qaAdmin123')
                for route in ('custom_admin_dashboard', 'custom_admin_hospitals', 'custom_admin_departments',
                    'custom_admin_doctors', 'service_list', 'bhyt_rule_list', 'cashier_list', 'cashier_monitor'):
                    visit(route)
                print('PASS browser: patient history + original admin and TASK02/05/06 pages', flush=True)
                assert not js_errors, f'JavaScript page errors: {js_errors}'
                print('PASS browser: no JavaScript page errors; CSRF forms used throughout', flush=True)
            except Exception:
                page.screenshot(path=str(artifacts / 'task08-browser-failure.png'), full_page=True)
                raise
            finally:
                connections.close_all()
                browser.close()


if __name__ == '__main__':
    result = DiscoverRunner(verbosity=1, interactive=False).run_tests(['__main__.BrowserWorkflow'])
    sys.exit(bool(result))
