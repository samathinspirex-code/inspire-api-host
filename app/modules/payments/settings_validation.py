import math

from app.core.errors import ValidationError
from .core import DEFAULTS, money


def validate(key,value):
    if value is None: raise ValidationError('A setting value is required')
    def integer(v,low,high):
        return isinstance(v,int) and not isinstance(v,bool) and low<=v<=high
    valid=False
    if key=='college_name': valid=isinstance(value,str) and 1<=len(value.strip())<=200
    elif key=='fy_start_month': valid=integer(value,1,12)
    elif key in {'invoice_lead_days','link_expiry_days'}: valid=integer(value,0 if key=='invoice_lead_days' else 1,365)
    elif key=='registration_fee':
        valid=not isinstance(value,bool) and money(value)>=0
    elif key=='reminders':
        valid=isinstance(value,dict) and set(value)=={'before_days','on_due','after_days'} and integer(value['before_days'],0,365) and integer(value['after_days'],0,365) and isinstance(value['on_due'],bool)
    elif key=='late_fee':
        valid=isinstance(value,dict) and set(value)=={'enabled','amount','after_days'} and isinstance(value['enabled'],bool) and money(value['amount'])>=0 and integer(value['after_days'],0,365)
    elif key=='ecl_rates':
        valid=isinstance(value,dict) and set(value)=={'current','d30','d60','d90','d90p'} and all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and 0<=v<=1 for v in value.values())
    elif key=='payment_methods':
        valid=isinstance(value,list) and bool(value) and all(v in {'payhere','bank_transfer','cash'} for v in value) and len(set(value))==len(value)
    if key not in DEFAULTS or not valid: raise ValidationError('Invalid value for setting '+key)
    return value
