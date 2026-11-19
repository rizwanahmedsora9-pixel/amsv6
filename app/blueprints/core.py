"""HTTP routes: core."""
from __future__ import annotations

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, Response, make_response, send_from_directory, abort, session
from flask_login import login_required, login_user, logout_user, current_user
from datetime import datetime, date, timedelta
from sqlalchemy import func, case, text, or_, and_, exists, not_
from types import SimpleNamespace
from decimal import Decimal, ROUND_HALF_UP
import os, io, json, re, logging, calendar

from models import *
from app.services.api import *  # noqa
from utils.audit import audit_log

bp = Blueprint('core', __name__)

@bp.route('/')
@login_required
def index():
    today = pk_today().strftime('%B %d, %Y')
    today_date = pk_today()

    client_count = db.session.query(func.count(Client.id)).scalar() or 0

    # Current Stock by Brand — authoritative movement ledger (entry IN/OUT,
    # non-voided) grouped by material, merged onto the Material master so:
    #   * every material from the master appears (0/0/0 rows for materials
    #     that exist but have no movements yet — the reported "empty table")
    #   * entry materials that are no longer in the master are still shown
    #     explicitly instead of being silently dropped
    # The grouped aggregation stays in SQL (one query); no per-row loading.
    stats_query = db.session.query(
        Entry.material,
        func.sum(case((Entry.type == 'IN', Entry.qty), else_=0)).label('total_in'),
        func.sum(case((Entry.type == 'OUT', Entry.qty), else_=0)).label('total_out')
    ).filter(Entry.is_void == False).group_by(Entry.material).all()

    material_units = {
        (m.name or '').strip().lower(): (m.unit or 'Bags')
        for m in Material.query.with_entities(Material.name, Material.unit).all()
    }
    material_names = sorted(
        {(m.name or '').strip() for m in
         Material.query.with_entities(Material.name).all()}
    )

    entry_map = {}
    for row in stats_query:
        entry_map[(row.material or '').strip()] = {
            'in': int(row.total_in or 0),
            'out': int(row.total_out or 0),
        }

    # Every material from the master, plus any entry-only material
    # (explicitly marked rather than silently lost) — excluding services,
    # whose Entry rows exist for the booking pool, not physical stock.
    all_names = [
        n for n in (
            material_names + sorted(
                n for n in entry_map.keys() if n and n not in material_names
            )
        ) if not is_service_name(n)
    ]
    stats = sorted([{
        'name': name or 'Unknown/Unbranded',
        'in': entry_map.get(name, {}).get('in', 0),
        'out': entry_map.get(name, {}).get('out', 0),
        'stock': (entry_map.get(name, {}).get('in', 0)
                  - entry_map.get(name, {}).get('out', 0)),
        'unit': material_units.get((name or '').lower(), 'Bags'),
        'has_movement': name in entry_map,
    } for name in all_names], key=lambda x: x['name'])

    total_stock = sum(s['stock'] for s in stats)

    # Daily Cash — money actually handed over today.
    # The old sum counted EVERY Payment row, which swept in material-return
    # credit notes (goods handed back, no cash) and waive-offs.  The shared
    # resolver keeps only real receipts, so the tile matches the cash ledger and
    # the /financial_details drill-down beside it.
    cash_components = cash_receipt_components(today_date)
    # The tile reports cash taken on the day's own trading documents.  Client
    # receipts posted separately are reported beside it so the two figures
    # together equal the cash ledger's "money in" for the day — the tile is a
    # trading figure, not a replacement for the ledger.
    daily_cash = cash_components['trading']
    daily_cash_other = cash_components['payments']
    daily_cash_ledger = cash_components['total']

    # Daily Due — money collectable for goods that have actually been delivered.
    # An unfulfilled booking is not a receivable: the customer has promised to
    # buy, but nothing has left the shop.  It is reported separately (below) so
    # it stays visible without inflating what the shop can collect today.
    due_components = due_on_delivered_goods(today_date)
    daily_credit = due_components['delivered']
    daily_undelivered = due_components['undelivered']

    # Total Outstanding is the same grouped client-ledger projection used by
    # Current Payables and Accounts.  It must not sum derived PendingBill rows.
    payable_report = build_current_payables(status='outstanding', page=1, per_page=200)
    total_outstanding = float(payable_report.get('total_outstanding') or 0)

    # The tile above is a NET client-ledger figure: a client holding an
    # unallocated advance nets to zero even though their bills are still open,
    # so the old label ("Real dues across open bills") was simply untrue and
    # read 0.00 while 17,500 of bills were outstanding.  The gross figure is
    # published next to it, from the same pending-bill rows the unpaid-bills
    # screen lists.
    open_bill_total = round(float(
        db.session.query(func.coalesce(func.sum(PendingBill.amount), 0.0))
        .filter(PendingBill.is_void == False, PendingBill.is_paid == False)  # noqa: E712
        .scalar() or 0
    ), 2)

    # Daily Sales Breakdown
    sales_breakdown = {}

    # 1. Bookings
    booking_total = db.session.query(func.sum(Booking.amount)).filter(func.date(Booking.date_posted) == today_date, Booking.is_void == False).scalar() or 0
    if booking_total > 0:
        sales_breakdown['Bookings'] = booking_total

    # 2. Direct Sales
    ds_query = db.session.query(DirectSale.category, func.sum(DirectSale.amount))\
        .filter(func.date(DirectSale.date_posted) == today_date, DirectSale.is_void == False)\
        .group_by(DirectSale.category).all()

    for cat, amt in ds_query:
        if amt > 0:
            cat_name = normalize_sale_category(cat, default='Credit Customer')
            if cat_name == 'Credit Customer':
                cat_name = 'Credit Sales'
            elif cat_name == 'Cash':
                cat_name = 'Cash Sales'
            sales_breakdown[cat_name] = sales_breakdown.get(cat_name, 0) + amt

    sales_breakdown_list = [{'category': k, 'amount': v} for k, v in sales_breakdown.items()]
    sales_breakdown_list.sort(key=lambda x: x['amount'], reverse=True)

    # ``clients`` / ``materials`` dropdowns are injected globally by the
    # inject_dropdown_data context processor (same data, 20s app-level cache);
    # re-loading both full tables here duplicated two scans per dashboard hit.
    return render_template('index.html',
                           today_date=today,
                           total_stock=int(total_stock),
                           client_count=client_count,
                           stats=stats,
                           daily_cash=daily_cash,
                           daily_cash_other=daily_cash_other,
                           daily_cash_ledger=daily_cash_ledger,
                           daily_credit=daily_credit,
                           daily_undelivered=daily_undelivered,
                           total_outstanding=total_outstanding,
                           open_bill_total=open_bill_total,
                           sales_breakdown=sales_breakdown_list)


