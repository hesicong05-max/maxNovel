"""
Locust load testing suite for 满分小说 backend.

Run with:
    pip install locust
    locust -f load_tests/locustfile.py --host=http://localhost:8000

Then open http://localhost:8089 to configure and start tests.

Recommended scenarios:
    1. Smoke test: 10 users, 1 spawn rate, 30s duration
    2. Normal load: 50 users, 5 spawn rate, 2min duration
    3. Stress test: 200 users, 10 spawn rate, 3min duration
    4. Spike test: 500 users, 50 spawn rate, 1min duration
"""

import json
import random
import string
import time

from locust import HttpUser, between, events, task


# ═══════════════════════════════════════════════════════════════
# Test data
# ═══════════════════════════════════════════════════════════════

GENRES = ["玄幻", "都市", "科幻", "武侠", "仙侠", "悬疑", "言情"]

TEST_WORLDVIEW = {
    "characters": [
        {"name": "林远", "personality": "坚韧果敢", "background": "出身寒门", "motivation": "守护家园", "ability": "剑道天赋", "relations": []},
        {"name": "苏瑶", "personality": "聪慧冷静", "background": "世家之女", "motivation": "寻找真相", "ability": "阵法传承", "relations": []},
    ],
    "geography": [{"name": "天玄大陆", "description": "万族林立", "significance": "故事主舞台"}],
    "factions": [{"name": "青云宗", "stance": "正道", "power_level": "一流", "relations": []}],
    "power_system": [{"name": "灵气体系", "levels": "炼气-筑基-金丹-元婴", "rules": "吸收天地灵气", "limitations": "境界瓶颈"}],
    "history": [{"event": "上古大战", "time": "万年前", "description": "人妖大战", "impact": "划分疆域"}],
    "conflicts": [{"name": "正邪之争", "type": "阵营冲突", "parties": "正道vs魔道", "stakes": "大陆控制权", "resolution_hint": "主角平衡"}],
    "special_settings": [{"name": "天道法则", "description": "世界运行规则", "rules": "因果循环"}],
    "source": "manual",
}


def random_username() -> str:
    return "loadtest_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


# ═══════════════════════════════════════════════════════════════
# Load test user
# ═══════════════════════════════════════════════════════════════


class NovelAppUser(HttpUser):
    """Simulates a typical user session: register → create project → set worldview → browse community."""

    wait_time = between(1, 3)

    def on_start(self):
        """Register and login at the start of each user session."""
        self.username = random_username()
        self.password = "LoadTest123!"
        self.token = None
        self.auth_headers = {}
        self.project_id = None

        # Try to register
        resp = self.client.post(
            "/api/auth/register",
            json={"username": self.username, "password": self.password},
            name="POST /api/auth/register",
        )

        if resp.status_code == 409:
            # User exists, try login directly
            pass

        # Login
        resp = self.client.post(
            "/api/auth/login",
            json={"username": self.username, "password": self.password},
            name="POST /api/auth/login",
        )

        if resp.status_code == 200:
            data = resp.json()
            self.token = data.get("access_token")
            self.auth_headers = {"Authorization": f"Bearer {self.token}"}
        else:
            # If auth fails, continue as anonymous user (community browsing only)
            pass

    # ── Health & Static ──

    @task(5)
    def health_check(self):
        self.client.get("/api/health", name="GET /api/health")

    # ── Project Management ──

    @task(3)
    def create_project(self):
        """Create a new writing project."""
        if not self.auth_headers:
            return

        resp = self.client.post(
            "/api/projects",
            json={
                "title": f"负载测试小说-{self.username[:8]}",
                "genre": random.choice(GENRES),
                "total_chapters": random.choice([5, 10, 20]),
                "chapter_word_count": random.choice([1000, 2000, 3000]),
                "style_intensity": "standard",
            },
            headers=self.auth_headers,
            name="POST /api/projects",
        )

        if resp.status_code == 200:
            self.project_id = resp.json().get("id")

    @task(2)
    def list_projects(self):
        if not self.auth_headers:
            return
        self.client.get("/api/projects", headers=self.auth_headers, name="GET /api/projects")

    @task(1)
    def set_worldview(self):
        """Set worldview for a project."""
        if not self.auth_headers or not self.project_id:
            return

        self.client.post(
            f"/api/worldview/{self.project_id}",
            json=TEST_WORLDVIEW,
            headers=self.auth_headers,
            name="POST /api/worldview/{id}",
        )

    @task(1)
    def get_project_progress(self):
        if not self.auth_headers or not self.project_id:
            return
        self.client.get(
            f"/api/projects/{self.project_id}/progress",
            headers=self.auth_headers,
            name="GET /api/projects/{id}/progress",
        )

    # ── Chapter Operations ──

    @task(1)
    def list_chapters(self):
        if not self.auth_headers or not self.project_id:
            return
        self.client.get(
            f"/api/chapters/{self.project_id}",
            headers=self.auth_headers,
            name="GET /api/chapters/{id}",
        )

    @task(1)
    def get_word_counts(self):
        if not self.auth_headers or not self.project_id:
            return
        self.client.get(
            f"/api/chapters/{self.project_id}/word-counts",
            headers=self.auth_headers,
            name="GET /api/chapters/{id}/word-counts",
        )

    # ── Community ──

    @task(5)
    def browse_community(self):
        """Browse community novels — most common user action."""
        offset = random.choice([0, 10, 20, 50])
        sort_by = random.choice(["latest", "popular", "random"])
        self.client.get(
            f"/api/community/novels?offset={offset}&limit=10&sort={sort_by}",
            name="GET /api/community/novels",
        )

    @task(2)
    def get_community_tags(self):
        self.client.get("/api/community/tags", name="GET /api/community/tags")

    @task(2)
    def view_novel_detail(self):
        """View a random novel detail (increments view count)."""
        # First get a list, then view one
        resp = self.client.get(
            "/api/community/novels?limit=5&sort=random",
            name="GET /api/community/novels (for detail)",
        )
        if resp.status_code == 200:
            novels = resp.json().get("novels", [])
            if novels:
                novel_id = random.choice(novels)["id"]
                self.client.get(
                    f"/api/community/novels/{novel_id}",
                    name="GET /api/community/novels/{id}",
                )

    @task(1)
    def like_novel(self):
        """Like a random novel."""
        resp = self.client.get(
            "/api/community/novels?limit=5&sort=popular",
            name="GET /api/community/novels (for like)",
        )
        if resp.status_code == 200:
            novels = resp.json().get("novels", [])
            if novels:
                novel_id = random.choice(novels)["id"]
                headers = self.auth_headers if self.auth_headers else {}
                self.client.post(
                    f"/api/community/novels/{novel_id}/like",
                    headers=headers,
                    name="POST /api/community/novels/{id}/like",
                )

    # ── Export ──

    @task(1)
    def export_project(self):
        if not self.auth_headers or not self.project_id:
            return
        self.client.get(
            f"/api/export/{self.project_id}/txt",
            headers=self.auth_headers,
            name="GET /api/export/{id}/txt",
        )

    # ── Settings ──

    @task(1)
    def get_settings(self):
        if not self.auth_headers:
            return
        self.client.get(
            "/api/settings",
            headers=self.auth_headers,
            name="GET /api/settings",
        )


