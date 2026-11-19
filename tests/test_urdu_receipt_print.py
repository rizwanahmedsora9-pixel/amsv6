"""Verify the real receipt route ships a small, self-contained Urdu printout."""
from datetime import datetime
from io import BytesIO
import sys

import pytest

from app import create_app
from app.services import bill_urdu, urdu_receipt
from models import DirectSale, DirectSaleItem, db


@pytest.fixture(scope='module')
def receipt_request(tmp_path_factory):
    root = tmp_path_factory.mktemp('urdu-receipt')
    with pytest.MonkeyPatch.context() as env:
        env.setenv('APP_DB_PATH', str(root / 'receipts.db'))
        env.setenv('AMS_SCHEMA_VERSION', 'v44')
        env.setenv('DB_HEALTH_SNAPSHOT_PATH', str(root / 'health_snapshot.json'))
        env.setenv('AMS_FONT_CACHE_DIR', str(root / 'fonts'))
        env.setenv('SECRET_KEY', 'isolated-urdu-receipt-test')
        env.setenv('DEFAULT_ADMIN_USER', 'ReceiptTestAdmin')
        env.setenv('DEFAULT_ADMIN_PASSWORD', 'receipt-test-only-password')
        app = create_app({'TESTING': True, 'INSTANCE_PATH': root / 'instance',
                          'ENABLE_BACKGROUND_WORKERS': False, 'DAILY_BACKUP_SCHEDULER': False,
                          'SESSION_COOKIE_SECURE': False})
        assert not app.config.get('AMS_BOOTSTRAP_ERROR')
        with app.app_context():
            sale = DirectSale(client_name='محمد احمد', category='Cash', amount=7500,
                              manual_bill_no='7501', date_posted=datetime(2026, 11, 19, 14))
            sale.items = [DirectSaleItem(product_name='سیمنٹ 50 کلو', qty=3, price_at_time=2500)]
            db.session.add(sale)
            db.session.flush()
            sale_id = sale.id
            db.session.commit()

        web = app.test_client()
        assert web.post('/login', data={'username': 'ReceiptTestAdmin',
                                        'password': 'receipt-test-only-password'}).status_code == 302

        def get(**options):
            query = {'src': 'direct_sale', 'src_id': sale_id, 'lang': 'ur',
                     'action': 'print', 'tc': '0', **options}
            return web.get('/download_invoice/7501',
                           query_string={key: value for key, value in query.items() if value is not None})

        yield get


@pytest.mark.parametrize(('copies', 'width', 'height'), [
    ('1', 419.53, 595.28),  # A5 portrait
    ('2', 841.89, 595.28),  # two A5 copies on one A4 landscape leaf
])
def test_urdu_print_embeds_jameel_in_an_inline_pdf(receipt_request, copies, width, height):
    pytest.importorskip('reportlab')
    pytest.importorskip('uharfbuzz')
    pytest.importorskip('fontTools')
    pdf_reader = pytest.importorskip('pypdf').PdfReader
    response = receipt_request(copies=copies)
    assert response.status_code == 200
    assert response.mimetype == 'application/pdf'
    assert response.headers['Content-Disposition'].startswith('inline;')
    assert response.cache_control.no_store
    assert len(response.data) < 200_000  # the glyph subset, not the full 10.8 MB font

    pdf = pdf_reader(BytesIO(response.data))
    assert len(pdf.pages) == 1
    page = pdf.pages[0]
    assert float(page.mediabox.width) == pytest.approx(width, abs=0.01)
    assert float(page.mediabox.height) == pytest.approx(height, abs=0.01)
    fonts = page['/Resources']['/Font']
    jameel = {name for name, ref in fonts.items()
              if 'Jameel-Noori-Nastaleeq' in ref.get_object()['/BaseFont']}
    assert jameel and all('/FontFile2' in fonts[name]['/FontDescriptor'] for name in jameel)

    # The shaped glyph IDs are private to the font, not extractable Urdu text.
    # Verify the page actually draws those glyphs with the embedded face.
    font = None
    urdu_glyphs = 0
    unit_prices = 0
    for operands, operator in page.get_contents().operations:
        if operator == b'Tf':
            font = operands[0]
        elif operator == b'Tj':
            urdu_glyphs += font in jameel and bool(operands[0])
            unit_prices += operands[0] == '2,500'
    assert urdu_glyphs > 0
    assert unit_prices == int(copies)


def test_urdu_pdf_download_is_still_an_attachment(receipt_request):
    pytest.importorskip('reportlab')
    pytest.importorskip('fontTools')
    response = receipt_request(action='download')
    assert response.status_code == 200
    assert response.mimetype == 'application/pdf'
    assert response.headers['Content-Disposition'].startswith('attachment;')
    assert response.cache_control.no_store


def test_explicit_browser_print_skips_pdf_rendering(receipt_request, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('The explicit Browser Print choice should not render a PDF')

    monkeypatch.setattr(bill_urdu, 'render_pdf', unexpected)
    response = receipt_request(print_format='html')
    assert response.status_code == 200
    assert response.mimetype == 'text/html'
    assert b'JameelNooriNastaleeq.ttf' in response.data
    assert b'printUrduReceipt()' in response.data
    assert response.cache_control.no_store


def test_urdu_print_falls_back_if_pdf_rendering_raises(receipt_request, monkeypatch):
    pytest.importorskip('reportlab')
    pytest.importorskip('uharfbuzz')

    def unavailable(*args, **kwargs):
        raise RuntimeError('test PDF backend failure')

    monkeypatch.setattr(bill_urdu, 'render_pdf', unavailable)
    response = receipt_request(copies='2')
    assert response.status_code == 200
    assert response.mimetype == 'text/html'
    assert b'size: 841.8898pt 595.2756pt' in response.data
    assert response.cache_control.no_store


def test_browser_print_remains_available_without_pdf_dependencies(receipt_request, monkeypatch):
    # urdu_text normally requires ReportLab. HTML can still let the browser
    # shape the font when the server only has the core dependencies.
    monkeypatch.setitem(sys.modules, 'app.services.urdu_text', None)
    response = receipt_request()
    assert response.status_code == 200
    assert response.mimetype == 'text/html'
    assert b'JameelNooriNastaleeq.ttf' in response.data
    assert response.cache_control.no_store


def test_confirm_page_keeps_the_requested_browser_print_format(receipt_request):
    response = receipt_request(tc=None, print_format='html', copies='2')
    assert response.status_code == 200
    assert response.mimetype == 'text/html'
    assert b'name="print_format" value="html"' in response.data


def test_missing_mapped_urdu_font_does_not_produce_a_blank_pdf(monkeypatch):
    pytest.importorskip('reportlab')
    monkeypatch.setattr(urdu_receipt, 'urdu_font_name', lambda: None)
    assert urdu_receipt.render_pdf(urdu_receipt.build_document()) is None
