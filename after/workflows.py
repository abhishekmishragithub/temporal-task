from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy

from shared import BotInput, WorkflowResult, RepoInfo, PRDetails


@workflow.defn
class GitHubPRWorkflow:
    """Workflow that orchestrates the PR creation process."""

    @workflow.run
    async def run(self, input: BotInput) -> WorkflowResult:
        """Execute the workflow with cleanup."""
        workflow.logger.info(f"Starting workflow for issue: {input.issue_url}")

        local_repo_path = None

        try:
            # parse the issue URL (returns tuple)
            result = await workflow.execute_activity(
                "parse_issue_url",
                input.issue_url,
                start_to_close_timeout=timedelta(seconds=30)
            )
            repo_info = RepoInfo(owner=result[0]["owner"], name=result[0]["name"])
            issue_number = result[1]

            workflow.logger.info(f"Processing {repo_info.owner}/{repo_info.name} issue #{issue_number}")

            local_repo_path = await workflow.execute_activity(
                "clone_repo_and_create_branch",
                args=[repo_info, issue_number],
                start_to_close_timeout=timedelta(minutes=2)
            )

            await workflow.execute_activity(
                "apply_fix_and_commit",
                args=[local_repo_path, issue_number],
                start_to_close_timeout=timedelta(seconds=30)
            )

            # retry  for flaky operations
            retry_policy = RetryPolicy(
                maximum_attempts=5,
                initial_interval=timedelta(seconds=1),
                maximum_interval=timedelta(seconds=10),
                backoff_coefficient=2.0
            )

            await workflow.execute_activity(
                "push_changes",
                args=[local_repo_path, issue_number],
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=retry_policy
            )

            pr_result = await workflow.execute_activity(
                "create_pull_request",
                args=[repo_info, issue_number],
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=retry_policy
            )

            pr_details = PRDetails(url=pr_result["url"])
            workflow.logger.info(f"Workflow completed successfully: {pr_details.url}")
            return WorkflowResult(pr_url=pr_details.url)

        except Exception as e:
            workflow.logger.error(f"Workflow failed: {str(e)}")
            raise

        finally:
            if local_repo_path:
                workflow.logger.info("Executing cleanup")
                try:
                    await workflow.execute_activity(
                        "cleanup_local_repo",
                        local_repo_path,
                        start_to_close_timeout=timedelta(minutes=1)
                    )
                    workflow.logger.info("Cleanup completed")
                except Exception as cleanup_error:
                    workflow.logger.error(f"Cleanup failed: {cleanup_error}")
