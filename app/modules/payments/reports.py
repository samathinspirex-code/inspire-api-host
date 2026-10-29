from datetime import date, timedelta

from app.core.errors import NotFoundError
from . import billing, core, ledger
from .db import one, rows


async def contract_split(c, end):
    return await one(c, '''SELECT COALESCE(SUM(b) FILTER(WHERE b>0),0) AS liability,COALESCE(-SUM(b) FILTER(WHERE b<0),0) AS asset
        FROM(SELECT l.enrolment_id,SUM(l.credit-l.debit) AS b FROM journal_lines l JOIN journals j ON j.id=l.journal_id
        WHERE l.account_code='2100' AND j.entry_date <= $1 GROUP BY l.enrolment_id) x''', end)


async def open_items(c,end,ap=False):
    sql = ("""SELECT b.number AS ref,s.name,b.due_date,b.amount-COALESCE((SELECT SUM(l.debit-l.credit) FROM journal_lines l
           JOIN journals j ON j.id=l.journal_id WHERE j.source='bills' AND j.source_ref=b.number AND l.account_code='2010' AND j.entry_date <= $1 AND l.debit>0),0) AS amount
           FROM bills b JOIN suppliers s ON s.id=b.supplier_id WHERE b.status<>'cancelled' AND b.bill_date <= $1""" if ap else
           """SELECT i.number AS ref,l.first_name||' '||l.last_name AS name,i.due_date,
           i.amount-LEAST(i.amount,COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.invoice_id=i.id AND p.received_date <= $1),0)) AS amount
           FROM invoices i JOIN learners l ON l.id=i.learner_id LEFT JOIN enrolments e ON e.id=i.enrolment_id
           WHERE i.issue_date <= $1 AND (i.status<>'cancelled' OR COALESCE(i.cancelled_on,e.withdrawn_on) > $1)""")
    return [dict(r,days_overdue=core.days(r['due_date'],end)) for r in await rows(c,sql,end)]


async def report(c,kind,q):
    end=core.day(q.get('as_of') or q.get('to') or core.today())
    if kind in {'ar-ageing','ap-ageing'}: return core.ageing(await open_items(c,end,kind=='ap-ageing'),'Payables ageing' if kind=='ap-ageing' else 'Receivables ageing','As at '+end)
    if kind in {'balance-sheet','trial-balance'}:
        accs=await ledger.account_balances(c,end)
        totals={a['code']:dict(debit=a['debit'],credit=a['credit']) for a in accs}
        if kind=='trial-balance': return core.trial_balance(accs,totals,'As at '+end)
        return core.balance_sheet(accs,totals,await contract_split(c,end),'As at '+end)
    start=core.day(q.get('from') or core.fiscal_year(end,(await billing.settings(c))['fy_start_month'])['start'])
    period=f'{start} to {end}'
    if kind=='profit-loss':
        accs=await ledger.account_balances(c,end,start)
        return core.profit_loss(accs,{a['code']:dict(debit=a['debit'],credit=a['credit']) for a in accs},period)
    if kind=='cash-flow':
        data=await one(c,"""WITH cash AS (
            SELECT j.entry_date,j.source,COALESCE(j.cash_flow_category,'other_operating') AS cat,l.debit-l.credit AS amount
            FROM journal_lines l JOIN journals j ON j.id=l.journal_id WHERE l.account_code IN ('1010','1020') AND j.entry_date <= $2)
            SELECT COALESCE(SUM(amount) FILTER(WHERE entry_date<$1 OR source='migration'),0) AS opening,
                COALESCE(SUM(amount),0) AS closing,
                (SELECT COALESCE(jsonb_agg(m),'[]') FROM (SELECT cat,SUM(amount) AS amount FROM cash
                    WHERE entry_date >= $1 AND source<>'migration' GROUP BY cat) m) AS moves FROM cash""",start,end)
        return core.cash_flow({r['cat']:r['amount'] for r in data['moves']},data['opening'],data['closing'],period)
    if kind=='general-ledger':
        code=q.get('account','1020'); a=await one(c,'SELECT * FROM accounts WHERE code=$1',code)
        if not a: raise NotFoundError('Account not found')
        before=(date.fromisoformat(start)-timedelta(days=1)).isoformat()
        opening=await ledger.balance(c,code,before); running=opening
        data=await rows(c,'''SELECT j.number,j.entry_date,j.memo,l.debit,l.credit FROM journal_lines l JOIN journals j ON j.id=l.journal_id
            WHERE l.account_code=$1 AND j.entry_date BETWEEN $2 AND $3 ORDER BY j.entry_date,j.id,l.id''',code,start,end)
        for r in data:
            running+=core.natural(a['type'],r['debit'],r['credit']); r['running_balance']=running
        return dict(account=a,opening_balance=opening,lines=data,closing_balance=running)
    if kind=='deferred-revenue':
        data=await rows(c,"SELECT j.entry_date,j.source,l.debit,l.credit FROM journal_lines l JOIN journals j ON j.id=l.journal_id WHERE l.account_code='2100' AND j.entry_date <= $1",end)
        opening=sum((r['credit']-r['debit'] for r in data if r['entry_date']<start),core.ZERO)
        movement=[r for r in data if r['entry_date']>=start]
        billed=sum((r['credit'] for r in movement if r['source']=='billing'),core.ZERO)
        recognised=sum((r['debit'] for r in movement if r['source'] in {'close','year_end'}),core.ZERO)
        net=sum((r['credit']-r['debit'] for r in movement),core.ZERO)
        programmes=await rows(c,"""SELECT p.name,SUM(l.credit-l.debit) AS balance FROM journal_lines l JOIN journals j ON j.id=l.journal_id
            JOIN enrolments e ON e.id=l.enrolment_id JOIN programmes p ON p.code=e.programme_code
            WHERE l.account_code='2100' AND j.entry_date <= $1 GROUP BY p.name ORDER BY p.name""",end)
        return dict(kind='deferred_revenue',title='Deferred tuition revenue',period=period,sections=[dict(heading='Roll-forward',lines=[
            dict(label='Opening balance',amount=opening),dict(label='Add: tuition invoiced',amount=billed),dict(label='Less: revenue recognised',amount=-recognised),
            dict(label='Withdrawals, refunds & other',amount=net-billed+recognised)],total=None),dict(heading='Closing balance by programme',lines=[dict(label=r['name'],amount=r['balance']) for r in programmes],total=None)],grand=[dict(label='Closing deferred tuition revenue (net)',amount=opening+net)])
    raise NotFoundError('Report not found')


