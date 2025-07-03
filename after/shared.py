import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

PR_BOT_TASK_QUEUE_NAME = "GITHUB_PR_BOT_QUEUE"


@dataclass(frozen=True)
class PushRequest:
    """Input for the GitHub PR Bot workflow."""

    repo_path: str
    issue_number: int

    @property
    def workflow_id(self) -> str:
        """Generate a deterministic workflow ID from the repo path and issue number."""
        content = f"{self.repo_path}-issue-{self.issue_number}"
        return f"github-pr-bot-{hashlib.sha256(content.encode()).hexdigest()[:12]}"


@dataclass(frozen=True)
class RepoInfo:
    """Repository information extracted from GitHub URL."""

    owner: str
    name: str

    @property
    def full_name(self) -> str:
        """Get the full repository name in owner/name format."""
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class IssueInfo:
    """Issue information."""

    number: int
    title: Optional[str] = None
    body: Optional[str] = None


@dataclass(frozen=True)
class ParsedIssueResult:
    """Result of parsing a GitHub issue URL."""

    repo_info: RepoInfo
    issue_info: IssueInfo


@dataclass(frozen=True)
class CloneResult(BaseModel):
    """Result of cloning a repository."""

    local_path: Path
    branch_name: str


@dataclass(frozen=True)
class AIGeneratedFixResult:
    """Result from the AI fix generation activity."""

    file_to_edit: str
    new_content: str
    commit_message: str


@dataclass(frozen=True)
class CommitResult:
    """Result of committing changes."""

    commit_hash: str
    commit_message: str


@dataclass(frozen=True)
class PushResult:
    """Result of pushing changes to remote."""

    branch_name: str
    pushed_commits: int


@dataclass(frozen=True)
class PullRequestResult:
    """Result of creating a pull request."""

    url: str
    number: int
    title: str


@dataclass(frozen=True)
class CleanupResult(BaseModel):
    """Result of cleanup operation."""

    cleaned_path: Optional[Path]
    success: bool
    message: str


@dataclass(frozen=True)
class WorkflowResult:
    """Final result of the GitHub PR Bot workflow."""

    pull_request: PullRequestResult
    cleanup: CleanupResult
