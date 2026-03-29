"""
core/agent.py — MCP-Agent mode: direct HTTP to MicroStrategy MCP server
Supports conversation threading for <Follow-up> prompts via the history parameter.

MCP JSON-RPC:
  POST <connector_url>
  { "jsonrpc":"2.0", "id":1, "method":"tools/call",
    "params": { "name":"ask_agent", "arguments": {
      "id": agent_id, "projectId": project_id,
      "needChartData": true,
      "history": [ {"id":"fakeId","question":"...","text":"..."}, ... ],
      "question": "..."
    }}}

The connector URL and agent/project IDs are passed at runtime rather than
read from settings, allowing multiple MCP connectors to be used interchangeably.
"""

import time
import json
from core.results import infer_attributes_metrics, build_conversation_groups
from core.cli import progress, warn, info


def _call_ask_agent(session, question: str, history: list,
                    connector_url: str, agent_id: str, project_id: str,
                    need_chart_data: bool = True) -> dict:
    """Make a single MCP tools/call to ask_agent. Returns raw JSON-RPC response."""
    payload = {
        "jsonrpc": "2.0",
        "id":      1,
        "method":  "tools/call",
        "params":  {
            "name": "ask_agent",
            "arguments": {
                "id":            agent_id,
                "projectId":     project_id,
                "needChartData": need_chart_data,
                "history":       history,
                "question":      question,
            }
        }
    }

    resp = session.session.post(
        connector_url,
        json=payload,
        headers=session._headers(),
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def _parse_mcp_response(rpc_response: dict) -> dict:
    """Parse JSON-RPC response into schema fields."""
    result = {
        "text":                None,
        "interpretedQuestion": None,
        "insights":            None,
        "chartData":           None,
    }
    try:
        content = rpc_response.get("result", {}).get("content", [])
        for item in content:
            if item.get("type") == "text":
                inner   = json.loads(item["text"])
                answers = inner.get("answers", [])
                if answers:
                    a = answers[0]
                    result["text"]                = a.get("text")
                    result["interpretedQuestion"] = a.get("interpretedQuestion")
                    result["insights"]            = a.get("insights")
                    cd = a.get("chartData", "")
                    if cd:
                        try:
                            result["chartData"] = json.loads(cd) if isinstance(cd, str) else cd
                        except Exception:
                            result["chartData"] = cd
                break
    except Exception:
        pass
    return result


def _build_history_entry(question: str, answer_text: str) -> dict:
    """Build a single history entry in the format ask_agent expects."""
    return {
        "id":       "fakeId",
        "question": question,
        "text":     answer_text or "",
    }


def run_standard(prompts_cfg: list, result_records: list, session,
                 connector_url: str, agent_id: str, project_id: str,
                 delay: float = 1.0):
    """
    Run all prompts in Standard mode via direct HTTP MCP calls.
    Handles <Follow-up> prompts by threading history.
    Updates result_records in-place.
    """
    # Build a lookup from id → record for easy access
    rec_by_id = {rec["id"]: rec for rec in result_records}

    total  = len(prompts_cfg)
    groups = build_conversation_groups(prompts_cfg)

    info(f"Running {total} prompts in Standard mode (MCP direct HTTP) "
         f"— {len(groups)} conversation(s)...")
    print()

    prompt_num = 0  # global counter for progress bar

    for group in groups:
        # Build the history as we go through the group
        history: list[dict] = []
        all_in_group = [group["root"]] + group["children"]

        for cfg in all_in_group:
            prompt_num += 1
            prompt_id   = cfg["id"]
            is_followup = cfg["prompt"].startswith("<Follow-up>")
            # Strip the prefix for the actual question sent
            clean_question = cfg["prompt"][len("<Follow-up>"):].strip() if is_followup else cfg["prompt"]
            rec = rec_by_id[prompt_id]

            # Set parentId on the record
            if is_followup:
                rec["parentId"] = cfg.get("_parentId")

            progress(prompt_num, total,
                     f"{'↳ Follow-up' if is_followup else 'Prompt'} {prompt_id}: {clean_question[:45]}...")

            t0 = time.time()
            try:
                rpc_resp = _call_ask_agent(session, clean_question, history,
                                           connector_url, agent_id, project_id)
                elapsed  = round(time.time() - t0, 2)

                if "error" in rpc_resp:
                    raise RuntimeError(f"MCP error: {rpc_resp['error']}")

                parsed = _parse_mcp_response(rpc_resp)
                attrs, metrics = infer_attributes_metrics(parsed.get("chartData"))

                rec.update({
                    "status":              "Success",
                    "error":               None,
                    "responseTime":        elapsed,
                    "mode":                "mcp-agent",
                    "responseText":        parsed["text"],
                    "interpretedQuestion": parsed["interpretedQuestion"],
                    "insights":            parsed["insights"],
                    "chartData":           parsed["chartData"],
                    "attributesUsed":      attrs   or None,
                    "metricsUsed":         metrics or None,
                })

                # Append this Q&A to history for the next follow-up in the group
                history.append(_build_history_entry(clean_question, parsed["text"] or ""))

            except Exception as e:
                elapsed = round(time.time() - t0, 2)
                rec.update({
                    "status":       "Error",
                    "error":        str(e),
                    "responseTime": elapsed,
                    "mode":         "mcp-agent",
                })
                warn(f"Prompt {prompt_id} failed: {e}")
                # Still append to history so follow-ups can continue (with empty answer)
                history.append(_build_history_entry(clean_question, ""))

            if prompt_num < total:
                time.sleep(delay)

    print()
