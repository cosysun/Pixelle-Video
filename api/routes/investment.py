"""Private investment endpoints share the application's session authentication."""

import asyncio
import inspect
from datetime import datetime, time, timezone
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request, UploadFile

from api.schemas.investment import (
    ConfirmRequest,
    PreviewRequest,
    RecordRequest,
    ValuationRequest,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


async def invoke(function, *args, **kwargs):
    if inspect.iscoroutinefunction(function):
        return await function(*args, **kwargs)
    return await asyncio.to_thread(function, *args, **kwargs)


def record_values(body):
    values = body.model_dump()
    stamp = datetime.fromisoformat(values["occurred_at"].replace("Z", "+00:00"))
    if values["date_only"]:
        stamp = datetime.combine(stamp.astimezone(SHANGHAI).date(), time(23, 59, 59), SHANGHAI)
    values["occurred_at"] = stamp.astimezone(timezone.utc).isoformat()
    if not values["settlement_currency"]:
        values["settlement_currency"] = "CNY" if values["market"] == "HK" else values["currency"]
    return values


def mount(api, service):
    def investment(request):
        return service(request).investment

    @api.get("/investment/overview")
    async def overview(request: Request):
        return await invoke(investment(request).overview)

    @api.get("/investment/positions")
    async def positions(request: Request):
        result = await invoke(investment(request).positions)
        return {"items": result} if isinstance(result, list) else result

    @api.get("/investment/curve")
    async def curve(request: Request, period: str = "month"):
        if period not in {"today", "week", "month", "quarter", "all"}:
            raise HTTPException(400, "请选择有效的曲线时间范围")
        result = await invoke(investment(request).curve, period)
        return {"points": result} if isinstance(result, list) else result

    @api.get("/investment/records")
    async def records(request: Request):
        result = await invoke(investment(request).list_records, include_voided=True)
        return {"items": result} if isinstance(result, list) else result

    @api.post("/investment/records")
    async def create_record(body: RecordRequest, request: Request):
        return await invoke(investment(request).create_record, record_values(body))

    @api.patch("/investment/records/{record_id}")
    async def edit_record(record_id: str, body: RecordRequest, request: Request):
        return await invoke(investment(request).update_record, record_id, record_values(body))

    @api.delete("/investment/records/{record_id}")
    async def void_record(record_id: str, request: Request):
        return await invoke(investment(request).void_record, record_id)

    @api.get("/investment/records/{record_id}/revisions")
    async def revisions(record_id: str, request: Request):
        result = await invoke(investment(request).repository.revisions, record_id)
        return {"items": result}

    @api.post("/investment/refresh")
    async def refresh(request: Request):
        engine = investment(request)
        running = getattr(engine, "_manual_refresh", None)
        if running is None or running.done():

            async def update():
                try:
                    await engine.refresh()
                except Exception:
                    from loguru import logger

                    logger.warning("投资行情刷新失败，将在下次采集重试")

            engine._manual_refresh = asyncio.create_task(update())
        return {"status": "refreshing"}

    @api.post("/investment/valuation")
    async def valuation(body: ValuationRequest, request: Request):
        engine = investment(request)
        if hasattr(engine, "valuation"):
            return await invoke(engine.valuation, body.model_dump())
        stamp = datetime.fromisoformat(body.occurred_at.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
        price = body.price
        if price is None:
            result = await invoke(engine.positions, stamp)
            items = result if isinstance(result, list) else result.get("items", [])
            item = next(
                (p for p in items if p["market"] == body.market and p["symbol"] == body.symbol),
                None,
            )
            if not item or Decimal(item["quantity"]) <= 0:
                raise HTTPException(400, "该日期没有持仓，无法按总金额计算净值")
            with localcontext() as context:
                context.prec = 80
                price = format(Decimal(body.total_amount) / Decimal(item["quantity"]), "f")
        await invoke(
            engine.repository.add_price,
            {
                "market": body.market,
                "symbol": body.symbol,
                "price": price,
                "previous_close": None,
                "occurred_at": stamp.isoformat(),
                "market_date": stamp.astimezone(SHANGHAI).date().isoformat(),
                "source": "manual",
                "kind": "manual",
            },
        )
        await invoke(engine.rebuild_history)
        return {"success": True}

    def importer(request):
        from pixelle_video.investment.imports import ImportService

        return ImportService(investment(request).repository)

    @api.post("/investment/imports/inspect")
    async def inspect_import(request: Request, file: UploadFile):
        try:
            raw = await file.read(10 * 1024 * 1024 + 1)
            if len(raw) > 10 * 1024 * 1024:
                raise HTTPException(413, "文件不能超过 10 MB")
            return await invoke(importer(request).inspect, raw, file.filename or "")
        finally:
            await file.close()

    @api.post("/investment/imports/preview")
    async def preview_import(body: PreviewRequest, request: Request):
        return await invoke(
            importer(request).preview,
            body.id,
            body.sheet,
            body.mapping,
            mode=body.mode,
            snapshot_date=body.snapshot_date,
        )

    @api.post("/investment/imports/confirm")
    async def confirm_import(body: ConfirmRequest, request: Request):
        result = await invoke(importer(request).confirm, body.id, body.indices)
        await invoke(investment(request).rebuild_history)
        return result
