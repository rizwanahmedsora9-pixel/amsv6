"""Shared constants and mutable process state."""
from __future__ import annotations

import os
import io
import secrets
import json
import calendar
import threading
import shutil
import sqlite3
import zipfile
import urllib.request
import urllib.error
import re
import logging
import importlib
from itertools import zip_longest
from urllib.parse import unquote
from contextlib import redirect_stderr
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from sqlalchemy import func, case, text, or_, and_, exists, not_
from sqlalchemy.orm import selectinload
from types import SimpleNamespace
from flask import (
    current_app as app,
    render_template, request, redirect, url_for, flash, jsonify,
    send_file, Response, make_response, send_from_directory,
    got_request_exception, abort, session, g,
)
from flask_login import LoginManager, login_user, login_required, logout_user, current_user

from models import *
from utils.audit import audit_log
from utils.reconciliation import run_auto_reconcile
from cash_flow_reconciliation_helpers import (
    create_reconciliation, update_reconciliation, delete_reconciliation,
    get_reconciliation_history, migrate_legacy_record,
)

OPEN_KHATA_CODE = 'OPEN-KHATA'

OPEN_KHATA_NAME = 'OPEN KHATA'

_HOURLY_BACKUP_WORKER_STARTED = False

_HOURLY_BACKUP_LAST_SLOT = None

_RECON_WORKER_STARTED = False

PK_TZ = ZoneInfo('Asia/Karachi')

SALE_CATEGORY_CHOICES = ['Booking Delivery', 'Mixed Transaction', 'Credit Customer', 'Open Khata', 'Cash']

_SALE_CATEGORY_ALIASES = {
    'booked sale': 'Booking Delivery',
    'booked': 'Booking Delivery',
    'booking delivery': 'Booking Delivery',
    'booking': 'Booking Delivery',
    'booked +credit': 'Mixed Transaction',
    'booked+credit': 'Mixed Transaction',
    'booked + credit': 'Mixed Transaction',
    'mixed': 'Mixed Transaction',
    'mixed transaction': 'Mixed Transaction',
    'credit': 'Credit Customer',
    'credit sale': 'Credit Customer',
    'credit customer': 'Credit Customer',
    'open khata': 'Open Khata',
    'cash': 'Cash',
    'cash sale': 'Cash',
}

# Background work (auto-reconcile, daily backup, embedded maintenance) must run
# in a process that was started for it -- a cron/scheduled task or the local dev
# server -- never inside a web worker.  A WSGI worker that starts these threads
# copies the database from the request path, competes with the shop for the
# SQLite write lock, and lets the balance auto-healer rewrite accounts while a
# user is working.  ``wsgi.py`` deliberately does not set this flag.
_BACKGROUND_WORKER_VALUES = ('1', 'true', 'yes', 'on')
_ENABLE_BACKGROUND_WORKERS = (
    os.environ.get('AMS_ENABLE_BACKGROUND_WORKERS', '').strip().lower()
    in _BACKGROUND_WORKER_VALUES
)

_AUTO_BACKUP_ENABLED = False

_WIPE_BACKUP_ENABLED = False

_AUTO_RECONCILE_ENABLED = os.environ.get('AUTO_RECONCILE_ENABLED', '1').strip() != '0'

_AUTO_RECONCILE_FIX = os.environ.get('AUTO_RECONCILE_FIX', '1').strip() != '0'

_AUTO_RECONCILE_INTERVAL_SEC = int(os.environ.get('AUTO_RECONCILE_INTERVAL_SEC', '600') or 600)

_AUTO_RECONCILE_TOL = float(os.environ.get('AUTO_RECONCILE_TOL', '0.01') or 0.01)

# A missing database is a valid first-run state.  The application factory and
# schema bootstrap create the file, tables, and default admin account.  The
# environment switch remains available for protection against an existing
# undersized database file.
_ALLOW_EMPTY_DB = os.environ.get('ALLOW_EMPTY_DB', '1').strip() == '1'

