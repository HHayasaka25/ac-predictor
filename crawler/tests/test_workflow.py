import subprocess
import unittest
from pathlib import Path


class WorkflowTest(unittest.TestCase):
  def test_healthcheck_wrapper_preserves_crawler_failure(self):
    completed = subprocess.run(
      [
        "bash",
        "-c",
        'status=0; false || status=$?; true || true; exit "$status"',
      ],
      check=False,
    )
    self.assertEqual(completed.returncode, 1)

  def test_workflow_uses_saved_status_for_healthcheck_and_exit(self):
    workflow = (
      Path(__file__).parents[2] / ".github/workflows/run-crawler.yaml"
    ).read_text()
    self.assertIn("|| status=$?", workflow)
    self.assertIn("/$status || true", workflow)
    self.assertIn('exit "$status"', workflow)
    self.assertNotIn("concurrency:", workflow)


if __name__ == "__main__":
  unittest.main()
