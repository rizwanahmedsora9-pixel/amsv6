"""profit — split from reports.py."""
from ._common import *  # noqa

@bp.route('/financial_details')
@login_required
def financial_details():
    type_filter = request.args.get('type', 'cash')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    client_query = request.args.get('client', '').strip()
    min_price = request.args.get('min_price', type=float)
    max_price = request.args.get('max_price', type=float)

    if not start_date: start_date = pk_today().strftime('%Y-%m-%d')
    if not end_date: end_date = pk_today().strftime('%Y-%m-%d')

    # Resolve client code to name if applicable
    if client_query and (
        client_query.lower().startswith('tmpc-') or
        client_query.lower().startswith('fbmcl-') or
        client_query.lower().startswith('fbm-') or
        client_query[0].isdigit()
    ):
         c = Client.query.filter(Client.code.ilike(f'%{client_query}%')).first()
         if c:
             client_query = c.name

    transactions = []

    if type_filter == 'cash':
        # 1. Payments
        q_pay = Payment.query.filter(func.date(Payment.date_posted) >= start_date,
                                   func.date(Payment.date_posted) <= end_date, Payment.is_void == False)
        if client_query:
            q_pay = q_pay.filter(Payment.client_name.ilike(f'%{client_query}%'))
        if min_price is not None: q_pay = q_pay.filter(Payment.amount >= min_price)
        if max_price is not None: q_pay = q_pay.filter(Payment.amount <= max_price)

        for p in q_pay.all():
            # A material-return credit / waive-off is not money received; the
            # dashboard tile excludes it too (same shared rule).
            if not is_cash_receipt(p):
                continue
            transactions.append({
                'date': p.date_posted,
                'client': p.client_name,
                'amount': p.amount,
                'type': 'Payment',
                'ref': p.manual_bill_no or p.auto_bill_no or f'PAY-{p.id}',
                'note': (p.note or '').strip()
            })

        # 2. Booking Advances
        q_book = Booking.query.filter(func.date(Booking.date_posted) >= start_date,
                                    func.date(Booking.date_posted) <= end_date,
                                    Booking.paid_amount > 0, Booking.is_void == False)
        if client_query:
            q_book = q_book.filter(Booking.client_name.ilike(f'%{client_query}%'))
        if min_price is not None: q_book = q_book.filter(Booking.paid_amount >= min_price)
        if max_price is not None: q_book = q_book.filter(Booking.paid_amount <= max_price)

        for b in q_book.all():
            transactions.append({
                'date': b.date_posted,
                'client': b.client_name,
                'amount': b.paid_amount,
                'type': 'Booking Advance',
                'ref': b.manual_bill_no or b.auto_bill_no or f'BK-{b.id}',
                'note': (b.note or '').strip()
            })

        # 3. Direct Sale Cash
        q_sale = DirectSale.query.filter(func.date(DirectSale.date_posted) >= start_date,
                                       func.date(DirectSale.date_posted) <= end_date,
                                       DirectSale.paid_amount > 0, DirectSale.is_void == False)
        if client_query:
            q_sale = q_sale.filter(DirectSale.client_name.ilike(f'%{client_query}%'))
        if min_price is not None: q_sale = q_sale.filter(DirectSale.paid_amount >= min_price)
        if max_price is not None: q_sale = q_sale.filter(DirectSale.paid_amount <= max_price)

        for s in q_sale.all():
            transactions.append({
                'date': s.date_posted,
                'client': s.client_name,
                'amount': s.paid_amount,
                'type': 'Direct Sale',
                'ref': s.manual_bill_no or s.auto_bill_no or f'DS-{s.id}',
                'note': (s.note or '').strip()
            })

    elif type_filter == 'credit':
        # Pooled per client, exactly like the dashboard tile: every receivable
        # document of the period is a charge, every rupee tendered on it plus
        # every separate client receipt is money in, and what is left is
        # collectable.  Per-document netting alone would overstate the day when
        # a customer pays an old bill -- the tile would say 4,200 and this page
        # 6,200, which is the bug this replaced.
        def _client_key(name, code=None):
            """Resolve to one identity for documents and receipts alike.

            A Payment row carries no client_code, so keying on (code, name)
            would split the same customer into two pools and a receipt would
            never settle a bill.  Resolve to the client id first, then fall
            back to the trimmed lower-case name.
            """
            name = (name or '').strip()
            code = (code or '').strip()
            if code:
                found = Client.query.filter(
                    func.lower(func.trim(Client.code)) == code.lower()).first()
                if found is not None:
                    return f'id:{found.id}'
            if name:
                found = Client.query.filter(
                    func.lower(func.trim(Client.name)) == name.lower()).first()
                if found is not None:
                    return f'id:{found.id}'
            return f'name:{name.lower()}'

        docs = {}   # client key -> list of receivable documents
        money_in = {}

        # 1. Bookings: only the delivered part is collectable.
        q_book = Booking.query.filter(func.date(Booking.date_posted) >= start_date,
                                    func.date(Booking.date_posted) <= end_date,
                                    Booking.is_void == False)
        if client_query:
            q_book = q_book.filter(Booking.client_name.ilike(f'%{client_query}%'))
        for b in q_book.all():
            undelivered = booking_undelivered_value(b.client_name)
            charge = max(0.0, float(b.amount or 0) - undelivered)
            docs.setdefault(_client_key(b.client_name), []).append({
                'date': b.date_posted,
                'client': b.client_name,
                'charge': charge,
                'tendered': float(b.paid_amount or 0),
                'type': 'Booking Credit',
                'ref': b.manual_bill_no or b.auto_bill_no or f'BK-{b.id}',
                'note': (b.note or '').strip(),
            })

        # 2. Direct sales: dispatched as billed, so the unpaid part is due.
        q_sale = DirectSale.query.filter(func.date(DirectSale.date_posted) >= start_date,
                                       func.date(DirectSale.date_posted) <= end_date,
                                       DirectSale.is_void == False)
        if client_query:
            q_sale = q_sale.filter(DirectSale.client_name.ilike(f'%{client_query}%'))
        for s in q_sale.all():
            docs.setdefault(_client_key(s.client_name, getattr(s, 'client_code', None)), []).append({
                'date': s.date_posted,
                'client': s.client_name,
                'charge': float(s.amount or 0) - float(s.discount or 0),
                'tendered': float(s.paid_amount or 0),
                'type': 'Direct Sale Credit',
                'ref': s.manual_bill_no or s.auto_bill_no or f'DS-{s.id}',
                'note': (s.note or '').strip(),
            })

        # 3. Separate receipts (return credit notes are not money — B-04).
        for p in Payment.query.filter(func.date(Payment.date_posted) >= start_date,
                                      func.date(Payment.date_posted) <= end_date,
                                      Payment.is_void == False).all():
            if not is_cash_receipt(p):
                continue
            key = _client_key(p.client_name, getattr(p, 'client_code', None))
            money_in[key] = money_in.get(key, 0.0) + float(p.amount or 0)

        # Oldest bill first, the way a ledger is applied.
        for key, client_docs in docs.items():
            client_docs.sort(key=lambda d: d['date'] or 0)
            pool = money_in.get(key, 0.0)
            for doc in client_docs:
                credit = doc['charge'] - doc['tendered']
                if pool > 0 and credit > 0:
                    applied = min(pool, credit)
                    credit -= applied
                    pool -= applied
                if credit <= 0.01:
                    continue
                if min_price is not None and credit < min_price: continue
                if max_price is not None and credit > max_price: continue
                transactions.append({
                    'date': doc['date'],
                    'client': doc['client'],
                    'amount': credit,
                    'type': doc['type'],
                    'ref': doc['ref'],
                    'note': doc['note'],
                })

    transactions.sort(key=lambda x: x['date'], reverse=True)

    clients = Client.query.filter_by(is_active=True).order_by(Client.name.asc()).all()
    materials = Material.query.order_by(Material.name.asc()).all()

    return render_template('financial_details.html',
                           transactions=transactions,
                           type=type_filter,
                           start_date=start_date,
                           end_date=end_date,
                           client=client_query,
                           min_price=min_price,
                           max_price=max_price,
                           clients=clients,
                           materials=materials)

