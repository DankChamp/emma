from __future__ import annotations
from typing import Optional
from dataclasses import dataclass

from core.orchestrator.luna_client import DelegationResult


@dataclass
class SynthesisContext:
    """Context for result synthesis."""
    user_message: str
    intent_type: str
    delegated_to: str  # "luna", "aqua"
    delegation_result: DelegationResult
    conversation_history: list[dict] = None


class ResultSynthesizer:
    """
    Convert subordinate agent results into natural Emma responses.
    
    Emma speaks in her own voice — warm, conversational, synthesizing
    technical details into accessible summaries.
    """
    
    def __init__(self):
        # Templates for different task types
        self.templates = {
            "code": {
                "success": [
                    "Luna implemented that for you. {summary}",
                    "Done! Luna wrote the code — {summary}",
                    "Luna took care of it. {summary}"
                ],
                "partial": [
                    "Luna made progress but hit a snag: {summary}",
                    "Luna got partway through. {summary} — want me to have her continue?"
                ],
                "failed": [
                    "Luna ran into an issue: {summary}",
                    "That didn't work out — Luna says: {summary}"
                ]
            },
            "debug": {
                "success": [
                    "Found it! {summary}",
                    "Luna tracked down the bug. {summary}",
                    "Fixed! The issue was: {summary}"
                ],
                "partial": [
                    "Luna found something but needs more info: {summary}",
                    "Partial progress on the debug — {summary}"
                ],
                "failed": [
                    "Luna couldn't reproduce it: {summary}",
                    "Debug hit a wall: {summary}"
                ]
            },
            "refactor": {
                "success": [
                    "Refactored! {summary}",
                    "Luna cleaned that up. {summary}",
                    "Done — {summary}"
                ],
                "partial": [
                    "Luna started the refactor but {summary}",
                    "Partway through the refactor — {summary}"
                ],
                "failed": [
                    "Refactor didn't go as planned: {summary}",
                    "Luna hit issues with the refactor: {summary}"
                ]
            },
            "git": {
                "success": [
                    "Git done. {summary}",
                    "Luna handled the git work. {summary}",
                    "Pushed and ready. {summary}"
                ],
                "partial": [
                    "Luna started but {summary}",
                    "Git operation partial: {summary}"
                ],
                "failed": [
                    "Git issue: {summary}",
                    "Luna couldn't complete the git operation: {summary}"
                ]
            }
        }
    
    def synthesize(self, ctx: SynthesisContext) -> str:
        """Generate natural Emma response from delegation result."""
        templates = self.templates.get(ctx.intent_type, self.templates["code"])
        status_templates = templates.get(ctx.delegation_result.status, templates["success"])
        
        # Pick template (could add variety logic here)
        template = status_templates[0]
        
        # Build response
        summary = ctx.delegation_result.summary or "task completed"
        response = template.format(summary=summary)
        
        # Add details if available
        details = []
        if ctx.delegation_result.files_changed:
            files = ", ".join(ctx.delegation_result.files_changed[:3])
            if len(ctx.delegation_result.files_changed) > 3:
                files += f" and {len(ctx.delegation_result.files_changed) - 3} more"
            details.append(f"Files: {files}")
        
        if ctx.delegation_result.tests_run > 0:
            details.append(f"Tests: {ctx.delegation_result.tests_passed}/{ctx.delegation_result.tests_run} passed")
        
        if ctx.delegation_result.next_steps:
            details.append(f"Next: {ctx.delegation_result.next_steps[0]}")
        
        if details:
            response += " (" + "; ".join(details) + ")"
        
        return response
    
    def synthesize_streaming_start(self, intent_type: str, delegated_to: str) -> str:
        """Initial acknowledgement when delegation starts."""
        acknowledgements = {
            "luna": [
                "I'll have Luna take a look at that.",
                "Let me ask Luna to handle this.",
                "Luna's on it.",
                "Delegating to Luna..."
            ],
            "aqua": [
                "I'll pass that to Aqua.",
                "Aqua can handle the research on that.",
                "Let me get Aqua working on it."
            ]
        }
        import random
        return random.choice(acknowledgements.get(delegated_to, acknowledgements["luna"]))
    
    def synthesize_streaming_tool(self, tool_name: str, args: dict) -> str:
        """Natural description of tool execution for streaming."""
        descriptions = {
            "write": f"Writing {args.get('path', 'file')}...",
            "edit": f"Editing {args.get('path', 'file')}...",
            "bash": f"Running command...",
            "grep": f"Searching code...",
            "glob": f"Finding files...",
            "read": f"Reading {args.get('path', 'file')}...",
        }
        return descriptions.get(tool_name, f"Using {tool_name}...")


def get_synthesizer() -> ResultSynthesizer:
    """Get singleton synthesizer instance."""
    return ResultSynthesizer()