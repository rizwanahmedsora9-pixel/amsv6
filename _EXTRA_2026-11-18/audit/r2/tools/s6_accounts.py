"""S6 — cash movements: transfers, operating expenses, client refunds, voids, reconciliation."""
import sys

sys.path.insert(0, "tools")
import harness as H
import r2lib as L

c = H.client()
T = L.TODAY
print("== S6 accounts / cash ==")
CLI, CODE = "R2-CLI-02", "AC02"


def total_cash():
    return L.f(H.scalar("SELECT SUM(balance) FROM account WHERE COALESCE(is_active,1)=1"))


def acct(name):
    return H.one("SELECT id, balance FROM account WHERE name=?", (name,))


def client_net(code=CODE):
    with H.app.app_context():
        from app.services.financial_ledgers import build_client_financial_ledger, Client
        cl = Client.query.filter_by(code=code).first()
        return float(build_client_financial_ledger(cl).get("closing_balance") or 0)


CASH = acct("R2 Main Cash")
BANK = acct("R2 Bank")

# ------------------------------------------------------------------ A-01 transfer
tot_b, cash_b, bank_b = total_cash(), L.cash(CASH["id"]), L.cash(BANK["id"])
r = c.post("/accounts/transfers/add", data={
    "from_account": str(CASH["id"]), "to_account": str(BANK["id"]),
    "amount": "5000", "date_posted": T, "note": "R2 cash to bank"},
    follow_redirects=True)
tot_a, cash_a, bank_a = total_cash(), L.cash(CASH["id"]), L.cash(BANK["id"])
L.check("R2-C01", "Accounts / Transfer", "/accounts/transfers/add",
        "Move 5,000 from Main Cash to the bank account",
        "cash -5,000, bank +5,000, total money unchanged",
        f"cash {cash_b}->{cash_a}; bank {bank_b}->{bank_a}; total {tot_b}->{tot_a}; "
        f"flash={H.flash_messages(r)}",
        abs((cash_b - cash_a) - 5000) < 0.01 and abs((bank_a - bank_b) - 5000) < 0.01
        and abs(tot_a - tot_b) < 0.01, "HIGH")

# ------------------------------------------------------------------ A-02 operating expense
cash_b = L.cash(CASH["id"])
r = c.post("/accounts/transactions/new", data={
    "tx_mode": "pay", "pay_target": "personal_expense", "pay_from_account_id": str(CASH["id"]),
    "amount": "3000", "target_label": "R2 shop rent November", "note": "R2 rent",
    "expense_category_id": "1", "expense_party_id": "1", "method": "Cash",
    "date_posted": T}, follow_redirects=True)
cash_a = L.cash(CASH["id"])
exp = H.q("SELECT id, transaction_type, amount, description, from_account_id FROM account_transaction "
          "ORDER BY id DESC LIMIT 2")
st, exp_txt = L.page("/accounts/expenditures")
st2, pnl = L.page("/profit_reports")
opex = L.money_in(pnl, "Operating Expenses") or L.money_in(pnl, "GRN operating")
L.check("R2-C02", "Accounts / Operating expense", "/accounts/transactions/new",
        "Record 3,000 of shop rent paid out of Main Cash with an expense category and a payee",
        "cash -3,000, an expense voucher is posted, and the money shows up as an operating expense "
        "rather than as goods cost",
        f"cash {cash_b}->{cash_a}; vouchers={exp}; expenditures page has 3,000={'3,000' in exp_txt}; "
        f"P&L operating expense figure={opex}; flash={H.flash_messages(r)}",
        abs((cash_b - cash_a) - 3000) < 0.01 and any(x["transaction_type"] == "Expense" for x in exp),
        "HIGH")

# ------------------------------------------------------------------ A-03 expense without a payee
cash_b = L.cash(CASH["id"])
r = c.post("/accounts/transactions/new", data={
    "tx_mode": "pay", "pay_target": "personal_expense", "pay_from_account_id": str(CASH["id"]),
    "amount": "700", "target_label": "R2 expense without payee", "expense_category_id": "1",
    "method": "Cash", "date_posted": T}, follow_redirects=True)
cash_a = L.cash(CASH["id"])
L.check("R2-C03", "Accounts / Operating expense", "/accounts/transactions/new",
        "Post an expense with a category but no payee name",
        "refused — an expense must say who was paid (require_party)",
        f"cash {cash_b}->{cash_a}; flash={H.flash_messages(r)}", abs(cash_b - cash_a) < 0.01, "MEDIUM")

# ------------------------------------------------------------------ A-04 client refund of an advance
net_b, cash_b = client_net(), L.cash(CASH["id"])
r = c.post("/accounts/transactions/new", data={
    "tx_mode": "pay", "pay_target": "client_refund", "pay_from_account_id": str(CASH["id"]),
    "client_id_refund": H.scalar("SELECT id FROM client WHERE code='AC02'"),
    "client_input_refund": CODE, "amount": "2000", "method": "Cash",
    "date_posted": T, "note": "R2 refund part of AC02 advance"}, follow_redirects=True)
net_a, cash_a = client_net(), L.cash(CASH["id"])
L.check("R2-C04", "Accounts / Client refund", "/accounts/transactions/new",
        "Refund 2,000 of AC02's 11,200 advance in cash",
        "cash -2,000 and the client's credit shrinks by exactly 2,000",
        f"cash {cash_b}->{cash_a}; AC02 net {net_b}->{net_a}; flash={H.flash_messages(r)}",
        abs((cash_b - cash_a) - 2000) < 0.01 and abs((net_a - net_b) - 2000) < 0.01, "HIGH")