_ALLOW_DB_DROP = os.environ.get('ALLOW_DB_DROP', '0').strip() == '1'

_DB_HEALTH_DROP_RATIO = float(os.environ.get('DB_HEALTH_DROP_RATIO', '0.8') or '0.8')

_DB_HEALTH_DROP_MIN = int(os.environ.get('DB_HEALTH_DROP_MIN', '50') or '50')

_DB_HEALTH_MIN_BYTES = int(os.environ.get('DB_HEALTH_MIN_BYTES', '4096') or '4096')

_RESET_CONTEXT = None

_WEASYPRINT_MODULE = None

DOMAIN_WIPE_REGISTRY = {
    # Order matters: children before the rows they point at.  Everything that
    # stores money state for an account has to be here, otherwise a domain wipe
    # leaves figures that look hard-coded on the next set of accounts (SQLite
    # reuses the freed account ids).
    'accounts_domain': [
        # cash-flow rows (audit -> entry -> adjustment chain)
        'CashFlowEntryAudit',
        'CashFlowEntry',
        'CashFlowReconciliationAudit',
        'CashFlowDifferenceAdjustment',
        # Loan event history references both the lender and its cash ledger row.
        'LoanMovement',
        'LoanParty',
        # ledger (references account_reconciliation, so it goes before it)
        'AccountTransaction',
        # per-account reconciliation history + daily board counts/locks
        'AccountReconciliation',
        # difference history (holds the counter-account posting links) and the
        # shift-close snapshots: account-keyed, no FK, so they must be named
        'CashDayShiftClosePosition',
        'CashDayShiftClose',
        'CashDayDifference',
        'CashDayAccountPosition',
        'CashDayLock',
        # drawer
        'FbmCashDrawerEntry',
        'FbmCashDrawerCategory',
        # Future tables should be added only here to be included in wipes
        # Example future table: 'FutureAccountAuditLog' (defined in models.py)
    ],
    # Dummy future domain to validate registry extensibility
    'audit_domain': [
        'FutureAccountAuditLog',
    ]
}

USER_PERMISSION_DEFAULTS = {
    'can_view_stock': True,
    'can_view_daily': True,
    'can_view_history': True,
    'can_import_export': False,
    'can_manage_directory': False,
    'can_view_dashboard': True,
    'can_manage_grn': True,
    'can_manage_bookings': True,
    'can_manage_payments': True,
    'can_manage_sales': True,
    'can_view_delivery_rent': True,
    'can_manage_pending_bills': True,
    'can_view_reports': True,
    'can_manage_notifications': True,
    'can_view_client_ledger': True,
    'can_view_supplier_ledger': True,
    'can_view_decision_ledger': True,
    'can_manage_clients': False,
    'can_manage_suppliers': False,
    'can_manage_materials': False,
    'can_manage_delivery_persons': False,
    'can_access_settings': False,
    'can_manage_accounts': False,
    'can_view_cash_flow': False,
}

PERMISSION_LEGACY_FALLBACKS = {
    'can_manage_grn': 'can_view_stock',
    'can_manage_bookings': 'can_view_history',
    'can_manage_payments': 'can_view_history',
    'can_manage_sales': 'can_view_history',
    'can_view_delivery_rent': 'can_view_history',
    'can_manage_pending_bills': 'can_view_history',
    'can_view_reports': 'can_view_history',
    'can_manage_notifications': 'can_view_history',
    'can_view_client_ledger': 'can_view_history',
    'can_view_supplier_ledger': 'can_view_history',
    'can_view_decision_ledger': 'can_view_history',
    'can_manage_clients': 'can_manage_directory',
    'can_manage_suppliers': 'can_manage_directory',
    'can_manage_materials': 'can_manage_directory',
    'can_manage_delivery_persons': 'can_manage_directory',
}

