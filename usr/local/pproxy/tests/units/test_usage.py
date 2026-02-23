import unittest
from unittest.mock import MagicMock, patch
import os
import sys
from datetime import datetime

# Setup paths
up_dir = os.path.normpath(os.path.dirname(os.path.abspath(__file__)) + '/../../')
if up_dir not in sys.path:
    sys.path.append(up_dir)

import usage  # noqa: E402


class TestUsage(unittest.TestCase):

    def setUp(self):
        self.mock_logger = MagicMock()
        self.mock_config = MagicMock()
        self.mock_config.get.side_effect = lambda section, key: {
            ('usage', 'db-path'): '/tmp/test_usage.db'
        }.get((section, key))
        self.mock_restore = MagicMock(return_value=True)
        self.usage_instance = usage.Usage(
            self.mock_logger,
            usage_type="shadowsocks",
            config=self.mock_config,
            restore_callback=self.mock_restore
        )

    @patch('usage.dataset.connect')
    def test_db_query_success(self, mock_connect):
        mock_db = MagicMock()
        mock_connect.return_value = mock_db

        # Test basic connection
        res = self.usage_instance.db_query()
        self.assertEqual(res, mock_db)

        # Test query_str
        mock_db.query.return_value = [{'res': 1}]
        res = self.usage_instance.db_query(query_str="SELECT 1")
        self.assertEqual(res, [{'res': 1}])

        # Test table lookup
        mock_table = MagicMock()
        mock_db.__getitem__.return_value = mock_table
        res = self.usage_instance.db_query(table_name="records")
        mock_table.count.assert_called_once()
        self.assertIsInstance(res, list)

    @patch('usage.dataset.connect')
    def test_db_query_corruption_recovery(self, mock_connect):
        mock_connect.side_effect = [
            Exception("database disk image is malformed"),
            MagicMock()
        ]
        res = self.usage_instance.db_query()
        self.mock_restore.assert_called_once()
        self.assertIsNotNone(res)

    @patch('usage.sqli.connect')
    def test_sqli_query(self, mock_connect):
        mock_conn = MagicMock()
        mock_connect.return_value = mock_conn
        mock_cursor = mock_conn.cursor.return_value

        res = self.usage_instance.sqli_query("DELETE FROM records")
        self.assertEqual(res, mock_cursor)
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

    @patch('usage.Usage.sqli_query')
    def test_del_user_usage(self, mock_sqli):
        self.usage_instance.del_user_usage("user1")
        mock_sqli.assert_called_once_with("delete from records where certname like ?", ["user1"])

    def test_calculate_delta_with_wrap_around(self):
        # Normal case
        self.assertEqual(self.usage_instance.calculate_delta_with_wrap_around(150, 100), 50)
        # Wrap around case
        self.assertEqual(self.usage_instance.calculate_delta_with_wrap_around(50, 100), 50)

    @patch('usage.Usage.db_query')
    def test_get_delta_for_cert(self, mock_db_query):
        mock_table = MagicMock()
        mock_db_query.return_value = mock_table

        # Case: record exists
        mock_table.find_one.return_value = {'raw_usage': 100}
        delta = self.usage_instance.get_delta_for_cert("user1", 150)
        self.assertEqual(delta, 50)

        # Case: record missing
        mock_table.find_one.return_value = None
        delta = self.usage_instance.get_delta_for_cert("user2", 150)
        self.assertEqual(delta, 150)

    @patch('usage.Usage.get_record_for_cert')
    @patch('usage.Usage.get_delta_for_cert')
    @patch('usage.Usage.db_query')
    def test_update_recorded_usage(self, mock_db_query, mock_get_delta, mock_get_record):
        mock_table = MagicMock()
        mock_db_query.return_value = mock_table
        mock_get_delta.return_value = 50
        mock_get_record.return_value = {'short_term': 100, 'long_term': 1000}

        st, lt, d = self.usage_instance.update_recorded_usage("user1", 150)
        self.assertEqual(st, 150)
        self.assertEqual(lt, 1050)
        self.assertEqual(d, 50)
        mock_table.upsert.assert_called_once()

        # Test clearing flags
        st, lt, d = self.usage_instance.update_recorded_usage(
            "user1", 150, clear_short_term=True, clear_long_term=True)
        call_args = mock_table.upsert.call_args[0][0]
        self.assertEqual(call_args['short_term'], 0)
        self.assertEqual(call_args['long_term'], 0)

    @patch('usage.Usage.db_query')
    def test_get_record_for_cert(self, mock_db_query):
        mock_table = MagicMock()
        mock_db_query.return_value = mock_table
        mock_table.find_one.return_value = {'certname': 'user1'}

        res = self.usage_instance.get_record_for_cert("user1")
        self.assertEqual(res, {'certname': 'user1'})


if __name__ == '__main__':
    unittest.main()
