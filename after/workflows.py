import logging
import os
from datetime import timedelta
from typing import Optional

from activities import GitHubActivities
from shared import (
    CleanupResult,
    CloneResult,
    PushRequest,
    WorkflowResult,
)
from temporalio import workflow
from temporalio.common import RetryPolicy

IS_DEBUG = os.environ.get("DEBUG", "false").lower() in ["true", "1", "yes"]
logging.basicConfig(
    level=logging.DEBUG if IS_DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


@workflow.defn
class GitHubPRWorkflow:
    """Workflow that orchestrates the GitHub PR creation process."""

    @workflow.run
    async def run(self, request: PushRequest) -> WorkflowResult:
        """Execute the GitHub PR Bot workflow with proper error handling and cleanup."""
        workflow.logger.info(
            "Starting GitHub PR Bot workflow",
            extra={
                "workflow_id": request.workflow_id,
                "repo_path": request.repo_path,
                "issue_number": request.issue_number,
            },
        )

        clone_result: Optional[CloneResult] = None
        cleanup_result: Optional[CleanupResult] = None

        # default_retry_policy = RetryPolicy(maximum_attempts=3, backoff_coefficient=2.0)
        long_retry_policy = RetryPolicy(
            maximum_attempts=5,
            maximum_interval=timedelta(seconds=60),
            backoff_coefficient=2.0,
        )

        try:
            # step 1: parse the issue URL and validate inputs
            parsed_issue = await workflow.execute_activity_method(
                GitHubActivities.parse_issue_url,
                request,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=5),
                    backoff_coefficient=2.0,
                    non_retryable_error_types=["ApplicationError"],
                ),
            )

            workflow.logger.info(
                "Issue URL parsed successfully",
                extra={
                    "repo_owner": parsed_issue.repo_info.owner,
                    "repo_name": parsed_issue.repo_info.name,
                    "issue_number": parsed_issue.issue_info.number,
                },
            )

            # step 2: get issue details -- agentic one
            issue_details = await workflow.execute_activity_method(
                GitHubActivities.get_issue_details,
                args=[parsed_issue.repo_info, parsed_issue.issue_info.number],
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=5),
                    backoff_coefficient=2.0,
                    non_retryable_error_types=["ApplicationError"],
                ),
            )

            workflow.logger.info(
                "Successfully obtained issue details",
                extra={
                    "issue_title": issue_details.title,
                    "issue_number": issue_details.number,
                },
            )

            # step 3: clone repository and create branch
            clone_result = await workflow.execute_activity_method(
                GitHubActivities.clone_repo_and_create_branch,
                args=[parsed_issue.repo_info, parsed_issue.issue_info.number],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=2),
                    maximum_interval=timedelta(seconds=10),
                    backoff_coefficient=2.0,
                ),
            )

            workflow.logger.info(
                "Repository cloned and branch created",
                extra={
                    "local_path": str(clone_result.local_path)
                    if clone_result
                    else "unknown",
                    "branch_name": clone_result.branch_name
                    if clone_result
                    else "unknown",
                },
            )

            # step 4: generate fix with AI
            ai_fix_result = await workflow.execute_activity_method(
                GitHubActivities.generate_fix_with_ai,
                args=[issue_details, clone_result],
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=5),
                    backoff_coefficient=2.0,
                    non_retryable_error_types=["ApplicationError"],
                ),
            )

            workflow.logger.info(
                "Model generated fix for the issue",
                extra={
                    "edited_file": ai_fix_result.file_to_edit,
                    "commit_message": ai_fix_result.commit_message,
                },
            )

            # step 5: apply fix and commit changes
            commit_result = await workflow.execute_activity_method(
                GitHubActivities.apply_fix_and_commit,
                args=[clone_result, ai_fix_result],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(
                    maximum_attempts=3,
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=5),
                    backoff_coefficient=2.0,
                    non_retryable_error_types=["ApplicationError"],
                ),
            )

            workflow.logger.info(
                "Applied fix to the issue",
                extra={
                    "commit_hash": commit_result.commit_hash,
                    "commit_message": commit_result.commit_message,
                },
            )

            # step 6: push changes
            push_result = await workflow.execute_activity_method(
                GitHubActivities.push_changes,
                args=[clone_result, commit_result],
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=long_retry_policy,
            )

            workflow.logger.info(
                "Changes pushed successfully",
                extra={
                    "pushed_branch_name": push_result.branch_name,
                    "pushed_commit_message": push_result.pushed_commits,
                },
            )

            # step 7: create pull request with retry for API rate limits
            pull_request_result = await workflow.execute_activity_method(
                GitHubActivities.create_pull_request,
                args=[
                    parsed_issue.repo_info,
                    parsed_issue.issue_info.number,
                    clone_result,
                    push_result,
                ],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=long_retry_policy,
            )

            workflow.logger.info(
                "Pull request created successfully",
                extra={
                    "pr_url": pull_request_result.url,
                    "pr_number": pull_request_result.number,
                    "pr_title": pull_request_result.title,
                },
            )

            # Note: cleanup will be handled in finally block,
            # so we don't return the result here yet
            final_cleanup = cleanup_result or CleanupResult(
                cleaned_path=clone_result.local_path if clone_result else None,
                success=False,
                message="Cleanup not yet performed",
            )

            return WorkflowResult(
                pull_request=pull_request_result, cleanup=final_cleanup
            )

        except Exception as e:
            workflow.logger.error(
                "Workflow execution failed",
                extra={
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "repo_path": request.repo_path,
                    "issue_number": request.issue_number,
                },
            )
            raise

        finally:
            # always attempt cleanup if we have a clone result
            if clone_result is not None:
                workflow.logger.info(
                    "Executing cleanup",
                    extra={
                        "local_path": str(clone_result.local_path),
                    },
                )

                try:
                    cleanup_result = await workflow.execute_activity_method(
                        GitHubActivities.cleanup_local_repo,
                        args=[clone_result],
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=RetryPolicy(
                            maximum_attempts=3,
                            initial_interval=timedelta(seconds=1),
                            maximum_interval=timedelta(seconds=10),
                            backoff_coefficient=2.0,
                        ),
                    )

                    if cleanup_result and cleanup_result.success:
                        workflow.logger.info(
                            "Cleanup completed successfully",
                            extra={
                                "cleaned_path": str(cleanup_result.cleaned_path)
                                if cleanup_result.cleaned_path
                                else "unknown",
                                "message": cleanup_result.message,
                            },
                        )
                    elif cleanup_result:
                        workflow.logger.warning(
                            "Cleanup completed with issues",
                            extra={
                                "cleaned_path": str(cleanup_result.cleaned_path)
                                if cleanup_result.cleaned_path
                                else "unknown",
                                "message": cleanup_result.message,
                            },
                        )

                except Exception as cleanup_error:
                    workflow.logger.error(
                        "Cleanup failed",
                        extra={
                            "cleanup_error": str(cleanup_error),
                            "local_path": str(clone_result.local_path),
                        },
                    )
                    # don't raise cleanup errors, just log them
            else:
                workflow.logger.info(
                    "No cleanup needed - no local repository was created"
                )
