"""Pure accounting calculations. Money stays Decimal until the API boundary."""
import calendar
import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from app.core.errors import ValidationError

ZERO = Decimal('0.00')
PL_TYPES = {'income', 'cost_of_delivery', 'expense'}
DEBIT_NORMAL = {'asset', 'cost_of_delivery', 'expense'}
CASH = {'1010', '1020'}
BUCKETS = [('current', 'Not yet due', 0), ('d30', '1–30 days', 30), ('d60', '31–60 days', 60), ('d90', '61–90 days', 90), ('d90p', '90+ days', 10**9)]
CF = {
    'learners': ('Receipts from learners', 'operating'), 'staff': ('Payments to staff & statutory bodies', 'operating'),
    'suppliers': ('Payments to lecturers & suppliers', 'operating'), 'marketing': ('Marketing', 'operating'),
    'premises': ('Rent & utilities', 'operating'), 'refunds': ('Refunds to learners', 'operating'),
    'other_operating': ('Other operating items', 'operating'), 'capex': ('Purchase of equipment', 'investing'),
    'loans': ('Directors’ loans received / (repaid)', 'financing'), 'capital': ('Capital introduced', 'financing'),
}
DEFAULTS = dict(college_name='Inspire College', fy_start_month=4, registration_fee=5000, invoice_lead_days=5,
                reminders=dict(before_days=3, on_due=True, after_days=7), late_fee=dict(enabled=False, amount=1000, after_days=7),
                link_expiry_days=30, ecl_rates=dict(current=.02, d30=.1, d60=.25, d90=.5, d90p=.9),
                payment_methods=['payhere', 'bank_transfer', 'cash'])


def money(value, positive=False):
    try:
        n = Decimal(str(value if value is not None else 0))
        if not n.is_finite() or abs(n) > Decimal('100000000000'):
            raise ValueError()
        n = n.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        if positive and n <= 0:
            raise ValueError()
        return n
    except (ValueError, InvalidOperation):
        raise ValidationError('Enter a valid amount' + (' greater than zero' if positive else ''))


def day(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (ValueError, TypeError):
        raise ValidationError('Use a valid date in YYYY-MM-DD format')


def today():
    return datetime.now(ZoneInfo('Asia/Colombo')).date().isoformat()


def month_start(d):
    return day(str(d)[:7] + '-01')


def month_end(d):
    d = date.fromisoformat(month_start(d))
    return d.replace(day=calendar.monthrange(d.year, d.month)[1]).isoformat()


def add_months(d, n):
    d = date.fromisoformat(day(d))
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1])).isoformat()


def days(a, b):
    return (date.fromisoformat(day(b)) - date.fromisoformat(day(a))).days


def split_instalments(total, n):
    total = money(total, True)
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 120:
        raise ValidationError('Instalments must be between 1 and 120')
    part = money(total / n)
    result = [part] * (n - 1) + [total - part * (n - 1)]
    if min(result) <= 0:
        raise ValidationError('The fee is too small for this many instalments')
    return result


def earned_to_date(total, months, start, end):
    count = max(0, min(months, (int(end[:4]) - int(start[:4])) * 12 + int(end[5:7]) - int(start[5:7]) + 1))
    return money(money(total) * count / months)


def fiscal_year(d, start_month=4):
    d = date.fromisoformat(day(d))
    y = d.year if d.month >= start_month else d.year - 1
    start = f'{y}-{start_month:02d}-01'
    return dict(start=start, end=month_end(add_months(start, 11)), label=f'FY {y}' + (f'/{str(y+1)[2:]}' if start_month != 1 else ''))


def natural(kind, debit=0, credit=0):
    n = money(debit) - money(credit)
    return n if kind in DEBIT_NORMAL else -n


def validate_journal(j, accounts):
    day(j.get('entry_date'))
    if not str(j.get('memo', '')).strip() or len(j['memo']) > 2000:
        raise ValidationError('A narration of at most 2000 characters is required')
    lines = j.get('lines', [])
    if not isinstance(lines, list) or not 2 <= len(lines) <= 2000:
        raise ValidationError('A journal needs 2 to 2000 lines')
    known = {a['code']: a for a in accounts}
    for line in lines:
        a = known.get(line.get('account_code'))
        d, c = money(line.get('debit', 0)), money(line.get('credit', 0))
        if not a or not a.get('is_active', True):
            raise ValidationError('Choose an active account for every line')
        if d < 0 or c < 0 or (d > 0) == (c > 0):
            raise ValidationError('Every line needs one positive debit or credit')
    if sum(money(l.get('debit', 0)) for l in lines) != sum(money(l.get('credit', 0)) for l in lines):
        raise ValidationError('Debits must equal credits')


def cash_category(lines):
    codes = [l['account_code'] for l in lines]
    if not CASH.intersection(codes):
        return None
    other = [c for c in codes if c not in CASH]
    if not other:
        return 'transfer'
    c = other[0]
    if c in {'1030', '1100', '2110'} or c.startswith('4'): return 'learners'
    if c in {'6010', '6015', '2300'}: return 'staff'
    if c == '6030': return 'marketing'
    if c in {'6040', '6050'}: return 'premises'
    if c == '2400': return 'refunds'
    if c == '1500': return 'capex'
    if c == '2500': return 'loans'
    if c == '3010': return 'capital'
    if c.startswith('5') or c in {'2010', '2200'}: return 'suppliers'
    return 'other_operating'


def balances(accounts, totals):
    return [dict(a, debit=money(totals.get(a['code'], {}).get('debit', 0)),
                 credit=money(totals.get(a['code'], {}).get('credit', 0)),
                 balance=natural(a['type'], **totals.get(a['code'], {}))) for a in accounts]


