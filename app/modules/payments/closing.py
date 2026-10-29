from app.core.errors import ConflictError, ValidationError
from . import billing, core, ledger, reports
from .db import audit, one, rows

TASKS=[dict(key=k,label=l,help=h,**({'run':k} if k in {'revenue','depreciation','ecl'} else {})) for k,l,h in [
    ('bank','Reconcile the operating bank account','Match each bank statement line.'),('payhere','Settle and reconcile PayHere clearing','Review unsettled receipts.'),
    ('billing','Confirm instalment billing','Invoice all instalments due this month.'),('revenue','Run tuition revenue recognition','Recognise delivered teaching.'),
    ('depreciation','Post depreciation','Review the fixed asset register.'),('ecl','Review expected credit loss','Adjust the receivables allowance.'),
    ('accruals','Post accruals','Record costs not yet billed.'),('review','Review statements','Review the balance sheet and results.')]]


async def tasks(c,period):
    ps=core.month_start(period)
    data=await one(c,"""SELECT COALESCE((SELECT status FROM fiscal_periods WHERE period_start=$1),'open') AS status,
        (SELECT COALESCE(jsonb_agg(t),'[]') FROM close_tasks t WHERE period_start=$1) AS tasks""",ps)
    done={r['task_key']:r for r in data['tasks']}
    status=data['status']
    return dict(period_start=ps,status=status,tasks=[dict(t,done=bool(done.get(t['key'],{}).get('done')),done_by=done.get(t['key'],{}).get('done_by')) for t in TASKS])


async def set_task(c,period,key,done,actor):
    if key not in {t['key'] for t in TASKS}: raise ValidationError('Unknown close task')
    await ledger.ensure_open(c,period)
    await c.execute('''INSERT INTO close_tasks(period_start,task_key,done,done_by,done_at) VALUES($1,$2,$3,$4,now())
        ON CONFLICT(period_start,task_key) DO UPDATE SET done=EXCLUDED.done,done_by=EXCLUDED.done_by,done_at=now()''',core.month_start(period),key,done,actor)
    await audit(c,actor,'close_task','period',core.month_start(period),dict(key=key,done=done))


async def run(c,period,what,actor,dry=False):
    ps=core.month_start(period); pe=core.month_end(period)
    if not dry: await ledger.ensure_open(c,ps)
    if what=='revenue':
        es=await rows(c,"""SELECT e.*,p.teaching_months,p.revenue_account,p.name AS programme,
            COALESCE((SELECT SUM(amount) FROM revenue_recognition r WHERE r.enrolment_id=e.id),0) AS recognised
            FROM enrolments e JOIN programmes p ON p.code=e.programme_code WHERE e.start_date <= $1 AND e.status<>'withdrawn'
            AND NOT EXISTS(SELECT 1 FROM revenue_recognition r WHERE r.enrolment_id=e.id AND r.period_start=$2)""",pe,ps)
        plan=[dict(e,amount=core.earned_to_date(e['total_fee'],e['teaching_months'],e['start_date'],pe)-e['recognised']) for e in es]
        plan=[e for e in plan if e['amount']>0]; total=sum((e['amount'] for e in plan),core.ZERO); by={}
        for e in plan: by[e['programme']]=by.get(e['programme'],core.ZERO)+e['amount']
        out=dict(period_start=ps,enrolments=len(plan),total=total,by_programme=by,posted=None)
        if not dry:
            if plan:
                credits={}
                for e in plan: credits[e['revenue_account']]=credits.get(e['revenue_account'],core.ZERO)+e['amount']
                j=await ledger.post(c,dict(entry_date=pe,memo='Tuition revenue recognition '+ps[:7],source='close',idempotency_key='revrec:'+ps,
                    lines=[dict(account_code='2100',debit=e['amount'],enrolment_id=e['id'],learner_id=e['learner_id']) for e in plan]+[dict(account_code=k,credit=v) for k,v in credits.items()]),actor)
                for e in plan: await c.execute('INSERT INTO revenue_recognition(enrolment_id,period_start,amount,journal_id) VALUES($1,$2,$3,$4)',e['id'],ps,e['amount'],j['id'])
                out['posted']=j['number']
            await set_task(c,ps,what,True,actor)
        return out
    if what=='depreciation':
        assets=await rows(c,'''SELECT a.*,a.opening_depreciation+COALESCE((SELECT SUM(amount) FROM depreciation_runs d WHERE d.asset_id=a.id),0) AS to_date
            FROM fixed_assets a WHERE a.acquired_on <= $1 AND (a.disposed_on IS NULL OR a.disposed_on > $1)
            AND NOT EXISTS(SELECT 1 FROM depreciation_runs d WHERE d.asset_id=a.id AND d.period_start=$2)''',pe,ps)
        plan=[dict(a,amount=min(core.money((a['cost']-a['salvage'])/a['useful_life_months']),a['cost']-a['salvage']-a['to_date'])) for a in assets]
        plan=[a for a in plan if a['amount']>0]; total=sum((a['amount'] for a in plan),core.ZERO)
        out=dict(period_start=ps,assets=len(plan),total=total,posted=None)
        if not dry:
            if plan:
                j=await ledger.post(c,dict(entry_date=pe,memo='Depreciation '+ps[:7],source='close',idempotency_key='dep:'+ps,
                    lines=[dict(account_code='6070',debit=total),dict(account_code='1510',credit=total)]),actor)
                for a in plan: await c.execute('INSERT INTO depreciation_runs(asset_id,period_start,amount,journal_id) VALUES($1,$2,$3,$4)',a['id'],ps,a['amount'],j['id'])
                out['posted']=j['number']
            await set_task(c,ps,what,True,actor)
        return out
    if what=='ecl':
        ageing=core.ageing(await reports.open_items(c,pe)); rates=(await billing.settings(c))['ecl_rates']
        target=core.money(sum((b['amount']*__import__('decimal').Decimal(str(rates.get(b['key'],0))) for b in ageing['buckets']),core.ZERO))
        current=await ledger.balance(c,'1150',pe); delta=target-current
        out=dict(period_start=ps,target=target,current=current,adjustment=delta,buckets=ageing['buckets'],posted=None)
        if not dry:
            if delta:
                # Adjustment journals may be repeated after a documented review;
                # the current allowance makes an unchanged rerun a no-op.
                j=await ledger.post(c,dict(entry_date=pe,memo='Expected credit loss review '+ps[:7],source='close',
                    lines=[dict(account_code='6080',debit=delta),dict(account_code='1150',credit=delta)] if delta>0 else [dict(account_code='1150',debit=-delta),dict(account_code='6080',credit=-delta)]),actor)
                out['posted']=j['number']
            await set_task(c,ps,what,True,actor)
        return out
    raise ValidationError('Unknown close run')


