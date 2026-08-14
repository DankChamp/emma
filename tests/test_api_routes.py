"""
Focused API route regression tests.
"""
import unittest

from api.routes.projects import router


class ApiRouteTest(unittest.TestCase):
    def test_projects_study_route_is_not_shadowed_by_project_id(self):
        paths = [
            route.path
            for route in router.routes
            if "GET" in getattr(route, "methods", set())
        ]

        self.assertLess(
            paths.index("/projects/study"),
            paths.index("/projects/{project_id}"),
        )


if __name__ == "__main__":
    unittest.main()
