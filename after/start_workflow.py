import asyncio
import logging
import os
import sys
from argparse import ArgumentParser

from shared import PR_BOT_TASK_QUEUE_NAME, PushRequest, WorkflowResult
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from workflows import GitHubPRWorkflow

IS_DEBUG = os.environ.get("DEBUG", "false").lower() in ["true", "1", "yes"]
logging.basicConfig(
    level=logging.DEBUG if IS_DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


def parse_github_url(issue_url: str) -> PushRequest:
    """Parse GitHub issue URL to extract repo path and issue number."""
    try:
        # remove protocol and domain
        if issue_url.startswith("https://github.com/"):
            path_part = issue_url[19:]  # remove "https://github.com/"
        elif issue_url.startswith("github.com/"):
            path_part = issue_url[11:]  # remove "github.com/"
        else:
            raise ValueError("URL must be a GitHub issue URL")

        # split path: owner/repo/issues/number
        parts = path_part.strip("/").split("/")
        if len(parts) != 4 or parts[2] != "issues":
            raise ValueError(
                "URL must be in format: https://github.com/owner/repo/issues/123"
            )

        owner, repo, _, issue_number_str = parts
        issue_number = int(issue_number_str)

        repo_path = f"{owner}/{repo}"

        return PushRequest(repo_path=repo_path, issue_number=issue_number)

    except (ValueError, IndexError) as e:
        logger.error(
            "Failed to parse GitHub issue URL",
            extra={
                "issue_url": issue_url,
                "error": str(e),
            },
        )
        raise ValueError(f"Invalid GitHub issue URL '{issue_url}': {e}") from e


async def main() -> None:
    """Start a GitHub PR Bot workflow."""
    parser = ArgumentParser(description="Start GitHub PR Bot workflow")
    parser.add_argument(
        "issue_url",
        help="GitHub issue URL (e.g., https://github.com/owner/repo/issues/123)",
    )
    parser.add_argument(
        "--temporal-address",
        default=os.environ.get("TEMPORAL_ADDRESS", "localhost:7233"),
        help="Temporal server address (default: localhost:7233 or TEMPORAL_ADDRESS env var)",
    )

    args = parser.parse_args()

    # validate gitHub token
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        logger.error(
            "GitHub token not found",
            extra={
                "required_env_var": "GITHUB_TOKEN",
            },
        )
        sys.exit(1)

    try:
        # parse the gitHub issue URL
        request = parse_github_url(args.issue_url)

        logger.info(
            "Starting GitHub PR Bot workflow",
            extra={
                "issue_url": args.issue_url,
                "repo_path": request.repo_path,
                "issue_number": request.issue_number,
                "workflow_id": request.workflow_id,
                "temporal_address": args.temporal_address,
            },
        )

        # connect to Temporal
        try:
            client = await Client.connect(
                args.temporal_address, data_converter=pydantic_data_converter
            )
            logger.info(
                "Connected to Temporal server",
                extra={
                    "temporal_address": args.temporal_address,
                },
            )
        except Exception as e:
            logger.error(
                "Failed to connect to Temporal server",
                extra={
                    "temporal_address": args.temporal_address,
                    "error": str(e),
                },
            )
            sys.exit(1)

        # start the workflow
        try:
            handle = await client.start_workflow(
                GitHubPRWorkflow.run,
                request,
                id=request.workflow_id,
                task_queue=PR_BOT_TASK_QUEUE_NAME,
            )

            logger.info(
                "Workflow started successfully",
                extra={
                    "workflow_id": handle.id,
                    "repo_path": request.repo_path,
                    "issue_number": request.issue_number,
                },
            )

            # Wait for the workflow to complete
            result: WorkflowResult = await handle.result()

            logger.info(
                "Workflow completed successfully",
                extra={
                    "workflow_id": handle.id,
                    "pr_url": result.pull_request.url,
                    "pr_number": result.pull_request.number,
                    "cleanup_success": result.cleanup.success,
                },
            )

        except Exception as e:
            logger.error(
                "Workflow execution failed",
                extra={
                    "workflow_id": request.workflow_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            sys.exit(1)

    except ValueError as e:
        logger.error("Invalid input", extra={"error": str(e)})
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Workflow start cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(
            "Unexpected error during workflow start",
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
