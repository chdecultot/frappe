# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: MIT. See LICENSE

"""Wait for Approval: a step that parks the run until a person approves or rejects it.

It fails closed. Only an approval, given in time by a person allowed to decide, lets the
steps after it run. A rejection, a timeout or a test run ends the branch the step sits in,
or the run when the step is at the top level.
"""

import frappe
from frappe import _
from frappe.automation_engine.dispatch import kick_drainer
from frappe.automation_engine.events import SUBSCRIPTION, TIMEOUT_UNITS, _claim_subscription
from frappe.permissions import rights
from frappe.utils import add_to_date, cint, now

APPROVAL_EVENT = "automation_engine.approval"
APPROVE = "Approve"
REJECT = "Reject"
# A wait without a timeout. Its resume row is never due, so only a decision resumes the run.
NO_EXPIRY = "9999-12-31 23:59:59"


def validate_approval_params(params: dict):
	if not params.get("approver_role") or not frappe.db.exists("Role", params["approver_role"]):
		frappe.throw(_("Wait for Approval requires an existing Approver Role"))
	if cint(params.get("timeout_value")) and params.get("timeout_unit") not in TIMEOUT_UNITS:
		frappe.throw(_("Wait for Approval timeout unit must be one of {0}").format(", ".join(TIMEOUT_UNITS)))
	for pair in params.get("required_permissions") or []:
		if not (
			isinstance(pair, list)
			and len(pair) == 2
			and frappe.db.exists("DocType", pair[0])
			and pair[1] in rights
		):
			frappe.throw(_("List the required permissions as [DocType, permission] pairs"))


def request_approval(context: dict, params: dict, step_key: str, resume_from_idx: int) -> dict:
	"""Park the run behind a subscription that only `decide`, or the timeout, settles.

	Returns the step output: who may decide, kept in the run state for `decide` to check."""
	# Deferred: runner imports this module at load time.
	from frappe.automation_engine.runner import resume_row_values

	timeout = cint(params.get("timeout_value")) * TIMEOUT_UNITS.get(params.get("timeout_unit"), 0)
	expires_at = add_to_date(now(), seconds=timeout) if timeout else NO_EXPIRY
	queue = frappe.get_doc(resume_row_values(context, expires_at, resume_from_idx)).insert(
		ignore_permissions=True
	)
	frappe.get_doc(
		{
			"doctype": SUBSCRIPTION,
			"event_name": APPROVAL_EVENT,
			# Random, so that no `emit` call can settle the wait in place of `decide`.
			"correlation_key": frappe.generate_hash(length=24),
			"run": context["run"].name,
			"step_key": step_key,
			"resume_queue": queue.name,
			"expires_at": expires_at,
			"status": "Waiting",
		}
	).insert(ignore_permissions=True)
	return {
		"detail": _("Waiting for approval by {0}").format(_(params["approver_role"])),
		"approver_role": params["approver_role"],
		"required_permissions": params.get("required_permissions") or [],
		"allow_self_approval": cint(params.get("allow_self_approval")),
		"requested_by": context["queue_row"].triggered_by,
		"expires_at": str(expires_at) if timeout else None,
	}


def approval_outcome(event: dict, request: dict) -> dict:
	"""The step output once the wait is settled. Anything but an approval is a rejection."""
	payload = event.get("payload") or {}
	approved = event.get("outcome") == "Matched" and payload.get("decision") == APPROVE
	if approved:
		detail = _("Approved by {0}").format(payload.get("decided_by"))
	elif event.get("outcome") == "Matched":
		detail = _("Rejected by {0}").format(payload.get("decided_by"))
	else:
		detail = _("No decision before the timeout")
	return {
		**request,
		"detail": detail,
		"approved": approved,
		"decision": payload.get("decision") or REJECT,
		"decided_by": payload.get("decided_by"),
		"comment": payload.get("comment"),
	}


@frappe.whitelist(methods=["POST"])
def decide(run: str, step_key: str, decision: str, comment: str | None = None) -> dict:
	"""Approve or reject the approval step `step_key` that run `run` is waiting on."""
	if decision not in (APPROVE, REJECT):
		frappe.throw(_("Decision must be {0} or {1}").format(APPROVE, REJECT))
	approval = PendingApproval.load(run, step_key)
	approval.check_decider()
	approval.settle(decision, (comment or "").strip()[:1000])
	return {"run": run, "step_key": step_key, "decision": decision}


class PendingApproval:
	def __init__(self, subscription: str, run, request: dict):
		self.subscription = subscription
		self.run = run
		self.request = request

	@classmethod
	def load(cls, run_name: str, step_key: str) -> "PendingApproval":
		subscription = frappe.db.get_value(
			SUBSCRIPTION,
			{"run": run_name, "step_key": step_key, "event_name": APPROVAL_EVENT, "status": "Waiting"},
		)
		if not subscription:
			frappe.throw(_("No approval is waiting for this step"), frappe.DoesNotExistError)
		run = frappe.db.get_value(
			"Automation Run", run_name, ["reference_doctype", "reference_name", "run_state"], as_dict=True
		)
		state = frappe.parse_json(run.run_state) if run.run_state else {}
		return cls(subscription, run, (state.get("step_outputs") or {}).get(step_key) or {})

	def check_decider(self):
		"""The role, the declared permissions, write access to the document, and not one's own run."""
		role = self.request.get("approver_role")
		# A request that lost its role cannot be decided: it fails closed.
		if not role or role not in frappe.get_roles():
			frappe.throw(
				_("Only a user with the {0} role can decide").format(_(role or "")), frappe.PermissionError
			)
		for doctype, ptype in self.request.get("required_permissions") or []:
			if not frappe.has_permission(doctype, ptype):
				frappe.throw(
					_("Deciding needs the {0} permission on {1}").format(_(ptype), _(doctype)),
					frappe.PermissionError,
				)
		if self.run.reference_doctype and self.run.reference_name:
			frappe.get_doc(self.run.reference_doctype, self.run.reference_name).check_permission("write")
		if self.request.get("requested_by") == frappe.session.user and not self.request.get(
			"allow_self_approval"
		):
			frappe.throw(_("You started this run, so another user must decide"), frappe.PermissionError)

	def settle(self, decision: str, comment: str):
		payload = {"decision": decision, "decided_by": frappe.session.user, "comment": comment}
		if not _claim_subscription(self.subscription, payload):
			frappe.throw(_("This approval has expired"))
		frappe.db.after_commit.add(kick_drainer)