# ------------------------------------------------------------------ A-05 transfer bigger than the balance
cash_b = L.cash(CASH["id"])
r = c.post("/accounts/transfers/add", data={
    "from_account": str(CASH["id"]), "to_account": str(BANK["id"]),
    "amount": "9999999", "date_posted": T, "note": "R2 impossible transfer"},
    follow_redirects=True)
cash_a = L.cash(CASH["id"])
L.check("R2-C05", "Accounts / Transfer", "/accounts/transfers/add",
        "Try to transfer 9,999,999 out of a Main Cash account that holds far less",
        "refused, no negative cash balance",
        f"cash {cash_b}->{cash_a}; flash={H.flash_messages(r)}", abs(cash_b - cash_a) < 0.01, "HIGH")

# ------------------------------------------------------------------ A-06 void an account transaction
tx = H.one("SELECT id, amount, transaction_type, from_account_id FROM account_transaction "
           "WHERE transaction_type='Expense' ORDER BY id DESC LIMIT 1")
cash_b = L.cash(tx["from_account_id"]) if tx else 0
r = c.post(f"/accounts/transactions/{tx['id']}/void", data={"reason": "rent posted twice"},
           follow_redirects=True) if tx else None
tx2 = H.one("SELECT id, amount, is_void FROM account_transaction WHERE id=?", (tx["id"],)) if tx else None
cash_a = L.cash(tx["from_account_id"]) if tx else 0
L.check("R2-C06", "Accounts / Void", f"/accounts/transactions/{tx['id'] if tx else 0}/void",
        "Void the 700 rent voucher (or the 3,000 one) and check the cash comes back",
        "cash restored by the voucher amount and the voucher kept as void (not erased)",
        f"voucher={tx} -> {tx2}; cash {cash_b}->{cash_a}; flash={H.flash_messages(r) if r else 'n/a'}",
        tx2 and tx2["is_void"] == 1 and abs((cash_a - cash_b) - L.f(tx["amount"])) < 0.01, "HIGH")

# ------------------------------------------------------------------ A-07 edit an account transaction
# C-06 now voids the 3,000 voucher (and keeps it), so the edit probe posts its
# own 3,000 rent voucher and corrects that one.  The amount, the target and the
# expected cash delta are all part of this step, not leftovers from the void.
cash_b = L.cash(CASH["id"])
c.post("/accounts/transactions/new", data={
    "tx_mode": "pay", "pay_target": "personal_expense", "pay_from_account_id": str(CASH["id"]),
    "amount": "3000", "target_label": "R2 shop rent November (to be corrected)", "note": "R2 rent correction probe",
    "expense_category_id": "1", "expense_party_id": "1", "method": "Cash",
    "date_posted": T}, follow_redirects=True)
tx3 = H.one("SELECT id, amount, transaction_type, from_account_id FROM account_transaction "
            "WHERE transaction_type='Expense' AND COALESCE(is_void,0)=0 ORDER BY id DESC LIMIT 1")
cash_b = L.cash(tx3["from_account_id"]) if tx3 else cash_b
r = c.post(f"/accounts/transactions/{tx3['id']}/edit", data={
    "pay_from_account_id": str(tx3["from_account_id"]), "amount": "2500",
    "target_label": "R2 shop rent November (corrected)", "expense_category_id": "1",
    "expense_party_id": "1", "method": "Cash", "date_posted": T,
    "note": "R2 rent corrected to 2500"}, follow_redirects=True) if tx3 else None
tx4 = H.one("SELECT id, amount, transaction_type FROM account_transaction WHERE id=?", (tx3["id"],)) if tx3 else None
cash_a = L.cash(tx3["from_account_id"]) if tx3 else 0
L.check("R2-C07", "Accounts / Edit", f"/accounts/transactions/{tx3['id'] if tx3 else 0}/edit",
        "Correct the rent voucher from 3,000 to 2,500",
        "the voucher becomes 2,500 and cash is corrected by the 500 difference (not by 2,500 again)",
        f"voucher {tx3} -> {tx4}; cash {cash_b}->{cash_a}; flash={H.flash_messages(r) if r else 'n/a'}",
        tx4 and abs(float(tx4["amount"]) - 2500) < 0.01
        and abs((cash_a - cash_b) - 500) < 0.01, "HIGH")

# ------------------------------------------------------------------ A-08 account balances vs transactions
accounts = H.q("SELECT id, name, opening_balance, balance FROM account WHERE COALESCE(is_active,1)=1 ORDER BY id")
rows = []
for a in accounts:
    inflow = L.f(H.scalar("SELECT SUM(amount) FROM account_transaction WHERE to_account_id=? AND COALESCE(is_void,0)=0",
                          (a["id"],)))
    outflow = L.f(H.scalar("SELECT SUM(amount) FROM account_transaction WHERE from_account_id=? AND COALESCE(is_void,0)=0",
                           (a["id"],)))
    expected = L.f(a["opening_balance"]) + inflow - outflow
    rows.append((a["name"], L.f(a["balance"]), expected, round(L.f(a["balance"]) - expected, 2)))
st_or, orphan = L.page("/accounts/transactions") if False else (200, "")
L.check("R2-C08", "Accounts / Integrity", "account + account_transaction",
        "Rebuild every account balance from its opening balance plus/minus every non-void transaction",
        "each stored balance equals the ledger arithmetic",
        f"{rows}", all(abs(x[3]) < 0.01 for x in rows), "CRITICAL",
        evidence="account.opening_balance + SUM(in) - SUM(out) recomputed independently in SQL.")

print("S6 done")