# "Any operational permission" — the floor for the read-only JSON surface.
# Deliberately broad (it must not lock out a working cashier or storekeeper),
# but it is no longer empty: an account whose every can_* flag is 0 owns
# nothing and can read nothing through /api/.
API_OPERATIONAL = (
    'can_view_dashboard', 'can_manage_grn', 'can_view_stock', 'can_view_daily',
    'can_view_history', 'can_manage_bookings', 'can_manage_payments', 'can_manage_sales',
    'can_view_delivery_rent', 'can_view_client_ledger', 'can_view_supplier_ledger',
    'can_view_decision_ledger', 'can_manage_pending_bills', 'can_view_reports',
    'can_manage_notifications', 'can_import_export', 'can_manage_clients',
    'can_manage_suppliers', 'can_manage_materials', 'can_manage_delivery_persons',
    'can_manage_accounts', 'can_view_cash_flow', 'can_access_settings',
)

ENDPOINT_PERMISSION_MAP = {
    # Logs section (app/blueprints/system/logs.py). Reading the audit trail is
    # a settings-level capability; the heartbeat only ever writes the caller's
    # own presence row, so it needs no extra permission.
    'system.logs_page': 'can_access_settings',
    'system.logs_index': 'can_access_settings',
    'system.logs_download': 'can_access_settings',
    'system.activity_state': 'can_access_settings',
    'logs_page': 'can_access_settings',
    'activity_log_page': 'can_access_settings',
    'login_sessions_page': 'can_access_settings',
    'index': 'can_view_dashboard',
    'grn': 'can_manage_grn',
    'accounts.grn_payments': 'can_manage_payments',
    'accounts.grn_payment_save': 'can_manage_payments',
    'accounts.grn_payment_void': 'can_manage_payments',
    'accounts.grn_obligations_api': 'can_view_cash_flow',
    'accounts.grn_expense_payments_api': 'can_view_cash_flow',
    'accounts.grn_expenses_pay_from_modal': 'can_manage_payments',
    'accounts.purchase_payables': 'can_view_cash_flow',
    'edit_grn': 'can_manage_grn',
    'export_grn': 'can_manage_grn',
    'bookings_page': 'can_manage_bookings',
    'add_booking': 'can_manage_bookings',
    'client_booking_cancel': 'can_manage_bookings',
    'client_booking_cancel_revert': 'can_manage_bookings',
    'edit_booking': 'can_manage_bookings',
    'payments_page': 'can_manage_payments',
    'add_payment': 'can_manage_payments',
    'edit_payment': 'can_manage_payments',
    'direct_sales_page': 'can_manage_sales',
    'add_direct_sale': 'can_manage_sales',
    'add_sale': 'can_manage_sales',
    'edit_direct_sale': 'can_manage_sales',
    'material_returns_page': 'can_manage_sales',
    'add_material_return': 'can_manage_sales',
    'void_transaction': 'can_manage_sales',
    'delete_bill': 'can_manage_sales',
    'view_bill': 'can_view_history',
    'download_invoice': 'can_view_history',
    'view_bill_detail': 'can_view_history',
    'dispatching': 'can_view_daily',
    'add_record': 'can_view_daily',
    'edit_entry': 'can_view_daily',
    'delete_entry': 'can_view_daily',
    'import_dispatch_data': 'can_view_daily',
    'tracking': 'can_view_history',
    'ledger_page': 'can_view_client_ledger',
    'financial_ledger': 'can_view_client_ledger',
    'financial_ledger_details': 'can_view_client_ledger',
    'client_ledger': 'can_view_client_ledger',
    'download_client_ledger': 'can_view_client_ledger',
    'download_full_client_history': 'can_view_client_ledger',
    'client_ledger_qr': 'can_manage_clients',
    'client_ledger_qr_dialog': 'can_manage_clients',

    'decision_ledger': 'can_view_decision_ledger',
    'clients': 'can_view_client_ledger',
    'add_client': 'can_manage_clients',
    'edit_client': 'can_manage_clients',
    'delete_client': 'can_manage_clients',
    'transfer_client': 'can_manage_clients',
    'reclaim_client': 'can_manage_clients',
    'client_toggle_active': 'can_manage_clients',
    'activate_all_clients': 'can_manage_clients',
    'manage_client_categories': 'can_manage_clients',
    'add_client_category': 'can_manage_clients',
    'rename_client_category': 'can_manage_clients',
    'toggle_client_category': 'can_manage_clients',
    'delete_client_category': 'can_manage_clients',
    'suppliers': 'can_view_supplier_ledger',
    'supplier_ledger': 'can_view_supplier_ledger',
    'api_supplier_balance': 'can_view_supplier_ledger',
    'add_supplier': 'can_manage_suppliers',
    'edit_supplier': 'can_manage_suppliers',
    'delete_supplier': 'can_manage_suppliers',
    'hard_delete_supplier': 'can_manage_suppliers',
    'transfer_supplier': 'can_manage_suppliers',
    'shift_grn_supplier': 'can_manage_suppliers',
    'supplier_ledger_shift_page': 'can_manage_suppliers',
    'shift_supplier_ledger': 'can_manage_suppliers',
    'shift_supplier_payment': 'can_manage_suppliers',
    'reclaim_supplier_ledger': 'can_manage_suppliers',
    'api_supplier_ledger_shift_preview': 'can_manage_suppliers',
    'add_supplier_payment': 'can_manage_suppliers',
    'edit_supplier_payment': 'can_manage_suppliers',
    'delete_supplier_payment': 'can_manage_suppliers',
    'delivery_rents_page': 'can_view_delivery_rent',
    'delivery_rent_group': 'can_view_delivery_rent',
    'void_delivery_rent': 'can_manage_sales',
    'pay_delivery_rent': 'can_manage_sales',
    'pay_delivery_person_rent': 'can_manage_sales',
    'delivery_persons_page': 'can_manage_delivery_persons',
    'add_delivery_person': 'can_manage_delivery_persons',
    'toggle_delivery_person': 'can_manage_delivery_persons',
    'edit_delivery_person': 'can_manage_delivery_persons',
    'materials': 'can_manage_materials',
    'api_material_next_code': 'can_manage_materials',
    # AMS-2026-1119-02: the read-only JSON surface had NO permission entries at
    # all, so a signed-in account with every flag at zero could read client and
    # supplier lists, last-sold prices, GRN consumption, bill lookups and the
    # ledger revision counter.  Each endpoint is now tied to the module whose
    # data it exposes (a tuple = any one of them is enough).
    'api_clients_search': API_OPERATIONAL,
    'api.api_clients_search': API_OPERATIONAL,
    'api_suppliers_search': API_OPERATIONAL,
    'api.api_suppliers_search': API_OPERATIONAL,
    'api_last_sold_price': ('can_manage_sales', 'can_manage_bookings', 'can_view_stock'),
    'api.api_last_sold_price': ('can_manage_sales', 'can_manage_bookings', 'can_view_stock'),
    'api_grn_consumption': ('can_manage_grn', 'can_view_stock', 'can_view_daily'),
    'api.api_grn_consumption': ('can_manage_grn', 'can_view_stock', 'can_view_daily'),
    'api_ledger_revision': API_OPERATIONAL,
    'api.api_ledger_revision': API_OPERATIONAL,
    'api_client_next_code': ('can_manage_clients', 'can_manage_sales', 'can_manage_bookings'),
    'api.api_client_next_code': ('can_manage_clients', 'can_manage_sales', 'can_manage_bookings'),
    'check_bill_api': ('can_manage_sales', 'can_manage_bookings', 'can_manage_pending_bills',
                       'can_view_history', 'can_view_reports'),
    'api.check_bill_api': ('can_manage_sales', 'can_manage_bookings', 'can_manage_pending_bills',
                           'can_view_history', 'can_view_reports'),
    'api_client_booking_status': ('can_manage_bookings', 'can_manage_sales'),
    'api.api_client_booking_status': ('can_manage_bookings', 'can_manage_sales'),
    'api_client_financial_summary': ('can_view_client_ledger', 'can_manage_payments', 'can_view_reports'),
    'api.api_client_financial_summary': ('can_view_client_ledger', 'can_manage_payments', 'can_view_reports'),
    'api_supplier_balance': ('can_view_supplier_ledger', 'can_manage_payments', 'can_manage_grn'),
    'api.api_supplier_balance': ('can_view_supplier_ledger', 'can_manage_payments', 'can_manage_grn'),
    'api_supplier_ledger_shift_preview': ('can_view_supplier_ledger', 'can_manage_payments', 'can_manage_grn'),
    'masters.api_supplier_ledger_shift_preview': ('can_view_supplier_ledger', 'can_manage_payments',
                                                  'can_manage_grn'),
    'delivery_person_search_api': ('can_view_delivery_rent', 'can_manage_bookings', 'can_manage_payments'),
    'ledgers.delivery_person_search_api': ('can_view_delivery_rent', 'can_manage_bookings',
                                           'can_manage_payments'),
    'delivery_person_ledger_api': ('can_view_delivery_rent', 'can_manage_payments'),
    'ledgers.delivery_person_ledger_api': ('can_view_delivery_rent', 'can_manage_payments'),
    'api_notifications_due': ('can_manage_notifications', 'can_view_dashboard'),
    'api.api_notifications_due': ('can_manage_notifications', 'can_view_dashboard'),
    'api_notifications_due_ack': ('can_manage_notifications', 'can_view_dashboard'),
    'api.api_notifications_due_ack': ('can_manage_notifications', 'can_view_dashboard'),
    'api_notifications_contact_history': ('can_manage_notifications', 'can_view_client_ledger'),
    'api.api_notifications_contact_history': ('can_manage_notifications', 'can_view_client_ledger'),
    'current_payables_api': ('can_view_cash_flow', 'can_view_reports'),
    'reports.current_payables_api': ('can_view_cash_flow', 'can_view_reports'),
    'current_payable_detail_api': ('can_view_cash_flow', 'can_view_reports'),
    'reports.current_payable_detail_api': ('can_view_cash_flow', 'can_view_reports'),
    'api_financial_integrity_audit': ('can_access_settings', 'can_view_reports'),
    'api.api_financial_integrity_audit': ('can_access_settings', 'can_view_reports'),
    'merge_materials': 'can_manage_materials',
    'add_material': 'can_manage_materials',
    'edit_material': 'can_manage_materials',
    'bulk_update_material_unit': 'can_manage_materials',
    'add_material_category': 'can_manage_materials',
    'rename_material_category': 'can_manage_materials',
    'toggle_material_category': 'can_manage_materials',
    'delete_material': 'can_manage_materials',
    'activate_all_materials': 'can_manage_materials',
    # Services & Charges is the charge half of the same master file.  These
    # routes had no permission mapping at all, so a standard user whose
    # can_manage_materials is False could still add, rename (which cascades
    # into historical sales / bookings / entries), merge and delete services
    # while every material route correctly refused.  Gate them identically.
    'services': 'can_manage_materials',
    'add_service': 'can_manage_materials',
    'edit_service': 'can_manage_materials',
    'toggle_service': 'can_manage_materials',
    'delete_service': 'can_manage_materials',
    'merge_services': 'can_manage_materials',
    'migrate_rent_to_services': 'can_manage_materials',
    'pending_bills': 'can_manage_pending_bills',
    'add_pending_bill': 'can_manage_pending_bills',
    'edit_pending_bill': 'can_manage_pending_bills',
    'delete_pending_bill': 'can_manage_pending_bills',
    'toggle_bill_paid': 'can_manage_pending_bills',
    'export_pending_bills': 'can_manage_pending_bills',
    'import_pending_bills': 'can_manage_pending_bills',
    'unpaid_transactions_page': 'can_view_reports',
    'export_unpaid_transactions': 'can_view_reports',
    'financial_details': 'can_view_reports',
    'profit_reports': 'can_view_reports',
    # The client 360 summary exposes a client's dues and payment history as
    # JSON; the same data is gated on the ledger page, so gate it here too.
    'api_client_financial_summary': 'can_view_client_ledger',
    'mixed_transactions': 'can_view_reports',
    'notifications_page': 'can_manage_notifications',
    'notifications_upcoming': 'can_manage_notifications',
    'notifications_add_email': 'can_manage_notifications',
    'notifications_toggle_email': 'can_manage_notifications',
    'notifications_delete_email': 'can_manage_notifications',
    'notifications_set_reminder': 'can_manage_notifications',
    'notifications_log_contact': 'can_manage_notifications',
    'notifications_close_reminder': 'can_manage_notifications',
    'notifications_set_severity': 'can_manage_notifications',
    'notifications_bill_detail': 'can_manage_notifications',
    'notifications_mark_paid': 'can_manage_notifications',
    'api_notifications_contact_history': 'can_manage_notifications',
    'notifications_resolve_due': 'can_manage_notifications',
    'notifications_ack_reminder': 'can_manage_notifications',
    'api_notifications_due': 'can_manage_notifications',
    'settings': 'can_access_settings',
    'activity_log_page': 'can_access_settings',
    'change_password': 'can_access_settings',
    'sidebar_settings_page': 'can_access_settings',
    'sidebar_settings_save': 'can_access_settings',
    'sidebar_settings_reset': 'can_access_settings',
    'data_lab.upload': 'can_import_export',
    'data_lab.view_basket': 'can_import_export',
    'data_lab.correct_bill': 'can_import_export',
    'data_lab.legacy_import': 'can_import_export',
    'import_export.import_export_page': 'can_import_export',
    'import_export.get_template': 'can_import_export',
    'import_export.preview_import': 'can_import_export',
    'import_export.execute_import': 'can_import_export',
    'import_export.full_raw_export': 'can_import_export',
    'import_export.full_raw_import': 'can_import_export',
    'import_export.export_data': 'can_import_export',
    'import_export.email_file': 'can_import_export',
    'import_export.export_master': 'can_import_export',
    'import_export.export_excel_all': 'can_import_export',
    'import_export.import_master': 'can_import_export',
    'import_export.master_import_start': 'can_import_export',
    'import_export.master_import_status': 'can_import_export',
    'inventory.stock_summary': 'can_view_stock',
    'inventory.stock_summary_save_physical': 'can_view_stock',
    'inventory.lost_ledger': 'can_view_stock',
    'inventory.lost_ledger_add': 'can_view_stock',
    'inventory.lost_ledger_edit': 'can_view_stock',
    'inventory.lost_ledger_delete': 'can_view_stock',
    'inventory.lost_ledger_export': 'can_view_stock',
    'inventory.lost_ledger_material_info': 'can_view_stock',
    'inventory.daily_transactions': 'can_view_daily',
    'inventory.inventory_log': 'can_view_history',

    # --- Sales / Bookings / Pending Bills: draft & on-demand edit modals ---
    'booking_edit_modal': 'can_manage_bookings',
    'hold_direct_sale': 'can_manage_sales',
    'resume_direct_sale_draft': 'can_manage_sales',
    'delete_direct_sale_draft': 'can_manage_sales',
    'direct_sale_edit_modal': 'can_manage_sales',
    'pending_bill_modals': 'can_manage_pending_bills',
    'client_modals': 'can_manage_clients',
    'rename_material_label': 'can_manage_materials',

    # --- Cash Flow module (new) ---
    'cash_flow': 'can_view_cash_flow',
    'cash_flow_categories_meta': 'can_view_cash_flow',
    'cash_flow_subcategories_meta': 'can_view_cash_flow',
    'cash_flow_parties_meta': 'can_view_cash_flow',
    'cash_flow_entry_json': 'can_view_cash_flow',
    'cash_flow_differences': 'can_view_cash_flow',
    'cash_flow_reconciliation_detail': 'can_view_cash_flow',
    # Cash-day board + financial tracker live in the reports blueprint, so the
    # cash_flow map entries above never covered them.  daily_reconciliation is
    # not read-only: it saves counted balances and locks/unlocks a cash day.
    'daily_reconciliation': 'can_view_cash_flow',
    'daily_reconciliation_differences': 'can_view_cash_flow',
    'financial_tracker': 'can_view_cash_flow',
    'current_payables_api': 'can_view_cash_flow',
    'current_payable_detail_api': 'can_view_cash_flow',

    # --- Import / Export: async job engine + extended pages ---
    'import_upload_file': 'can_import_export',
    'import_list_uploads': 'can_import_export',
    'import_get_upload': 'can_import_export',
    'import_delete_upload': 'can_import_export',
    'import_start_job': 'can_import_export',
    'import_job_progress': 'can_import_export',
    'import_job_history': 'can_import_export',
    'import_browse_history': 'can_import_export',
    'import_cancel_job': 'can_import_export',
    'full_raw_import_history': 'can_import_export',
    'full_raw_import_report': 'can_import_export',
    'tenant_db_export': 'can_import_export',
    'tenant_db_restore': 'can_import_export',
    'transfer_export': 'can_import_export',
    'transfer_import': 'can_import_export',

    # --- Supplier ledger: PDF/voucher downloads + pay form ---
    'download_supplier_ledger': 'can_view_supplier_ledger',
    'download_supplier_payment': 'can_view_supplier_ledger',
    'pay_supplier_page': 'can_manage_suppliers',
    'supplier_opening_balance': 'can_manage_suppliers',
    'client_opening_balance': 'can_manage_clients',

    # --- Delivery person ledger: page + APIs + PDF (view) / payments (manage) ---
    'delivery_person_ledger': ('can_view_delivery_rent', 'can_view_client_ledger'),
    'delivery_person_ledger_api': ('can_view_delivery_rent', 'can_view_client_ledger'),
    'delivery_person_search_api': ('can_view_delivery_rent', 'can_view_client_ledger'),
    'download_delivery_person_ledger': ('can_view_delivery_rent', 'can_view_client_ledger'),
    'delivery_person_opening_balance': 'can_manage_delivery_persons',
    'settle_delivery_person': 'can_manage_sales',
    'edit_delivery_person_payment': 'can_manage_sales',
    'void_delivery_person_payment': 'can_manage_sales',

    # --- Ledger views & exports ---
    'material_ledger_page': 'can_view_history',
    'download_client_clearance': 'can_view_client_ledger',

    # --- Redirect aliases for inventory pages ---
    'daily_transactions_redirect': 'can_view_daily',
    'stock_summary_redirect': 'can_view_stock',
    'lost_ledger_redirect': 'can_view_stock',
    'export_current_payables': 'can_view_reports',

    # --- Admin diagnostics ---
    'api_financial_integrity_audit': 'can_access_settings',
}

