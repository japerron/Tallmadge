"""
core/api.py — API mode: MicroStrategy REST API calls
POST /api/questions → poll GET /api/questions/{id} until isFinished
Returns full schema including SQL, explanation, attributesUsed, metricsUsed etc.
"""

import re
import time
import json
import base64
import requests
import urllib3
from core.results import infer_attributes_metrics, build_conversation_groups, extract_where_tokens
from core.cli import progress, warn, info, success, error, grey
from config.settings import LOGIN_MODE

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_SORRY_RE = re.compile(r"sorry,?\s+i\s+(could\s+not|can'?t)\s+answer", re.IGNORECASE)


# ── Authentication ─────────────────────────────────────────────────────────────

class MSTRSession:
    """Manages a MicroStrategy REST API session."""

    def __init__(self, base_url: str = ""):
        self.base_url  = base_url.rstrip("/")
        self.session   = requests.Session()
        self.auth_token = None

    def login(self, username: str, password: str,
              login_mode: int = LOGIN_MODE) -> bool:
        """
        Authenticate and store the auth token.
        login_mode: 1 = Standard, 16 = LDAP (matches MicroStrategy loginMode values).
        Returns True on success.
        """
        url = f"{self.base_url}/api/auth/login"
        payload = {
            "username":  username,
            "password":  password,
            "loginMode": login_mode,
        }
        try:
            resp = self.session.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            token = resp.headers.get("X-MSTR-AuthToken") or resp.headers.get("x-mstr-authtoken")
            if not token:
                # Some versions return it in the body
                body = resp.json()
                token = body.get("token") or body.get("authToken")
            if token:
                self.auth_token = token
                self.session.headers.update({"X-MSTR-AuthToken": token})
                return True
            return False
        except requests.RequestException as e:
            error(f"Login failed: {e}")
            return False

    def logout(self):
        """Close the session."""
        try:
            self.session.delete(f"{self.base_url}/api/auth/logout", timeout=10)
        except Exception:
            pass

    def _headers(self, project_id: str | None = None,
                 accept: str | None = None) -> dict:
        h = {
            "Content-Type": "application/json",
            "Accept":        accept or "application/json",
        }
        if self.auth_token:
            h["X-MSTR-AuthToken"] = self.auth_token
        if project_id:
            h["X-MSTR-ProjectID"] = project_id
        return h


# ── Question runner ────────────────────────────────────────────────────────────

POLL_INTERVAL = 1.0   # seconds between polls
POLL_TIMEOUT  = 120   # seconds before giving up


def _keep_session_alive(session: MSTRSession) -> None:
    """PUT /api/sessions — resets the server-side session timeout."""
    try:
        session.session.put(
            f"{session.base_url}/api/sessions",
            headers=session._headers(),
            timeout=10,
        )
    except Exception:
        pass


def _post_question(session: MSTRSession, agent_id: str, project_id: str,
                   prompt: str, conversation_id: str | None = None,
                   application_id: str | None = None) -> tuple[str, str]:
    """
    POST a question and return (question_id, conversation_id).
    botIds and applicationId go as query params (not in the body).
    Pass conversation_id to continue an existing conversation thread.
    """
    url    = f"{session.base_url}/api/questions"
    params = {"botIds": agent_id, "useHistory": False}
    if application_id:
        params["applicationId"] = application_id

    payload = {
        "text":           prompt,
        "textOnly":       False,                                  # allow charts/images in response
        "answers.images": [{"width": 800, "height": 600}],       # request rendered image dimensions
    }
    if conversation_id:
        payload["conversationId"] = conversation_id

    # Project ID goes in header; Prefer: respond-async for immediate 202
    headers = {**session._headers(project_id=project_id), "Prefer": "respond-async"}
    resp = session.session.post(url, json=payload, headers=headers, params=params, timeout=30)
    if resp.status_code in (200, 202):
        body    = resp.json()
        q_id    = body.get("id")
        conv_id = body.get("conversationId") or conversation_id
        return q_id, conv_id
    else:
        raise RuntimeError(f"POST /api/questions failed: {resp.status_code} {resp.text[:200]}")


def _poll_question(session: MSTRSession, question_id: str, project_id: str = "") -> dict:
    """
    Poll GET /api/questions/{id} until complete.
    Async API convention: 202 = still processing, 200 = done.
    Falls back to checking isFinished field if present.
    Returns the full response body.
    """
    url = f"{session.base_url}/api/questions/{question_id}"
    deadline = time.time() + POLL_TIMEOUT

    while time.time() < deadline:
        resp = session.session.get(
            url, headers=session._headers(project_id=project_id), timeout=30
        )
        resp.raise_for_status()
        body = resp.json()
        if resp.status_code == 200 or body.get("isFinished"):
            return body
        # 202 = still processing — keep polling
        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f"Question {question_id} did not finish within {POLL_TIMEOUT}s")


IMAGE_RETRY_SECS  = 5.0   # total seconds to keep retrying a 5xx image response
IMAGE_RETRY_SLEEP = 0.5   # pause between retries


