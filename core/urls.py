from django.urls import path
from . import views
from .payments import views as payment_views
from .payments import cashiers as cashier_views
from .service_catalog import views as catalog_views
from .insurance import views as insurance_views
from .receipts import views as receipt_views

urlpatterns = [
    path('thanh-toan/hoa-don/<int:invoice_id>/phieu/xem/', receipt_views.receipt_pdf, name='receipt_view'),
    path('thanh-toan/hoa-don/<int:invoice_id>/phieu/tai/', receipt_views.receipt_pdf, {'download': True}, name='receipt_download'),
    path('thanh-toan/hoa-don/<int:invoice_id>/phieu/in/', receipt_views.receipt_print, name='receipt_print'),
    path('thanh-toan/hoa-don/<int:invoice_id>/email/thu-lai/', receipt_views.receipt_retry, name='receipt_email_retry'),
    path('quan-tri/thu-ngan/', cashier_views.branch_manager_list, name='cashier_list'),
    path('quan-tri/thu-ngan/tao/', cashier_views.branch_manager_form, name='cashier_create'),
    path('quan-tri/thu-ngan/<int:pk>/sua/', cashier_views.branch_manager_form, name='cashier_edit'),
    path('quan-tri/thu-ngan/hoa-don/', payment_views.payment_list, {'monitor': True}, name='cashier_monitor'),
    path('quan-tri/thu-ngan/hoa-don/<int:lich_id>/', payment_views.payment_detail, {'monitor': True}, name='cashier_monitor_detail'),
    path('quan-tri/thu-ngan/hoa-don/<int:invoice_id>/ket-thuc-doi-soat/', payment_views.reconciliation_close, name='cashier_monitor_reconcile_close'),
    path('thanh-toan/hoa-don/<int:invoice_id>/tien-mat/', payment_views.payment_cash, name='payment_cash'),
    path('quan-tri/bhyt-mo-phong/', insurance_views.rule_list, name='bhyt_rule_list'),
    path('quan-tri/bhyt-mo-phong/tao/', insurance_views.rule_form, name='bhyt_rule_create'),
    path('quan-tri/bhyt-mo-phong/<int:pk>/', insurance_views.rule_detail, name='bhyt_rule_detail'),
    path('quan-tri/bhyt-mo-phong/<int:pk>/sua/', insurance_views.rule_form, name='bhyt_rule_edit'),

    path('quan-tri/dich-vu/', catalog_views.service_list, name='service_list'),
    path('quan-tri/dich-vu/tao/', catalog_views.service_form, name='service_create'),
    path('quan-tri/dich-vu/<int:pk>/', catalog_views.service_detail, name='service_detail'),
    path('quan-tri/dich-vu/<int:pk>/sua/', catalog_views.service_form, name='service_edit'),
    path('quan-tri/dich-vu/<int:pk>/trang-thai/', catalog_views.service_status, name='service_status'),

    path('thanh-toan/', payment_views.payment_list, name='payment_list'),
    path('thanh-toan/lich-kham/<int:lich_id>/', payment_views.payment_detail, name='payment_detail'),
    path('thanh-toan/lich-kham/<int:lich_id>/dich-vu/', payment_views.services_save, name='payment_services_save'),
    path('thanh-toan/lich-kham/<int:lich_id>/hoa-don/', payment_views.invoice_create, name='invoice_create'),
    path('thanh-toan/hoa-don/<int:invoice_id>/vnpay/', payment_views.payment_start, name='payment_start'),
    path('thanh-toan/vnpay/return/', payment_views.vnpay_return, name='vnpay_return'),
    path('thanh-toan/vnpay/ipn/', payment_views.vnpay_ipn, name='vnpay_ipn'),

    # Trang chủ
    path("", views.home, name="home"),
    path("gioi-thieu/", views.gioi_thieu, name="gioi_thieu"),

    # Chi tiết bệnh viện
    path("benh-vien/<int:id>/", views.hospital_detail, name="hospital_detail"),

    # Đặt lịch theo bệnh viện
    path("dat-lich/<int:bv_id>/", views.dat_lich, name="dat_lich"),

    # Auth
    path("login/", views.user_login, name="login"),
    path("quan-tri/", views.custom_admin_dashboard, name="custom_admin_dashboard"),
    path("quan-tri/benh-vien/", views.custom_admin_hospitals, name="custom_admin_hospitals"),
    path("quan-tri/benh-vien/tao/", views.custom_admin_hospital_create, name="custom_admin_hospital_create"),
    path("quan-tri/benh-vien/<int:pk>/sua/", views.custom_admin_hospital_edit, name="custom_admin_hospital_edit"),
    path("quan-tri/benh-vien/<int:pk>/xoa/", views.custom_admin_hospital_delete, name="custom_admin_hospital_delete"),
    path("quan-tri/khoa/", views.custom_admin_departments, name="custom_admin_departments"),
    path("quan-tri/khoa/tao/", views.custom_admin_department_create, name="custom_admin_department_create"),
    path("quan-tri/khoa/<int:pk>/sua/", views.custom_admin_department_edit, name="custom_admin_department_edit"),
    path("quan-tri/khoa/<int:pk>/xoa/", views.custom_admin_department_delete, name="custom_admin_department_delete"),
    path("quan-tri/bac-si/", views.custom_admin_doctors, name="custom_admin_doctors"),
    path("quan-tri/bac-si/tao/", views.custom_admin_doctor_create, name="custom_admin_doctor_create"),
    path("quan-tri/bac-si/<int:pk>/sua/", views.custom_admin_doctor_edit, name="custom_admin_doctor_edit"),
    path("quan-tri/bac-si/<int:pk>/xoa/", views.custom_admin_doctor_delete, name="custom_admin_doctor_delete"),
    path("quan-tri/api/khoa/", views.custom_admin_departments_api, name="custom_admin_departments_api"),
    path("quan-tri/du-lieu/<slug:model_key>/", views.custom_admin_model_list, name="custom_admin_model_list"),
    path("quan-tri/du-lieu/<slug:model_key>/tao/", views.custom_admin_model_create, name="custom_admin_model_create"),
    path("quan-tri/du-lieu/<slug:model_key>/<int:pk>/sua/", views.custom_admin_model_edit, name="custom_admin_model_edit"),
    path("quan-tri/du-lieu/<slug:model_key>/<int:pk>/xoa/", views.custom_admin_model_delete, name="custom_admin_model_delete"),
    path("register/", views.register, name="register"),
    path("logout/", views.user_logout, name="logout"),
    path("quen-mat-khau/", views.forgot_password, name="forgot_password"),
    path("lien-he/", views.contact_feedback, name="contact_feedback"),
    path("thong-bao/", views.notifications, name="notifications"),
    path("lich-kham-sap-toi/", views.upcoming_appointments, name="upcoming_appointments"),
    path("lich-kham/<int:lich_id>/huy/", views.cancel_appointment, name="cancel_appointment"),
    path("lich-su-kham/", views.medical_history, name="medical_history"),
    path("phieu-kham/<int:phieu_id>/", views.phieu_kham_detail, name="phieu_kham_detail"),
    path("bac-si/kham/<int:lich_id>/", views.doctor_exam, name="doctor_exam"),
    path("bac-si/", views.bac_si_home, name="bac_si_home"),
    path("bac-si/benh-nhan/<int:benh_nhan_id>/", views.bac_si_patient_detail, name="bac_si_patient_detail"),
    path("bac-si/lich/<int:lich_id>/bat-dau/", views.start_exam, name="start_exam"),
    path("bac-si/thong-bao/", views.bac_si_notifications, name="bac_si_notifications"),
    path("bac-si/lich-lam-viec/", views.bac_si_schedule, name="bac_si_schedule"),
    path("bac-si/phieu-kham/<int:phieu_id>/sua/", views.bac_si_phieu_kham_edit, name="bac_si_phieu_kham_edit"),
    path("profile/", views.profile, name="profile"),

]
