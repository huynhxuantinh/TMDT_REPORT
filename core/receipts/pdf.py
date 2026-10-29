"""In-memory A4 documents. No public media files or clinical fields."""
from io import BytesIO
from pathlib import Path
from threading import Lock
from xml.sax.saxutils import escape

from django.db import transaction
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.models import HoaDon, LichKham

ROOT = Path(__file__).resolve().parent
_font_lock = Lock()


def register_fonts():
    with _font_lock:
        if 'ReceiptNoto' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('ReceiptNoto', str(ROOT / 'fonts/NotoSans-Regular.ttf')))
            pdfmetrics.registerFont(TTFont('ReceiptNotoBold', str(ROOT / 'fonts/NotoSans-Bold.ttf')))
            pdfmetrics.registerFontFamily('ReceiptNoto', normal='ReceiptNoto', bold='ReceiptNotoBold')


def money(value):
    return f'{value:,.0f}'.replace(',', '.') + ' VND'


def date_time(value):
    return timezone.localtime(value).strftime('%d/%m/%Y %H:%M:%S') if value else 'Chưa ghi nhận'


def snapshot(invoice):
    """Invoice must be locked by caller to keep its version and lines consistent."""
    appointment = LichKham.objects.select_related('benh_nhan', 'bac_si__benh_vien').get(pk=invoice.lich_kham_id)
    payment = invoice.giao_dichs.filter(trang_thai='success').select_related('benh_vien').first()
    pending = invoice.giao_dichs.filter(trang_thai='pending').exists()
    zero = invoice.hoan_tat_bhyt and invoice.so_tien == 0
    if zero:
        method = 'Không cần thu tiền (0đ do BHYT MÔ PHỎNG)'
        status = 'Đã hoàn tất bằng BHYT MÔ PHỎNG'
    elif invoice.trang_thai == 'paid':
        method = payment.get_phuong_thuc_display() if payment else 'Chưa lưu phương thức (hóa đơn cũ)'
        status = 'Đã thanh toán'
    else:
        method = 'VNPAY (đang xử lý)' if pending else 'Chưa ghi nhận thanh toán'
        status = 'Đang xử lý — chưa xác nhận thanh toán' if pending else 'Chưa thanh toán'
    if invoice.can_doi_soat:
        status = 'Cần đối soát — chưa phát hành xác nhận thu tiền'
    hospital = payment.benh_vien if payment and payment.benh_vien_id else appointment.bac_si.benh_vien
    return {
        'id': invoice.pk, 'version': invoice.phien_ban, 'created': date_time(invoice.ngay_tao),
        'hospital': hospital.ten, 'patient': appointment.benh_nhan.ho_ten,
        'exam_date': appointment.ngay_kham.strftime('%d/%m/%Y'),
        'lines': list(invoice.chi_tiet_hien_hanh.values('ma_dich_vu', 'ten_dich_vu', 'so_luong', 'don_gia', 'thanh_tien')),
        'gross': invoice.tong_truoc_bhyt, 'support': invoice.bhyt_ho_tro, 'amount': invoice.so_tien,
        'method': method, 'status': status, 'paid_at': date_time(invoice.ngay_thanh_toan) if invoice.trang_thai == 'paid' else 'Chưa ghi nhận',
        'confirmed': invoice.trang_thai == 'paid' and not invoice.can_doi_soat,
    }


@transaction.atomic
def invoice_snapshot(invoice_id):
    return snapshot(HoaDon.objects.select_for_update().get(pk=invoice_id))


def render_pdf(data):
    register_fonts()
    output = BytesIO()
    styles = {
        'body': ParagraphStyle('body', fontName='ReceiptNoto', fontSize=9, leading=14, spaceAfter=4),
        'title': ParagraphStyle('title', fontName='ReceiptNotoBold', fontSize=16, leading=23, alignment=TA_CENTER, spaceAfter=10),
        'bold': ParagraphStyle('bold', fontName='ReceiptNotoBold', fontSize=9, leading=14),
        'right': ParagraphStyle('right', fontName='ReceiptNoto', fontSize=8, leading=12, alignment=TA_RIGHT),
        'cell': ParagraphStyle('cell', fontName='ReceiptNoto', fontSize=8, leading=12),
    }

    def p(text, style='body'):
        # Never allow catalog/patient values to introduce ReportLab markup or URLs.
        return Paragraph(escape(str(text)), styles[style])

    story = []
    logo = ROOT.parent / 'static/core/img/benh-vien-3t-logo.png'
    brand = Table([[Image(str(logo), width=14*mm, height=14*mm, kind='proportional'), p('MedCare GIS', 'bold')]], colWidths=[18*mm, 160*mm])
    brand.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
    story += [brand, Spacer(1, 6*mm), p('PHIẾU THANH TOÁN NỘI BỘ', 'title'),
        p('Không phải hóa đơn điện tử có giá trị pháp lý.'),
        p(f'Mã hóa đơn: #{data["id"]} · Phiên bản: {data["version"]}'), p(f'Ngày lập: {data["created"]}'),
        p(f'Bệnh viện: {data["hospital"]}'), p(f'Bệnh nhân: {data["patient"]}'),
        p(f'Ngày khám: {data["exam_date"]}'), p(f'Trạng thái: {data["status"]}', 'bold'), Spacer(1, 4*mm)]
    if not data['confirmed']:
        story += [p('Phiếu này chưa xác nhận đã thu tiền. Vui lòng kiểm tra trạng thái tại quầy.'), Spacer(1, 3*mm)]
    if data['lines']:
        rows = [[p('Dịch vụ', 'bold'), p('SL', 'bold'), p('Đơn giá đã chốt', 'bold'), p('Thành tiền', 'bold')]]
        for line in data['lines']:
            rows.append([p(f'{line["ma_dich_vu"]} · {line["ten_dich_vu"]}', 'cell'), p(line['so_luong'], 'cell'),
                         p(money(line['don_gia']), 'right'), p(money(line['thanh_tien']), 'right')])
        table = Table(rows, colWidths=[85*mm, 12*mm, 40*mm, 41*mm], repeatRows=1, hAlign='LEFT')
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eaf6ee')),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#d8e4df')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 7),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ]))
        story += [table, Spacer(1, 5*mm)]
    else:
        story += [p('Hóa đơn cũ chưa lưu chi tiết dịch vụ. Tổng tiền theo hóa đơn đã chốt.'), Spacer(1, 4*mm)]
    story += [p(f'Tổng chi phí: {money(data["gross"])}'), p(f'BHYT hỗ trợ (MÔ PHỎNG): {money(data["support"])}'),
        p(f'Tổng tiền bệnh nhân phải trả: {money(data["amount"])}', 'bold'), Spacer(1, 4*mm),
        p(f'Phương thức: {data["method"]}'), p(f'Thời gian thanh toán / hoàn tất: {data["paid_at"]}'),
        Spacer(1, 5*mm), p('BHYT MÔ PHỎNG phục vụ đồ án; không phải kết quả giám định BHYT thực tế.')]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('ReceiptNoto', 8)
        canvas.setFillColor(colors.HexColor('#647772'))
        canvas.drawString(16*mm, 10*mm, f'MedCare GIS · Phiếu nội bộ #{data["id"]}')
        canvas.drawRightString(A4[0]-16*mm, 10*mm, f'Trang {doc.page}')
        canvas.restoreState()

    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=16*mm, rightMargin=16*mm,
        topMargin=14*mm, bottomMargin=19*mm, title=f'Phieu thanh toan noi bo #{data["id"]}', author='MedCare GIS')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
