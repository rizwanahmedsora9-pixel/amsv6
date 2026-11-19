"""Dedicated two-way loan party register for principal borrowing, lending, and repayments."""
from datetime import datetime
from uuid import uuid4

from ._common import *  # noqa
from .helpers import _company_accounts, _not_legacy_loan_query, _not_system_account_query
from app.services.loan_register import (
    annotate_party_movements,
    create_loan_party,
    delete_loan_party,
    delete_principal_movement,
    record_principal_movement,
    toggle_loan_party,
    update_loan_party,
    update_principal_movement,
)


def _parse_movement_datetime(raw_value: str | None, *, fallback_dt: datetime | None = None) -> datetime:
    raw = (raw_value or '').strip()
    if not raw:
        return fallback_dt or pk_now()
    for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    try:
        parsed = datetime.strptime(raw, '%Y-%m-%d')
    except ValueError as exc:
        raise ValueError('Enter a valid movement date.') from exc
    if fallback_dt is not None and parsed.date() == fallback_dt.date():
        return fallback_dt
    return parsed


def _loan_register_redirect(filter_party_id: int | None = None):
    if filter_party_id:
        return redirect(url_for('accounts.loan_register', party_id=filter_party_id))
    return redirect(url_for('accounts.loan_register'))


@accounts_bp.route('/loans')
@login_required
def loan_register():
    parties = LoanParty.query.order_by(LoanParty.is_active.desc(), LoanParty.name.asc()).all()
    selected_party_id = request.args.get('party_id', type=int)
    if selected_party_id and not any(p.id == selected_party_id for p in parties):
        selected_party_id = None

    movements_q = LoanMovement.query
    if selected_party_id:
        movements_q = movements_q.filter(LoanMovement.loan_party_id == selected_party_id)
    movements = movements_q.order_by(LoanMovement.date_posted.desc(), LoanMovement.id.desc()).limit(300).all()

    totals = {}
    received_totals = {}
    repaid_totals = {}
    movement_counts = {}
    for party in parties:
        party_movements = party.movements.all()
        movement_counts[party.id] = len(party_movements)
        rec = sum(int(m.amount_minor or 0) for m in party_movements if m.movement_type == 'received')
        rep = sum(int(m.amount_minor or 0) for m in party_movements if m.movement_type == 'repaid')
        received_totals[party.id] = rec
        repaid_totals[party.id] = rep
        totals[party.id] = int(party.opening_principal_minor or 0) + rec - rep

    total_we_owe_minor = sum(bal for bal in totals.values() if bal > 0)
    total_owes_us_minor = sum(-bal for bal in totals.values() if bal < 0)
    net_loan_position_minor = total_we_owe_minor - total_owes_us_minor

    movement_meta = annotate_party_movements(parties)
    selected_party = next((p for p in parties if p.id == selected_party_id), None)

    # Only company cash/bank accounts can fund a loan movement. Loan parties are
    # external counterparties and never appear as either side of a company transfer.
    cash_accounts = [a for a in _company_accounts()
                     if (a.category or '').strip().lower() in ('cash', 'bank')]

    edit_cash_accounts = list(cash_accounts)
    seen_acc_ids = {a.id for a in edit_cash_accounts}
    for m in movements:
        if m.cash_account_id and m.cash_account_id not in seen_acc_ids and m.cash_account:
            edit_cash_accounts.append(m.cash_account)
            seen_acc_ids.add(m.cash_account_id)

    return render_template(
        'accounts/loan_register.html',
        parties=parties,
        selected_party=selected_party,
        movements=movements,
        movement_meta=movement_meta,
        outstanding_minor=totals,
        received_totals=received_totals,
        repaid_totals=repaid_totals,
        movement_counts=movement_counts,
        total_we_owe_minor=total_we_owe_minor,
        total_owes_us_minor=total_owes_us_minor,
        net_loan_position_minor=net_loan_position_minor,
        cash_accounts=cash_accounts,
        edit_cash_accounts=edit_cash_accounts,
        selected_party_id=selected_party_id,
        idempotency_key=uuid4().hex,
        today=pk_today().strftime('%Y-%m-%d'),
    )


