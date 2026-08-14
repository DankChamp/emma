from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional
import json


class IntentType(str, Enum):
    """Classification of user intent for orchestration routing."""
    CHAT = "chat"                    # General conversation, handled by Emma
    SCHEDULE = "schedule"            # Calendar, timetable, blocks
    TASK = "task"                    # Task management
    REMINDER = "reminder"            # Reminder management
    MEMORY = "memory"                # Memory operations
    CODE = "code"                    # Coding tasks → Luna
    DEBUG = "debug"                  # Debugging → Luna
    REFACTOR = "refactor"            # Refactoring → Luna
    GIT = "git"                      # Git operations → Luna
    RESEARCH = "research"            # Deep research → Aqua (future)
    AUTOMATION = "automation"        # Workflow automation → Aqua (future)
    MULTI_AGENT = "multi_agent"      # Requires multiple agents
    VOICE_COMMAND = "voice_command"  # Voice-specific command


class IntentClassification(BaseModel):
    """Result of intent classification."""
    primary: IntentType
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    suggested_agent: Optional[str] = None  # "emma", "luna", "aqua"
    requires_context: list[str] = []       # What context to gather
    constraints: dict = {}


# System prompt for LLM-based intent classification
INTENT_CLASSIFIER_PROMPT = """You are an intent classifier for Emma, a personal AI orchestrator.

Classify the user's message into ONE primary intent type. Return ONLY valid JSON.

Intent types:
- chat: General conversation, questions, casual talk
- schedule: Calendar, timetable, time blocks, scheduling
- task: Task creation, updates, listing, completion
- reminder: Reminder creation, dismissal, listing
- memory: Memory operations (save, recall, facts)
- code: Writing new code, implementing features
- debug: Finding/fixing bugs, troubleshooting
- refactor: Restructuring, improving existing code
- git: Git operations (commit, push, PR, branch)
- research: Deep research, knowledge synthesis (future → Aqua)
- automation: Workflow automation (future → Aqua)
- multi_agent: Explicitly requires multiple specialists
- voice_command: Voice-specific control (volume, stop, etc.)

Agent routing:
- emma: chat, schedule, task, reminder, memory, voice_command
- luna: code, debug, refactor, git
- aqua: research, automation (currently stubbed)

Rules:
1. Default to "chat" for ambiguous conversational input
2. Code-related → luna (code, debug, refactor, git)
3. Explicit multi-agent requests → multi_agent
4. Voice control words (stop, volume, pause) → voice_command
5. Be concise in reasoning

Examples:
User: "Hey Emma, how's my day looking?"
→ {"primary": "schedule", "confidence": 0.95, "reasoning": "Asking about daily schedule", "suggested_agent": "emma"}

User: "Fix the login bug in auth.py"
→ {"primary": "debug", "confidence": 0.98, "reasoning": "Explicit bug fix request in code file", "suggested_agent": "luna"}

User: "Remind me to call John at 3pm"
→ {"primary": "reminder", "confidence": 0.99, "reasoning": "Explicit reminder creation with time", "suggested_agent": "emma"}

User: "Refactor the user service to use dependency injection"
→ {"primary": "refactor", "confidence": 0.95, "reasoning": "Code restructuring request", "suggested_agent": "luna"}

User: "Create a PR for the auth changes"
→ {"primary": "git", "confidence": 0.97, "reasoning": "Git operation request", "suggested_agent": "luna"}

User: "What's the weather like?"
→ {"primary": "chat", "confidence": 0.9, "reasoning": "General knowledge question", "suggested_agent": "emma"}"""


