"""
Model client. Every call goes through call_model(), which:
  - sends X-Item-Id (the grader's proxy attributes calls to items by it),
  - fixes the required sampling settings (temperature 1.0, top_p 0.95,
    reasoning disabled),
  - counts every request ATTEMPT against the item's budget. A retried request
    may already have reached the proxy, so a retry is only made while the item
    still has budget left. This never over-spends, at the cost of not retrying
    a failed call at 1x.
"""
import os
import threading
import time
from collections import Counter

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

MODEL = "ibm-granite/granite-4.2-8b"   # fixed by the brief; never read from the environment
BASE_URL = (os.environ.get("OPENROUTER_BASE_URL") or os.environ.get("BASE_URL")
            or os.environ.get("OPENAI_BASE_URL"))
API_KEY = (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("API_KEY")
           or os.environ.get("OPENAI_API_KEY"))

_client = None
_lock = threading.Lock()
CALLS = Counter()


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(base_url=BASE_URL, api_key=API_KEY,
                         timeout=180, max_retries=0)
    return _client


class BudgetExceeded(RuntimeError):
    pass


def calls_left(item_id, cap):
    with _lock:
        return cap - CALLS[item_id]


def call_model(item_id, messages, cap, max_tokens=1500):
    """One model call for item_id. Returns the text, or None if every attempt
    failed. Each attempt is charged to the item; retries stop at the cap."""
    while True:
        with _lock:
            if CALLS[item_id] >= cap:
                return None
            CALLS[item_id] += 1
            attempt = CALLS[item_id]
        try:
            r = _get_client().chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=1.0,
                top_p=0.95,
                max_tokens=max_tokens,
                extra_headers={"X-Item-Id": item_id},
                extra_body={"reasoning": {"enabled": False}},
            )
            return r.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {item_id} attempt {attempt} failed: {e}", flush=True)
            time.sleep(3)
