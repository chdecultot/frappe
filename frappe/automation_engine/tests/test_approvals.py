# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: MIT. See LICENSE

import json

import frappe
from frappe.automation_engine.approvals import APPROVAL_EVENT, APPROVE, NO_EXPIRY, REJECT, decide
from frappe.automation_engine.runner import execute_automation
from frappe.automation_engine.tests.test_automation_runner import (
	QUEUE,
	AutomationRunnerTestCase,
	make_automation,
	make_todo,
	set_field,
)

SUBSCRIPTION = "Automation Event Subscription"
ROLE = "Automation Approval Tester"


class TestWaitForApproval(AutomationRunnerTestCase):
	def setUp(self):
		super().setUp()
		if not frappe.db.exists("Role", ROLE):
			frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert()
		self.approver = self.make_user("approver", ROLE, "System Manager")
		self.todo = make_todo(priority="Low")

	def make_user(self, name, *roles):
		email = f"automation-{name}@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": name, "send_welcome_email": 0}
			).insert()
		user = frappe.get_doc("User", email)
		user.set("roles", [])
		user.add_roles(*roles)
		return email

	def flow(self, steps=None, **params):
		approval = {"step_type": "WaitForApproval", "params": json.dumps({"approver_role": ROLE, **params})}
		return make_automation(steps or [approval, set_field("priority", "High")])

	def start(self, automation, triggered_by=None):
		name = self.queue_row(automation, self.todo.name)
		frappe.db.set_value(QUEUE, name, "triggered_by", triggered_by)
		execute_automation(name)
		run = frappe.get_last_doc("Automation Run", {"automation": automation})
		self.assertEqual(run.status, "Waiting")
		return run

	def decide_as(self, user, run, decision, step_key="step_1", comment=None):
		frappe.set_user(user)
		try:
			return decide(run.name, step_key, decision, comment)
		finally:
			frappe.set_user("Administrator")

	def resume(self, automation):
		row = frappe.db.get_value(QUEUE, {"automation": automation, "resume_run": ("is", "set")}, "name")
		execute_automation(row)

	def priority(self):
		return frappe.db.get_value("ToDo", self.todo.name, "priority")

	def last_step(self, run, step_key=None):
		run.reload()
		return [step for step in run.steps if step_key in (None, step.step_key)][-1]

	def test_approval_runs_the_later_steps_and_records_the_decision(self):
		automation = self.flow()
		run = self.start(automation)
		self.assertEqual(self.priority(), "Low")
		self.decide_as(self.approver, run, APPROVE, comment="Looks right")
		self.resume(automation)

		self.assertEqual(self.priority(), "High")
		step = self.last_step(run, "step_1")
		self.assertEqual(step.status, "Success")
		output = json.loads(step.output)
		self.assertEqual(
			{key: output[key] for key in ("approved", "decision", "decided_by", "comment")},
			{"approved": True, "decision": APPROVE, "decided_by": self.approver, "comment": "Looks right"},
		)

	def test_rejection_ends_the_run(self):
		automation = self.flow()
		run = self.start(automation)
		self.decide_as(self.approver, run, REJECT)
		self.resume(automation)

		self.assertEqual(self.priority(), "Low")
		step = self.last_step(run)
		self.assertEqual((step.step_key, step.status), ("step_1", "Rejected"))
		self.assertFalse(json.loads(step.output)["approved"])
		self.assertEqual(run.status, "Success")

	def test_timeout_is_a_rejection(self):
		automation = self.flow(timeout_value=1, timeout_unit="Days")
		run = self.start(automation)
		self.resume(automation)

		self.assertEqual(self.priority(), "Low")
		self.assertEqual(self.last_step(run).status, "Rejected")
		self.assertEqual(frappe.db.get_value(SUBSCRIPTION, {"run": run.name}, "status"), "Timed Out")

	def test_without_a_timeout_only_a_decision_resumes_the_run(self):
		run = self.start(self.flow())
		subscription = frappe.db.get_value(
			SUBSCRIPTION, {"run": run.name}, ["event_name", "expires_at", "resume_queue"], as_dict=True
		)
		self.assertEqual(subscription.event_name, APPROVAL_EVENT)
		self.assertEqual(str(subscription.expires_at), NO_EXPIRY)
		self.assertEqual(frappe.db.get_value(QUEUE, subscription.resume_queue, "status"), "Scheduled")

	def test_rejection_inside_an_if_ends_only_that_arm(self):
		approval = {"approver_role": ROLE}
		automation = self.flow(
			[
				{"step_type": "If", "step_condition": "True"},
				{
					"step_type": "WaitForApproval",
					"params": json.dumps(approval),
					"parent_step": 1,
					"branch": "If",
				},
				{**set_field("priority", "High"), "parent_step": 1, "branch": "If"},
				set_field("status", "Closed"),
			]
		)
		run = self.start(automation)
		self.decide_as(self.approver, run, REJECT, step_key="step_2")
		self.resume(automation)

		self.assertEqual(self.priority(), "Low")
		self.assertEqual(frappe.db.get_value("ToDo", self.todo.name, "status"), "Closed")

	def test_a_test_run_is_never_approved(self):
		automation = self.flow()
		frappe.flags.in_automation_trial = True
		try:
			execute_automation(self.queue_row(automation, self.todo.name))
		finally:
			frappe.flags.in_automation_trial = False
		run = frappe.get_last_doc("Automation Run", {"automation": automation})
		self.assertEqual(self.priority(), "Low")
		self.assertEqual([step.status for step in run.steps], ["Rejected"])

	def test_only_an_allowed_decider_may_decide(self):
		run = self.start(self.flow(), triggered_by=self.approver)
		cases = (
			(self.make_user("outsider", "System Manager"), "role"),
			(self.make_user("no-write", ROLE), "write access"),
			(self.approver, "self-approval"),
		)
		for user, reason in cases:
			with self.subTest(reason), self.assertRaises(frappe.PermissionError):
				self.decide_as(user, run, APPROVE)
		self.assertEqual(frappe.db.get_value(SUBSCRIPTION, {"run": run.name}, "status"), "Waiting")

	def test_the_decider_needs_the_required_permissions(self):
		run = self.start(self.flow(required_permissions=[["Role", "create"]]))
		self.todo.db_set("allocated_to", self.make_user("no-role-create", ROLE))
		with self.assertRaises(frappe.PermissionError):
			self.decide_as("automation-no-role-create@example.com", run, APPROVE)
		self.decide_as(self.approver, run, APPROVE)

	def test_self_approval_when_the_flow_allows_it(self):
		run = self.start(self.flow(allow_self_approval=1), triggered_by=self.approver)
		self.decide_as(self.approver, run, APPROVE)
		with self.assertRaises(frappe.DoesNotExistError):
			self.decide_as(self.approver, run, REJECT)

	def test_invalid_decision_and_params_are_refused(self):
		run = self.start(self.flow())
		with self.assertRaises(frappe.ValidationError):
			self.decide_as(self.approver, run, "Maybe")
		for params in ({}, {"approver_role": "No Such Role"}, {"approver_role": ROLE, "timeout_value": 1}):
			with self.subTest(params=params), self.assertRaises(frappe.ValidationError):
				make_automation([{"step_type": "WaitForApproval", "params": json.dumps(params)}])
