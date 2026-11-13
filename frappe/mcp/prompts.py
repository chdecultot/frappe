# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: MIT. See LICENSE
"""Prompts that apps list under the `mcp_prompts` hook. Frappe itself has none."""

from collections.abc import Callable
from dataclasses import dataclass

import frappe
from frappe.mcp.protocol import INVALID_PARAMS, McpError


@dataclass(frozen=True)
class PromptArgument:
	name: str
	description: str | None = None
	required: bool = False

	def definition(self) -> dict:
		definition = {"name": self.name, "required": self.required}
		if self.description:
			definition["description"] = self.description
		return definition


@dataclass(frozen=True)
class Prompt:
	"""The handler takes the arguments as keyword strings and returns the text of a user message,
	or a list of MCP prompt messages."""

	name: str
	handler: Callable[..., str | list[dict]]
	description: str | None = None
	arguments: tuple[PromptArgument, ...] = ()

	def definition(self) -> dict:
		definition = {"name": self.name, "arguments": [argument.definition() for argument in self.arguments]}
		if self.description:
			definition["description"] = self.description
		return definition

	def render(self, arguments: dict[str, str]) -> dict:
		messages = self.handler(**arguments)
		if isinstance(messages, str):
			messages = [{"role": "user", "content": {"type": "text", "text": messages}}]
		result = {"messages": messages}
		if self.description:
			result["description"] = self.description
		return result


def registry() -> tuple[Prompt, ...]:
	if (prompts := getattr(frappe.local, "mcp_prompts", None)) is not None:
		return prompts

	prompts = []
	for path in frappe.get_hooks("mcp_prompts"):
		prompt = frappe.get_attr(path)
		if not isinstance(prompt, Prompt):
			frappe.throw("MCP prompt {0} must be a frappe.mcp.prompts.Prompt".format(path))
		if any(existing.name == prompt.name for existing in prompts):
			frappe.throw("Duplicate MCP prompt name: {0}".format(prompt.name))
		prompts.append(prompt)

	frappe.local.mcp_prompts = tuple(prompts)
	return frappe.local.mcp_prompts


def definitions() -> list[dict]:
	return [prompt.definition() for prompt in registry()]


def get(params: dict) -> dict:
	name = params.get("name")
	prompt = next((prompt for prompt in registry() if prompt.name == name), None)
	if prompt is None:
		raise McpError(INVALID_PARAMS, "Unknown MCP prompt: {0}".format(name))
	return prompt.render(validate_arguments(prompt, params.get("arguments") or {}))


def validate_arguments(prompt: Prompt, arguments: dict) -> dict[str, str]:
	if not isinstance(arguments, dict) or not all(isinstance(value, str) for value in arguments.values()):
		raise McpError(INVALID_PARAMS, "Prompt arguments must be an object of strings")

	declared = {argument.name for argument in prompt.arguments}
	if unknown := sorted(set(arguments) - declared):
		raise McpError(INVALID_PARAMS, "Unknown prompt arguments: {0}".format(", ".join(unknown)))
	if missing := [arg.name for arg in prompt.arguments if arg.required and arg.name not in arguments]:
		raise McpError(INVALID_PARAMS, "Missing prompt arguments: {0}".format(", ".join(missing)))
	return arguments
