"""Exercise the Sales list's read-only posted date filters on an isolated database."""
from datetime import datetime
from html import unescape
import re
from urllib.parse import parse_qs, urlsplit

from flask import template_rendered
import pytest

from app import create_app
from models import DirectSale, db


@pytest.fixture
def sales_page(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DB_PATH', str(tmp_path / 'sales.db'))
    monkeypatch.setenv('AMS_SCHEMA_VERSION', 'v44')
    monkeypatch.setenv('DB_HEALTH_SNAPSHOT_PATH', str(tmp_path / 'health_snapshot.json'))
    monkeypatch.setenv('SECRET_KEY', 'isolated-sales-date-test')
    monkeypatch.setenv('DEFAULT_ADMIN_USER', 'SalesTestAdmin')
    monkeypatch.setenv('DEFAULT_ADMIN_PASSWORD', 'sales-test-only-password')
    app = create_app({'TESTING': True, 'INSTANCE_PATH': tmp_path / 'instance',
                      'ENABLE_BACKGROUND_WORKERS': False, 'DAILY_BACKUP_SCHEDULER': False,
                      'SESSION_COOKIE_SECURE': False})
    assert not app.config.get('AMS_BOOTSTRAP_ERROR')
    web = app.test_client()
    assert web.post('/login', data={'username': 'SalesTestAdmin', 'password': 'sales-test-only-password'}).status_code == 302

    records = [
        ('BEFORE', datetime(2026, 11, 15, 23, 59, 59), 'Alice', False),
        ('FIRST', datetime(2026, 11, 16), 'Alice', False),
        ('LAST', datetime(2026, 11, 16, 23, 59, 59, 999999), 'Alice', False),
        ('AFTER', datetime(2026, 11, 17), 'Alice', False),
        ('VOIDED', datetime(2026, 11, 16, 9), 'Alice', True),
        ('OTHER', datetime(2026, 11, 16, 9), 'Bob', False),
    ]
    records += [(f'MID-{n}', datetime(2026, 11, 16, 10, n), 'Alice', False) for n in range(9)]
    with app.app_context():
        sales = {bill: DirectSale(date_posted=when, client_name=client, category='Cash', is_void=is_void)
                 for bill, when, client, is_void in records}
        db.session.add_all(sales.values())
        db.session.flush()
        labels = {sale.id: bill for bill, sale in sales.items()}
        db.session.commit()

    rendered = {}

    def capture(sender, template, context, **extra):
        if template.name == 'direct_sales.html':
            rendered.update(context)

    def get(path='/direct_sales', **query):
        rendered.clear()
        with template_rendered.connected_to(capture, app):
            response = web.get(path, query_string=query if query else None)
        assert response.status_code == 200
        visible_sales = {labels[sale.id] for sale in rendered['sales']}
        return visible_sales, rendered['pagination'].total, response.get_data(as_text=True)

    return get


def test_sales_date_range_includes_whole_days_and_survives_navigation(sales_page):
    filters = {'date_from': '2026-11-16', 'date_to': '2026-11-16', 'client': 'Alice'}
    first_page, total, html = sales_page(**filters)
    assert total == 11
    assert len(first_page) == 10
    for field, value in filters.items():
        assert re.search(rf'<input[^>]*name="{field}"[^>]*value="{value}"', html)

    next_url = unescape(re.search(r'href="([^"]+)">Next</a>', html)[1])
    last_page, total, html = sales_page(next_url)
    assert total == 11
    assert first_page.isdisjoint(last_page)
    assert first_page | last_page == {'FIRST', 'LAST', *(f'MID-{n}' for n in range(9))}

    previous_url = unescape(re.search(r'href="([^"]+)">Previous</a>', html)[1])
    assert sales_page(previous_url)[0] == first_page

    all_url = unescape(re.search(r'href="([^"]+)"[^>]*>All</a>', html)[1])
    query = parse_qs(urlsplit(all_url).query)
    assert query['show'] == ['all']
    assert all(query[key] == [value] for key, value in filters.items())
    assert sales_page(all_url)[1] == 12  # same filters, plus the voided sale

    reset_url = unescape(re.search(r'href="([^"]+)"[^>]*>Reset</a>', html)[1])
    assert sales_page(reset_url)[1] == 14  # all active sales without date or client filters


def test_sales_date_range_can_use_either_bound(sales_page):
    assert sales_page(date_from='2026-11-17')[:2] == ({'AFTER'}, 1)
    assert sales_page(date_to='2026-11-15')[:2] == ({'BEFORE'}, 1)


@pytest.mark.parametrize(('query', 'error'), [
    ({'date_from': '2026-02-30'}, 'Enter valid dates in YYYY-MM-DD format.'),
    ({'date_to': '2026-11-15', 'date_from': '2026-11-16'}, 'To Date must be on or after From Date.'),
])
def test_invalid_sales_date_range_prompts_for_correction(sales_page, query, error):
    bills, total, html = sales_page(**query)
    assert not bills and total == 0
    assert error in html
