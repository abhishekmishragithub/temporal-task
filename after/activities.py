import os
import tempfile
import shutil
from urllib.parse import urlparse
from git import Repo
import requests
from temporalio import activity
from temporalio.exceptions import ApplicationError

from shared import RepoInfo, PRDetails


@activity.defn
async def parse_issue_url(issue_url: str) -> tuple[dict, int]:
    """Parse GitHub issue URL to extract repository info and issue number."""
    activity.logger.info(f"Parsing issue URL: {issue_url}")

    parsed = urlparse(issue_url)
    path_parts = parsed.path.strip('/').split('/')

    if len(path_parts) != 4 or path_parts[2] != 'issues':
        raise ApplicationError(
            f"Invalid GitHub issue URL: {issue_url}",
            non_retryable=True
        )

    repo_info = {"owner": path_parts[0], "name": path_parts[1]}
    issue_number = int(path_parts[3])

    activity.logger.info(f"Parsed: {repo_info['owner']}/{repo_info['name']} issue #{issue_number}")
    return repo_info, issue_number


@activity.defn
async def clone_repo_and_create_branch(repo_info: RepoInfo, issue_number: int) -> str:
    """Clone repository and create a new branch."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ApplicationError("GITHUB_TOKEN environment variable not set", non_retryable=True)

    temp_dir = tempfile.mkdtemp(prefix=f"pr-bot-{repo_info.name}-")
    activity.logger.info(f"Cloning repository to {temp_dir}")

    try:
        clone_url = f"https://{token}@github.com/{repo_info.owner}/{repo_info.name}.git"
        repo = Repo.clone_from(clone_url, temp_dir)

        branch_name = f"fix-issue-{issue_number}"
        activity.logger.info(f"Creating branch {branch_name}")
        repo.create_head(branch_name)
        repo.heads[branch_name].checkout()

        return temp_dir
    except Exception as e:
        # clean up on failure
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        raise ApplicationError(f"Failed to clone repo: {str(e)}")


@activity.defn
async def apply_fix_and_commit(local_repo_path: str, issue_number: int):
    """Apply fix and commit changes."""
    activity.logger.info(f"Applying fix for issue #{issue_number}")

    readme_path = os.path.join(local_repo_path, "README.md")
    with open(readme_path, 'a') as f:
        f.write(f"\n\nFixed by PR Bot in response to issue #{issue_number}\n")
    repo = Repo(local_repo_path)
    repo.index.add(["README.md"])
    repo.index.commit(f"Fix issue #{issue_number}")

    activity.logger.info("Changes committed successfully")


@activity.defn
async def push_changes(local_repo_path: str, issue_number: int):
    """Push changes to remote repository."""
    info = activity.info()

    # simulate failures for the first 2 attempts (to show retry)
    if info.attempt < 3:
        activity.logger.warning(f"Simulating push failure (attempt {info.attempt})")
        raise ApplicationError("Simulated GitHub API rate limit")

    activity.logger.info(f"Pushing changes (attempt {info.attempt})")

    repo = Repo(local_repo_path)
    branch_name = f"fix-issue-{issue_number}"

    origin = repo.remote("origin")
    origin.push(branch_name)

    activity.logger.info("Changes pushed successfully")


@activity.defn
async def create_pull_request(repo_info: RepoInfo, issue_number: int) -> dict:
    """Create a pull request on GitHub."""
    activity.logger.info(f"Creating pull request for issue #{issue_number}")

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ApplicationError("GITHUB_TOKEN environment variable not set", non_retryable=True)

    url = f"https://api.github.com/repos/{repo_info.owner}/{repo_info.name}/pulls"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }
    branch_name = f"fix-issue-{issue_number}"
    data = {
        "title": f"Fix issue #{issue_number}",
        "body": f"This PR fixes issue #{issue_number}\n\nCloses #{issue_number}",
        "head": branch_name,
        "base": "main"
    }

    response = requests.post(url, json=data, headers=headers)

    if response.status_code != 201:
        raise ApplicationError(
            f"Failed to create PR: {response.status_code} - {response.text}"
        )

    pr_data = response.json()
    pr_url = pr_data["html_url"]
    activity.logger.info(f"Pull request created: {pr_url}")
    return {"url": pr_url}


@activity.defn
async def cleanup_local_repo(local_repo_path: str):
    """Clean up the local repository clone."""
    activity.logger.info(f"Cleaning up {local_repo_path}")

    if os.path.exists(local_repo_path):
        shutil.rmtree(local_repo_path)
        activity.logger.info("Cleanup completed successfully")
    else:
        activity.logger.warning(f"Path {local_repo_path} does not exist")
