"""
github/pr_manager.py — Pull Request Creation & Management

Handles creating branches and PRs on GitHub via PyGithub.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from github import Github, GithubException
from github.PullRequest import PullRequest

logger = logging.getLogger(__name__)


class PRManager:
    """
    Manages Pull Request creation via the GitHub API.
    
    Supports:
      1. Checking write permissions
      2. Forking repositories if write access is missing
      3. Opening PRs from forks back to origin
    """

    def __init__(self, token: str):
        self._gh = Github(token)
        self._user = None

    def _get_user(self):
        if self._user is None:
            self._user = self._gh.get_user()
        return self._user

    async def fork_repo(self, repo_url: str) -> dict:
        """Fork a repository to the authenticated user's account if no write access."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fork_repo_sync, repo_url)

    def _fork_repo_sync(self, repo_url: str) -> dict:
        path      = repo_url.rstrip("/").split("github.com/")[-1].split("/")
        full_name = f"{path[0]}/{path[1]}"
        repo      = self._gh.get_repo(full_name)
        
        user = self._get_user()
        
        # Check permissions
        try:
            can_push = repo.permissions.push
        except Exception:
            can_push = False  # fallback if scopes don't allow checking

        if can_push:
            logger.info(f"[PRManager] Have direct write access to {full_name}")
            return {
                "is_forked":      False,
                "repo_full_name": full_name,
                "clone_url":      repo.clone_url,
                "owner_login":    repo.owner.login,
                "default_branch": repo.default_branch
            }

        # Check if already forked
        try:
            existing_fork = user.get_repo(repo.name)
            if existing_fork and existing_fork.fork and existing_fork.parent.full_name == repo.full_name:
                logger.info(f"[PRManager] Found existing fork: {existing_fork.full_name}")
                return {
                    "is_forked":      True,
                    "repo_full_name": existing_fork.full_name,
                    "clone_url":      existing_fork.clone_url,
                    "owner_login":    user.login,
                    "default_branch": existing_fork.default_branch
                }
        except GithubException:
            pass

        logger.info(f"[PRManager] Forking {full_name} to {user.login}...")
        fork = user.create_fork(repo)
        return {
            "is_forked":      True,
            "repo_full_name": fork.full_name,
            "clone_url":      fork.clone_url,
            "owner_login":    user.login,
            "default_branch": fork.default_branch
        }

    async def create_pr(
        self,
        repo_url: str,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        issue_number: Optional[int] = None,
        labels: list[str] | None = None,
    ) -> PullRequest:
        """
        Create a Pull Request on GitHub.
        
        Args:
            repo_url:     Repository URL (https://github.com/owner/repo)
            title:        PR title
            body:         PR description (Markdown)
            head:         Source branch name
            base:         Target branch (usually "main" or "master")
            issue_number: If provided, the PR will close this issue
            labels:       List of label names to attach
        
        Returns:
            PyGithub PullRequest object
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._create_pr_sync,
            repo_url, title, body, head, base, issue_number, labels or [],
        )

    def _create_pr_sync(
        self,
        repo_url: str,
        title: str,
        body: str,
        head: str,
        base: str,
        issue_number: Optional[int],
        labels: list[str],
    ) -> PullRequest:
        # Parse repo URL
        path      = repo_url.rstrip("/").split("github.com/")[-1].split("/")
        full_name = f"{path[0]}/{path[1]}"
        repo      = self._gh.get_repo(full_name)

        # Ensure we're targeting the correct default branch
        if base == "main":
            try:
                repo.get_branch("main")
            except GithubException:
                base = repo.default_branch
                logger.info(f"[PRManager] Default branch is '{base}', not 'main'")

        # Append "closes issue" to body if issue_number provided
        if issue_number:
            body += f"\n\nCloses #{issue_number}"

        pr = repo.create_pull(
            title=title,
            body=body,
            head=head,
            base=base,
            draft=False,
        )

        logger.info(f"[PRManager] Created PR #{pr.number}: {pr.html_url}")

        # Apply labels if provided
        if labels:
            try:
                pr.add_to_labels(*labels)
            except GithubException as e:
                logger.warning(f"[PRManager] Could not apply labels: {e}")

        return pr

    async def add_comment(self, repo_url: str, pr_number: int, comment: str) -> None:
        """Add a review comment to an existing PR."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, self._add_comment_sync, repo_url, pr_number, comment
        )

    def _add_comment_sync(self, repo_url: str, pr_number: int, comment: str) -> None:
        path      = repo_url.rstrip("/").split("github.com/")[-1].split("/")
        full_name = f"{path[0]}/{path[1]}"
        repo      = self._gh.get_repo(full_name)
        pr        = repo.get_pull(pr_number)
        pr.create_issue_comment(comment)
