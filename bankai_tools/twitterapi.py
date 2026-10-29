"""twitterapi.io 只读客户端。密钥只放在 x-api-key 请求头里，且只发往 HTTPS。"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any


def valid_key_format(key: str) -> bool:
    return re.fullmatch(r"[\x21-\x7e]{8,256}", key) is not None


BASE_URL = "https://api.twitterapi.io"
KEY_ENV = "TWITTERAPI_IO_KEY"
HANDLE = re.compile(r"[A-Za-z0-9_]{1,15}")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class ApiError(RuntimeError):
    """已脱敏的接口错误：消息里不含密钥。fatal 为假表示稍后重试可能成功。"""

    def __init__(self, message: str, *, status: int = 0, fatal: bool = True) -> None:
        super().__init__(message)
        self.status = status
        self.fatal = fatal


def redact(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text


def check_base_url(base_url: str) -> None:
    """密钥只发往 HTTPS；明文 HTTP 仅允许本机回环地址（测试用）。"""
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and parsed.hostname in LOOPBACK:
        return
    raise ValueError("拒绝把密钥发往非 HTTPS 地址")


def short(text: Any, limit: int = 120) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TwitterApiClient:
    def __init__(
        self,
        key: str,
        *,
        base_url: str = BASE_URL,
        opener: Callable[..., Any] | None = None,
        timeout: float = 30.0,
        interval: float = 0,
        stop: threading.Event | None = None,
    ) -> None:
        check_base_url(base_url)
        if not valid_key_format(key):
            raise ApiError("API Key 格式不对（应为 8–256 位可见 ASCII 字符，不含空格）。")
        self._key = key
        self.base_url = base_url
        self._opener = opener or urllib.request.build_opener(NoRedirect()).open
        self.timeout = timeout
        self.interval = interval
        self.stop = stop or threading.Event()
        self._last_request = float("-inf")

    def get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """一次请求，不重试；把失败分类为可重试（fatal=False）或致命。"""
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            headers={
                "x-api-key": self._key,
                "Accept": "application/json",
                "User-Agent": "bankai-tools/0.1",
            },
        )
        delay = max(0.0, self.interval - (time.monotonic() - self._last_request))
        if self.stop.wait(delay):
            raise ApiError("用户暂停；已保存页面可继续")
        self._last_request = time.monotonic()
        try:
            with self._opener(request, timeout=self.timeout) as response:
                status, raw = response.status, response.read(2_000_000)
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read(4096)
        except urllib.error.URLError, TimeoutError, OSError:
            raise ApiError("网络错误或超时", fatal=False) from None
        try:
            body = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            body = None
        detail = ""
        if isinstance(body, dict):
            detail = short(
                redact(
                    str(body.get("msg") or body.get("message") or body.get("detail") or ""),
                    self._key,
                )
            )
        if status in (429, 500, 502, 503):
            raise ApiError(f"HTTP {status}，稍后重试", status=status, fatal=False)
        if status == 402:
            raise ApiError("余额不足（HTTP 402）：到 twitterapi.io 后台充值后再试", status=status)
        if status in (401, 403):
            raise ApiError(
                f"API Key 无效或未发送（HTTP {status}）：请重新从后台复制密钥", status=status
            )
        if status == 400:
            raise ApiError(f"请求参数错误（HTTP 400）：{detail}", status=status)
        if status != 200:
            raise ApiError(f"HTTP {status} {detail}".strip(), status=status)
        if not isinstance(body, dict):
            raise ApiError("响应不是 JSON 对象", status=status)
        if body.get("status") == "error":  # 例如 user not found 也是 HTTP 200
            raise ApiError(f"接口返回错误：{detail or 'unknown'}", status=status)
        return body

    def user_info(self, handle: str) -> dict[str, Any]:
        """账号基本信息：id、用户名、资料页声明的粉丝 / 关注数（约 18 credits）。"""
        handle = handle.strip().removeprefix("@")
        if not HANDLE.fullmatch(handle):
            raise ApiError("请填写 @用户名（1–15 位字母、数字或下划线）。")
        data = self.get("/twitter/user/info", {"userName": handle}).get("data")
        if not isinstance(data, dict):
            raise ApiError("接口没有返回账号信息")
        if data.get("unavailable"):
            raise ApiError(
                f"账号不可用：{short(data.get('unavailableReason') or data.get('message'), 60)}"
            )
        account_id = str(data.get("id") or "")
        if not account_id.isdigit():
            raise ApiError("接口返回的账号 ID 不符合预期")
        return {
            "id": account_id,
            "username": str(data.get("userName") or handle),
            "followers": data.get("followers"),
            "following": data.get("following"),
        }

    def page(
        self, relation: str, handle: str, cursor: str | None, page_size: int = 200
    ) -> dict[str, Any]:
        if relation not in {"following", "followers"} or not 20 <= page_size <= 200:
            raise ApiError("列表类型或每页人数无效")
        handle = handle.strip().removeprefix("@")
        if not HANDLE.fullmatch(handle):
            raise ApiError("账号格式无效")
        list_key = "followings" if relation == "following" else "followers"
        body = self.get(
            f"/twitter/user/{list_key}",
            {"userName": handle, "cursor": cursor or "", "pageSize": page_size},
        )
        source = body.get(list_key)
        if not isinstance(source, list) or not isinstance(body.get("has_next_page"), bool):
            raise ApiError("接口列表或终止标记不兼容，保留已有数据")
        rows = []
        for row in source:
            if not isinstance(row, dict):
                raise ApiError("用户字段不兼容，保留已有数据")
            member_id = row.get("id")
            member_handle = row.get("userName") or row.get("username") or row.get("screen_name")
            if (
                not isinstance(member_id, str)
                or not member_id.isdigit()
                or not isinstance(member_handle, str)
                or not HANDLE.fullmatch(member_handle)
            ):
                raise ApiError("用户字段不兼容，保留已有数据")
            rows.append(
                {
                    "id": member_id,
                    "username": member_handle,
                    "name": str(row.get("name") or ""),
                    "bio": str(row.get("description") or ""),
                }
            )
        next_cursor = body.get("next_cursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise ApiError("接口游标不兼容，保留已有数据")
        return {
            "rows": rows,
            "cursor": next_cursor or None,
            "terminal": body["has_next_page"] is False,
        }