def _fetch_answer_images(
    session: MSTRSession,
    question_id: str,
    project_id: str,
    images_meta: list,
) -> list[dict]:
    """
    Fetch each rendered answer image from:
      GET /api/questions/{question_id}/answers/images/{img_id}
    Retries for up to IMAGE_RETRY_SECS on 5xx (image may still be rendering).
    Returns a list of dicts: {id, width, height, data (base64 PNG string)}.
    Failed fetches are warned and skipped.
    """
    fetched = []
    for img in images_meta:
        img_id = img.get("id")
        if not img_id:
            continue
        url = f"{session.base_url}/api/questions/{question_id}/answers/images/{img_id}"
        headers = {
            "X-MSTR-AuthToken": session.auth_token or "",
            "X-MSTR-ProjectID": project_id,
            "Accept": "image/png",
        }
        deadline = time.time() + IMAGE_RETRY_SECS
        last_err = None
        while time.time() <= deadline:
            try:
                resp = session.session.get(url, headers=headers, timeout=30)
                if resp.status_code == 200:
                    fetched.append({
                        "id":     img_id,
                        "width":  img.get("width"),
                        "height": img.get("height"),
                        "data":   base64.b64encode(resp.content).decode("ascii"),
                    })
                    last_err = None
                    break
                elif 500 <= resp.status_code < 600:
                    last_err = f"HTTP {resp.status_code} — {resp.text[:200]}"
                    time.sleep(IMAGE_RETRY_SLEEP)
                else:
                    # 4xx or unexpected — no point retrying
                    last_err = f"HTTP {resp.status_code} — {resp.text[:200]}"
                    break
            except Exception as e:
                last_err = str(e)
                time.sleep(IMAGE_RETRY_SLEEP)
        if last_err:
            pass  # silently skip unfetchable images
    return fetched


def _fetch_answer_data(
    session: MSTRSession,
    question_id: str,
    project_id: str,
    data_id: str,
) -> list:
    """
    Fetch raw tabular data from:
      GET /api/questions/{question_id}/answers/data/{data_id}
    Returns charts[0]["data"] (a list of row dicts), or [] on failure.
    """
    if not data_id:
        return []
    url = f"{session.base_url}/api/questions/{question_id}/answers/data/{data_id}"
    try:
        resp = session.session.get(
            url,
            headers=session._headers(project_id=project_id),
            timeout=30,
        )
        if resp.status_code != 200:
            warn(f"  Data fetch returned HTTP {resp.status_code}")
            return []
        charts = resp.json().get("charts", [])
        return charts[0].get("data", []) if charts else []
    except Exception as e:
        warn(f"  Data fetch failed: {e}")
        return []


def _parse_question_response(body: dict) -> dict:
    """
    Parse the full /api/questions/{id} response into our schema.
    """
    result = {
        "responseText":        None,
        "interpretedQuestion": None,
        "answerType":          None,   # e.g. "data", "text" — from a.type
        "insights":            None,
        "chartData":           None,
        "gridData":            None,   # tabular grid from a.data
        "imagesData":          None,   # list[{id, width, height, data(base64)}] — filled by run_extended
        "attributesUsed":      None,
        "metricsUsed":         None,
        "attributeFormsUsed":  None,
        "datasetsUsed":        None,
        "sqlQueries":          None,
        "explanation":         None,
        "isCacheUsed":         None,
    }

    answers = body.get("answers", [])
    if not answers:
        return result

    a = answers[0]

    result["responseText"]        = a.get("text")
    result["interpretedQuestion"] = a.get("interpretedQuestion")
    result["answerType"]          = a.get("type")
    result["isCacheUsed"]         = a.get("isCacheUsed")

    # Insights: can be a dict or string
    insights_raw = a.get("insights")
    if isinstance(insights_raw, dict):
        result["insights"] = json.dumps(insights_raw)
    else:
        result["insights"] = insights_raw

    # Chart data (present when API returns chart config)
    charts = a.get("charts")
    col_formats = a.get("columnFormats")
    if charts:
        result["chartData"] = {"charts": charts, "columnFormats": col_formats}

    # gridData is populated later in run_extended via _fetch_answer_data;
    # a.get("data") here is only a reference dict {"id": "..."}, not actual rows.

    # Attributes / metrics (direct from API)
    result["attributesUsed"]     = a.get("attributesUsed") or []
    result["metricsUsed"]        = a.get("metricsUsed") or []
    result["attributeFormsUsed"] = a.get("attributeFormsUsed") or []
    result["datasetsUsed"]       = a.get("datasetsUsed") or []

    # SQL
    sql_queries = a.get("sqlQueries", [])
    result["sqlQueries"]        = sql_queries if sql_queries else None
    result["whereClauseTokens"] = extract_where_tokens(result["sqlQueries"])

    # Explanation: from queries[0].explanation
    queries = a.get("queries", [])
    if queries and isinstance(queries[0], dict):
        result["explanation"] = queries[0].get("explanation")

    # Fall back to inferred attrs/metrics from chartData if API returned empty
    if not result["attributesUsed"] and not result["metricsUsed"]:
        attrs, metrics = infer_attributes_metrics(result["chartData"])
        result["attributesUsed"] = attrs or None
        result["metricsUsed"]    = metrics or None

    return result