@accounts_bp.route('/loans/parties/add', methods=['POST'])
@login_required
def add_loan_party():
    try:
        party, created = create_loan_party(
            name=request.form.get('name') or '',
            opening_principal=request.form.get('opening_principal'),
            opening_direction=request.form.get('opening_direction'),
            opening_date=request.form.get('opening_date'),
            note=request.form.get('note') or '',
            actor=current_user,
        )
        db.session.commit()
    except (TypeError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc), 'danger')
        return redirect(url_for('accounts.loan_register'))
    except Exception:
        db.session.rollback()
        logger.exception('Loan party create failed')
        flash('Unable to add the loan party. No changes were saved.', 'danger')
        return redirect(url_for('accounts.loan_register'))

    if not created:
        flash('That loan party already exists; the register is active.', 'info')
        return redirect(url_for('accounts.loan_register'))

    audit_log(
        current_user,
        'loan.party.create',
        f'name={party.name}, opening_principal_minor={party.opening_principal_minor}',
    )
    flash('Loan party added to the register.', 'success')
    return redirect(url_for('accounts.loan_register'))


@accounts_bp.route('/loans/parties/<int:party_id>/edit', methods=['POST'])
@login_required
def edit_loan_party(party_id):
    filter_party_id = request.form.get('filter_party_id', type=int)
    if 'is_active' in request.form:
        is_active_val = (request.form.get('is_active') or '').strip().lower() in ('1', 'true', 'yes', 'on')
    elif 'is_active_submitted' in request.form:
        is_active_val = False
    else:
        is_active_val = None

    try:
        party = update_loan_party(
            loan_party_id=party_id,
            name=request.form.get('name') or '',
            opening_principal=request.form.get('opening_principal'),
            opening_direction=request.form.get('opening_direction'),
            opening_date=request.form.get('opening_date'),
            note=request.form.get('note') or '',
            is_active=is_active_val,
            actor=current_user,
        )
        db.session.commit()
        audit_log(
            current_user,
            'loan.party.update',
            f'id={party.id}, name={party.name}, opening_principal_minor={party.opening_principal_minor}, active={party.is_active}',
        )
        flash('Loan party updated.', 'success')
    except (TypeError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc) or 'Unable to update loan party.', 'danger')
    except Exception:
        db.session.rollback()
        logger.exception('Loan party update failed')
        flash('Unable to update the loan party. No changes were saved.', 'danger')
    return _loan_register_redirect(filter_party_id)


@accounts_bp.route('/loans/parties/<int:party_id>/toggle', methods=['POST'])
@login_required
def toggle_loan_party_route(party_id):
    filter_party_id = request.form.get('filter_party_id', type=int)
    try:
        party = toggle_loan_party(party_id, actor=current_user)
        db.session.commit()
        status_label = 'activated' if party.is_active else 'deactivated'
        audit_log(current_user, 'loan.party.toggle', f'id={party.id}, active={party.is_active}')
        flash(f'Loan party {status_label}.', 'success')
    except (TypeError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc) or 'Unable to update loan party status.', 'danger')
    except Exception:
        db.session.rollback()
        logger.exception('Loan party toggle failed')
        flash('Unable to change loan party status. No changes were saved.', 'danger')
    return _loan_register_redirect(filter_party_id)


@accounts_bp.route('/loans/parties/<int:party_id>/delete', methods=['POST'])
@login_required
def delete_loan_party_route(party_id):
    filter_party_id = request.form.get('filter_party_id', type=int)
    try:
        deleted = delete_loan_party(party_id, actor=current_user)
        db.session.commit()
        audit_log(current_user, 'loan.party.delete', f"id={deleted['id']}, name={deleted['name']}")
        flash('Loan party deleted from the register.', 'success')
        if filter_party_id == party_id:
            filter_party_id = None
    except (TypeError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc) or 'Unable to delete loan party.', 'danger')
    except Exception:
        db.session.rollback()
        logger.exception('Loan party delete failed')
        flash('Unable to delete the loan party. No changes were saved.', 'danger')
    return _loan_register_redirect(filter_party_id)