async def lock(c,period,actor):
    t=await tasks(c,period)
    if not all(x['done'] for x in t['tasks']): raise ConflictError('Finish every close checklist item first')
    await c.execute("INSERT INTO fiscal_periods(period_start,status,locked_at,locked_by) VALUES($1,'locked',now(),$2) ON CONFLICT(period_start) DO UPDATE SET status='locked',locked_at=now(),locked_by=$2",t['period_start'],actor)
    await audit(c,actor,'lock','period',t['period_start'])


async def unlock(c,period,reason,actor):
    if len(reason.strip())<3: raise ValidationError('A reason is required to reopen a period')
    await c.execute("UPDATE fiscal_periods SET status='open' WHERE period_start=$1",core.month_start(period))
    await audit(c,actor,'unlock','period',core.month_start(period),dict(reason=reason))


async def year_end(c,end,actor):
    end=core.day(end); fy=core.fiscal_year(end,(await billing.settings(c))['fy_start_month'])
    if end!=fy['end']: raise ValidationError('Year end is '+fy['end'])
    if await c.fetchval('SELECT 1 FROM journals WHERE idempotency_key=$1','yearend:'+end): raise ConflictError('Year already closed')
    bs=core.balances(await ledger.accounts(c),await ledger.totals(c,end=end)); lines=[]; result=core.ZERO
    for a in bs:
        if a['type'] not in core.PL_TYPES: continue
        d=a['debit']-a['credit']; result-=d
        if d: lines.append(dict(account_code=a['code'],**({'credit':d} if d>0 else {'debit':-d})))
    if result: lines.append(dict(account_code='3100',**({'credit':result} if result>0 else {'debit':-result})))
    if len(lines)<2: raise ConflictError('Nothing to close')
    # The final month must be open; reopening is a separate audited operation.
    j=await ledger.post(c,dict(entry_date=end,memo='Year-end close '+fy['label'],source='year_end',idempotency_key='yearend:'+end,lines=lines),actor)
    for i in range(12):
        await c.execute("INSERT INTO fiscal_periods(period_start,status,locked_at,locked_by) VALUES($1,'locked',now(),$2) ON CONFLICT(period_start) DO UPDATE SET status='locked',locked_at=now(),locked_by=$2",core.add_months(fy['start'],i),actor)
    await audit(c,actor,'year_end','fiscal_year',fy['label'])
    return dict(fiscal_year=fy['label'],result=result,journal=j['number'])