# ── Main runner ────────────────────────────────────────────────────────────────

def run_extended(
    prompts_cfg: list,
    result_records: list,
    session: MSTRSession,
    agent_id: str,
    project_id: str,
    delay: float = 0.5,
    application_id: str | None = None,
    on_sorry: str = "stop",
    sorry_retries: int = 0,
    sorry_delay: float = 120.0,
):
    """
    Run all prompts in Extended mode via REST API.
    Handles <Follow-up> prompts by threading conversationId.
    Updates result_records in-place.

    on_sorry controls behaviour when the agent returns a rephrasing response:
      "stop"      — stop the run immediately and log partial results.
      "continue"  — log the sorry response and continue to the next prompt.
      "resubmit"  — re-submit the same prompt up to sorry_retries times,
                    waiting sorry_delay seconds between attempts, then continue.
    sorry_retries — max re-submission attempts per prompt (0-3, "resubmit" only).
    sorry_delay   — seconds to wait before each resubmission (default 120).
    """
    rec_by_id = {rec["id"]: rec for rec in result_records}
    total     = len(prompts_cfg)
    groups    = build_conversation_groups(prompts_cfg)

    info(f"Running {total} prompts via REST API "
         f"— {len(groups)} conversation(s)...")
    print()

    prompt_num = 0

    for group in groups:
        conversation_id = None  # reset for each new root conversation
        all_in_group = [group["root"]] + group["children"]

        for cfg in all_in_group:
            prompt_num += 1
            prompt_id   = cfg["id"]
            is_followup = cfg["prompt"].startswith("<Follow-up>")
            clean_question = cfg["prompt"][len("<Follow-up>"):].strip() if is_followup else cfg["prompt"]
            rec = rec_by_id[prompt_id]

            if is_followup:
                rec["parentId"] = cfg.get("_parentId")

            # Print prompt label on its own permanent line BEFORE submitting so it
            # is always visible regardless of how fast the response arrives.
            prefix = "↳ Follow-up" if is_followup else "Prompt"
            print(f"  [{prompt_num}/{total}] {prefix} {prompt_id}: {clean_question}",
                  flush=True)

            retries_left = sorry_retries
            while True:
                t0 = time.time()
                try:
                    _keep_session_alive(session)
                    question_id, conversation_id = _post_question(
                        session, agent_id, project_id, clean_question, conversation_id,
                        application_id=application_id or None,
                    )
                    if not question_id:
                        raise RuntimeError("No question ID returned from POST")

                    body    = _poll_question(session, question_id, project_id=project_id)
                    elapsed = round(time.time() - t0, 2)
                    parsed  = _parse_question_response(body)

                    # Fetch raw tabular data if the API returned a data reference
                    data_ref = (body.get("answers") or [{}])[0].get("data")
                    data_id  = data_ref.get("id", "") if isinstance(data_ref, dict) else ""
                    if data_id:
                        raw_data = _fetch_answer_data(session, question_id, project_id, data_id)
                        if raw_data:
                            parsed["gridData"] = raw_data

                    # Fetch rendered images — only for answers that have valid image IDs
                    images_raw  = (body.get("answers") or [{}])[0].get("images", [])
                    images_meta = [img for img in images_raw
                                   if isinstance(img, dict) and img.get("id")]
                    if images_meta:
                        parsed["imagesData"] = _fetch_answer_images(
                            session, question_id, project_id, images_meta
                        )

                    is_sorry = bool(_SORRY_RE.search(parsed.get("responseText", "") or ""))

                    rec.update({
                        "status":         "Success",
                        "error":          None,
                        "responseTime":   elapsed,
                        "mode":           "api",
                        "conversationId": conversation_id,
                        **parsed,
                    })

                    if is_sorry:
                        warn(f"Agent requests rephrasing  ({elapsed}s)")
                        first_line = (parsed.get("responseText", "") or "").split("\n")[0].strip()
                        if first_line:
                            print(f"       {grey(first_line[:120])}", flush=True)

                        if on_sorry == "stop":
                            info("Stopping run early (output file will still be saved).")
                            return
                        if on_sorry == "resubmit" and retries_left > 0:
                            retries_left -= 1
                            info(
                                f"Resubmitting in {int(sorry_delay)}s "
                                f"({retries_left} attempt(s) remaining)..."
                            )
                            time.sleep(sorry_delay)
                            continue  # retry the same prompt
                        if on_sorry == "resubmit":
                            info("All resubmission attempts exhausted, continuing.")
                        # on_sorry == "continue", or resubmit retries exhausted → move on
                    else:
                        success(f"Success  ({elapsed}s)")

                    break  # move to next prompt

                except Exception as e:
                    elapsed = round(time.time() - t0, 2)
                    rec.update({
                        "status":       "Error",
                        "error":        str(e),
                        "responseTime": elapsed,
                        "mode":         "api",
                    })
                    error(f"Error  ({elapsed}s)")
                    print(f"       {grey(str(e)[:200])}", flush=True)
                    break  # don't retry on exceptions

            if prompt_num < total:
                time.sleep(delay)
