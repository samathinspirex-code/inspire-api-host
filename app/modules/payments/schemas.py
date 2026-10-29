from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Money = Annotated[Decimal, Field(gt=0, le=100000000000, max_digits=14, decimal_places=2)]
Nonnegative = Annotated[Decimal, Field(ge=0, le=100000000000, max_digits=14, decimal_places=2)]
Text = Annotated[str, Field(min_length=1, max_length=2000)]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)

    def data(self):
        return self.model_dump(mode='json', exclude_none=True)


class Learner(Input):
    first_name: Annotated[str, Field(min_length=1, max_length=100)]
    last_name: Annotated[str, Field(min_length=1, max_length=100)]
    email: EmailStr | None = None
    phone: Annotated[str, Field(max_length=40)] | None = None
    address: Text | None = None
    city: Annotated[str, Field(max_length=100)] = 'Colombo'

    @field_validator('email', mode='before')
    @classmethod
    def empty_email(cls, value):
        return value or None


class FeeSchedule(Input):
    programme_code: Text
    start_date: date
    total_fee: Money | None = None
    instalments: Annotated[int, Field(ge=1, le=120)] | None = None
    first_due_date: date | None = None
    registration_fee: Nonnegative | None = None
    counsellor: Text | None = None


class Enrolment(FeeSchedule):
    learner_id: UUID


class EnrolLearner(Learner):
    enrolment: FeeSchedule


class LmsEnrolment(EnrolLearner):
    lms_ref: Annotated[str, Field(min_length=1,max_length=100)]


class Invoice(Input):
    learner_id: UUID
    kind: Literal['registration','late_fee','other']
    description: Text
    amount: Money
    issue_date: date | None = None
    due_date: date | None = None
    revenue_account: Annotated[str, Field(pattern=r'^\d{4}$')] = '4090'


class Receipt(Input):
    invoice_id: UUID | None = None
    learner_id: UUID | None = None
    amount: Money
    method: Literal['cash','bank_transfer'] = 'bank_transfer'
    received_date: date | None = None
    memo: Text | None = None


class Link(Input):
    invoice_id: UUID | None = None
    learner_id: UUID | None = None
    kind: Literal['registration','instalment','course','late_fee','other'] | None = None
    amount: Money | None = None
    description: Text | None = None
    due_date: date | None = None
    channel: Literal['email','whatsapp','sms'] = 'email'


class Line(Input):
    account_code: Annotated[str, Field(pattern=r'^\d{4}$')]
    debit: Nonnegative = Decimal('0')
    credit: Nonnegative = Decimal('0')
    learner_id: UUID | None = None
    enrolment_id: UUID | None = None


class Journal(Input):
    entry_date: date
    memo: Text
    lines: Annotated[list[Line], Field(min_length=2, max_length=2000)]


class Reversal(Input):
    entry_date: date
    reason: Annotated[str, Field(min_length=3, max_length=2000)]


class Withdrawal(Input):
    date: date
    refund_amount: Nonnegative = Decimal('0')
    reason: Text = 'Withdrawal'


class Supplier(Input):
    name: Text
    kind: Literal['lecturer','vendor','landlord','awarding_body','other'] = 'vendor'
    email: EmailStr | None = None
    phone: Text | None = None


class Bill(Input):
    supplier_id: UUID
    supplier_ref: Text | None = None
    bill_date: date
    due_date: date | None = None
    expense_account: Annotated[str, Field(pattern=r'^\d{4}$')]
    description: Text
    amount: Money


class BillPayment(Input):
    amount: Money | None = None
    paid_date: date | None = None
    bank_account: Literal['1010','1020'] = '1020'


class Asset(Input):
    name: Text
    cost: Money
    salvage: Nonnegative = Decimal('0')
    acquired_on: date
    useful_life_months: Annotated[int, Field(ge=1, le=1200)]
    opening_depreciation: Nonnegative = Decimal('0')


class Account(Input):
    code: Annotated[str, Field(pattern=r'^\d{4}$')]
    name: Text
    type: Literal['asset','contra_asset','liability','equity','income','cost_of_delivery','expense']
    grp: Literal['CA','NCA','CL','NCL','EQ','REV','COD','OPEX']


class AccountPatch(Input):
    name: Text | None = None
    is_active: bool | None = None


class Programme(Input):
    code: Annotated[str, Field(min_length=2, max_length=40)]
    name: Text
    revenue_account: Annotated[str, Field(pattern=r'^\d{4}$')]
    teaching_months: Annotated[int, Field(ge=1, le=1200)]
    default_fee: Money | None = None
    default_instalments: Annotated[int, Field(ge=1, le=120)] = 1


class Task(Input):
    done: bool


class Reason(Input):
    reason: Annotated[str, Field(min_length=3, max_length=2000)]


class YearEnd(Input):
    fy_end: date


class BankPost(Input):
    account_code: Annotated[str, Field(pattern=r'^\d{4}$')]
    memo: Text | None = None


class BankMatch(Input):
    journal_id: Annotated[int, Field(gt=0)]


class Settlement(Input):
    fee: Nonnegative = Decimal('0')
