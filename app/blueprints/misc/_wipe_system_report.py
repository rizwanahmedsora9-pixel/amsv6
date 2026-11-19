"""wipe — split from misc.py."""
from ._common import *  # noqa

@bp.route('/system_report')
@login_required
def system_report():
    if current_user.role != 'admin':
        flash('Unauthorized', 'danger')
        return redirect(url_for('index'))

    report = {
        'sync_issues': [],
        'stock_issues': [],
        'unpaid_count': 0,
        'zero_amount_bills': 0
    }

    # 1. Check Dispatch vs Pending Bill Sync
    # Find dispatch entries that have a bill number but NO pending bill record
    entries = db.session.query(Entry).filter(
        Entry.type == 'OUT',
        Entry.is_void == False,
        Entry.bill_no != None,
        Entry.bill_no != ''
    ).all()

    for e in entries:
        if e.bill_no.upper() == 'CASH': continue # Skip cash entries

        # A booking dispatch is fulfilled by the booking's own bill, so a
        # "Missing Pending Bill" here is a false alarm on the app's normal flow.
        if (getattr(e, 'client_category', '') or '').strip() in ('Booking Delivery', 'Booked Return'):
            continue
        pb = PendingBill.query.filter_by(bill_no=e.bill_no, client_code=e.client_code, is_void=False).first()
        if not pb and (getattr(e, 'source_table', '') or '') == 'direct_sale' and getattr(e, 'source_id', None):
            # Linked movements are covered by their source document's bill.
            pb = PendingBill.query.filter_by(
                source_table='direct_sale', source_id=e.source_id, is_void=False
            ).first()
            if not pb:
                # A sale settled in full at the counter (cash, or any bill with
                # nothing left to collect) is not supposed to have a receivable.
                sale = DirectSale.query.get(e.source_id)
                if sale is not None:
                    discount = float(getattr(sale, 'discount', 0) or 0)
                    unpaid = float(sale.amount or 0) - discount - float(sale.paid_amount or 0)
                    if sale.is_void or unpaid <= 0.00001:
                        continue
        if (getattr(e, 'client_category', '') or '').strip() == 'Cash':
            # Cash dispatch with no receivable is normal business, not a sync issue.
            continue
        if not pb:
            report['sync_issues'].append({
                'type': 'Missing Pending Bill',
                'desc': f"Entry #{e.id}: Bill {e.bill_no} for {e.client} ({e.client_code}) is missing from Pending Bills."
            })

    # 2. Check Financial Data
    report['unpaid_count'] = PendingBill.query.filter_by(is_paid=False, is_void=False).count()

    zero_bills = PendingBill.query.filter_by(is_paid=False, is_void=False, amount=0).all()
    for zb in zero_bills:
        report['sync_issues'].append({
            'type': 'Zero Amount Bill',
            'desc': f"Bill {zb.bill_no} for {zb.client_name} has 0.00 amount."
        })
        report['zero_amount_bills'] += 1

    # 2b. Receivable completeness (AMS-2026-1119-01).
    #     A document that still owes money must have a live pending bill.  The
    #     original report only walked OUT entries and skipped the 'Cash'
    #     category, so a DELETED receivable belonging to a half-paid counter
    #     sale, or to a booking, left the page showing "no sync issues" while
    #     the money owed had vanished from Current Payables.  Recompute what
    #     each source document says it still owes and require a bill for it.
    try:
        live_bills = {}
        for pb in PendingBill.query.filter(
                PendingBill.source_table.in_(('direct_sale', 'booking')),
                PendingBill.is_void == False,  # noqa: E712
        ).all():
            live_bills.setdefault((pb.source_table, pb.source_id), []).append(pb)

        for model, source_name, label in (
            (DirectSale, 'direct_sale', 'sale'),
            (Booking, 'booking', 'booking'),
        ):
            for doc in model.query.filter(model.is_void == False).all():  # noqa: E712
                owed = (float(doc.amount or 0)
                        - float(getattr(doc, 'discount', 0) or 0)
                        - float(getattr(doc, 'paid_amount', 0) or 0))
                if owed <= 0.005:
                    continue
                if live_bills.get((source_name, doc.id)):
                    continue
                bill_ref = (getattr(doc, 'manual_bill_no', None)
                            or getattr(doc, 'auto_bill_no', None) or f"#{doc.id}")
                report['sync_issues'].append({
                    'type': f'Missing {label.title()} Receivable',
                    'desc': f"{label.title()} {bill_ref} for {doc.client_name} still owes "
                            f"{owed:,.2f} but has no pending bill. The receivable has been lost.",
                })
    except Exception:  # noqa: BLE001 - a diagnostic must never 500 the page
        pass

    # 2c. Orphan receipts (AMS-2026-1119-01): a payment attributed to a client
    #     that does not exist in the client master.  Its money is in the cash
    #     account but belongs to nobody, so the client ledger can never show it.
    try:
        known = {(c.name or '').strip().upper() for c in Client.query.all()}
        for pay in Payment.query.filter(Payment.is_void == False).all():  # noqa: E712
            name = (pay.client_name or '').strip()
            if not name or name.upper() in known:
                continue
            report['sync_issues'].append({
                'type': 'Orphan Receipt',
                'desc': f"Receipt #{pay.id} of {float(pay.amount or 0):,.2f} is booked against "
                        f"'{name}', which is not a client. It cannot appear in any ledger.",
            })
    except Exception:  # noqa: BLE001
        pass

    # 2d. Bills marked paid with no receipt behind them (AMS-2026-1119-01).
    #     Cleared bills carrying a real amount must have a non-void payment
    #     pointing at their bill number; otherwise the receivable was written
    #     off by a flag change while the cash never arrived.
    try:
        for pb in PendingBill.query.filter(
                PendingBill.is_paid == True,  # noqa: E712
                PendingBill.is_void == False,  # noqa: E712
        ).all():
            if float(pb.amount or 0) <= 0.005:
                continue  # settled by discount at the counter: nothing was owed
            received = Payment.query.filter(
                Payment.is_void == False,  # noqa: E712
                db.or_(Payment.auto_bill_no == pb.bill_no,
                       Payment.manual_bill_no == pb.bill_no),
            ).count()
            if received:
                continue
            report['sync_issues'].append({
                'type': 'Paid Without Receipt',
                'desc': f"Bill {pb.bill_no} for {pb.client_name} is marked paid "
                        f"({float(pb.amount or 0):,.2f}) but no receipt was ever recorded against it.",
            })
    except Exception:  # noqa: BLE001
        pass

    # 3. Check Stock Consistency
    materials = Material.query.all()
    for m in materials:
        total_in = db.session.query(func.sum(Entry.qty)).filter_by(material=m.name, type='IN', is_void=False).scalar() or 0
        total_out = db.session.query(func.sum(Entry.qty)).filter_by(material=m.name, type='OUT', is_void=False).scalar() or 0
        calc_total = total_in - total_out

        if abs(calc_total - (m.total or 0)) > 0.1:
            report['stock_issues'].append({
                'material': m.name,
                'db_stock': m.total,
                'calc_stock': calc_total,
                'diff': m.total - calc_total
            })

    # 4. Cross-report reconciliation: the same question must get the same answer
    #    on every screen.  These are the pairs that silently drifted apart before.
    report['recon'] = []
    try:
        from app.services.financial_ledgers import (
            build_current_payables, _client_snapshot, _build_client_financial_summary,
        )
        snapshot = _client_snapshot()
        payables_total = float(build_current_payables(status='outstanding', page=1, per_page=200,
                                                      snapshot=snapshot).get('total_outstanding') or 0)
        ledger_total = 0.0
        for client in snapshot['clients']:
            ledger = _build_client_financial_summary(client, snapshot=snapshot)
            # Compare against what the client ledger PAGE shows: its net balance
            # holds undelivered bookings outside the receivable, so the raw
            # closing balance is reduced the same way here.
            from app.services.financial_ledgers import _booking_undelivered_value
            net_shown = float(ledger.get('closing_balance') or 0) - _booking_undelivered_value(
                getattr(client, 'name', ''))
            ledger_total += max(0.0, net_shown)
        report['recon'].append({
            'label': 'Total Outstanding vs client ledgers',
            'a_label': 'Current Payables', 'a_value': round(payables_total, 2),
            'b_label': 'Client ledger net', 'b_value': round(ledger_total, 2),
        })
    except Exception:  # noqa: BLE001 - a diagnostic must not 500 the page
        pass
    try:
        cash_accounts = Account.query.filter(Account.channel == 'cash').all()
        for acct in cash_accounts:
            ledger_sum = db.session.query(func.sum(AccountTransaction.amount)).filter(
                AccountTransaction.account_id == acct.id,
                AccountTransaction.is_void == False,  # noqa: E712
            ).scalar()
            report['recon'].append({
                'label': f'Cash account "{acct.name}" balance vs its ledger',
                'a_label': 'Account balance', 'a_value': round(float(acct.balance or 0), 2),
                'b_label': 'Sum of transactions', 'b_value': round(float(ledger_sum or 0), 2),
            })
    except Exception:  # noqa: BLE001
        pass
    for row in report['recon']:
        row['diff'] = round(float(row['a_value']) - float(row['b_value']), 2)
    report['recon_issues'] = [row for row in report['recon'] if abs(row['diff']) >= 0.01]

    return render_template('system_report.html', report=report)

