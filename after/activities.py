import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from shared import (
    AIGeneratedFixResult,
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
from temporalio.exceptions import ApplicationError

IS_DEBUG = os.environ.get("DEBUG", "false").lower() in ["true", "1", "yes"]
logging.basicConfig(
    level=logging.DEBUG if IS_DEBUG else logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


class GitHubActivities:
    """GitHub PR Bot activities for Temporal workflow."""

    def __init__(self) -> None:
        """Initialize GitHub activities."""
        self.logger = logger

    @activity.defn
    async def parse_issue_url(self, request: PushRequest) -> ParsedIssueResult:
        """Parse GitHub issue URL to extract repository info and issue number."""
        issue_url = (
            f"https://github.com/{request.repo_path}/issues/{request.issue_number}"
        )

        activity.logger.info(
            "Parsing issue URL",
            extra={
                "issue_url": issue_url,
                "repo_path": request.repo_path,
                "issue_number": request.issue_number,
            },
        )

        parsed = urlparse(issue_url)
        path_parts = parsed.path.strip("/").split("/")

        if len(path_parts) != 4 or path_parts[2] != "issues":
            activity.logger.error(
                "Invalid GitHub issue URL format",
                extra={
                    "issue_url": issue_url,
                    "path_parts": path_parts,
                },
            )
            raise ApplicationError(
                f"Invalid GitHub issue URL: {issue_url}", non_retryable=True
            )

        repo_info = RepoInfo(owner=path_parts[0], name=path_parts[1])
        issue_info = IssueInfo(number=int(path_parts[3]))

        activity.logger.info(
            "Successfully parsed issue URL",
            extra={
                "repo_owner": repo_info.owner,
                "repo_name": repo_info.name,
                "issue_number": issue_info.number,
            },
        )

        return ParsedIssueResult(repo_info=repo_info, issue_info=issue_info)

    @activity.defn
    async def get_issue_details(
        self, repo_info: RepoInfo, issue_number: int
    ) -> IssueInfo:
        """Fetch issue details (title, body) from GitHub."""
        import requests

        github_token = os.environ.get("GITHUB_TOKEN")
        if not github_token:
            logger.error(
                "GitHub token not found",
                extra={
                    "required_env_var": "GITHUB_TOKEN",
                },
            )
            sys.exit(1)
        api_url = (
            f"https://api.github.com/repos/{repo_info.full_name}/issues/{issue_number}"
        )
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json",
        }
        try:
            response = requests.get(api_url, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            activity.logger.info(
                "Successfully obtained issue details",
                extra={
                    "issue_title": data["title"],
                    "issue_number": issue_number,
                },
            )
            return IssueInfo(
                number=issue_number, title=data["title"], body=data["body"]
            )
        except requests.RequestException as e:
            raise ApplicationError(
                f"Failed to fetch issue details: {e}", non_retryable=True
            )

    @activity.defn
    async def generate_fix_with_ai(
        self, issue_info: IssueInfo, clone_result: CloneResult
    ) -> AIGeneratedFixResult:
        """Generate a fix for the issue using an AI model."""
        from google import genai  # type: ignore [attr-defined]

        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            logger.error(
                "Gemini API Key not found",
                extra={
                    "required_env_var": "GEMINI_API_KEY",
                },
            )
            raise ApplicationError("GEMINI_API_KEY not set", non_retryable=True)

        client = genai.Client(api_key=gemini_api_key)
        # for this example, we'll assume the fix is always in the README.md
        file_to_edit = "README.md"
        file_path = clone_result.local_path / file_to_edit

        if not file_path.exists():
            logger.error(
                f"{file_to_edit} not found",
                extra={
                    "file_name": file_to_edit,
                },
            )
            raise ApplicationError(
                f"{file_to_edit} not found in repository", non_retryable=True
            )

        with file_path.open("r", encoding="utf-8") as f:
            original_content = f.read()

        # model = self.client.models.get("gemini-2.5-flash")
        prompt = f"""
        You are an expert programmer tasked with fixing a GitHub issue.
        Based on the issue details below, please provide the updated file content.

        Issue Title: {issue_info.title}
        Issue Body: {issue_info.body}

        Here is the current content of the file to be fixed (`{file_to_edit}`):
        ---
        {original_content}
        ---

        Please provide the full, updated content for the file `{file_to_edit}` that resolves the issue.
        Your response should ONLY contain the new file content, with no other text, comments, or explanations.
        """
        model_name = "gemini-2.5-flash"
        activity.logger.info(f"Requesting fix from model: {model_name}")
        try:
            # client.aio.models.generate_content for the async call.
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=prompt,
            )
            new_content = response.text.strip()
            if not new_content:
                activity.logger.error(
                    "AI model returned an empty response.", extra={"model": model_name}
                )
                raise ApplicationError(
                    "AI model returned an empty response.", non_retryable=False
                )
        except Exception as e:
            activity.logger.error(
                "Failed to generate content from Gemini API.", extra={"error": str(e)}
            )
            raise ApplicationError(f"Gemini API call failed: {e}") from e

        commit_message = f"fix: {issue_info.title}\n\nThis AI-generated commit addresses the issue described in the title.\n\nCloses #{issue_info.number}"
        activity.logger.info(
            "Successfully generated AI fix.", extra={"file_edited": file_to_edit}
        )

        return AIGeneratedFixResult(
            file_to_edit=file_to_edit,
            new_content=new_content,
            commit_message=commit_message,
        )

    @activity.defn
    async def clone_repo_and_create_branch(
        self, repo_info: RepoInfo, issue_number: int
    ) -> CloneResult:
        """Clone repository and create a new branch."""
        from git import Repo

        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            activity.logger.error("GitHub token not found in environment")
            raise ApplicationError(
                "GITHUB_TOKEN environment variable not set", non_retryable=True
            )

        temp_dir = Path(tempfile.mkdtemp(prefix=f"pr-bot-{repo_info.name}-"))
        branch_name = f"fix-issue-{issue_number}"

        activity.logger.info(
            "Starting repository clone",
            extra={
                "repo_full_name": repo_info.full_name,
                "temp_directory": str(temp_dir),
                "branch_name": branch_name,
            },
        )

        try:
            clone_url = f"https://{token}@github.com/{repo_info.full_name}.git"
            repo = Repo.clone_from(clone_url, str(temp_dir))

            activity.logger.info(
                "Repository cloned successfully",
                extra={
                    "repo_full_name": repo_info.full_name,
                    "local_path": str(temp_dir),
                },
            )

            repo.create_head(branch_name)
            repo.heads[branch_name].checkout()

            activity.logger.info(
                "Branch created and checked out",
                extra={
                    "branch_name": branch_name,
                    "repo_full_name": repo_info.full_name,
                },
            )

            return CloneResult(local_path=temp_dir, branch_name=branch_name)

        except Exception as e:
            activity.logger.error(
                "Failed to clone repository",
                extra={
                    "repo_full_name": repo_info.full_name,
                    "error": str(e),
                    "temp_directory": str(temp_dir),
                },
            )

            # clean up on failure
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

            raise ApplicationError(f"Failed to clone repo: {str(e)}") from e

    @activity.defn
    async def apply_fix_and_commit(
        self, clone_result: CloneResult, fix_result: AIGeneratedFixResult
    ) -> CommitResult:
        """Apply AI-generated fix and commit changes."""
        from git import Repo

        activity.logger.info(
            "Applying fix for issue",
            extra={
                "local_path": str(clone_result.local_path),
                "branch_name": clone_result.branch_name,
            },
        )
        try:
            repo = Repo(str(clone_result.local_path))
            file_path = clone_result.local_path / fix_result.file_to_edit

            with file_path.open("w", encoding="utf-8") as f:
                f.write(fix_result.new_content)

            repo.index.add([fix_result.file_to_edit])
            commit = repo.index.commit(fix_result.commit_message)

            activity.logger.info(
                "Changes committed successfully",
                extra={
                    "commit_hash": commit.hexsha,
                    "commit_message": fix_result.commit_message,
                },
            )

            return CommitResult(
                commit_hash=commit.hexsha, commit_message=fix_result.commit_message
            )
        except Exception as e:
            activity.logger.error(
                "Failed to apply fix and commit",
                extra={
                    "local_path": str(clone_result.local_path),
                    "error": str(e),
                },
            )
            raise ApplicationError(f"Failed to apply fix and commit: {str(e)}") from e

    @activity.defn
    async def push_changes(
        self, clone_result: CloneResult, commit_result: CommitResult
    ) -> PushResult:
        """Push changes to remote repository."""
        from git import Repo

        info = activity.info()

        activity.logger.info(
            "Pushing changes to remote",
            extra={
                "branch_name": clone_result.branch_name,
                "commit_hash": commit_result.commit_hash,
                "attempt": info.attempt,
                "local_path": str(clone_result.local_path),
            },
        )

        try:
            repo = Repo(str(clone_result.local_path))
            origin = repo.remote("origin")
            push_info = origin.push(clone_result.branch_name)

            activity.logger.info(
                "Changes pushed successfully",
                extra={
                    "branch_name": clone_result.branch_name,
                    "commit_hash": commit_result.commit_hash,
                    "attempt": info.attempt,
                    "pushed_commits": len(push_info),
                },
            )

            return PushResult(
                branch_name=clone_result.branch_name, pushed_commits=len(push_info)
            )

        except Exception as e:
            activity.logger.warning(
                "Failed to push changes",
                extra={
                    "branch_name": clone_result.branch_name,
                    "attempt": info.attempt,
                    "error": str(e),
                },
            )
            raise ApplicationError(f"Failed to push changes: {str(e)}") from e

    @activity.defn
    async def create_pull_request(
        self,
        repo_info: RepoInfo,
        issue_number: int,
        clone_result: CloneResult,
        push_result: PushResult,
    ) -> PullRequestResult:
        """Create a pull request on GitHub."""

        import requests

        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            activity.logger.error("GitHub token not found in environment")
            raise ApplicationError(
                "GITHUB_TOKEN environment variable not set", non_retryable=True
            )

        activity.logger.info(
            "Creating pull request",
            extra={
                "repo_full_name": repo_info.full_name,
                "issue_number": issue_number,
                "branch_name": clone_result.branch_name,
                "pushed_commits": push_result.pushed_commits,
            },
        )

        api_url = f"https://api.github.com/repos/{repo_info.full_name}/pulls"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }

        title = f"Fix issue #{issue_number}"
        # body = f"This PR fixes issue #{issue_number}\n\nCloses #{issue_number}"
        body = f"This PR fixes issue #{issue_number} using an AI-powered agent.\n\nCloses #{issue_number}"

        data = {
            "title": title,
            "body": body,
            "head": clone_result.branch_name,
            "base": "main",
        }

        try:
            response = requests.post(api_url, json=data, headers=headers, timeout=30)

            if response.status_code != 201:
                activity.logger.error(
                    "Failed to create pull request",
                    extra={
                        "repo_full_name": repo_info.full_name,
                        "status_code": response.status_code,
                        "response_text": response.text,
                        "issue_number": issue_number,
                    },
                )
                raise ApplicationError(
                    f"Failed to create PR: {response.status_code} - {response.text}"
                )

            pr_data = response.json()
            pr_url = pr_data["html_url"]
            pr_number = pr_data["number"]

            activity.logger.info(
                "Pull request created successfully",
                extra={
                    "pr_url": pr_url,
                    "pr_number": pr_number,
                    "issue_number": issue_number,
                    "repo_full_name": repo_info.full_name,
                },
            )

            return PullRequestResult(url=pr_url, number=pr_number, title=title)

        except requests.RequestException as e:
            activity.logger.error(
                "Network error while creating pull request",
                extra={
                    "repo_full_name": repo_info.full_name,
                    "issue_number": issue_number,
                    "error": str(e),
                },
            )
            raise ApplicationError(f"Network error creating PR: {str(e)}") from e

    @activity.defn
    async def cleanup_local_repo(self, clone_result: CloneResult) -> CleanupResult:
        """Clean up the local repository clone."""
        activity.logger.info(
            "Starting cleanup of local repository",
            extra={
                "local_path": str(clone_result.local_path),
                "branch_name": clone_result.branch_name,
            },
        )

        try:
            if clone_result.local_path.exists():
                shutil.rmtree(clone_result.local_path)

                activity.logger.info(
                    "Cleanup completed successfully",
                    extra={
                        "cleaned_path": str(clone_result.local_path),
                    },
                )

                return CleanupResult(
                    cleaned_path=clone_result.local_path,
                    success=True,
                    message="Successfully cleaned up local repository",
                )
            else:
                activity.logger.warning(
                    "Cleanup path does not exist",
                    extra={
                        "path": str(clone_result.local_path),
                    },
                )

                return CleanupResult(
                    cleaned_path=clone_result.local_path,
                    success=True,
                    message="Path did not exist, no cleanup needed",
                )

        except Exception as e:
            activity.logger.error(
                "Failed to cleanup local repository",
                extra={
                    "local_path": str(clone_result.local_path),
                    "error": str(e),
                },
            )

            return CleanupResult(
                cleaned_path=clone_result.local_path,
                success=False,
                message=f"Cleanup failed: {str(e)}",
            )
