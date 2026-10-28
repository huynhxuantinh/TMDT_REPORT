from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from core.models import DanhMucDichVu, GiaoDichThanhToan, HoaDon, LogHeThong
from core.service_catalog import services
from core.templatetags.catalog_money import vnd
from core.test_vnpay import CONFIG, fixtures as payment_fixtures, sign_callback
from core.payments.services import create_invoice, start_payment


def service_data(**overrides):
    data = {'ma_dich_vu': 'KHAM_TQ', 'ten': 'Khám tổng quát', 'nhom': 'Khám bệnh',
            'mo_ta': 'Mô tả thử nghiệm', 'don_gia': '100000', 'dang_ap_dung': 'on'}
    data.update(overrides)
    return data


class ServiceCatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Existing app calls is_staff=True accounts Admin; no extra role introduced.
        cls.admin = User.objects.create_user('catalog_admin', is_staff=True)
        cls.service = DanhMucDichVu.objects.create(
            ma_dich_vu='XN_MAU', ten='Xét nghiệm máu', nhom='Xét nghiệm', don_gia=Decimal('80000'),
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_admin_can_create_with_normalized_unique_code_and_audit(self):
        response = self.client.post(reverse('service_create'), service_data(ma_dich_vu=' kham_tq '), REMOTE_ADDR='127.0.0.1')
        service = DanhMucDichVu.objects.get(ma_dich_vu='KHAM_TQ')
        self.assertRedirects(response, reverse('service_detail', args=[service.pk]))
        self.assertEqual(service.don_gia, Decimal('100000'))
        log = LogHeThong.objects.get(model_name='DanhMucDichVu', object_id=service.pk)
        self.assertEqual(log.nguoi_thuc_hien_id, self.admin.pk)
        self.assertEqual(log.ip_address, '127.0.0.1')
        self.assertEqual(log.hanh_dong, 'create')
        self.assertIsNone(log.du_lieu_cu)
        self.assertEqual(log.du_lieu_moi['don_gia'], '100000')

    def test_duplicate_codes_are_case_insensitive(self):
        for code in ['XN_MAU', 'xn_mau', ' Xn_Mau ']:
            with self.subTest(code=code):
                response = self.client.post(reverse('service_create'), service_data(ma_dich_vu=code))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['form'].errors)
        self.assertEqual(DanhMucDichVu.objects.count(), 1)
        self.assertFalse(LogHeThong.objects.filter(model_name='DanhMucDichVu').exists())

    def test_invalid_prices_rejected_without_writes(self):
        for amount in ['-1', '100.5', 'NaN', 'Infinity', 'abc', '', '10000000000']:
            with self.subTest(amount=amount):
                response = self.client.post(reverse('service_create'), service_data(don_gia=amount))
                self.assertIn('don_gia', response.context['form'].errors)
        self.assertEqual(DanhMucDichVu.objects.count(), 1)

    def test_zero_and_maximum_integer_price_allowed(self):
        for index, amount in enumerate(['0', '9999999999']):
            with self.subTest(amount=amount):
                response = self.client.post(reverse('service_create'), service_data(ma_dich_vu=f'FREE_{index}', don_gia=amount))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(DanhMucDichVu.objects.get(ma_dich_vu=f'FREE_{index}').don_gia, Decimal(amount))

    def test_required_fields_and_invalid_code(self):
        for override in [{'ma_dich_vu': ''}, {'ma_dich_vu': 'KHÁM TQ'}, {'ten': '   '}, {'nhom': '\t'}, {'don_gia': ''}]:
            with self.subTest(override=override):
                response = self.client.post(reverse('service_create'), service_data(**override))
                self.assertTrue(response.context['form'].errors)
        self.assertEqual(DanhMucDichVu.objects.count(), 1)

    def test_admin_edits_all_mutable_fields_and_keeps_code(self):
        response = self.client.post(reverse('service_edit', args=[self.service.pk]), service_data(
            ma_dich_vu='ATTACK', ten='Xét nghiệm máu cập nhật', nhom='Nhóm mới', mo_ta='Mô tả mới', don_gia='120000'))
        self.assertRedirects(response, reverse('service_detail', args=[self.service.pk]))
        self.service.refresh_from_db()
        self.assertEqual(self.service.ma_dich_vu, 'XN_MAU')
        self.assertEqual(self.service.ten, 'Xét nghiệm máu cập nhật')
        self.assertEqual(self.service.nhom, 'Nhóm mới')
        self.assertEqual(self.service.mo_ta, 'Mô tả mới')
        self.assertEqual(self.service.don_gia, Decimal('120000'))
        log = LogHeThong.objects.get(model_name='DanhMucDichVu', object_id=self.service.pk)
        self.assertEqual(log.du_lieu_cu['don_gia'], '80000')
        self.assertEqual(log.du_lieu_moi['don_gia'], '120000')
        self.assertEqual(log.nguoi_thuc_hien_id, self.admin.pk)

    def test_invalid_edit_preserves_existing_price_and_history(self):
        response = self.client.post(reverse('service_edit', args=[self.service.pk]), service_data(don_gia='-1'))
        self.assertEqual(response.status_code, 200)
        self.service.refresh_from_db()
        self.assertEqual(self.service.don_gia, Decimal('80000'))
        self.assertFalse(LogHeThong.objects.filter(model_name='DanhMucDichVu').exists())

    def test_deactivate_reactivate_and_repeated_post_are_audited_once(self):
        url = reverse('service_status', args=[self.service.pk])
        for _ in range(2):
            self.assertEqual(self.client.post(url, {'dang_ap_dung': '0'}).status_code, 302)
        self.service.refresh_from_db()
        self.assertFalse(self.service.dang_ap_dung)
        logs = LogHeThong.objects.filter(model_name='DanhMucDichVu', object_id=self.service.pk)
        self.assertEqual(logs.count(), 1)
        self.assertTrue(logs.get().du_lieu_cu['dang_ap_dung'])
        self.assertFalse(logs.get().du_lieu_moi['dang_ap_dung'])
        self.client.post(url, {'dang_ap_dung': '1'})
        self.service.refresh_from_db()
        self.assertTrue(self.service.dang_ap_dung)
        self.assertEqual(logs.count(), 2)

    def test_invalid_status_and_get_never_mutate(self):
        url = reverse('service_status', args=[self.service.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        for value in ['', 'yes', 'false', '2']:
            self.assertEqual(self.client.post(url, {'dang_ap_dung': value}).status_code, 400)
        self.service.refresh_from_db()
        self.assertTrue(self.service.dang_ap_dung)

    def test_search_by_code_name_group_and_status(self):
        other = DanhMucDichVu.objects.create(ma_dich_vu='XN_NT', ten='Xét nghiệm nước tiểu', nhom='Xét nghiệm', don_gia=0, dang_ap_dung=False)
        image = DanhMucDichVu.objects.create(ma_dich_vu='SIEU_AM', ten='Siêu âm', nhom='Chẩn đoán hình ảnh', don_gia=120000)
        cases = [({'q': 'xn_mau'}, [self.service]), ({'q': 'nước tiểu'}, [other]),
                 ({'nhom': 'Xét nghiệm', 'trang_thai': '1'}, [self.service]),
                 ({'nhom': 'Xét nghiệm', 'trang_thai': '0'}, [other]),
                 ({'nhom': 'Chẩn đoán hình ảnh'}, [image]), ({'q': 'không có'}, []),
                 ({'trang_thai': 'invalid'}, [])]
        for filters, expected in cases:
            with self.subTest(filters=filters):
                response = self.client.get(reverse('service_list'), filters)
                self.assertEqual(list(response.context['page_obj']), expected)

    def test_list_pagination_preserves_filters(self):
        for index in range(26):
            DanhMucDichVu.objects.create(ma_dich_vu=f'PAGE_{index:02}', ten='Dịch vụ trang', nhom='Nhóm', don_gia=0)
        response = self.client.get(reverse('service_list'), {'q': 'PAGE_', 'nhom': 'Nhóm', 'trang_thai': '1', 'page': 2})
        self.assertEqual(len(response.context['page_obj']), 1)
        self.assertIn('q=PAGE_', response.context['query_string'])
        self.assertIn('trang_thai=1', response.context['query_string'])

    def test_detail_and_currency_render_with_escaped_description(self):
        self.service.mo_ta = '<script>alert(1)</script>'
        self.service.save()
        response = self.client.get(reverse('service_detail', args=[self.service.pk]))
        self.assertContains(response, '80.000 VND')
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertEqual(vnd('9999999999'), '9.999.999.999 VND')

    def test_dashboard_links_to_catalog(self):
        response = self.client.get(reverse('custom_admin_dashboard'))
        self.assertContains(response, reverse('service_list'))
        self.assertContains(response, 'Dịch vụ &amp; bảng giá')

    def test_anonymous_and_regular_users_are_rejected(self):
        regular = User.objects.create_user('regular')
        endpoints = [('service_list', []), ('service_create', []), ('service_detail', [self.service.pk]), ('service_edit', [self.service.pk])]
        self.client.logout()
        for name, args in endpoints:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 302)
        self.client.force_login(regular)
        for name, args in endpoints:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403)
        for name, args, data in [('service_create', [], service_data()), ('service_edit', [self.service.pk], service_data()),
                                 ('service_status', [self.service.pk], {'dang_ap_dung': '0'})]:
            self.assertEqual(self.client.post(reverse(name, args=args), data).status_code, 403)
        with self.assertRaises(PermissionDenied):
            services.save_service(service_data(), regular)
        with self.assertRaises(PermissionDenied):
            services.set_service_status(self.service.pk, False, regular)

    def test_post_endpoints_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        for name, args in [('service_create', []), ('service_edit', [self.service.pk]), ('service_status', [self.service.pk])]:
            self.assertEqual(client.post(reverse(name, args=args), service_data()).status_code, 403)

    def test_get_form_does_not_write_and_hides_code_edit(self):
        response = self.client.get(reverse('service_edit', args=[self.service.pk]))
        self.assertTrue(response.context['form'].fields['ma_dich_vu'].disabled)
        self.assertFalse(LogHeThong.objects.filter(model_name='DanhMucDichVu').exists())

    def test_audit_failure_rolls_back_catalog_update(self):
        with patch('core.service_catalog.services.record_change', side_effect=DatabaseError('audit failed')):
            with self.assertRaises(DatabaseError):
                services.save_service(service_data(don_gia='150000'), self.admin, pk=self.service.pk)
        self.service.refresh_from_db()
        self.assertEqual(self.service.don_gia, Decimal('80000'))

    def test_audit_failure_rolls_back_status_change(self):
        with patch('core.service_catalog.services.record_change', side_effect=DatabaseError('audit failed')):
            with self.assertRaises(DatabaseError):
                services.set_service_status(self.service.pk, False, self.admin)
        self.service.refresh_from_db()
        self.assertTrue(self.service.dang_ap_dung)

    def test_noop_edit_does_not_add_false_history(self):
        data = service_data(ma_dich_vu='XN_MAU', ten=self.service.ten, nhom=self.service.nhom, mo_ta='', don_gia='80000')
        services.save_service(data, self.admin, pk=self.service.pk)
        self.assertFalse(LogHeThong.objects.filter(model_name='DanhMucDichVu').exists())

    def test_instance_and_queryset_deletion_are_blocked(self):
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.service.delete()
        with self.assertRaises(ProtectedError), transaction.atomic():
            DanhMucDichVu.objects.filter(pk=self.service.pk).delete()
        self.assertTrue(DanhMucDichVu.objects.filter(pk=self.service.pk).exists())

    def test_model_rejects_fractional_and_negative_money(self):
        for price in [Decimal('-1'), Decimal('100.1'), Decimal('NaN')]:
            with self.subTest(price=price), self.assertRaises(ValidationError):
                DanhMucDichVu.objects.create(ma_dich_vu='BAD', ten='Test', nhom='Test', don_gia=price)

    def test_database_constraints_reject_negative_blank_and_noncanonical_values(self):
        for changes in [{'don_gia': -1}, {'ten': ' '}, {'nhom': ''}, {'ma_dich_vu': 'xn_mau'}]:
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                DanhMucDichVu.objects.filter(pk=self.service.pk).update(**changes)

    def test_seed_is_idempotent_and_preserves_existing_prices(self):
        output = StringIO()
        call_command('seed_dich_vu_demo', stdout=output)
        seeded = DanhMucDichVu.objects.get(ma_dich_vu='DEMO_KHAM_TQ')
        seeded.don_gia = Decimal('999000')
        seeded.dang_ap_dung = False
        seeded.save()
        count = DanhMucDichVu.objects.count()
        log_count = LogHeThong.objects.filter(model_name='DanhMucDichVu').count()
        call_command('seed_dich_vu_demo', stdout=output)
        seeded.refresh_from_db()
        self.assertEqual(DanhMucDichVu.objects.count(), count)
        self.assertEqual(LogHeThong.objects.filter(model_name='DanhMucDichVu').count(), log_count)
        self.assertEqual(seeded.don_gia, Decimal('999000'))
        self.assertFalse(seeded.dang_ap_dung)