# Blueprint-level fallback: every endpoint inside these blueprints needs the
# given permission (a tuple = any of them, OR semantics).  Covers whole
# feature packs (e.g. the Financial Accounts module, ~40 endpoints) without
# listing each route, and keeps protecting future routes in the same blueprint.
# The Accounts module keeps its historical rule: payment managers can
# transact there (enforced again inside the views), while the dedicated
# can_manage_accounts flag opens the Khata module for other roles.  Account
# MASTER operations (add/edit/delete/toggle) stay admin/root-only inside the
# views regardless of these flags.
BLUEPRINT_PERMISSION_PREFIXES = {
    'accounts': ('can_manage_accounts', 'can_manage_payments'),
    # AMS-2026-1119-02: deny-by-default floor for the JSON surface.  Routes that
    # are not listed individually in ENDPOINT_PERMISSION_MAP (i.e. any /api/
    # route added later) still require at least one operational permission
    # instead of falling through the old "unmapped = allow" path.
    'api': API_OPERATIONAL,
}

AUTO_BILL_NS_DEFAULT = 'GEN'

AUTO_BILL_NAMESPACES = {
    'BOOKING': 'BK',
    'PAYMENT': 'CP',
    'SUPPLIER_PAYMENT': 'SP',
    'DIRECT_SALE': 'SL',
    'MATERIAL_RETURN': 'RTN',
    'GRN': 'GRN',
    'GRN_PAYMENT': 'GP',
    'ENTRY': 'EN',
    'STOCK_ADJUSTMENT': 'SA',
}