def section(heading, lines, label):
    return dict(heading=heading, lines=lines, total=dict(label=label, amount=sum((l['amount'] for l in lines), ZERO)))


def profit_loss(accounts, totals, period=''):
    rows = balances(accounts, totals)
    secs = [section(h, [dict(code=r['code'], label=r['name'], amount=r['balance']) for r in rows if r['grp'] == g], 'Total ' + h.lower())
            for g, h in [('REV', 'Revenue'), ('COD', 'Cost of delivery'), ('OPEX', 'Operating expenses')]]
    rev, cod, op = [s['total']['amount'] for s in secs]
    gross, net = rev - cod, rev - cod - op
    margin = round(float(gross / rev * 100), 1) if rev else 0
    secs.insert(2, dict(heading=None, lines=[], total=dict(label=f'Gross profit · {margin}% margin', amount=gross)))
    return dict(kind='profit_loss', title='Profit & loss', period=period, sections=secs,
                grand=[dict(label='Net result for the period', amount=net)],
                figures=dict(revenue=rev, cost_of_delivery=cod, gross_profit=gross, operating_expenses=op, net_result=net, gross_margin_pct=margin))


def balance_sheet(accounts, totals, contract, period=''):
    rows = balances(accounts, totals)
    result = sum((r['balance'] if r['type'] == 'income' else -r['balance'] for r in rows if r['type'] in PL_TYPES), ZERO)
    groups = {}
    for g in ['CA', 'NCA', 'CL', 'NCL', 'EQ']:
        lines = []
        for r in rows:
            if r['grp'] != g: continue
            amount = -r['balance'] if r['type'] == 'contra_asset' else r['balance']
            if r['code'] == '1120': amount += contract['asset']
            if r['code'] == '2100': amount = contract['liability']
            lines.append(dict(code=r['code'], label=r['name'], amount=amount))
        if g == 'EQ': lines.append(dict(code=None, label='Current year result (not yet closed)', amount=result))
        groups[g] = section({'CA': 'Current assets', 'NCA': 'Non-current assets', 'CL': 'Current liabilities', 'NCL': 'Non-current liabilities', 'EQ': 'Equity'}[g], lines, 'Total ' + g)
    total = lambda g: groups[g]['total']['amount']
    assets, liabilities, equity = total('CA') + total('NCA'), total('CL') + total('NCL'), total('EQ')
    return dict(kind='balance_sheet', title='Balance sheet', period=period, sections=list(groups.values()),
                grand=[dict(label='Total assets', amount=assets), dict(label='Total liabilities & equity', amount=liabilities + equity)],
                check=dict(ok=assets == liabilities + equity, label='Assets = liabilities + equity' if assets == liabilities + equity else 'Out of balance'),
                figures=dict(total_assets=assets, total_liabilities=liabilities, total_equity=equity, current_year_result=result))


def trial_balance(accounts, totals, period=''):
    rows = []
    for a in balances(accounts, totals):
        d = a['debit'] - a['credit']
        if a['debit'] or a['credit']: rows.append(dict(code=a['code'], label=a['name'], debit=max(d, ZERO), credit=max(-d, ZERO)))
    dr, cr = sum((r['debit'] for r in rows), ZERO), sum((r['credit'] for r in rows), ZERO)
    return dict(kind='trial_balance', title='Trial balance', period=period, rows=rows, totals=dict(debit=dr, credit=cr), check=dict(ok=dr == cr, label='Debits = credits' if dr == cr else 'Out of balance'))


def cash_flow(moves, opening, closing, period=''):
    secs = [section(s.title() + ' activities', [dict(code=k, label=v[0], amount=money(moves.get(k, 0))) for k, v in CF.items() if v[1] == s], 'Net cash from ' + s + ' activities') for s in ['operating', 'investing', 'financing']]
    change = sum((s['total']['amount'] for s in secs), ZERO)
    secs.append(dict(heading=None, lines=[dict(label='Net change in cash', amount=change), dict(label='Cash at start of period', amount=opening)], total=None))
    return dict(kind='cash_flow', title='Cash flow statement', period=period, sections=secs, grand=[dict(label='Cash at end of period', amount=opening + change)], check=dict(ok=opening + change == closing, label='Agrees to cash accounts' if opening + change == closing else 'Differs from cash accounts'))


def ageing(items, title='Receivables ageing', period=''):
    totals = {b[0]: ZERO for b in BUCKETS}
    rows = []
    for item in items:
        if money(item['amount']) <= 0: continue
        key, label, _ = next(b for b in BUCKETS if item['days_overdue'] <= b[2])
        totals[key] += money(item['amount'])
        rows.append(dict(item, bucket=key, bucket_label=label))
    return dict(kind='ageing', title=title, period=period, buckets=[dict(key=k, label=l, amount=totals[k]) for k, l, _ in BUCKETS], rows=sorted(rows, key=lambda r: -r['days_overdue']), total=sum(totals.values(), ZERO))


def parse_bank_csv(raw):
    result = []
    try:
        for n, row in enumerate(csv.reader(io.StringIO(raw.lstrip('\ufeff'))), 1):
            if not row or not any(row): continue
            if n == 1 and row[0].strip().lower() == 'date': continue
            if len(row) not in (3, 4): raise ValueError()
            d = row[0].strip()
            if '/' in d: d = datetime.strptime(d, '%d/%m/%Y').date().isoformat()
            amount = money(row[2].replace(',', '').strip())
            if amount == 0: raise ValueError()
            result.append(dict(txn_date=day(d), description=row[1].strip(), amount=amount, external_ref=row[3].strip() if len(row) == 4 else ''))
        if len(result) > 10000: raise ValueError()
    except (ValueError, csv.Error):
        raise ValidationError('Use date,description,amount,reference columns and at most 10000 rows')
    return result
