import uuid
from pathlib import Path

# from unittest.mock import AsyncMock, MagicMock
import pytest
from shared import (
    CleanupResult,
    CloneResult,
    CommitResult,
    IssueInfo,
    ParsedIssueResult,
    PullRequestResult,
    PushRequest,
    PushResult,
    RepoInfo,
)
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ActivityError, ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from workflows import GitHubPRWorkflow

WORKFLOW_TO_TEST = GitHubPRWorkflow
TASK_QUEUE = "github-pr-bot-test-queue"


mock_results = {
    "parse_issue_url": ParsedIssueResult(
        repo_info=RepoInfo(owner="test-owner", name="test-repo"),
        issue_info=IssueInfo(number=123),
    ),
    "clone_repo_and_create_branch": CloneResult(
        local_path=Path("/tmp/test-repo-123"), branch_name="fix-issue-123"
    ),
    "apply_fix_and_commit": CommitResult(
        commit_hash="abc123def456", commit_message="Fix issue #123"
    ),
    "push_changes": PushResult(branch_name="fix-issue-123", pushed_commits=1),
    "create_pull_request": PullRequestResult(
        url="http://github.com/pull/1", number=1, title="Fix issue #123"
    ),
    "cleanup_local_repo": CleanupResult(
        cleaned_path=Path("/tmp/test-repo-123"),
        success=True,
        message="Cleaned up.",
    ),
}


@pytest.mark.asyncio
async def test_github_pr_workflow_success():
    """Verify the workflow completes successfully when all activities succeed."""

    @activity.defn
    async def parse_issue_url(request: PushRequest) -> ParsedIssueResult:
        return mock_results["parse_issue_url"]

    @activity.defn
    async def clone_repo_and_create_branch(
        repo_info: RepoInfo, issue_number: int
    ) -> CloneResult:
        return mock_results["clone_repo_and_create_branch"]

    @activity.defn
    async def apply_fix_and_commit(
        clone_result: CloneResult, issue_number: int
    ) -> CommitResult:
        return mock_results["apply_fix_and_commit"]

    @activity.defn
    async def push_changes(
        clone_result: CloneResult, commit_result: CommitResult
    ) -> PushResult:
        return mock_results["push_changes"]

    @activity.defn
    async def create_pull_request(*args, **kwargs) -> PullRequestResult:
        return mock_results["create_pull_request"]

    @activity.defn
    async def cleanup_local_repo(clone_result: CloneResult) -> CleanupResult:
        return mock_results["cleanup_local_repo"]

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[WORKFLOW_TO_TEST],
            activities=[
                parse_issue_url,
                clone_repo_and_create_branch,
                apply_fix_and_commit,
                push_changes,
                create_pull_request,
                cleanup_local_repo,
            ],
        ):
            request = PushRequest(repo_path="test/repo", issue_number=123)
            result = await env.client.execute_workflow(
                WORKFLOW_TO_TEST.run,
                request,
                id=str(uuid.uuid4()),
                task_queue=TASK_QUEUE,
            )
            assert result.pull_request.url == "http://github.com/pull/1"
            assert result.cleanup.success is True


@pytest.mark.asyncio
async def test_github_pr_workflow_non_retryable_failure():
    """Verify the workflow fails immediately on a non-retryable error."""

    @activity.defn
    async def parse_issue_url(request: PushRequest) -> ParsedIssueResult:
        raise ApplicationError("Invalid issue URL", non_retryable=True)

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[WORKFLOW_TO_TEST],
            activities=[parse_issue_url],  # only need to provide the failing activity
        ):
            request = PushRequest(repo_path="bad/path", issue_number=0)
            with pytest.raises(WorkflowFailureError) as exc_info:
                await env.client.execute_workflow(
                    WORKFLOW_TO_TEST.run,
                    request,
                    id=str(uuid.uuid4()),
                    task_queue=TASK_QUEUE,
                )

            # check the exception chain
            assert isinstance(exc_info.value.__cause__, ActivityError)
            cause = exc_info.value.__cause__.__cause__
            assert isinstance(cause, ApplicationError)
            assert str(cause) == "Invalid issue URL"


@pytest.mark.asyncio
async def test_github_pr_workflow_retry_and_succeed():
    """Verify a failing activity is retried and the workflow eventually succeeds."""

    # class to track state across retries
    class MockState:
        pr_attempts = 0

    # mocking all activities that run before the one we want to fail
    @activity.defn
    async def parse_issue_url(*args, **kwargs) -> ParsedIssueResult:
        return mock_results["parse_issue_url"]

    @activity.defn
    async def clone_repo_and_create_branch(*args, **kwargs) -> CloneResult:
        return mock_results["clone_repo_and_create_branch"]

    @activity.defn
    async def apply_fix_and_commit(*args, **kwargs) -> CommitResult:
        return mock_results["apply_fix_and_commit"]

    @activity.defn
    async def push_changes(*args, **kwargs) -> PushResult:
        return mock_results["push_changes"]

    @activity.defn
    async def cleanup_local_repo(*args, **kwargs) -> CleanupResult:
        return mock_results["cleanup_local_repo"]

    # this is the activity we will make fail
    @activity.defn
    async def create_pull_request(*args, **kwargs) -> PullRequestResult:
        MockState.pr_attempts += 1
        if MockState.pr_attempts <= 2:
            raise ApplicationError("GitHub API rate limit", non_retryable=False)
        return mock_results["create_pull_request"]

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[WORKFLOW_TO_TEST],
            activities=[
                parse_issue_url,
                clone_repo_and_create_branch,
                apply_fix_and_commit,
                push_changes,
                create_pull_request,
                cleanup_local_repo,
            ],
        ):
            request = PushRequest(repo_path="test/repo", issue_number=123)
            await env.client.execute_workflow(
                WORKFLOW_TO_TEST.run,
                request,
                id=str(uuid.uuid4()),
                task_queue=TASK_QUEUE,
            )
            # assert that the failing activity was called 3 times
            assert MockState.pr_attempts == 3