async def classify_intent(
    message: str,
    ai_router,
    context: dict | None = None
) -> IntentClassification:
    """
    Classify user intent using LLM.
    Falls back to rule-based if LLM unavailable.
    """
    from core.router import TaskType
    
    # Build classification prompt
    system_prompt = INTENT_CLASSIFIER_PROMPT
    if context:
        context_str = json.dumps(context, indent=2)
        system_prompt += f"\n\nCurrent context:\n{context_str}"
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Classify: {message}"}
    ]
    
    try:
        # Use a fast, cheap model for classification
        # Try local first, then groq, then others
        for provider_name in ["local_generic", "ollama", "groq", "nvidia_nim"]:
            provider = ai_router.providers_by_name.get(provider_name)
            if provider and await provider.is_available():
                result = await provider.complete(
                    messages=messages,
                    temperature=0.1,
                    max_tokens=200
                )
                if result:
                    break
        else:
            raise RuntimeError("No provider available for classification")
        
        # Parse JSON from response
        content = result.strip()
        # Extract JSON if wrapped in markdown
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        
        data = json.loads(content)
        return IntentClassification(**data)
        
    except Exception as e:
        # Fallback to rule-based classification
        return _rule_based_classify(message)


def _rule_based_classify(message: str) -> IntentClassification:
    """Fallback rule-based classification."""
    msg_lower = message.lower()
    
    # Voice commands
    if any(w in msg_lower for w in ["stop", "pause", "volume", "louder", "quieter", "mute"]):
        return IntentClassification(
            primary=IntentType.VOICE_COMMAND,
            confidence=0.9,
            reasoning="Voice control keyword detected",
            suggested_agent="emma"
        )
    
    # Code/delegation keywords
    code_keywords = ["fix", "debug", "bug", "error", "exception", "crash",
                     "refactor", "rewrite", "implement", "add feature",
                     "write code", "create function", "class", "method",
                     "git", "commit", "push", "pull", "branch", "merge",
                     "pr", "pull request", "review"]
    
    if any(kw in msg_lower for kw in code_keywords):
        # Determine specific type
        if any(kw in msg_lower for kw in ["fix", "debug", "bug", "error", "exception", "crash"]):
            return IntentClassification(
                primary=IntentType.DEBUG,
                confidence=0.85,
                reasoning="Bug/fix keywords detected",
                suggested_agent="luna"
            )
        elif any(kw in msg_lower for kw in ["refactor", "rewrite", "restructure"]):
            return IntentClassification(
                primary=IntentType.REFACTOR,
                confidence=0.85,
                reasoning="Refactor keywords detected",
                suggested_agent="luna"
            )
        elif any(kw in msg_lower for kw in ["git", "commit", "push", "pull", "branch", "merge", "pr", "pull request"]):
            return IntentClassification(
                primary=IntentType.GIT,
                confidence=0.9,
                reasoning="Git operation keywords detected",
                suggested_agent="luna"
            )
        else:
            return IntentClassification(
                primary=IntentType.CODE,
                confidence=0.8,
                reasoning="General code keywords detected",
                suggested_agent="luna"
            )
    
    # Schedule
    if any(kw in msg_lower for kw in ["schedule", "calendar", "timetable", "block", "meeting", "appointment"]):
        return IntentClassification(
            primary=IntentType.SCHEDULE,
            confidence=0.85,
            reasoning="Schedule/calendar keywords",
            suggested_agent="emma"
        )
    
    # Tasks
    if any(kw in msg_lower for kw in ["task", "todo", "to-do", "add task"]):
        return IntentClassification(
            primary=IntentType.TASK,
            confidence=0.85,
            reasoning="Task management keywords",
            suggested_agent="emma"
        )
    
    # Reminders
    if any(kw in msg_lower for kw in ["remind", "reminder", "alert me", "notify me"]):
        return IntentClassification(
            primary=IntentType.REMINDER,
            confidence=0.9,
            reasoning="Reminder keywords",
            suggested_agent="emma"
        )
    
    # Memory
    if any(kw in msg_lower for kw in ["remember", "recall", "forget", "memory", "fact"]):
        return IntentClassification(
            primary=IntentType.MEMORY,
            confidence=0.8,
            reasoning="Memory operation keywords",
            suggested_agent="emma"
        )
    
    # Default to chat
    return IntentClassification(
        primary=IntentType.CHAT,
        confidence=0.6,
        reasoning="No specific intent detected, defaulting to chat",
        suggested_agent="emma"
    )