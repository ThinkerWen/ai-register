import json
import time
from typing import Callable, Optional

import requests

IN_URL = "https://2captcha.com/in.php"
RES_URL = "https://2captcha.com/res.php"
DEFAULT_TENCENT_APP_ID = "189979000"


class TwoCaptchaError(RuntimeError):
    """2captcha 调用失败。"""


def _read_json(response: requests.Response) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise TwoCaptchaError(f"2captcha 响应格式错误: {response.text[:200]}") from exc
    if not isinstance(payload, dict):
        raise TwoCaptchaError(f"2captcha 响应格式错误: {payload}")
    return payload


def solve_tencent_captcha(
    api_key: str,
    app_id: str = DEFAULT_TENCENT_APP_ID,
    page_url: str = "",
    captcha_script: str = "",
    timeout: int = 180,
    poll_interval: float = 5,
    proxy: str = "",
    logger: Optional[Callable[[str], None]] = None,
) -> dict:
    """使用 2captcha 的 tencent 方法求解腾讯点选验证码，返回 ticket / randstr。"""
    key = str(api_key or "").strip()
    if not key:
        raise TwoCaptchaError("2captcha API key 不能为空")
    page_url = str(page_url or "").strip()
    if not page_url:
        raise TwoCaptchaError("2captcha 求解腾讯验证码需要提供页面地址")

    payload = {
        "key": key,
        "method": "tencent",
        "app_id": str(app_id or DEFAULT_TENCENT_APP_ID).strip(),
        "pageurl": page_url,
        "json": 1,
    }
    if proxy:
        payload["proxy"] = str(proxy).strip()
        payload["proxytype"] = "http"
    if captcha_script:
        payload["captcha_script"] = str(captcha_script).strip()

    if logger:
        logger(f"提交 2captcha 腾讯验证码任务 | app_id={payload['app_id']}")
    try:
        response = requests.post(IN_URL, data=payload, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise TwoCaptchaError(f"2captcha 任务提交失败: {exc}") from exc

    data = _read_json(response)
    if int(data.get("status") or 0) != 1:
        raise TwoCaptchaError(f"2captcha 任务提交失败: {data.get('request') or data}")
    task_id = str(data.get("request") or "").strip()
    if not task_id:
        raise TwoCaptchaError(f"2captcha 未返回任务 ID: {data}")
    if logger:
        logger(f"2captcha 任务已创建 | id={task_id}")

    deadline = time.monotonic() + max(1, int(timeout))
    while time.monotonic() < deadline:
        time.sleep(max(0.5, float(poll_interval)))
        try:
            response = requests.get(
                RES_URL,
                params={"key": key, "action": "get", "id": task_id, "json": 1},
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise TwoCaptchaError(f"2captcha 结果查询失败: {exc}") from exc

        data = _read_json(response)
        if int(data.get("status") or 0) == 1:
            result = data.get("request")
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except ValueError:
                    pass
            if not isinstance(result, dict):
                raise TwoCaptchaError(f"2captcha 返回结果无效: {result}")
            ticket = str(result.get("ticket") or "").strip()
            randstr = str(result.get("randstr") or "").strip()
            if not ticket or not randstr:
                raise TwoCaptchaError(f"2captcha 返回结果缺少 ticket / randstr: {result}")
            return {
                "appid": str(result.get("appid") or payload["app_id"]),
                "ret": int(result.get("ret") or 0),
                "ticket": ticket,
                "randstr": randstr,
            }

        message = str(data.get("request") or "").strip()
        if message == "CAPCHA_NOT_READY":
            continue
        raise TwoCaptchaError(f"2captcha 求解失败: {message or data}")

    raise TwoCaptchaError(f"2captcha 求解腾讯验证码超时（{timeout} 秒）")
