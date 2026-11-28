"""An example factory plugin: one grant application, checked and reviewed.

Every hook is pure. It reads the snapshot and the upload, and returns a
verdict. Storing, rendering on review, receipts and the review's independence
are core's: this plugin only says what a good application is.
"""

from __future__ import annotations

import json

from yaaf_plugin_api import (
    Accept,
    ActionResult,
    Brief,
    FactoryPlugin,
    FieldSpec,
    Owed,
    Page,
    ReadFields,
    Refuse,
    StepState,
    ToolError,
    hook,
)

from grant_factory.budget import total

APPLICATION = "application.json"


class GrantFactory(FactoryPlugin):
    @hook.brief
    def brief(self, ctx):
        return Brief("Draft application.json from the funder's call. Every budget line "
                     "names the rule of the call it meets; a second seat reviews the draft.")

    @hook.plan_gate
    def plan(self, ctx):
        if ctx.latest("calls.json") is None:
            return [Owed("call", "read the funder's call first (the call_for_proposals "
                                 "action): a plan for an application is a plan for one call")]
        return []

    @hook.create_fields
    def create_fields(self):
        return [FieldSpec("funder", "string", "Funder", "Who the application goes to.",
                          required=True),
                FieldSpec("amount", "number", "Amount asked (USD)", default=10000)]

    @hook.validate_create
    def validate_create(self, spec):
        amount = spec.fields.get("amount")
        return [] if isinstance(amount, (int, float)) and amount > 0 else \
            ["amount must be more than zero: an application asks for something"]

    @hook.completion_gate
    def completion(self, ctx):
        if ctx.latest(APPLICATION) is None:
            return [Owed("application", "no application.json has been accepted yet")]
        return []

    @hook.check_artifact(APPLICATION)
    def check_application(self, ctx, upload):
        try:
            doc = json.loads(upload.data)
        except (ValueError, UnicodeDecodeError):
            return Refuse(["application.json is not valid JSON"])
        if not isinstance(doc, dict):
            return Refuse(["application.json must be one JSON object"])
        problems = []
        if not isinstance(doc.get("title"), str) or not doc["title"].strip():
            problems.append("title is required")
        lines = doc.get("budget")
        if not isinstance(lines, list) or not lines:
            problems.append("budget needs at least one line")
        else:
            for i, line in enumerate(lines):
                if not isinstance(line, dict) or not isinstance(line.get("amount"), (int, float)):
                    problems.append(f"budget[{i}].amount is a number")
                if not isinstance(line, dict) or not line.get("rule"):
                    problems.append(f"budget[{i}].rule names the call's rule it meets")
        if problems:
            return Refuse(problems)
        asked = total(lines)
        return Accept(data=json.dumps(doc, sort_keys=True, indent=2).encode(),
                      state_patch={"asked": asked}, load_bearing={"budget_total": asked})

    @hook.check_review("application-review.json")
    def check_review(self, ctx, review, subject):
        try:
            doc = json.loads(review.data)
        except (ValueError, UnicodeDecodeError):
            return Refuse(["application-review.json is not valid JSON"])
        if not isinstance(doc, dict) or doc.get("verdict") not in ("pass", "return"):
            return Refuse(['verdict is "pass" or "return"'])
        if not isinstance(doc.get("summary"), str) or not doc["summary"].strip():
            return Refuse(["summary says what was checked"])
        return Accept()

    @hook.render
    def render(self, ctx, accepted, reviewed):
        doc = json.loads(accepted.data)
        mark = "" if reviewed else "> DRAFT — not reviewed yet\n\n"
        lines = "\n".join(f"- {b['rule']}: ${b['amount']:,.2f}" for b in doc["budget"])
        body = f"{mark}# {doc['title']}\n\n{lines}\n\nTotal: ${total(doc['budget']):,.2f}\n"
        return [Page("application.md", "text/markdown", body.encode())]

    @hook.fields_of_read
    def fields(self, tool, result):
        content = result.get("structuredContent")
        if isinstance(content, dict) and content:
            return ReadFields(tuple(sorted(content)))
        return ReadFields.unknown()

    @hook.data_action("call_for_proposals")
    def read_call(self, ctx, gateway):
        try:
            result = gateway.call("get_call", {})
        except ToolError as exc:
            return ActionResult({"read": False, "why": exc.message})
        body = json.dumps(result.get("structuredContent") or {}, sort_keys=True).encode()
        return ActionResult({"read": True, "bytes": len(body)}, {"calls.json": body})

    @hook.setup_step("funder_source")
    def funder_source(self, ctx):
        if "calls" in ctx.coverage.capabilities:
            return StepState(True, "The funder's calls are connected.")
        return StepState(False, "Connect the data source that holds the funder's calls.")

    @hook.view
    def view(self, ctx):
        latest = ctx.latest(APPLICATION)
        state = {"state": "ok" if latest else "info",
                 "text": "Drafted" if latest else "Not drafted yet"}
        data = {"state": state}
        if "asked" in ctx.kind_state:
            data["ask"] = {"label": "Asked", "value": ctx.kind_state["asked"], "unit": "USD"}
        if latest:
            data["draft"] = {"name": APPLICATION, "version": latest.version}
        return data


plugin = GrantFactory()