@override_settings(**CONFIG)
class CatalogPaymentIsolationTests(TestCase):
    def setUp(self):
        self.admin, _, self.patient_user, self.appointment = payment_fixtures()
        self.doctor_user = User.objects.create_user('catalog_doctor')
        self.appointment.bac_si.user = self.doctor_user
        self.appointment.bac_si.save()
        self.service, _ = services.save_service(service_data(), self.admin)
        self.invoice = create_invoice(self.appointment.pk, Decimal('250000'), self.admin)

    def update_price(self, price):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('service_edit', args=[self.service.pk]), service_data(don_gia=price))
        self.assertEqual(response.status_code, 302)

    def test_patient_and_doctor_profiles_do_not_grant_catalog_access(self):
        for user in [self.patient_user, self.doctor_user]:
            self.client.force_login(user)
            self.assertEqual(self.client.post(reverse('service_edit', args=[self.service.pk]), service_data(don_gia='1')).status_code, 403)
            self.assertEqual(self.client.post(reverse('service_status', args=[self.service.pk]), {'dang_ap_dung': '0'}).status_code, 403)

    def test_price_changes_leave_unpaid_invoice_and_pending_attempt_unchanged(self):
        invoice_before = list(HoaDon.objects.values())
        self.update_price('500000')
        self.assertEqual(list(HoaDon.objects.values()), invoice_before)
        payment, _ = start_payment(self.invoice.pk, self.admin, '127.0.0.1')
        payment_before = list(GiaoDichThanhToan.objects.values())
        self.update_price('600000')
        services.set_service_status(self.service.pk, False, self.admin)
        self.assertEqual(list(HoaDon.objects.values()), invoice_before)
        self.assertEqual(list(GiaoDichThanhToan.objects.values()), payment_before)
        self.assertEqual(payment.so_tien, Decimal('250000'))

    def test_price_changes_never_modify_paid_invoice_or_successful_payment(self):
        payment, _ = start_payment(self.invoice.pk, self.admin, '127.0.0.1')
        params = sign_callback({'vnp_TmnCode': CONFIG['VNPAY_TMN_CODE'], 'vnp_TxnRef': payment.ma_tham_chieu,
                                'vnp_Amount': '25000000', 'vnp_ResponseCode': '00',
                                'vnp_TransactionStatus': '00', 'vnp_TransactionNo': '12345678'})
        self.assertEqual(self.client.get(reverse('vnpay_ipn'), params).json()['RspCode'], '00')
        invoice_before = list(HoaDon.objects.values())
        payment_before = list(GiaoDichThanhToan.objects.values())
        self.update_price('900000')
        services.set_service_status(self.service.pk, False, self.admin)
        self.assertEqual(list(HoaDon.objects.values()), invoice_before)
        self.assertEqual(list(GiaoDichThanhToan.objects.values()), payment_before)


