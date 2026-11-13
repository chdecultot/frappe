# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# License: MIT. See LICENSE

"""Actions that set a job_queue run in a job of their own, apart from the drain."""

import json
from unittest.mock import patch

import frappe
from frappe.automation_engine.actions.base import AutomationAction, get_action_registry
from frappe.automation_engine.drainer import (
	claim_batch,
	execute_batch,
	requeue_stale_running,
	run_step_job,
)
from frappe.automation_engine.queue import HANDED_OFF, QUEUE, clear_effects
from frappe.automation_engine.registry import clear_automation_cache
from frappe.automation_engine.runner import RUN, execute_automation, step_job_id
from frappe.automation_engine.settings import skip_automations
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now

STEP_JOB = "frappe.automation_engine.drainer.run_step_job"


class SlowAction(AutomationAction):
	"""Stands in for an action that waits seconds on a remote model."""

	action_type = "SlowTestAction"
	job_queue = "long"
	transactional = False
	calls: list = []  # noqa: RUF012
	error: str | None = None

	def execute(self, doc, params, context):
		SlowAction.calls.append(doc.name)
		if SlowAction.error:
			raise ValueError(SlowAction.error)
		return {"detail": "answered", "answer": "42"}


def set_field(field, value, **kwargs):
	return {"action_type": "SetFieldValue", "params": json.dumps({"field": field, "value": value}), **kwargs}


def ask_step():
	return {"action_type": "SlowTestAction", "params": "{}", "step_key": "ask"}


