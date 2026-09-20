"""Configuration loading tests: precedence, .env parsing, validation."""
import os
import unittest
from pathlib import Path
from unittest import mock

from config import Config


class TestConfig(unittest.TestCase):
    def setUp(self) -> None:
        # Isolate every test from the real environment and the real .env.
        self._env = dict(os.environ)
        os.environ.pop("ATRIA_API_KEY", None)
        os.environ.pop("ATRIA_BASE_URL", None)
        os.environ.pop("ATRIA_MODEL", None)
        os.environ.pop("ATRIA_MAX_ITERATIONS", None)
        os.environ.pop("ATRIA_TIMEOUT", None)
        os.environ.pop("ATRIA_TEMPERATURE", None)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._env)

    def load(self, cwd: Path, overrides: dict | None = None) -> Config:
        with mock.patch.object(Path, "cwd", return_value=cwd):
            return Config.load(overrides)

    def test_requires_an_api_key(self):
        with self.assertRaises(SystemExit):
            self.load(Path(__file__).parent)

    def test_cli_flag_wins_over_env(self):
        os.environ["ATRIA_API_KEY"] = "k"
        os.environ["ATRIA_MODEL"] = "env-model"
        cfg = self.load(Path(__file__).parent, {"model": "cli-model"})
        self.assertEqual(cfg.model, "cli-model")

    def test_env_var_used_when_no_flag(self):
        os.environ["ATRIA_API_KEY"] = "key-from-env"
        os.environ["ATRIA_MODEL"] = "env-model"
        cfg = self.load(Path(__file__).parent)
        self.assertEqual(cfg.api_key, "key-from-env")
        self.assertEqual(cfg.model, "env-model")

    def test_defaults(self):
        os.environ["ATRIA_API_KEY"] = "k"
        cfg = self.load(Path(__file__).parent)
        self.assertEqual(cfg.base_url, "https://api.atria-asi.ai/v1")
        self.assertEqual(cfg.model, "Atria-Dawn-Preview")
        self.assertEqual(cfg.max_iterations, 25)
        self.assertEqual(cfg.request_timeout, 120)
        self.assertEqual(cfg.temperature, 0.2)
        self.assertFalse(cfg.auto_approve)

    def test_trailing_slash_stripped_from_base_url(self):
        os.environ["ATRIA_API_KEY"] = "k"
        cfg = self.load(Path(__file__).parent, {"base_url": "https://x.test/v1//"})
        self.assertEqual(cfg.base_url, "https://x.test/v1")

    def test_dotenv_is_read(self):
        tmp = Path(__file__).parent / "_scratch_config"
        tmp.mkdir(exist_ok=True)
        try:
            (tmp / ".env").write_text(
                "ATRIA_API_KEY=from-file\nATRIA_MODEL=file-model\n", encoding="utf-8"
            )
            cfg = self.load(tmp)
            self.assertEqual(cfg.api_key, "from-file")
            self.assertEqual(cfg.model, "file-model")
        finally:
            for f in tmp.glob("*"):
                f.unlink()
            tmp.rmdir()

    def test_real_env_beats_dotenv(self):
        tmp = Path(__file__).parent / "_scratch_config"
        tmp.mkdir(exist_ok=True)
        try:
            (tmp / ".env").write_text("ATRIA_API_KEY=from-file\n", encoding="utf-8")
            os.environ["ATRIA_API_KEY"] = "from-shell"
            cfg = self.load(tmp)
            self.assertEqual(cfg.api_key, "from-shell")
        finally:
            for f in tmp.glob("*"):
                f.unlink()
            tmp.rmdir()

    def test_dotenv_ignores_comments_and_blank_lines(self):
        tmp = Path(__file__).parent / "_scratch_config"
        tmp.mkdir(exist_ok=True)
        try:
            (tmp / ".env").write_text(
                "# a comment\n\nATRIA_API_KEY='quoted'\n", encoding="utf-8"
            )
            cfg = self.load(tmp)
            self.assertEqual(cfg.api_key, "quoted")
        finally:
            for f in tmp.glob("*"):
                f.unlink()
            tmp.rmdir()

    def test_int_knobs_from_env(self):
        os.environ["ATRIA_API_KEY"] = "k"
        os.environ["ATRIA_MAX_ITERATIONS"] = "50"
        os.environ["ATRIA_TIMEOUT"] = "30"
        os.environ["ATRIA_TEMPERATURE"] = "0.7"
        cfg = self.load(Path(__file__).parent)
        self.assertEqual(cfg.max_iterations, 50)
        self.assertEqual(cfg.request_timeout, 30)
        self.assertAlmostEqual(cfg.temperature, 0.7)

    def test_bad_int_falls_back_to_default(self):
        os.environ["ATRIA_API_KEY"] = "k"
        os.environ["ATRIA_MAX_ITERATIONS"] = "not-a-number"
        cfg = self.load(Path(__file__).parent)
        self.assertEqual(cfg.max_iterations, 25)

    def test_temperature_is_clamped(self):
        os.environ["ATRIA_API_KEY"] = "k"
        os.environ["ATRIA_TEMPERATURE"] = "5.0"
        cfg = self.load(Path(__file__).parent)
        self.assertEqual(cfg.temperature, 2.0)

    def test_zero_iterations_is_clamped_to_one(self):
        os.environ["ATRIA_API_KEY"] = "k"
        os.environ["ATRIA_MAX_ITERATIONS"] = "0"
        cfg = self.load(Path(__file__).parent)
        self.assertEqual(cfg.max_iterations, 1)


if __name__ == "__main__":
    unittest.main()
