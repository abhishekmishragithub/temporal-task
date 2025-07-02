from dataclasses import dataclass

@dataclass
class BotInput:
    """Input for workflow (workflow accepts the issue URL)"""
    issue_url: str

@dataclass
class RepoInfo:
    """Repo Info"""
    owner: str
    name: str

@dataclass
class PRDetails:
    """PR details."""
    url: str

@dataclass
class WorkflowResult:
    pr_url: str
