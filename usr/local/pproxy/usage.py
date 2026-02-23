import sqlite3 as sqli
import dataset
from datetime import datetime


class Usage:
    def __init__(self, logger, service_type, config, restore_callback=None):
        self.logger = logger
        self.config = config
        self.restore_callback = restore_callback
        self.config_key = 'db-path'
        self.section_name = 'usage'
        self.table_name = 'records'
        self.type = service_type

    def db_query(self, table_name=None, query_str=None, use_backup=False, return_table=False):
        """
        Generalized DB query method with error handling and recovery.
        """
        db_path = self.config.get(self.section_name, self.config_key)
        if use_backup:
            db_path += ".backup"

        try:
            local_db = dataset.connect(
                'sqlite:///' + db_path + "?check_same_thread=False")

            if query_str:
                results = local_db.query(query_str)
                return list(results)

            if table_name:
                table = local_db[table_name]
                if return_table:
                    return table
                # Trigger a check by doing a count
                table.count()
                return list(table.all())

            return local_db
        except Exception as e:
            self.logger.error(
                f"Database query error (config_key={self.config_key}, backup={use_backup}): {e}")
            msg = str(e).lower()
            if self.config_key == 'db-path' and not use_backup and ("malformed" in msg or "corrupt" in msg):
                self.logger.warning("Corruption detected during db_query, attempting restore.")
                if self.restore_callback and self.restore_callback():
                    # Retry once after restore
                    return self.db_query(table_name, query_str, use_backup, return_table)
            return None

    def sqli_query(self, query_str, params=None, close_conn=True):
        conn = sqli.connect(self.config.get(self.section_name, self.config_key))
        cur = conn.cursor()
        try:
            cur.execute(query_str, params)
        except:
            self.logger.exception("Some error in deleting from usage")
        conn.commit()
        if close_conn:
            conn.close()
        return cur

    def del_user_usage(self, certname):
        if certname:
            try:
                self.sqli_query("delete from records where certname like ?", [certname])
            except:
                self.logger.exception("Some error in deleting from usage")

    def calculate_delta_with_wrap_around(self, current_usage, stored_usage):
        if current_usage < stored_usage:
            return current_usage
        return current_usage - stored_usage

    def get_delta_for_cert(self, certname, current_reading):
        servers = self.db_query(self.table_name, return_table=True)
        record = servers.find_one(certname=certname)
        if record is None or 'raw_usage' not in record:
            recorded_usage = 0
        else:
            recorded_usage = record['raw_usage']

        delta = self.calculate_delta_with_wrap_around(
            current_reading, recorded_usage)
        return delta

    def update_recorded_usage(self, certname, new_reading,
                              clear_long_term=False,
                              clear_short_term=False):
        table = self.db_query(self.table_name, return_table=True)
        delta = self.get_delta_for_cert(certname, new_reading)

        record = self.get_record_for_cert(certname)
        short_term_stored = record['short_term'] if record and 'short_term' in record else 0
        long_term_stored = record['long_term'] if record and 'long_term' in record else 0

        short_term_delta = delta + short_term_stored
        long_term_delta = delta + long_term_stored

        table.upsert({'certname': certname,
                      'raw_usage': new_reading,
                      'short_term': short_term_delta if not clear_short_term else 0,
                      'long_term': long_term_delta if not clear_long_term else 0,
                      'last_update': datetime.now(),
                      'type': self.type}, ['certname'])
        return short_term_delta, long_term_delta, delta

    def get_record_for_cert(self, certname):
        servers = self.db_query(self.table_name, return_table=True)
        record = servers.find_one(certname=certname)
        return record
