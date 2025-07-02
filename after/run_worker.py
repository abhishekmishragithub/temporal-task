import asyncio
import os
from temporalio.client import Client
from temporalio.worker import Worker

from activities import (
    parse_issue_url,
    clone_repo_and_create_branch,
    apply_fix_and_commit,
    push_changes,
    create_pull_request,
    cleanup_local_repo
)
from workflows import GitHubPRWorkflow


async def main():
    """Run the Temporal worker."""
    if not os.environ.get("GITHUB_TOKEN"):
        print("ERROR: GITHUB_TOKEN environment variable not set")
        print("Please export GITHUB_TOKEN='your-github-pat'")
        return

    client = await Client.connect("localhost:7233")
    worker = Worker(
        client,
        task_queue="github-pr-bot-queue",
        workflows=[GitHubPRWorkflow],
        activities=[
            parse_issue_url,
            clone_repo_and_create_branch,
            apply_fix_and_commit,
            push_changes,
            create_pull_request,
            cleanup_local_repo
        ],
    )

    print("Worker started, listening for tasks...")
    print("Press Ctrl+C to stop")

    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
