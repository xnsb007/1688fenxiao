# -*- coding: utf-8 -*-
import argparse
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_env():
    possible_paths = [
        os.path.join(BASE_DIR, 'config', 'production.env'),
        '/opt/fenxiao/config/production.env',
        os.path.join(os.getcwd(), 'config', 'production.env'),
    ]
    env_file = next((path for path in possible_paths if os.path.exists(path)), None)
    if not env_file:
        print(f'Config file not found in any of: {possible_paths}', file=sys.stderr)
        return None

    last_error = None
    for encoding in ('utf-8', None):
        try:
            open_kwargs = {'encoding': encoding} if encoding else {}
            with open(env_file, 'r', **open_kwargs) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        os.environ[key] = value.strip()
            print(f'Loaded config from {env_file}', file=sys.stderr)
            return env_file
        except Exception as exc:
            last_error = exc
    print(f'Failed to load config {env_file}: {last_error}', file=sys.stderr)
    return None


def parse_args():
    parser = argparse.ArgumentParser(description='Run 1688 product follow job')
    parser.add_argument('--dry-run', action='store_true', help='List candidates without updating database')
    parser.add_argument('--date', help='Business date in YYYY-MM-DD, default is today in app timezone')
    parser.add_argument('--retry-failed-only', action='store_true', help='Only retry follow_status=failed rows')
    parser.add_argument('--limit', type=int, default=500, help='Maximum candidate rows to process')
    parser.add_argument('--max-retries', type=int, default=3, help='Maximum SDK attempts per product')
    parser.add_argument('--qps', type=float, default=1.0, help='API call rate limit, default QPS=1')
    return parser.parse_args()


def main():
    load_env()
    sys.path.insert(0, os.path.join(BASE_DIR, 'pythonSDK'))

    from runtime_logging import setup_daily_file_logging
    setup_daily_file_logging('1688_follow_job', log_dir=os.path.join(BASE_DIR, 'logs'))

    from app.models import init_db
    from app.services.ali1688_product_follow_service import ali1688_product_follow_service

    args = parse_args()
    init_db()

    result = ali1688_product_follow_service.run_daily_follow(
        date_text=args.date,
        retry_failed_only=args.retry_failed_only,
        dry_run=args.dry_run,
        limit=args.limit,
        max_retries=args.max_retries,
        qps=args.qps,
    )
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))

    if result.get('lock_skipped'):
        return 0
    return 0 if result.get('success') else 1


if __name__ == '__main__':
    raise SystemExit(main())