@accounts_bp.route('/loans/movements/add', methods=['POST'])
@login_required
def add_loan_movement():
    try:
        date_raw = (request.form.get('date_posted') or '').strip()
        posted = _parse_movement_datetime(date_raw) if date_raw else pk_now()
        movement, created = record_principal_movement(
            loan_party_id=request.form.get('loan_party_id', type=int),
            movement_type=request.form.get('movement_type'),
            amount=request.form.get('amount'),
            cash_account_id=request.form.get('cash_account_id', type=int),
            date_posted=posted,
            reference=request.form.get('reference'),
            note=request.form.get('note'),
            idempotency_key=request.form.get('idempotency_key'),
            actor=current_user,
        )
        db.session.commit()
        flash('Loan principal movement recorded.' if created else 'This loan movement was already recorded.',
              'success' if created else 'info')
    except (TypeError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc) or 'Unable to record this loan movement.', 'danger')
    except Exception:
        db.session.rollback()
        logger.exception('Loan register movement failed')
        flash('Unable to record the loan movement. No balance was changed.', 'danger')
    return redirect(url_for('accounts.loan_register'))


@accounts_bp.route('/loans/movements/<int:movement_id>/edit', methods=['POST'])
@login_required
def edit_loan_movement(movement_id):
    filter_party_id = request.form.get('filter_party_id', type=int)
    existing = db.session.get(LoanMovement, movement_id)
    if existing is None:
        flash('Loan movement not found.', 'danger')
        return _loan_register_redirect(filter_party_id)
    try:
        posted = _parse_movement_datetime(
            request.form.get('date_posted'),
            fallback_dt=existing.date_posted,
        )
        movement = update_principal_movement(
            movement_id=movement_id,
            loan_party_id=request.form.get('loan_party_id', type=int),
            movement_type=request.form.get('movement_type'),
            amount=request.form.get('amount'),
            cash_account_id=request.form.get('cash_account_id', type=int),
            date_posted=posted,
            reference=request.form.get('reference'),
            note=request.form.get('note'),
            actor=current_user,
        )
        db.session.commit()
        audit_log(
            current_user,
            'loan.movement.update',
            f'id={movement.id}, party_id={movement.loan_party_id}, '
            f'type={movement.movement_type}, amount_minor={movement.amount_minor}',
        )
        flash('Loan principal movement updated.', 'success')
    except (TypeError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc) or 'Unable to update this loan movement.', 'danger')
    except Exception:
        db.session.rollback()
        logger.exception('Loan register movement edit failed')
        flash('Unable to update the loan movement. No balance was changed.', 'danger')
    return _loan_register_redirect(filter_party_id)


@accounts_bp.route('/loans/movements/<int:movement_id>/delete', methods=['POST'])
@login_required
def delete_loan_movement(movement_id):
    filter_party_id = request.form.get('filter_party_id', type=int)
    try:
        deleted = delete_principal_movement(
            movement_id=movement_id,
            reason=request.form.get('reason') or '',
            actor=current_user,
        )
        db.session.commit()
        audit_log(
            current_user,
            'loan.movement.delete',
            f"id={deleted['id']}, party_id={deleted['loan_party_id']}, "
            f"type={deleted['movement_type']}, amount_minor={deleted['amount_minor']}",
        )
        flash('Loan principal movement deleted and cash/bank balance reversed.', 'success')
    except (TypeError, ValueError) as exc:
        db.session.rollback()
        flash(str(exc) or 'Unable to delete this loan movement.', 'danger')
    except Exception:
        db.session.rollback()
        logger.exception('Loan register movement delete failed')
        flash('Unable to delete the loan movement. No balance was changed.', 'danger')
    return _loan_register_redirect(filter_party_id)
