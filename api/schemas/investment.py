"""Investment requests keep monetary precision through decimal strings."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RecordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["buy", "sell", "deposit", "withdraw", "dividend", "fee", "exchange", "split"]
    occurred_at: str
    date_only: bool = False
    market: Literal["A", "HK", "US", "FUND", "OTHER", "CASH"] = "A"
    symbol: str = Field("", max_length=40)
    name: str = Field("", max_length=120)
    currency: Literal["CNY", "HKD", "USD"] = "CNY"
    settlement_currency: Literal["CNY", "HKD", "USD"] | None = None
    quantity: str = "0"
    price: str = "0"
    amount: str = "0"
    fee: str = "0"
    settlement_amount: str | None = None
    settlement_fee: str | None = None
    fx_rate: str | None = None
    note: str = Field("", max_length=2000)
    external_id: str | None = Field(None, max_length=200)
    estimated: bool = False

    @field_validator(
        "quantity",
        "price",
        "amount",
        "fee",
        "settlement_amount",
        "settlement_fee",
        "fx_rate",
        mode="before",
    )
    @classmethod
    def decimal_value(cls, value):
        if isinstance(value, (float, bool)):
            raise ValueError("请使用十进制字符串传输金额和数量")
        if value is None or value == "":
            return None
        if len(str(value)) > 100:
            raise ValueError("数值过长")
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("请输入有效数字") from None
        if (
            not number.is_finite()
            or number < 0
            or number > Decimal("1e18")
            or abs(number.adjusted()) > 80
        ):
            raise ValueError("金额、数量及汇率必须是有效的非负数")
        return format(number, "f")

    @field_validator("occurred_at")
    @classmethod
    def timestamp(cls, value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("请输入有效交易日期或时间") from None
        if parsed.tzinfo is None:
            raise ValueError("交易时间必须带时区")
        return parsed.isoformat()

    @field_validator("symbol")
    @classmethod
    def symbol_value(cls, value):
        import re

        value = value.strip().upper()
        if value and not re.fullmatch(r"[A-Z0-9._-]+", value):
            raise ValueError("证券代码格式无效")
        return value

    @model_validator(mode="after")
    def semantics(self):
        expected = {"A": "CNY", "FUND": "CNY", "HK": "HKD", "US": "USD"}
        if (
            self.action in {"buy", "sell", "split"}
            and self.market in expected
            and self.currency != expected[self.market]
        ):
            raise ValueError("报价币种与市场不匹配")
        if self.action in {"buy", "sell", "split", "dividend"} and not self.symbol:
            raise ValueError("请选择或填写证券代码")
        if self.action in {"buy", "sell", "split"} and Decimal(self.quantity or "0") <= 0:
            raise ValueError("成交数量或拆股比例必须大于零")
        if (
            self.action in {"buy", "sell"}
            and Decimal(self.amount or "0") <= 0
            and Decimal(self.price or "0") <= 0
        ):
            raise ValueError("请填写成交金额或价格")
        if (
            self.action in {"deposit", "withdraw", "dividend", "exchange"}
            and Decimal(self.amount or "0") <= 0
        ):
            raise ValueError("金额必须大于零")
        if self.action == "exchange":
            if not self.settlement_currency or self.currency == self.settlement_currency:
                raise ValueError("换汇需要两种不同的币种")
            if Decimal(self.settlement_amount or "0") <= 0:
                raise ValueError("请填写实际到账金额")
        if self.fx_rate is not None and Decimal(self.fx_rate) <= 0:
            raise ValueError("汇率必须大于零")
        return self


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    sheet: str
    mapping: dict[str, str] = Field(default_factory=dict)
    mode: Literal["transactions", "snapshot", "reconcile"] = "transactions"
    snapshot_date: str | None = None


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    indices: list[int] = Field(max_length=50000)


class ValuationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: Literal["OTHER"] = "OTHER"
    symbol: str = Field(min_length=1, max_length=40)
    occurred_at: str
    price: str | None = None
    total_amount: str | None = None

    @model_validator(mode="after")
    def valuation(self):
        if (self.price is None) == (self.total_amount is None):
            raise ValueError("请填写单位净值或总金额其中一项")
        value = self.price if self.price is not None else self.total_amount
        if len(str(value)) > 100:
            raise ValueError("估值数值过长")
        try:
            number = Decimal(value)
        except (InvalidOperation, TypeError):
            raise ValueError("估值格式无效") from None
        if (
            not number.is_finite()
            or number <= 0
            or number > Decimal("1e18")
            or abs(number.adjusted()) > 80
        ):
            raise ValueError("估值必须大于零")
        try:
            parsed = datetime.fromisoformat(self.occurred_at.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("估值时间格式无效") from None
        if parsed.tzinfo is None:
            raise ValueError("估值时间必须带时区")
        return self