# ═══════════════════════════════════════════════════════════════
# SSE-specific load test (separate class)
# ═══════════════════════════════════════════════════════════════


class SSEChapterGenerationUser(HttpUser):
    """
    Dedicated SSE load tester — simulates concurrent chapter generation.

    Usage:
        locust -f load_tests/locustfile.py:SSEChapterGenerationUser --host=http://localhost:8000

    This tests the most resource-intensive operation: SSE streaming chapter generation.
    """

    wait_time = between(5, 15)

    def on_start(self):
        self.username = random_username()
        self.password = "LoadTest123!"

        # Register + login
        self.client.post(
            "/api/auth/register",
            json={"username": self.username, "password": self.password},
            name="POST /api/auth/register",
        )

        resp = self.client.post(
            "/api/auth/login",
            json={"username": self.username, "password": self.password},
            name="POST /api/auth/login",
        )

        if resp.status_code == 200:
            self.token = resp.json().get("access_token")
            self.auth_headers = {"Authorization": f"Bearer {self.token}"}

            # Create project with worldview
            proj = self.client.post(
                "/api/projects",
                json={
                    "title": f"SSE测试-{self.username[:8]}",
                    "genre": "玄幻",
                    "total_chapters": 5,
                    "chapter_word_count": 1000,
                    "style_intensity": "standard",
                },
                headers=self.auth_headers,
                name="POST /api/projects (SSE setup)",
            )

            if proj.status_code == 200:
                self.project_id = proj.json().get("id")
                self.client.post(
                    f"/api/worldview/{self.project_id}",
                    json=TEST_WORLDVIEW,
                    headers=self.auth_headers,
                    name="POST /api/worldview (SSE setup)",
                )

                # Generate outline (mock mode)
                self.client.post(
                    f"/api/outline/{self.project_id}/generate",
                    headers=self.auth_headers,
                    name="POST /api/outline/generate (SSE setup)",
                )
        else:
            self.auth_headers = {}
            self.project_id = None

    @task
    def generate_chapter_sse(self):
        """Simulate SSE chapter generation — the heaviest endpoint."""
        if not self.auth_headers or not self.project_id:
            return

        # Start SSE stream (will timeout if LLM is not configured — expected in load test)
        with self.client.get(
            f"/api/chapters/{self.project_id}/stream/1",
            headers=self.auth_headers,
            name="GET /api/chapters/stream (SSE)",
            catch_response=True,
            stream=True,
        ) as resp:
            if resp.status_code == 200:
                # Read first few SSE events then close
                try:
                    for _ in range(3):
                        next(resp.iter_lines())
                    resp.success()
                except (StopIteration, Exception):
                    resp.failure("SSE stream closed early")
            elif resp.status_code == 429:
                resp.failure("Rate limited")
            else:
                resp.failure(f"Unexpected status: {resp.status_code}")


# ═══════════════════════════════════════════════════════════════
# Event listeners for custom metrics
# ═══════════════════════════════════════════════════════════════


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    print("\n" + "=" * 60)
    print("  Load test starting")
    print(f"  Target: {environment.host}")
    print(f"  Max users: {environment.parsed_options.num_users}")
    print(f"  Spawn rate: {environment.parsed_options.spawn_rate}/s")
    print("=" * 60 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    stats = environment.stats
    print("\n" + "=" * 60)
    print("  Load test complete!")
    print(f"  Total requests: {stats.total.num_requests}")
    print(f"  Total failures: {stats.total.num_failures}")
    print(f"  Avg response time: {stats.total.avg_response_time:.1f}ms")
    print(f"  Max response time: {stats.total.max_response_time:.1f}ms")
    if stats.total.num_requests > 0:
        success_rate = (1 - stats.total.num_failures / stats.total.num_requests) * 100
        print(f"  Success rate: {success_rate:.1f}%")
    print("=" * 60 + "\n")