class TestStepJob(IntegrationTestCase):
	# The drain and the step job commit, so these tests clean up explicitly.

	def setUp(self):
		frappe.set_user("Administrator")
		SlowAction.calls = []
		SlowAction.error = None
		frappe.local.automation_actions = {**get_action_registry(), "SlowTestAction": SlowAction()}
		self.enqueue = self.enterContext(patch.object(frappe, "enqueue"))
		self.addCleanup(self.cleanup)

	def cleanup(self):
		frappe.db.rollback()
		for name in frappe.get_all(QUEUE, pluck="name"):
			clear_effects(name)
		frappe.db.delete(QUEUE)
		for doctype in (RUN, "Automation Flow"):
			for name in frappe.get_all(doctype, pluck="name"):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		frappe.db.delete("ToDo", {"description": ("like", "step-job%")})
		frappe.db.commit()  # nosemgrep
		frappe.local.automation_actions = None
		clear_automation_cache()

	def make_flow(self, actions, stop_on_error=1):
		flow = frappe.new_doc("Automation Flow")
		flow.update(
			{"title": "Step Job", "trigger_type": "Doc Created", "document_type": "ToDo", "enabled": 1}
		)
		flow.stop_on_error = stop_on_error
		for action in actions:
			flow.append("actions", action)
		return flow.insert().name

	def queue(self, flow, description):
		with skip_automations():
			todo = frappe.get_doc({"doctype": "ToDo", "description": description}).insert()
		frappe.get_doc(
			{
				"doctype": QUEUE,
				"automation": flow,
				"ref_doctype": "ToDo",
				"ref_name": todo.name,
				"status": "Pending",
				"triggered_at": now(),
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()  # nosemgrep
		return todo.name

	def drain_once(self):
		execute_batch(execute_automation, claim_batch())

	def run_of(self, flow):
		return frappe.get_doc(RUN, {"automation": flow})

	def step_jobs(self):
		return [call for call in self.enqueue.call_args_list if call.args == (STEP_JOB,)]

	def handed_off(self):
		"""Queue a slow flow ahead of a fast one, and drain them together."""
		self.slow_flow = self.make_flow([ask_step(), set_field("description", "{{ steps.ask.answer }}")])
		self.fast_flow = self.make_flow([set_field("priority", "Low")])
		self.slow_todo = self.queue(self.slow_flow, "step-job slow")
		self.fast_todo = self.queue(self.fast_flow, "step-job fast")
		self.drain_once()
		return self.run_of(self.slow_flow)

	def test_slow_step_does_not_hold_up_an_unrelated_row(self):
		run = self.handed_off()

		self.assertEqual(SlowAction.calls, [])
		self.assertEqual(frappe.db.get_value("ToDo", self.fast_todo, "priority"), "Low")
		self.assertEqual(self.run_of(self.fast_flow).status, "Success")
		self.assertEqual(run.status, "Waiting")
		self.assertEqual(frappe.db.count(QUEUE, {"resume_run": run.name, "status": HANDED_OFF}), 1)

	def test_hand_off_enqueues_one_deduplicated_job_on_the_action_queue(self):
		run = self.handed_off()

		[job] = self.step_jobs()
		self.assertEqual(job.kwargs["queue"], "long")
		self.assertEqual(job.kwargs["job_id"], step_job_id(run.name, 0))
		self.assertTrue(job.kwargs["deduplicate"])
		self.assertTrue(job.kwargs["enqueue_after_commit"])
		self.assertEqual((job.kwargs["run"], job.kwargs["step_idx"]), (run.name, 0))

	def test_drain_leaves_a_handed_off_row_to_its_job(self):
		self.handed_off()
		self.assertEqual(claim_batch(), [])

	def test_step_output_reaches_the_next_step(self):
		run = self.handed_off()

		run_step_job(run.name, 0)
		self.assertEqual(SlowAction.calls, [self.slow_todo])
		run.reload()
		self.assertEqual(run.status, "Waiting")
		self.assertEqual(json.loads(run.steps[0].output)["answer"], "42")

		self.drain_once()
		run.reload()
		self.assertEqual(run.status, "Success")
		self.assertEqual([step.status for step in run.steps], ["Success", "Success"])
		self.assertEqual(frappe.db.get_value("ToDo", self.slow_todo, "description"), "42")
		self.assertFalse(frappe.db.count(QUEUE, {"resume_run": run.name}))

	def test_last_step_finishes_the_run_in_the_job(self):
		flow = self.make_flow([ask_step()])
		self.queue(flow, "step-job last")
		self.drain_once()

		run_step_job(self.run_of(flow).name, 0)
		self.assertEqual(self.run_of(flow).status, "Success")
		self.assertFalse(frappe.db.count(QUEUE))

	def test_failed_step_stops_the_run_on_error(self):
		SlowAction.error = "model unavailable"
		run = self.handed_off()

		run_step_job(run.name, 0)
		run.reload()
		self.assertEqual(run.status, "Failed")
		self.assertEqual(run.steps[0].status, "Failed")
		self.assertIn("model unavailable", run.error_summary)
		self.assertEqual(frappe.db.count(QUEUE, {"resume_run": run.name, "status": "Failed"}), 1)
		self.assertEqual(frappe.db.get_value("ToDo", self.slow_todo, "description"), "step-job slow")

	def test_failed_step_without_stop_on_error_ends_partially_failed(self):
		SlowAction.error = "model unavailable"
		flow = self.make_flow([ask_step(), set_field("priority", "High")], stop_on_error=0)
		todo = self.queue(flow, "step-job partial")
		self.drain_once()

		run_step_job(self.run_of(flow).name, 0)
		self.drain_once()
		run = self.run_of(flow)
		self.assertEqual(run.status, "Partially Failed")
		self.assertEqual([step.status for step in run.steps], ["Failed", "Success"])
		self.assertEqual(frappe.db.get_value("ToDo", todo, "priority"), "High")

	def test_second_job_for_the_same_step_does_nothing(self):
		run = self.handed_off()

		run_step_job(run.name, 0)
		run_step_job(run.name, 0)
		self.assertEqual(SlowAction.calls, [self.slow_todo])

	def test_lost_job_is_handed_off_again_under_the_same_job_id(self):
		run = self.handed_off()
		stale = add_to_date(now(), hours=-2)
		frappe.db.set_value(QUEUE, {"resume_run": run.name}, "modified", stale, update_modified=False)
		requeue_stale_running()
		frappe.db.commit()  # nosemgrep

		self.drain_once()
		self.assertEqual(frappe.db.count(QUEUE, {"resume_run": run.name, "status": HANDED_OFF}), 1)
		self.assertEqual({job.kwargs["job_id"] for job in self.step_jobs()}, {step_job_id(run.name, 0)})

		run_step_job(run.name, 0)
		self.assertEqual(SlowAction.calls, [self.slow_todo])

	def test_trial_runs_the_step_inline(self):
		flow = self.make_flow([ask_step()])
		todo = self.queue(flow, "step-job trial")
		row = frappe.db.get_value(QUEUE, {"ref_name": todo}, "name")
		frappe.flags.in_automation_trial = True
		try:
			execute_automation(row)
		finally:
			frappe.flags.in_automation_trial = False
		self.assertEqual(SlowAction.calls, [todo])
		self.assertEqual(self.step_jobs(), [])
