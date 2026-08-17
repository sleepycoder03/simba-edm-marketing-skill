#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from zoneinfo import ZoneInfo

FATAL_REASONS = {"EDM_DAILY_QUOTA_EXCEEDED", "EDM_TEMPLATE_NOT_FOUND", "EDM_PARAMS_ERROR"}


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def to_num(v: Any, default: int = 0) -> int:
    try:
        n = int(str(v))
        return n
    except Exception:
        return default


def request_json(
    url: str,
    headers: Dict[str, str],
    method: str = "GET",
    body: Optional[Dict[str, Any]] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    data = None
    req_headers = dict(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers["content-type"] = "application/json"

    req = Request(url=url, headers=req_headers, method=method, data=data)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.getcode()
    except HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        status = e.code
    except URLError as e:
        raise RuntimeError(f"network error: {e}")

    try:
        payload = json.loads(raw)
    except Exception:
        payload = {"raw": raw}

    return {"status": status, "json": payload, "text": raw}


def with_retry(fn, label: str, retries: int = 20):
    last_err = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            wait_ms = min(30000, 2000 * (i + 1))
            print(f"{label}_EXCEPTION retry={i+1}/{retries} wait={wait_ms}ms err={str(e)[:220]}")
            time.sleep(wait_ms / 1000.0)
    raise RuntimeError(f"{label} failed after retries: {last_err}")


def build_url(base: str, path: str, query: Optional[Dict[str, Any]] = None) -> str:
    q = f"?{urlencode(query)}" if query else ""
    return f"{base.rstrip('/')}/{path.lstrip('/')}" + q


def get_templates(base: str, headers: Dict[str, str]) -> List[Dict[str, Any]]:
    url = build_url(base, "edm/templates")
    resp = with_retry(lambda: request_json(url, headers), "templates")
    if resp["status"] != 200 or resp["json"].get("code") != 0:
        raise RuntimeError(f"failed templates: {resp['text'][:500]}")
    return (resp["json"].get("data") or {}).get("templates") or []


def get_total(base: str, headers: Dict[str, str], country: str, sent_status: str) -> int:
    url = build_url(
        base,
        "edm/customers",
        {"countryCode": country, "sentStatus": sent_status, "page": 1},
    )
    resp = with_retry(lambda: request_json(url, headers), f"total_{sent_status}")
    if resp["status"] != 200 or resp["json"].get("code") != 0:
        raise RuntimeError(f"failed total {sent_status}: {resp['text'][:500]}")
    return to_num(((resp["json"].get("data") or {}).get("total")), 0)


def fetch_unsent_page1(base: str, headers: Dict[str, str], country: str) -> List[Dict[str, Any]]:
    url = build_url(
        base,
        "edm/customers",
        {"countryCode": country, "sentStatus": "unsent", "page": 1},
    )
    resp = with_retry(lambda: request_json(url, headers), "fetch_customers")
    if resp["status"] != 200 or resp["json"].get("code") != 0:
        raise RuntimeError(f"failed fetch unsent page1: {resp['text'][:500]}")
    return ((resp["json"].get("data") or {}).get("customers") or [])


def fetch_all_customers(base: str, headers: Dict[str, str], country: str, max_pages: int = 5000) -> Dict[str, Any]:
    page = 1
    all_customers: List[Dict[str, Any]] = []
    total = None

    while page <= max_pages:
        url = build_url(base, "edm/customers", {"countryCode": country, "page": page})
        resp = with_retry(lambda: request_json(url, headers), f"customers_page_{page}")
        if resp["status"] != 200 or resp["json"].get("code") != 0:
            raise RuntimeError(f"fetch all customers failed at page {page}: {resp['text'][:500]}")

        data = resp["json"].get("data") or {}
        customers = data.get("customers") or []
        if total is None:
            total = to_num(data.get("total"), 0)

        if not customers:
            break

        all_customers.extend(customers)
        if len(all_customers) >= total:
            break

        page += 1
        time.sleep(0.05)

    return {"customers": all_customers, "total": total if total is not None else len(all_customers), "pages": page}


def should_send_not_sent_today(customer: Dict[str, Any], day_start_epoch: int) -> bool:
    has_sent = bool(customer.get("hasSent"))
    last_success = to_num(customer.get("lastSuccessSentAt"), 0)
    if not has_sent:
        return True
    if last_success <= 0:
        return True
    return last_success < day_start_epoch


def send_batch(base: str, headers: Dict[str, str], uids: List[str], template_code: str) -> Dict[str, Any]:
    url = build_url(base, "edm/send")
    resp = with_retry(
        lambda: request_json(url, headers, method="POST", body={"customerUids": uids, "templateCode": template_code}),
        "send",
    )
    return resp


def run_unsent_mode(
    base: str,
    headers: Dict[str, str],
    country: str,
    template_code: str,
    target: int,
    max_batch: int,
    sleep_success: float,
) -> Dict[str, Any]:
    success = 0
    attempted = 0
    batches: List[Dict[str, Any]] = []
    start_ts = time.time()

    while success < target:
        customers = fetch_unsent_page1(base, headers, country)
        if not customers:
            print("NO_MORE_UNSENT_CUSTOMERS")
            break

        need = min(max_batch, target - success, len(customers))
        uids = [c.get("uid") for c in customers[:need] if c.get("uid")]
        if not uids:
            print("NO_UIDS_IN_BATCH")
            break

        resp = send_batch(base, headers, uids, template_code)
        j = resp["json"] or {}

        if resp["status"] == 200 and j.get("code") == 0:
            d = j.get("data") or {}
            sc = to_num(d.get("successCount"), 0)
            fc = to_num(d.get("failedCount"), 0)
            tc = to_num(d.get("totalCount"), len(uids))
            bid = d.get("batchId") or ""
            success += sc
            attempted += tc
            batches.append({"batchId": bid, "success": sc, "failed": fc, "total": tc})
            elapsed = int(time.time() - start_ts)
            print(f"OK #{len(batches)} batch={bid} +{sc} success={success}/{target} elapsed={elapsed}s")
            time.sleep(sleep_success)
            continue

        reason = j.get("reason") or "UNKNOWN"
        retry_after = max(2, to_num((j.get("metadata") or {}).get("retryAfterSeconds"), 2))
        print(f"FAIL status={resp['status']} reason={reason} retry={retry_after}s resp={json.dumps(j, ensure_ascii=False)[:320]}")
        if reason in FATAL_REASONS:
            break
        time.sleep(retry_after)

    return {"success": success, "attempted": attempted, "batches": batches}


def run_not_sent_today_mode(
    base: str,
    headers: Dict[str, str],
    country: str,
    template_code: str,
    target: int,
    max_batch: int,
    timezone: str,
    sleep_success: float,
) -> Dict[str, Any]:
    tz = ZoneInfo(timezone)
    day_start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_epoch = int(day_start.timestamp())

    all_data = fetch_all_customers(base, headers, country)
    candidates = [c for c in all_data["customers"] if should_send_not_sent_today(c, day_start_epoch)]

    success = 0
    attempted = 0
    batches: List[Dict[str, Any]] = []
    start_ts = time.time()

    effective_target = min(target, len(candidates))
    print(
        f"NOT_SENT_TODAY_FILTER timezone={timezone} day_start_epoch={day_start_epoch} "
        f"eligible={len(candidates)} effective_target={effective_target}"
    )

    idx = 0
    while success < effective_target and idx < len(candidates):
        need = min(max_batch, effective_target - success, len(candidates) - idx)
        batch = candidates[idx:idx + need]
        idx += need

        uids = [c.get("uid") for c in batch if c.get("uid")]
        if not uids:
            continue

        resp = send_batch(base, headers, uids, template_code)
        j = resp["json"] or {}

        if resp["status"] == 200 and j.get("code") == 0:
            d = j.get("data") or {}
            sc = to_num(d.get("successCount"), 0)
            fc = to_num(d.get("failedCount"), 0)
            tc = to_num(d.get("totalCount"), len(uids))
            bid = d.get("batchId") or ""
            success += sc
            attempted += tc
            batches.append({"batchId": bid, "success": sc, "failed": fc, "total": tc})
            elapsed = int(time.time() - start_ts)
            print(f"OK #{len(batches)} batch={bid} +{sc} success={success}/{effective_target} elapsed={elapsed}s")
            time.sleep(sleep_success)
            continue

        reason = j.get("reason") or "UNKNOWN"
        retry_after = max(2, to_num((j.get("metadata") or {}).get("retryAfterSeconds"), 2))
        print(f"FAIL status={resp['status']} reason={reason} retry={retry_after}s resp={json.dumps(j, ensure_ascii=False)[:320]}")
        if reason in FATAL_REASONS:
            break
        time.sleep(retry_after)

    return {
        "success": success,
        "attempted": attempted,
        "batches": batches,
        "eligibleBefore": len(candidates),
        "effectiveTarget": min(target, len(candidates)),
        "dayStartEpoch": day_start_epoch,
        "dayStartIso": day_start.astimezone(ZoneInfo("UTC")).isoformat(),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Simba EDM bulk sender")
    p.add_argument("--token", default=os.getenv("SIMBA_TOKEN", ""), help="Bearer token (or set SIMBA_TOKEN)")
    p.add_argument("--base-url", default="https://cms-api.aoneroom.com/wefeed-cms-bff")
    p.add_argument("--country", required=True, help="Country code, e.g. MY/IN")
    p.add_argument("--target", type=int, required=True, help="Target send count")
    p.add_argument("--segment", choices=["unsent", "not_sent_today"], default="unsent")
    p.add_argument("--template-code", default="", help="Optional template code")
    p.add_argument("--max-batch", type=int, default=50)
    p.add_argument("--max-retries", type=int, default=20)
    p.add_argument("--timezone", default="Asia/Shanghai", help="For not_sent_today mode")
    p.add_argument("--sleep-success", type=float, default=0.25)
    p.add_argument("--output", default="")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if not args.token:
        print("ERROR: token is required. pass --token or set SIMBA_TOKEN", file=sys.stderr)
        return 2

    if args.target <= 0:
        print("ERROR: --target must be > 0", file=sys.stderr)
        return 2

    headers = {"authorization": f"Bearer {args.token}"}

    templates = get_templates(args.base_url, headers)
    if not templates:
        raise RuntimeError("no template available")

    template = None
    if args.template_code:
        for t in templates:
            if t.get("templateCode") == args.template_code:
                template = t
                break
        if template is None:
            raise RuntimeError(f"template code not found: {args.template_code}")
    else:
        template = templates[0]

    template_code = template.get("templateCode")
    template_name = template.get("name")

    before_unsent = get_total(args.base_url, headers, args.country, "unsent")
    before_sent = get_total(args.base_url, headers, args.country, "sent")

    print(
        f"START country={args.country} target={args.target} segment={args.segment} "
        f"before_unsent={before_unsent} before_sent={before_sent} template={template_code}({template_name})"
    )

    if args.dry_run:
        result = {"success": 0, "attempted": 0, "batches": []}
    elif args.segment == "unsent":
        effective_target = min(args.target, before_unsent)
        print(f"EFFECTIVE_TARGET={effective_target}")
        result = run_unsent_mode(
            base=args.base_url,
            headers=headers,
            country=args.country,
            template_code=template_code,
            target=effective_target,
            max_batch=args.max_batch,
            sleep_success=args.sleep_success,
        )
        result["effectiveTarget"] = effective_target
    else:
        result = run_not_sent_today_mode(
            base=args.base_url,
            headers=headers,
            country=args.country,
            template_code=template_code,
            target=args.target,
            max_batch=args.max_batch,
            timezone=args.timezone,
            sleep_success=args.sleep_success,
        )

    after_unsent = get_total(args.base_url, headers, args.country, "unsent")
    after_sent = get_total(args.base_url, headers, args.country, "sent")

    print(
        f"FINAL after_unsent={after_unsent} after_sent={after_sent} "
        f"success={result.get('success', 0)} attempted={result.get('attempted', 0)} "
        f"batches={len(result.get('batches', []))}"
    )

    default_output = os.path.join(
        os.getcwd(),
        "outputs",
        f"edm_send_report_{args.country.lower()}_{args.segment}_{args.target}_{datetime.now().strftime('%Y-%m-%d')}.json",
    )
    output_path = args.output or default_output
    ensure_dir(os.path.dirname(output_path))

    report = {
        "runAt": now_iso(),
        "country": args.country,
        "segment": args.segment,
        "target": args.target,
        "effectiveTarget": result.get("effectiveTarget", args.target),
        "templateCode": template_code,
        "templateName": template_name,
        "timezone": args.timezone,
        "success": result.get("success", 0),
        "attempted": result.get("attempted", 0),
        "batchCount": len(result.get("batches", [])),
        "eligibleBefore": result.get("eligibleBefore"),
        "dayStartEpoch": result.get("dayStartEpoch"),
        "dayStartIso": result.get("dayStartIso"),
        "before": {"unsent": before_unsent, "sent": before_sent},
        "after": {"unsent": after_unsent, "sent": after_sent},
        "delta": {"unsent": after_unsent - before_unsent, "sent": after_sent - before_sent},
        "batches": result.get("batches", []),
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"REPORT {output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"FATAL {e}", file=sys.stderr)
        raise
