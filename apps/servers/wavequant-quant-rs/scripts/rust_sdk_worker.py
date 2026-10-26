"""Rust 的 AKShare 数据边界；只调用白名单 SDK 接口，不运行任何量化规则。"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
from typing import Any

MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 20 * 1024 * 1024


def records(frame: Any, maximum: int = 20000) -> list[dict[str, object]]:
    """DataFrame 是第三方动态边界，转换为 JSON 后由 Rust 校验领域字段。"""
    if len(frame.index) > maximum:
        raise ValueError("SDK response exceeds row limit")
    value = json.loads(frame.to_json(orient="records", date_format="iso", double_precision=15))
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise ValueError("SDK response is not a record array")
    return value


def dispatch(sdk: Any, request: dict[str, object]) -> object:
    operation = request.get("operation")
    symbol = request.get("symbol")
    if operation == "index_daily":
        asof = request.get("asof")
        if not isinstance(symbol, str) or not re.fullmatch(r"(?:sh\.000|sz\.(?:399|980)|bj\.899)\d{3}", symbol):
            raise ValueError("invalid index identity")
        if not isinstance(asof, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", asof):
            raise ValueError("index history requires an as-of date")
        frame = sdk.stock_zh_index_daily_em(symbol=symbol.replace(".", ""), start_date="19900101",
                                           end_date=asof.replace("-", ""))
        # 东财K线成交量由手统一为股；指数点位不调用个股复权接口。
        frame["volume"] = frame["volume"] * 100
        return records(frame)
    if operation in ("daily", "factors", "minutes"):
        if not isinstance(symbol, str) or not re.fullmatch(r"(?:sh|sz)\.\d{6}", symbol):
            raise ValueError("Sina SDK requires a Shanghai or Shenzhen symbol")
        symbol = symbol.replace(".", "")
    if operation == "daily":
        asof = request.get("asof")
        if not isinstance(asof, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", asof):
            raise ValueError("daily requires an as-of date")
        return records(sdk.stock_zh_a_daily(symbol=symbol, start_date="19900101", end_date=asof.replace("-", ""), adjust=""))
    if operation == "factors":
        return records(sdk.stock_zh_a_daily(symbol=symbol, adjust="hfq-factor"), 100000)
    if operation == "minutes":
        return records(sdk.stock_zh_a_minute(symbol=symbol, period="5", adjust=""))
    if operation in ("eastmoney_daily", "tencent_daily"):
        asof, start = request.get("asof"), request.get("start", "1990-01-01")
        if not isinstance(symbol, str) or not re.fullmatch(r"(?:sh|sz|bj)\.\d{6}", symbol):
            raise ValueError("invalid history symbol")
        if any(not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) for date in (asof, start)):
            raise ValueError("invalid history dates")
        if operation == "tencent_daily":
            return records(sdk.stock_zh_a_hist_tx(symbol=symbol.replace(".", ""), start_date=start,
                                                 end_date=asof, adjust=""))
        frame = sdk.stock_zh_a_hist(symbol=symbol[3:], period="daily", start_date=start.replace("-", ""),
                                   end_date=asof.replace("-", ""), adjust="")
        frame = frame.rename(columns={"日期": "date", "开盘": "open", "最高": "high", "最低": "low",
                                      "收盘": "close", "成交量": "volume", "成交额": "amount"})
        frame["volume"] = frame["volume"] * 100
        return records(frame)
    if operation == "calendar":
        return records(sdk.tool_trade_date_hist_sina(), 100000)
    if operation == "catalog":
        stocks = records(sdk.stock_info_a_code_name())
        try:
            calendar = records(sdk.tool_trade_date_hist_sina(), 100000)
        except Exception:
            # 与旧接口一样，日历失败由 Rust 明确警告并保守估算日期。
            calendar = None
        return {"stocks": stocks, "calendar": calendar}
    if operation == "limit_up":
        date = request.get("date")
        if not isinstance(date, str) or not re.fullmatch(r"\d{8}", date):
            raise ValueError("limit_up requires YYYYMMDD")
        return records(sdk.stock_zt_pool_em(date=date), 10000)
    raise ValueError("unsupported SDK operation")


def main() -> None:
    """一进程一请求，超时或超大响应由 Rust 终止并回收进程。"""
    request_id = ""
    version = "unknown"
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("SDK request exceeds limit")
        request = json.loads(raw)
        if not isinstance(request, dict) or request.get("protocol") != 1:
            raise ValueError("invalid SDK request")
        request_id = request.get("request_id", "")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{64}", request_id):
            raise ValueError("invalid SDK request identity")
        with open(os.devnull, "w", encoding="utf-8") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            import akshare

            version = str(akshare.__version__)
            data = dispatch(akshare, request)
        response = {"protocol": 1, "request_id": request_id, "sdk_version": version, "ok": True, "data": data}
    except Exception as error:
        # 不把第三方异常中的路径、代理凭据或完整响应泄露到协议。
        code = "sdk_missing" if isinstance(error, ModuleNotFoundError) else "sdk_call_failed"
        response = {"protocol": 1, "request_id": request_id, "sdk_version": version, "ok": False, "error": code,
                    "error_type": type(error).__name__}
    encoded = json.dumps(response, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_RESPONSE_BYTES:
        encoded = json.dumps({"protocol": 1, "request_id": request_id, "ok": False, "error": "response_too_large"}).encode()
    sys.stdout.buffer.write(encoded)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
