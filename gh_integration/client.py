"""
github/client.py — GitHub API Client Wrapper

Wraps PyGithub for async use (all calls run in thread pool).
Provides: fetching issues, repo metadata, file contents.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from github import Github, GithubException
from github.Issue import Issue
from github.Repository import Repository

logger = logging.getLogger(__name__)


class GitHubClient:
    """
    Async-friendly wrapper around PyGithub.
    
    Runs synchronous PyGithub calls in the event loop's thread pool
    to avoid blocking the event loop.
    """

    def __init__(self, token: str):
        self._gh       = Github(token)
        self._loop_ref = None

    def _get_repo(self, repo_url: str) -> Repository:
        """Parse repo URL → owner/name and return Repository object."""
        # Normalize: https://github.com/owner/repo → owner/repo
        path = repo_url.rstrip("/")
        if "github.com" in path:
            parts = path.split("github.com/")[-1].split("/")
            full_name = f"{parts[0]}/{parts[1]}"
        else:
            full_name = path  # assume already "owner/repo"
        return self._gh.get_repo(full_name)

    # -------------------------------------------------------------------------
    # Issues
    # -------------------------------------------------------------------------

    async def get_issue(self, repo_url: str, issue_number: int) -> Issue:
        """Fetch a GitHub issue by number."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._get_issue_sync, repo_url, issue_number
        )

    def _get_issue_sync(self, repo_url: str, issue_number: int) -> Issue:
        repo  = self._get_repo(repo_url)
        issue = repo.get_issue(number=issue_number)
        logger.info(f"[GitHub] Fetched issue #{issue_number}: {issue.title}")
        return issue

    # -------------------------------------------------------------------------
    # Repository info
    # -------------------------------------------------------------------------

    async def get_repo_info(self, repo_url: str) -> dict:
        """Return basic repository metadata."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_repo_info_sync, repo_url)

    def _get_repo_info_sync(self, repo_url: str) -> dict:
        repo = self._get_repo(repo_url)
        return {
            "name":           repo.name,
            "full_name":      repo.full_name,
            "default_branch": repo.default_branch,
            "language":       repo.language,
            "description":    repo.description,
            "clone_url":      repo.clone_url,
        }

    # -------------------------------------------------------------------------
    # File contents
    # -------------------------------------------------------------------------

    async def get_file_content(self, repo_url: str, path: str, ref: str = "main") -> Optional[str]:
        """Fetch the raw content of a file from a repository."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._get_file_content_sync, repo_url, path, ref
        )

    def _get_file_content_sync(self, repo_url: str, path: str, ref: str) -> Optional[str]:
        try:
            repo    = self._get_repo(repo_url)
            content = repo.get_contents(path, ref=ref)
            return content.decoded_content.decode("utf-8", errors="replace")
        except GithubException as e:
            logger.warning(f"[GitHub] Could not fetch {path}: {e}")
            return None
