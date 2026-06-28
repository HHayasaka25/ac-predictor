import json
import tempfile
import threading
import unittest

from ac_predictor_crawler.domain.aperf import APerfState
from ac_predictor_crawler.repository.filerepository import FileRepository


class FileRepositoryTest(unittest.TestCase):
  def test_algorithm_and_heuristic_states_are_separate(self):
    with tempfile.TemporaryDirectory() as directory:
      repository = FileRepository(directory)
      repository.store_aperf_state(
        "algorithm",
        {"user": APerfState(1000, 1, "abc001")},
      )
      repository.store_aperf_state(
        "heuristic",
        {"user": APerfState(2000, 1, "ahc001")},
      )
      self.assertEqual(repository.get_aperf_state("algorithm")["user"].aperf, 1000)
      self.assertEqual(repository.get_aperf_state("heuristic")["user"].aperf, 2000)

  def test_concurrent_atomic_state_writes_never_publish_partial_json(self):
    with tempfile.TemporaryDirectory() as directory:
      repository = FileRepository(directory)
      errors = []

      def write(value):
        try:
          for _ in range(20):
            repository.store_aperf_state(
              "algorithm",
              {"user": APerfState(value, 1, f"contest-{value}")},
            )
            repository.get_aperf_state("algorithm")
        except Exception as error:
          errors.append(error)

      threads = [
        threading.Thread(target=write, args=(1000,)),
        threading.Thread(target=write, args=(2000,)),
      ]
      for thread in threads:
        thread.start()
      for thread in threads:
        thread.join()

      self.assertEqual(errors, [])
      with open(f"{directory}/aperf-state/algorithm.json") as file:
        stored = json.load(file)
      self.assertIn(stored["users"]["user"]["aperf"], (1000, 2000))


if __name__ == "__main__":
  unittest.main()