class CatalogMigrationTests(TransactionTestCase):
    def test_migration_adds_catalog_without_rewriting_existing_invoice(self):
        # This test only ever runs inside Django's disposable test database.
        latest_targets = MigrationExecutor(connection).loader.graph.leaf_nodes()
        target = [('core', '0020_danh_muc_dich_vu')]
        try:
            MigrationExecutor(connection).migrate([('core', '0019_vnpay_sandbox')])
            self.assertNotIn('core_danhmucdichvu', connection.introspection.table_names())
            admin, _, _, appointment = payment_fixtures()
            historical_invoice = MigrationExecutor(connection).loader.project_state([('core', '0019_vnpay_sandbox')]).apps.get_model('core', 'HoaDon')
            invoice = historical_invoice.objects.create(lich_kham_id=appointment.pk, so_tien=Decimal('123000'), nguoi_tao_id=admin.pk)
            before = historical_invoice.objects.filter(pk=invoice.pk).values().get()
            MigrationExecutor(connection).migrate(target)
            self.assertIn('core_danhmucdichvu', connection.introspection.table_names())
            self.assertEqual(historical_invoice.objects.filter(pk=invoice.pk).values().get(), before)
            self.assertEqual(DanhMucDichVu.objects.count(), 0)
        finally:
            MigrationExecutor(connection).migrate(latest_targets)
