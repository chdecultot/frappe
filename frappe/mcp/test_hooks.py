# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: MIT. See LICENSE

from unittest.mock import Mock

import frappe
from frappe.mcp import prompts, protocol, tools
from frappe.mcp.test_utils import McpTestCase
from frappe.tests import UnitTestCase


class EchoArguments(tools.ToolArguments):
	text: str


def whoami(text: str) -> dict:
	return {"user": frappe.session.user, "text": text}


def greet(name: str, tone: str = "plain") -> str:
	return f"Greet {name} in a {tone} tone."


ECHO = tools.Tool(
	name="app_echo", arguments=EchoArguments, handler=whoami, annotations={"readOnlyHint": True}
)
DUPLICATE = tools.Tool(name="get_document", arguments=EchoArguments, handler=Mock())
GREET = prompts.Prompt(
	name="greet",
	description="Write a greeting.",
	handler=greet,
	arguments=(prompts.PromptArgument("name", required=True), prompts.PromptArgument("tone")),
)

HOOKS = {"mcp_tools": [f"{__name__}.ECHO"], "mcp_prompts": [f"{__name__}.GREET"]}


class TestRegistries(UnitTestCase):
	def setUp(self):
		super().setUp()
		self.clear()
		self.addCleanup(self.clear)

	def clear(self):
		frappe.local.mcp_tools = frappe.local.mcp_prompts = None

	def test_hook_tools_follow_builtin_tools(self):
		with self.patch_hooks({"mcp_tools": [f"{__name__}.ECHO"]}):
			registry = tools.registry()
		self.assertEqual(registry[-1], ECHO)
		self.assertIs(tools.registry(), registry)

	def test_duplicate_tool_name_is_an_error(self):
		with (
			self.patch_hooks({"mcp_tools": [f"{__name__}.DUPLICATE"]}),
			self.assertRaises(frappe.ValidationError),
		):
			tools.registry()

	def test_hook_must_name_a_tool(self):
		with (
			self.patch_hooks({"mcp_tools": [f"{__name__}.whoami"]}),
			self.assertRaises(frappe.ValidationError),
		):
			tools.registry()

	def test_duplicate_prompt_name_is_an_error(self):
		hooks = {"mcp_prompts": [f"{__name__}.GREET", f"{__name__}.GREET"]}
		with self.patch_hooks(hooks), self.assertRaises(frappe.ValidationError):
			prompts.registry()

	def test_no_prompts_by_default(self):
		with self.patch_hooks({"mcp_prompts": []}):
			self.assertEqual(prompts.registry(), ())


class TestHookRequests(McpTestCase):
	def rpc(self, method, params=None):
		with self.patch_hooks(HOOKS):
			return self.request({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})

	def test_hook_tool_is_listed(self):
		listed = {tool["name"]: tool for tool in self.rpc("tools/list").json["result"]["tools"]}
		self.assertIn("get_document", listed)
		self.assertEqual(listed["app_echo"]["annotations"], {"readOnlyHint": True})

	def test_hook_tool_runs_as_request_user(self):
		result = self.rpc("tools/call", {"name": "app_echo", "arguments": {"text": "hi"}}).json["result"]
		self.assertFalse(result["isError"])
		self.assertEqual(result["structuredContent"], {"user": self.user, "text": "hi"})

	def test_prompts_capability_and_list(self):
		result = self.rpc(
			"initialize",
			{
				"protocolVersion": protocol.LEGACY_VERSION,
				"capabilities": {},
				"clientInfo": {"name": "test", "version": "1"},
			},
		).json["result"]
		self.assertEqual(result["capabilities"]["prompts"], {"listChanged": False})

		listed = self.rpc("prompts/list").json["result"]["prompts"]
		self.assertEqual(
			listed,
			[
				{
					"name": "greet",
					"description": "Write a greeting.",
					"arguments": [{"name": "name", "required": True}, {"name": "tone", "required": False}],
				}
			],
		)

	def test_prompts_get(self):
		result = self.rpc("prompts/get", {"name": "greet", "arguments": {"name": "Ada"}}).json["result"]
		self.assertEqual(
			result["messages"],
			[{"role": "user", "content": {"type": "text", "text": "Greet Ada in a plain tone."}}],
		)

	def test_prompts_get_rejects_bad_arguments(self):
		cases = (
			{"name": "missing"},
			{"name": "greet", "arguments": {}},
			{"name": "greet", "arguments": {"name": "Ada", "other": "x"}},
			{"name": "greet", "arguments": {"name": 1}},
		)
		for params in cases:
			with self.subTest(params=params):
				response = self.rpc("prompts/get", params)
				self.assertEqual(response.json["error"]["code"], protocol.INVALID_PARAMS)

	def test_no_prompts_capability_without_prompts(self):
		response = self.request(
			{
				"jsonrpc": "2.0",
				"id": 1,
				"method": "initialize",
				"params": {
					"protocolVersion": protocol.LEGACY_VERSION,
					"capabilities": {},
					"clientInfo": {"name": "test", "version": "1"},
				},
			}
		)
		self.assertNotIn("prompts", response.json["result"]["capabilities"])
