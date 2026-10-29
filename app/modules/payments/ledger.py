from app.core.errors import ConflictError, NotFoundError, ValidationError
from . import core
from .db import audit, one, rows


async def accounts(c):
    return await rows(c, 'SELECT * FROM accounts ORDER BY code')


async def totals(c, start=None, end=None):
    data = await rows(c, '''SELECT l.account_code, SUM(l.debit) AS debit, SUM(l.credit) AS credit
        FROM journal_lines l JOIN journals j ON j.id=l.journal_id
        WHERE ($1::date IS NULL OR j.entry_date >= $1) AND ($2::date IS NULL OR j.entry_date <= $2)
        GROUP BY l.account_code''', start, end)
    return {r['account_code']: dict(debit=r['debit'], credit=r['credit']) for r in data}


async def balance(c, code, end=None):
    a = await one(c, '''SELECT a.type,COALESCE(SUM(l.debit) FILTER(WHERE $2::date IS NULL OR j.entry_date <= $2),0) AS debit,
        COALESCE(SUM(l.credit) FILTER(WHERE $2::date IS NULL OR j.entry_date <= $2),0) AS credit
        FROM accounts a LEFT JOIN journal_lines l ON l.account_code=a.code LEFT JOIN journals j ON j.id=l.journal_id
        WHERE a.code=$1 GROUP BY a.code,a.type''', code, end)
    if not a: raise NotFoundError('Account not found')
    return core.natural(a['type'], a['debit'], a['credit'])


async def account_balances(c, end=None, start=None):
    """Fetch the chart and cumulative balances in one database round trip."""
    return await rows(c, '''SELECT a.*,COALESCE(SUM(l.debit) FILTER(WHERE ($1::date IS NULL OR j.entry_date <= $1) AND ($2::date IS NULL OR j.entry_date >= $2)),0) AS debit,
        COALESCE(SUM(l.credit) FILTER(WHERE ($1::date IS NULL OR j.entry_date <= $1) AND ($2::date IS NULL OR j.entry_date >= $2)),0) AS credit
        FROM accounts a LEFT JOIN journal_lines l ON l.account_code=a.code LEFT JOIN journals j ON j.id=l.journal_id
        GROUP BY a.code ORDER BY a.code''', end, start)


async def ensure_open(c, d):
    if await c.fetchval("SELECT status='locked' FROM fiscal_periods WHERE period_start=$1", core.month_start(d)):
        raise ConflictError('This accounting period is locked. Reopen it or choose an open period')


async def post(c, j, actor):
    core.validate_journal(j, await accounts(c))
    await ensure_open(c, j['entry_date'])
    if j.get('idempotency_key'):
        old = await one(c, 'SELECT * FROM journals WHERE idempotency_key=$1', j['idempotency_key'])
        if old: return dict(old, duplicate=True)
    n = await c.fetchval("SELECT nextval('journal_number_seq')")
    result = await one(c, '''INSERT INTO journals(number,entry_date,memo,source,source_ref,cash_flow_category,idempotency_key,reversal_of,created_by)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *''', f'JE-{n:05d}', core.day(j['entry_date']), j['memo'].strip(),
        j.get('source', 'manual'), j.get('source_ref'), j.get('cash_flow_category', core.cash_category(j['lines'])),
        j.get('idempotency_key'), j.get('reversal_of'), actor)
    for l in j['lines']:
        await c.execute('INSERT INTO journal_lines(journal_id,account_code,debit,credit,learner_id,enrolment_id) VALUES($1,$2,$3,$4,$5,$6)',
                        result['id'], l['account_code'], core.money(l.get('debit', 0)), core.money(l.get('credit', 0)), l.get('learner_id'), l.get('enrolment_id'))
    await audit(c, actor, 'post', 'journal', result['number'], j)
    return dict(result, lines=j['lines'])


async def reverse(c, journal_id, entry_date, reason, actor):
    j = await one(c, 'SELECT * FROM journals WHERE id=$1', journal_id)
    if not j: raise NotFoundError('Journal not found')
    if j['source'] not in {'manual','migration'}:
        raise ValidationError('Correct this entry through its billing, banking or close workflow so its records stay in agreement')
    if await c.fetchval('SELECT 1 FROM journals WHERE reversal_of=$1', journal_id): raise ConflictError('Journal already reversed')
    if len(reason.strip()) < 3: raise ValidationError('Give a reason for the reversal')
    lines = await rows(c, 'SELECT * FROM journal_lines WHERE journal_id=$1 ORDER BY id', journal_id)
    return await post(c, dict(entry_date=entry_date, memo=f"Reversal of {j['number']}: {reason}", source='reversal', reversal_of=journal_id,
        cash_flow_category=j['cash_flow_category'], lines=[dict(account_code=l['account_code'], debit=l['credit'], credit=l['debit'], learner_id=l['learner_id'], enrolment_id=l['enrolment_id']) for l in lines]), actor)


async def list_journals(c, start=None, end=None, account=None, limit=200, offset=0):
    return await rows(c, '''SELECT j.*, json_agg(json_build_object('account_code',l.account_code,'account_name',a.name,'debit',l.debit,'credit',l.credit) ORDER BY l.id) AS lines
        FROM journals j JOIN journal_lines l ON l.journal_id=j.id JOIN accounts a ON a.code=l.account_code
        WHERE ($1::date IS NULL OR j.entry_date >= $1) AND ($2::date IS NULL OR j.entry_date <= $2)
          AND ($3::text IS NULL OR EXISTS(SELECT 1 FROM journal_lines x WHERE x.journal_id=j.id AND x.account_code=$3))
        GROUP BY j.id ORDER BY j.entry_date DESC,j.id DESC LIMIT $4 OFFSET $5''', start, end, account, limit, offset)
