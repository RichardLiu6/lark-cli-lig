"""CLI behavior tests for approval comments."""

import json
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from lark_cli_lig.commands.approval import approval


PROCESS_NUMBER = "202608120002"
INSTANCE_CODE = "27977468-32ED-4CDE-B499-F4CFDDD13ECC"
COMMENT_TEXT = "原押金流程编号为 202608010001。"


class ApprovalCommentCLITests(unittest.TestCase):
    def test_comment_accepts_process_number_and_hides_instance_code(self):
        calls = []
        test_case = self

        def fake_post(self, path, body=None, params=None):
            calls.append((path, body, params))
            if path.startswith("/open-apis/approval/v4/instances/query"):
                return {
                    "instance_list": [{
                        "instance": {
                            "code": INSTANCE_CODE,
                            "serial_id": PROCESS_NUMBER,
                        }
                    }]
                }
            test_case.assertEqual(
                path,
                f"/open-apis/approval/v4/instances/{INSTANCE_CODE}/comments",
            )
            return {"comment_id": "7673208895377264155"}

        with patch("lark_cli_lig.api.LarkAPI.post", fake_post), patch(
            "lark_cli_lig.auth.TokenManager.get_current_open_id",
            return_value="ou_current_user",
        ):
            result = CliRunner().invoke(
                approval,
                ["comment", PROCESS_NUMBER, COMMENT_TEXT, "--no-verify"],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(f"Process {PROCESS_NUMBER}", result.output)
        self.assertNotIn(INSTANCE_CODE, result.output)

        comment_path, comment_body, comment_params = calls[-1]
        self.assertTrue(comment_path.endswith(f"/{INSTANCE_CODE}/comments"))
        self.assertEqual(comment_params, {
            "user_id": "ou_current_user",
            "user_id_type": "open_id",
        })
        self.assertIs(comment_body["disable_bot"], False)
        self.assertEqual(json.loads(comment_body["content"]), {
            "text": COMMENT_TEXT,
            "files": [],
        })

    def test_comments_lists_plain_text_for_process_number(self):
        def fake_post(self, path, body=None, params=None):
            return {
                "instance_list": [{
                    "instance": {
                        "code": INSTANCE_CODE,
                        "serial_id": PROCESS_NUMBER,
                    }
                }]
            }

        def fake_get(self, path, params=None):
            return {
                "comments": [{
                    "id": "comment-1",
                    "content": json.dumps({"text": COMMENT_TEXT, "files": []}),
                    "create_time": "1786563319000",
                    "is_delete": 0,
                }]
            }

        with patch("lark_cli_lig.api.LarkAPI.post", fake_post), patch(
            "lark_cli_lig.api.LarkAPI.get", fake_get
        ), patch(
            "lark_cli_lig.auth.TokenManager.get_current_open_id",
            return_value="ou_current_user",
        ):
            result = CliRunner().invoke(approval, ["comments", PROCESS_NUMBER])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(f"Comments for process {PROCESS_NUMBER}", result.output)
        self.assertIn(COMMENT_TEXT, result.output)
        self.assertNotIn(INSTANCE_CODE, result.output)


if __name__ == "__main__":
    unittest.main()
