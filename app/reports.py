"""Authenticated DSS reporting exports (Excel and PDF)."""
from datetime import date
from io import BytesIO
from functools import wraps

from flask import Blueprint, jsonify, request, send_file
from app.auth import get_current_user
from app.db import get_db_connection

reports_bp = Blueprint('reports', __name__, url_prefix='/api/reports')


def require_user(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user:
            return jsonify(success=False, message='Sesi tidak valid atau sudah berakhir.'), 401
        return fn(user, *args, **kwargs)
    return wrapper


def _filters(user):
    period = (request.args.get('period') or 'all').lower()
    if period not in ('all', 'month', 'year'):
        raise ValueError('Periode tidak valid.')
    today = date.today()
    year = int(request.args.get('year', today.year))
    month = int(request.args.get('month', today.month))
    if not 2000 <= year <= 2100 or not 1 <= month <= 12:
        raise ValueError('Tahun atau bulan tidak valid.')
    department_id = request.args.get('department_id', '').strip()
    if department_id and user['role'] != 'ADMIN':
        raise PermissionError('Filter departemen hanya tersedia untuk Admin.')
    return period, year, month, department_id


def _fetch(user, period, year, month, department_id):
    clauses, params = ['1=1'], []
    if period in ('month', 'year'):
        clauses.append("strftime('%Y', s.created_at) = ?")
        params.append(f'{year:04d}')
    if period == 'month':
        clauses.append("strftime('%m', s.created_at) = ?")
        params.append(f'{month:02d}')
    if department_id:
        clauses.append('s.department_id = ?')
        params.append(int(department_id))
    if user['role'] != 'ADMIN':
        clauses.append('''(s.submitted_by = ? OR EXISTS (
            SELECT 1 FROM ss_contributors cx WHERE cx.submission_id=s.id AND cx.user_id=?))''')
        params.extend([user['id'], user['id']])
    sql = f'''SELECT s.id, s.ss_number, s.title, s.status, s.created_at, s.submitted_at,
        s.condition_before, s.improvement_action, s.result_after, s.expected_benefit, s.actual_benefit,
        d.name AS department_name, cat.name AS category_name, u.full_name AS submitter_name,
        COALESCE((SELECT GROUP_CONCAT(uc.full_name, ', ') FROM ss_contributors c JOIN users uc ON uc.id=c.user_id WHERE c.submission_id=s.id), u.full_name) AS contributors,
        COALESCE((SELECT SUM(k.points) FROM ss_kpi_points k WHERE k.submission_id=s.id), 0) AS kpi_points
        FROM ss_submissions s LEFT JOIN departments d ON d.id=s.department_id
        LEFT JOIN suggestion_categories cat ON cat.id=s.category_id
        LEFT JOIN users u ON u.id=s.submitted_by
        WHERE {' AND '.join(clauses)} ORDER BY s.created_at DESC, s.id DESC'''
    with get_db_connection() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _rows(user):
    try:
        return _fetch(user, *_filters(user)), None
    except (ValueError, PermissionError) as exc:
        return None, (jsonify(success=False, message=str(exc)), 400)


@reports_bp.get('/suggestions.xlsx')
@require_user
def export_excel(user):
    rows, error = _rows(user)
    if error:
        return error
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        return jsonify(success=False, message='Dependensi openpyxl belum terpasang.'), 503
    wb = Workbook()
    ws = wb.active
    ws.title = 'Rekap Suggestion'
    ws.append(['No. DSS', 'Judul', 'Departemen', 'Kategori', 'Pengusul', 'Kontributor', 'Status', 'Tanggal Dibuat', 'Tanggal Submit', 'Kondisi Sebelum', 'Tindakan Improvement', 'Hasil Sesudah', 'Benefit Diharapkan', 'Benefit Aktual', 'Poin KPI'])
    for row in rows:
        ws.append([row.get('ss_number'), row.get('title'), row.get('department_name'), row.get('category_name'), row.get('submitter_name'), row.get('contributors'), row.get('status'), row.get('created_at'), row.get('submitted_at'), row.get('condition_before'), row.get('improvement_action'), row.get('result_after'), row.get('expected_benefit'), row.get('actual_benefit'), row.get('kpi_points')])
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='147D70')
        cell.alignment = Alignment(vertical='center', wrap_text=True)
    widths = [18, 32, 20, 20, 22, 35, 18, 21, 21, 35, 40, 35, 30, 30, 12]
    for idx, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical='top', wrap_text=True)
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name='DSS-Rekap-Suggestion.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@reports_bp.get('/suggestions.pdf')
@require_user
def export_pdf(user):
    rows, error = _rows(user)
    if error:
        return error
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
            KeepTogether
        )
    except ImportError:
        return jsonify(success=False, message='Dependensi reportlab belum terpasang.'), 503

    # Formal company report. No company logo or signatory names are fabricated.
    output = BytesIO()
    page_size = landscape(A4)
    doc = SimpleDocTemplate(
        output, pagesize=page_size,
        rightMargin=12 * mm, leftMargin=12 * mm,
        topMargin=15 * mm, bottomMargin=19 * mm,
        title='Laporan Digital Suggestion System - PT INDOFOOD FORTUNA MAKMUR',
        author='Digital Suggestion System'
    )
    styles = getSampleStyleSheet()
    company_style = ParagraphStyle(
        'CompanyHeader', parent=styles['Title'], fontName='Helvetica-Bold',
        fontSize=15, leading=18, alignment=1, textColor=colors.HexColor('#12324A'),
        spaceAfter=2
    )
    report_style = ParagraphStyle(
        'ReportHeader', parent=styles['Heading2'], fontName='Helvetica-Bold',
        fontSize=11, leading=14, alignment=1, textColor=colors.HexColor('#147D70'),
        spaceAfter=2
    )
    meta_style = ParagraphStyle(
        'ReportMeta', parent=styles['Normal'], fontSize=8, leading=11,
        alignment=1, textColor=colors.HexColor('#526579')
    )
    small = ParagraphStyle('ReportSmall', parent=styles['BodyText'], fontSize=6.8, leading=8.4)
    small_center = ParagraphStyle('ReportSmallCenter', parent=small, alignment=1)
    section_style = ParagraphStyle(
        'ReportSection', parent=styles['Heading3'], fontSize=9,
        leading=12, textColor=colors.HexColor('#12324A'), spaceBefore=5, spaceAfter=4
    )

    period, year, month, department_id = _filters(user)
    period_label = 'Seluruh Periode'
    if period == 'year':
        period_label = f'Tahun {year}'
    elif period == 'month':
        period_label = f'{month:02d}/{year}'
    department_label = 'Seluruh Departemen'
    if department_id and rows:
        department_label = rows[0].get('department_name') or f'Departemen ID {department_id}'

    status_counts = {}
    for row in rows:
        key = (row.get('status') or 'UNKNOWN').upper()
        status_counts[key] = status_counts.get(key, 0) + 1
    approved_count = status_counts.get('APPROVED_FINAL', 0)
    pending_count = sum(v for k, v in status_counts.items() if k not in ('APPROVED_FINAL', 'REJECTED', 'CANCELLED'))
    rejected_count = status_counts.get('REJECTED', 0)
    total_points = sum(int(row.get('kpi_points') or 0) for row in rows)

    def safe(value):
        text = str(value if value not in (None, '') else '-')
        return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\n', '<br/>')

    def draw_footer(canvas, document):
        canvas.saveState()
        width, _ = page_size
        canvas.setStrokeColor(colors.HexColor('#D5DEE7'))
        canvas.setLineWidth(0.5)
        canvas.line(12 * mm, 13 * mm, width - 12 * mm, 13 * mm)
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#657789'))
        canvas.drawString(12 * mm, 8 * mm, 'PT INDOFOOD FORTUNA MAKMUR | DIGITAL SUGGESTION SYSTEM')
        canvas.drawRightString(width - 12 * mm, 8 * mm, f'Halaman {document.page}')
        canvas.restoreState()

    story = [
        Paragraph('PT INDOFOOD FORTUNA MAKMUR', company_style),
        Paragraph('DIGITAL SUGGESTION SYSTEM', report_style),
        Paragraph('LAPORAN RESMI REKAPITULASI SUGGESTION', styles['Heading2']),
        Paragraph(
            f'Periode: {safe(period_label)} &nbsp;&nbsp; | &nbsp;&nbsp; '
            f'Departemen: {safe(department_label)} &nbsp;&nbsp; | &nbsp;&nbsp; '
            f'Tanggal cetak: {date.today().strftime("%d-%m-%Y")}', meta_style
        ),
        Spacer(1, 5 * mm),
        Paragraph('RINGKASAN LAPORAN', section_style)
    ]

    summary_data = [[
        Paragraph('<b>Total Suggestion</b>', small_center),
        Paragraph('<b>Approved Final</b>', small_center),
        Paragraph('<b>Dalam Proses</b>', small_center),
        Paragraph('<b>Rejected</b>', small_center),
        Paragraph('<b>Total Poin KPI</b>', small_center)
    ], [str(len(rows)), str(approved_count), str(pending_count), str(rejected_count), str(total_points)]]
    summary = Table(summary_data, colWidths=[(page_size[0] - 24 * mm) / 5] * 5)
    summary.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#EAF2F5')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#12324A')),
        ('BACKGROUND', (0, 1), (-1, 1), colors.white),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD7E1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#D8E0E8')),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.extend([summary, Spacer(1, 4 * mm), Paragraph('RINCIAN DATA SUGGESTION', section_style)])

    headers = ['No. DSS', 'Judul', 'Departemen', 'Kategori', 'Pengusul / Kontributor', 'Status', 'Tanggal Dibuat', 'Poin']
    data = [[Paragraph(f'<b>{safe(value)}</b>', small) for value in headers]]
    for row in rows:
        vals = [
            row.get('ss_number'), row.get('title'), row.get('department_name'),
            row.get('category_name'),
            (row.get('submitter_name') or '-') + ' / ' + (row.get('contributors') or '-'),
            row.get('status'), row.get('created_at'), str(row.get('kpi_points') or 0)
        ]
        data.append([Paragraph(safe(value), small) for value in vals])
    available_width = page_size[0] - 24 * mm
    proportions = [0.105, 0.16, 0.105, 0.09, 0.235, 0.115, 0.13, 0.06]
    table = Table(data, repeatRows=1, colWidths=[available_width * value for value in proportions])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#147D70')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#D8E0E8')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F4F7FA')]),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(table)

    signature_block = [
        Spacer(1, 10 * mm),
        Paragraph('PENGESAHAN', section_style),
    ]
    signature_data = [[
        Paragraph('Dibuat oleh,<br/><b>ADMIN DSS</b><br/><br/><br/><br/>(________________________)', small_center),
        Paragraph('Diperiksa oleh,<br/><b>MANAGER</b><br/><br/><br/><br/>(________________________)', small_center),
    ]]
    signatures = Table(signature_data, colWidths=[available_width / 2] * 2, rowHeights=[34 * mm])
    signatures.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD7E1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD7E1')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
    ]))
    signature_block.append(signatures)
    story.append(KeepTogether(signature_block))

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    output.seek(0)
    return send_file(
        output, as_attachment=True,
        download_name='DSS-Laporan-Resmi-PT-Indofood-Fortuna-Makmur.pdf',
        mimetype='application/pdf'
    )
