import logging
import unittest

from safe_logging import log_safe_exception, log_safe_external_failure


_PRIVATE_LOG_SENTINEL = "private-log-secret-992f41"


class SafeLoggingTests(unittest.TestCase):
    def test_exception_diagnostic_keeps_type_and_stack_without_message(self):
        logger = logging.getLogger("tests.safe_logging.exception")

        try:
            raise RuntimeError(_PRIVATE_LOG_SENTINEL)
        except RuntimeError as exc:
            with self.assertLogs(logger, level="ERROR") as logs:
                log_safe_exception(
                    logger,
                    "aio.test.failed",
                    exc,
                    job_id="job-1\r\nforged=true",
                    row=1,
                )

        output = "\n".join(logs.output)
        self.assertIn("aio.test.failed", output)
        self.assertIn("exception_type=RuntimeError", output)
        self.assertIn("test_exception_diagnostic_keeps_type_and_stack", output)
        self.assertIn("job_id=job-1_forged_true", output)
        self.assertNotIn(_PRIVATE_LOG_SENTINEL, output)
        self.assertNotIn("\r", output)

    def test_external_failure_records_shape_without_raw_detail(self):
        logger = logging.getLogger("tests.safe_logging.external")

        with self.assertLogs(logger, level="WARNING") as logs:
            log_safe_external_failure(
                logger,
                "aio.provider.failed",
                _PRIVATE_LOG_SENTINEL,
                provider="jina",
            )

        output = "\n".join(logs.output)
        self.assertIn("aio.provider.failed", output)
        self.assertIn("detail_type=str", output)
        self.assertIn("detail_length=25", output)
        self.assertIn("provider=jina", output)
        self.assertNotIn(_PRIVATE_LOG_SENTINEL, output)


if __name__ == "__main__":
    unittest.main()