async def dashboard(c,as_of=None):
    from .closing import TASKS
    end=core.day(as_of or core.today()); s=await billing.settings(c); fy=core.fiscal_year(end,s['fy_start_month'])
    data=await rows(c,"""SELECT a.*,COALESCE(SUM(l.debit) FILTER(WHERE j.entry_date <= $1),0) AS debit,
        COALESCE(SUM(l.credit) FILTER(WHERE j.entry_date <= $1),0) AS credit,
        COALESCE(SUM(l.debit) FILTER(WHERE j.entry_date BETWEEN $2 AND $1),0) AS year_debit,
        COALESCE(SUM(l.credit) FILTER(WHERE j.entry_date BETWEEN $2 AND $1),0) AS year_credit
        FROM accounts a LEFT JOIN journal_lines l ON l.account_code=a.code LEFT JOIN journals j ON j.id=l.journal_id
        GROUP BY a.code ORDER BY a.code""",end,fy['start'])
    pl=core.profit_loss(data,{a['code']:dict(debit=a['year_debit'],credit=a['year_credit']) for a in data})
    bal={a['code']:core.natural(a['type'],a['debit'],a['credit']) for a in data}
    ls=await billing.learners(c,end); overdue=[l for l in ls if l['days_overdue']>0]
    summary=await one(c,"""SELECT
        (SELECT COUNT(*) FROM payment_links WHERE status='sent') AS open_links,
        (SELECT COUNT(*) FROM payment_links WHERE status='sent' AND due_date<$1) AS overdue_links,
        (SELECT COUNT(*) FROM bank_statement_lines WHERE status='unmatched') AS unmatched_bank_lines,
        COALESCE((SELECT status FROM fiscal_periods WHERE period_start=$2),'open') AS period_status,
        (SELECT COUNT(*) FROM close_tasks WHERE period_start=$2 AND done) AS tasks_done,
        (EXISTS(SELECT 1 FROM enrolments WHERE status='active' AND start_date <= $1)
            AND NOT EXISTS(SELECT 1 FROM revenue_recognition WHERE period_start=$2)) AS revenue_pending,
        (SELECT COALESCE(SUM(b) FILTER(WHERE b>0),0) FROM (
            SELECT SUM(l.credit-l.debit) AS b FROM journal_lines l JOIN journals j ON j.id=l.journal_id
            WHERE l.account_code='2100' AND j.entry_date <= $1 GROUP BY l.enrolment_id) contracts) AS deferred
        """,end,core.month_start(end))
    series=await rows(c,"""SELECT to_char(m.month,'YYYY-MM') AS month,
        COALESCE(SUM(l.credit) FILTER(WHERE l.account_code='1100' AND j.source IN ('payhere','receipt')),0) AS collected,
        COALESCE(SUM(l.credit-l.debit) FILTER(WHERE a.type='income'),0) AS revenue
        FROM generate_series($1::date-interval '6 months',$1::date,interval '1 month') m(month)
        LEFT JOIN journals j ON j.entry_date BETWEEN m.month::date AND LEAST((m.month+interval '1 month - 1 day')::date,$2::date) AND j.source<>'year_end'
        LEFT JOIN journal_lines l ON l.journal_id=j.id LEFT JOIN accounts a ON a.code=l.account_code
        GROUP BY m.month ORDER BY m.month""",core.month_start(end),end)
    return dict(as_of=end,fiscal_year=fy,cash=bal['1010']+bal['1020'],payhere_clearing=bal['1030'],receivables=bal['1100'],allowance=bal['1150'],
        deferred_revenue=summary['deferred'],revenue_ytd=pl['figures']['revenue'],net_result_ytd=pl['figures']['net_result'],gross_margin_pct=pl['figures']['gross_margin_pct'],
        overdue_learners=len(overdue),overdue_amount=sum((l['overdue_amount'] for l in overdue),core.ZERO),open_links=summary['open_links'],overdue_links=summary['overdue_links'],
        unmatched_bank_lines=summary['unmatched_bank_lines'],
        close=dict(period=core.month_start(end),status=summary['period_status'],done=summary['tasks_done'],total=len(TASKS)),
        revenue_recognition_pending=summary['revenue_pending'],series=series)