EDITABLE_USER_PERMISSION_FIELDS = [
    'can_view_dashboard',
    'can_manage_grn',
    'can_view_stock',
    'can_view_daily',
    'can_view_history',
    'can_manage_bookings',
    'can_manage_payments',
    'can_manage_sales',
    'can_view_delivery_rent',
    'can_view_client_ledger',
    'can_view_supplier_ledger',
    'can_view_decision_ledger',
    'can_manage_pending_bills',
    'can_view_reports',
    'can_manage_notifications',
    'can_import_export',
    'can_manage_clients',
    'can_manage_suppliers',
    'can_manage_materials',
    'can_manage_delivery_persons',
    'can_manage_accounts',
    'can_view_cash_flow',
    'can_access_settings',
]

# Single source of truth for the Settings user-role UI: which features exist,
# grouped by module.  The permission checkboxes (add/edit user) and the
# Feature x User access matrix both render from this list.
PERMISSION_GROUPS = [
    ('Operations', [
        ('can_view_dashboard', 'Dashboard'),
        ('can_manage_grn', 'GRN (Receiving)'),
        ('can_view_stock', 'Stock Summary'),
        ('can_view_daily', 'Daily Breakdown'),
        ('can_view_history', 'History & Bills'),
    ]),
    ('Sales & Billing', [
        ('can_manage_bookings', 'Bookings'),
        ('can_manage_sales', 'Sales (Direct Sale / Returns / Void)'),
        ('can_manage_payments', 'Payments'),
        ('can_manage_pending_bills', 'Pending Bills'),
        ('can_view_delivery_rent', 'Delivery Rent + Driver Ledger'),
    ]),
    ('Ledgers & Reports', [
        ('can_view_client_ledger', 'Client Ledger / Clearance'),
        ('can_view_supplier_ledger', 'Supplier Ledger / Vouchers'),
        ('can_view_decision_ledger', 'Decision Ledger'),
        ('can_view_reports', 'Reports (Profit / Unpaid / Mixed)'),
        ('can_view_cash_flow', 'Cash Flow + Differences'),
    ]),
    ('Masters', [
        ('can_manage_clients', 'Manage Clients (Add/Edit/Delete)'),
        ('can_manage_suppliers', 'Manage Suppliers + Supplier Payments'),
        ('can_manage_materials', 'Manage Materials + Categories'),
        ('can_manage_delivery_persons', 'Manage Delivery Persons'),
    ]),
    ('Finance & System', [
        ('can_manage_accounts', 'Financial Accounts (Khata module)'),
        ('can_manage_notifications', 'Notifications / Reminders'),
        ('can_import_export', 'Import / Export Center'),
        ('can_access_settings', 'Settings Access (page + activity log)'),
    ]),
]


basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
legacy_instance_dir = os.path.join(basedir, 'instance')
os.makedirs(legacy_instance_dir, exist_ok=True)
legacy_db_path = os.path.join(legacy_instance_dir, 'ahmed_cement.db')
v44_db_path = os.path.join(legacy_instance_dir, 'ahmed_cement_v44_fresh.db')
db_path = os.environ.get('APP_DB_PATH') or v44_db_path
_DB_HEALTH_SNAPSHOT_PATH = os.environ.get('DB_HEALTH_SNAPSHOT_PATH') or os.path.join(legacy_instance_dir, 'health_snapshot.json')
_max_upload_mb = int(os.environ.get('MAX_UPLOAD_MB', '64') or '64')


