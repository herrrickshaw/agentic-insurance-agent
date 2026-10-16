#!/usr/bin/env python3
"""Generate BPMN 2.0 (with diagram layout) and DMN 1.3 files into ../models.

Process logic is declared as small Python specs so the diagrams stay in sync
with what the workers implement. Output is plain Camunda-7-flavoured BPMN, so it
opens in bpmn.io / Camunda Modeler and deploys to Operaton, Camunda 7 CE or
(with minor changes) Flowable.
"""
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

OUT = Path(__file__).resolve().parent.parent / "models"
SIZE = {"event": (36, 36), "gw": (50, 50), "task": (110, 80)}
KIND_SIZE = {"start": "event", "end": "event", "msg": "event", "timer": "event",
             "xor": "gw", "evgw": "gw"}
COL_W, ROW_H, X0, Y0 = 150, 120, 60, 60


class Proc:
    def __init__(self, pid, name):
        self.pid, self.name = pid, name
        self.nodes, self.flows, self.messages = {}, [], {}

    def node(self, nid, kind, name="", col=0, row=0, **a):
        self.nodes[nid] = dict(kind=kind, name=name, col=col, row=row, a=a)

    def flow(self, src, tgt, cond=None, label="", default=False):
        self.flows.append(dict(src=src, tgt=tgt, cond=cond, label=label, default=default))

    # ---- geometry -------------------------------------------------------
    def box(self, nid):
        n = self.nodes[nid]
        w, h = SIZE[KIND_SIZE.get(n["kind"], "task")]
        cx, cy = X0 + n["col"] * COL_W + 55, Y0 + n["row"] * ROW_H + 40
        return cx - w // 2, cy - h // 2, w, h

    def waypoints(self, s, t):
        sx, sy, sw, sh = self.box(s)
        tx, ty, tw, th = self.box(t)
        a, b = (sx + sw, sy + sh // 2), (tx, ty + th // 2)
        if a[1] == b[1]:
            return [a, b]
        mid = (a[0] + b[0]) // 2
        return [a, (mid, a[1]), (mid, b[1]), b]

    # ---- XML ------------------------------------------------------------
    def element(self, nid, n):
        k, a = n["kind"], n["a"]
        nm = f" name={quoteattr(n['name'])}" if n["name"] else ""
        default = next((f"{i}" for i, f in enumerate(self.flows)
                        if f["src"] == nid and f["default"]), None)
        dflt = f' default="Flow_{default}"' if default is not None else ""
        if k == "start":
            return f'<bpmn:startEvent id="{nid}"{nm} />'
        if k == "end":
            return f'<bpmn:endEvent id="{nid}"{nm} />'
        if k == "xor":
            return f'<bpmn:exclusiveGateway id="{nid}"{nm}{dflt} />'
        if k == "evgw":
            return f'<bpmn:eventBasedGateway id="{nid}"{nm} />'
        if k == "msg":
            self.messages[a["message"]] = f"Msg_{a['message']}"
            return (f'<bpmn:intermediateCatchEvent id="{nid}"{nm}><bpmn:messageEventDefinition '
                    f'messageRef="Msg_{a["message"]}" /></bpmn:intermediateCatchEvent>')
        if k == "timer":
            return (f'<bpmn:intermediateCatchEvent id="{nid}"{nm}><bpmn:timerEventDefinition>'
                    f'<bpmn:timeDuration xsi:type="bpmn:tFormalExpression">{escape(a["duration"])}'
                    f'</bpmn:timeDuration></bpmn:timerEventDefinition></bpmn:intermediateCatchEvent>')
        if k == "ext":
            io = ""
            if a.get("inputs"):
                params = "".join(f'<camunda:inputParameter name="{p}">{escape(v)}</camunda:inputParameter>'
                                 for p, v in a["inputs"].items())
                io = f"<bpmn:extensionElements><camunda:inputOutput>{params}</camunda:inputOutput></bpmn:extensionElements>"
            return (f'<bpmn:serviceTask id="{nid}"{nm} camunda:type="external" '
                    f'camunda:topic="{a["topic"]}">{io}</bpmn:serviceTask>')
        if k == "user":
            fields = "".join(
                f'<camunda:formField id="{f[0]}" label="{f[1]}" type="{f[2]}"'
                + (f' defaultValue="{f[3]}"' if len(f) > 3 else "") + " />" for f in a.get("fields", []))
            return (f'<bpmn:userTask id="{nid}"{nm} camunda:candidateGroups="{a["group"]}">'
                    f'<bpmn:extensionElements><camunda:formData>{fields}</camunda:formData>'
                    f'</bpmn:extensionElements></bpmn:userTask>')
        if k == "dmn":
            return (f'<bpmn:businessRuleTask id="{nid}"{nm} camunda:decisionRef="{a["decision"]}" '
                    f'camunda:resultVariable="{a["result"]}" camunda:mapDecisionResult="singleResult"{dflt} />')
        if k == "call":
            return (f'<bpmn:callActivity id="{nid}"{nm} calledElement="{a["called"]}" '
                    f'camunda:calledElementBinding="latest"><bpmn:extensionElements>'
                    f'<camunda:in variables="all" /></bpmn:extensionElements></bpmn:callActivity>')
        raise ValueError(k)

    def xml(self):
        body = [self.element(i, n) for i, n in self.nodes.items()]
        for i, f in enumerate(self.flows):
            lab = f" name={quoteattr(f['label'])}" if f["label"] else ""
            cond = (f'<bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">{escape(f["cond"])}'
                    f"</bpmn:conditionExpression>") if f["cond"] else ""
            body.append(f'<bpmn:sequenceFlow id="Flow_{i}"{lab} sourceRef="{f["src"]}" targetRef="{f["tgt"]}">{cond}</bpmn:sequenceFlow>')
        shapes = []
        for i, n in self.nodes.items():
            x, y, w, h = self.box(i)
            mk = ' isMarkerVisible="true"' if n["kind"] == "xor" else ""
            shapes.append(f'<bpmndi:BPMNShape id="{i}_di" bpmnElement="{i}"{mk}><dc:Bounds x="{x}" y="{y}" width="{w}" height="{h}" /></bpmndi:BPMNShape>')
        for i, f in enumerate(self.flows):
            pts = "".join(f'<di:waypoint x="{x}" y="{y}" />' for x, y in self.waypoints(f["src"], f["tgt"]))
            shapes.append(f'<bpmndi:BPMNEdge id="Flow_{i}_di" bpmnElement="Flow_{i}">{pts}</bpmndi:BPMNEdge>')
        msgs = "".join(f'<bpmn:message id="{mid}" name="{name}" />' for name, mid in self.messages.items())
        return f'''<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:camunda="http://camunda.org/schema/1.0/bpmn" id="Defs_{self.pid}" targetNamespace="http://insurance-workflow/bpmn">
{msgs}<bpmn:process id="{self.pid}" name={quoteattr(self.name)} isExecutable="true" camunda:historyTimeToLive="P365D">
{chr(10).join(body)}
</bpmn:process>
<bpmndi:BPMNDiagram id="Diagram_{self.pid}"><bpmndi:BPMNPlane id="Plane_{self.pid}" bpmnElement="{self.pid}">
{chr(10).join(shapes)}
</bpmndi:BPMNPlane></bpmndi:BPMNDiagram>
</bpmn:definitions>
'''


def policy_issuance():
    p = Proc("policy_issuance", "Policy issuance")
    p.node("start", "start", "Application received", 0, 1)
    p.node("validate", "ext", "Validate application", 1, 1, topic="validate-application")
    p.node("g_valid", "xor", "Valid?", 2, 1)
    p.node("rate", "ext", "Actuarial risk rating", 3, 1, topic="rate-risk")
    p.node("uw", "dmn", "Underwriting rules (DMN)", 4, 1, decision="underwriting", result="uw")
    p.node("g_uw", "xor", "Decision", 5, 1)
    p.node("manual", "user", "Manual underwriting", 6, 0, group="underwriters",
           fields=[("approved", "Approve policy?", "boolean"), ("loadingPct", "Extra loading %", "long")])
    p.node("g_man", "xor", "Approved?", 7, 0)
    p.node("premium", "ext", "Calculate premium", 8, 1, topic="calculate-premium",
           inputs={"loadingPct": "${uw.loadingPct}"})
    p.node("issue", "ext", "Issue policy", 9, 1, topic="issue-policy")
    p.node("docs", "ext", "Send policy documents", 10, 1, topic="send-documents")
    p.node("collect", "call", "Collect premium", 11, 1, called="premium_collection")
    p.node("end_ok", "end", "Policy in force", 12, 1)
    p.node("reject", "ext", "Notify applicant of rejection", 6, 2, topic="notify-applicant",
           inputs={"uwDecision": "${execution.hasVariable('uw') ? uw.decision : 'invalid'}"})
    p.node("end_no", "end", "Application rejected", 7, 2)
    p.flow("start", "validate")
    p.flow("validate", "g_valid")
    p.flow("g_valid", "rate", "${valid}", "yes")
    p.flow("rate", "uw")
    p.flow("g_valid", "reject", default=True, label="no")
    p.flow("uw", "g_uw")
    p.flow("g_uw", "reject", "${uw.decision == 'decline'}", "decline")
    p.flow("g_uw", "manual", "${uw.decision == 'refer'}", "refer")
    p.flow("g_uw", "premium", default=True, label="auto-approve")
    p.flow("manual", "g_man")
    p.flow("g_man", "premium", "${approved}", "approved")
    p.flow("g_man", "reject", default=True, label="declined")
    p.flow("premium", "issue")
    p.flow("issue", "docs")
    p.flow("docs", "collect")
    p.flow("collect", "end_ok")
    p.flow("reject", "end_no")
    return p


def premium_collection():
    p = Proc("premium_collection", "Premium collection & payment tracking")
    p.node("start", "start", "Policy issued", 0, 0)
    p.node("invoice", "ext", "Raise invoice", 1, 0, topic="create-invoice")
    p.node("w1", "evgw", "Await payment", 2, 0)
    p.node("paid1", "msg", "Payment received", 3, 0, message="PaymentReceived")
    p.node("due", "timer", "Due date passes", 3, 1, duration="${dueIn}")
    p.node("remind", "ext", "Send reminder", 4, 1, topic="send-reminder")
    p.node("w2", "evgw", "Grace period", 5, 1)
    p.node("paid2", "msg", "Payment received (late)", 6, 0, message="PaymentReceived")
    p.node("grace", "timer", "Grace expires", 6, 2, duration="${graceIn}")
    p.node("lapse", "ext", "Lapse policy", 7, 2, topic="lapse-policy")
    p.node("recon", "ext", "Reconcile payment", 7, 0, topic="reconcile-payment")
    p.node("end_paid", "end", "Premium paid", 8, 0)
    p.node("end_lapsed", "end", "Policy lapsed", 8, 2)
    for a, b in [("start", "invoice"), ("invoice", "w1"), ("w1", "paid1"), ("w1", "due"),
                 ("due", "remind"), ("remind", "w2"), ("w2", "paid2"), ("w2", "grace"),
                 ("grace", "lapse"), ("lapse", "end_lapsed"), ("paid1", "recon"),
                 ("paid2", "recon"), ("recon", "end_paid")]:
        p.flow(a, b)
    return p


def claim_handling():
    p = Proc("claim_handling", "Claim handling & payout tracking")
    p.node("start", "start", "Claim filed (FNOL)", 0, 1)
    p.node("verify", "ext", "Verify coverage", 1, 1, topic="verify-coverage")
    p.node("g_cov", "xor", "Covered?", 2, 1)
    p.node("fraud", "ext", "Fraud score", 3, 1, topic="fraud-score")
    p.node("aidocs", "ext", "AI: classify & extract documents", 4, 1, topic="ai-process-documents")
    p.node("aitriage", "ext", "AI: triage recommendation", 5, 1, topic="ai-triage")
    p.node("triage", "dmn", "Triage rules (DMN)", 6, 1, decision="claims-triage", result="triage")
    p.node("g_route", "xor", "Route", 7, 1)
    p.node("adjuster", "user", "Adjuster assessment", 8, 0, group="adjusters",
           fields=[("claimSummary", "AI claim summary", "string", "${claimSummary}"), ("aiRoute", "AI recommended route", "string", "${aiRoute}"), ("approved", "Approve claim?", "boolean"), ("approvedAmount", "Approved amount", "long")])
    p.node("siu", "user", "SIU investigation", 8, 2, group="siu",
           fields=[("claimSummary", "AI claim summary", "string", "${claimSummary}"), ("aiRoute", "AI recommended route", "string", "${aiRoute}"), ("approved", "Approve claim?", "boolean"), ("approvedAmount", "Approved amount", "long")])
    p.node("g_appr", "xor", "Approved?", 9, 1)
    p.node("settle", "ext", "Settle claim (create payout)", 10, 1, topic="settle-claim")
    p.node("w", "evgw", "Await payout confirmation", 11, 1)
    p.node("conf", "msg", "Payout confirmed", 12, 1, message="PayoutConfirmed")
    p.node("sla", "timer", "Payout SLA breached", 12, 2, duration="${payoutSla}")
    p.node("esc", "ext", "Escalate payout", 13, 2, topic="escalate-payout")
    p.node("conf2", "msg", "Payout confirmed (late)", 14, 2, message="PayoutConfirmed")
    p.node("close", "ext", "Close claim & notify", 15, 1, topic="close-claim")
    p.node("end_paid", "end", "Claim paid", 16, 1)
    p.node("reject", "ext", "Notify claimant of rejection", 5, 3, topic="reject-claim")
    p.node("end_rej", "end", "Claim rejected", 6, 3)
    for a, b, c, l, d in [
        ("start", "verify", None, "", False), ("verify", "g_cov", None, "", False),
        ("g_cov", "fraud", "${covered}", "covered", False), ("g_cov", "reject", None, "not covered", True),
        ("fraud", "aidocs", None, "", False), ("aidocs", "aitriage", None, "", False),
        ("aitriage", "triage", None, "", False), ("triage", "g_route", None, "", False),
        ("g_route", "adjuster", "${triage.route == 'adjuster'}", "adjuster", False),
        ("g_route", "siu", "${triage.route == 'siu'}", "siu", False),
        ("g_route", "settle", None, "fast-track", True),
        ("adjuster", "g_appr", None, "", False), ("siu", "g_appr", None, "", False),
        ("g_appr", "settle", "${approved}", "approved", False), ("g_appr", "reject", None, "denied", True),
        ("settle", "w", None, "", False), ("w", "conf", None, "", False), ("w", "sla", None, "", False),
        ("sla", "esc", None, "", False), ("esc", "conf2", None, "", False),
        ("conf", "close", None, "", False), ("conf2", "close", None, "", False),
        ("close", "end_paid", None, "", False), ("reject", "end_rej", None, "", False)]:
        p.flow(a, b, c, l, d)
    return p


def customer_onboarding():
    p = Proc("customer_onboarding", "Customer data ingestion & profile creation")
    p.node("start", "start", "Data file received", 0, 1)
    p.node("ingest", "ext", "Ingest, validate & hash PII", 1, 1, topic="ingest-customers")
    p.node("g_q", "xor", "Reject rate OK?", 2, 1)
    p.node("notify", "ext", "Notify data owner", 3, 2, topic="notify-owner",
           inputs={"rejectRate": "${rejectRate}"})
    p.node("end_bad", "end", "Batch rejected", 4, 2)
    p.node("resolve", "ext", "Entity resolution (dedupe)", 3, 1, topic="resolve-entities")
    p.node("g_rev", "xor", "Pairs to review?", 4, 1)
    p.node("steward", "user", "Data steward review", 5, 0, group="data-stewards",
           fields=[("reviewSample", "Candidate duplicates", "string", "${reviewSample}"), ("mergeAll", "Merge all pending pairs?", "boolean")])
    p.node("apply", "ext", "Apply steward decisions", 6, 0, topic="apply-steward-decisions")
    p.node("profiles", "ext", "Build customer 360 profiles & segments", 7, 1, topic="build-profiles")
    p.node("end_ok", "end", "Profiles ready", 8, 1)
    p.flow("start", "ingest"); p.flow("ingest", "g_q")
    p.flow("g_q", "resolve", "${rejectRate <= 0.2}", "ok")
    p.flow("g_q", "notify", default=True, label="too many rejects")
    p.flow("notify", "end_bad")
    p.flow("resolve", "g_rev")
    p.flow("g_rev", "steward", "${reviewPending > 0}", "yes")
    p.flow("g_rev", "profiles", default=True, label="no")
    p.flow("steward", "apply"); p.flow("apply", "profiles"); p.flow("profiles", "end_ok")
    return p


def portfolio_risk_review():
    p = Proc("portfolio_risk_review", "Portfolio risk review for underwriting")
    p.node("start", "start", "Review cycle starts", 0, 1)
    p.node("metrics", "ext", "Exposure, VaR/TVaR, concentration", 1, 1, topic="portfolio-metrics")
    p.node("reserve", "ext", "Reserving (chain-ladder / BF)", 2, 1, topic="run-reserving")
    p.node("limits", "dmn", "Risk-appetite limits (DMN)", 3, 1, decision="portfolio-limits", result="limits")
    p.node("g", "xor", "Breach?", 4, 1)
    p.node("cro", "ext", "Escalate to CRO", 5, 0, topic="notify-owner",
           inputs={"notifyMessage": "${metricsSummary}"})
    p.node("committee", "user", "Underwriting committee review", 6, 1, group="uw-committee",
           fields=[("metricsSummary", "Portfolio metrics", "string", "${metricsSummary}"),
                   ("committeeDecision", "Decision / guideline change", "string")])
    p.node("guide", "ext", "Update underwriting guidelines", 7, 1, topic="update-guidelines")
    p.node("end_ok", "end", "Within appetite", 5, 2)
    p.node("end_done", "end", "Guidelines updated", 8, 1)
    p.flow("start", "metrics"); p.flow("metrics", "reserve"); p.flow("reserve", "limits"); p.flow("limits", "g")
    p.flow("g", "cro", "${limits.decision == 'escalate'}", "escalate")
    p.flow("g", "committee", "${limits.decision == 'review'}", "review")
    p.flow("g", "end_ok", default=True, label="ok")
    p.flow("cro", "committee"); p.flow("committee", "guide"); p.flow("guide", "end_done")
    return p


def records_migration():
    p = Proc("records_migration", "Records migration with AI classification and review")
    p.node("start", "start", "Legacy library exported", 0, 1)
    p.node("analyse", "ext", "AI classify, ROT, PII, retention", 1, 1, topic="records-analyse")
    p.node("g_rev", "xor", "Items to review?", 2, 1)
    p.node("review", "user", "Records manager review", 3, 0, group="records-managers",
           fields=[("analysisSummary", "Analysis", "string", "${analysisSummary}"), ("applyRecommended", "Apply recommended dispositions?", "boolean"),
                   ("reviewer", "Reviewer name", "string")])
    p.node("apply", "ext", "Apply decisions (audited)", 4, 0, topic="records-apply-decisions")
    p.node("final", "ext", "Manifest, preflight, readiness report", 5, 1, topic="records-finalize")
    p.node("g_ready", "xor", "Ready to migrate?", 6, 1)
    p.node("end_ok", "end", "Manifest released", 7, 1)
    p.node("block", "ext", "Notify records owner", 7, 2, topic="notify-owner", inputs={"notifyMessage": "${analysisSummary}"})
    p.node("end_no", "end", "Blocked", 8, 2)
    p.flow("start", "analyse"); p.flow("analyse", "g_rev")
    p.flow("g_rev", "review", "${reviewCount > 0}", "yes"); p.flow("g_rev", "final", default=True, label="no")
    p.flow("review", "apply"); p.flow("apply", "final"); p.flow("final", "g_ready")
    p.flow("g_ready", "end_ok", "${migrationReady}", "ready"); p.flow("g_ready", "block", default=True, label="not ready")
    p.flow("block", "end_no")
    return p


def dmn(did, name, inputs, outputs, rules):
    ins = "".join(
        f'<input id="in_{did}_{i}" label="{l}"><inputExpression id="ie_{did}_{i}" typeRef="{t}"><text>{e}</text></inputExpression></input>'
        for i, (l, e, t) in enumerate(inputs))
    outs = "".join(f'<output id="out_{did}_{i}" label="{n}" name="{n}" typeRef="{t}" />'
                   for i, (n, t) in enumerate(outputs))
    rows = ""
    for r, (conds, results) in enumerate(rules):
        c = "".join(f"<inputEntry id=\"ic_{did}_{r}_{i}\"><text>{escape(x)}</text></inputEntry>" for i, x in enumerate(conds))
        o = "".join(f"<outputEntry id=\"oc_{did}_{r}_{i}\"><text>{escape(x)}</text></outputEntry>" for i, x in enumerate(results))
        rows += f'<rule id="r_{did}_{r}">{c}{o}</rule>'
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="https://www.omg.org/spec/DMN/20191111/MODEL/" xmlns:camunda="http://camunda.org/schema/1.0/dmn" id="Defs_{did}" name="{name}" namespace="http://insurance-workflow/dmn">
<decision id="{did}" name="{name}" camunda:historyTimeToLive="P365D"><decisionTable id="dt_{did}" hitPolicy="FIRST">{ins}{outs}{rows}</decisionTable></decision>
</definitions>
'''


def main():
    OUT.mkdir(exist_ok=True)
    for p in (policy_issuance(), premium_collection(), claim_handling(), customer_onboarding(), portfolio_risk_review(), records_migration()):
        (OUT / f"{p.pid}.bpmn").write_text(p.xml())
    (OUT / "underwriting.dmn").write_text(dmn(
        "underwriting", "Underwriting",
        [("Age", "age", "integer"), ("Sum insured", "sumInsured", "long"), ("Smoker", "smoker", "boolean"),
         ("Rating class", "ratingClass", "string")],
        [("decision", "string"), ("loadingPct", "integer")],
        [(["", "", "", '"decline"'], ['"decline"', "0"]),
         (["<18,>70", "", "", ""], ['"decline"', "0"]),
         (["", "", "", '"refer"'], ['"refer"', "0"]),
         (["", ">10000000", "", ""], ['"refer"', "0"]),
         ([">50", "", "true", ""], ['"refer"', "0"]),         # mortality loading is priced via ratingClass multiple,
         (["", "", "true", ""], ['"auto_approve"', "0"]),    # not here (avoids double-counting smoker/age)
         ([">50", "", "false", ""], ['"auto_approve"', "0"]),
         (["", "", "", ""], ['"auto_approve"', "0"])]))
    (OUT / "portfolio-limits.dmn").write_text(dmn(
        "portfolio-limits", "Portfolio limits",
        [("VaR % own funds", "var995PctOwnFunds", "double"), ("Max state share %", "maxStateSharePct", "double"),
         ("Expected loss ratio", "expectedLossRatio", "double")],
        [("decision", "string")],
        [([">25", "", ""], ['"escalate"']), ([">15", "", ""], ['"review"']), (["", ">30", ""], ['"review"']),
         (["", "", ">0.8"], ['"review"']), (["", "", ""], ['"ok"'])]))
    (OUT / "claims-triage.dmn").write_text(dmn(
        "claims-triage", "Claims triage",
        [("Claim amount", "claimAmount", "long"), ("Fraud score", "fraudScore", "integer")],
        [("route", "string")],
        [(["", ">=70"], ['"siu"']),
         ([">100000", ""], ['"adjuster"']),
         (["<=10000", "<30"], ['"fast_track"']),
         (["", ""], ['"adjuster"'])]))
    print("models written to", OUT)


if __name__ == "__main__":
    main()
